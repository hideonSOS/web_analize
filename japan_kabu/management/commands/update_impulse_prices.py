"""独自セクター別インパルス用の日次終値を取得する（対象は impulse.py の定義銘柄のみ）

セクター別インパルス（時系列ヒートマップ）は日々の騰落率の履歴が必要なため、
定義銘柄の調整後終値を DailyPrice に蓄積する。JP/US とも yfinance で取る
（J-Quants無料プランは直近が約12週遅延するため使えない。東証は <コード>.T）。

対象は japan_kabu/impulse.py の IMPULSE_SECTORS に列挙した数銘柄だけなので
1日あたり十数コールで済む。差分同期（保存済み最終日の翌日から取得）。

    python manage.py update_impulse_prices              # 差分のみ（cron想定）
    python manage.py update_impulse_prices --days 60    # 初回。60暦日分を遡る
    python manage.py update_impulse_prices --full       # 全期間（--days分）取り直し

⚠️ 必ず調整後終値（auto_adjust=True）。未調整だと分割時に騰落率が壊れる。
⚠️ 市場クローズ前に実行しても安全なよう、**取引時間中の未確定当日バーは保存しない**
   （_cutoff_date）。保存すると差分同期のため日中の途中値が永久に残ってしまう。
   cron は米国クローズ確定後の JST 朝7時（us_ranking_update.sh と同枠）が最適。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand

from japan_kabu.impulse import IMPULSE_SECTORS, impulse_universe
from japan_kabu.models import DailyPrice, Stock

DEFAULT_DAYS = 60   # 表示20営業日 + 余裕（暦日）


class Command(BaseCommand):
    help = 'セクター別インパルス用の日次終値を取得する（impulse.py の定義銘柄のみ）'

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
            for stock, ticker in self._targets(country):
                try:
                    n = self._sync(stock, ticker, start, cutoff, full=options['full'])
                    ok += 1
                    self.stdout.write(f'  {stock.display_code:6} {country} +{n}件')
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

    @staticmethod
    def _targets(country):
        """[(Stock, yfinanceティッカー)] を返す。マスタに無いコードは警告なしで飛ばさず例外に出る"""
        codes = impulse_universe(country)
        if country == 'JP':
            found = {s.display_code: s for s in
                     Stock.objects.filter(country='JP', display_code__in=codes)}
            return [(found[c], f'{c}.T') for c in codes if c in found]
        found = {s.display_code: s for s in
                 Stock.objects.filter(country='US', code__in=[f'US-{c}' for c in codes])}
        return [(found[c], c.replace('.', '-')) for c in codes if c in found]

    def _sync(self, stock, ticker, start, cutoff, full=False):
        """未取得期間だけ取得して保存する。戻り値は追加件数"""
        from_date = start
        if not full:
            latest = (DailyPrice.objects.filter(stock=stock)
                      .order_by('-date').values_list('date', flat=True).first())
            if latest:
                from_date = max(start, latest + timedelta(days=1))
                if from_date > date.today():
                    return 0

        rows = [(d, c) for d, c in self._fetch(ticker, from_date) if d <= cutoff]
        if not rows:
            return 0
        objs = [DailyPrice(stock=stock, date=d, close=c) for d, c in rows]
        DailyPrice.objects.bulk_create(objs, ignore_conflicts=True, batch_size=1000)
        return len(objs)

    @staticmethod
    def _fetch(ticker, from_date):
        """yfinance。auto_adjust=True で分割・配当調整済みの終値を取る"""
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
