#!/usr/bin/env bash
#
# 市場指数の早朝更新（下落上等ページの下落メーター用）
#
# 目的: 暴落の朝に「規模」をすぐ知るため、US引け直後に指数終値を取り込む。
# 米国市場は 16:00 ET クローズ。JST換算は夏時間 5:00 / 冬時間 6:00 と動くため、
# 通年で成立する最速の固定時刻として JST 6:30 に実行する:
#   - 冬: 6:30 JST = 16:30 ET → クローズ確定後（コマンド側ガードは16:05 ET）
#   - 夏: 6:30 JST = 17:30 ET → 余裕
#   ※ 6:00 は不可。冬は 16:00 ET ちょうどでガードに弾かれ、前日分しか入らない。
#
#   crontab -e で以下を登録する（サーバーがJSTの場合）:
#     30 6 * * * /srv/web_analize/scripts/us_index_update.sh
#
# 設計メモ:
# - ここで回すのは軽い2コマンドのみ（指数2銘柄 + インパルス約110銘柄・計1分弱）。
#   重い S&P500 ランキング(update_us_ranking)は従来どおり 7:00 の
#   us_ranking_update.sh に残す（引け直後は約500銘柄の未確定値リスクがあるため）。
# - 同じ2コマンドは 7:00 の us_ranking_update.sh でも引き続き実行される。
#   これは意図的な二重化: 差分同期なので6:30が成功していれば7:00は何もせず、
#   6:30がyfinance障害等で失敗しても7:00が拾う（暴落の朝の取りこぼし防止）。
# - 日曜だけ指数を --full で全期間取り直す（週次の穴埋め）。差分同期は「最新日より
#   後ろ」しか取らないため、Yahoo側で一度消えて後から復活した日（2026-08-28の
#   米国データ消失で実際に発生）が穴として残る。--full は保存済みと重複する分を
#   ignore_conflicts で捨てて欠けだけ埋めるので、既存データは壊れない。指数2銘柄
#   ×480日で数秒。曜日判定を内蔵しているので cron は毎日の1行でよい。
#
set -u

cd "$(dirname "$0")/.." || exit 1

# 本番(Linux)のパス。Windows開発機で手動確認する場合は Scripts/python.exe を使う
PY="./venv/bin/python"
[ -x "$PY" ] || PY="./venv/Scripts/python.exe"
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/us_index_$(date +%Y%m%d).log"

if [ ! -x "$PY" ]; then
    echo "$(date '+%F %T') [FATAL] venv が見つかりません: $PY" | tee -a "$LOG"
    exit 1
fi

status=0

# 日曜(=7)は --full で全期間を取り直し、Yahoo側の欠落復活分の穴を埋める
EXTRA=""
if [ "$(date +%u)" = "7" ]; then
    EXTRA="--full"
fi

# 市場指数（日経平均・S&P500）。下落メーターの材料。最優先なので先に回す
echo "" >> "$LOG"
echo "===== $(date '+%F %T') manage.py update_index_prices $EXTRA =====" >> "$LOG"
if "$PY" manage.py update_index_prices $EXTRA >> "$LOG" 2>&1; then
    echo "----- OK -----" >> "$LOG"
else
    status=$?
    echo "----- FAILED (exit $status) -----" >> "$LOG"
fi

# セクター別インパルス用の日次終値（暴落の朝に「どこが売られたか」を見る用）
echo "===== $(date '+%F %T') manage.py update_impulse_prices =====" >> "$LOG"
if "$PY" manage.py update_impulse_prices >> "$LOG" 2>&1; then
    echo "----- OK -----" >> "$LOG"
else
    status=$?
    echo "----- FAILED (exit $status) -----" >> "$LOG"
fi

# 30日より古いログは削除する
find "$LOG_DIR" -name 'us_index_*.log' -mtime +30 -delete 2>/dev/null

exit "$status"
