"""kabutan — 株タン手法（押し目買いスイング）の日次スクリーナー

対象ユニバース = JPX日経400（Jpx400Member）∪ カルテ登録銘柄（JPのみ）。
日足は yfinance の調整後OHLCを KabutanBar に蓄積し（日付別ではなく銘柄別テーブル）、
判定結果は WAIT も含めて ScreenResult に全件記録する（仕様書の
「不成立・見送りも記録する」要件。ルール版ごとに成績を分離できる）。

⚠️ DailyPrice（終値のみ）とは別テーブル。ローソク足（始値・安値）が必要なため。
⚠️ ルールの定義・検証結果は Desktop\\study\\files\\kabutan_backtest.py /
   kabutan_v11_test.py（バックテスト側）と kabutan/logic.py の docstring を参照。
"""
from django.db import models

from japan_kabu.models import Stock


class Jpx400Member(models.Model):
    """JPX日経400の構成銘柄（毎年8月に定期入替 → import_jpx400 で更新）"""
    stock = models.OneToOneField(Stock, on_delete=models.CASCADE,
                                 related_name='jpx400', verbose_name='銘柄')
    sector = models.CharField('業種（日経分類）', max_length=20, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['stock_id']

    def __str__(self):
        return f'{self.stock.display_code} {self.stock.name}'


class KabutanBar(models.Model):
    """日足（調整後OHLC＋出来高）。yfinance auto_adjust=True で取得。

    ⚠️ 必ず調整後を保存する（未調整だと分割で価格が飛びMAが壊れる）。
    ⚠️ 取引時間中の未確定当日バーは保存しない（run_kabutan_screen のガード参照）。
    """
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE,
                              related_name='kabutan_bars')
    date = models.DateField(db_index=True)
    open = models.FloatField()
    high = models.FloatField()
    low = models.FloatField()
    close = models.FloatField()
    volume = models.BigIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['stock', 'date'],
                                               name='uniq_kabutanbar_stock_date')]
        ordering = ['date']


class ScreenResult(models.Model):
    """日次判定の記録（BUY_CANDIDATE だけでなく WAIT も全銘柄分残す）"""
    JUDGMENTS = [
        ('BUY', 'BUY_CANDIDATE'),
        ('WAIT', 'WAIT'),
        ('NODATA', 'INSUFFICIENT_DATA'),
    ]
    date = models.DateField('判定日', db_index=True)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE,
                              related_name='kabutan_results')
    rule_version = models.CharField('ルール版', max_length=16, default='v1.1')
    judgment = models.CharField('判定', max_length=8, choices=JUDGMENTS)
    met = models.JSONField('成立した条件', default=list, blank=True)
    unmet = models.JSONField('不成立の条件', default=list, blank=True)
    close = models.FloatField('判定日終値', null=True, blank=True)
    stop_price = models.FloatField('損切り価格（直近10日安値）', null=True, blank=True)
    tp_price = models.FloatField('利確目安（20日MA+10%）', null=True, blank=True)
    source = models.CharField('ユニバース', max_length=8, default='jpx400')  # jpx400 / karte / both
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['date', 'stock', 'rule_version'],
            name='uniq_screenresult_date_stock_rule')]
        ordering = ['-date']
