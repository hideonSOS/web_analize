"""米国株の財務データを取得してカルテの指標で使えるようにする

対象は「カルテを作った銘柄」と「売買日記に登場した銘柄」の米国株だけ。
全12,484銘柄は取らない（1銘柄1コールのため）。

取得元（2026-09-16 に変更・ユーザー決定）:
  1. **SEC EDGAR companyfacts**（公式・無料・キー不要・一次情報。`japan_kabu/edgar.py`）
     10年以上の FY/四半期が揃う。Q4 は FY−(Q1+Q2+Q3) で補完。値は提出時点のまま（分割調整なし）
  2. EDGAR に無い銘柄（ADR の一部・CIK 未登録・純利益タグ無し）だけ **yfinance にフォールバック**
  期末株価はどちらも yfinance の日足（EDGAR 経路は **調整前** 終値。提出時点の株数と整合させるため）

日本株との違い:
  - 日本株(J-Quants)の四半期は **期初からの累計**、米国株の四半期は **その四半期単独**
  そのため per_type を分けて保存する: FY = 通期 / Q = 米国式の単独四半期（日本株は 1Q/2Q/3Q）。
  TTM の計算方法もビュー側で国別に分岐する（indicators.py）。
  ⚠️ 米国株は FY と Q4 が同じ期末日になるため、一意制約は (stock, per_end, per_type)（0011）

使い方:
    python manage.py update_us_financials
    python manage.py update_us_financials --ticker MSTR   # 1銘柄だけ試す
    python manage.py update_us_financials --source yf     # 強制的に yfinance（比較・保険用）
"""
from datetime import timedelta

from django.core.management.base import BaseCommand

