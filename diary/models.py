from django.db import models

from japan_kabu.models import Stock


class DiaryEntry(models.Model):
    """売買日記の1エントリ

    記録時の判断（理由・心理）は後から編集しない方針。
    振り返り（review_*）だけを後日追記して、当時の判断と比較する。
    """
    ACTION_CHOICES = [
        ('buy', '買い'),
        ('sell', '売り'),
        ('pass', '見送り'),
    ]
    RESULT_CHOICES = [
        ('success', '成功'),
        ('failure', '失敗'),
        ('lesson', '学びあり'),
    ]

    stock = models.ForeignKey(Stock, on_delete=models.SET_NULL, null=True, blank=True)
    stock_name = models.CharField(max_length=100)        # 上場廃止後も表示できるよう名前を保存
    stock_code = models.CharField(max_length=5, blank=True)
    recorded_at = models.DateTimeField()                 # 判断した日時
    price = models.FloatField(null=True, blank=True)     # その時点の株価
    shares = models.IntegerField(null=True, blank=True)  # 購入（売却）株数
    target_price = models.FloatField(null=True, blank=True)  # 目標株価（出口計画）
    stop_price = models.FloatField(null=True, blank=True)    # 損切りライン（出口計画）
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    tags = models.CharField(max_length=200, blank=True)  # 判断理由タグ（カンマ区切り）
    mood = models.CharField(max_length=20, blank=True)   # 記録時の心理状態
    reason = models.TextField()                          # 判断理由
    impression = models.TextField(blank=True)            # 感想・メモ

    # 振り返り（後日追記）
    review_note = models.TextField(blank=True)
    review_result = models.CharField(max_length=10, blank=True, choices=RESULT_CHOICES)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.recorded_at:%Y-%m-%d} {self.stock_name} {self.get_action_display()}"
