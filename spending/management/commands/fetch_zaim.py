"""Zaim 公式 API から記録を取り、エクスポート CSV と同じ形で保存して取り込む。

    python manage.py fetch_zaim                 # 取得 → data/spending/zaim/ に保存 → 取り込み
    python manage.py fetch_zaim --no-import     # 保存だけ
    python manage.py fetch_zaim --check         # 最新の手動 CSV と月×支払元で突き合わせて欠けを表示
    python manage.py fetch_zaim --since 2024-01-01

設定は config.json の "zaim" キー（settings.ZAIM_API）:
    "zaim": {"consumer_key": "...", "consumer_secret": "...",
             "access_token": "...", "access_token_secret": "..."}
トークンは scripts/zaim_authorize.py をローカルで一度実行して発行する（ブラウザ承認）。
未設定なら何もせず正常終了する（cron に入れたまま未設定でも失敗扱いにしない）。

⚠️ API は手入力の記録しか返さない（口座連携の自動取込は返らない、と公式注記）。
   楽天カード連携・銀行連携の行が欠ける可能性があるので、初回は必ず --check で
   最新の手動 CSV と比べ、欠けが大きい支払元があれば手動アップロードを併用する。
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from card_insight import zaim_api
from spending import services

DEFAULT_SINCE = '2016-01-01'     # 実物 CSV の最古が 2016-01-06。全期間を毎回取る（約150ページ）
KEEP_API_FILES = 3               # 世代を残す数（古い API 生成ファイルだけ消す。手動分は消さない）


class Command(BaseCommand):
    help = 'Zaim API から記録を取得し、エクスポート CSV と同じ形で保存して取り込む'

    def add_arguments(self, parser):
        parser.add_argument('--since', default=DEFAULT_SINCE, help='取得開始日 YYYY-MM-DD')
        parser.add_argument('--no-import', action='store_true', help='保存だけで取り込みはしない')
        parser.add_argument('--check', action='store_true',
                            help='最新の手動 CSV と月×支払元で突き合わせ、欠けを表示（保存も取り込みもしない）')
        parser.add_argument('--keep', type=int, default=KEEP_API_FILES)

    def handle(self, *args, **opts):
        conf = getattr(settings, 'ZAIM_API', {}) or {}
        need = ('consumer_key', 'consumer_secret', 'access_token', 'access_token_secret')
        if not all(conf.get(k) for k in need):
            self.stdout.write('Zaim API は未設定（config.json の "zaim" に4つの鍵が必要）。何もしません。')
            return

        client = zaim_api.ZaimClient(conf['consumer_key'], conf['consumer_secret'],
                                     conf['access_token'], conf['access_token_secret'])
        try:
            me = client.verify()
        except Exception as e:   # noqa: BLE001
            raise CommandError(f'Zaim API の認証に失敗: {e}')
        self.stdout.write(f'Zaim API 認証OK: user_id={me.get("id")} login={me.get("login", "")}')

        categories, genres, accounts = client.categories(), client.genres(), client.accounts()
        self.stdout.write(f'マスタ: カテゴリ{len(categories)} 内訳{len(genres)} 口座{len(accounts)}')

        since = opts['since']
        try:
            date.fromisoformat(since)
        except ValueError:
            raise CommandError('--since は YYYY-MM-DD')
        records = list(client.iter_money(since))
        rows = zaim_api.records_to_rows(records, categories, genres, accounts)
        self.stdout.write(f'記録 {len(records):,}件（{since} 以降）→ CSV行 {len(rows):,}')
        if not rows:
            raise CommandError('記録が0件。API の権限か --since を確認してください')

        if opts['check']:
            self._check(rows)
            return

        services._ensure_dirs()
        path = services.ZAIM_DIR / zaim_api.api_csv_name(datetime.now())
        zaim_api.write_csv(rows, path)
        self.stdout.write(f'保存: {path.name}（{path.stat().st_size:,} bytes）')
        self._prune(opts['keep'])

        if opts['no_import']:
            return
        log = services.import_from_files()
        self.stdout.write(('取り込み: ' if log.ok else '取り込み失敗: ') + log.message)
        if not log.ok:
            raise CommandError(log.message)

    # --- 世代管理 ------------------------------------------------------------
    def _prune(self, keep: int):
        files = sorted(services.ZAIM_DIR.glob('Zaim.*.api.csv'))
        for p in files[:-keep] if keep > 0 else []:
            p.unlink(missing_ok=True)
            self.stdout.write(f'古い API 生成ファイルを削除: {p.name}')

    # --- 手動 CSV との突き合わせ ---------------------------------------------
    def _check(self, rows: list[dict]):
        """月×支払元の件数・金額を最新の手動 CSV と比べ、API 側で欠ける行を可視化する"""
        import pandas as pd
        manual = [p for p in sorted(services.ZAIM_DIR.glob('Zaim*.csv')) if '.api.' not in p.name]
        if not manual:
            self.stdout.write('比較対象の手動 CSV が zaim/ にありません')
            return
        m = pd.read_csv(manual[-1], encoding='cp932')
        a = pd.DataFrame(rows)
        self.stdout.write(f'手動 CSV: {manual[-1].name} {len(m):,}行 ／ API: {len(a):,}行')
        for df in (m, a):
            df['ym'] = df['日付'].astype(str).str[:7]
            df['支払元'] = df['支払元'].fillna('-').astype(str)
        start = max(m['ym'].min(), a['ym'].min())
        m, a = m[m['ym'] >= start], a[a['ym'] >= start]

        def agg(df):
            return df.groupby(['方法', '支払元']).agg(n=('日付', 'size'), yen=('支出', 'sum'), inc=('収入', 'sum'))
        cmp = agg(m).join(agg(a), lsuffix='_csv', rsuffix='_api', how='outer').fillna(0).astype(int)
        cmp['欠け'] = cmp['n_csv'] - cmp['n_api']
        self.stdout.write(f'\n{start} 以降・方法×支払元ごとの件数（csv=手動 / api）:')
        self.stdout.write(cmp.sort_values('欠け', ascending=False).to_string())
        missing = cmp[cmp['欠け'] > 0]
        if missing.empty:
            self.stdout.write('\n✅ API で欠ける行はありません。手動アップロードは不要です')
        else:
            self.stdout.write(f'\n⚠️ 手動 CSV にあって API に無い行: {int(missing["欠け"].sum()):,}件。'
                              '口座連携の自動取込は API で取れないため。該当の支払元は手動アップロードを併用してください')
        # 直近3か月の月別も出す（直近ほど重要）
        by_ym = (m.groupby('ym').size().rename('csv').to_frame()
                 .join(a.groupby('ym').size().rename('api'), how='outer').fillna(0).astype(int))
        self.stdout.write('\n月別件数（直近6か月）:\n' + by_ym.tail(6).to_string())
