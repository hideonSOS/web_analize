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

    # 戦略タグと、短期取引（Trade）への紐付け（2026-09-09）。短期の買い/売りは
    # Trade 側から自動で日記にも書かれるので、日記は「全部の記録」として読める
    strategy = models.CharField(max_length=10, blank=True, default='')
    trade = models.ForeignKey('Trade', null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='diary_entries')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.recorded_at:%Y-%m-%d} {self.stock_name} {self.get_action_display()}"


class ContraSetting(models.Model):
    """短期トレードの設定（1行のシングルトン）。資金は手入力（遊び・学習目的、ユーザー方針）"""
    # ⚠️ 為替は考えない（ユーザー決定 2026-09-09）。一度ドルに替えたら円に戻さず運用するので、
    # 資金も損益も取引の通貨（米国株ならドル）のまま扱う。戦略そのものの精度を見るのが目的
    capital = models.IntegerField(default=10_000, help_text='資金全体（取引の通貨のまま。米国株ならドル）。1〜2%ルールの分母')
    risk_pct = models.FloatField(default=2.0, help_text='1回の損失の上限（資金の%）')
    default_stop_pct = models.FloatField(default=5.0, help_text='損切り幅の既定（%）')
    default_target_pct = models.FloatField(default=10.0, help_text='利確幅の既定（%）')
    # moomoo証券ベーシックコースの米国株手数料 = 約定金額の 0.12%（税込 0.132%）・上限 22 米ドル
    # （2026-09-09 実測・ユーザー指定「moomoo に近しい値」）。上限は未考慮（$16,700 超の注文で効く）
    cost_pct = models.FloatField(default=0.132, help_text='片道の手数料（%）。moomoo ベーシック 税込 0.132%')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return '短期設定'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Trade(models.Model):
    """1往復の取引（エントリー〜イグジット）。短期ルールの機械的な運用と成績集計の単位。

    日記（DiaryEntry）は「判断の記録」で編集しない。こちらは帳簿で、損切り・利確ラインを
    エントリー時に確定して持つ。価格は銘柄の通貨のまま（米国株はドル。指値を入れる基準が
    ドルなので、円換算して混ぜない）。1〜2%ルールも同じ通貨で判定する（為替は考えない方針）。
    """
    STRATEGY = [('contra', '短期'), ('long', '長期'), ('div', '配当')]
    # early=早期利確: +10% に届く前に利益で降りた（勝率には入れず、損益だけ積算。ユーザー決定 2026-09-09）
    EXIT = [('stop', '損切り'), ('target', '利確'), ('early', '早期利確'), ('manual', '裁量'), ('time', '期限')]

    stock = models.ForeignKey(Stock, null=True, blank=True, on_delete=models.SET_NULL)
    stock_name = models.CharField(max_length=100)
    ticker = models.CharField(max_length=20)            # 表示コード（US: 'RGTI' / JP: '6758'）
    country = models.CharField(max_length=2, default='US')
    currency = models.CharField(max_length=3, default='USD')
    strategy = models.CharField(max_length=10, choices=STRATEGY, default='contra')

    entry_date = models.DateField()
    entry_price = models.FloatField()
    shares = models.IntegerField()
    stop_pct = models.FloatField()
    target_pct = models.FloatField()
    stop_price = models.FloatField()                    # エントリー時に確定（指値の基準）
    target_price = models.FloatField()
    entry_note = models.TextField(blank=True)
    fx_at_entry = models.FloatField(null=True, blank=True)   # 未使用（為替は考えない方針。互換のため残す）
    risk_jpy = models.FloatField(null=True, blank=True)      # 計画時の最大損失（取引の通貨。列名は歴史的事情）
    over_risk = models.BooleanField(default=False)          # 1〜2%ルールを超えて入った（裁量）

    exit_date = models.DateField(null=True, blank=True)
    exit_price = models.FloatField(null=True, blank=True)
    exit_reason = models.CharField(max_length=10, blank=True, choices=EXIT)
    exit_note = models.TextField(blank=True)

    entry_diary = models.ForeignKey(DiaryEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='+')
    exit_diary = models.ForeignKey(DiaryEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-entry_date', '-id']

    def __str__(self):
        return f'{self.entry_date} {self.ticker} {self.get_strategy_display()}'

    @property
    def is_open(self):
        return self.exit_date is None

    @property
    def pnl_pct(self):
        """粗利（%）。未決済は None"""
        if self.exit_price is None or not self.entry_price:
            return None
        return (self.exit_price / self.entry_price - 1) * 100

    def pnl_pct_net(self, cost_pct):
        p = self.pnl_pct
        return None if p is None else p - 2 * cost_pct

    @property
    def pnl_amount(self):
        if self.exit_price is None:
            return None
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def days_held(self):
        from datetime import date
        end = self.exit_date or date.today()
        return (end - self.entry_date).days


class TradeBar(models.Model):
    """取引中の日足（生の OHLC・調整なし）。ザラ場の安値/高値で損切り・利確に触れたかを見る。

    ⚠️ DailyPrice（調整後終値）は使わない。指値は生の価格に置くので判定も生の価格で行う。
    """
    trade = models.ForeignKey(Trade, on_delete=models.CASCADE, related_name='bars')
    date = models.DateField()
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['trade', 'date'], name='uniq_trade_bar')]
        ordering = ['date']
