"""配当の履歴と次回予定を yfinance から取る（2026-09-27・配当金ページ用）

対象: 投資スタイルが「配当狙い」の保有株（--all で保有株すべて、--ticker で1銘柄）。
- Ticker.dividends … 権利落ち日と1株配当（分割調整済み）。直近6年ぶんを upsert
- Ticker.calendar  … 次回（または直近）の権利落ち日と支払日。支払日は権利落ち日の 0〜120 日後のときだけ採用
  （yfinance の calendar は稀に食い違う。OWL で支払日が権利落ち日より前になっていた）
使い方:
    python manage.py update_dividends
    python manage.py update_dividends --all
    python manage.py update_dividends --ticker PFE
"""
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from japan_kabu.models import Stock
from portfolio.dividends import DIV_STYLE, plausible_next
from portfolio.models import DividendRecord


def _symbol(stock):
    return f'{stock.display_code}.T' if stock.country == 'JP' else stock.display_code.replace('.', '-')


def _d(v):
    """pandas.Timestamp / datetime / date → date。⚠️ Timestamp も datetime も date のサブクラスなので、
    isinstance(v, date) で先に判定すると Timestamp のまま返って date との比較で落ちる（実際に起きた）"""
    if v is None:
        return None
    if isinstance(v, datetime) or hasattr(v, 'to_pydatetime'):
        return v.date()
    return v if isinstance(v, date) else None


class Command(BaseCommand):
    help = '配当狙いの保有株の配当履歴と次回予定を取得する（yfinance）'

    def add_arguments(self, parser):
        parser.add_argument('--all', action='store_true', help='保有株すべて')
        parser.add_argument('--ticker', help='この銘柄だけ（表示コード）')

    def handle(self, *args, **opt):
        from portfolio.services import current_stock_holdings
        if opt.get('ticker'):
            stocks = list(Stock.objects.filter(display_code=opt['ticker'].upper()))
        else:
            rows, _ = current_stock_holdings()
            stocks = [r['stock'] for r in rows if r['stock'] and (opt['all'] or r.get('style') == DIV_STYLE)]
        if not stocks:
            self.stdout.write('対象の銘柄がありません（投資スタイル「配当狙い」の保有株）')
            return
        ok = 0
        for s in stocks:
            try:
                n = self._one(s)
                self.stdout.write(f'  {s.display_code:<6} 配当 {n} 件')
                ok += 1
            except Exception as e:   # noqa: BLE001  1銘柄の失敗で止めない
                self.stderr.write(f'  {s.display_code:<6} 失敗: {type(e).__name__}: {e}')
        self.stdout.write(self.style.SUCCESS(f'完了: {ok}/{len(stocks)} 銘柄'))

    def _one(self, stock):
        import yfinance as yf
        t = yf.Ticker(_symbol(stock))
        cur = 'JPY' if stock.country == 'JP' else 'USD'
        since = date.today() - timedelta(days=365 * 6)
        n = 0
        divs = t.dividends
        for ts, amt in (divs.items() if divs is not None else []):
            ex = _d(ts)
            if ex is None or ex < since or not amt or amt != amt:
                continue
            DividendRecord.objects.update_or_create(stock=stock, ex_date=ex,
                                                    defaults={'amount': float(amt), 'currency': cur})
            n += 1
        try:
            cal = t.calendar or {}
        except Exception:   # noqa: BLE001
            cal = {}
        if isinstance(cal, dict) and cal:     # 取得失敗（空）のときは既存の予定を消さない
            ex, pay = _d(cal.get('Ex-Dividend Date')), _d(cal.get('Dividend Date'))
            past = list(DividendRecord.objects.filter(stock=stock, amount__isnull=False)
                        .values_list('ex_date', flat=True))
            known = ex and DividendRecord.objects.filter(stock=stock, ex_date=ex, amount__isnull=False).exists()
            if ex and not known and not plausible_next(ex, past):
                self.stdout.write(f'  {stock.display_code}: calendar の権利落ち {ex} は直前({max(past)})から近すぎるので採らない')
                ex = None
            # 採らなかった・取り消された予定（金額未定の未来行）を掃除する
            stale = DividendRecord.objects.filter(stock=stock, amount__isnull=True, ex_date__gt=date.today())
            (stale.exclude(ex_date=ex) if ex else stale).delete()
            if ex:
                rec, _ = DividendRecord.objects.get_or_create(stock=stock, ex_date=ex, defaults={'currency': cur})
                if pay and 0 <= (pay - ex).days <= 120:
                    rec.pay_date = pay
                    rec.save(update_fields=['pay_date', 'updated_at'])
        return n
