"""売買日記に登場した米国株ティッカーだけ、最新株価を取得する

市場全体ではなく「日記で実際に記録したティッカー」に限定するため、
数十件程度のバッチで済む（yfinance を使用）。日次で update_marketcap /
update_volume と同じタイミングに実行する想定。

使い方:
    python manage.py update_us_prices
"""
from datetime import datetime

from django.core.management.base import BaseCommand

from diary.models import DiaryEntry
from japan_kabu.models import Stock


class Command(BaseCommand):
    help = '日記で使われた米国株ティッカーの株価を更新する（yfinance）'

    def handle(self, *args, **options):
        # 日記で参照されている米国株コードを集める
        used_codes = set(
            DiaryEntry.objects.filter(stock__country='US')
            .values_list('stock_id', flat=True)
        )
        stocks = list(Stock.objects.filter(code__in=used_codes))
        if not stocks:
            self.stdout.write('対象の米国株記録がありません')
            return

        tickers = [s.display_code for s in stocks]
        prices, price_date = self._fetch_prices(tickers)
        updated = []
        for s in stocks:
            c = prices.get(s.display_code)
            if c is not None:
                s.close = c
                s.price_date = price_date
                updated.append(s)
        Stock.objects.bulk_update(updated, ['close', 'price_date'], batch_size=500)
        self.stdout.write(self.style.SUCCESS(
            f'米国株株価更新: {len(updated)}/{len(stocks)}銘柄（基準日 {price_date}）'))

    @staticmethod
    def _fetch_prices(tickers):
        import yfinance as yf

        data = yf.download(tickers, period='5d', progress=False, auto_adjust=True)
        closes = data['Close']
        # 最新の有効な終値日
        last_row = closes.ffill().iloc[-1]
        price_date = closes.index[-1]
        if hasattr(price_date, 'date'):
            price_date = price_date.date()
        else:
            price_date = datetime.today().date()

        result = {}
        if len(tickers) == 1:
            # 単一銘柄のときは Series になる
            val = last_row.iloc[0] if hasattr(last_row, 'iloc') else last_row
            result[tickers[0]] = float(val) if val == val else None  # NaN除外
        else:
            for t in tickers:
                if t in last_row.index:
                    v = last_row[t]
                    result[t] = float(v) if v == v else None
        return result, price_date
