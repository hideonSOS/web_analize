# web_kabuanalize 申し送り（デプロイ・保守用）

日本株の分析ツールサイト。
ローカル開発環境: `C:\Users\matsuyama\Desktop\study\web_kabuanalize`（Windows / venv同梱）

## 📌 開発環境のバージョン一覧（2026-07-21 実測値）

サーバー構築時はこの表に合わせること。**推測で入れないこと。**

| 対象 | 開発環境の実測値 | サーバー側の指針 |
|---|---|---|
| Python | **3.10.1** | 3.10以上（Django 5.2は3.10〜3.13対応）。3.9以下は不可 |
| Django | **5.2.16** | requirements.txt でピン留め済み。上げない |
| PostgreSQL | **17.10** | **17系**を推奨。16以下に落とすとpg_dumpの復元で失敗しうる |
| psycopg | **3.3.4**（`psycopg[binary]`） | psycopg2 ではなく **psycopg3**。ENGINEは同じ`django.db.backends.postgresql` |
| Pillow | **12.3.0** | 銘柄カルテの顔写真(ImageField)に必須 |
| yfinance | **1.5.1** | 米国株の株価取得のみで使用 |

- 依存は `pip install -r requirements.txt` で再現する。
- **DBのcollationに注意**（下の「Windows→Linux移行の落とし穴」を必ず読むこと）。
- 調査で一時導入した `simfin` は未使用のため削除済み（米国株ファンダは保留中）。

## ⚠️ 最重要: データベース回り（エラーになりやすい）

### PostgreSQL が必須
- 開発機には **PostgreSQL 17.10** をインストール済み（winget / localhost:5432 / DB名 `web_kabuanalize`）。
  **サーバーにも PostgreSQL のインストールと DB 作成が必要**:
  ```sql
  CREATE DATABASE web_kabuanalize ENCODING 'UTF8' TEMPLATE template0;
  ```

### ⚠️ Windows→Linux 移行の落とし穴（collation不一致）
開発機のDBは Windows 固有のロケールで作られている:

```
encoding = UTF8 / collate = Japanese_Japan.932 / ctype = Japanese_Japan.932
```

`Japanese_Japan.932` は **Linux には存在しない**。そのため:

- Linuxで同じ collation を指定して `CREATE DATABASE` すると `invalid locale name` で失敗する
- 開発機の `pg_dump` をそのままLinuxへ `pg_restore` すると collation 不一致でエラーになりうる

**対処**: Linux側では以下で作成する（encodingはUTF8で揃える）。
```sql
CREATE DATABASE web_kabuanalize ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;
```
影響は日本語文字列の並び順のみで、**機能上の不具合はない**
（ランキングは数値ソート。日本語ソートは銘柄カルテのセクター絞り込みボタンの並び程度で、
表示順がわずかに変わるだけ）。

**推奨**: ダンプを移送するより、サーバーで空DBを作って
`migrate` → 各種バッチ（下記）でデータを再構築する方が事故が少ない。
- 接続情報は `config.json`（後述）の `database` キーで渡す。
- `config.json` に `database` キーが無い場合の挙動（settings.py の DATABASES 分岐）:
  - **DEBUG=True（開発）** → SQLite にフォールバック
  - **DEBUG=False（本番）** → `ImproperlyConfigured` で**起動を停止する**
    （本番で黙って空のSQLiteが作られ「サイトは動くがデータが無い」という
    気付きにくい不整合になるのを防ぐための意図的な設計。検証済み）
- 「PostgreSQLに入れたはずのデータが見えない」場合は、まず config.json の
  database キー欠落を疑うこと。
- Python ドライバは `psycopg[binary]`（requirements.txt に含まれる）。

### ⚠️ migrations/ を .gitignore に入れないこと
migrations を除外すると、サーバーで `migrate` してもテーブルが作られず
「テーブル/カラムが無い」という DB不整合エラーになる。`.gitignore` にも警告を明記済み。
現在のマイグレーションは全23ファイル（japan_kabu 7・diary 3・karte 13）。
デプロイ後は `python manage.py migrate` を必ず実行する。

### 開発環境のデータ規模（2026-07-21 時点・移行量の目安）

