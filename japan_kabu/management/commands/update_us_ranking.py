"""米国株（S&P500級・約500銘柄）の時価総額と出来高異常度を更新する

日本株の update_marketcap / update_volume の米国株版。対象は「S&P500の
構成銘柄」に限定する（yfinanceは1銘柄1コールのため全12,484件は非現実的）。
ランキング表示用に Stock の market_cap / close / volume / volume_z 等を埋める。

使い方:
    python manage.py update_us_ranking                # 通常（週1〜日次で実行）
    python manage.py update_us_ranking --tickers AAPL,MSFT,NVDA   # 少数で動作確認
    python manage.py update_us_ranking --limit 20     # 先頭20銘柄だけで確認
    python manage.py update_us_ranking --no-marketcap # 出来高だけ更新（高速）
    python manage.py update_us_ranking --refresh-list # 構成銘柄CSVを再取得して同梱

設計メモ:
- 構成銘柄は DataHub の公開CSV（プレーンテキスト・依存追加不要）から取得し、
  取得成功時に japan_kabu/data/sp500.csv へ保存する。次回以降ネットワークが
  落ちても同梱CSVでフォールバックできる（外部依存で止めない）。
- 出来高異常度は update_volume と同じ式（対数z-score）。ただし米国株は
  yf.download の一括取得（1リクエストで全銘柄の40日分）で完結するため、
  DailyVolume には保存せず in-memory で計算して Stock に書き戻す。
- 時価総額($)は fast_info から取得し market_cap 列に **ドルのまま** 入れる。
  ランキング表示は country で必ず絞るので、円($混在)の心配はない。
"""
import csv
import io
import math
import statistics
import urllib.request
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand

from japan_kabu.models import Stock

# 構成銘柄リスト（プレーンCSV・無料・安定）。列: Symbol, Security, GICS Sector, ...
SP500_CSV_URL = (
    'https://raw.githubusercontent.com/datasets/s-and-p-500-companies/'
    'main/data/constituents.csv'
)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / 'data'
LOCAL_CSV = DATA_DIR / 'sp500.csv'

# 出来高異常度（update_volume と同じパラメータ。閾値だけドル建てに変える）
WINDOW = 20            # 比較対象の過去営業日数
MIN_HISTORY = 15       # 計算に必要な最低履歴日数
SIGMA_FLOOR = 0.1      # σの下限（zの発散防止）
MIN_TURNOVER_USD = 1e6  # 過去売買代金の中央値の下限（ドル）。薄商い除外
FETCH_PERIOD = '40d'   # 取得する暦日数（営業日20日+余裕）
DOWNLOAD_CHUNK = 100   # yf.download に一度に渡す銘柄数（多すぎると一部が取りこぼれる）


