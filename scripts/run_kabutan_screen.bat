@echo off
rem スイング候補（株タン手法）の日次スクリーニング — Windows開発機のタスクスケジューラ用
rem 登録: 平日15:40（JP大引け後）と 平日7:05（USクローズ後の保険）
rem 本番サーバーは cron（daily_update.sh 21:10 / us_ranking_update.sh 7:00）が担当
cd /d "%~dp0.."
if not exist logs mkdir logs
venv\Scripts\python.exe manage.py run_kabutan_screen >> logs\kabutan_task.log 2>&1