| テーブル | 行数 | 備考 |
|---|---|---|
| japan_kabu_dailyvolume | 125,321 | 日次出来高。70日でローリング削除 |
| japan_kabu_dailyprice | 10,473 | 日次終値3年分。**登録銘柄のみ**（14銘柄）。ドローダウン算出用 |
| japan_kabu_financialreport | 70,022 | 決算(FY+四半期)。5年バックフィル済み |
| japan_kabu_stock | 16,199 | 日本株3,715 + 米国株12,484 |
| karte_* / diary_* | 数件〜十数件 | 手入力データ。**再構築できないので必ず移送する** |

japan_kabu_* はバッチで再生成できるが、**銘柄カルテ(karte_*)と売買日記(diary_*)は
手入力の資産なので失うと戻せない**。移行時はこの2アプリを優先して退避すること。
`/media/` 配下の顔写真も同様（gitに含まれない）。

## 🔐 サイト全体の合言葉認証

`website/middleware.py` の `SitePasswordMiddleware` が、**全ページを合言葉で保護**する。
未認証だと `/login/` に飛ばされ、正しい合言葉を入れると元のページへ戻る。

- 合言葉は **`config.json` の `site_password`** に置く（**コードに書かないこと**。
  リポジトリは公開されているため、コードに書くとGitHubで誰でも読める）
- `site_password` が空または未設定なら認証は無効（開発時の利便性のため）
- セッションは30日保持。アクセスのたびに期限を延長する
- 5回失敗で60秒ロック（総当たり対策）
- `/static/` のみ認証免除（ログイン画面のCSS表示に必要なため）

### ⚠️ 限界と注意
- **HTTPSでないと合言葉が平文で流れる**。公開運用ではSSLを必ず併用すること
- 合言葉が単純だと総当たりで破られる。**推測されにくい文字列を設定すること**
- `/media/`（カルテの経営陣写真）は、本番でnginxが直接配信する場合
  **Djangoの認証を通らない**。保護が必要なら nginx 側でも制限をかけること

### config.json（gitに含まれない・手動設置が必要）
`.gitignore` で除外しているため、**サーバーには手動で設置する**。プロジェクト直下に:
```json
{
    "api_key": "<J-Quants APIキー（無料プラン）>",
    "edinet_api_key": "<EDINET APIキー>",
    "site_password": "<サイト閲覧用の合言葉>",
    "_本番のみ": "以下3つは本番の config.json だけに入れる（開発は未指定＝開発既定）",
    "secret_key": "<本番用に新規生成した鍵>",
    "debug": false,
    "allowed_hosts": ["ドメイン名", "サーバーIP"],
    "database": {
        "NAME": "web_kabuanalize",
        "USER": "postgres",
        "PASSWORD": "<パスワード>",
        "HOST": "localhost",
        "PORT": "5432"
    }
}
```
キーの値は開発機の `config.json` からコピーする。

### データ移行の落とし穴（実際に踏んだ）
- Windows の `manage.py dumpdata -o file.json` は **cp932 で書き出す**。
  loaddata は UTF-8 前提なので `UnicodeDecodeError` になる。
  → dumpdata 前に環境変数 `PYTHONUTF8=1` を設定するか、cp932→UTF-8 変換を挟む。
- 移行するより、サーバーで初回バッチ（下記）を回してデータを再構築する方が簡単な場合もある。
  ただし決算5年分のバックフィルは API 約1,300コール・1時間程度かかる。

## 🔄 データ更新バッチ（cron登録は必須。忘れるとデータが凍結する）

### なぜ必須か
株価・出来高・決算はすべて **PostgreSQL に保存された静的データ** で、
画面表示時にAPIを叩くことはしない（表示を高速にするための設計）。
そのため **バッチを回さない限りデータは永久に古いまま** になる。

実際にローカル開発機では登録を忘れており、5日間データが止まっていた。
**サーバーでは必ず下記の cron を登録すること。**

### cron 登録

> 📄 **サーバーでの cron セットアップ作業手順は `scripts/SETUP_CRON.md` に単体で
> 完結する手順書としてまとめてある**（作業者/AI向け）。以下は要約。

更新用スクリプト `scripts/daily_update.sh` をリポジトリに同梱済み。
複数コマンドを正しい順番で実行し、ログ出力と30日でのログ削除まで行う。

