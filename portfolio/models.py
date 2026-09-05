from django.db import models

from japan_kabu.models import Stock


class Product(models.Model):
    """Stockマスタに無い資産（投資信託・貴金属）の商品マスタ

    設計方針:
    - 個別株は既存の japan_kabu.Stock を使う。このモデルは「株ではない商品」専用
    - 価格は ProductPrice に日次で蓄積し、画面は常にDBの最新値を見る
      （株価と同じ「バッチが書き、表示時に外部APIを叩かない」方式）
    """
    CATEGORY_CHOICES = [
        ('fund', '投資信託'),
        ('metal', '貴金属'),
        ('crypto', '暗号資産'),
    ]
    METAL_CHOICES = [
        ('gold', '金'),
        ('silver', '銀'),
        ('platinum', 'プラチナ'),
    ]
    # 暗号資産（2026-09-05 追加）。価格は yfinance の「銘柄-USD」× ドル円 → 円/枚。
    # 貴金属と同じ「商品マスタ1件を使い回す」方式。銘柄を足すときはここと
    # update_product_prices の CRYPTO_TICKERS の両方に足すこと
    CRYPTO_CHOICES = [
        ('btc', 'ビットコイン'),
        ('eth', 'イーサリアム'),
        ('xrp', 'XRP'),
        ('sol', 'ソラナ'),
    ]

    category = models.CharField(max_length=10, choices=CATEGORY_CHOICES)
    # 商品名は略称一本（例: オルカン / S&P500）。正式名称は持たない
    # （ISIN・協会コードで商品は特定できるため、表示に使う短い名前だけでよい。ユーザー合意済み）
    name = models.CharField(max_length=100)

    # ── 投資信託のみ: 投信協会の基準価額CSV取得に使う ──
    # 投信総合検索ライブラリーのCSVは isinCd と associFundCd の両方が必要
    isin = models.CharField(max_length=12, blank=True)
    assoc_fund_code = models.CharField(max_length=16, blank=True)

    # ── 貴金属のみ: 金/銀の別（円/gの自動計算は先物×ドル円で行う） ──
    metal = models.CharField(max_length=10, blank=True, choices=METAL_CHOICES)

    # ── 暗号資産のみ: 銘柄（円/枚の自動計算は 銘柄-USD × ドル円 で行う） ──
    crypto = models.CharField(max_length=10, blank=True, choices=CRYPTO_CHOICES)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']

    @property
    def unit_label(self):
        """数量の単位。投信=口 / 貴金属=g / 暗号資産=銘柄記号（BTC 等）"""
        if self.category == 'fund':
            return '口'
        if self.category == 'crypto':
            return self.crypto.upper() or '枚'
        return 'g'

    @property
    def kind_label(self):
        """商品の種別表示（貴金属なら金/銀、暗号資産なら銘柄名、投信なら「投信」）"""
        if self.category == 'metal':
            return self.get_metal_display()
        if self.category == 'crypto':
            return self.get_crypto_display()
        return '投信'

    @property
    def display_name(self):
        return self.name

    def __str__(self):
        return self.display_name


