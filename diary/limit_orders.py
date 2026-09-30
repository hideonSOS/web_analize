"""指値の買い注文（LimitOrder）の判定と画面用の値（2026-09-30 ユーザー要望）。

流れ: 注文時に1回だけ記録 → 翌日、画面の目安（その日の安値が指値に届いたか）を見て
「約定した」「約定しなかった」を押す。約定したら通常の買いの日記を作る（views.order_fill）。

- 当日限り。**注文が有効な取引日（session_date）**は市場の時計で決める。米国株は米国東部時間で、
  引け（16:00）以降に入れた注文は翌営業日の注文。日本株は 15:30 以降なら翌営業日。土日は月曜へ
  （祝日は日足が無いので、その日以降の最初の足を注文日の足とみなす）
- 日足は yfinance の生の OHLC（auto_adjust=False。指値は生の価格なので判定も生の価格）
- 約定価格の目安: 寄り付きが指値より下なら始値で約定（それ以外は指値）
- 約定しなかった注文の答え合わせ: 注文日の安値が指値まで あと何%／その後 AFTER_BARS 本で
  上に行ってしまったか（指値が深すぎた）・指値まで下げたか（待てば買えた）
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from .models import LimitOrder

AFTER_BARS = 10          # その後を見る本数（営業日）
TZ = {'US': ZoneInfo('America/New_York'), 'JP': ZoneInfo('Asia/Tokyo')}
CLOSE = {'US': time(16, 0), 'JP': time(15, 30)}
DATA_LAG = timedelta(minutes=40)   # 引けから日足が取れるまでの余裕


def _country(o: LimitOrder) -> str:
    return 'US' if (o.stock and o.stock.country == 'US') or str(o.stock_code).startswith('US') else 'JP'


def _next_weekday(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def session_date_for(placed_at: datetime, country: str) -> date:
    """注文日時 → その注文が有効な取引日（市場の現地日付）"""
    local = placed_at.astimezone(TZ[country])
    d = local.date()
    if local.time() >= CLOSE[country]:      # 引け後の注文は翌営業日
        d += timedelta(days=1)
    return _next_weekday(d)


def session_closed(d: date, country: str, now: datetime | None = None) -> bool:
    """その取引日の引け（＋日足が取れるまでの余裕）を過ぎたか"""
    now = now or timezone.now()
    close_dt = datetime.combine(d, CLOSE[country], tzinfo=TZ[country]) + DATA_LAG
    return now >= close_dt


def yf_ticker(o: LimitOrder) -> str:
    code = o.stock.display_code if o.stock else o.stock_code
    return code if _country(o) == 'US' else f'{code}.T'


def fetch(o: LimitOrder, now: datetime | None = None) -> bool:
    """注文日の日足と、その後 AFTER_BARS 本を取って保存。1注文1コール。取れたら True"""
    import yfinance as yf
    country = _country(o)
    if not session_closed(o.session_date, country, now):
        return False
    end = min(date.today() + timedelta(days=1), o.session_date + timedelta(days=AFTER_BARS * 2 + 10))
    df = yf.download(yf_ticker(o), start=(o.session_date - timedelta(days=1)).isoformat(), end=end.isoformat(),
                     interval='1d', auto_adjust=False, progress=False)
    if df is None or df.empty:
        return False
    cols = {}
    for name in ('Open', 'High', 'Low', 'Close'):
        s = df[name]
        cols[name] = s.iloc[:, 0] if hasattr(s, 'columns') else s
    bars = []
    for ts in df.index:
        d = ts.date() if hasattr(ts, 'date') else ts
        if d < o.session_date or not session_closed(d, country, now):   # 引け前の途中の足は使わない
            continue
        vals = [float(cols[n].loc[ts]) for n in ('Open', 'High', 'Low', 'Close')]
        if any(v != v for v in vals):
            continue
        bars.append((d, *vals))
    if not bars:
        return False
    d0, o0, h0, l0, c0 = bars[0]
    o.bar_date, o.day_open, o.day_high, o.day_low, o.day_close = d0, o0, h0, l0, c0
    after = bars[1:AFTER_BARS + 1]
    o.after_n = len(after)
    if after:
        hi = max(after, key=lambda b: b[2])
        lo = min(after, key=lambda b: b[3])
        o.after_high, o.after_high_date = hi[2], hi[0]
        o.after_low, o.after_low_date = lo[3], lo[0]
        o.after_close, o.after_close_date = after[-1][4], after[-1][0]
    o.save(update_fields=['bar_date', 'day_open', 'day_high', 'day_low', 'day_close', 'after_n',
                          'after_high', 'after_high_date', 'after_low', 'after_low_date',
                          'after_close', 'after_close_date'])
    return True


def needs_fetch(o: LimitOrder) -> bool:
    """日足を取りに行く注文: 指値中で注文日の足がまだ無い／約定しなかった注文で「その後」がまだ揃っていない"""
    if o.status == 'pending':
        return o.bar_date is None or o.after_n < AFTER_BARS
    if o.status == 'unfilled':
        return o.after_n < AFTER_BARS
    return False


def row(o: LimitOrder) -> dict:
    """画面用: 判定の目安・約定価格の目安・その後"""
    L = o.limit_price
    is_us = _country(o) == 'US'
    cur = float(o.stock.close) if (o.stock and o.stock.close) else None
    r = {'o': o, 'is_us': is_us, 'cur_pre': '$' if is_us else '', 'cur_suf': '' if is_us else '円',
         'cur': cur, 'to_limit': ((L / cur - 1) * 100) if cur else None,
         'closed': session_closed(o.session_date, 'US' if is_us else 'JP'),
         'verdict': '', 'fill_price': L, 'gap_open': False, 'miss_pct': None, 'after': None}
    if o.day_low is not None:
        if o.day_low <= L:
            r['verdict'] = 'reached'            # 指値に届いた → 約定したはず
            if o.day_open is not None and o.day_open < L:
                r['gap_open'] = True            # 寄り付きが指値より下 → 始値で約定した可能性
                r['fill_price'] = o.day_open
        else:
            r['verdict'] = 'missed'             # 届かず → 約定していない
            r['miss_pct'] = (o.day_low / L - 1) * 100
    if o.after_n:
        up = (o.after_high / L - 1) * 100 if o.after_high else None
        last = (o.after_close / L - 1) * 100 if o.after_close else None
        reached_later = o.after_low is not None and o.after_low <= L
        if reached_later:
            d = o.after_low_date
            word, tone = f'{d.month}/{d.day}に指値まで下げた（待てば買えた）', 'wait'
        elif last is not None and last >= 5:
            word, tone = '上に行ってしまった（指値が深すぎた）', 'up'
        else:
            word, tone = '指値に届かないまま', 'flat'
        r['after'] = {'n': o.after_n, 'up': up, 'last': last, 'word': word, 'tone': tone,
                      'done': o.after_n >= AFTER_BARS}
    return r


def summary(orders) -> dict:
    """約定率と、約定しなかった注文が平均で何%届かなかったか"""
    done = [o for o in orders if o.status in ('filled', 'unfilled')]
    filled = sum(1 for o in done if o.status == 'filled')
    misses = [(o.day_low / o.limit_price - 1) * 100 for o in done
              if o.status == 'unfilled' and o.day_low is not None and o.day_low > o.limit_price]
    return {'done': len(done), 'filled': filled, 'unfilled': len(done) - filled,
            'fill_rate': (filled / len(done) * 100) if done else None,
            'avg_miss': (sum(misses) / len(misses)) if misses else None}
