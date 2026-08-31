"""日次の資産スナップショットを記録する（資産推移グラフの材料）

AssetSnapshot モデルのdocstringが前提とする夜間バッチ。表示時に毎回全履歴を
再計算すると価格履歴の欠損で過去の数字が変わってしまうため、
「その日に計算した結果」を確定値として残す。**過去分は再構築できない**ので、
記録しなかった日の歴史はそのまま失われる（cron登録が必須な理由）。

    python manage.py snapshot_assets    # 今日のスナップショットを記録（同日再実行は上書き）

⚠️ 2026-08-31まで**このコマンド自体が未実装だった**（モデル・adminだけ存在し
0行のまま）。実装と同時に daily_update.sh へ登録した。
update_product_prices の後に実行すること（最新の基準価額・為替で評価するため）。
"""
from datetime import date

from django.core.management.base import BaseCommand

from portfolio.models import AssetSnapshot
from portfolio.services import build_portfolio


class Command(BaseCommand):
    help = '日次の資産スナップショットを記録する（同日の再実行は上書き）'

    def handle(self, *args, **options):
        data = build_portfolio()
        by = data['by_class']
        # 投資元本 = 総資産 − 含み損益（＝取得原価ベースの投下額 + 現金）。
        # モデルコメントの「期首総資産 + 入金累計 - 出金累計」の実装形
        # （期首総資産そのものは保存していないため、原価から導出する）
        principal = data['total'] - data['unrealized']
        snap, created = AssetSnapshot.objects.update_or_create(
            date=date.today(),
            defaults={
                'stock_jp': by['stock_jp']['value'],
                'stock_us': by['stock_us']['value'],
                'fund': by['fund']['value'],
                'metal': by['metal']['value'],
                'cash': by['cash']['value'],
                'total': data['total'],
                'principal': principal,
            })
        self.stdout.write(self.style.SUCCESS(
            f'{"記録" if created else "上書き"}: {snap.date} 総資産 ¥{snap.total:,.0f} '
            f'(元本 ¥{snap.principal:,.0f})'))
