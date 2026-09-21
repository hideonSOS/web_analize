"""監視（2026-09-21）: 自分で選んだ銘柄の「1年高値からの下落率」を横棒で並べ、指定した買値まで
下がるのを待つ。ユーザーの使い方: 同じ銘柄で買いと売りを繰り返すので、保有の追跡ではなく
**下がりきった銘柄を探す**ための一覧。追加した順に上から並ぶ（並び替え可）。
株価はカルテと同じ日次終値（japan_kabu.DailyPrice）を使う。監視銘柄も夜バッチの取得対象に入れてある
"""
from django.db import models

from japan_kabu.models import Stock


class WatchItem(models.Model):
    stock = models.OneToOneField(Stock, on_delete=models.CASCADE, related_name='watch_item')
    target_price = models.FloatField(null=True, blank=True)   # この価格まで下がったら買う（取引通貨: 円 / ドル）
    note = models.CharField(max_length=200, blank=True)        # 一言。2026-09-22 に廃止（フォーム・表示から削除。列は残置）
    sort_order = models.IntegerField(default=0)                # 小さいほど上。0 は追加順
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['sort_order', 'created_at']

    def __str__(self):
        return f'{self.stock.display_code} → {self.target_price}'