class ProductPrice(models.Model):
    """商品の日次価格履歴

    価格の意味は商品カテゴリで決まる:
    - 投資信託: 基準価額（1万口あたり・円）→ 評価額 = 口数 × price ÷ 10000
    - 貴金属:   円/グラム              → 評価額 = グラム数 × price
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='prices')
    date = models.DateField()
    price = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['product', 'date'], name='uniq_productprice_product_date'),
        ]
        indexes = [models.Index(fields=['product', '-date'])]
        ordering = ['-date']

    def __str__(self):
        return f'{self.product} {self.date} {self.price}'


class FxRate(models.Model):
    """為替レートの日次履歴（現状 USDJPY のみ）

    米国株・貴金属の円換算に使う。夜間バッチが yfinance(USDJPY=X) から取得。
    「毎日同じ基準で比較できること」が目的で、リアルタイム性は追わない。
    """
    pair = models.CharField(max_length=10, default='USDJPY')
    date = models.DateField()
    rate = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['pair', 'date'], name='uniq_fxrate_pair_date'),
        ]
        ordering = ['-date']

    def __str__(self):
        return f'{self.pair} {self.date} {self.rate}'


class Holding(models.Model):
    """期首残高（棚卸しで登録する保有）

    ⚠️ このテーブルは「棚卸し時点の数量」を固定で持つ（quantity を日々更新しない）。
    現在の保有数 = quantity + baseline_date 以降の売買日記(DiaryEntry)の増減、を
    表示時に導出する。日記は編集不可の設計なので導出結果は安定し、
    保有テーブルと日記の二重入力・二重管理が発生しない。

    stock / product はどちらか一方だけを設定する（CheckConstraintで強制）:
    - 個別株（日本株・米国株）→ stock
    - 投資信託・貴金属       → product
    現金はこのテーブルには入れない（PortfolioSetting.baseline_cash + CashFlow で管理）。
    """
    stock = models.ForeignKey(
        Stock, on_delete=models.PROTECT, null=True, blank=True, related_name='holdings')
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, null=True, blank=True, related_name='holdings')

    # 口座区分（プルダウン選択）。NISAの積立枠/成長枠のように同じ商品でも平均取得
    # 単価が異なる場合、区分ごとに別の行として登録する（楽天証券の表示と同じ粒度）。
    # ダッシュボードでは同一商品を合算・加重平均して1行で表示する。
    # 表示文字列をそのまま保存する（キー変換の手間を省く。選択肢の追加はここに1行足すだけ）
    ACCOUNT_CHOICES = [
        ('', '指定なし'),
        ('積立投資枠', '積立投資枠'),
        ('成長投資枠', '成長投資枠'),
    ]
    account = models.CharField(max_length=20, blank=True, default='', choices=ACCOUNT_CHOICES)

    # セクター（個別株のみ・任意）。語彙はセクターインパルスの IMPULSE_SECTORS と
    # 同じ名前を使う（選択肢はフォーム側で国別に出し分ける）。
    # 空欄なら表示時に公式業種(sector17)へフォールバックする
    sector = models.CharField(max_length=30, blank=True, default='')

    # 投資スタイル（個別株のみ・任意）。セクター＝市場の軸に対して、こちらは
    # 「なぜ持つか」という自分の戦略の軸（グロース/大型/配当狙い…）。
    # 銘柄の属性からは導出できないため唯一の手動分類。選択肢の追加はここに1行足すだけ
    STYLE_CHOICES = [
        ('', '指定なし'),
        ('グロース', 'グロース'),
        ('大型', '大型'),
        ('配当狙い', '配当狙い'),
        ('バリュー', 'バリュー'),
    ]
    style = models.CharField(max_length=20, blank=True, default='', choices=STYLE_CHOICES)

    quantity = models.FloatField()   # 株数 / 口数 / グラム
    # 平均取得単価。通貨・単位は資産により異なる:
    #   日本株=円, 米国株=ドル, 投信=1万口あたり円, 貴金属=円/g
    avg_cost = models.FloatField()
    baseline_date = models.DateField()  # この日以降の売買日記を保有数に加算する起点

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # stock か product のどちらか一方だけ
            models.CheckConstraint(
                condition=(
                    models.Q(stock__isnull=False, product__isnull=True)
                    | models.Q(stock__isnull=True, product__isnull=False)
                ),
                name='holding_stock_xor_product',
            ),
            # 同じ銘柄・商品×口座区分の保有行は1つだけ（買い増しは日記で表現する。
            # 同じ組み合わせでの再登録は棚卸しのやり直し=上書きになる）
            models.UniqueConstraint(
                fields=['stock', 'account'], condition=models.Q(stock__isnull=False),
                name='uniq_holding_stock_acct'),
            models.UniqueConstraint(
                fields=['product', 'account'], condition=models.Q(product__isnull=False),
                name='uniq_holding_product_acct'),
        ]
        ordering = ['id']

    @property
    def instrument(self):
        return self.stock or self.product

    def __str__(self):
        return f'{self.instrument} × {self.quantity}'


class CashFlow(models.Model):
    """証券口座への入出金の記録

    資産推移グラフで「入金による増加」と「運用益」を分離する唯一の材料。
    株の売買に伴う現金の増減はここには記録しない（日記から導出できるため。
    導出をON/OFFする設定は PortfolioSetting.link_diary_to_cash）。
    """
    KIND_CHOICES = [
        ('deposit', '入金'),
        ('withdraw', '出金'),
    ]
    date = models.DateField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    amount = models.FloatField()                 # 円・正の値で入れる
    memo = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-id']

    def __str__(self):
        return f'{self.date} {self.get_kind_display()} ¥{self.amount:,.0f}'

    @property
    def signed_amount(self):
        return self.amount if self.kind == 'deposit' else -self.amount


class PortfolioSetting(models.Model):
    """資産管理の全体設定（1行だけ使うシングルトン）

    - baseline_cash: 棚卸し時点の現金残高（円）。
      現在の現金 = baseline_cash + CashFlow の入出金合計
                  (+ link_diary_to_cash が True なら日記の売買代金の増減)
    - アラートのしきい値もここに置く（コードに埋めると調整のたびに再デプロイになるため）
    """
    baseline_cash = models.FloatField(default=0)
    baseline_cash_date = models.DateField(null=True, blank=True)

    # ── 売買日記との連動（ユーザー要望により既定OFF: 便利だが競合が起きやすいため）──
    # ON: 棚卸し日より後の日記の買い/売りを保有数・平均取得単価へ自動反映する
    link_diary_to_holdings = models.BooleanField(default=False)
    # ON: 日記の約定代金を現金残高へ自動反映する
    link_diary_to_cash = models.BooleanField(default=False)

    # 偏りアラートのしきい値（%）
    alert_sector_pct = models.FloatField(default=30)    # 1セクターへの集中
    alert_top3_pct = models.FloatField(default=40)      # 上位3銘柄への集中
    alert_single_pct = models.FloatField(default=20)    # 1銘柄への集中

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '資産管理設定'

    @classmethod
    def get(cls):
        """常に1行目を返す（無ければ作る）"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class TargetAllocation(models.Model):
    """目標ポートフォリオ（大分類ごとの目標比率%）

    大分類はダッシュボードの内側リング（個別株・投資信託・金銀・現金）と一致させる。
    合計が100%でなくても保存は許す（画面側で警告表示するだけにする）。
    """
    ASSET_CLASS_CHOICES = [
        ('stock', '個別株'),
        ('fund', '投資信託'),
        ('metal', '貴金属'),
        ('crypto', '暗号資産'),
        ('cash', '現金'),
    ]
    asset_class = models.CharField(max_length=10, choices=ASSET_CLASS_CHOICES, unique=True)
    ratio = models.FloatField()  # %

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.get_asset_class_display()} {self.ratio}%'


