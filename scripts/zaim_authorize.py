"""Zaim API のアクセストークンを一度だけ発行する（ローカルで実行・ブラウザで承認）。

    python scripts/zaim_authorize.py

    1. https://dev.zaim.net で「新しいアプリケーションを追加」→ Consumer Key / Secret を控える
       （サービス種別は「クライアント型」、コールバックは不要。権限は 読み込み＋書き込み でよい。
       ⚠️ 書き込み権限があっても出金・口座操作はできない。家計簿の記録の読み書きまで）
    2. このスクリプトを実行し、Consumer Key / Secret を入力
    3. 表示された URL をブラウザで開いて Zaim にログインし「許可」。
       → 画面に認証コード（oauth_verifier）が出ればそれを入力。
       → 代わりに http://127.0.0.1:5000/callback?...&oauth_verifier=XXXX へ飛んで
         「このサイトにアクセスできません」と出たら、アドレス欄の URL を丸ごと貼ればよい
         （oauth_verifier を自動で取り出す）
    4. 最後に表示される JSON を、**サーバーの config.json** に "zaim" キーとして貼る
       （config.json は .gitignore 済み。リポジトリには絶対に入れないこと）

トークンは失効しない限りずっと使える。失効したら同じ手順でやり直す。
依存: requests のみ（署名は card_insight/zaim_api.py の自前実装）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from card_insight.zaim_api import ZaimClient  # noqa: E402


def main():
    print('Zaim API のアクセストークンを発行します（dev.zaim.net で作ったアプリの鍵が必要）')
    ck = input('Consumer Key: ').strip()
    cs = input('Consumer Secret: ').strip()
    if not ck or not cs:
        print('鍵が空です'); return 1
    c = ZaimClient(ck, cs)
    c.request_token()
    url = c.authorize_url()
    # ⚠️ 端末の幅で URL が折り返されると、コピーしたときに改行が混ざってトークンが壊れ、
    # Zaim が「メンテナンス中か端末の設定により…」の画面を出す（実際に踏んだ）。
    # 既定のブラウザで直接開いて、手でコピーさせない
    import webbrowser
    opened = webbrowser.open(url)
    print('\nブラウザを開きました。Zaim にログインして「許可」してください。' if opened
          else '\n次の URL を1行に繋げてブラウザで開き、Zaim にログインして「許可」してください:')
    print('  ' + url + '\n')
    print('許可後、画面に出た認証コードか、飛んだ先の URL（127.0.0.1:5000/callback?...）を丸ごと貼ってください。')
    verifier = input('認証コード または URL（Zaim が出さない場合は空のまま Enter）: ').strip()
    if 'oauth_verifier=' in verifier:
        from urllib.parse import parse_qs, urlparse
        verifier = parse_qs(urlparse(verifier).query).get('oauth_verifier', [''])[0]
    # Zaim は verifier 無しでもアクセストークンを返す（実測。空でも通る）
    c.access_token(verifier)
    me = c.verify()
    print(f'\n認証OK: user_id={me.get("id")} login={me.get("login", "")}')

    conf = {'consumer_key': ck, 'consumer_secret': cs,
            'access_token': c.token, 'access_token_secret': c.token_secret}
    # ⚠️ トークンを画面に出さない（端末に残る／会話に貼られる事故が実際に起きた）。
    # ローカルの config.json に書き、サーバーへは ssh で直接差し込む
    local = Path(__file__).resolve().parent.parent / 'config.json'
    _merge_config(local, conf)
    print(f'ローカルの {local.name} に "zaim" を書きました（値は表示しません）')

    server = input(f'\nサーバーにも入れますか？ [{DEFAULT_SERVER}] （Enter=はい / n=いいえ）: ').strip()
    if server.lower() != 'n':
        host, path = (server or DEFAULT_SERVER).split(':', 1) if ':' in (server or DEFAULT_SERVER) \
            else (server or DEFAULT_SERVER, '/srv/web_analize/config.json')
        _push_server(host, path, conf)
    print('\n次: サーバーで  ./venv/bin/python manage.py fetch_zaim --check  を実行して欠けを確認')
    return 0


DEFAULT_SERVER = 'root@160.251.215.92:/srv/web_analize/config.json'

_REMOTE_MERGE = r'''
import json, sys, shutil
path = sys.argv[1]
conf = json.load(sys.stdin)
try:
    cur = json.load(open(path, encoding='utf-8'))
except FileNotFoundError:
    cur = {}
shutil.copy(path, path + '.bak') if cur else None
cur['zaim'] = conf
json.dump(cur, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=4)
print('server config.json updated:', sorted(conf.keys()))
'''


def _merge_config(path: Path, conf: dict):
    """config.json に "zaim" を差し込む（他のキーは触らない。上書き前に .bak を残す）"""
    import shutil
    cur = {}
    if path.exists():
        cur = json.loads(path.read_text(encoding='utf-8'))
        shutil.copy(path, path.with_suffix('.json.bak'))
    cur['zaim'] = conf
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=4) + '\n', encoding='utf-8')


def _push_server(host: str, remote_path: str, conf: dict):
    """ssh でサーバーの config.json に "zaim" を差し込む。値は標準入力で渡す（引数・画面に出ない）"""
    import base64
    import subprocess
    # ssh は引数を空白で連結して遠隔シェルに渡すので、複数行のコードはそのままでは壊れる。
    # base64 で1トークンにして exec する（値は標準入力で渡す）
    code = base64.b64encode(_REMOTE_MERGE.encode()).decode()
    remote = f"python3 -c \"import base64,sys;exec(base64.b64decode('{code}'))\" '{remote_path}'"
    r = subprocess.run(['ssh', host, remote], input=json.dumps(conf), text=True, capture_output=True)
    if r.returncode == 0:
        print(f'サーバー {host} の {remote_path} に "zaim" を書きました')
    else:
        print(f'サーバーへの書き込みに失敗（{r.returncode}）: {r.stderr.strip()[:300]}')
        print('ローカルの config.json の "zaim" ブロックを、手でサーバーの config.json に貼ってください')


if __name__ == '__main__':
    sys.exit(main())
