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
from datetime import date, datetime, timedelta

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
    # 2026-09-08 追加（ユーザー要望: ドル円と日米の政策金利）
    'USDJPY':        ('fred', 'EXJPUS'),           # ドル円（月中平均・円/ドル。1971〜。日次の
                                                   # DEXJPUS は使わない＝他系列と同じ月次に揃える）
    'JPCALL':        ('fred', 'IRSTCI01JPM156N'),  # 日本の無担保コール翌日物（月平均）＝政策金利の
                                                   # 実勢。⚠️ 中銀金利そのものの IRSTCB01JPM156N は
                                                   # 2023-12 で配信停止、公定歩合 INTDSRJPM193N は
                                                   # 2017-04 で停止（実測）。コールレートだけが現役
                                                   # （約2か月遅れ）。米国の政策金利は FEDFUNDS
    # 株価指数の月末値（yfinance・月足）。「特殊」タブの相関分析用（2026-09-08）。
    # FRED の SP500 は直近10年しか無いので yfinance（^GSPC は 1927〜、^N225 は 1965〜）
    'SP500':         ('yf', '^GSPC'),
    'N225':          ('yf', '^N225'),
    # 金・銀・ビットコイン（ユーザー要望 2026-09-08）。先物の連続足はETFより履歴が長い
    # （GC=F/SI=F は 2000〜、BTC-USD は 2014〜）。単位はドル（金銀=トロイオンス）
    'GOLD':          ('yf', 'GC=F'),
    'SILVER':        ('yf', 'SI=F'),
    'BTC':           ('yf', 'BTC-USD'),
}

# 日次の金利（2026-09-27 追加）。⚠️ 月平均の系列は翌月まで出ず、動きの速い局面で大きくずれる
# （実測: 米10年の 8月平均 4.68% に対し 9/24 の日次は 5.18%、FF 金利は 9月に利上げして 8月平均 3.63%
#  のまま表示していた）。チップの「最新値」と、チャート末尾の当月（途中）平均はこの日次から作る。
# 月次チャートの系列そのもの（GS10 等）は従来どおり。保存キーは 'D_' 始まり（月次と混ぜない）。
# 直近 DAILY_DAYS 日だけ持つ（全期間は不要＝月次で足りる）
DAILY_DAYS = 800
DAILY_SOURCES = {
    'D_DGS10':  ('fred', 'DGS10'),      # 米10年国債利回り（日次・FRED は1〜2営業日遅れ）
    'D_DGS2':   ('fred', 'DGS2'),       # 米2年国債利回り（日次）
    'D_DFF':    ('fred', 'DFF'),        # FF 金利の実効値（日次）
    'D_FEDLO':  ('fred', 'DFEDTARL'),   # FF 金利の誘導目標レンジ下限
    'D_FEDUP':  ('fred', 'DFEDTARU'),   # 同 上限
    'D_JGB10':  ('mof', '10年'),        # 日本10年国債（財務省「国債金利情報」の日次・前営業日まで）
    'D_JPCALL': ('boj', 'FM01/STRDCLUCON'),  # 無担保コール O/N 平均（日銀 時系列統計 API・日次）
}
# 日本の CPI は 2026年8月分から「2025年基準」に切り替わった（総務省。旧 2020年基準は DBnomics の STATJP）。
# 旧基準のまま前年比を出すと公表値より 0.1pt 高かった（2026-09-27 に発覚: 8月 総合 2.0 vs 公表 1.9、
# コア 1.8 vs 1.7、コアコア 2.0 vs 1.9）。2026年以降は新基準の前年比になるよう、旧基準の系列に接続する
# （_splice_2025base）。CSV は 2025年1月から（前年比は 2026年1月から出せる）
STATJP_2025 = 'https://www.stat.go.jp/data/cpi/2025/csv/zmi2025aa.csv'
STATJP_2025_CODES = {'JPCPI_ALL': '0001', 'JPCPI_CORE': '0161', 'JPCPI_CORECORE': '0178'}
SPLICE_FROM = date(2026, 1, 1)
MOF_ALL = 'https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv'   # 前月末まで
MOF_CUR = 'https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv'             # 当月分
BOJ_API = 'https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=csv&lang=jp&db={db}&code={code}&startDate={start}'
ERA = {'S': 1925, 'H': 1988, 'R': 2018}

