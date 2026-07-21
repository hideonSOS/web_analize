"""米国株の財務データを取得して銘柄別指標で使えるようにする

対象は「カルテを作った銘柄」と「売買日記に登場した銘柄」の米国株だけ。
全12,484銘柄は取らない（yfinanceは1銘柄1コールのため）。

日本株との違い:
  - 日本株(J-Quants)の四半期は **期初からの累計**
  - 米国株(yfinance)の四半期は **その四半期単独**
  そのため per_type を分けて保存する:
      FY = 通期 / Q = 米国式の単独四半期（日本株は 1Q/2Q/3Q）
  TTMの計算方法もビュー側で国別に分岐する。

使い方:
    python manage.py update_us_financials
    python manage.py update_us_financials --ticker MSTR   # 1銘柄だけ試す
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from diary.models import DiaryEntry
from japan_kabu.models import FinancialReport, Stock
from karte.models import StockKarte


def _num(v):
    """NaN・欠損を None にする"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None    # NaN は自身と等しくない


def _pick(df, keys, col):
    """複数の候補ラベルから最初に見つかった値を返す"""
    if df is None or df.empty or col not in df.columns:
        return None
    for k in keys:
        if k in df.index:
            v = _num(df.loc[k, col])
            if v is not None:
                return v
    return None


class Command(BaseCommand):
    help = 'カルテ/売買日記に登録された米国株の財務データを取得する（yfinance）'

    def add_arguments(self, parser):
        parser.add_argument('--ticker', help='この銘柄だけ処理する（例: MSTR）')

    def handle(self, *args, **options):
        stocks = self._targets(options.get('ticker'))
        if not stocks:
            self.stdout.write('対象の米国株がありません（カルテか売買日記に登録してください）')
            return

        ok = failed = 0
        for s in stocks:
            try:
                n = self._fetch_one(s)
                self.stdout.write(f'  {s.display_code:<6} {n}期を保存  {s.name[:30]}')
                ok += 1
            except Exception as e:
                self.stderr.write(f'  {s.display_code:<6} 取得失敗: {type(e).__name__}: {e}')
                failed += 1
        self.stdout.write(self.style.SUCCESS(
            f'完了: {ok}銘柄を更新（失敗 {failed}）'))

    @staticmethod
    def _targets(ticker):
        """カルテ or 売買日記に登録された米国株"""
        if ticker:
            return list(Stock.objects.filter(country='US', display_code=ticker.upper()))
        codes = set(StockKarte.objects.filter(stock__country='US')
                    .values_list('stock_id', flat=True))
        codes |= set(DiaryEntry.objects.filter(stock__country='US')
                     .values_list('stock_id', flat=True))
        return list(Stock.objects.filter(code__in=codes))

    def _fetch_one(self, stock):
        import yfinance as yf

        t = yf.Ticker(stock.display_code)
        qi, qb = t.quarterly_income_stmt, t.quarterly_balance_sheet
        ai, ab = t.income_stmt, t.balance_sheet
        divs = t.dividends
        # 期末時点の株価を引くための日足（PER/PBRの推移に使う）
        hist = t.history(period='6y', auto_adjust=True)
        closes = hist['Close'] if hist is not None and not hist.empty else None

        saved = 0
        # 通期(FY) → 四半期(Q) の順で保存する
        for per_type, inc, bal in (('FY', ai, ab), ('Q', qi, qb)):
            if inc is None or inc.empty:
                continue
            for col in inc.columns:
                per_end = col.date() if hasattr(col, 'date') else col
                np_ = _pick(inc, ['Net Income', 'Net Income Common Stockholders'], col)
                if np_ is None:
                    continue    # 損益が取れない期は保存しない

                # 貸借対照表は同じ期末日の列を探す（無い場合は None のまま）
                bcol = self._match_column(bal, col)
                equity = _pick(bal, ['Stockholders Equity', 'Common Stock Equity'], bcol)
                assets = _pick(bal, ['Total Assets'], bcol)
                shares = _pick(bal, ['Share Issued', 'Ordinary Shares Number'], bcol)

                FinancialReport.objects.update_or_create(
                    stock=stock, per_end=per_end,
                    defaults={
                        'per_type': per_type,
                        'fy_end': per_end,          # 米国株はFY紐付けを使わないので同値
                        'disc_date': per_end + timedelta(days=40),  # 開示は期末の約40日後
                        'sales': self._as_int(_pick(inc, ['Total Revenue', 'Operating Revenue'], col)),
                        'op': self._as_int(_pick(inc, ['Operating Income'], col)),
                        'np': self._as_int(np_),
                        'eps': _pick(inc, ['Diluted EPS', 'Basic EPS'], col),
                        'total_assets': self._as_int(assets),
                        'equity': self._as_int(equity),
                        'equity_ratio': (equity / assets) if (equity and assets) else None,
                        'bps': (equity / shares) if (equity and shares) else None,
                        'shares': self._as_int(shares),
                        'div_ann': self._trailing_dividend(divs, per_end),
                        'close': self._close_at(closes, per_end),
                        'close_date': per_end,
                    },
                )
                saved += 1
        return saved

    @staticmethod
    def _as_int(v):
        return int(v) if v is not None else None

    @staticmethod
    def _match_column(df, col):
        """損益と同じ期末日の列を貸借対照表から探す（数日ずれる場合があるので近傍も見る）"""
        if df is None or df.empty:
            return None
        if col in df.columns:
            return col
        for c in df.columns:
            try:
                if abs((c - col).days) <= 5:
                    return c
            except TypeError:
                continue
        return None

    @staticmethod
    def _trailing_dividend(divs, per_end):
        """その期末までの直近12か月の配当合計"""
        if divs is None or len(divs) == 0:
            return None
        try:
            s = divs.copy()
            s.index = s.index.tz_localize(None)
            window = s[(s.index.date > per_end - timedelta(days=365))
                       & (s.index.date <= per_end)]
            total = float(window.sum())
            return total if total > 0 else None
        except Exception:
            return None

    @staticmethod
    def _close_at(closes, per_end):
        """その期末日以前で最も新しい終値"""
        if closes is None or len(closes) == 0:
            return None
        try:
            s = closes.copy()
            s.index = s.index.tz_localize(None)
            past = s[s.index.date <= per_end]
            return float(past.iloc[-1]) if len(past) else None
        except Exception:
            return None