class DrillNote(models.Model):
    """下落上等ページ（/portfolio/drill/）の手入力データ（1行だけ使うシングルトン）

    暴落時にパニックにならないための「毎日読む合言葉」のページ。
    - slogan: 毎日読むスローガン（1行=1項目で箇条書き表示する）
    - lessons: 教訓の自由記述（相場を見て感じたこと・過去の失敗を書き足していく）
    - cash_target: 確保したい現金の目標額（円）。弾薬ゲージのモチベーション用
    ⚠️ カルテの経緯と同じく項目は細分化しない（細分化すると書く気が失せる）
    """
    slogan = models.TextField(blank=True, default='')
    lessons = models.TextField(blank=True, default='')
    cash_target = models.FloatField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '下落上等ノート'

    @classmethod
    def get(cls):
        """常に1行目を返す（無ければ作る）"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AssetSnapshot(models.Model):
    """日次の資産スナップショット（夜間バッチ snapshot_assets が記録する）

    資産推移グラフの材料。表示時に毎回全履歴を再計算すると価格履歴の欠損で
    過去の数字が変わってしまうため、「その日に計算した結果」を確定値として残す。
    同日に再実行された場合は上書き（date が unique）。
    """
    date = models.DateField(unique=True)

    stock_jp = models.FloatField(default=0)   # 日本株 評価額（円）
    stock_us = models.FloatField(default=0)   # 米国株 評価額（円換算）
    fund = models.FloatField(default=0)       # 投資信託 評価額（円）
    metal = models.FloatField(default=0)      # 貴金属 評価額（円）
    crypto = models.FloatField(default=0)     # 暗号資産 評価額（円）2026-09-05 追加
    cash = models.FloatField(default=0)       # 現金残高（円）
    total = models.FloatField(default=0)      # 総資産（円）

    # 投資元本 = 期首総資産 + 入金累計 - 出金累計。
    # total との差が「運用でつくった利益」になる（資産推移グラフの2本目の線）
    principal = models.FloatField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f'{self.date} 総資産 ¥{self.total:,.0f}'
