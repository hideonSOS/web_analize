# サーバー定期実行（cron）セットアップ手順書

このファイルは **Linux 本番サーバー上で、データ更新バッチを cron に登録する作業者
（人間または AI エージェント）向けの手順書** です。この1ファイルだけを読めば作業が
完結するように書いてあります。会話の文脈は不要です。

- 対象サーバー: **Linux**（VPS / クラウド。常時稼働前提）
- リポジトリ: https://github.com/hideonSOS/web_analize
- 前提: 既に「初回デプロイ手順」（プロジェクト直下 `CLAUDE.md` 参照）が完了し、
  `python manage.py runserver` 等でアプリが起動できる状態になっていること。
- 権限: **root 不要**。アプリを動かしているユーザーの crontab に登録する。
  そのユーザーで SSH ログインして作業する。

> ⚠️ この作業は **crontab（永続設定）を変更**します。作業前に現在の登録内容を
> `crontab -l` で控え、後述の「重複防止」に従って**必要な行だけ追記**すること。
> 既存の行を削除・上書きしないこと。

---

## 登録する2つのジョブ

| ジョブ | スクリプト | 実行時刻(JST) | 内容 |
|---|---|---|---|
| 日本株の夜バッチ | `scripts/daily_update.sh` | 平日 21:10 | J-Quantsの株価・出来高・決算・時価総額、登録米国株の株価/決算 |
| 米国株ランキング | `scripts/us_ranking_update.sh` | 毎日 07:00 | S&P500の時価総額・出来高ランキング（yfinance） |

**なぜ時刻を分けるか（重要）**: 米国株ランキングは yfinance を使う。米国市場は
**16:00 ET にクローズ**し、その確定値は **JST 翌朝6時頃**に揃う。取引時間中
（例: JST 0:00 = US 11:00 ET）に取ると**未確定の途中値**を掴んでしまい、出来高
z-scoreが過小・時価総額が日中値になる。そのため**JST 7:00 に実行**する。
日本株(J-Quants)は夜に確定するので夜21:10のまま。**この2つは同じ時刻にしない。**

---

## 手順

### 0. 最新コードを取得
```bash
cd /path/to/web_analize          # ← 実際の配置パスに置き換える（以降 $ROOT と表記）
git pull
```
`scripts/us_ranking_update.sh` と `japan_kabu/data/sp500.csv`、更新済みの
`scripts/daily_update.sh` が入っていることを確認する:
```bash
ls scripts/us_ranking_update.sh japan_kabu/data/sp500.csv
```

### 1. 静的ファイルを再収集（CSS/JS/テンプレートを変更したため）
`DEBUG=False` の本番では Django が静的ファイルを配信しない。nginx等が配信する
`STATIC_ROOT` を更新する:
```bash
./venv/bin/python manage.py collectstatic --noinput
```
> マイグレーションは**不要**（今回の変更はDBスキーマを変えていない）。

### 2. スクリプトに実行権限を付与
```bash
chmod +x scripts/daily_update.sh scripts/us_ranking_update.sh
```

### 3. 初回だけ手動でデータ投入
米国株ランキングの初期データを作る。`--refresh-shares` で発行済株式数を取得・保存する
（以降の日次実行はこの保存値を再利用するので、初回のこの1回が必要）。約3〜5分:
```bash
./venv/bin/python manage.py update_us_ranking --refresh-shares
```
最後に `完了: 米国株ランキング更新 5xx銘柄 ...` と出れば成功。

### 4. サーバーのタイムゾーンを確認 → cron時刻を決定
**cron はサーバーOSのタイムゾーンで動く**（Djangoの `TIME_ZONE=Asia/Tokyo` とは
別物なので混同しないこと）。まず確認する:
```bash
timedatectl        # "Time zone: Asia/Tokyo (JST, +0900)" のように表示される
# timedatectl が無ければ:
date +%Z%z         # 例: JST+0900 / UTC+0000
```
確認結果に応じて、次の手順で使う**時刻**を決める:

| サーバーTZ | 夜バッチ(JST21:10) | 米国株(JST07:00) |
|---|---|---|
| **Asia/Tokyo (JST)** | `10 21 * * 1-5` | `0 7 * * *` |
| **UTC** | `10 12 * * 1-5` | `0 22 * * *` |
| その他 | JSTから各自換算（JST = UTC+9） | 同左 |

> UTC換算の考え方: JST 21:10 = UTC 12:10、JST 07:00 = UTC 22:00（前日扱い）。

