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
    print('\n次の URL をブラウザで開き、Zaim にログインして「許可」してください:\n')
    print('  ' + c.authorize_url() + '\n')
    print('許可後、画面に出た認証コードか、飛んだ先の URL（127.0.0.1:5000/callback?...）を丸ごと貼ってください。')
    verifier = input('認証コード または URL: ').strip()
    if 'oauth_verifier=' in verifier:
        from urllib.parse import parse_qs, urlparse
        verifier = parse_qs(urlparse(verifier).query).get('oauth_verifier', [''])[0]
    if not verifier:
        print('認証コードが空です'); return 1
    c.access_token(verifier)
    me = c.verify()
    print(f'\n認証OK: user_id={me.get("id")} login={me.get("login", "")}\n')
    print('config.json に以下を追加してください（サーバー側。gitに入れないこと）:\n')
    print(json.dumps({'zaim': {
        'consumer_key': ck, 'consumer_secret': cs,
        'access_token': c.token, 'access_token_secret': c.token_secret,
    }}, ensure_ascii=False, indent=4))
    return 0


if __name__ == '__main__':
    sys.exit(main())