```bash
chmod +x scripts/daily_update.sh
crontab -e
```
```cron
# 日本株の夜バッチ: 平日21:10（サーバーのタイムゾーンがJSTの場合）
10 21 * * 1-5 /path/to/web_analize/scripts/daily_update.sh
# 米国株ランキング: 毎朝7:00（US 16:00 ET クローズ確定後。詳細は下記）
0 7 * * * /path/to/web_analize/scripts/us_ranking_update.sh
```

### ⚠️ 米国株ランキングは JST 朝に別建てで実行する（時間帯が重要）
`update_us_ranking` は **`scripts/us_ranking_update.sh` で JST 朝7:00 に実行**する
（日本株の夜バッチ `daily_update.sh` には**入れない**。現に外してある）。理由:
- yfinanceは**場中に取ると当日の未確定バー（途中の株価・出来高）**を返す。
  JST 0:00 = US 11:00 ET は**取引時間中**なので、回すと出来高z-scoreが過小になり
  時価総額も日中値で上書きされる。**0時ちょうどでの実行は不可**。
- US クローズは 16:00 ET = JST 翌朝6:00頃に確定。**JST 7:00 なら当日の確定
  クローズ**を取得できる。
- `us_ranking_update.sh` は**日曜だけ自動で `--refresh-shares`**（発行済株式数の
  取り直し）を付ける。曜日判定を内蔵しているので cron は1行でよい。

**⚠️ cron はOSのタイムゾーンで動く**（DjangoのTIME_ZONE=Asia/Tokyoとは別物）。
`timedatectl`（または `date +%Z%z`）でサーバーTZを確認してから時刻を決める:
- サーバーが **JST** → `0 7 * * *`（そのまま朝7時）
- サーバーが **UTC** → `0 22 * * *`（JST7:00 = UTC22:00・前日）

**⚠️ サーバーが UTC の場合は時刻を変換すること**（JST 21:10 = UTC 12:10）:
```cron
10 12 * * 1-5 /path/to/web_analize/scripts/daily_update.sh
```
`timedatectl` でサーバーのタイムゾーンを確認してから決めること。
（DjangoのTIME_ZONEは Asia/Tokyo だが、cronはOSのタイムゾーンで動く。ここは別物）

### 実行時刻の考え方
- J-Quants の当日データは **夜に確定**するため、21時台に実行する。
- 土日祝に動いても害はない。各コマンドは「直近の営業日」を見るだけなので、
  新しいデータが無ければ何も変わらない。**祝日カレンダーは持たせていない。**
- 米国株(`update_us_prices`)は前営業日の終値を取る。売買日記に米国株が
  無ければ即座に終了する。

### スクリプトが実行する内容（順番に意味がある）
```bash
python manage.py update_jp_ranking  # 日本株ランキング（時価総額・出来高／yfinance）★J-Quants代替
python manage.py update_us_prices   # 売買日記で使われた米国株の株価（yfinance）
python manage.py update_us_financials # 登録した米国株の決算（yfinance）
python manage.py update_us_ranking  # 米国株ランキング（S&P500の時価総額・出来高／yfinance）
python manage.py update_daily_prices # 登録銘柄の日次終値（ドローダウン算出用・差分のみ）
```

### ⚠️ J-Quants は無料プランのみ（2026-08〜）→ 日本株はハイブリッド構成
課金プランを解約したため、**当日の日本株株価は J-Quants では取れない**（無料プランは
直近データが遅延し `/equities/bars/daily` の直近は 400/403）。そこで役割を分けた:
- **株価・時価総額・出来高（ランキング）＝ yfinance**（`update_jp_ranking`・東証は
  `<コード>.T`）。US版のミラー。時価総額は既存の `Stock.shares`（J-Quants由来・DBに
  残存）を再利用して「終値×株式数」。週1で `--refresh-shares`。
- **銘柄マスタ＋決算（銘柄別指標用）＝ J-Quants無料**（`update_marketcap`）。無料プランは
  直近が**約12週遅延**なので、取れない直近日は**スキップして継続**する（落とさない）。
  ＝指標ページの財務は「最新四半期が約1Q遅れ」になるが、履歴は揃う。
- `update_volume` は廃止（出来高は `update_jp_ranking` が算出）。無料プランのキーは
  `config.json` の `api_key`。**キーは公開リポジトリに書かないこと**。
