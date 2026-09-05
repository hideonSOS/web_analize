"""支出分析（spending）のモデル

目的は家計簿ではなく **投資への入金力を増やすこと**。支出の可視化はその手段で、
出口は SavingsPlan（節約の決定）→ 月あたり追加入金力 → portfolio の入金計画。

設計の要点（収支/申し送り.md に対応）:
- 元データ（Zaim / e-navi の CSV）は追記で蓄積し、**台帳は毎回ゼロから作り直す**。
  そのため Transaction は「取り込みのたびに ledger_id で upsert される派生データ」で、
  ここを手で直すのではなく MerchantRule を育てるのが運用の中心。
- ただし画面で直した分類（category_source='manual'）だけは再取込で上書きしない。
- 認証情報は一切持たない。CSV は人が手でダウンロードして画面からアップロードする。
"""
from django.db import models


class MerchantRule(models.Model):
    """加盟店名 → 統一名・分類・種別・必要度 のルール。

    merchant_rules.csv を初回シードし、以後は DB を正にする。
    節約候補の抽出は kind（サブスク/年会費/変動）と necessity（必要度）で決まるので、
    ここを育てることが分析精度に直結する。
    """
    KIND = [('サブスク', 'サブスク'), ('年会費', '年会費'), ('変動', '変動')]
    NECESSITY = [('必須', '必須'), ('準必須', '準必須'), ('裁量', '裁量'), ('要確認', '要確認')]

    priority = models.IntegerField(default=100, help_text='小さいほど先に評価する')
    pattern = models.CharField(max_length=200, help_text='正規化後の店名に対する正規表現')
    merchant = models.CharField(max_length=100, help_text='表示に使う統一名')
    category = models.CharField(max_length=50, blank=True)
    subcategory = models.CharField(max_length=50, blank=True)
    kind = models.CharField(max_length=10, choices=KIND, default='変動')
    necessity = models.CharField(max_length=10, choices=NECESSITY, default='要確認')
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'id']

    def __str__(self):
        return f'{self.pattern} → {self.merchant}'


class Transaction(models.Model):
    """統合台帳の1行（1決済＝1行・重複なし）。

    ⚠️ 下書きの CardTransaction から一般化した（申し送り 10.4）。カードだけでなく
    現金・銀行・支払元未設定も同じテーブルに載せて「全容」にする。

    ledger_id は内容（日付|支払元|金額|店|品目|支払方法）のハッシュで、
    同じ入力なら同じ ID になる決定的な値。再取込はこれを主キー代わりに upsert する。
    """
    SOURCE_KIND = [
        ('card', '楽天カード'), ('cash', '現金'), ('bank', '銀行'), ('unset', '支払元未設定'),
    ]
    CATEGORY_SOURCE = [
        ('manual', '手動'), ('zaim', 'Zaim'), ('rule', 'ルール'), ('fix', '品目ルール'),
        ('bank', '銀行明細'), ('none', '未分類'),
    ]
    MATCH = [('matched', '両方'), ('zaim_only', 'Zaimのみ'), ('enavi_only', 'e-naviのみ'), ('', '—')]

    ledger_id = models.CharField(max_length=16, unique=True, db_index=True)
    date = models.DateField(db_index=True)
    ym = models.CharField(max_length=7, db_index=True)
    amount = models.IntegerField(help_text='円。負は値引き・ポイント利用・返金')

    source_kind = models.CharField(max_length=8, choices=SOURCE_KIND, db_index=True)
    source_name = models.CharField(max_length=50, blank=True)

    shop = models.CharField(max_length=200, blank=True)
    shop_norm = models.CharField(max_length=200, blank=True, db_index=True)
    merchant = models.CharField(max_length=100, blank=True, db_index=True)
    label = models.CharField(max_length=200, blank=True, help_text='表示名（メモ→品目→店名の優先）')
    item = models.CharField(max_length=200, blank=True)
    memo = models.TextField(blank=True)

    category = models.CharField(max_length=50, blank=True)
    subcategory = models.CharField(max_length=50, blank=True)
    # 'bank' は銀行明細の摘要辞書で確定した分類（2026-09-06）。Zaim 由来でも推定でもない
    category_source = models.CharField(max_length=8, choices=CATEGORY_SOURCE, default='none')
    kind = models.CharField(max_length=10, blank=True)
    necessity = models.CharField(max_length=10, blank=True)

    match_status = models.CharField(max_length=12, choices=MATCH, blank=True)
    enavi_pay_method = models.CharField(max_length=20, blank=True)
    enavi_is_installment = models.BooleanField(default=False)

    # 除外は「消さずに理由を残す」（監査できるように）。in_total=True の行だけ集計に入る
    row_type = models.CharField(max_length=10, default='normal')
    exclude_reason = models.CharField(max_length=24, blank=True, db_index=True)
    in_total = models.BooleanField(default=True, db_index=True)
    dup_flag = models.CharField(max_length=16, blank=True)

    # 画面で分類を直した場合の保持用。再取込時に上書きしない
    # レシート由来の行の種類（item/tax/discount/bag/unknown）と表示用に掃除した品目名。
    # ⚠️ 商品でない行も集計には必ず含めること。外税55,913円は実際に払った消費税で、
    # 除外すると支出の総額が実態より小さくなる。畳むのは画面側の仕事
    label_kind = models.CharField(max_length=10, blank=True, db_index=True)
    label_clean = models.CharField(max_length=200, blank=True)

    manual_category = models.CharField(max_length=50, blank=True)
    manual_subcategory = models.CharField(max_length=50, blank=True)
    manual_necessity = models.CharField(max_length=10, blank=True)
    exclude_override = models.BooleanField(
        null=True, blank=True, help_text='True=強制的に集計に含める / False=強制除外 / 未設定=自動判定')

    imported_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-id']
        indexes = [
            models.Index(fields=['ym', 'source_kind']),
            models.Index(fields=['-date', 'merchant']),
        ]

    def __str__(self):
        return f'{self.date} {self.merchant} {self.amount:,}円'

    @property
    def has_manual_edit(self):
        return bool(self.manual_category or self.manual_necessity or self.exclude_override is not None)


