# Zaim 自動取得（公式API）のセットアップ手順

支出分析の Zaim データを、手動の CSV アップロードではなく **Zaim 公式 API** で毎晩自動取得する。
2026-09-06 導入。実装は `card_insight/zaim_api.py`・`spending/management/commands/fetch_zaim.py`・
`scripts/zaim_authorize.py`。

## なぜ API か（方針）
- Zaim には**出金の権限が無い**ので、自動化してもユーザーの線引き
  （「出金する権限が流出するのは困る」）に収まる。楽天・銀行・Amazon は従来どおり手動
- サーバーが持つのは **アプリの鍵＋一度ブラウザで承認して発行したトークン**。Zaim の
  パスワードは持たない。トークンで出来るのは家計簿の記録の読み書きまで
- 自動ログイン＋CSVダウンロード（ブラウザ自動化）は採らない（画面変更・二段階認証で壊れる）
- 出力は **Zaim のエクスポート CSV と同じ 16 列・cp932** で `data/spending/zaim/Zaim.<日時>.api.csv`
  に保存し、既存の取り込み（`import_from_files`）をそのまま回す。**下流は無変更**
- 手動アップロードは併存。`latest_zaim()` は名前順の末尾を採用するので、手動でも API でも
  **日時が新しい方**が使われる

## 1. Zaim 開発者センターでアプリを作る（ユーザー自身が行う）
1. https://dev.zaim.net にログイン →「新しいアプリケーションを追加」
2. 名前は任意（例: kabu_analyze）。サービス種別は**クライアント型**、コールバック URL は不要
3. **Consumer Key / Consumer Secret** を控える

## 2. トークンを発行する（ローカルで一度だけ）
```bash
python scripts/zaim_authorize.py
```
venv の Python で実行する（システムの Python には requests が無い）:
```powershell
& "<プロジェクト>\venv\Scripts\python.exe" "<プロジェクト>\scripts\zaim_authorize.py"
```
Consumer Key / Secret を入力 → 表示された URL をブラウザで開いて Zaim にログイン →「許可」
→ 画面の認証コード（oauth_verifier）を入力するか、`http://127.0.0.1:5000/callback?...` へ飛んで
「アクセスできません」と出た場合はその URL を丸ごと貼る → 最後に JSON が出る。

⚠️ `oauth_callback='oob'` は Zaim が受け付けず **401** になる（実際に踏んだ）。コールバックは
URL の形が必須で、既定は `http://127.0.0.1:5000/callback`（ローカルにサーバーは不要）。
アプリ登録画面にコールバック URL 欄があれば同じ値を入れる。

## 3. サーバーの config.json に貼る
```json
"zaim": {
    "consumer_key": "...",
    "consumer_secret": "...",
    "access_token": "...",
    "access_token_secret": "..."
}
```
`config.json` は `.gitignore` 済み。**リポジトリに入れない**。

## 4. 初回は必ず --check で欠けを確認する
```bash
cd /srv/web_analize
./venv/bin/python manage.py fetch_zaim --check
```
**API は手入力した記録しか返さない**（口座連携で自動取込された行は返らない、と公式注記）。
実測（2026-09-06）:

| 行の種類 | 手動 CSV | API | 結果 |
|---|---|---|---|
| レシート撮影・手入力（支払元 未設定／お財布／ゆうちょ） | 13,750 | 13,774 | **完全一致**（API の方が新しい分だけ多い） |
| 楽天カード連携（payment） | 1,137 | 0 | 欠ける |
| 三菱UFJ 連携（payment / transfer / balance） | 382 | 0 | 欠ける |
| 給料（income・入金先 三菱UFJ） | 76 | 0 | 欠ける |
| 残高調整（balance） | 67 | 0 | 欠ける（API に無い） |

そこで `fetch_zaim` は **最新の手動 CSV から欠ける行だけを補完**して 1 本の CSV にする
（`--no-manual` で止められる）。判定は「方法が balance」または「(支払元, 入金先) の組が API 側に
一つも無い」。API が返す組の行は取らないので重複しない。分析期間（2025-08〜）の台帳は
手動 CSV と 7 列キーで差 0、直近月だけ API で新しくなることを確認済み。

**運用**: レシート・手入力分は毎晩自動。口座連携分は補完元の手動 CSV の日付までなので、
**月1回程度は従来どおり手動アップロード**する（忘れても、カード分は e-navi・家賃と給料は
`ufj_bank/` が持つので集計は壊れない。Zaim 側で付けた連携行の分類が古くなるだけ）。

## 5. 本番反映
```bash
./venv/bin/python manage.py fetch_zaim            # 取得 → 保存 → 取り込み（約150ページ・数分）
```
`scripts/daily_update.sh` に `run fetch_zaim` を追加済みなので、以後は平日 21:10 の cron で
毎晩回る。未設定の環境では「未設定。何もしません」と出て正常終了する（失敗扱いにならない）。

## 仕様メモ（変更時に読む）
- `GET /v2/home/money` は `limit` 最大 100。`page` で送り、100 件未満が返ったら終わり。
  全期間（2016-01〜）で約 155 ページ。ページ間 0.3 秒待ち、429/5xx は 3 回まで再試行
- マスタ（カテゴリ・内訳・口座）は ID で返るので `home/category` `home/genre` `home/account`
  で名前に引き直す。無いものは実物 CSV に合わせて `"-"`
- **集計の設定は API に無い**。payment/income は「常に集計に含める」、transfer は
  「集計に含めない」に倒す（実物 CSV の分布 15,200:64 / 0:11 に合わせた近似）。
  Zaim 側で「集計に含めない」にした支出 64 行は API 経由だと集計に入る。気になるなら明細画面の
  「除外する」で個別に落とす
- 残高調整（balance）行は API では作れない（実物 CSV に 67 行）。台帳では元々
  `card_balance_rows` 参考用で集計には入らないので影響なし
- `active == -1`（削除済み）は落とす
- 署名は OAuth 1.0a HMAC-SHA1 を標準ライブラリで自前実装（依存追加なし）。
  requests-oauthlib と同じ署名になることを確認済み（リクエストトークン・アクセストークン・
  記録取得の3種）。⚠️ 正規化パラメータは**1回だけ**エンコードする。二重にすると日付だけの
  GET は通るのに `oauth_callback` 付きで 401 Unauthorized になる（実際に踏んだ）
- トークン取得は **POST**（GET は 401）。Zaim は verifier 無しでもアクセストークンを返す
- エクスポート CSV の癖（お店が空なら `-`、先頭 `-` の文字列に `'`）に `_csv_quirk` で合わせる。
  合わせないと同じ記録が別 `ledger_id` になり手動修正が外れる（実測 3 行）
- API 生成ファイルは `Zaim.<日時>.api.csv` で 3 世代残す（`--keep`）。手動分は消さない
- 副産物: API の `receipt_id` でレシート1枚の行をまとめられる（未使用。「レシートの疑い」検出を
  確実にできる将来課題）
