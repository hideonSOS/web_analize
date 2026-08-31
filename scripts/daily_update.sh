#!/usr/bin/env bash
#
# 日次データ更新（cron から実行する）
#
#   crontab -e で以下を登録（サーバーがJSTの場合。UTCなら 12:10 にする）
#     10 21 * * 1-5 /path/to/web_analize/scripts/daily_update.sh
#
# 設計メモ:
# - 1つのコマンドが失敗しても後続は実行する（例: yfinance障害で米国株が取れなくても
#   日本株の更新は通す）。最終的な終了コードで失敗有無を返す。
# - 土日祝に実行されても害はない。各コマンドは「直近の営業日」を見て動くため、
#   新しいデータが無ければ何も変わらないだけ。祝日カレンダーは持たない。
#
set -u

cd "$(dirname "$0")/.." || exit 1

# 本番(Linux)のパス。Windows開発機で手動確認する場合は Scripts/python.exe を使う
PY="./venv/bin/python"
[ -x "$PY" ] || PY="./venv/Scripts/python.exe"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/update_$(date +%Y%m%d).log"

if [ ! -x "$PY" ]; then
    echo "$(date '+%F %T') [FATAL] venv が見つかりません: $PY" | tee -a "$LOG"
    exit 1
fi

run() {
    echo "" >> "$LOG"
    echo "===== $(date '+%F %T') manage.py $* =====" >> "$LOG"
    if "$PY" manage.py "$@" >> "$LOG" 2>&1; then
        echo "----- OK -----" >> "$LOG"
        return 0
    else
        rc=$?
        echo "----- FAILED (exit $rc) -----" >> "$LOG"
        return $rc
    fi
}

status=0

# 日本株はハイブリッド構成（J-Quantsは無料プランのみ）:
#   - マスタ＋決算(銘柄別指標用) = J-Quants無料（直近は遅延で取れない分はスキップ）
#   - 株価・時価総額・出来高(ランキング) = yfinance（当日値が取れる）
# 順番に意味がある: 先にマスタを整えてから yfinance でランキングを作る。
# ※update_volume は J-Quants依存のため廃止（出来高は update_jp_ranking が算出）。
run update_marketcap     || status=1   # 銘柄マスタ＋決算（J-Quants無料）
run update_jp_ranking    || status=1   # 日本株ランキング（時価総額・出来高／yfinance）
run update_us_prices     || status=1   # 登録した米国株の株価（yfinance）
run update_us_financials || status=1   # 登録した米国株の決算（yfinance）
run update_daily_prices  || status=1   # 登録銘柄の日次終値（ドローダウン算出用・差分のみ）
run update_product_prices || status=1  # 投信の基準価額・金銀の円/g・ドル円（ポートフォリオ用）
run snapshot_assets       || status=1  # 日次の資産スナップショット（資産推移用。価格更新の後に）
# 注: 米国株ランキング(update_us_ranking)は US クローズ確定後のJST朝に
#     scripts/us_ranking_update.sh で別建て実行する（このバッチには入れない）。

# 30日より古いログは削除する
find "$LOG_DIR" -name 'update_*.log' -mtime +30 -delete 2>/dev/null

if [ "$status" -ne 0 ]; then
    echo "$(date '+%F %T') [WARN] 失敗したコマンドがあります。$LOG を確認してください" >> "$LOG"
fi
exit "$status"
