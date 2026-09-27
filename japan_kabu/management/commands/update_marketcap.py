"""J-Quants API（無料プラン）から銘柄マスタと決算（銘柄別指標用）を取得する

⚠️ 株価・時価総額・出来高は yfinance（update_jp_ranking）が担当する。
J-Quants無料プランは直近データに遅延があり当日株価を取れないため、この
コマンドは「マスタ＋決算＋発行済株式数＋期末株価」だけを更新する。
無料プランの遅延で直近日が取れない分はスキップして継続する（落とさない）。

使い方:
    python manage.py update_marketcap                     # 通常更新（前回開示日以降の差分のみ）
    python manage.py update_marketcap --full              # 過去380日分を取り直す
    python manage.py update_marketcap --backfill-years 5  # 決算を過去N年分バックフィル（初回のみ）

決算走査では発行済株式数と決算サマリー（銘柄別指標用）を同じAPIレスポンスから
取り込むため、追加のAPIコールは発生しない。cron で平日夜に実行する想定。
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
# 無料プランの遅延（直近約12週は 400 で取れない。2026-09-27 実測: 8/7 は 400・7/1 は 200）。
# これより新しい日付は取りに行かない（毎晩 36 営業日ぶん無駄に叩いていた）
FREE_PLAN_DELAY_DAYS = 80
# 遅延の見積もりが甘くても止まらないよう、連続でこの回数失敗したら走査を打ち切る
STOP_AFTER_FAILS = 3
# 週1回だけやること（マスタ全量・埋まらなかった期末株価の再試行）の曜日。⚠️ cron は平日のみ
# （10 21 * * 1-5）なので日曜にすると永遠に来ない。月曜=0
WEEKLY_WEEKDAY = 0
# 期末株価: 取れるようになって間もない期末日（遅延を抜けた直後）は毎晩、それ以外は週1回だけ再試行。
# ⚠️ 旧実装は空の行を全部毎晩取りに行き、米国株（J-Quants に無い）と遅延中の期末日で必ず失敗 →
#    429 のリトライ待ち（最大 225 秒/コール）が積み上がって毎晩 2.5 時間かかっていた（2026-09-27）
RECENT_WINDOW_DAYS = 14


class Command(BaseCommand):
    help = 'J-Quants APIから時価総額データを更新する'

    def add_arguments(self, parser):
        parser.add_argument('--full', action='store_true',
                            help='過去380日分を取り直す')
        parser.add_argument('--backfill-years', type=int, default=0,
                            help='決算走査を指定年数分遡る（通期決算の初回バックフィル用）')
        parser.add_argument('--master', action='store_true',
                            help='銘柄マスタを取り直す（既定は月曜だけ。--full でも取る）')

    def handle(self, *args, **options):
        # J-Quants無料プランは直近データに遅延があり、当日株価は取得できない。
        # 株価・時価総額・出来高は yfinance（update_jp_ranking）が担当する。
        # ここは無料プランで取れる「銘柄マスタ＋決算（銘柄別指標用）」だけを更新する。
        weekly = date.today().weekday() == WEEKLY_WEEKDAY
        # マスタは月に数件しか変わらない（取得 90 秒＋3,700 件の更新）ので週1回。
        if options['master'] or options['full'] or options['backfill_years'] or weekly:
            self.update_master()
        else:
            self.stdout.write('マスタ更新: スキップ（月曜と --master のみ）')
        self.update_shares(full=options['full'],
                           backfill_years=options['backfill_years'])
        self.fill_period_prices(retry_all=options['full'] or weekly)
        total = FinancialReport.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'完了: マスタ＋決算を更新（FinancialReport {total}件）'))

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
        # 上場廃止銘柄の削除（日本株のみ。米国株マスタには触れない）。
        # ⚠️ StockKarte.stock は OneToOne CASCADE、DiaryEntry.stock も紐づく。
        #   マスタに一時的に載らない銘柄を削除すると、手入力のカルテ/日記が道連れで
        #   消える事故が起きる（実際にサーバーで発生）。**手入力データが紐づく銘柄は
        #   絶対に削除しない**（上場廃止でもマスタから外れるだけで実害はない）。
        from diary.models import DiaryEntry
        from karte.models import StockKarte
        codes = {r['Code'] for r in rows}
        protected = set(StockKarte.objects.values_list('stock_id', flat=True))
        protected |= set(DiaryEntry.objects.filter(stock__isnull=False)
                         .values_list('stock_id', flat=True))
        removed, _ = (Stock.objects.filter(country='JP')
                      .exclude(code__in=codes)
                      .exclude(code__in=protected).delete())
        self.stdout.write(f'マスタ更新: {len(rows)}銘柄（削除 {removed}・カルテ/日記銘柄は保護）')

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
        count = report_count = skipped = fails = 0
        end = date.today() - timedelta(days=FREE_PLAN_DELAY_DAYS)   # 遅延で取れない直近は走査しない
        d = start
        while d <= end:
            if d.weekday() < 5:  # 土日は開示なし
                try:
                    fins = jquants.get_fins_by_date(d.isoformat())
                    fails = 0
                except Exception:  # noqa: BLE001  無料プランは直近が遅延で400。取れる範囲だけ使う
                    skipped += 1
                    fails += 1
                    fins = []
                    if fails >= STOP_AFTER_FAILS:
                        break   # 遅延の境界に達した（以降は全部失敗する）
                for r in fins:
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
        note = f'（直近{skipped}日は無料プランの遅延で取得不可・スキップ）' if skipped else ''
        self.stdout.write(
            f'株式数更新: {count}件 ／ 決算更新: {report_count}件（{start} 以降の開示分）{note}')

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

    def fill_period_prices(self, retry_all=False):
        """決算期末時点の終値をFinancialReportへ埋める（PER/PBR推移の計算用）

        期末日が休日の場合は直近の営業日まで最大7日遡る。
        期末日ごとに日付一括APIを1コール使う（同一期末日の全銘柄をまとめて処理）。
        対象は日本株だけ（米国株の期末株価は update_us_financials が yfinance で入れる。J-Quants には無い）。
        遅延で取れない期末日（直近 FREE_PLAN_DELAY_DAYS）は取りに行かない。
        遅延を抜けて間もない期末日は毎晩試し、それより古いのに空のまま（上場廃止・休場など）の
        期末日は retry_all（月曜・--full）のときだけ再試行する
        """
        today = date.today()
        limit = today - timedelta(days=FREE_PLAN_DELAY_DAYS)
        qs = (FinancialReport.objects
              .filter(close__isnull=True, per_end__isnull=False, stock__country='JP')
              .filter(per_end__lte=limit))
        if not retry_all:
            qs = qs.filter(per_end__gte=limit - timedelta(days=RECENT_WINDOW_DAYS))
        target_dates = sorted(set(qs.values_list('per_end', flat=True)))
        filled_dates = 0
        for per_end in target_dates:
            prices, actual = self._closes_near(per_end)
            if not prices:
                continue
            reps = list(FinancialReport.objects.filter(per_end=per_end, close__isnull=True, stock__country='JP'))
            for rep in reps:
                c = prices.get(rep.stock_id)
                if c is not None:
                    rep.close = c
                    rep.close_date = actual
            FinancialReport.objects.bulk_update(reps, ['close', 'close_date'], batch_size=500)
            filled_dates += 1
        self.stdout.write(f'期末株価取得: {filled_dates}日付分（対象 {len(target_dates)} 日付'
                          f'{"・全部再試行" if retry_all else "・遅延を抜けた直後のみ"}）')

    @staticmethod
    def _closes_near(target):
        """target以前の直近営業日の全銘柄終値を返す: ({code: close}, 実際の日付)"""
        for offset in range(8):
            d = target - timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            time.sleep(jquants.REQUEST_WAIT)
            try:
                bars = jquants.get_bars_by_date(d.isoformat())
            except Exception:  # noqa: BLE001  無料プランは直近日が遅延で400。古い期末日は取れる
                bars = []
            if bars:
                return (
                    {b['Code']: b['C'] for b in bars if b.get('C') is not None},
                    d,
                )
        return {}, None