### 5. cron に登録（重複防止しながら追記）
まず現在の登録を確認する:
```bash
crontab -l 2>/dev/null
```
- `daily_update.sh` の行が**既にあれば残す**（重複登録しない）。無ければ追加する。
- `us_ranking_update.sh` の行は通常まだ無いので追加する。

`crontab -e` で開き、**手順4の表で選んだ時刻**の行を末尾に追記する。
以下は **サーバーがJSTの場合**の例（`$ROOT` は実際の絶対パスに置換）:
```cron
# 日本株の夜バッチ（平日21:10 JST）
10 21 * * 1-5 /path/to/web_analize/scripts/daily_update.sh

# 米国株ランキング（毎朝07:00 JST。US確定クローズ後）
0 7 * * * /path/to/web_analize/scripts/us_ranking_update.sh
```
**サーバーがUTCの場合**は時刻だけ差し替える:
```cron
10 12 * * 1-5 /path/to/web_analize/scripts/daily_update.sh
0 22 * * *    /path/to/web_analize/scripts/us_ranking_update.sh
```
> `us_ranking_update.sh` は**日曜だけ自動で `--refresh-shares`**（株式数の取り直し）を
> 付ける曜日判定を内蔵している。よって cron は上記1行でよく、週次の別行は不要。

### 6. 登録内容と cron 常駐を確認
```bash
crontab -l                       # 追記した2行が見えること
systemctl status cron            # Debian/Ubuntu系。activeであること
# RHEL/CentOS系はサービス名が crond:
systemctl status crond
```
`cron`(または`crond`)が `inactive/dead` の場合は有効化する:
```bash
sudo systemctl enable --now cron    # RHEL系は cron を crond に読み替え
```

### 7. 動作確認（登録翌日以降）
実行時刻を過ぎたらログを見る。`----- OK -----` が出ていれば成功:
```bash
tail -n 20 logs/us_ranking_$(date +%Y%m%d).log
tail -n 40 logs/update_$(date +%Y%m%d).log
```
すぐ確認したい場合は、手動でスクリプトを叩いて同じ動作を再現できる:
```bash
./scripts/us_ranking_update.sh && echo "exit=$?"
```

---

## トラブルシュート

- **`/usr/bin/env: 'bash\r': No such file or directory`**
  スクリプトが CRLF 改行になっている。リポジトリは `.gitattributes` で `*.sh eol=lf` を
  強制しているので通常は起きないが、発生したら変換する:
  `sed -i 's/\r$//' scripts/*.sh`
- **`venv が見つかりません`**（スクリプトのFATAL）
  `./venv/bin/python` が無い。デプロイ手順のvenv作成が未完。`CLAUDE.md` の
  「初回デプロイ手順」を先に完了させる。
- **cron では動くがログが空 / データが更新されない**
  cron はPATHが最小限。スクリプトは先頭で `cd "$(dirname "$0")/.."` して相対パスで
  動くため通常は問題ないが、**crontab には必ずスクリプトの絶対パス**を書くこと。
- **米国株の出来高が0や異常に低い / 時価総額が中途半端**
  実行時刻が**US取引時間中**になっている。手順4を見直し、JST 07:00（UTCなら22:00）に
  なっているか確認する。
- **`update_us_ranking` が「対象銘柄がありません」**
  `japan_kabu/data/sp500.csv` が無く、かつ構成銘柄CSVのネット取得にも失敗している。
  ネット接続を確認し、`--refresh-list` を付けて再取得する:
  `./venv/bin/python manage.py update_us_ranking --refresh-list --refresh-shares`

---

## 各コマンドの役割（背景）

- `scripts/daily_update.sh` … 日本株中心の夜バッチ。J-Quants(株価/出来高/決算/時価総額)、
  登録米国株の株価・決算、登録銘柄の日次終値を順に更新する。
- `scripts/us_ranking_update.sh` … 米国株ランキング専用。S&P500構成銘柄(約500)の
  時価総額と出来高異常度を yfinance で更新する。日次は約2.5〜3分。株価・出来高は
  一括ダウンロード、時価総額は保存済み発行済株式数で計算するためネット負荷は小さい。
  日曜のみ発行済株式数を取り直す。
- 詳細な設計・注意点はプロジェクト直下 `CLAUDE.md` の
  「米国株ランキング（時価総額・出来高）」および「データ更新バッチ」節を参照。
