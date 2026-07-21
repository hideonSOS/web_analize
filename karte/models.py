from django.db import models

from japan_kabu.models import Stock


class StockKarte(models.Model):
    """銘柄カルテ: IR資料を読みながら手入力する詳細分析

    設計方針:
    - 全銘柄で **共通の雛形**（銘柄ごとに項目を変えない）
      → 銘柄間の横断比較ができ、雛形自体が「IR資料で何を見るべきか」の
        チェックリストとして機能する（読みながら入力して頭に入れるのが目的）
    - 各項目は **すべて空欄可**。埋まっていない項目は「まだ調べていない箇所」として
      画面上で分かるようにする
    """
    stock = models.OneToOneField(Stock, on_delete=models.CASCADE, related_name='karte')

    # ── 経営陣の考課 ──
    mgmt_track_record = models.TextField(blank=True)    # 過去の公約・中計の達成度
    mgmt_capital = models.TextField(blank=True)         # 資本配分の巧拙
    mgmt_stance = models.TextField(blank=True)          # 姿勢・開示の誠実さ
    # ── 事業理解 ──
    business_model = models.TextField(blank=True)       # 何を誰に売って稼ぐか
    revenue_structure = models.TextField(blank=True)    # 収益構造・課金モデル
    # ── 競争環境 ──
    strengths = models.TextField(blank=True)            # 強み・参入障壁
    risks = models.TextField(blank=True)                # リスク・弱み
    competition = models.TextField(blank=True)          # 競合・シェア
    # ── 財務・還元 ──
    financial_policy = models.TextField(blank=True)     # 財務方針・株主還元・投資計画
    # ── 投資判断 ──
    hypothesis = models.TextField(blank=True)           # 投資仮説（なぜ買うか）
    disconfirm = models.TextField(blank=True)           # 仮説が崩れる条件
    next_check = models.TextField(blank=True)           # 次回決算で確認すること
    # ── 参照 ──
    ir_url = models.URLField(blank=True, max_length=500)
    memo = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"カルテ {self.stock_id}"


def executive_photo_path(instance, filename):
    """銘柄ごとのフォルダに保存する: media/executives/<銘柄コード>/<ファイル名>"""
    code = instance.karte.stock.display_code
    return f'executives/{code}/{filename}'


class Executive(models.Model):
    """経営陣の顔写真とコメント

    入力の手間を最小にするため、項目は「写真」と「コメント」だけ。
    氏名・役職を書きたい場合もコメント欄に自由に書く。
    """
    karte = models.ForeignKey(StockKarte, on_delete=models.CASCADE, related_name='executives')
    photo = models.ImageField(upload_to=executive_photo_path, blank=True)
    note = models.TextField(blank=True)                         # コメント（自由記述）
    order = models.IntegerField(default=0)                      # 表示順（小さいほど先頭）
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def delete(self, *args, **kwargs):
        # レコード削除時に実ファイルも消す（媒体ファイルの残骸を防ぐ）
        if self.photo:
            self.photo.delete(save=False)
        super().delete(*args, **kwargs)

    def __str__(self):
        return self.note[:20] or f'経営陣 #{self.pk}'


class MidTermTarget(models.Model):
    """中期経営計画の目標 vs 現在値（進捗バーで表示）

    IR資料の目玉だが自動取得できない情報。全銘柄共通の形で持てる。
    """
    karte = models.ForeignKey(StockKarte, on_delete=models.CASCADE, related_name='targets')
    label = models.CharField(max_length=50)                        # 例: 営業利益
    target_value = models.FloatField()                             # 目標値
    current_value = models.FloatField(null=True, blank=True)       # 現在値（任意）
    unit = models.CharField(max_length=10, blank=True)             # 億円 / % / 倍
    target_fy = models.CharField(max_length=20, blank=True)        # 例: 2027年度
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']

    @property
    def progress(self):
        """達成率(%)。目標が0以下、現在値未入力の場合はNone"""
        if self.current_value is None or not self.target_value:
            return None
        return self.current_value / self.target_value * 100

    def __str__(self):
        return f"{self.label} {self.target_value}{self.unit}"


class KpiEntry(models.Model):
    """独自KPI・セグメント別業績の時系列（自動でグラフ化する）

    KPI名は銘柄ごとに異なる（ゲーム部門売上 / ARR / 既存店売上 など）が、
    「KPI名 × 期 × 数値」という入れ物は全銘柄共通。
    """
    karte = models.ForeignKey(StockKarte, on_delete=models.CASCADE, related_name='kpis')
    name = models.CharField(max_length=50)             # 例: ゲーム部門売上
    period = models.CharField(max_length=20)           # 例: 2025Q1 / 2025年度
    value = models.FloatField()
    unit = models.CharField(max_length=10, blank=True)  # 億円 / 万人 / %
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name', 'period']

    def __str__(self):
        return f"{self.name} {self.period}={self.value}"