class SavingsPlan(models.Model):
    """節約候補に対する本人の決定。**この合計が入金力になる**。

    静的なダッシュボードとの決定的な違いがこのテーブル。見て終わりにせず、
    「やる/やらない」を記録し、その年間効果額を portfolio の入金計画へ渡す。
    """
    STATUS = [
        ('todo', '検討中'), ('doing', '実行中'), ('done', '完了'), ('skip', 'やらない'),
    ]

    merchant = models.CharField(max_length=100)
    action = models.CharField(max_length=50, default='解約', help_text='解約 / 半減 / プラン変更 など')
    annual_effect = models.IntegerField(help_text='年間効果額（円）')
    status = models.CharField(max_length=8, choices=STATUS, default='todo', db_index=True)
    decided_at = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-annual_effect', 'id']

    def __str__(self):
        return f'{self.merchant} {self.action} 年{self.annual_effect:,}円 [{self.get_status_display()}]'

    @classmethod
    def monthly_capacity(cls):
        """実行中＋完了の年間効果額 ÷ 12。portfolio の入金計画へ渡す確定値。

        「全部やったら」の機械試算は candidates 側の参考値で、こちらは本人の決定分のみ。
        """
        total = sum(p.annual_effect for p in cls.objects.filter(status__in=['doing', 'done']))
        return total // 12


class Budget(models.Model):
    """カテゴリ別の月次予算（目標値）。

    支出は「使わないほど良い」ので、ポートフォリオの目標配分（近づけたい値）とは
    意味が逆になる。**予算は上限**で、超過が問題・未達は歓迎という向きで表示する。

    金額で持つ（比率ではなく）理由: 支出総額そのものを減らしたいのに比率で目標を
    置くと、総額が増えても比率が保たれてしまい目標にならないため。
    """
    category = models.CharField(max_length=50, unique=True)
    monthly_limit = models.IntegerField(help_text='月あたりの上限（円）')
    note = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-monthly_limit', 'category']

    def __str__(self):
        return f'{self.category} 月{self.monthly_limit:,}円まで'

    @classmethod
    def total_limit(cls):
        return sum(b.monthly_limit for b in cls.objects.all())


class ImportLog(models.Model):
    """取り込みの履歴。前回との差分が説明できるかを毎回確認するために残す（申し送り 11.3）。"""
    created_at = models.DateTimeField(auto_now_add=True)
    zaim_file = models.CharField(max_length=200, blank=True)
    enavi_files = models.IntegerField(default=0)
    rows_total = models.IntegerField(default=0)
    rows_added = models.IntegerField(default=0)
    rows_removed = models.IntegerField(default=0)
    ok = models.BooleanField(default=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} {self.rows_total}行 (+{self.rows_added}/-{self.rows_removed})'