- **まだ未移行で凍結**: JP銘柄のドローダウン用日次終値（`update_daily_prices` のJP分は
  無料プランの遅延で直近が取れない）。必要になれば yfinance 化する。
1つが失敗しても後続は実行する設計（yfinance障害で日本株の更新まで止めないため）。
失敗時はスクリプトの終了コードが 1 になる。

### 初回のみ実行するもの（cronには入れない）
```bash
python manage.py update_marketcap --backfill-years 5   # 決算5年分・約1時間
python manage.py import_us_master                      # 米国株ティッカー12,484件・数分
python manage.py update_daily_prices --years 3         # 日次終値3年分・数十秒
```

## 株価レンジ（買い場の目安）— カルテの `price` セクション

カルテ/日記に登録した銘柄だけ、**日次終値3年分**を `japan_kabu_dailyprice` に保存し、
「高値からの下落率（ドローダウン）」と「レンジ内位置」を算出して表示する。
算出は `japan_kabu/prices.py`（`price_stats` / `bulk_price_stats`）。

### ⚠️ 必ず調整後終値を使うこと
未調整のまま1年をまたぐと、**株式分割時に株価が飛び、高値が実態の数倍になる**
（NVDAは10:1分割済み。調整後の3年安値は約40ドル、未調整だと約400ドルになる）。
- JP: J-Quants の `AdjC`（無ければ `C` で代替）
- US: yfinance `auto_adjust=True`

`update_marketcap` は時価総額算出のため**調整前**終値を使っている。**流用しないこと。**

### 設計上の決めごと（変更前に読むこと）
- **終値ベースで保持する**。日中の高値/安値（ヒゲ）は1日の瞬間値で高安が決まり不安定なため使わない
- **判断の主役はドローダウン。レンジ内位置ではない**。上昇トレンド銘柄では3年安値が
  遠い過去になり、レンジ内位置は万年80〜90%を指し続けて判断材料にならない
  （実測: NVDA の3年レンジ内位置86%に対し、ドローダウンは-12.0%）
- **1年と3年を必ず併記する**。高値更新中の銘柄では両者は一致し、乖離するのは
  「数年前に天井を打って未回復」の銘柄だけ。この差自体が情報になる
  （実測: MSTR は1年-76.1% / 3年-78.5%、1年高値426 < 3年高値474）
- ここでの安値は期間中の最安値であり、**サポートライン（何度も反発した価格帯）ではない**。
  本来の支持帯が欲しい場合は価格帯別の滞在日数ヒストグラムが必要（未実装）

### 差分同期
初回のみ `--years 3` で遡り、以降は保存済みの最終日の翌日から取得する。
毎回3年分を取り直さないこと（API負荷の無駄）。`--full` で全期間の取り直し、
`--code 6758` で1銘柄だけ試せる。

### 動作確認とトラブル時
```bash
./scripts/daily_update.sh          # 手動実行して確認
tail -50 logs/update_$(date +%Y%m%d).log
```
データが更新されているかは以下で確認できる:
```bash
./venv/bin/python manage.py shell -c "from japan_kabu.models import Stock; from django.db.models import Max; print(Stock.objects.aggregate(m=Max('price_date')))"
```
**バッチが止まると**: ランキングの株価が古くなる／売買日記の「その後±%」「概算損益」が
更新されない／出来高z-scoreが過去のまま。画面にエラーは出ないので気付きにくい。

## 米国株の指標対応（登録した銘柄のみ）

**「カルテを作った銘柄」か「売買日記に登場した銘柄」の米国株だけ**、日本株と同じ
6指標（PER/PBR/ROE/ROA/配当利回り/自己資本比率）と四半期推移を表示できる。
全12,484銘柄は取得しない（yfinanceは1銘柄1コールのため）。

```bash
python manage.py update_us_financials             # 登録済み米国株の決算を取得
python manage.py update_us_financials --ticker MSTR   # 1銘柄だけ試す
```

### ⚠️ 日本株との構造的な違い（実装時の注意）

