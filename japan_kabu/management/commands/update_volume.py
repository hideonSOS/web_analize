"""日次出来高を取得し、出来高異常度（対数z-score・倍率）を計算する

使い方:
    python manage.py update_volume

初回は過去約45日分の株価データを日付単位で取得（30回程度のAPIコール）。
2回目以降は未取得の営業日分だけ取得する。cron/タスクスケジューラで
update_marketcap と同じタイミングの実行を想定。

計算式（出来高の分布は対数正規に近いため対数変換してから標準化する）:
    z = (ln V_t - mean(ln V_hist)) / max(stdev(ln V_hist), SIGMA_FLOOR)
    ratio = V_t / mean(V_hist)          # 表示用の倍率
    V_hist = 直近20営業日（当日を除く）の出来高
"""
import math
import statistics
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand

from japan_kabu import jquants
from japan_kabu.models import DailyVolume, Stock

WINDOW = 20            # 比較対象の過去営業日数
MIN_HISTORY = 15       # 計算に必要な最低履歴日数（上場直後などを除外）
SIGMA_FLOOR = 0.1      # σの下限（毎日ほぼ同一出来高の銘柄でzが発散するのを防ぐ）
MIN_TURNOVER = 1e8     # 過去売買代金の中央値の下限（円）。薄商い銘柄の誤検知除外
BACKFILL_DAYS = 45     # 取得を遡る暦日数（営業日20日+余裕）
KEEP_DAYS = 70         # これより古い履歴は削除


class Command(BaseCommand):
    help = '日次出来高を取得し出来高異常度（z-score）を更新する'

    def handle(self, *args, **options):
        latest = self._latest_trading_date()
        if latest is None:
            self.stderr.write('最新営業日が取得できませんでした')
            return
        self._fetch_missing(latest)
        self._prune(latest)
        computed = self._compute(latest)
        self.stdout.write(self.style.SUCCESS(
            f'完了: 出来高異常度を算出 {computed}銘柄（基準日 {latest}）'))

    def _latest_trading_date(self):
        from_date = (date.today() - timedelta(days=14)).isoformat()
        ref = jquants.get_bars_by_code('7203', from_date=from_date)
        if not ref:
            return None
        return datetime.strptime(ref[-1]['Date'], '%Y-%m-%d').date()

    def _fetch_missing(self, latest):
        """未取得の営業日の全銘柄出来高を取得する"""
        have = set(DailyVolume.objects.values_list('date', flat=True).distinct())
        codes = set(Stock.objects.values_list('code', flat=True))
        fetched = 0
        d = latest - timedelta(days=BACKFILL_DAYS)
        while d <= latest:
            if d.weekday() < 5 and d not in have:
                time.sleep(jquants.REQUEST_WAIT)
                bars = jquants.get_bars_by_date(d.isoformat())
                objs = [
                    DailyVolume(
                        stock_id=b['Code'], date=d,
                        volume=int(b['Vo']), turnover=int(b.get('Va') or 0),
                    )
                    for b in bars
                    if b['Code'] in codes and b.get('Vo') is not None
                ]
                if objs:
                    DailyVolume.objects.bulk_create(objs, ignore_conflicts=True)
                    fetched += 1
            d += timedelta(days=1)
        self.stdout.write(f'出来高取得: {fetched}営業日分を追加')

    def _prune(self, latest):
        removed, _ = DailyVolume.objects.filter(
            date__lt=latest - timedelta(days=KEEP_DAYS)).delete()
        if removed:
            self.stdout.write(f'古い履歴を削除: {removed}件')

    def _compute(self, latest):
        """銘柄ごとに対数z-scoreと倍率を計算してStockに保存する"""
        series = defaultdict(list)
        rows = (DailyVolume.objects
                .filter(date__gte=latest - timedelta(days=BACKFILL_DAYS))
                .order_by('date')
                .values_list('stock_id', 'date', 'volume', 'turnover'))
        for code, d, vol, val in rows:
            series[code].append((d, vol, val))

        stocks = list(Stock.objects.all())
        computed = 0
        for s in stocks:
            s.volume = s.volume_date = s.volume_ratio = s.volume_z = None
            result = self._score(series.get(s.code, []), latest)
            if result:
                s.volume, s.volume_ratio, s.volume_z = result
                s.volume_date = latest
                computed += 1
        Stock.objects.bulk_update(
            stocks, ['volume', 'volume_date', 'volume_ratio', 'volume_z'],
            batch_size=500)
        return computed

    @staticmethod
    def _score(rows, latest):
        """(出来高, 倍率, z) を返す。計算不能・フィルタ除外は None"""
        if not rows or rows[-1][0] != latest:
            return None
        cur_volume = rows[-1][1]
        if cur_volume <= 0:
            return None
        hist = [(v, t) for _, v, t in rows[-(WINDOW + 1):-1] if v > 0]
        if len(hist) < MIN_HISTORY:
            return None
        volumes = [v for v, _ in hist]
        turnovers = [t for _, t in hist]
        if statistics.median(turnovers) < MIN_TURNOVER:
            return None
        ln_hist = [math.log(v) for v in volumes]
        mu = statistics.fmean(ln_hist)
        sigma = max(statistics.pstdev(ln_hist), SIGMA_FLOOR)
        z = (math.log(cur_volume) - mu) / sigma
        ratio = cur_volume / statistics.fmean(volumes)
        return cur_volume, ratio, z
