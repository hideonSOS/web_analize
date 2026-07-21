"""J-Quants APIから銘柄マスタ・株価・発行済株式数・通期決算を取得し時価総額を更新する

使い方:
    python manage.py update_marketcap                     # 通常更新（前回開示日以降の差分のみ）
    python manage.py update_marketcap --full              # 過去380日分を取り直す
    python manage.py update_marketcap --backfill-years 5  # 通期決算を過去5年分バックフィル（初回のみ）

決算走査では発行済株式数（時価総額用）と通期決算サマリー（銘柄別指標用）を
同じAPIレスポンスから取り込むため、追加のAPIコールは発生しない。
cron/タスクスケジューラで平日夜に実行する想定。
"""
import time
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db.models import Max

from japan_kabu import jquants
from japan_kabu.models import FinancialReport, Stock

# 保存対象の決算期種別（通期 + 四半期）
PERIOD_TYPES = ('FY', '1Q', '2Q', '3Q')


def _num(v):
    """空文字列・None を None に、それ以外を float に正規化する"""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None

# 普通株式のみ対象（ETF・REIT等を除外）
PRODUCT_CATEGORY_STOCK = '011'
TARGET_MARKETS = ('プライム', 'スタンダード', 'グロース')
# 初回同期で発行済株式数を遡る日数（四半期開示を確実にカバー）
FULL_SYNC_DAYS = 380