class AmazonOrderItem(models.Model):
    """Amazon 注文履歴の 1 商品 1 行。カード明細の「Amazon.co.jp」を品目に分解する。

    カード明細も Zaim も、Amazon の支出は加盟店名 `Amazon.co.jp` としか記録しない。
    実測で 82 件・約 20 万円が使途不明のまま残っており、カード支出で最大の塊だった。

    ⚠️ カードの請求は注文単位ではなく**出荷単位**で立つため、1 注文が複数の請求に
    割れる。だから商品 → 請求は多対一で持つ（transaction が同じ商品が複数ある）。
    金額が合わなかった商品は transaction=None のまま残す。埋めないのが正しい
    （間違った品目を出すくらいなら不明のままの方がよい）。

    台帳と同じく**毎回 CSV から作り直す**。手で直す項目は持たない。
    """
    MATCH_HOW = [
        ('charge', 'クレカ請求額で一致'),
        ('order', '注文合計で一致'),
        ('item', '商品小計で一致'),
    ]

    order_id = models.CharField(max_length=40, db_index=True)
    order_date = models.DateField(null=True, blank=True)
    product_name = models.CharField(max_length=300)
    quantity = models.IntegerField(default=1)
    item_total = models.IntegerField(default=0, help_text='この商品の小計（円）')
    order_total = models.IntegerField(default=0, help_text='注文全体の合計（円）')
    status = models.CharField(max_length=40, blank=True)
    source_file = models.CharField(max_length=200, blank=True)

    transaction = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='amazon_items', help_text='対応するカード請求。突合できなければ空')
    match_how = models.CharField(max_length=10, blank=True, choices=MATCH_HOW)

    class Meta:
        ordering = ['-order_date', 'order_id']
        indexes = [models.Index(fields=['order_date'])]

    def __str__(self):
        return f'{self.order_date} {self.product_name[:30]} {self.item_total:,}円'


class MonthlyIncome(models.Model):
    """月ごとの収入（手取り）。支出と突き合わせて貯蓄率＝入金力を出すための唯一の材料。

    なぜ要るか: 支出だけ見ても「月いくら投資に回せるはずか」は出ない。目的は入金力の
    向上なので、収入 − 支出 = 余力 まで出して初めて話が閉じる。

    2系統を持つ:
    - source='zaim'   … Zaim CSV の収入行から自動取込。**取り込みのたびに作り直す**
    - source='manual' … 画面からの手入力。Zaim に記録が無い月を埋める

    ⚠️ 同じ月に両方あるときは**手入力を優先**する（手で入れたのは自動が間違って
    いるか記録が無いときなので、自動で上書きしてはいけない）。
    """
    SOURCE = [('zaim', 'Zaimから取込'), ('bank', '銀行明細から取込'), ('manual', '手入力')]

    ym = models.CharField(max_length=7, db_index=True, help_text='YYYY-MM')
    amount = models.IntegerField(help_text='その月の手取り合計（円）')
    source = models.CharField(max_length=8, choices=SOURCE, default='manual')
    note = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ym']
        constraints = [
            models.UniqueConstraint(fields=['ym', 'source'], name='uniq_income_ym_source'),
        ]

    def __str__(self):
        return f'{self.ym} {self.amount:,}円（{self.get_source_display()}）'

    @classmethod
    def effective(cls) -> dict:
        """月 → 採用する収入額。優先順位: 手入力 > 銀行明細 > Zaim

        銀行明細は実際に振り込まれた額なので Zaim の手記録より確か。
        手入力は「自動が間違っているか無いとき」に人が入れたものなので常に最上位。
        """
        out = {i.ym: i.amount for i in cls.objects.filter(source='zaim')}
        out.update({i.ym: i.amount for i in cls.objects.filter(source='bank')})
        out.update({i.ym: i.amount for i in cls.objects.filter(source='manual')})
        return out


