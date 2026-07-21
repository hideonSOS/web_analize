"""米国株のティッカー一覧を取り込む（売買日記の銘柄選択・検索の正規キー用）

NASDAQ Trader の公開シンボルディレクトリ（無料・静的ファイル）を取得する:
  - nasdaqlisted.txt : NASDAQ 上場銘柄
  - otherlisted.txt  : NYSE / NYSE American / Cboe 等

使い方:
    python manage.py import_us_master

ティッカーは display_code に、PK code は "US-<ティッカー>" として保存する
（日本株の数字コードと衝突させないため）。株価はここでは取得しない
（update_us_prices が日記に登場したティッカーだけ更新する）。
"""
import urllib.request

from django.core.management.base import BaseCommand

from japan_kabu.models import Stock

SOURCES = [
    ('https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt', 'NASDAQ', 'Symbol'),
    ('https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt', 'NYSE/その他', 'ACT Symbol'),
]


class Command(BaseCommand):
    help = '米国株ティッカー一覧を取り込む（NASDAQ Trader）'

    def handle(self, *args, **options):
        rows = {}
        for url, exchange, symbol_col in SOURCES:
            for rec in self._fetch(url):
                # テスト銘柄は除外
                if rec.get('Test Issue') == 'Y':
                    continue
                symbol = (rec.get(symbol_col) or '').strip()
                name = (rec.get('Security Name') or '').strip()[:100]  # nameのmax_lengthに合わせる
                # ティッカーに記号を含むもの（優先株・ワラント等）は除外して検索を素直に保つ
                if not symbol or not name or not symbol.isalnum():
                    continue
                rows[symbol] = (name, exchange)

        objs = [
            Stock(
                code=f'US-{sym}',
                display_code=sym,
                country='US',
                name=name,
                market=exchange,
            )
            for sym, (name, exchange) in rows.items()
        ]
        # 既存US銘柄を入れ替え（上場廃止分の掃除も兼ねる）。日本株には触れない
        deleted, _ = Stock.objects.filter(country='US').exclude(
            code__in=[o.code for o in objs]).delete()
        Stock.objects.bulk_create(
            objs, batch_size=1000,
            update_conflicts=True, unique_fields=['code'],
            update_fields=['display_code', 'name', 'market', 'country'])
        self.stdout.write(self.style.SUCCESS(
            f'米国株マスタ取り込み: {len(objs)}銘柄（削除 {deleted}）'))

    @staticmethod
    def _fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        text = urllib.request.urlopen(req, timeout=60).read().decode('latin-1')
        lines = text.splitlines()
        header = lines[0].split('|')
        for line in lines[1:]:
            # 末尾の「File Creation Time...」行を除外
            if line.startswith('File Creation Time') or '|' not in line:
                continue
            values = line.split('|')
            if len(values) != len(header):
                continue
            yield dict(zip(header, values))
