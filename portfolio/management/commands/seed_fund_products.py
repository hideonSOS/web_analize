"""投資信託の商品マスタ（プルダウン候補）を初期投入する

商品マスタはDBデータなので git では運ばれない。サーバーの初回セットアップ時に
このコマンドを1回実行して候補を揃える（何度実行しても安全な冪等設計。
既存の商品は上書きしない）。

    python manage.py seed_fund_products

新しい投信を候補に足したいときは、このリストに1行追加してデプロイするか、
登録ページ下部の「投信の商品情報を編集」で直接追加・編集する。
ISIN・協会コードは投信協会「投信総合検索ライブラリー」の詳細ページURLに
両方入っている（?isinCd=...&associFundCd=...）。
"""
from django.core.management.base import BaseCommand

from portfolio.models import Product

# (表示名, ISINコード, 協会コード) — いずれも投信協会ライブラリーで検証済み
FUNDS = [
    ('オルカン', 'JP90C000H1T1', '0331418A'),        # eMAXIS Slim 全世界株式（オール・カントリー）
    ('S&P500', 'JP90C000GKC6', '03311187'),          # eMAXIS Slim 米国株式（S&P500）
    ('FANG+', 'JP90C000FZD4', '04311181'),           # iFreeNEXT FANG+インデックス
    ('TOPIX', 'JP90C000ENA9', '03317172'),           # eMAXIS Slim 国内株式（TOPIX）
    ('日経平均', 'JP90C000FXV1', '03311182'),        # eMAXIS Slim 国内株式（日経平均）
]


class Command(BaseCommand):
    help = '投信の商品マスタ（プルダウン候補）を初期投入する（冪等）'

    def handle(self, *args, **options):
        created = 0
        for name, isin, assoc in FUNDS:
            # 名前でもISINでも既存判定する（片方だけ変えた再実行で重複させない）
            exists = Product.objects.filter(category='fund').filter(
                name=name).exists() or Product.objects.filter(isin=isin).exists()
            if exists:
                self.stdout.write(f'{name}: 既存のためスキップ')
                continue
            Product.objects.create(
                category='fund', name=name, isin=isin, assoc_fund_code=assoc)
            created += 1
            self.stdout.write(f'{name}: 作成（{isin} / {assoc}）')
        self.stdout.write(self.style.SUCCESS(
            f'完了: 新規{created}件 / 合計{Product.objects.filter(category="fund").count()}件。'
            '続けて update_product_prices を実行すると基準価額が入る'))
