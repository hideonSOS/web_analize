"""独自セクター別インパルス用の日次終値を取得する（対象は impulse.py の定義銘柄のみ）

セクター別インパルス（時系列ヒートマップ）は日々の騰落率の履歴が必要なため、
定義銘柄の調整後終値を DailyPrice に蓄積する。JP/US とも yfinance で取る
（J-Quants無料プランは直近が約12週遅延するため使えない。東証は <コード>.T）。
ポートフォリオの保有銘柄も対象に加える（個別株分析・カルテのドローダウン算出用）。

    python manage.py update_impulse_prices              # 差分のみ（cron想定）
    python manage.py update_impulse_prices --days 60    # 初回。60暦日分を遡る
    python manage.py update_impulse_prices --full       # 全期間（--days分）取り直し

⚠️ 必ず調整後終値（auto_adjust=True）。未調整だと分割時に騰落率が壊れる。
⚠️ 市場クローズ前に実行しても安全なよう、**取引時間中の未確定当日バーは保存しない**
   （_cutoff_date）。保存すると差分同期のため日中の途中値が永久に残ってしまう。
   cron は米国クローズ確定後の JST 朝7時（us_ranking_update.sh と同枠）が最適。
⚠️ 2026-08-31に**一括ダウンロード化**した（旧実装は1銘柄1コールで日次約117コール）。
   国別×取得開始日別にまとめて yf.download し、日次のネット呼び出しを数コールに削減
   （update_us_ranking と同方針）。一括で取りこぼした銘柄（"possibly delisted" の
   一時的な癖）だけ単発フェッチで再試行する。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from japan_kabu.impulse import IMPULSE_SECTORS, impulse_universe
from japan_kabu.models import DailyPrice, Stock

DEFAULT_DAYS = 60      # 表示20営業日 + 余裕（暦日）
DOWNLOAD_CHUNK = 100   # 一括downloadの1回あたり銘柄数（多すぎると取りこぼしが増える）


class Command(BaseCommand):
    help = 'セクター別インパルス用の日次終値を取得する（impulse.py の定義銘柄 + 保有銘柄）'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                            help=f'遡る暦日数（既定 {DEFAULT_DAYS}日）。初回のみ意味を持つ')
        parser.add_argument('--full', action='store_true',
                            help='保存済みを無視して指定日数分を取り直す')

    def handle(self, *args, **options):
        start = date.today() - timedelta(days=options['days'])
        ok = ng = 0
        for country in IMPULSE_SECTORS:
            cutoff = self._cutoff_date(country)
            targets = self._collect_targets(country)

            # 銘柄ごとの取得開始日を出し、同じ開始日の銘柄をまとめて一括downloadする
            # （通常は全銘柄が「保存済み最終日の翌日」で揃い、1グループ=1コールになる）
            plans = {}
            for stock, ticker in targets:
                from_date = start
                if not options['full']:
                    latest = (DailyPrice.objects.filter(stock=stock)
                              .order_by('-date').values_list('date', flat=True).first())
                    if latest:
                        from_date = max(start, latest + timedelta(days=1))
                        if from_date > date.today():
                            ok += 1
                            continue
                plans.setdefault(from_date, []).append((stock, ticker))

            for from_date, group in plans.items():
                fetched = self._fetch_batch([t for _, t in group], from_date)
                for stock, ticker in group:
                    try:
                        rows = fetched.get(ticker)
                        if rows is None:
                            # 一括で取りこぼした銘柄だけ単発で再試行
                            rows = self._fetch(ticker, from_date)
                        rows = [(d, c) for d, c in rows if d <= cutoff]
                        if rows:
                            objs = [DailyPrice(stock=stock, date=d, close=c)
                                    for d, c in rows]
                            DailyPrice.objects.bulk_create(
                                objs, ignore_conflicts=True, batch_size=1000)
                        ok += 1
                        self.stdout.write(
                            f'  {stock.display_code:6} {country} +{len(rows)}件')
                    except Exception as e:  # noqa: BLE001  1銘柄の失敗で全体を止めない
                        ng += 1
                        self.stderr.write(f'  {stock.display_code:6} {country} 失敗: {e}')

        self.stdout.write(self.style.SUCCESS(f'インパルス日次終値: {ok}銘柄成功 / {ng}銘柄失敗'))

    @staticmethod
    def _cutoff_date(country):
        """保存してよい最新の日付を返す（市場クローズ前は当日バーが未確定なので前日まで）

        yfinanceは取引時間中に当日の途中値を「日足」として返す。差分同期のため
        一度保存すると翌日以降も上書きされないので、クローズ確定前の当日は弾く。
        """
        if country == 'US':
            now = datetime.now(ZoneInfo('America/New_York'))
            close = (16, 5)     # 16:00 ET クローズ + 余裕
        else:
            now = datetime.now(ZoneInfo('Asia/Tokyo'))
            close = (15, 35)    # 15:30 大引け + 余裕
        if (now.hour, now.minute) < close:
            return now.date() - timedelta(days=1)
        return now.date()

    @classmethod
    def _collect_targets(cls, country):
        """その国の全対象 [(Stock, yfinanceティッカー)]

        インパルス定義銘柄 + 保有銘柄 + カルテ/日記の登録銘柄。
        カルテ/日記銘柄は2026-08-31に追加した。従来は夜バッチの update_daily_prices
        だけが供給源で、夜バッチが止まると「カルテのみ登録」の銘柄（PLTR等）の
        日次終値だけが凍結し、カルテの現在値・チャートが古いまま表示される事故が
        実際に起きた（保有銘柄はこちらの朝バッチが拾うため気付きにくい）。
        朝バッチ1本で全登録銘柄をカバーする。JP銘柄もyfinance調整後終値で取れるので、
        J-Quants無料プランの遅延で凍結していたJPカルテ銘柄の日次終値もここで再開する。
        """
        from diary.models import DiaryEntry
        from karte.models import StockKarte
        from portfolio.models import Holding

        targets = list(cls._targets(country))
        seen = {s.code for s, _ in targets}

        extra_codes = set(
            Holding.objects.filter(stock__isnull=False).values_list('stock_id', flat=True))
        extra_codes |= set(StockKarte.objects.values_list('stock_id', flat=True))
        extra_codes |= set(DiaryEntry.objects.filter(stock__isnull=False)
                           .values_list('stock_id', flat=True))
        for stock in Stock.objects.filter(code__in=extra_codes, country=country):
            if stock.code in seen:
                continue
            ticker = (f'{stock.display_code}.T' if country == 'JP'
                      else stock.display_code.replace('.', '-'))
            targets.append((stock, ticker))
            seen.add(stock.code)
        return targets

    @staticmethod
    def _targets(country):
        """[(Stock, yfinanceティッカー)] を返す。マスタに無いコードは警告なしで飛ばさず例外に出る"""
        codes = impulse_universe(country)
        if country == 'JP':
            # ⚠️ display_code で引かないこと（views.py の _impulse_series と同じ理由）。
            # 優先株が同じ表示コードを持つため（9434など）、code=表示コード+'0'（普通株）
            # で確定させる。過去に優先株の行へ9434.Tの株価を書き込む事故が起きた
            found = {s.display_code: s for s in
                     Stock.objects.filter(country='JP', code__in=[f'{c}0' for c in codes])}
            return [(found[c], f'{c}.T') for c in codes if c in found]
        found = {s.display_code: s for s in
                 Stock.objects.filter(country='US', code__in=[f'US-{c}' for c in codes])}
        return [(found[c], c.replace('.', '-')) for c in codes if c in found]

    @staticmethod
    def _fetch_batch(tickers, from_date):
        """複数銘柄をまとめて取得する。{ティッカー: [(date, close)]} を返す

        取得できなかった銘柄はキー自体が無い（呼び出し側が単発フェッチで再試行する）。
        """
        import yfinance as yf

        out = {}
        for i in range(0, len(tickers), DOWNLOAD_CHUNK):
            chunk = tickers[i:i + DOWNLOAD_CHUNK]
            df = yf.download(chunk, start=from_date.isoformat(), progress=False,
                             auto_adjust=True, group_by='column')
            if df is None or df.empty or 'Close' not in df:
                continue
            closes = df['Close']
            if not hasattr(closes, 'columns'):
                # 1銘柄だけのチャンクでは Series で返ることがある
                closes = closes.to_frame(name=chunk[0])
            for t in closes.columns:
                s = closes[t].dropna()
                rows = [(idx.date() if hasattr(idx, 'date') else idx, float(v))
                        for idx, v in s.items()]
                if rows:
                    out[str(t)] = rows
        return out

    @staticmethod
    def _fetch(ticker, from_date):
        """単発フェッチ（一括の取りこぼし再試行用）。auto_adjust=True で調整後終値"""
        import yfinance as yf

        df = yf.download(ticker, start=from_date.isoformat(),
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        closes = df['Close']
        if hasattr(closes, 'columns'):   # 1銘柄でもDataFrameで返ることがある
            closes = closes.iloc[:, 0]
        out = []
        for idx, v in closes.items():
            if v != v:                   # NaN除外
                continue
            d = idx.date() if hasattr(idx, 'date') else idx
            out.append((d, float(v)))
        return out
