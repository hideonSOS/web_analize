"""市場指数（日経平均・S&P500）の日次終値を取得する（下落上等ページ用）

下落上等ページ（/portfolio/drill/）の下落メーターは「52週高値から現在何%下か」を
表示する。その材料となる指数の調整後終値を IndexPrice に蓄積する。

    python manage.py update_index_prices               # 差分のみ（cron想定）
    python manage.py update_index_prices --days 480    # 初回。52週高値の算出に1年強必要

⚠️ 必ず調整後終値（auto_adjust=True）。
⚠️ 取引時間中の未確定当日バーは保存しない（update_impulse_prices と同じガード）。
   cron は us_ranking_update.sh（JST朝7時）と同枠でよい。
⚠️ yfinance の指数取得は "possibly delisted" で一時的に空が返る既知の癖がある
   （実在銘柄でも起きる。S&P500ランキングの取りこぼしと同種）。リトライで吸収する。
"""
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from japan_kabu.models import IndexPrice

DEFAULT_DAYS = 480   # 52週(252営業日)高値 + 余裕（暦日）

# (symbol, yfinanceティッカー, 市場の国。クローズ判定に使う)
INDEXES = [
    ('N225', '^N225', 'JP'),
    ('GSPC', '^GSPC', 'US'),
]

RETRIES = 4


class Command(BaseCommand):
    help = '市場指数（日経平均・S&P500）の日次終値を取得する（下落上等ページ用）'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=DEFAULT_DAYS,
                            help=f'遡る暦日数（既定 {DEFAULT_DAYS}日）。初回のみ意味を持つ')
        parser.add_argument('--full', action='store_true',
                            help='保存済みを無視して指定日数分を取り直す')

    def handle(self, *args, **options):
        start = date.today() - timedelta(days=options['days'])
        ok = ng = 0
        for symbol, ticker, country in INDEXES:
            try:
                n = self._sync(symbol, ticker, start,
                               self._cutoff_date(country), full=options['full'])
                ok += 1
                self.stdout.write(f'  {symbol:6} +{n}件')
            except Exception as e:  # noqa: BLE001  1指数の失敗で全体を止めない
                ng += 1
                self.stderr.write(f'  {symbol:6} 失敗: {e}')
        self.stdout.write(self.style.SUCCESS(f'指数日次終値: {ok}指数成功 / {ng}指数失敗'))
        if ng:
            raise SystemExit(1)

    @staticmethod
    def _cutoff_date(country):
        """保存してよい最新の日付（クローズ前は当日バーが未確定なので前日まで）"""
        if country == 'US':
            now = datetime.now(ZoneInfo('America/New_York'))
            close = (16, 5)
        else:
            now = datetime.now(ZoneInfo('Asia/Tokyo'))
            close = (15, 35)
        if (now.hour, now.minute) < close:
            return now.date() - timedelta(days=1)
        return now.date()

    def _sync(self, symbol, ticker, start, cutoff, full=False):
        from_date = start
        if not full:
            latest = (IndexPrice.objects.filter(symbol=symbol)
                      .order_by('-date').values_list('date', flat=True).first())
            if latest:
                from_date = max(start, latest + timedelta(days=1))
                if from_date > date.today():
                    return 0

        rows = [(d, c) for d, c in self._fetch(ticker, from_date) if d <= cutoff]
        if not rows:
            return 0
        objs = [IndexPrice(symbol=symbol, date=d, close=c) for d, c in rows]
        IndexPrice.objects.bulk_create(objs, ignore_conflicts=True, batch_size=1000)
        return len(objs)

    @staticmethod
    def _fetch(ticker, from_date):
        import yfinance as yf

        df = None
        for attempt in range(RETRIES):
            df = yf.download(ticker, start=from_date.isoformat(),
                             progress=False, auto_adjust=True)
            if df is not None and not df.empty:
                break
            time.sleep(3 + attempt * 3)   # "possibly delisted" の一時失敗を吸収
        if df is None or df.empty:
            return []
        closes = df['Close']
        if hasattr(closes, 'columns'):
            closes = closes.iloc[:, 0]
        out = []
        for idx, v in closes.items():
            if v != v:                    # NaN除外
                continue
            d = idx.date() if hasattr(idx, 'date') else idx
            out.append((d, float(v)))
        return out
