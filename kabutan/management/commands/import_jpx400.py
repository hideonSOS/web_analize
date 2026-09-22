"""JPX日経400の構成銘柄リストを取り込む（Jpx400Member を全入替）

    python manage.py import_jpx400                 # 同梱CSV（kabutan/data/jpx400.csv）
    python manage.py import_jpx400 --csv path.csv  # 別ファイルを指定

CSVの列: code,name,company,sector（codeは4桁表示コード。日経公式サイト由来）。
毎年8月の定期入替後に新しいCSVを取得して再実行する。

⚠️ 銘柄解決は code = 表示コード + '0'（普通株に確定。9434優先株事故の再発防止）。
   Stock マスタに無いコードはスキップして警告を出す（新規上場直後など）。
"""
import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from japan_kabu.models import Stock
from kabutan.models import Jpx400Member

DEFAULT_CSV = Path(__file__).resolve().parents[2] / 'data' / 'jpx400.csv'


class Command(BaseCommand):
    help = 'JPX日経400の構成銘柄リストを取り込む（全入替）'

    def add_arguments(self, parser):
        parser.add_argument('--csv', type=str, default=str(DEFAULT_CSV),
                            help='構成銘柄CSVのパス（既定: kabutan/data/jpx400.csv）')

    def handle(self, *args, **options):
        path = Path(options['csv'])
        rows = list(csv.DictReader(path.open(encoding='utf-8-sig')))
        if not rows:
            self.stderr.write('CSVが空です')
            return

        codes5 = {r['code'].strip() + '0': r for r in rows}
        stocks = {s.code: s for s in Stock.objects.filter(code__in=codes5.keys())}

        missing = [c for c in codes5 if c not in stocks]
        for c in missing:
            self.stderr.write(f'  マスタに無い: {c[:-1]} {codes5[c]["name"]}（スキップ）')

        # 全入替（定期入替で外れた銘柄を残さない）
        Jpx400Member.objects.all().delete()
        objs = [Jpx400Member(stock=stocks[c], sector=codes5[c].get('sector', ''))
                for c in codes5 if c in stocks]
        Jpx400Member.objects.bulk_create(objs, batch_size=500)

        self.stdout.write(self.style.SUCCESS(
            f'JPX400: {len(objs)}銘柄を登録（CSV {len(rows)}件、マスタ欠落 {len(missing)}件）'))
