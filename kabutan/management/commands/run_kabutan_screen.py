"""株タン手法の日次スクリーニング（日足の差分取得 → 全ユニバース判定 → 記録）

    python manage.py run_kabutan_screen                # 差分取得＋判定（夜バッチ想定）
    python manage.py run_kabutan_screen --backfill 300 # 初回: 過去300暦日分を取得
    python manage.py run_kabutan_screen --no-fetch     # 取得を飛ばして判定だけやり直す
    python manage.py run_kabutan_screen --code 6758    # 1銘柄の判定内容を表示（デバッグ）

ユニバース = JPX400（Jpx400Member）∪ カルテ登録銘柄（JPのみ）。
日足は yfinance の一括 download（DOWNLOAD_CHUNK=100・調整後OHLC）。
判定は kabutan/logic.py（ルールv1.1）で行い、WAIT も含め全銘柄を ScreenResult に保存。

⚠️ JP市場のクローズ（15:30 JST）前に走ると当日バーが未確定の途中値になるため、
   15:35 より前は当日バーを保存しない（update_impulse_prices と同じ理由のガード）。
⚠️ 必ず調整後OHLC（auto_adjust=True）。未調整だと分割でMAが壊れる。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from django.core.management.base import BaseCommand
from django.db.models import Max

from django.utils import timezone

from japan_kabu.models import Stock
from kabutan import logic
from kabutan.models import Jpx400Member, KabutanBar, ScreenResult, ScreenRun

DOWNLOAD_CHUNK = 100      # update_us_ranking と同方針
DEFAULT_BACKFILL = 300    # 初回に遡る暦日数（MA100 + 余裕）


class Command(BaseCommand):
    help = '株タン手法（v1.1）の日次スクリーニングを実行する'

    def add_arguments(self, parser):
        parser.add_argument('--backfill', type=int, default=0,
                            help='この暦日数だけ遡って日足を取り直す（初回用）')
        parser.add_argument('--no-fetch', action='store_true',
                            help='日足の取得を飛ばし、保存済みデータで判定だけ行う')
        parser.add_argument('--code', type=str, default='',
                            help='この表示コードの判定内訳を表示する（デバッグ）')

    # ── ユニバース ───────────────────────────────────────
    @staticmethod
    def _universe():
        """{stock_id: source} を返す。source は jpx400 / karte / both"""
        from karte.models import StockKarte
        jpx = set(Jpx400Member.objects.values_list('stock_id', flat=True))
        karte = set(StockKarte.objects.filter(stock__country='JP')
                    .values_list('stock_id', flat=True))
        out = {}
        for sid in jpx | karte:
            out[sid] = 'both' if (sid in jpx and sid in karte) else (
                'jpx400' if sid in jpx else 'karte')
        return out

    # ── 日足取得 ────────────────────────────────────────
    def _fetch(self, stocks, backfill):
        import yfinance as yf

        # 銘柄ごとの最終保存日 → 取得開始日（全銘柄の最小。差分同期）
        latest = dict(KabutanBar.objects.filter(stock__in=stocks)
                      .values('stock_id').annotate(m=Max('date'))
                      .values_list('stock_id', 'm'))
        today = date.today()
        if backfill:
            start = today - timedelta(days=backfill)
        else:
            missing = [s for s in stocks if s.code not in latest]
            starts = [latest[s.code] + timedelta(days=1)
                      for s in stocks if s.code in latest]
            if missing:
                start = today - timedelta(days=DEFAULT_BACKFILL)
            elif starts:
                start = min(starts)
            else:
                start = today - timedelta(days=DEFAULT_BACKFILL)
            if start > today:
                return 0

        # 未確定当日バーのガード（JPクローズ 15:30 → 15:35 以降のみ当日を保存）
        now = datetime.now(ZoneInfo('Asia/Tokyo'))
        cutoff = today if (now.hour, now.minute) >= (15, 35) else today - timedelta(days=1)

        by_ticker = {f'{s.display_code}.T': s for s in stocks}
        tickers = list(by_ticker.keys())
        added = 0
        for i in range(0, len(tickers), DOWNLOAD_CHUNK):
            chunk = tickers[i:i + DOWNLOAD_CHUNK]
            # ⚠️ 1チャンクの失敗で全体を止めない（yfinance の一時障害でも
            #    残りの銘柄は取得し、判定は必ず実行する）
            try:
                df = yf.download(chunk, start=start.isoformat(), progress=False,
                                 auto_adjust=True, group_by='ticker', threads=True)
            except Exception as e:   # noqa: BLE001
                self.stderr.write(f'  チャンク取得失敗（スキップ）: {type(e).__name__}: {e}')
                continue
            if df is None or df.empty:
                continue
            for t in chunk:
                try:
                    sub = df[t] if len(chunk) > 1 else df
                except KeyError:
                    continue
                sub = sub.dropna(subset=['Open', 'High', 'Low', 'Close'])
                stock = by_ticker[t]
                last = latest.get(stock.code)
                objs = []
                for idx, r in sub.iterrows():
                    d = idx.date() if hasattr(idx, 'date') else idx
                    if d > cutoff or (last and d <= last and not backfill):
                        continue
                    objs.append(KabutanBar(
                        stock=stock, date=d,
                        open=float(r['Open']), high=float(r['High']),
                        low=float(r['Low']), close=float(r['Close']),
                        volume=int(r['Volume']) if pd.notna(r.get('Volume')) else None))
                if objs:
                    KabutanBar.objects.bulk_create(objs, ignore_conflicts=True,
                                                   batch_size=1000)
                    added += len(objs)
            self.stdout.write(f'  日足取得 {min(i + DOWNLOAD_CHUNK, len(tickers))}'
                              f'/{len(tickers)}銘柄')
        return added

    # ── 判定 ───────────────────────────────────────────
    def _load_frames(self, stock_ids):
        """DBから {stock_id: DataFrame} を組む（直近220営業日で十分）"""
        qs = (KabutanBar.objects.filter(stock_id__in=stock_ids)
              .values_list('stock_id', 'date', 'open', 'high', 'low', 'close'))
        rows = pd.DataFrame(qs, columns=['sid', 'Date', 'Open', 'High', 'Low', 'Close'])
        frames = {}
        for sid, g in rows.groupby('sid'):
            g = g.sort_values('Date').tail(220).reset_index(drop=True)
            g['Date'] = pd.to_datetime(g['Date'])
            frames[sid] = g[['Date', 'Open', 'High', 'Low', 'Close']]
        return frames

    def handle(self, *args, **options):
        run_started = timezone.now()
        note = ''
        universe = self._universe()
        stocks = list(Stock.objects.filter(code__in=universe.keys()))
        if not stocks:
            self.stdout.write('ユニバースが空です（import_jpx400 を先に実行）')
            return
        self.stdout.write(f'ユニバース: {len(stocks)}銘柄 '
                          f'(JPX400∪カルテ) / ルール {logic.RULE_VERSION}')

        bars_added = None
        if not options['no_fetch']:
            # ⚠️ 取得が丸ごと失敗しても保存済みデータで判定は必ず実行する
            #    （「出力が永遠に出ない」を防ぐ。鮮度の異常は画面側の警告で気付ける）
            try:
                bars_added = self._fetch(stocks, options['backfill'])
                self.stdout.write(f'日足: +{bars_added}件')
            except Exception as e:   # noqa: BLE001
                note = f'日足取得に失敗（保存済みデータで判定）: {type(e).__name__}: {e}'[:300]
                self.stderr.write(note)

        frames = self._load_frames(list(universe.keys()))

        # 地合い: JPX400構成銘柄の等ウェイト合成指数
        jpx_ids = set(Jpx400Member.objects.values_list('stock_id', flat=True))
        wide = pd.DataFrame({sid: f.set_index('Date')['Close']
                             for sid, f in frames.items() if sid in jpx_ids})
        market_ok = logic.build_market_ok(wide) if not wide.empty else {}

        # デバッグ: 1銘柄の内訳表示
        if options['code']:
            code5 = options['code'] + '0'
            res = logic.judge_latest(frames.get(code5), market_ok)
            self.stdout.write(f'{options["code"]}: {res}')
            return

        ok = buy = 0
        for s in stocks:
            res = logic.judge_latest(frames.get(s.code), market_ok)
            if res['date'] is None:
                continue
            ScreenResult.objects.update_or_create(
                date=res['date'].date(), stock=s, rule_version=logic.RULE_VERSION,
                defaults=dict(
                    judgment=res['judgment'], met=res['met'], unmet=res['unmet'],
                    close=res['close'], stop_price=res['stop_price'],
                    tp_price=res['tp_price'], source=universe[s.code]))
            ok += 1
            buy += (res['judgment'] == 'BUY')

        # 実行記録（画面に「最終実行 いつ・何をしたか」を出すため）
        ScreenRun.objects.create(started_at=run_started, rule_version=logic.RULE_VERSION,
                                 bars_added=bars_added, n_judged=ok, n_buy=buy, note=note)
        self.stdout.write(self.style.SUCCESS(
            f'判定: {ok}銘柄（BUY候補 {buy}件） ルール {logic.RULE_VERSION}'))
