"""日本株の時価総額と出来高異常度を yfinance で更新する（J-Quants代替）

J-Quants 解約に伴い、ランキング（時価総額・出来高）の日本株データを yfinance
から取得する。US版 update_us_ranking のミラー。

- 対象は既存の日本株マスタ（Stock.country='JP'）。東証ティッカーは <コード>.T。
- 出来高異常度は update_volume と同じ対数z-score。yf.download の一括取得で完結する
  ため DailyVolume には保存せず in-memory で計算して Stock に直接書く。
- 時価総額は **保存済みの発行済株式数(Stock.shares)を再利用**して「終値×株式数」で
  算出（J-Quants由来の株式数はDBに残っており変動が遅い）。ネット呼び出しゼロ。
  株式数が未保存の銘柄だけ fast_info を並列取得。--refresh-shares で全件取り直す。

使い方:
    python manage.py update_jp_ranking                # 日次（daily_update.sh に同梱）
    python manage.py update_jp_ranking --refresh-shares  # 週1想定。発行済株式数を取り直す
    python manage.py update_jp_ranking --codes 7203,6758 # 少数で動作確認
    python manage.py update_jp_ranking --limit 50        # 先頭N銘柄だけ
    python manage.py update_jp_ranking --no-marketcap    # 出来高だけ更新
"""
import math
import statistics
from datetime import datetime

from django.core.management.base import BaseCommand

from japan_kabu.models import Stock

# 出来高異常度（update_volume と同じパラメータ）
WINDOW = 20            # 比較対象の過去営業日数
MIN_HISTORY = 15       # 計算に必要な最低履歴日数
SIGMA_FLOOR = 0.1      # σの下限（zの発散防止）
MIN_TURNOVER_JPY = 1e8  # 過去売買代金の中央値の下限（円）。薄商い除外
FETCH_PERIOD = '40d'   # 取得する暦日数（営業日20日+余裕）
DOWNLOAD_CHUNK = 100   # yf.download に一度に渡す銘柄数（多すぎると一部が取りこぼれる）


