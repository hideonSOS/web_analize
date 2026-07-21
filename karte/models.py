import re

from django.db import models

from japan_kabu.models import Stock

# YouTubeのURLから動画IDを取り出すパターン（watch / youtu.be / embed / shorts）
_YT_PATTERNS = [
    re.compile(r'(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([\w-]{11})'),
    re.compile(r'youtu\.be/([\w-]{11})'),
    re.compile(r'(?:youtube\.com|youtube-nocookie\.com)/embed/([\w-]{11})'),
    re.compile(r'youtube\.com/shorts/([\w-]{11})'),
    re.compile(r'youtube\.com/live/([\w-]{11})'),
]


def extract_youtube_id(url):
    """YouTubeのURLから動画IDを取り出す。取れなければ None。

    URLをそのまま iframe に渡さず、ID だけを取り出して埋め込みURLを
    自前で組み立てるために使う（任意のURLが埋め込まれるのを防ぐ）。
    """
    if not url:
        return None
    for pattern in _YT_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


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

    # ── 経営陣の考課（写真中心。テキストは自由記述1つに集約）──
    mgmt_note = models.TextField(blank=True)
    # ── 事業理解（自由記述1つ）──
    business_note = models.TextField(blank=True)
    # ── 競争環境（自由記述1つ）──
    competitive_note = models.TextField(blank=True)
    # ── 投資判断（自由記述1つ）──
    invest_note = models.TextField(blank=True)
    # IR資料のURL（上部の「IR資料を開く」リンクに使う。入力欄は参照動画セクション内）
    ir_url = models.URLField(blank=True, max_length=500)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        return f"カルテ {self.stock_id}"


class ReferenceVideo(models.Model):
    """参照動画（決算説明会・IR動画など）と、その要約

    URLはそのまま保持するが、埋め込みには video_id から組み立てたURLだけを使う。
    """
    karte = models.ForeignKey(StockKarte, on_delete=models.CASCADE, related_name='videos')
    url = models.URLField(max_length=500)
    title = models.CharField(max_length=100, blank=True)   # 任意のラベル
    note = models.TextField(blank=True)                    # 動画の要約・感想
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    @property
    def youtube_id(self):
        return extract_youtube_id(self.url)

    @property
    def embed_url(self):
        """iframe用のURL。YouTube以外・解析不能なURLは埋め込まない"""
        vid = self.youtube_id
        # youtube-nocookie は再生するまでCookieを置かない配信元
        return f'https://www.youtube-nocookie.com/embed/{vid}' if vid else None

    def __str__(self):
        return self.title or self.url


def executive_photo_path(instance, filename):
    """銘柄ごとのフォルダに保存する: media/executives/<銘柄コード>/<ファイル名>"""
    code = instance.karte.stock.display_code
    return f'executives/{code}/{filename}'


def screenshot_path(instance, filename):
    """銘柄ごとのフォルダに保存する: media/screenshots/<銘柄コード>/<ファイル名>"""
    code = instance.karte.stock.display_code
    return f'screenshots/{code}/{filename}'


class Screenshot(models.Model):
    """IR資料などの画面キャプチャと説明

    経営陣の写真と同じく「画像 + コメント」だけの構成。
    グラフや表など横長の画像を想定し、縦横比を保って表示する。
    """
    karte = models.ForeignKey(StockKarte, on_delete=models.CASCADE, related_name='screenshots')
    image = models.ImageField(upload_to=screenshot_path, blank=True)
    note = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def delete(self, *args, **kwargs):
        # レコード削除時に実ファイルも消す
        if self.image:
            self.image.delete(save=False)
        super().delete(*args, **kwargs)

    def __str__(self):
        return self.note[:20] or f'スクリーンショット #{self.pk}'


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