# 鮮度の許容日数（最新データの日付＝月次は月初日 から今日までの日数）。これを超えたら「止まっている」とみなす。
# 毎朝の実行でチェックし、画面のチップにも ⚠ を出す（2026-09-27 ユーザー要望: 毎月また遅れだすと判断を誤る）。
# 目安: 米 CPI・失業率は翌月中旬に出る → 8月分は 9/15 ごろ。75日＝翌々月の中旬まで出なければ異常
#       日本の失業率（OECD 経由）は約2か月遅れ → 100日。日次は祝日・週末込みで 7日
MAX_AGE_DAYS = {
    'CPIAUCSL': 75, 'CPILFESL': 75, 'UNRATE': 75, 'GS10': 75, 'GS2': 75, 'FEDFUNDS': 75,
    'JPCPI_ALL': 75, 'JPCPI_CORE': 75, 'JPCPI_CORECORE': 75, 'JPUNRATE': 100,
    'JP10Y': 100, 'JPCALL': 100, 'USDJPY': 75,
    'SP500': 40, 'N225': 40, 'GOLD': 40, 'SILVER': 40, 'BTC': 40,
    'D_DGS10': 7, 'D_DGS2': 7, 'D_DFF': 7, 'D_FEDLO': 7, 'D_FEDUP': 7, 'D_JGB10': 7, 'D_JPCALL': 14,
}


def stale_series(today=None):
    """[(保存キー, 最新日, 経過日数, 許容日数)] … 許容日数を超えて更新が止まっている系列"""
    from django.db.models import Max
    today = today or date.today()
    latest = dict(MacroIndicator.objects.values('series').annotate(m=Max('date')).values_list('series', 'm'))
    out = []
    for key, limit in MAX_AGE_DAYS.items():
        d = latest.get(key)
        age = (today - d).days if d else None
        if age is None or age > limit:
            out.append((key, d, age, limit))
    return out


