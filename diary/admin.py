from django.contrib import admin

from .models import DiaryEntry


@admin.register(DiaryEntry)
class DiaryEntryAdmin(admin.ModelAdmin):
    """売買日記の直接編集用。米国株の価格を円→ドルに是正する等に使う。

    価格の通貨は銘柄の国で決まる（US=$ / JP=円）。一覧に「通貨」列を出し、
    price/target/stop は一覧上で直接編集できるようにしている。
    """
    list_display = ('pk', 'stock_code', 'stock_name', 'currency', 'action',
                    'price', 'target_price', 'stop_price', 'shares', 'recorded_at')
    list_display_links = ('pk', 'stock_code')
    list_editable = ('price', 'target_price', 'stop_price')
    list_filter = ('action', 'stock__country')
    search_fields = ('stock_code', 'stock_name')
    # 銘柄マスタは1.6万件あるためプルダウンにせずID入力にする（重さ回避）
    raw_id_fields = ('stock',)
    ordering = ('-recorded_at',)

    @admin.display(description='通貨')
    def currency(self, obj):
        return '$ (米ドル)' if (obj.stock and obj.stock.country == 'US') else '円'