| | 日本株(J-Quants) | 米国株(yfinance) |
|---|---|---|
| 四半期の数値 | **期初からの累計** | **その四半期単独** |
| TTM純利益 | 直前FY + 当期累計 − 前年同期累計 | 直近4四半期の単純合計 |
| `per_type` | `FY` / `1Q` `2Q` `3Q` | `FY` / `Q` |
| PER | 来期**予想**EPSベース | **実績TTM**ベース（予想が無いため） |
| 四半期履歴 | **20期(5年)** | **5〜6期のみ** |
| 通貨・単位 | 円 / 億円 | ドル / 百万ドル |

TTMの計算は `japan_kabu/views.py` の `_ttm_np`（日本株）と `_ttm_np_us`（米国株）で
分岐している。**混同すると利益が4倍や1/4になるので注意。**

## 米国株ランキング（時価総額・出来高）— 国別タブ

時価総額ランキング(`/japan_kabu/`)と出来高急増ランキング(`/japan_kabu/volume/`)は
**`?country=JP|US` の国別タブ**で日本株/米国株を切り替える（既定JP）。
**ページは増やさず**、既存2ページにタブを足す方針（`日本株 | 米国株`）。

### 対象は S&P500 構成銘柄のみ（約500件）
全米国株12,484件は yfinance が1銘柄1コールのため日次取得が非現実的。そこで
ランキング対象を **S&P500級の約500銘柄に限定**した（ユーザー合意済み）。

```bash
python manage.py update_us_ranking                # 日次（約2.5〜3分。daily_update.sh に同梱済み）
python manage.py update_us_ranking --refresh-shares  # 週1想定。発行済株式数を取り直す
python manage.py update_us_ranking --tickers AAPL,MSFT,NVDA   # 少数で動作確認
python manage.py update_us_ranking --limit 20     # 先頭N銘柄だけ
python manage.py update_us_ranking --no-marketcap # 出来高だけ更新
python manage.py update_us_ranking --refresh-list # 構成銘柄CSVを再取得
```

### ⚠️ 実行コストの考え方（サーバーの日次cron向け・実測値）
- **日次は約2.5〜3分**。律速は `yf.download`（約500銘柄・40日分）の一括取得で
  **約150秒**。yfinance無料エンドポイントのスループット上限で、これ以上は縮まない
  （チャンク分割しても時間は同じ。既存の update_marketcap 約10分より軽いので、
  夜間バッチとして現実的）。
- **時価総額は fast_info を毎日は叩かない**。発行済株式数(`Stock.shares`)を保存して
  再利用し、時価総額 = 最新終値 × 保存株式数 を**ネット呼び出しゼロ**で計算する。
  そのため日次のネット呼び出しは実質「一括ダウンロード1系統」だけ。
- 発行済株式数は変動が遅いので **週1で `--refresh-shares`**（約500コールを並列
  取得・数分）。初回や銘柄入れ替え直後は自動で未保存分だけ取りに行く。

### 実装メモ（`japan_kabu/management/commands/update_us_ranking.py`）
- 構成銘柄は DataHub の公開CSV（プレーンテキスト・**依存追加なし**）から取得し、
  成功時に `japan_kabu/data/sp500.csv` へ保存する。次回ネット断でも同梱CSVで
  フォールバックする（外部依存でバッチを止めない設計）。**このCSVはコミットする**。
- 出来高異常度は `update_volume` と同じ対数z-score。米国株は `yf.download` の
  一括取得で完結するため **DailyVolume には保存せず** in-memory で計算して Stock に
  直接書く。薄商い除外の閾値だけドル建て（`MIN_TURNOVER_USD = 1e6`）。
- **500銘柄を1回で download すると一部が "possibly delisted" で取りこぼれる**
  （McDonald's 等の実在銘柄でも起きる一時的な癖）。`DOWNLOAD_CHUNK = 100` 件ずつに
  分けて投げることで取得漏れを減らしている（所要時間は一括とほぼ同じ）。
- **時価総額($)は `Stock.market_cap` にドルのまま格納**する（列コメントは「円」だが、
  表示・集計は必ず `country` で絞るので混ざらない）。JS(`marketcap.js`)は
  `chart_data.currency` を見て `$X.XXT`/`兆円` を切り替える。
- S&P500銘柄は `update_us_ranking` 自身が `Stock` へ upsert する。`import_us_master`
  が英数字のみで除外していた**ドット付きティッカー(BRK.B等)もここで追加**される
  （yfinance呼び出し時のみ `.`→`-` に変換）。**マイグレーション不要**（既存列を使う）。

