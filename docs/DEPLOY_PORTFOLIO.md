# ポートフォリオ機能のサーバー反映手順（指示書）

コミット `96a8464` 以降で追加された `portfolio` アプリをサーバーへ反映するための手順。
上から順に実行する。所要は数分。

## なぜ「投信の候補が反映されない」のか（今回の事象）

投信のプルダウン候補（オルカン等）は **商品マスタ（DBのデータ）** であり、
git はコードしか運ばない。開発機のDBに登録した商品はサーバーには存在しないため、
サーバーでは候補が空になる。→ 下記 手順3 の `seed_fund_products` で投入する。

同じ理由で、**開発機で登録した保有・現金・入出金・目標比率もサーバーには移らない**。
少量ならサーバーの登録ページで入れ直すのが簡単（下記「補足A」に移送方法もあり）。

## 手順（サーバー側）

```bash
cd /srv/web_analize
git pull

# 1. マイグレーション（portfolio の 0001〜0005 が適用される）
./venv/bin/python manage.py migrate

# 2. 静的ファイル（portfolio.css が増えている）
./venv/bin/python manage.py collectstatic --noinput

# 3. 投信の商品マスタを投入（プルダウン候補。冪等なので何度実行しても安全）
./venv/bin/python manage.py seed_fund_products

# 4. 価格の初回取得（投信の基準価額全履歴・ドル円。1〜2分）
./venv/bin/python manage.py update_product_prices

# 5. アプリ再起動（Pythonコード/テンプレ変更の反映に必須）
sudo systemctl restart web_analize.service
```

反映確認: サイトの ナビ「記録 > ポートフォリオ」→「資産の登録・入出金」を開き、
「投資信託を追加」のプルダウンに オルカン / S&P500 / FANG+ / TOPIX / 日経平均 の
5件が出ていればOK。

## cron について（追加登録は不要）

**2026-09-05 追加: 暗号資産**。`portfolio` のマイグレーション `0010` を適用すること
（`Product.crypto` / `AssetSnapshot.crypto` / 大分類の選択肢）。`update_product_prices` は
暗号資産（BTC/ETH/XRP/SOL）も yfinance「銘柄-USD」×ドル円で円/枚を取得する。追加作業は
migrate と restart だけで、cron は既存のまま（同じコマンドに同梱）。

`update_product_prices`（投信・金銀・暗号資産・ドル円の日次取得）は既存の
`scripts/daily_update.sh` に同梱済み。既に cron 登録されている環境なら
追加作業は無い。米国株の株価はポートフォリオ保有銘柄も
`update_us_prices`（同スクリプト内）が拾うよう対象を拡張済み。

## 補足A: 開発機で登録した保有データを移送したい場合

件数が少なければサーバーの登録ページで入れ直す方が早い。移送するなら:

```bash
# 開発機（Windows）。cp932事故防止に PYTHONUTF8=1 が必須（CLAUDE.md参照）
set PYTHONUTF8=1
python manage.py dumpdata portfolio -o portfolio_data.json
```

サーバーへコピーして:

```bash
./venv/bin/python manage.py loaddata portfolio_data.json
```

⚠️ loaddata は同じPKを上書きする。サーバー側で先に登録を始めていた場合は
手入力のやり直しの方が安全（`seed_fund_products` 済みだと商品のPKが
開発機と食い違うため、混在させないこと）。

## 補足B: 新しい投信を候補に追加する方法（2通り）

1. サーバーの登録ページ下部「投信の商品情報を編集」は既存商品の編集専用なので、
   新規追加は `portfolio/management/commands/seed_fund_products.py` の
   `FUNDS` リストに1行足して deploy → `seed_fund_products` を再実行（冪等）
2. ISIN・協会コードは投信協会「投信総合検索ライブラリー」
   （https://toushin-lib.fwg.ne.jp/FdsWeb/）でファンド名を検索し、
   詳細ページの **URL** から取る（`?isinCd=JP90C...&associFundCd=0331...`）。
   楽天証券のファンド詳細URLの `ID=` はISINだが、協会コードは載っていない
