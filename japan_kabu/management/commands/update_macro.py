"""マクロ指標（日米のCPI・失業率）を取得する

    python manage.py update_macro

APIキー不要の公開エンドポイント2系統から取る（requests は既存依存・追加なし）:
- **FRED**（セントルイス連銀）fredgraph.csv … 米国全系列 + 日本の失業率
- **DBnomics** … 日本のCPI（provider STATJP = 総務省統計局の公式データ。
  日本式の体系＝総合/生鮮食品を除く/生鮮食品及びエネルギーを除く がそのまま取れる）

⚠️ 日本のCPIをFRED/OECDで取らないこと。OECDの日本CPI系列は **2021-06で配信停止**
しており（提供打ち切り・実測）、IMF系列も約1年遅れる。DBnomicsのSTATJPだけが
米国と同じ鮮度（前月分まで）だった（2026-08時点の実測）。

⚠️ 季節調整済み系列は過去分も遡って改定されるため、差分取得ではなく毎回全期間を
取得して変化行を上書きする（全系列でも7,000行未満・数秒）。
"""
import csv
import io
from datetime import date, datetime

import requests
from django.core.management.base import BaseCommand

from japan_kabu.models import MacroIndicator

FRED_CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}'
DBNOMICS = 'https://api.db.nomics.world/v22/series/{sid}?observations=1'

# 保存キー → (取得元, 取得元でのID)。ビューは保存キーで引く
SOURCES = {
    # 米国
    'CPIAUCSL':      ('fred', 'CPIAUCSL'),         # 総合CPI（1982-84=100）
    'CPILFESL':      ('fred', 'CPILFESL'),         # コアCPI（食品・エネルギー除く）
    'UNRATE':        ('fred', 'UNRATE'),           # 失業率 U-3
    # 日本
    'JPCPI_ALL':     ('dbnomics', 'STATJP/CPIm/001'),  # 総合（2020=100）
    'JPCPI_CORE':    ('dbnomics', 'STATJP/CPIm/733'),  # 生鮮食品を除く総合（日銀コア）
    'JPCPI_CORECORE': ('dbnomics', 'STATJP/CPIm/740'),  # 生鮮食品及びエネルギーを除く
    'JPUNRATE':      ('fred', 'LRUNTTTTJPM156S'),  # 完全失業率（季節調整済み）
    # 金利（月次平均）。⚠️ DGS10等の日次系列は使わない（ページの他系列が月次で、
    # カテゴリ軸 'YYYY-MM' に揃えているため。FREDには月次平均のGS系がある）
    'GS10':          ('fred', 'GS10'),             # 米10年国債利回り（1953〜）
    'GS2':           ('fred', 'GS2'),              # 米2年国債利回り（1976〜）逆イールド判定用
    'FEDFUNDS':      ('fred', 'FEDFUNDS'),         # FF金利実効値（政策金利）
    'JP10Y':         ('fred', 'IRLTLT01JPM156N'),  # 日本10年国債利回り（OECD経由・
                                                   # CPIと違い金利系列は配信継続中。1989〜）
}


class Command(BaseCommand):
    help = 'マクロ指標（日米のCPI・失業率）をFRED/DBnomicsから取得する'

    def handle(self, *args, **options):
        total_new = total_upd = 0
        for key, (src, sid) in SOURCES.items():
            try:
                rows = self._fetch_fred(sid) if src == 'fred' else self._fetch_dbnomics(sid)
            except Exception as e:  # noqa: BLE001  1系列の失敗で全体を止めない
                self.stderr.write(f'  {key}: 取得失敗: {e}')
                continue
            n_new, n_upd = self._store(key, rows)
            total_new += n_new
            total_upd += n_upd
            last = rows[-1] if rows else None
            self.stdout.write(f'  {key:15} {len(rows)}行  新規{n_new} 改定{n_upd}  最新: {last[0]} = {last[1]}')
        self.stdout.write(self.style.SUCCESS(f'マクロ指標: 新規{total_new}行 / 改定{total_upd}行'))

    @staticmethod
    def _fetch_fred(sid):
        """[(date, value), ...]。欠測（'.'）は飛ばす"""
        r = requests.get(FRED_CSV.format(sid=sid), timeout=60)
        r.raise_for_status()
        out = []
        for row in csv.reader(io.StringIO(r.text)):
            if not row or row[0] == 'observation_date':
                continue
            if len(row) < 2 or row[1] in ('', '.'):
                continue
            out.append((datetime.strptime(row[0], '%Y-%m-%d').date(), float(row[1])))
        return out

    @staticmethod
    def _fetch_dbnomics(sid):
        """DBnomicsのJSON（period='YYYY-MM'の月次前提）→ [(date, value), ...]"""
        r = requests.get(DBNOMICS.format(sid=sid), timeout=60)
        r.raise_for_status()
        docs = r.json()['series']['docs']
        if not docs:
            raise ValueError('系列が見つからない')
        out = []
        for p, v in zip(docs[0]['period'], docs[0]['value']):
            if not isinstance(v, (int, float)):   # 欠測は "NA" 等の文字列で返る
                continue
            y, m = p.split('-')
            out.append((date(int(y), int(m), 1), float(v)))
        return out

    @staticmethod
    def _store(key, rows):
        """新規は挿入・値が変わった既存行は上書き（季節調整の遡及改定に追従する）"""
        existing = dict(MacroIndicator.objects.filter(series=key)
                        .values_list('date', 'value'))
        to_create, to_update = [], []
        for d, v in rows:
            if d not in existing:
                to_create.append(MacroIndicator(series=key, date=d, value=v))
            elif abs(existing[d] - v) > 1e-9:
                to_update.append((d, v))
        if to_create:
            MacroIndicator.objects.bulk_create(to_create, batch_size=1000)
        for d, v in to_update:
            MacroIndicator.objects.filter(series=key, date=d).update(value=v)
        return len(to_create), len(to_update)