## 米国株マスタ（売買日記の銘柄選択用）

- 売買日記は米国株も記録できる。銘柄マスタは `import_us_master` が NASDAQ Trader の
  公開シンボルディレクトリ（`nasdaqlisted.txt` / `otherlisted.txt`）から取り込む。**外部HTTP接続が必要**。
- 米国株の株価は `update_us_prices` が **yfinance** で取得。全銘柄ではなく「日記に登場した
  ティッカーだけ」なので数十コールで済む。**yfinance と外部HTTP接続が必要**（requirements.txt に含む）。
- 米国株は `japan_kabu.Stock` に `country='US'` / `code="US-<ティッカー>"` で保存。JP専用の
  ランキング・指標ページは `market_cap` 等の isnull フィルタで US を自然に除外している。
- 銘柄マスタ（JP+US 約1.7MB / 16,199件）は `/karte/stock-options.json` と
  `/diary/stock-options.json` で配信する（ページ埋め込みだと毎回1.7MBになるため分離した）。
  本番では gzip 必須。

### ⚠️ 銘柄マスタ配信のキャッシュ（事故が起きた箇所）
当初は `Cache-Control: public, max-age=3600` にしていたが、**サーバーで
`import_us_master` を後から実行した際、ブラウザに古いJSON（日本株のみ3,715件）が
1時間貼り付き、NVDA等を検索してもヒットしない**事故が発生した。

`/karte/stock-options.json` は **ETag + 条件付きGET** に変更済み（`karte/views.py`）:
- ETag は「銘柄件数 + `updated_at` の最大値」から生成する
- `Cache-Control: no-cache` で毎回サーバに再確認させる。
  **`no-cache` は「キャッシュ禁止」ではなく「毎回再検証」の意味**
- マスタが変わらなければ **304 / 0バイト** を返し、1.7MBの本文は転送しない

**⚠️ `/diary/stock-options.json` は未対応で、同じバグが残っている**
（`diary/views.py` に `max-age=3600` のまま）。売買日記の銘柄選択で
同じ事故が起きうるので、対応する場合は karte 側と同じ実装にすること。

補足: バッチの `bulk_update` は更新対象フィールドを明示指定しているため
`updated_at` は動かない。ただし銘柄の増減は件数に出るため、上記の事故ケースは
ETagで確実に検知できる。

## J-Quants API の知見（重要）

- **V2 のみ有効**。V1 (`/v1/...`) は 410 を返す。base URL: `https://api.jquants.com/v2`
- 認証は `x-api-key` ヘッダー（`settings.JQUANTS_API_KEY`）。
- **レートリミットあり**: 連続60コール程度で 429。`japan_kabu/jquants.py` にリトライ実装済み。
- レスポンスのカラム名は短縮形（C=終値, Vo=出来高, ShOutFY=発行済株式数, CoName=社名 等）。
- **決算数値は文字列で返る**（"12479620000000"）。数値変換必須（views/コマンド内の `_num()` 参照）。
- 決算サマリーには業績予想修正が混ざる。**DocType に FinancialStatements を含む行のみ**保存
  すること（update_marketcap の `_store_report` 参照）。

## アプリ構成

| アプリ | 内容 |
|---|---|
| `website` | 共通レイアウト（base.html）・トップページ。ナビの機能割当は `website/views.py` の FEATURES |
| `japan_kabu` | 時価総額ランキング(`/japan_kabu/`)・出来高急増(`/japan_kabu/volume/`)・銘柄別指標(`/japan_kabu/stock/<code>/`)。ランキング2ページは`?country=JP/US`の国別タブで日米切替 |
| `diary` | 売買日記(`/diary/`)。判断記録は編集不可・振り返りのみ追記の設計 |
| `karte` | 銘柄カルテ(`/karte/`)。IR資料を読みながら手入力する定性分析＋株価レンジ |

### karte（銘柄カルテ）の構成
- 自由記述4項目（経営陣の考課／事業理解／投資判断／競争環境）。
  **項目は細分化せず1項目=1テキストエリア**（過去に細分化して書く気が失せた経緯あり。戻さないこと）