class Command(BaseCommand):
    help = '日本株ランキング（時価総額・出来高）を yfinance で更新する（J-Quants代替）'

    def add_arguments(self, parser):
        parser.add_argument('--codes', help='カンマ区切りの表示コードで対象を限定（動作確認用）')
        parser.add_argument('--limit', type=int, help='対象を先頭N銘柄に限定（動作確認用）')
        parser.add_argument('--no-marketcap', action='store_true',
                            help='時価総額の算出を省き出来高だけ更新する')
        parser.add_argument('--refresh-shares', action='store_true',
                            help='発行済株式数を fast_info で全銘柄取り直す（週1想定）')
        parser.add_argument('--workers', type=int, default=10,
                            help='発行済株式数(fast_info)取得の並列数（既定10）')

    def handle(self, *args, **options):
        stocks = list(Stock.objects.filter(country='JP').order_by('code'))
        if options['codes']:
            want = {c.strip() for c in options['codes'].split(',') if c.strip()}
            stocks = [s for s in stocks if s.display_code in want]
        if options['limit']:
            stocks = stocks[:options['limit']]
        if not stocks:
            self.stderr.write('対象の日本株がありません')
            return
        self.stdout.write(f'対象: {len(stocks)}銘柄')

        vol_result, closes_map, price_date = self._fetch_volume(stocks)
        caps = {} if options['no_marketcap'] else self._fetch_marketcaps(
            stocks, closes_map,
            refresh_shares=options['refresh_shares'], workers=options['workers'])

        updated = self._apply(stocks, vol_result, caps, price_date)
        self.stdout.write(self.style.SUCCESS(
            f'完了: 日本株ランキング更新 {updated}銘柄'
            f'（時価総額 {len(caps)}件 / 出来高 {len(vol_result)}件 / 基準日 {price_date}）'))

    # ---- ティッカー -----------------------------------------------------
    @staticmethod
    def _yf(stock):
        return f'{stock.display_code}.T'   # 東証は <コード>.T

    # ---- 出来高（一括ダウンロード・チャンク分割） -----------------------
    def _fetch_volume(self, stocks):
        """{code:(volume,ratio,z)}, {code:latest_close}, 基準日 を返す

        500銘柄を超えると1回のdownloadで取りこぼれるため DOWNLOAD_CHUNK 件ずつ投げる。
        ここで得た最新終値は時価総額の再計算にも使い回す。
        """
        result = {}
        closes_map = {}
        price_date = None
        for i in range(0, len(stocks), DOWNLOAD_CHUNK):
            chunk = stocks[i:i + DOWNLOAD_CHUNK]
            pd_chunk = self._download_chunk(chunk, result, closes_map)
            if pd_chunk is not None:
                price_date = pd_chunk
            if (i // DOWNLOAD_CHUNK) % 5 == 4:
                self.stdout.write(f'  出来高取得 {min(i + DOWNLOAD_CHUNK, len(stocks))}/{len(stocks)} ...')
        if price_date is None:
            self.stderr.write('yf.download が全チャンクで空を返しました')
        return result, closes_map, price_date

    def _download_chunk(self, chunk, result, closes_map):
        import yfinance as yf

        by_ticker = {self._yf(s): s for s in chunk}
        tickers = list(by_ticker)
        try:
            data = yf.download(tickers, period=FETCH_PERIOD, progress=False,
                               auto_adjust=True, group_by='column')
        except Exception as e:  # noqa: BLE001  チャンク失敗は他チャンクを止めない
            self.stderr.write(f'  ダウンロード失敗（{len(tickers)}銘柄）: {e}')
            return None
        if data is None or data.empty:
            return None

        closes = data['Close']
        volumes = data['Volume']
        pd_raw = volumes.index[-1]
        price_date = pd_raw.date() if hasattr(pd_raw, 'date') else datetime.today().date()

        single = len(tickers) == 1
        for yf_t, s in by_ticker.items():
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
                last_close = next((float(c) for c in reversed(c_vals) if c == c), None)
                if last_close is not None:
                    closes_map[s.code] = last_close
            except Exception:  # noqa: BLE001
                continue
            score = self._score(pairs)
            if score:
                result[s.code] = score
        return price_date

    @staticmethod
    def _score(pairs):
        """[(volume, turnover)] 時系列（末尾が当日）から (volume, ratio, z) を返す"""
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
        if statistics.median(turns) < MIN_TURNOVER_JPY:
            return None
        ln_hist = [math.log(v) for v in vols]
        mu = statistics.fmean(ln_hist)
        sigma = max(statistics.pstdev(ln_hist), SIGMA_FLOOR)
        z = (math.log(cur_volume) - mu) / sigma
        ratio = cur_volume / statistics.fmean(vols)
        return int(cur_volume), ratio, z

    # ---- 時価総額 -------------------------------------------------------
    def _fetch_marketcaps(self, stocks, closes_map, refresh_shares=False, workers=10):
        """{code:(market_cap_jpy, close, shares)} を返す

        発行済株式数(Stock.shares)を再利用し、時価総額=最新終値×株式数を無料で計算。
        株式数が未保存の銘柄だけ fast_info を並列取得。--refresh-shares で全件取り直す。
        """
        caps = {}
        need_fetch = []
        for s in stocks:
            close = closes_map.get(s.code)
            shares = None if refresh_shares else s.shares
            if shares and close:
                caps[s.code] = (int(close * shares), close, int(shares))
            elif close is not None:
                need_fetch.append(s)

        if need_fetch:
            self.stdout.write(
                f'  発行済株式数を取得: {len(need_fetch)}銘柄（並列{workers}）'
                f'／保存値で再計算: {len(caps)}銘柄')
            caps.update(self._fetch_fastinfo(need_fetch, closes_map, workers))
        return caps

    def _fetch_fastinfo(self, stocks, closes_map, workers):
        """fast_info を並列取得し {code:(mcap, close, shares)} を返す。1銘柄1コール"""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import yfinance as yf

        def one(s):
            try:
                fi = yf.Ticker(self._yf(s)).fast_info
                mc = self._fi(fi, 'market_cap', 'marketCap')
                price = self._fi(fi, 'last_price', 'lastPrice') or closes_map.get(s.code)
                shares = self._fi(fi, 'shares', 'sharesOutstanding')
                if mc is None and price and shares:
                    mc = price * shares
                if mc:
                    return s.code, (int(mc), price, int(shares) if shares else None)
            except Exception:  # noqa: BLE001  個別銘柄の失敗は全体を止めない
                pass
            return s.code, None

        out = {}
        done = 0
        total = len(stocks)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for fut in as_completed([ex.submit(one, s) for s in stocks]):
                code, val = fut.result()
                if val:
                    out[code] = val
                done += 1
                if done % 200 == 0:
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
    def _apply(self, stocks, vol_result, caps, price_date):
        by_code = {s.code: s for s in stocks}
        touched = []
        for code, (mc, price, shares) in caps.items():
            s = by_code.get(code)
            if not s:
                continue
            s.market_cap = mc
            if price is not None:
                s.close = price
                s.price_date = price_date
            if shares is not None:
                s.shares = shares
            touched.append(s)

        vol_touched = []
        for code, (vol, ratio, z) in vol_result.items():
            s = by_code.get(code)
            if not s:
                continue
            s.volume = vol
            s.volume_ratio = ratio
            s.volume_z = z
            s.volume_date = price_date
            vol_touched.append(s)

        if touched:
            Stock.objects.bulk_update(
                touched, ['market_cap', 'close', 'price_date', 'shares'], batch_size=500)
        if vol_touched:
            Stock.objects.bulk_update(
                vol_touched, ['volume', 'volume_ratio', 'volume_z', 'volume_date'],
                batch_size=500)
        return len({s.code for s in touched + vol_touched})