class LabelRule(models.Model):
    """品目名 → 分類の上書きルール。**Zaim の分類より優先**する唯一の仕組み。

    なぜ要るか: Zaim のレシート撮影は品目名から分類を学習し、一度誤ると同じ品目が
    毎回同じ誤った分類で入ってくる（実測: ごぼう→通信 が18回連続、豆腐→遊び/風俗）。
    明細の手動修正は**その行**にしか効かないので、翌月に同じ品目を買えば再発する。
    取り込み時に品目名で当てて分類を差し替えれば、今後のアップロードにも効く。

    優先順位: **品目ルール > Zaim の分類 > 加盟店ルール**（category_source='fix'）。
    表示時の manual_* はさらにその上（人が直したものは常に勝つ）。

    ⚠️ 外税・割引・レジ袋のような「レシート付随行」にはルールを作らないこと。
    正しい分類はレシート次第（食料品のレシートなら食費、通信のレシートなら通信）で、
    品目名だけでは決められない。
    """
    priority = models.IntegerField(default=100, help_text='小さいほど先に評価する')
    pattern = models.CharField(max_length=200, help_text='品目名に対する正規表現（部分一致）')
    category = models.CharField(max_length=50)
    subcategory = models.CharField(max_length=50, blank=True)
    note = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'id']

    def __str__(self):
        return f'/{self.pattern}/ → {self.category}/{self.subcategory}'


class FixedCostEntry(models.Model):
    """口座振替など CSV に出てこない基礎支出の、月ごとの実績（手入力）。

    なぜ要るか: 家賃・電気・ガス・水道は銀行の口座振替で払っていて、Zaim の記録経路
    （レシート撮影・楽天カード連携・お財布）のどれにも乗らない。実測で10年分の Zaim に
    電気代が1件しか無く、台帳には0件だった。金額の大きい基礎支出が丸ごと抜けると
    貯蓄率が過大に出るので、月ごとに手で入れる受け皿を持つ。

    項目名（item）は Budget.category と同じ名前で持ち、月次ページの「理想テンプレートと
    実際」で台帳の同名カテゴリ実績に足し合わせる（家賃は台帳に無いので実質これだけ）。
    """
    ym = models.CharField(max_length=7, db_index=True, help_text='YYYY-MM')
    item = models.CharField(max_length=50, help_text='Budget.category と同じ名前（家賃・電気 など）')
    amount = models.IntegerField(help_text='その月に払った額（円）')
    note = models.CharField(max_length=100, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-ym', 'item']
        constraints = [
            models.UniqueConstraint(fields=['ym', 'item'], name='uniq_fixedcost_ym_item'),
        ]

    def __str__(self):
        return f'{self.ym} {self.item} {self.amount:,}円'


class TemplateItem(models.Model):
    """理想の支出テンプレートの1項目（家賃・電気・ガス・水道 … と、その理想の月額）。

    ⚠️ 台帳（Transaction）とも予算（Budget）とも**つながない**。ユーザー方針
    （2026-09-06）: 理想も実際もすべて手入力で、月次ページの先頭に積み上げ横棒で
    「理想」と「実際」を並べて比べる。実際の月ごとの額は FixedCostEntry（ym, item）。
    台帳の自動集計を混ぜると「口座振替で CSV に無い項目」と「レシートで拾える項目」で
    精度が揃わず、比較の意味が壊れるため、この機能は台帳から独立させてある。
    """
    name = models.CharField(max_length=50, unique=True)
    ideal = models.IntegerField(help_text='理想の月額（円）')
    order = models.IntegerField(default=100, help_text='並び順（小さいほど先）')
    # 引き落としカレンダー用（2026-09-06）。日付は 1〜31（月末は 31）、未設定は None。
    # ⚠️ カード払いの項目は「その項目の日」ではなく**カードの引き落とし日**に1本で出る
    # （楽天は27日）。各サービスの日で並べると実際の口座の動きと合わない
    debit_day = models.IntegerField(null=True, blank=True, help_text='引き落とし日 1〜31（月末=31）')
    via_card = models.BooleanField(default=False, help_text='カード払い（カードの引き落とし日にまとめる）')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', '-ideal', 'name']

    def __str__(self):
        return f'{self.name} 理想 {self.ideal:,}円/月'


class SpendingSetting(models.Model):
    """支出分析の設定（1行だけ使うシングルトン）。引き落としカレンダーの基準日。"""
    card_debit_day = models.IntegerField(default=27, help_text='カードの引き落とし日（楽天=27）')
    salary_day = models.IntegerField(default=25, help_text='給与日（累計を切る目印）')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '支出分析設定'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
