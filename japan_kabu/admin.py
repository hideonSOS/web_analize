from django.contrib import admin

from .models import Stock


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    """銘柄マスタの参照用。売買日記の価格是正時に現在値(close)・通貨(country)・
    価格日付(price_date)を突き合わせるのに使う。"""
    list_display = ('code', 'display_code', 'country', 'name',
                    'close', 'price_date', 'market_cap', 'volume_z')
    list_filter = ('country',)
    search_fields = ('code', 'display_code', 'name')
    ordering = ('country', 'code')
