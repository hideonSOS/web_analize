"""Zaim 公式 API（OAuth 1.0a）から家計簿の記録を取り、エクスポート CSV と同じ形にする。

なぜ API か: Zaim には出金の権限が無いので、自動化してもユーザーの線引き
（「出金する権限が流出するのは困る」）に収まる。サーバーが持つのはアプリの鍵と
ユーザーが一度ブラウザで承認して発行したトークンだけで、Zaim のパスワードは持たない。
自動ログイン＋CSVダウンロード方式は画面変更・二段階認証で壊れるので採らない。

出力は **Zaim のエクスポート CSV と同じ 16 列・cp932** にして `data/spending/zaim/` に
置く。以降の取り込み（zaim_loader → ledger → 辞書 → 突合）は一切変えない。

依存を増やさないため OAuth 1.0a の署名（HMAC-SHA1）は標準ライブラリで自前実装。
requests-oauthlib と同じ署名になることは tests で確認済み。

API の要点（dev.zaim.net の公式ドキュメント・2026-09 時点）:
  - GET /v2/home/money          記録の一覧。limit は最大 100、page で送る
  - GET /v2/home/category       カテゴリ（id → name, mode）
  - GET /v2/home/genre          カテゴリの内訳（id → name, category_id）
  - GET /v2/home/account        口座＝支払元/入金先（id → name）
  - GET /v2/home/user/verify    トークンの確認
  ⚠️ 取れるのは**手入力した記録だけ**（レシート撮影は手入力扱い）。口座連携で自動取込
     された行は返らない、と公式に注記がある。実データでどれだけ欠けるかは
     `manage.py fetch_zaim --check` で最新の手動 CSV と突き合わせて確認する
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import secrets
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import requests

API_BASE = 'https://api.zaim.net/v2'
AUTH_REQUEST_URL = 'https://api.zaim.net/v2/auth/request'
AUTH_ACCESS_URL = 'https://api.zaim.net/v2/auth/access'
AUTHORIZE_URL = 'https://auth.zaim.net/users/auth'
# ⚠️ Zaim は oauth_callback='oob' を受け付けず 401 を返す（実際に踏んだ）。URL の形が必須。
# 承認後はこの URL へ oauth_verifier 付きで飛ぶ（ローカルにサーバーは要らない。アドレス欄か
# 画面のコピーボタンから verifier を取る）。pyzaim も同じ既定値
DEFAULT_CALLBACK = 'http://127.0.0.1:5000/callback'
PAGE_LIMIT = 100            # API の上限
PAGE_SLEEP = 0.3            # ページ間の待ち（レート制限の明記は無いが礼儀として）
RETRY = 3

# Zaim エクスポート CSV の列（zaim_loader.COLUMN_MAP と同じ順）
CSV_COLUMNS = ['日付', '方法', 'カテゴリ', 'カテゴリの内訳', '支払元', '入金先', '品目', 'メモ',
               'お店', '通貨', '収入', '支出', '振替', '残高調整', '通貨変換前の金額', '集計の設定']
INCLUDE = '常に集計に含める'
EXCLUDE = '集計に含めない'


def _pct(s: str) -> str:
    """RFC 3986 のパーセントエンコード（OAuth 1.0a 用。'~' は残す）"""
    return urllib.parse.quote(str(s), safe='-._~')


def _raise_with_body(r: requests.Response, what: str):
    """Zaim は失敗理由を JSON 本文に入れて返す（例: "401 Consumer is not found"）。
    raise_for_status だけだと本文が消えて原因が分からない（実際に困った）"""
    if r.ok:
        return
    raise RuntimeError(f'{what}の取得に失敗: HTTP {r.status_code} {r.text[:300]} '
                       '→ "Consumer is not found" なら Consumer Key/Secret が違う（dev.zaim.net の'
                       'アプリ画面の コンシューマID/シークレット を貼る）')


class ZaimClient:
    """OAuth 1.0a（HMAC-SHA1）で署名して Zaim API を叩く最小クライアント"""

    def __init__(self, consumer_key: str, consumer_secret: str,
                 token: str = '', token_secret: str = '', session: requests.Session | None = None):
        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.token = token
        self.token_secret = token_secret
        self.session = session or requests.Session()

    # --- 署名 --------------------------------------------------------------
    def _oauth_params(self, extra: dict | None = None) -> dict:
        p = {
            'oauth_consumer_key': self.consumer_key,
            'oauth_nonce': secrets.token_hex(16),
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': str(int(time.time())),
            'oauth_version': '1.0',
        }
        if self.token:
            p['oauth_token'] = self.token
        if extra:
            p.update(extra)
        return p

    def sign(self, method: str, url: str, params: dict, oauth: dict) -> str:
        """署名ベース文字列 = METHOD & URL & 正規化パラメータ を HMAC-SHA1"""
        allp = {**params, **oauth}
        norm = '&'.join(f'{_pct(k)}={_pct(v)}' for k, v in sorted((_pct(k), _pct(v)) for k, v in allp.items()))
        base = '&'.join([method.upper(), _pct(url), _pct(norm)])
        key = f'{_pct(self.consumer_secret)}&{_pct(self.token_secret)}'
        digest = hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()
        return base64.b64encode(digest).decode()

    def auth_header(self, method: str, url: str, params: dict | None = None, extra: dict | None = None) -> str:
        params = params or {}
        oauth = self._oauth_params(extra)
        oauth['oauth_signature'] = self.sign(method, url, params, oauth)
        return 'OAuth ' + ', '.join(f'{_pct(k)}="{_pct(v)}"' for k, v in sorted(oauth.items()))

    # --- 認可（初回にローカルで1度だけ） ------------------------------------
    def request_token(self, callback: str = DEFAULT_CALLBACK) -> dict:
        h = self.auth_header('GET', AUTH_REQUEST_URL, extra={'oauth_callback': callback})
        r = self.session.get(AUTH_REQUEST_URL, headers={'Authorization': h}, timeout=30)
        _raise_with_body(r, 'リクエストトークン')
        d = dict(urllib.parse.parse_qsl(r.text))
        self.token, self.token_secret = d['oauth_token'], d['oauth_token_secret']
        return d

    def authorize_url(self) -> str:
        return f'{AUTHORIZE_URL}?oauth_token={_pct(self.token)}'

    def access_token(self, verifier: str) -> dict:
        h = self.auth_header('GET', AUTH_ACCESS_URL, extra={'oauth_verifier': verifier})
        r = self.session.get(AUTH_ACCESS_URL, headers={'Authorization': h}, timeout=30)
        _raise_with_body(r, 'アクセストークン')
        d = dict(urllib.parse.parse_qsl(r.text))
        self.token, self.token_secret = d['oauth_token'], d['oauth_token_secret']
        return d

    # --- 取得 --------------------------------------------------------------
    def get(self, path: str, params: dict | None = None) -> dict:
        url = f'{API_BASE}/{path.lstrip("/")}'
        params = {k: str(v) for k, v in (params or {}).items()}
        last = None
        for attempt in range(RETRY):
            h = self.auth_header('GET', url, params)
            r = self.session.get(url, params=params, headers={'Authorization': h}, timeout=60)
            if r.status_code in (429, 500, 502, 503, 504):
                last = r
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f'Zaim API {path}: HTTP {last.status_code if last else "?"} が続きました')

    def verify(self) -> dict:
        return self.get('home/user/verify').get('me', {})

    def categories(self) -> dict[int, dict]:
        return {int(c['id']): c for c in self.get('home/category').get('categories', [])}

    def genres(self) -> dict[int, dict]:
        return {int(g['id']): g for g in self.get('home/genre').get('genres', [])}

    def accounts(self) -> dict[int, dict]:
        return {int(a['id']): a for a in self.get('home/account').get('accounts', [])}

    def iter_money(self, start: date | str, end: date | str | None = None, mode: str = ''):
        """記録を古い順に全件。limit=100 でページ送り（返りが100件未満で終わり）"""
        params = {'start_date': str(start), 'limit': PAGE_LIMIT, 'order': 'date'}
        if end:
            params['end_date'] = str(end)
        if mode:
            params['mode'] = mode
        page = 1
        while True:
            rows = self.get('home/money', {**params, 'page': page}).get('money', [])
            yield from rows
            if len(rows) < PAGE_LIMIT:
                return
            page += 1
            time.sleep(PAGE_SLEEP)


# --- 記録 → CSV 行 ----------------------------------------------------------
def _name(master: dict, key, default: str = '-') -> str:
    try:
        return master[int(key)]['name'] if key not in (None, '', 0, '0') else default
    except (KeyError, ValueError, TypeError):
        return default


def records_to_rows(records, categories: dict, genres: dict, accounts: dict) -> list[dict]:
    """API の money レコードを Zaim エクスポート CSV と同じ 16 列に変換する。

    合わせ方（実物 CSV の観察・2026-09）:
      - 無いものは "-"（カテゴリ・支払元・入金先）。品目/メモ/お店は空文字
      - 金額は mode に応じて 収入/支出/振替 のどれか1列。残高調整は API では作れない
      - 集計の設定は API に無いので payment/income は「常に集計に含める」、
        transfer は「集計に含めない」に倒す（実物 CSV は 15,200 対 64・11 対 0 でこの分布）
      - active が -1（削除済み）の行は落とす
    """
    out = []
    for r in records:
        if str(r.get('active', 1)) == '-1':
            continue
        mode = r.get('mode', 'payment')
        amount = int(float(r.get('amount') or 0))
        from_acc = _name(accounts, r.get('from_account_id'))
        to_acc = _name(accounts, r.get('to_account_id'))
        row = {
            '日付': str(r.get('date', ''))[:10],
            '方法': mode,
            'カテゴリ': _name(categories, r.get('category_id')) if mode != 'transfer' else '-',
            'カテゴリの内訳': _name(genres, r.get('genre_id')) if mode != 'transfer' else '-',
            '支払元': from_acc if mode in ('payment', 'transfer') else '-',
            '入金先': to_acc if mode in ('income', 'transfer') else '-',
            '品目': r.get('name') or '',
            'メモ': r.get('comment') or '',
            'お店': r.get('place') or '',
            '通貨': r.get('currency_code') or 'JPY',
            '収入': amount if mode == 'income' else 0,
            '支出': amount if mode == 'payment' else 0,
            '振替': amount if mode == 'transfer' else 0,
            '残高調整': 0,
            '通貨変換前の金額': amount,
            '集計の設定': EXCLUDE if mode == 'transfer' else INCLUDE,
            '_id': int(r.get('id') or 0),
        }
        out.append(row)
    out.sort(key=lambda x: (x['日付'], x['_id']))
    return out


def write_csv(rows: list[dict], path: Path) -> Path:
    """Zaim エクスポートと同じ cp932・ヘッダ付きで保存（読む側は zaim_loader.load_zaim）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='cp932', errors='replace', newline='') as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction='ignore', lineterminator='\n')
        w.writeheader()
        for r in rows:
            w.writerow(r)
    tmp.replace(path)
    return path


def api_csv_name(now: datetime | None = None) -> str:
    """`Zaim.<日時>.api.csv`。手動アップロード（`Zaim.<日時>.csv`）と同じ名前順で
    新しい方が採用される（services.latest_zaim は名前順の末尾を取る）"""
    now = now or datetime.now()
    return f'Zaim.{now:%Y%m%d%H%M%S}.api.csv'