- 付随データ: 経営陣の顔写真・参照動画(YouTube)・スクリーンショット・中期経営計画・独自KPI
- **セクションの表示順は銘柄ごとに保存**（`StockKarte.section_order`）。
  ドラッグハンドル(⠿)で並び替え、ドロップ時に `karte:reorder` へ自動保存する。
  順序の解決は `resolve_section_order()` が行い、**未知キーは除去・欠落キーは既定順で補完**する。
  そのためセクションを追加しても既存カルテを手直しする必要はない
- 一覧(`/karte/`)には**押し目一覧**（1年高値からの下落率順）を表示。
  母集団は**カルテ＋売買日記**の銘柄。カルテ未作成の銘柄は詳細ページが無いためリンクしない

- 銘柄別指標ページは**全銘柄データ（約5MB）をページに一括埋め込み**、銘柄切替はフロント完結。
  これはユーザーの明示要件（切替時にバックエンド通信ゼロ）。**API化・都度取得に変えないこと**。
  本番では gzip 圧縮（nginx等）を有効にする。
- グラフは Chart.js（CDN読み込み）。

## 🚀 初回デプロイ手順（サーバー側で実施・この順番で行う）

リポジトリ: https://github.com/hideonSOS/web_analize （public）
**リポジトリには `config.json` と `media/` と DBデータが含まれない**点に注意。

### 1. 取得と Python 環境
```bash
git clone https://github.com/hideonSOS/web_analize.git
cd web_analize
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```
- **Python 3.10 以上が必須**（Django 5.2 の要件。3.9以下では動かない）
- 依存33個は新規venvでの導入を検証済み（衝突なし）

### 2. PostgreSQL のセットアップ
```bash
sudo -u postgres psql -c "CREATE DATABASE web_kabuanalize ENCODING 'UTF8' LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;"
```
- **開発機の `Japanese_Japan.932` は Linux に存在しないので使わないこと**（上記参照）
- PostgreSQL は 17系を推奨

### 3. config.json を手動設置（これが無いと起動しない）
プロジェクト直下に配置する。書式は下の「config.json」節を参照。
**DBパスワードはサーバー用に変更すること**（開発機の値をそのまま使わない）。

### 4. 本番用の設定（**settings.py は編集しない。config.json に書く**）
本番/開発の差は **`config.json`（git管理外）** で与える。**`settings.py` を環境ごとに
編集してはいけない**——編集すると `git pull` のたびに衝突して更新できなくなる
（実際にサーバーで発生した）。本番の `config.json` に以下を追加する:
```json
{
    "secret_key": "<新規生成した鍵>",
    "debug": false,
    "allowed_hosts": ["ドメイン名", "サーバーIP"]
}
```
未指定なら開発既定（`debug:true` / `allowed_hosts:['*']` / 開発用SECRET_KEY）になる。
`STATIC_ROOT` は `BASE_DIR / "static"` に固定（`.gitignore` 済み。各環境で collectstatic）。

