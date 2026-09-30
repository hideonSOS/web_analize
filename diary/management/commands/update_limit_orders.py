"""指値の買い注文（LimitOrder）の注文日の日足と「その後」を yfinance から取る（2026-09-30）。

    python manage.py update_limit_orders            # 指値中＋約定しなかった注文（その後が揃うまで）
    python manage.py update_limit_orders --order 3  # 1件だけ

- 1注文1コール。引け前の注文（その日の足が確定していない）は飛ばす
- 毎日の日足更新（daily_update.sh＝日本株の引け後／us_index_update.sh＝米国株の引け後）に同梱
"""
from django.core.management.base import BaseCommand

from diary import limit_orders as LO
from diary.models import LimitOrder


class Command(BaseCommand):
    help = '指値注文の注文日の日足とその後を yfinance から更新する'

    def add_arguments(self, parser):
        parser.add_argument('--order', type=int, default=0, help='LimitOrder の id を1件だけ')

    def handle(self, *args, **options):
        qs = LimitOrder.objects.select_related('stock').filter(status__in=['pending', 'unfilled'])
        if options['order']:
            qs = LimitOrder.objects.select_related('stock').filter(id=options['order'])
        ok = skip = ng = 0
        for o in qs:
            if not options['order'] and not LO.needs_fetch(o):
                continue
            try:
                if LO.fetch(o):
                    ok += 1
                    self.stdout.write(f'  {o.stock_code:8} {o.session_date} 指値 {o.limit_price:g} 安値 {o.day_low} その後 {o.after_n}本')
                else:
                    skip += 1
            except Exception as e:   # noqa: BLE001  1件の失敗で他を止めない
                ng += 1
                self.stderr.write(f'  {o.stock_code}: 失敗: {e}')
        self.stdout.write(self.style.SUCCESS(f'指値注文の日足: {ok}件更新 / {skip}件まだ（引け前など） / {ng}件失敗'))