class Command(BaseCommand):
    help = '米国株（S&P500級）の時価総額・出来高異常度を更新する（yfinance）'

    def add_arguments(self, parser):
        parser.add_argument('--tickers', help='カンマ区切りのティッカーで対象を限定（動作確認用）')
        parser.add_argument('--limit', type=int, help='対象を先頭N銘柄に限定（動作確認用）')
        parser.add_argument('--no-marketcap', action='store_true',
                            help='時価総額(fast_info)の取得を省き出来高だけ更新する')
        parser.add_argument('--refresh-list', action='store_true',
                            help='構成銘柄CSVをネットから再取得し同梱ファイルを更新する')
        parser.add_argument('--refresh-shares', action='store_true',
                            help='発行済株式数を全銘柄ぶん取り直す（週1想定。通常は保存値を再利用）')
        parser.add_argument('--workers', type=int, default=10,
                            help='時価総額(fast_info)取得の並列数（既定10）')

    def handle(self, *args, **options):
        constituents = self._load_constituents(refresh=options['refresh_list'])
        if options['tickers']:
            wanted = {t.strip().upper() for t in options['tickers'].split(',') if t.strip()}
            constituents = [c for c in constituents if c['symbol'] in wanted]
        if options['limit']:
            constituents = constituents[:options['limit']]
        if not constituents:
            self.stderr.write('対象銘柄がありません')
            return
        self.stdout.write(f'対象: {len(constituents)}銘柄')

        stocks = self._upsert_stocks(constituents)

        vol_result, closes_map, chg_map, price_date = self._fetch_volume(constituents)
        caps = {} if options['no_marketcap'] else self._fetch_marketcaps(
            constituents, stocks, closes_map,
            refresh_shares=options['refresh_shares'], workers=options['workers'])

        updated = self._apply(stocks, vol_result, caps, chg_map, price_date)
        self.stdout.write(self.style.SUCCESS(
            f'完了: 米国株ランキング更新 {updated}銘柄'
            f'（時価総額 {len(caps)}件 / 出来高 {len(vol_result)}件 / 基準日 {price_date}）'))

    # ---- 構成銘柄リスト -------------------------------------------------
    def _load_constituents(self, refresh=False):
        """[{symbol, name, sector}] を返す。ネット取得→同梱CSVの順にフォールバック"""
        rows = None
        if refresh or not LOCAL_CSV.exists():
            rows = self._fetch_constituents()
            if rows:
                self._save_constituents(rows)
        if rows is None and LOCAL_CSV.exists():
            with LOCAL_CSV.open(encoding='utf-8') as f:
                rows = list(csv.DictReader(f))
                rows = [{'symbol': r['symbol'], 'name': r['name'], 'sector': r['sector']}
                        for r in rows]
        if not rows:
            # 同梱CSVも無く取得も失敗した場合の最終フォールバック
            rows = self._fetch_constituents()
            if rows:
                self._save_constituents(rows)
        return rows or []

    def _fetch_constituents(self):
        try:
            req = urllib.request.Request(SP500_CSV_URL, headers={'User-Agent': 'Mozilla/5.0'})
            text = urllib.request.urlopen(req, timeout=60).read().decode('utf-8')
        except Exception as e:  # noqa: BLE001  ネット断でも同梱CSVに落とすため握る
            self.stderr.write(f'構成銘柄CSVの取得に失敗（フォールバックへ）: {e}')
            return None
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            sym = (r.get('Symbol') or '').strip().upper()
            name = (r.get('Security') or r.get('Name') or '').strip()[:100]
            sector = (r.get('GICS Sector') or r.get('Sector') or '').strip()[:40]
            if sym and name:
                out.append({'symbol': sym, 'name': name, 'sector': sector})
        return out

    def _save_constituents(self, rows):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with LOCAL_CSV.open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['symbol', 'name', 'sector'])
            w.writeheader()
            w.writerows(rows)
        self.stdout.write(f'構成銘柄CSVを保存: {LOCAL_CSV}（{len(rows)}件）')

    # ---- 銘柄マスタの upsert -------------------------------------------
    def _upsert_stocks(self, constituents):
        """S&P500銘柄が Stock に存在することを保証し、名前・セクターを更新する

        既存の米国株マスタ(import_us_master)と code='US-<ティッカー>' で一致する。
        マスタが除外していたドット付きティッカー(BRK.B等)もここで追加される。
        market/close 等は触らない（出来高・時価総額フェーズで別途更新する）。
        """
        objs = [
            Stock(
                code=f'US-{c["symbol"]}',
                display_code=c['symbol'],
                country='US',
                name=c['name'],
                sector33=c['sector'],
                sector17=c['sector'],
            )
            for c in constituents
        ]
        Stock.objects.bulk_create(
            objs, batch_size=500,
            update_conflicts=True, unique_fields=['code'],
            update_fields=['display_code', 'name', 'sector33', 'sector17', 'country'])
        codes = [o.code for o in objs]
        return {s.code: s for s in Stock.objects.filter(code__in=codes)}

    # ---- 出来高（一括ダウンロード・チャンク分割） -----------------------
    def _fetch_volume(self, constituents):
        """{symbol:(volume,ratio,z)}, {symbol:latest_close}, 基準日 を返す

        yf.download の一括取得。500銘柄を1回で投げると一部が "possibly delisted"
        で取りこぼれるため、DOWNLOAD_CHUNK 件ずつに分けて投げる（所要時間は一括と
        ほぼ同じで取得の欠落が減る）。ここで得た最新終値は時価総額の再計算
        （close × 保存済み株式数）にも使い回し、日次のネット負荷を抑える。
        """
        symbols = [c['symbol'] for c in constituents]
        yf_map = {s: s.replace('.', '-') for s in symbols}  # yfinanceは BRK-B 形式

        result = {}
        closes_map = {}
        chg_map = {}
        price_date = None
        for i in range(0, len(symbols), DOWNLOAD_CHUNK):
            chunk = symbols[i:i + DOWNLOAD_CHUNK]
            pd_chunk = self._download_chunk([yf_map[s] for s in chunk], chunk, yf_map,
                                            result, closes_map, chg_map)
            if pd_chunk is not None:
                price_date = pd_chunk
        if price_date is None:
            self.stderr.write('yf.download が全チャンクで空を返しました')
        return result, closes_map, chg_map, price_date

    def _download_chunk(self, yf_tickers, symbols, yf_map, result, closes_map, chg_map):
        """1チャンクを取得し result/closes_map/chg_map を埋める。基準日を返す"""
        import yfinance as yf

        try:
            data = yf.download(yf_tickers, period=FETCH_PERIOD, progress=False,
                               auto_adjust=True, group_by='column')
        except Exception as e:  # noqa: BLE001  チャンク失敗は他チャンクを止めない
            self.stderr.write(f'  ダウンロード失敗（{len(yf_tickers)}銘柄）: {e}')
            return None
        if data is None or data.empty:
            return None

        closes = data['Close']
        volumes = data['Volume']
        pd_raw = volumes.index[-1]
        price_date = pd_raw.date() if hasattr(pd_raw, 'date') else datetime.today().date()

        single = len(yf_tickers) == 1
        for sym in symbols:
            yf_t = yf_map[sym]
            try:
                if single:
                    v_ser, c_ser = volumes, closes
                elif yf_t not in volumes.columns:
                    continue
                else:
                    v_ser, c_ser = volumes[yf_t], closes[yf_t]
                c_vals = c_ser.tolist()
                pairs = [
                    (float(v), float(v) * float(c))
                    for v, c in zip(v_ser.tolist(), c_vals)
                    if v == v and c == c and v > 0  # NaN除外
                ]
                valid_closes = [float(c) for c in c_vals if c == c]  # NaN除外
                if valid_closes:
                    closes_map[sym] = valid_closes[-1]
                if len(valid_closes) >= 2 and valid_closes[-2] > 0:
                    chg_map[sym] = (valid_closes[-1] / valid_closes[-2] - 1) * 100
            except Exception:  # noqa: BLE001
                continue
            score = self._score(pairs)
            if score:
                result[sym] = score
        return price_date

    @staticmethod
    def _score(pairs):
        """[(volume, turnover)] 時系列（末尾が当日）から (volume, ratio, z) を返す

        update_volume._score と同じ対数z-score。閾値のみドル建て。
        """
        if len(pairs) < MIN_HISTORY + 1:
            return None
        cur_volume = pairs[-1][0]
        if cur_volume <= 0:
            return None
        hist = pairs[-(WINDOW + 1):-1]
        if len(hist) < MIN_HISTORY:
            return None
        vols = [v for v, _ in hist]
        turns = [t for _, t in hist]
        if statistics.median(turns) < MIN_TURNOVER_USD:
            return None
        ln_hist = [math.log(v) for v in vols]
        mu = statistics.fmean(ln_hist)
        sigma = max(statistics.pstdev(ln_hist), SIGMA_FLOOR)
        z = (math.log(cur_volume) - mu) / sigma
        ratio = cur_volume / statistics.fmean(vols)
        return int(cur_volume), ratio, z

    # ---- 時価総額 -------------------------------------------------------
    def _fetch_marketcaps(self, constituents, stocks, closes_map,
                          refresh_shares=False, workers=10):
        """{symbol: (market_cap_usd, close, shares)} を返す

        発行済株式数は変動が遅いので **保存値(Stock.shares)を再利用** し、
        時価総額 = 最新終値(closes_map) × 株式数 を無料で計算する（日次はほぼ即時）。
        株式数が未保存の銘柄だけ fast_info を並列で取りに行く。
        --refresh-shares で全銘柄ぶん取り直す（週1想定）。
        """
        caps = {}
        need_fetch = []
        for c in constituents:
            sym = c['symbol']
            s = stocks.get(f'US-{sym}')
            close = closes_map.get(sym)
            shares = None if refresh_shares else (s.shares if s else None)
            if shares and close:
                caps[sym] = (int(close * shares), close, int(shares))
            else:
                need_fetch.append(sym)

        if need_fetch:
            self.stdout.write(
                f'  発行済株式数を取得: {len(need_fetch)}銘柄（並列{workers}）'
                f'／保存値で再計算: {len(caps)}銘柄')
            fetched = self._fetch_fastinfo(need_fetch, closes_map, workers)
            caps.update(fetched)
        return caps

    def _fetch_fastinfo(self, symbols, closes_map, workers):
        """fast_info を並列取得し {symbol:(mcap, close, shares)} を返す。1銘柄1コール"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import yfinance as yf

        def one(sym):
            try:
                fi = yf.Ticker(sym.replace('.', '-')).fast_info
                mc = self._fi(fi, 'market_cap', 'marketCap')
                price = self._fi(fi, 'last_price', 'lastPrice') or closes_map.get(sym)
                shares = self._fi(fi, 'shares', 'sharesOutstanding')
                if mc is None and price and shares:
                    mc = price * shares
                if mc:
                    return sym, (int(mc), price, int(shares) if shares else None)
            except Exception:  # noqa: BLE001  個別銘柄の失敗は全体を止めない
                pass
            return sym, None

        out = {}
        done = 0
        total = len(symbols)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            futures = [ex.submit(one, s) for s in symbols]
            for fut in as_completed(futures):
                sym, val = fut.result()
                if val:
                    out[sym] = val
                done += 1
                if done % 100 == 0:
                    self.stdout.write(f'    {done}/{total} ...')
        return out

    @staticmethod
    def _fi(fast_info, *keys):
        for k in keys:
            try:
                v = fast_info[k]
            except (KeyError, TypeError):
                v = getattr(fast_info, k, None)
            if v is not None and v == v:  # NaN除外
                return float(v)
        return None

    # ---- Stock へ書き戻し ----------------------------------------------
    def _apply(self, stocks, vol_result, caps, chg_map, price_date):
        touched = []
        for sym, (mc, price, shares) in caps.items():
            s = stocks.get(f'US-{sym}')
            if not s:
                continue
            s.market_cap = mc
            if price is not None:
                s.close = price
                s.price_date = price_date
            if shares is not None:
                s.shares = shares
            touched.append(s)

        chg_touched = []
        for sym, pct in chg_map.items():
            s = stocks.get(f'US-{sym}')
            if not s:
                continue
            s.change_pct = pct
            chg_touched.append(s)

        vol_touched = []
        for sym, (vol, ratio, z) in vol_result.items():
            s = stocks.get(f'US-{sym}')
            if not s:
                continue
            s.volume = vol
            s.volume_ratio = ratio
            s.volume_z = z
            s.volume_date = price_date
            vol_touched.append(s)

        # market_cap系と volume系で更新フィールドが異なるので2回に分ける
        if touched:
            Stock.objects.bulk_update(
                touched, ['market_cap', 'close', 'price_date', 'shares'], batch_size=500)
        if chg_touched:
            Stock.objects.bulk_update(chg_touched, ['change_pct'], batch_size=500)
        if vol_touched:
            Stock.objects.bulk_update(
                vol_touched, ['volume', 'volume_ratio', 'volume_z', 'volume_date'],
                batch_size=500)
        return len({s.code for s in touched + chg_touched + vol_touched})
