"""カルテ・売買日記に登録した銘柄の日次終値を取得する（約3年分）

ドローダウン（高値からの下落率）とレンジ内位置の算出元データ。
市場全体ではなく「自分が登録した銘柄」だけなので十数コールで済む。

    python manage.py update_daily_prices                # 差分のみ（cron想定）
    python manage.py update_daily_prices --years 3      # 初回バックフィル
    python manage.py update_daily_prices --full         # 全期間を取り直す
    python manage.py update_daily_prices --code 6758    # 1銘柄だけ試す

⚠️ 必ず調整後終値を保存する（JP:AdjC / US:auto_adjust=True）。
未調整だと株式分割時に株価が飛び、高値が実態の数倍になる。
"""
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from japan_kabu import jquants
from japan_kabu.models import DailyPrice, Stock

DEFAULT_YEARS = 3


class Command(BaseCommand):
    help = 'カルテ/日記に登録した銘柄の日次終値を取得する（ドローダウン算出用）'

    def add_arguments(self, parser):
        parser.add_argument('--years', type=int, default=DEFAULT_YEARS,
                            help=f'遡る年数（既定 {DEFAULT_YEARS}年）。初回のみ意味を持つ')
        parser.add_argument('--full', action='store_true',
                            help='保存済みを無視して指定年数分を取り直す')
        parser.add_argument('--code', type=str, default='',
                            help='この表示コードの銘柄だけ処理する（例: 6758 / NVDA）')

    def handle(self, *args, **options):
        stocks = self._targets(options['code'])
        if not stocks:
            self.stdout.write('対象銘柄がありません（カルテか売買日記に登録してください）')
            return

        start = date.today() - timedelta(days=365 * options['years'] + 10)
        ok = ng = 0
        for s in stocks:
            try:
                n = self._sync(s, start, full=options['full'])
                ok += 1
                self.stdout.write(f'  {s.display_code:6} {s.country} +{n}件')
            except Exception as e:
                # 1銘柄の失敗で全体を止めない（yfinance/APIの一時障害を想定）
                ng += 1
                self.stderr.write(f'  {s.display_code:6} {s.country} 失敗: {e}')

        self.stdout.write(self.style.SUCCESS(
            f'日次終値: {ok}銘柄成功 / {ng}銘柄失敗'))

    @staticmethod
    def _targets(code):
        """カルテ or 売買日記に登場する銘柄（JP/US両方）"""
        from diary.models import DiaryEntry
        from karte.models import StockKarte

        if code:
            return list(Stock.objects.filter(display_code=code))

        used = set(StockKarte.objects.values_list('stock_id', flat=True))
        used |= set(DiaryEntry.objects.values_list('stock_id', flat=True))
        return list(Stock.objects.filter(code__in=used).order_by('country', 'code'))

    def _sync(self, stock, start, full=False):
        """未取得期間だけ取得して保存する。戻り値は追加件数"""
        from_date = start
        if not full:
            latest = (DailyPrice.objects.filter(stock=stock)
                      .order_by('-date').values_list('date', flat=True).first())
            if latest:
                # 既にある分の翌日から。全期間を取り直さないための差分同期
                from_date = max(start, latest + timedelta(days=1))
                if from_date > date.today():
                    return 0

        rows = (self._fetch_us(stock, from_date) if stock.country == 'US'
                else self._fetch_jp(stock, from_date))
        if not rows:
            return 0
        objs = [DailyPrice(stock=stock, date=d, close=c) for d, c in rows]
        DailyPrice.objects.bulk_create(objs, ignore_conflicts=True, batch_size=1000)
        return len(objs)

    @staticmethod
    def _fetch_jp(stock, from_date):
        """J-Quants。AdjC(調整後終値)を使う。無ければC(終値)で代替"""
        bars = jquants.get_bars_by_code(stock.code, from_date=from_date.isoformat())
        out = []
        for b in bars:
            c = b.get('AdjC')
            if c is None:
                c = b.get('C')
            if c is None or not b.get('Date'):
                continue
            d = datetime.strptime(b['Date'], '%Y-%m-%d').date()
            out.append((d, float(c)))
        return out

    @staticmethod
    def _fetch_us(stock, from_date):
        """yfinance。auto_adjust=True で分割・配当調整済みの終値を取る"""
        import yfinance as yf

        df = yf.download(stock.display_code, start=from_date.isoformat(),
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return []
        closes = df['Close']
        # 複数銘柄指定時と同じ形（DataFrame）で返ることがあるので1列に均す
        if hasattr(closes, 'columns'):
            closes = closes.iloc[:, 0]
        out = []
        for idx, v in closes.items():
            if v != v:            # NaN除外
                continue
            d = idx.date() if hasattr(idx, 'date') else idx
            out.append((d, float(v)))
        return out
