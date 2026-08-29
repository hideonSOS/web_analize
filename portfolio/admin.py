from django.contrib import admin

from .models import (
    AssetSnapshot, CashFlow, FxRate, Holding, PortfolioSetting, Product,
    ProductPrice, TargetAllocation,
)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'category', 'isin', 'metal')
    list_filter = ('category',)


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = ('product', 'date', 'price')
    list_filter = ('product',)
    date_hierarchy = 'date'


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ('pair', 'date', 'rate')


@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'quantity', 'avg_cost', 'baseline_date')


@admin.register(CashFlow)
class CashFlowAdmin(admin.ModelAdmin):
    list_display = ('date', 'kind', 'amount', 'memo')
    list_filter = ('kind',)


@admin.register(PortfolioSetting)
class PortfolioSettingAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'baseline_cash', 'link_diary_to_cash')


@admin.register(TargetAllocation)
class TargetAllocationAdmin(admin.ModelAdmin):
    list_display = ('asset_class', 'ratio')


@admin.register(AssetSnapshot)
class AssetSnapshotAdmin(admin.ModelAdmin):
    list_display = ('date', 'total', 'cash', 'principal')
    date_hierarchy = 'date'