class Command(BaseCommand):
    help = 'マクロ指標（日米のCPI・失業率）をFRED/DBnomicsから取得する'

    def handle(self, *args, **options):
        total_new = total_upd = 0
        try:
            cpi2025 = self._fetch_statjp_2025()
        except Exception as e:  # noqa: BLE001  取れなければ旧基準のまま（公表値と 0.1pt ずれうる）
            self.stderr.write(f'  日本CPI 2025年基準: 取得失敗（旧基準のまま）: {e}')
            cpi2025 = {}
        for key, (src, sid) in SOURCES.items():
            try:
                rows = {'fred': self._fetch_fred, 'dbnomics': self._fetch_dbnomics,
                        'yf': self._fetch_yf}[src](sid)
            except Exception as e:  # noqa: BLE001  1系列の失敗で全体を止めない
                self.stderr.write(f'  {key}: 取得失敗: {e}')
                continue
            if cpi2025.get(key):
                rows = self._splice_2025base(rows, cpi2025[key])
            n_new, n_upd = self._store(key, rows)
            total_new += n_new
            total_upd += n_upd
            last = rows[-1] if rows else None
            self.stdout.write(f'  {key:15} {len(rows)}行  新規{n_new} 改定{n_upd}  最新: {last[0]} = {last[1]}')
        since = date.today() - timedelta(days=DAILY_DAYS)
        for key, (src, sid) in DAILY_SOURCES.items():
            try:
                rows = {'fred': self._fetch_fred, 'mof': self._fetch_mof, 'boj': self._fetch_boj}[src](sid)
                rows = [(d, v) for d, v in rows if d >= since]
                if not rows:
                    raise ValueError('直近のデータが無い')
            except Exception as e:  # noqa: BLE001
                self.stderr.write(f'  {key}: 取得失敗: {e}')
                continue
            n_new, n_upd = self._store(key, rows)
            total_new += n_new
            total_upd += n_upd
            self.stdout.write(f'  {key:15} {len(rows)}行  新規{n_new} 改定{n_upd}  最新: {rows[-1][0]} = {rows[-1][1]}')
        self.stdout.write(self.style.SUCCESS(f'マクロ指標: 新規{total_new}行 / 改定{total_upd}行'))
        stale = stale_series()
        for key, d, age, limit in stale:
            self.stderr.write(f'  [STALE] {key}: 最新 {d}（{age}日前・許容 {limit}日）… 取得元の停止・書式変更を疑う')
        if stale:
            raise SystemExit(1)     # cron ログで FAILED にする（画面のチップにも ⚠ が出る）

    @staticmethod
    def _fetch_statjp_2025():
        """総務省 2025年基準 CPI（全国・月次・cp932）→ {保存キー: {date: 指数}}"""
        r = requests.get(STATJP_2025, timeout=60)
        r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.content.decode('cp932', errors='ignore'))))
        codes = next((row for row in rows if row and row[0].startswith('類・品目符号')), None)
        if not codes:
            raise ValueError('類・品目符号の行が無い（書式変更の可能性）')
        out = {}
        for key, code in STATJP_2025_CODES.items():
            if code not in codes:
                raise ValueError(f'符号 {code} が無い')
            ci = codes.index(code)
            vals = {}
            for row in rows:
                if row and len(row[0]) == 6 and row[0].isdigit() and len(row) > ci and row[ci]:
                    vals[date(int(row[0][:4]), int(row[0][4:]), 1)] = float(row[ci])
            out[key] = vals
        return out

    @staticmethod
    def _splice_2025base(old_rows, new):
        """旧基準（2020年=100）の系列の 2026年以降を、新基準の前年比になる値に置き換える

        2026年m月 = 新(2026年m月) × 旧(2025年m月) ÷ 新(2025年m月)。月ごとの係数で接続するので、
        2025年までの前年比は旧基準の公表値のまま、2026年以降の前年比は新基準の公表値と一致する
        （2027年以降も同じ月の係数を使うので、前年比は 新/新 になる）。水準は旧基準の目盛りのまま
        """
        old = dict(old_rows)
        factor = {}
        for d, v in new.items():
            if d.year == 2025 and old.get(d):
                factor[d.month] = old[d] / v
        out = {d: v for d, v in old.items() if d < SPLICE_FROM}
        for d, v in new.items():
            if d >= SPLICE_FROM and d.month in factor:
                out[d] = v * factor[d.month]
        return sorted(out.items())

    @staticmethod
    def _fetch_mof(col):
        """財務省の国債金利情報（cp932・和暦の日付 R8.9.24）→ [(date, value)]。前月末までの全期間＋当月分"""
        out = {}
        for url in (MOF_ALL, MOF_CUR):
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.content.decode('cp932', errors='ignore'))))
            head = next((row for row in rows if row and row[0] == '基準日'), None)
            if not head or col not in head:
                raise ValueError(f'列 {col} が見つからない（書式変更の可能性）')
            ci = head.index(col)
            for row in rows:
                if len(row) <= ci or not row[0] or row[0][0] not in ERA:
                    continue
                try:
                    y, m, d = row[0][1:].split('.')
                    out[date(ERA[row[0][0]] + int(y), int(m), int(d))] = float(row[ci])
                except ValueError:      # '-'（その年限が無い日）や注記行
                    continue
        return sorted(out.items())

    @staticmethod
    def _fetch_boj(sid):
        """日銀 時系列統計データ検索サイトの API（CSV）→ [(date, value)]。null（休日）は飛ばす"""
        db, code = sid.split('/')
        start = (date.today() - timedelta(days=DAILY_DAYS)).strftime('%Y%m')
        r = requests.get(BOJ_API.format(db=db, code=code, start=start), timeout=60)
        r.raise_for_status()
        out = []
        for row in csv.reader(io.StringIO(r.content.decode('cp932', errors='ignore'))):
            if len(row) < 8 or row[0] != code or len(row[6]) != 8 or row[7] in ('', 'null'):
                continue
            out.append((datetime.strptime(row[6], '%Y%m%d').date(), float(row[7])))
        return sorted(out)

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
    def _fetch_yf(ticker):
        """yfinance の日足終値を月末値に丸める → [(月初日, その月の最終終値), ...]。

        ⚠️ interval='1mo' の月足は使わない。先物（GC=F/SI=F）の月足は月が抜ける
        （実測: 2015〜2026 の 141 か月中 120 か月しか無く、直近1年の相関が計算不能になった）。
        ^GSPC の月足も 1985 年からしか返らない。日足を全期間取って自分で月末値にすれば
        両方とも解決する（^GSPC は 1927〜・約2.5万行、1銘柄1コール・数秒）。
        当月は途中の最終終値が入り、毎朝更新される
        """
        import yfinance as yf
        df = yf.download(ticker, period='max', interval='1d', auto_adjust=False, progress=False)
        if df is None or df.empty:
            raise ValueError('取得結果が空')
        close = df['Close']
        if hasattr(close, 'columns'):          # MultiIndex 列（yfinance 0.2.5x〜）
            close = close.iloc[:, 0]
        by = {}
        for ts, v in close.dropna().items():
            d = ts.date() if hasattr(ts, 'date') else ts
            by[date(d.year, d.month, 1)] = float(v)   # 日付順なので後勝ち＝月末
        return sorted(by.items())

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
