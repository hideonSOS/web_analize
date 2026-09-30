"""スイングトレード用のカレンダー（2026-09-30 ユーザー要望）

決算・FOMC・経済指標・政治イベントなどを手で書き込み、1か月の予定を一目で見る。
自動取得はしない（ユーザー方針: 自動のしくみは極力控える）。1件＝1日・1行。
"""
from django.db import models


class CalendarEvent(models.Model):
    # 種類ごとに色を分ける（テンプレートの class と CSS の色がこのキーに対応）
    CATEGORIES = [
        ('earnings', '決算'),
        ('fomc', 'FOMC・金融政策'),
        ('macro', '経済指標'),
        ('politics', '政治・選挙'),
        ('dividend', '配当・権利'),
        ('other', 'その他'),
    ]
    date = models.DateField(db_index=True)
    category = models.CharField(max_length=12, choices=CATEGORIES, default='earnings')
    title = models.CharField(max_length=100)            # 例: NVDA 決算、FOMC 結果発表、米CPI
    ticker = models.CharField(max_length=20, blank=True)  # 任意（決算の銘柄など）
    time_note = models.CharField(max_length=20, blank=True)  # 任意（「引け後」「22:30」など）
    memo = models.TextField(blank=True)
    important = models.BooleanField(default=False)      # 重要（相場が大きく動きうる）
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', '-important', 'category', 'id']

    def __str__(self):
        return f'{self.date} {self.title}'
