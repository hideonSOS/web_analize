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
現在のマイグレーションは全13ファイル（japan_kabu 6・diary 3・karte 4）。
デプロイ後は `python manage.py migrate` を必ず実行する。

### 開発環境のデータ規模（2026-07-21 時点・移行量の目安）

| テーブル | 行数 | 備考 |
|---|---|---|
| japan_kabu_dailyvolume | 125,321 | 日次出来高。70日でローリング削除 |
| japan_kabu_financialreport | 70,022 | 決算(FY+四半期)。5年バックフィル済み |
| japan_kabu_stock | 16,199 | 日本株3,715 + 米国株12,484 |
| karte_* / diary_* | 数件〜十数件 | 手入力データ。**再構築できないので必ず移送する** |

japan_kabu_* はバッチで再生成できるが、**銘柄カルテ(karte_*)と売買日記(diary_*)は
手入力の資産なので失うと戻せない**。移行時はこの2アプリを優先して退避すること。
`/media/` 配下の顔写真も同様（gitに含まれない）。

### config.json（gitに含まれない・手動設置が必要）
`.gitignore` で除外しているため、**サーバーには手動で設置する**。プロジェクト直下に:
```json
{
    "api_key": "<J-Quants APIキー>",
    "edinet_api_key": "<EDINET APIキー>",
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

## データ更新バッチ（cron / タスクスケジューラ登録が必要）

平日夜に実行する（J-Quants のデータは当日夜〜翌朝に確定）:
```bash
python manage.py update_marketcap   # マスタ・株価・決算・時価総額・期末株価
python manage.py update_volume      # 日次出来高と出来高異常度(z-score)
python manage.py update_us_prices   # 売買日記で使われた米国株の株価（yfinance）
```
- 初回のみ: `python manage.py update_marketcap --backfill-years 5`（決算5年分、約1時間）
- 初回のみ: `python manage.py import_us_master`（米国株ティッカー一覧 約12,000件。売買日記の米国株選択用）
- バッチを止めると: ランキングの株価が古くなる／売買日記の「その後±%」「概算損益」が更新されない。

## 米国株（売買日記のみ）

- 売買日記は米国株も記録できる。銘柄マスタは `import_us_master` が NASDAQ Trader の
  公開シンボルディレクトリ（`nasdaqlisted.txt` / `otherlisted.txt`）から取り込む。**外部HTTP接続が必要**。
- 米国株の株価は `update_us_prices` が **yfinance** で取得。全銘柄ではなく「日記に登場した
  ティッカーだけ」なので数十コールで済む。**yfinance と外部HTTP接続が必要**（requirements.txt に含む）。
- 米国株は `japan_kabu.Stock` に `country='US'` / `code="US-<ティッカー>"` で保存。JP専用の
  ランキング・指標ページは `market_cap` 等の isnull フィルタで US を自然に除外している。
- 銘柄マスタ（JP+US 約2MB）は売買日記の `/diary/stock-options.json` で配信し、ブラウザに
  1時間キャッシュさせている（ページ埋め込みだと毎回2MBになるため分離した）。本番では gzip 必須。

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
| `japan_kabu` | 時価総額ランキング(`/japan_kabu/`)・出来高急増(`/japan_kabu/volume/`)・銘柄別指標(`/japan_kabu/stock/<code>/`) |
| `diary` | 売買日記(`/diary/`)。判断記録は編集不可・振り返りのみ追記の設計 |

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

### 4. 本番用の設定変更（`web_kabuanalize/settings.py`）
現状は開発用のままなので、以下4点を必ず変更する。**未対応だとサイトが表示されない。**

| 項目 | 現状 | 変更内容 |
|---|---|---|
| `DEBUG` | `True` | `False` |
| `ALLOWED_HOSTS` | `[]` | `['ドメイン名', 'サーバーIP']`。**空のままだと全リクエストを拒否** |
| `SECRET_KEY` | `django-insecure-...` | 新規生成して差し替え |
| `STATIC_ROOT` | 未設定 | `BASE_DIR / 'staticfiles'` を追記（collectstaticに必須） |

SECRET_KEY の生成:
```bash
./venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

### 5. マイグレーションと静的ファイル
```bash
./venv/bin/python manage.py migrate          # 全13マイグレーションが適用される
./venv/bin/python manage.py collectstatic    # STATIC_ROOT 設定後に実行
mkdir -p media && chmod 775 media            # 顔写真アップロード用。書き込み権限が必要
```

### 6. データ投入（この順番。初回は合計1〜2時間）
```bash
./venv/bin/python manage.py update_marketcap --backfill-years 5   # 約1時間（API約1,300コール）
./venv/bin/python manage.py import_us_master                      # 数分（米国株12,484件）
./venv/bin/python manage.py update_volume                         # 約10分
./venv/bin/python manage.py update_us_prices                      # 数秒（日記に米国株がある場合のみ）
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
- 平日夜のバッチを cron に登録（下の「データ更新バッチ」節）

### 補足: HTTPS化する場合のみ必要な設定
`check --deploy` で出る HSTS・SECURE_SSL_REDIRECT・SESSION_COOKIE_SECURE・
CSRF_COOKIE_SECURE の4警告は、SSL導入時に対応すればよい。

## 既知のハマりどころ

- **コードを直したのに挙動が変わらない** → 古い runserver プロセスがポートを握って旧コードで
  応答していることがある（Windows で実際に発生）。プロセス残骸を kill してから再起動。
- 旧 `db.sqlite3` は SQLite 時代のバックアップとして残置（PostgreSQL 移行済み・参照されない）。