SECRET_KEY の生成:
```bash
./venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 5. マイグレーションと静的ファイル
```bash
./venv/bin/python manage.py migrate          # 全23マイグレーションが適用される
./venv/bin/python manage.py collectstatic    # STATIC_ROOT(=BASE_DIR/static)へ出力
mkdir -p media && chmod 775 media            # 顔写真アップロード用。書き込み権限が必要
```

### 6. データ投入（この順番。初回は合計1〜2時間）
```bash
./venv/bin/python manage.py update_marketcap --backfill-years 5   # 約1時間（API約1,300コール）
./venv/bin/python manage.py import_us_master                      # 数分（米国株12,484件）
./venv/bin/python manage.py update_volume                         # 約10分
./venv/bin/python manage.py update_us_prices                      # 数秒（日記に米国株がある場合のみ）
./venv/bin/python manage.py update_us_ranking                     # 数分（S&P500の時価総額・出来高）
```

### 7. 手入力データの移行（自動再生成できないもの）
`karte_*`（銘柄カルテ）と `diary_*`（売買日記）は手入力の資産で、
バッチでは復元できない。開発機から移送する:
```bash
# 開発機（Windows）側。cp932で書き出されるのでUTF-8指定が必須
set PYTHONUTF8=1
python manage.py dumpdata karte diary -o handdata.json
```
サーバー側で `loaddata handdata.json`。
**`media/` 配下の顔写真も別途コピーする**（gitに含まれないため）。

### 8. Webサーバー設定と定期実行
- gunicorn/uWSGI + nginx を想定。nginx から `/static/` と `/media/` を配信する
  （`DEBUG=False` では Django は静的ファイルを配信しない）
- **⚠️ nginx の `location /static/` の `alias` は `STATIC_ROOT` に一致させる**:
  ```nginx
  location /static/ { alias /srv/web_analize/static/; expires 7d; }  # 末尾スラッシュ必須
  location /media/  { alias /srv/web_analize/media/; }
  ```
- 平日夜のバッチを cron に登録（下の「データ更新バッチ」節）

### ⚠️ デプロイしても反映されない（静的ファイルが古いまま）
実際に踏んだ事故。`git pull` しても**CSS/JSが古いまま**になる典型原因は2つ:
1. **アプリを再起動していない** → Pythonコード・テンプレートは
   `sudo systemctl restart web_analize.service`（本番のサービス名）で反映される。
2. **nginx が旧い収集先を配信している** → 当初 `STATIC_ROOT` 未設定時代の
   `/srv/web_analize/staticfiles/` を nginx が指したまま、`STATIC_ROOT` を
   `/srv/web_analize/static/` に変えたため不一致になった。`alias` を上記に合わせる。

**反映の正しい手順（変更種別で必要な操作が違う）**:
```bash
cd /srv/web_analize && git pull
./venv/bin/python manage.py collectstatic --noinput   # CSS/JS/画像を変えたとき
sudo systemctl reload nginx                           # nginx設定を変えたとき（無停止）
sudo systemctl restart web_analize.service            # Pythonコード/テンプレを変えたとき
```
**配信結果の直接確認**（サーバー内で最新が出ているか。1以上なら最新）:
```bash
curl -s http://localhost/static/diary/css/diary.css | grep -c dy-price-when
```
CSS/JSは `?v={% now 'U' %}` のキャッシュ避け付きなので、これが古い場合は
ブラウザ側ではなく**サーバーの配信ファイルが古い**（上記1か2を疑う）。

### 補足: HTTPS化する場合のみ必要な設定
`check --deploy` で出る HSTS・SECURE_SSL_REDIRECT・SESSION_COOKIE_SECURE・
CSRF_COOKIE_SECURE の4警告は、SSL導入時に対応すればよい。

## テキストエリアの自動拡張（触る前に読むこと）

カルテ・売買日記の入力欄は **スクロールバーを出さず、入力量に応じて高さを伸ばす**方針。
実装は `website/static/website/js/base.js` の1箇所に集約され、全ページの
`<textarea>` に適用される（`window.autoGrowTextareas` で再初期化も可能）。

### ⚠️ height = scrollHeight は間違い（実際に文字が消えた）
```js
ta.style.height = ta.scrollHeight + 'px';   // ← これはバグ
```
CSSが `box-sizing: border-box` + `border:1px` のため、**`height` には border が
含まれるが `scrollHeight` には含まれない**。そのまま代入すると border分だけ高さが
不足し、最終行がはみ出す。`overflow-y:hidden` なのでスクロールバーも出ず、
**文字が黙って消える**（気付けない）。必ず border を足し戻すこと。

### ⚠️ 横スクロールバーも高さを削る
長いURL等で横方向にあふれると横スクロールバーが出て、その高さ分だけ表示領域が
削られ、再び最終行が隠れる。`overflow-x:hidden; overflow-wrap:anywhere;` で
折り返して防いでいる。**この2つを外すと再発する。**

### 手動リサイズとの共存
`resize:vertical` でハンドルを出している。手動で高さを変えた欄は
`data-manual-resize="1"` を付けて**以後の自動調整を止める**。止めないと
次の入力で高さが戻り、手動調整が無駄になる。

### base.js 冒頭の存在確認は外さないこと
ファイル先頭のナビ開閉処理で例外が出ると、**同じファイル内の自動拡張処理まで
丸ごと停止する**（＝全ページで文字が隠れる）。要素の存在確認は必須。

## 既知のハマりどころ

- **コードを直したのに挙動が変わらない** → 古い runserver プロセスがポートを握って旧コードで
  応答していることがある（Windows で実際に発生）。プロセス残骸を kill してから再起動。
- 旧 `db.sqlite3` は SQLite 時代のバックアップとして残置（PostgreSQL 移行済み・参照されない）。
