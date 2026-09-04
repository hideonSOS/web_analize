# サーバー側AIへの作業指示: HTTPS化（パスキー導入の前提）

作成: 2026-09-05 開発機側AI（Claude）
宛先: サーバー側AI（160.251.215.92 / KABU ANALYZE）
関連: `docs/SERVER_TASK_CRON_EXECBIT.md`（前回の往復ルール）

## 0. 背景と目的（なぜやるか）

ユーザーの要望は「**PCのログイン画面のQRをスマホで読んで、Face IDでタップするだけで
ログインしたい**」。これは **パスキー（WebAuthn）** の機能。

**パスキーは HTTPS でしか動作しない**（ブラウザ仕様。localhost のみ例外）。
現在このサイトは `http://160.251.215.92/` の平文なので、**HTTPS化が前提条件**になる。

TOTP（6桁コード）は一度実装したが、ユーザーが「毎回スマホで数字を見て打つのが面倒」
として不採用。サーバー上の TotpDevice は削除済み。**現在はメール＋パスワードのみ**で
ログインできる状態（作業中もユーザーはログインできる）。

### 依頼したい作業は HTTPS化だけ

パスキーのアプリ側実装（WebAuthn の登録・認証フロー、Django のモデルとビュー）は
**開発機側で実装して push する**。サーバー側AIには **nginx と証明書の作業** をお願いしたい。

## 1. 現状（開発機側で確認済み）

| 項目 | 値 |
|---|---|
| URL | `http://160.251.215.92/`（**ドメイン名なし**） |
| Web | nginx（Ubuntu） + gunicorn/systemd `web_analize.service` |
| プロジェクト | `/srv/web_analize`（venv 同梱） |
| 認証 | Django 標準のユーザー認証。`website.middleware.SitePasswordMiddleware` が全ページを保護 |
| Django設定 | `config.json` で制御（`debug:false` / `allowed_hosts:["160.251.215.92"]`） |
| 既知の穴 | `/media/` を nginx が直接配信しており **Django の認証を通らない**（実測。要対処だが今回のスコープ外） |

## 2. 方針: ドメインを取らず、IPアドレスの証明書で HTTPS 化する

Let's Encrypt は **IPアドレス宛の証明書**を発行できる（短命証明書プロファイル）。
開発機から ACME ディレクトリを確認したところ、`profiles` に **`shortlived`** が
存在することを確認済み:

```
profiles: {'classic': ..., 'shortlived': ..., 'tlsserver': ...}
```

### 実施方針

1. **まず可否を判定してほしい。** IP証明書は有効期間が短い（6日程度）ため
   **自動更新が必須**。certbot は対応が追いついていない可能性があるので、
   `lego` または `acme.sh` を使う想定。
2. **可能なら実施**。ACME の HTTP-01 チャレンジを通すため、nginx で
   `/.well-known/acme-challenge/` を Django に渡さず静的配信する必要がある
   （後述の 4 章）。
3. **不可能だった場合は実施せず報告のみ**。代替案（ドメイン取得、
   自己署名＋警告許容、あるいは HTTPS を諦めてパスキーも見送り）は
   ユーザーが判断する。**勝手に自己署名証明書を入れないこと**（ブラウザの
   警告が出る状態はユーザーの体験を損なう）。

## 3. 作業手順（案。環境に合わせて調整可）

```bash
# 1) ACMEクライアントの導入（例: lego）
#    snap や apt で入るなら任意の方法でよい

# 2) HTTP-01 チャレンジ用のディレクトリを用意
mkdir -p /var/www/acme/.well-known/acme-challenge
chown -R www-data:www-data /var/www/acme

# 3) nginx の 80番に challenge の location を追加（4章参照）してリロード
nginx -t && systemctl reload nginx

# 4) IPアドレスで証明書を取得（shortlived プロファイル指定が必要）
#    ※ lego のバージョンによりオプション名が異なる。--help で確認すること
#    例: lego --server https://acme-v02.api.letsencrypt.org/directory \
#             --profile shortlived --http --http.webroot /var/www/acme \
#             --domains 160.251.215.92 --email <ユーザーのメール> run

# 5) 443 の server ブロックを追加して証明書を指す（4章参照）

# 6) 自動更新（6日証明書なので必須。1日1回など高頻度で回す）
#    systemd timer か cron に renew を登録し、成功時に nginx reload
```

## 4. nginx 設定で必ず守ること

```nginx
# 80番: ACME チャレンジだけは静的配信。それ以外は 443 へリダイレクト
server {
    listen 80;
    server_name 160.251.215.92;

    # ⚠️ これが Django に吸われると証明書を取得できない。location / より前に置く
    location /.well-known/acme-challenge/ {
        root /var/www/acme;
    }

    location / { return 301 https://$host$request_uri; }
}

server {
    listen 443 ssl;
    server_name 160.251.215.92;

    ssl_certificate     /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    # 既存の 80番 server ブロックの location 群（/static/ /media/ / への
    # proxy_pass）を**そのまま移植**する。設定を書き直さないこと
    # ⚠️ /static/ の alias は STATIC_ROOT(=/srv/web_analize/static/) に一致させる

    location / {
        proxy_pass http://127.0.0.1:<既存のポート>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        # ⚠️ これが無いと Django が「HTTPなのにSecure Cookieを送る」判定を誤る
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**X-Forwarded-Proto は必須**。Django 側で `SECURE_PROXY_SSL_HEADER` を有効にする予定で、
これが無いとリダイレクトループになる。

## 5. 開発機側で対応すること（サーバー側AIは触らなくてよい）

HTTPS が通ったら、開発機側で以下を実装して push する。**先に settings を変えると
HTTPSが未完成の状態でアクセス不能になる**ので、サーバー側の完了報告を待って行う。

- `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`
- `SESSION_COOKIE_SECURE = True` / `CSRF_COOKIE_SECURE = True`
- `CSRF_TRUSTED_ORIGINS` に `https://160.251.215.92` を追加
- パスキー（WebAuthn）の実装一式

## 6. 報告してほしいこと

1. `lego`（または acme.sh）の**バージョンと IP証明書対応の可否**
2. 証明書の取得に成功したか。失敗ならエラー全文
3. 自動更新をどう仕込んだか（timer/cron の内容と実行頻度）
4. `curl -sI https://160.251.215.92/login/` の結果（200 が返るか）
5. **HTTP でアクセスしたとき 301 で HTTPS に飛ぶか**

## 7. 禁止事項・注意

- **このリポジトリへ push しない**（認証情報が無い環境の想定）。恒久修正が必要なら
  `docs/` に引き継ぎ書を書き、ユーザー経由で開発機側AIに依頼する
- **自己署名証明書を勝手に入れない**（ブラウザ警告が出る状態にしない）
- **`config.json` を書き換えない**（Django設定の変更は開発機側の担当）
- **作業中もユーザーがログインできる状態を保つ**。80番を落としきる前に
  443 が動作することを確認する
- 失敗したら **80番だけの元の設定へ戻す**（作業前に
  `cp /etc/nginx/sites-available/<設定> /root/nginx.bak` を取ること）
