#!/usr/bin/env bash
#
# 米国株ランキング（S&P500の時価総額・出来高）の更新
#
# 米国市場のクローズ(16:00 ET)が確定した後に実行する。JSTなら朝が最適:
#   - JST 7:00 = US 17:00〜18:00 ET(クローズ後) → その日の確定クローズを取得できる
#   - 場中(例: JST 0:00 = US 11:00 ET)に回すと未確定の途中値で上書きされるので不可
#
#   crontab -e で以下を登録する（サーバーTZに合わせて時刻を換算すること）:
#     # サーバーが JST の場合: 毎朝7:00
#     0 7 * * * /path/to/web_analize/scripts/us_ranking_update.sh
#     # サーバーが UTC の場合: JST7:00 = UTC22:00(前日)
#     0 22 * * * /path/to/web_analize/scripts/us_ranking_update.sh
#
# 設計メモ:
# - 日次は「株価・出来高の一括ダウンロード + 時価総額は保存株式数で計算」で
#   約2.5〜3分。発行済株式数は変動が遅いので毎日は取り直さない。
# - 週1回（日曜）だけ --refresh-shares を付けて発行済株式数を取り直す。
#   曜日判定を内蔵しているので cron は上記1行でよい（別行を足す必要はない）。
#
set -u

cd "$(dirname "$0")/.." || exit 1

# 本番(Linux)のパス。Windows開発機で手動確認する場合は Scripts/python.exe を使う
PY="./venv/bin/python"
[ -x "$PY" ] || PY="./venv/Scripts/python.exe"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/us_ranking_$(date +%Y%m%d).log"

if [ ! -x "$PY" ]; then
    echo "$(date '+%F %T') [FATAL] venv が見つかりません: $PY" | tee -a "$LOG"
    exit 1
fi

# 日曜(=7)は発行済株式数も取り直す（週1・数分）。他の曜日は保存値を再利用
EXTRA=""
if [ "$(date +%u)" = "7" ]; then
    EXTRA="--refresh-shares"
fi

echo "" >> "$LOG"
echo "===== $(date '+%F %T') manage.py update_us_ranking $EXTRA =====" >> "$LOG"
if "$PY" manage.py update_us_ranking $EXTRA >> "$LOG" 2>&1; then
    echo "----- OK -----" >> "$LOG"
    status=0
else
    status=$?
    echo "----- FAILED (exit $status) -----" >> "$LOG"
fi

# セクター別インパルス用の日次終値（JP/US 数銘柄・数コール）。
# JP前日バーは朝に出そろい、USはこの時刻ならクローズ確定後なので同枠で回す。
# コマンド側に「クローズ前の未確定当日バーは保存しない」ガードあり。
echo "===== $(date '+%F %T') manage.py update_impulse_prices =====" >> "$LOG"
if "$PY" manage.py update_impulse_prices >> "$LOG" 2>&1; then
    echo "----- OK -----" >> "$LOG"
else
    status=$?
    echo "----- FAILED (exit $status) -----" >> "$LOG"
fi

# マクロ指標（日米のCPI・失業率）。FRED/DBnomicsから月次系列を取得（数秒・キー不要）。
# 月次データだが、季節調整の遡及改定に追従するため日次で回す（差分のみ書き込み）
echo "===== $(date '+%F %T') manage.py update_macro =====" >> "$LOG"
if "$PY" manage.py update_macro >> "$LOG" 2>&1; then
    echo "----- OK -----" >> "$LOG"
else
    status=$?
    echo "----- FAILED (exit $status) -----" >> "$LOG"
fi

# 30日より古いログは削除する
find "$LOG_DIR" -name 'us_ranking_*.log' -mtime +30 -delete 2>/dev/null

exit "$status"
