# サーバー側AIからの報告: HTTPS化 完了

作成: 2026-09-05 サーバー側AI（Claude）
宛先: 開発機側AI / ユーザー
返信元: `docs/SERVER_TASK_HTTPS_PASSKEY.md`

## 結論

**Let's Encrypt の IPアドレス証明書（shortlived プロファイル）で HTTPS 化が完了した。**
`https://160.251.215.92/` が有効な証明書で 200 を返し、`http://` は 301 で HTTPS へ飛ぶ。
開発機側は 5章の Django 設定変更とパスキー実装に進んでよい。

## 1. 報告事項（依頼書 6章への回答）

| # | 項目 | 結果 |
|---|---|---|
| 1 | ACMEクライアント | **lego v5.4.1**（GitHub Releases の linux_amd64 バイナリを `/usr/local/bin/lego` に配置）。apt の lego は 4.9.1 で `--profile` 非対応のため不採用。IP識別子 + `--profile shortlived` に対応 |
| 2 | 証明書取得 | **成功**。staging で先に検証してから本番で取得。発行元 `C=US, O=Let's Encrypt, CN=YE2`、SAN は `IP Address:160.251.215.92`。有効期間 約6.5日（2026-09-04 16:46 UTC 〜 2026-09-11 08:46 UTC） |
| 3 | 自動更新 | systemd timer `lego-renew.timer`（1日2回 03:30 / 15:30 JST、±30分ランダム）。詳細は 3章 |
| 4 | `curl -sI https://160.251.215.92/login/` | `HTTP/2 200`。`openssl s_client` の検証結果 `Verify return code: 0 (ok)` |
| 5 | HTTP → HTTPS | `HTTP/1.1 301 Moved Permanently` / `Location: https://160.251.215.92/login/` |

追加確認:
- HTTPS 経由のログイン POST（CSRF あり）が 403 にならず通ることを確認済み。
  gunicorn がデフォルトで `X-Forwarded-Proto` を `wsgi.url_scheme` に反映するため、
  Django の `request.is_secure()` は **現状の設定でも True** になっている。
  それでも 5章の `SECURE_PROXY_SSL_HEADER` は明示しておくのが安全。
- `/static/website/css/base.css` が HTTPS で 200。
- 確認のため server 自身の IP から意図的にログイン失敗を2回発生させたが、
  `LoginAttempt` の該当行（ip=160.251.215.92）は削除済み。ユーザーのロック回数には影響なし。

## 2. サーバーに加えた変更

| 対象 | 内容 |
|---|---|
| `/usr/local/bin/lego` | 新規（v5.4.1） |
| `/etc/lego/` | ACME アカウント鍵と証明書。`certificates/160.251.215.92.{crt,key,issuer.crt,json}`。`/etc/lego/staging/` は staging 検証用（残置、不要なら削除可） |
| `/var/www/acme/` | HTTP-01 チャレンジの webroot（www-data 所有） |
| `/etc/nginx/sites-available/web_analize` | 80番: `/.well-known/acme-challenge/` のみ静的配信、他は 301。443番: 既存 location 群を移植 + TLS 設定。**作業前のバックアップ** `/root/nginx.bak` と `web_analize.bak.20260905*` |
| `/etc/systemd/system/lego-renew.{service,timer}` | 自動更新 |
| ufw | `443/tcp` を ALLOW（v4/v6） |

`config.json` とリポジトリの追跡ファイルは変更していない。この文書だけ未追跡で置いてある。

## 3. 自動更新の仕組み

lego v5 では `renew` サブコマンドが `run` に統合されている（`run` が「取得または更新」）。

```
ExecStart=/usr/local/bin/lego --log.format text run \
  --path /etc/lego \
  --server https://acme-v02.api.letsencrypt.org/directory \
  --accept-tos --email <ユーザーのメール> \
  --domains 160.251.215.92 \
  --http --http.webroot /var/www/acme \
  --profile shortlived \
  --renew-days 3 \
  --deploy-hook "/usr/bin/systemctl reload nginx"
```

- ARI（RFC 9773）の推奨時期、または残り3日のどちらかで更新。手動実行で
  「Skip renewal: … the renewal can be performed in 3d14h」と判定されるのを確認済み。
- 更新成功時のみ nginx を reload。失敗時は既存証明書のまま（残り3日 × 1日2回なので
  6回以上リトライできる）。
- 動作確認コマンド:

```bash
systemctl list-timers lego-renew.timer
journalctl -u lego-renew.service -n 20
openssl x509 -in /etc/lego/certificates/160.251.215.92.crt -noout -dates
```

## 4. 開発機側への注意点

- 依頼書 5章のとおり `SECURE_PROXY_SSL_HEADER` / `SESSION_COOKIE_SECURE` /
  `CSRF_COOKIE_SECURE` / `CSRF_TRUSTED_ORIGINS=["https://160.251.215.92"]` を入れてよい。
  nginx は 443 側でも `X-Forwarded-Proto $scheme` を渡している。
- パスキー（WebAuthn）の RP ID について: **IPアドレスは RP ID にできない**（WebAuthn 仕様上
  RP ID は有効なドメイン名である必要がある）。ブラウザは `rp.id` を省略した場合
  origin のホスト部（= IP）を使おうとするが、Chrome/Safari は IP アドレス origin での
  `navigator.credentials.create()` を拒否する実装が多い。**実装前に開発機側で
  IP origin でのパスキー登録が通るかを確認すること**。通らない場合はドメイン取得が
  必要になり、その場合の証明書は `--domains <ドメイン>` に変えるだけで同じ仕組みで取れる
  （shortlived ではなく通常プロファイルも選べる）。
- `/media/` が Django 認証を通らない問題（依頼書 1章）は今回も未対応。

## 5. 戻し方（万一のとき）

```bash
cp /root/nginx.bak /etc/nginx/sites-available/web_analize
nginx -t && systemctl reload nginx
systemctl disable --now lego-renew.timer
```
