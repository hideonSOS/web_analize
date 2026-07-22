from django.db import models


class Stock(models.Model):
    """銘柄マスタ + 時価総額計算用データ

    日本株(country='JP')は update_marketcap 管理コマンドで J-Quants API から更新する。
    market_cap = close(調整前終値) × shares(期末発行済株式数・自己株含む)
    米国株(country='US')は import_us_master でティッカー一覧を取り込み、
    update_us_prices で「日記に登場したティッカーだけ」株価を更新する。
    """
    COUNTRY_CHOICES = [('JP', '日本株'), ('US', '米国株')]

    code = models.CharField(max_length=16, primary_key=True)      # JP:J-Quants5桁 / US:US-<ティッカー>
    display_code = models.CharField(max_length=12)                # JP:4桁 / US:ティッカー
    country = models.CharField(max_length=2, default='JP', choices=COUNTRY_CHOICES, db_index=True)
    name = models.CharField(max_length=100)
    market = models.CharField(max_length=30, blank=True)          # プライム/スタンダード/グロース、US:取引所
    sector33 = models.CharField(max_length=40, blank=True)        # 33業種名
    sector17 = models.CharField(max_length=40, blank=True)        # 17業種名（フィルタ用）

    shares = models.BigIntegerField(null=True, blank=True)        # 発行済株式数（ShOutFY）
    shares_disc_date = models.DateField(null=True, blank=True)    # その開示日
    close = models.FloatField(null=True, blank=True)              # 終値
    price_date = models.DateField(null=True, blank=True)          # 終値の日付
    market_cap = models.BigIntegerField(null=True, blank=True)    # 時価総額（円）

    # 出来高異常度（update_volume 管理コマンドで更新）
    volume = models.BigIntegerField(null=True, blank=True)        # 最新営業日の出来高（株）
    volume_date = models.DateField(null=True, blank=True)         # その日付
    volume_ratio = models.FloatField(null=True, blank=True)       # 過去20日平均比（倍率）
    volume_z = models.FloatField(null=True, blank=True)           # 対数出来高のz-score

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-market_cap']

    def __str__(self):
        return f"{self.display_code} {self.name}"


class FinancialReport(models.Model):
    """決算サマリー（通期FY + 四半期1Q/2Q/3Q）。銘柄別指標ページの計算元データ

    update_marketcap の決算走査時に蓄積される。同一決算期の訂正開示は
    開示日が新しいもので上書きする。四半期の損益（sales/op/np/eps）は
    期初からの累計値である点に注意（TTM計算時に単四半期へ変換する）。
    """
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='reports')
    per_type = models.CharField(max_length=2, default='FY')       # FY / 1Q / 2Q / 3Q
    per_end = models.DateField(null=True)                         # 当期末（CurPerEn）
    fy_end = models.DateField()                                   # 通期決算期末（CurFYEn）
    disc_date = models.DateField()                                # 開示日
    sales = models.BigIntegerField(null=True, blank=True)         # 売上高（円）
    op = models.BigIntegerField(null=True, blank=True)            # 営業利益（円）
    np = models.BigIntegerField(null=True, blank=True)            # 純利益（円）
    eps = models.FloatField(null=True, blank=True)
    bps = models.FloatField(null=True, blank=True)
    total_assets = models.BigIntegerField(null=True, blank=True)  # 総資産（TA）
    equity = models.BigIntegerField(null=True, blank=True)        # 自己資本（Eq）
    equity_ratio = models.FloatField(null=True, blank=True)       # 自己資本比率（EqAR）
    div_ann = models.FloatField(null=True, blank=True)            # 年間配当実績（DivAnn）
    nx_div_ann = models.FloatField(null=True, blank=True)         # 来期予想年間配当（NxFDivAnn）
    nx_np = models.BigIntegerField(null=True, blank=True)         # 来期予想純利益（NxFNp）
    shares = models.BigIntegerField(null=True, blank=True)        # 期末発行済株式数（ShOutFY）
    close = models.FloatField(null=True, blank=True)              # 当期末（直近営業日）の終値。PER/PBR推移用
    close_date = models.DateField(null=True, blank=True)          # その株価の日付

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['stock', 'per_end'], name='uniq_report_stock_perend'),
        ]
        ordering = ['per_end']

    def __str__(self):
        return f"{self.stock_id} {self.per_type} {self.per_end}"


class DailyPrice(models.Model):
    """日次終値の履歴（カルテ/売買日記に登録した銘柄のみ・約3年分）

    高値からの下落率（ドローダウン）とレンジ内位置の算出に使う。

    設計上の決めごと:
    - **終値ベースで保持する**。日中の高値/安値（ヒゲ）を使うと、たった1日の
      瞬間的な値で高安が決まり数字が不安定になるため採用しない。
    - **必ず調整後終値を入れる**（JP:AdjC / US:auto_adjust）。未調整のまま
      1年をまたぐと、株式分割時に株価が数分の1に飛び、高値が実態の数倍という
      壊れた数字になる（例: NVDAの10:1分割）。
    - 全銘柄ではなく登録銘柄のみ。12銘柄×3年でも約9,000行にしかならない。
    """
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='daily_prices')
    date = models.DateField(db_index=True)
    close = models.FloatField()                                   # 調整後終値

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['stock', 'date'], name='uniq_dailyprice_stock_date'),
        ]
        ordering = ['date']

    def __str__(self):
        return f"{self.stock_id} {self.date} {self.close}"


class DailyVolume(models.Model):
    """日次出来高の履歴（出来高異常度の計算用に直近分だけローリング保持する）"""
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name='daily_volumes')
    date = models.DateField(db_index=True)
    volume = models.BigIntegerField()                             # 出来高（株）
    turnover = models.BigIntegerField()                           # 売買代金（円）。薄商い除外フィルタ用

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['stock', 'date'], name='uniq_dailyvolume_stock_date'),
        ]

    def __str__(self):
        return f"{self.stock_id} {self.date} {self.volume}"
