"""保有中の逆張り取引の日足（生の OHLC）を yfinance から取る。

    python manage.py update_trade_bars            # 保有中の全取引
    python manage.py update_trade_bars --trade 12 # 1件だけ（エントリー直後の即時反映用）

- エントリー日から今日までを毎回取り直す（1銘柄1コール・数秒。保有中は数件しか無い）
- ⚠️ auto_adjust=False。指値は生の価格に置くので、判定に使う安値/高値も生の価格
- 当日の途中バーも入れる（画面の「現在価格」に使う。翌日の取得で確定値に上書きされる）
- 朝の us_index_update.sh（米国株の引け後）と夜の daily_update.sh（日本株の引け後）に同梱
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from diary.models import Trade, TradeBar


def yf_ticker(trade: Trade) -> str:
    return trade.ticker if trade.country == 'US' else f'{trade.ticker}.T'


def fetch_bars(trade: Trade) -> int:
    """エントリー日〜今日の日足を取り直して upsert。戻り値は保存した本数"""
    import yfinance as yf
    start = trade.entry_date - timedelta(days=1)
    df = yf.download(yf_ticker(trade), start=start.isoformat(), interval='1d',
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return 0
    cols = {}
    for name in ('Open', 'High', 'Low', 'Close'):
        s = df[name]
        cols[name] = s.iloc[:, 0] if hasattr(s, 'columns') else s
    existing = {b.date: b for b in trade.bars.all()}
    to_create, to_update = [], []
    for ts in df.index:
        d = ts.date() if hasattr(ts, 'date') else ts
        if d < trade.entry_date:
            continue
        vals = [float(cols[n].loc[ts]) for n in ('Open', 'High', 'Low', 'Close')]
        if any(v != v for v in vals):     # NaN
            continue
        o, h, lo, c = vals
        if d in existing:
            b = existing[d]
            if (b.open, b.high, b.low, b.close) != (o, h, lo, c):
                b.open, b.high, b.low, b.close = o, h, lo, c
                to_update.append(b)
        else:
            to_create.append(TradeBar(trade=trade, date=d, open=o, high=h, low=lo, close=c))
    if to_create:
        TradeBar.objects.bulk_create(to_create, ignore_conflicts=True)
    if to_update:
        TradeBar.objects.bulk_update(to_update, ['open', 'high', 'low', 'close'])
    return len(to_create) + len(to_update)


class Command(BaseCommand):
    help = '保有中の逆張り取引の日足（生OHLC）を yfinance から更新する'

    def add_arguments(self, parser):
        parser.add_argument('--trade', type=int, default=0, help='Trade の id を1件だけ')

    def handle(self, *args, **options):
        qs = Trade.objects.filter(exit_date__isnull=True)
        if options['trade']:
            qs = Trade.objects.filter(id=options['trade'])
        ok = ng = 0
        for t in qs:
            try:
                n = fetch_bars(t)
                ok += 1
                self.stdout.write(f'  {t.ticker:8} {t.entry_date}〜 {n}本更新（計 {t.bars.count()}本）')
            except Exception as e:   # noqa: BLE001  1件の失敗で他を止めない
                ng += 1
                self.stderr.write(f'  {t.ticker}: 失敗: {e}')
        self.stdout.write(self.style.SUCCESS(f'取引の日足: {ok}件成功 / {ng}件失敗（{date.today()}）'))