class Command(BaseCommand):
    help = 'J-Quants APIから時価総額データを更新する'

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true',
                            help='過去380日分を取り直す')
        parser.add_argument('--backfill-years', type=int, default=0,
                            help='決算走査を指定年数分遡る（通期決算の初回バックフィル用）')

    def handle(self, *args, **options):
        self.update_master()
        latest_date = self.update_prices()
        self.update_shares(full=options['full'],
                           backfill_years=options['backfill_years'])
        self.compute_market_cap()
        self.fill_period_prices()
        total = Stock.objects.filter(market_cap__isnull=False).count()
        self.stdout.write(self.style.SUCCESS(
            f'完了: 時価総額算出済み {total}銘柄（株価基準日 {latest_date}）'))

    def update_master(self):
        """銘柄マスタの取り込み（普通株式・国内3市場のみ）"""
        rows = [r for r in jquants.get_master()
                if r.get('ProdCat') == PRODUCT_CATEGORY_STOCK
                and r.get('MktNm') in TARGET_MARKETS]
        for r in rows:
            Stock.objects.update_or_create(
                code=r['Code'],
                defaults={
                    'display_code': r['Code'][:4],
                    'country': 'JP',
                    'name': r['CoName'],
                    'market': r.get('MktNm', ''),
                    'sector33': r.get('S33Nm', ''),
                    'sector17': r.get('S17Nm', ''),
                },
            )
        # 上場廃止銘柄の削除（日本株のみ。米国株マスタには触れない）
        codes = {r['Code'] for r in rows}
        removed, _ = Stock.objects.filter(country='JP').exclude(code__in=codes).delete()
        self.stdout.write(f'マスタ更新: {len(rows)}銘柄（削除 {removed}）')

    def update_prices(self):
        """最新営業日の終値を全銘柄に反映する"""
        # 基準銘柄（トヨタ）の直近データから最新営業日を得る
        from_date = (date.today() - timedelta(days=14)).isoformat()
        ref = jquants.get_bars_by_code('7203', from_date=from_date)
        if not ref:
            self.stderr.write('株価の最新日付が取得できませんでした')
            return None
        latest = ref[-1]['Date']

        bars = jquants.get_bars_by_date(latest)
        price_date = datetime.strptime(latest, '%Y-%m-%d').date()
        stocks = {s.code: s for s in Stock.objects.all()}
        updates = []
        for b in bars:
            s = stocks.get(b['Code'])
            if s and b.get('C') is not None:
                s.close = b['C']
                s.price_date = price_date
                updates.append(s)
        Stock.objects.bulk_update(updates, ['close', 'price_date'], batch_size=500)
        self.stdout.write(f'株価更新: {len(updates)}銘柄（{latest}）')
        return latest

    def update_shares(self, full=False, backfill_years=0):
        """開示日ベースで決算サマリーを走査し、発行済株式数と通期決算を更新する"""
        last = Stock.objects.aggregate(m=Max('shares_disc_date'))['m']
        if backfill_years > 0:
            start = date.today() - timedelta(days=365 * backfill_years)
        elif full or last is None:
            start = date.today() - timedelta(days=FULL_SYNC_DAYS)
        else:
            start = last - timedelta(days=3)  # 取りこぼし防止に少し重ねる
        stocks = {s.code: s for s in Stock.objects.all()}
        # 既存の決算レコード（訂正開示の新旧判定用）: {(code, per_end): report}
        existing = {
            (r.stock_id, r.per_end): r
            for r in FinancialReport.objects.all()
        }
        count = report_count = 0
        d = start
        while d <= date.today():
            if d.weekday() < 5:  # 土日は開示なし
                for r in jquants.get_fins_by_date(d.isoformat()):
                    s = stocks.get(r.get('Code'))
                    if s is None:
                        continue
                    disc = datetime.strptime(r['DiscDate'], '%Y-%m-%d').date()
                    sh = r.get('ShOutFY')
                    if sh and (s.shares_disc_date is None or disc >= s.shares_disc_date):
                        s.shares = int(sh)
                        s.shares_disc_date = disc
                        count += 1
                    if self._store_report(s, r, disc, existing):
                        report_count += 1
            d += timedelta(days=1)
        Stock.objects.bulk_update(
            stocks.values(), ['shares', 'shares_disc_date'], batch_size=500)
        self.stdout.write(
            f'株式数更新: {count}件 ／ 通期決算更新: {report_count}件（{start} 以降の開示分）')

    @staticmethod
    def _store_report(stock, r, disc, existing):
        """決算サマリー（通期・四半期）をFinancialReportに保存する。保存したらTrue

        業績予想修正・配当予想修正の開示（決算数値が空）は除外し、
        決算短信本体（DocTypeに FinancialStatements を含む）のみ保存する。
        """
        if 'FinancialStatements' not in (r.get('DocType') or ''):
            return False
        if (r.get('CurPerType') not in PERIOD_TYPES
                or not r.get('CurPerEn') or not r.get('CurFYEn')):
            return False
        per_end = datetime.strptime(r['CurPerEn'], '%Y-%m-%d').date()
        key = (stock.code, per_end)
        rep = existing.get(key)
        if rep is not None and rep.disc_date > disc:
            return False  # 手元の方が新しい（訂正開示済み）
        if rep is None:
            rep = FinancialReport(stock=stock, per_end=per_end)
            existing[key] = rep
        rep.per_type = r['CurPerType']
        rep.fy_end = datetime.strptime(r['CurFYEn'], '%Y-%m-%d').date()
        rep.disc_date = disc
        rep.sales = _int(r.get('Sales'))
        rep.op = _int(r.get('OP'))
        rep.np = _int(r.get('NP'))
        rep.eps = _num(r.get('EPS'))
        rep.bps = _num(r.get('BPS'))
        rep.total_assets = _int(r.get('TA'))
        rep.equity = _int(r.get('Eq'))
        rep.equity_ratio = _num(r.get('EqAR'))
        rep.div_ann = _num(r.get('DivAnn'))
        rep.nx_div_ann = _num(r.get('NxFDivAnn'))
        rep.nx_np = _int(r.get('NxFNp'))
        rep.shares = _int(r.get('ShOutFY'))
        rep.save()
        return True

    def compute_market_cap(self):
        updates = []
        for s in Stock.objects.all():
            if s.close and s.shares:
                s.market_cap = int(s.close * s.shares)
                updates.append(s)
        Stock.objects.bulk_update(updates, ['market_cap'], batch_size=500)
        self.stdout.write(f'時価総額算出: {len(updates)}銘柄')

    def fill_period_prices(self):
        """決算期末時点の終値をFinancialReportへ埋める（PER/PBR推移の計算用）

        期末日が休日の場合は直近の営業日まで最大7日遡る。
        期末日ごとに日付一括APIを1コール使う（同一期末日の全銘柄をまとめて処理）。
        """
        target_dates = sorted(set(
            FinancialReport.objects.filter(close__isnull=True, per_end__isnull=False)
            .filter(per_end__lt=date.today())
            .values_list('per_end', flat=True)
        ))
        filled_dates = 0
        for per_end in target_dates:
            prices, actual = self._closes_near(per_end)
            if not prices:
                continue
            reps = list(FinancialReport.objects.filter(per_end=per_end, close__isnull=True))
            for rep in reps:
                c = prices.get(rep.stock_id)
                if c is not None:
                    rep.close = c
                    rep.close_date = actual
            FinancialReport.objects.bulk_update(reps, ['close', 'close_date'], batch_size=500)
            filled_dates += 1
        self.stdout.write(f'期末株価取得: {filled_dates}日付分')

    @staticmethod
    def _closes_near(target):
        """target以前の直近営業日の全銘柄終値を返す: ({code: close}, 実際の日付)"""
        for offset in range(8):
            d = target - timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            time.sleep(jquants.REQUEST_WAIT)
            bars = jquants.get_bars_by_date(d.isoformat())
            if bars:
                return (
                    {b['Code']: b['C'] for b in bars if b.get('C') is not None},
                    d,
                )
        return {}, None