from diary.models import DiaryEntry
from japan_kabu import edgar
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
    help = 'カルテ/売買日記に登録された米国株の財務データを取得する（SEC EDGAR → 無ければ yfinance）'

    def add_arguments(self, parser):
        parser.add_argument('--ticker', help='この銘柄だけ処理する（例: MSTR）')
        parser.add_argument('--source', choices=['auto', 'edgar', 'yf'], default='auto',
                            help='auto=EDGAR→yfinance の順（既定）/ edgar のみ / yf のみ')

    def handle(self, *args, **options):
        stocks = self._targets(options.get('ticker'))
        if not stocks:
            self.stdout.write('対象の米国株がありません（カルテか売買日記に登録してください）')
            return
        source = options['source']
        ok = failed = 0
        for s in stocks:
            try:
                n, used = self._fetch_one(s, source)
                self.stdout.write(f'  {s.display_code:<6} {n}期を保存 [{used}]  {s.name[:30]}')
                ok += 1
            except Exception as e:   # noqa: BLE001  1銘柄の失敗で他を止めない
                self.stderr.write(f'  {s.display_code:<6} 取得失敗: {type(e).__name__}: {e}')
                failed += 1
        self.stdout.write(self.style.SUCCESS(f'完了: {ok}銘柄を更新（失敗 {failed}）'))

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

    def _fetch_one(self, stock, source='auto'):
        """1銘柄。(保存した期数, 使った取得元) を返す"""
        if source in ('auto', 'edgar'):
            try:
                n = self._fetch_edgar(stock)
                if n:
                    return n, 'edgar'
                if source == 'edgar':
                    return 0, 'edgar'
                self.stdout.write(f'  {stock.display_code:<6} EDGAR に決算が無いので yfinance で代替')
            except Exception as e:   # noqa: BLE001
                if source == 'edgar':
                    raise
                self.stdout.write(f'  {stock.display_code:<6} EDGAR 失敗（{type(e).__name__}: {e}）→ yfinance で代替')
        return self._fetch_one_yf(stock), 'yf'

    # ------------------------------------------------------------------ EDGAR
    def _fetch_edgar(self, stock):
        cik = edgar.ticker_to_cik(stock.display_code)
        if cik is None:
            return 0
        facts = edgar.fetch_companyfacts(cik)
        rows = edgar.build_reports(facts)
        if not rows:
            return 0
        # 期末株価: 調整前（提出時点の株数・EPS と整合させる）。yfinance 1コール
        closes = self._raw_closes(stock.display_code)
        saved = 0
        kept = []
        for r in rows:
            equity, assets, shares = r['equity'], r['total_assets'], r['shares']
            obj, _ = FinancialReport.objects.update_or_create(
                stock=stock, per_end=r['per_end'], per_type=r['per_type'],
                defaults={
                    'fy_end': r['per_end'],
                    'disc_date': r['per_end'] + timedelta(days=40),
                    'sales': self._as_int(r['sales']),
                    'op': self._as_int(r['op']),
                    'np': self._as_int(r['np']),
                    'eps': r['eps'],
                    'total_assets': self._as_int(assets),
                    'equity': self._as_int(equity),
                    'equity_ratio': (equity / assets) if (equity and assets) else None,
                    'bps': (equity / shares) if (equity and shares) else None,
                    'shares': self._as_int(shares),
                    'div_ann': r['div_ann'],
                    'close': self._close_at(closes, r['per_end']),
                    'close_date': r['per_end'],
                    'fin_currency': 'USD',   # EDGAR は USD 単位のタグだけ採る（edgar._entries）
                },
            )
            kept.append(obj.pk)
            saved += 1
        # 旧 yfinance 経路の行（期末が月末日で EDGAR の実際の期末日とズレる）が残ると同じ期が二重に
        # 並び、TTM（直近4四半期の合計）と業績推移が狂う。EDGAR で取れた銘柄は EDGAR の行だけにする
        FinancialReport.objects.filter(stock=stock).exclude(pk__in=kept).delete()
        return saved

    @staticmethod
    def _raw_closes(ticker):
        import yfinance as yf
        try:
            hist = yf.Ticker(ticker).history(period='max', auto_adjust=False)
            return hist['Close'] if hist is not None and not hist.empty else None
        except Exception:   # noqa: BLE001  株価が取れなくても決算は保存する
            return None

    # --------------------------------------------------------------- yfinance
    def _fetch_one_yf(self, stock):
        """従来の yfinance 経路（四半期は直近5〜6期しか無い）。EDGAR に無い銘柄の保険"""
        import yfinance as yf

        t = yf.Ticker(stock.display_code)
        # 財務の通貨。ADR（PayPay 等）は株価が USD でも財務が JPY で返る。取り違えると
        # 円の営業利益を「百万ドル」として為替を掛け、155倍に化ける（実際に起きた 2026-09-16）
        try:
            fin_ccy = (t.info.get('financialCurrency') or 'USD').upper()[:3]
        except Exception:   # noqa: BLE001
            fin_ccy = 'USD'
        qi, qb = t.quarterly_income_stmt, t.quarterly_balance_sheet
        ai, ab = t.income_stmt, t.balance_sheet
        divs = t.dividends
        hist = t.history(period='6y', auto_adjust=True)
        closes = hist['Close'] if hist is not None and not hist.empty else None

        saved = 0
        for per_type, inc, bal in (('FY', ai, ab), ('Q', qi, qb)):
            if inc is None or inc.empty:
                continue
            for col in inc.columns:
                per_end = col.date() if hasattr(col, 'date') else col
                np_ = _pick(inc, ['Net Income', 'Net Income Common Stockholders'], col)
                if np_ is None:
                    continue
                bcol = self._match_column(bal, col)
                equity = _pick(bal, ['Stockholders Equity', 'Common Stock Equity'], bcol)
                assets = _pick(bal, ['Total Assets'], bcol)
                shares = _pick(bal, ['Share Issued', 'Ordinary Shares Number'], bcol)
                FinancialReport.objects.update_or_create(
                    stock=stock, per_end=per_end, per_type=per_type,
                    defaults={
                        'fy_end': per_end,
                        'disc_date': per_end + timedelta(days=40),
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
                        'fin_currency': fin_ccy,
                    },
                )
                saved += 1
        return saved

    @staticmethod
    def _as_int(v):
        return int(v) if v is not None else None

    @staticmethod
    def _match_column(df, col):
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
        if divs is None or len(divs) == 0:
            return None
        try:
            s = divs.copy()
            s.index = s.index.tz_localize(None)
            window = s[(s.index.date > per_end - timedelta(days=365)) & (s.index.date <= per_end)]
            total = float(window.sum())
            return total if total > 0 else None
        except Exception:   # noqa: BLE001
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
        except Exception:   # noqa: BLE001
            return None
