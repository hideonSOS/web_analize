"""日本株のデュポン5分解用に、税引前利益（pretax）と経常利益（ordinary）を既存の決算行へ埋める

    python manage.py update_jp_pretax              # カルテ・保有中の日本株
    python manage.py update_jp_pretax --code 6758  # 1銘柄だけ

- 税引前利益: yfinance（<コード>.T）の通期損益計算書 'Pretax Income'（直近4〜5期）→ 通期(FY)の行へ。
  J-Quants の決算サマリーには税引前利益の項目が無いため（2026-09-27 に確認）
- 経常利益: J-Quants の銘柄別 /fins/summary の OdP（無料プランは直近約2年分）→ 通期・四半期の行へ。
  ⚠️ IFRS 採用企業（ソニー・トヨタ・アサヒ等）は経常利益が無く空で返る。税引前利益が取れない年の代わり
新しい決算の経常利益は夜の update_marketcap が保存時に入れる。このコマンドは夜バッチでは月曜だけ回す
（1銘柄 yfinance 1コール＋J-Quants 1コール・カルテ＋保有で20銘柄前後）。
"""
import time
from datetime import datetime

from django.core.management.base import BaseCommand

from japan_kabu import jquants
from japan_kabu.models import FinancialReport, Stock

PERIOD_TYPES = ('FY', '1Q', '2Q', '3Q')


class Command(BaseCommand):
    help = '日本株の決算行に税引前利益（yfinance）と経常利益（J-Quants）を埋める（カルテ・保有銘柄）'

    def add_arguments(self, parser):
        parser.add_argument('--code', default='', help='この表示コードの銘柄だけ（例: 6758）')

    def handle(self, *args, **opts):
        stocks = self._targets(opts['code'])
        n_pre = n_ord = 0
        for s in stocks:
            a = self._pretax_yf(s)
            b = self._ordinary_jq(s)
            n_pre += a
            n_ord += b
            self.stdout.write(f'  {s.display_code:6} 税引前 {a}行 ／ 経常 {b}行')
        self.stdout.write(self.style.SUCCESS(f'税引前利益 {n_pre}行・経常利益 {n_ord}行を更新（{len(stocks)}銘柄）'))

    def _pretax_yf(self, stock):
        """yfinance の通期 'Pretax Income' を、期末日が近い（±20日）通期行へ入れる"""
        import yfinance as yf
        try:
            df = yf.Ticker(f'{stock.display_code}.T').income_stmt
        except Exception as e:  # noqa: BLE001
            self.stderr.write(f'  {stock.display_code}: yfinance 失敗: {e}')
            return 0
        if df is None or df.empty or 'Pretax Income' not in df.index:
            return 0
        fy = list(FinancialReport.objects.filter(stock=stock, per_type='FY', per_end__isnull=False))
        n = 0
        for col, v in df.loc['Pretax Income'].items():
            if v is None or v != v:
                continue
            end = col.date() if hasattr(col, 'date') else col
            rep = min(fy, key=lambda r: abs((r.per_end - end).days), default=None)
            if rep is None or abs((rep.per_end - end).days) > 20:
                continue
            if rep.pretax != int(v):
                rep.pretax = int(v)
                rep.save(update_fields=['pretax'])
            n += 1
        return n

    def _ordinary_jq(self, stock):
        """J-Quants の銘柄別決算サマリーから経常利益（OdP）を埋める。埋まっていれば API を叩かない"""
        if not FinancialReport.objects.filter(stock=stock, ordinary__isnull=True, per_type__in=PERIOD_TYPES).exists():
            return 0
        try:
            time.sleep(jquants.REQUEST_WAIT)
            rows = jquants._get('/fins/summary', {'code': stock.code}).get('data', [])
        except Exception as e:  # noqa: BLE001
            self.stderr.write(f'  {stock.display_code}: J-Quants 失敗: {e}')
            return 0
        n = 0
        for r in rows:
            if 'FinancialStatements' not in (r.get('DocType') or ''):
                continue
            if r.get('CurPerType') not in PERIOD_TYPES or not r.get('CurPerEn') or r.get('OdP') in (None, ''):
                continue
            try:
                odp = int(float(r['OdP']))
            except (TypeError, ValueError):
                continue
            per_end = datetime.strptime(r['CurPerEn'], '%Y-%m-%d').date()
            n += FinancialReport.objects.filter(stock=stock, per_end=per_end, per_type=r['CurPerType'],
                                                ordinary__isnull=True).update(ordinary=odp)
        return n

    @staticmethod
    def _targets(code):
        if code:
            return list(Stock.objects.filter(country='JP', display_code=code))
        from karte.models import StockKarte
        from portfolio.models import Holding
        codes = set(StockKarte.objects.values_list('stock_id', flat=True))
        codes |= set(Holding.objects.filter(stock__isnull=False).values_list('stock_id', flat=True))
        return list(Stock.objects.filter(country='JP', code__in=codes).order_by('code'))
