"""日次終値からドローダウン（高値からの下落率）とレンジ内位置を算出する

買い場判断の補助に使う。設計上の考え方:

- **主役はドローダウン**。レンジ内位置は期間を延ばすほど鈍化する
  （上昇トレンド銘柄では3年安値が遠い過去になり、万年「レンジ上限付近」を
  指し続けて判断材料にならない）。
- **1年と3年を必ず併記する**。高値更新中の銘柄では両者はほぼ一致し、
  乖離するのは「過去に天井を打って未回復」の銘柄だけ。この差自体が情報になる。
- ここで出す高値/安値は終値ベースの最大最小であり、**サポートライン
  （何度も反発した価格帯）ではない**点に注意。
"""
from datetime import date, timedelta

from .models import DailyPrice

WINDOWS = [('1y', 365), ('3y', 365 * 3)]
MIN_POINTS = 30        # これ未満はレンジを語れないものとして扱う


def _stats(points, current):
    """(高値, 安値, 下落率%, レンジ内位置%) を返す。算出不能は None"""
    if len(points) < MIN_POINTS:
        return None
    high = max(points)
    low = min(points)
    if high <= 0:
        return None
    # 高値からの下落率（0以下。0なら高値更新中）
    drawdown = (current / high - 1) * 100
    span = high - low
    position = (current - low) / span * 100 if span > 0 else 100.0
    return {'high': high, 'low': low, 'drawdown': drawdown, 'position': position}


def price_stats(stock, rows=None):
    """銘柄のレンジ統計を返す。データ不足なら None

    rows: [(date, close), ...] を渡すと再クエリしない（一覧でのN+1回避用）
    """
    if rows is None:
        rows = list(DailyPrice.objects.filter(stock=stock)
                    .order_by('date').values_list('date', 'close'))
    if not rows:
        return None

    today = date.today()
    current = rows[-1][1]
    out = {
        'current': current,
        'as_of': rows[-1][0],
        'points': len(rows),
    }
    for label, days in WINDOWS:
        since = today - timedelta(days=days)
        vals = [c for d, c in rows if d >= since]
        out[label] = _stats(vals, current)

    if out['1y'] is None and out['3y'] is None:
        return None

    # 3年レンジを外枠、1年レンジを内側の帯として描くための位置（%）
    out['bar'] = _bar_geometry(out)
    return out


def _bar_geometry(s):
    """レンジバーの描画位置。3年レンジを0-100%とし、1年レンジの帯位置を返す"""
    base = s.get('3y') or s.get('1y')
    if not base:
        return None
    lo, hi = base['low'], base['high']
    span = hi - lo
    if span <= 0:
        return None

    def pct(v):
        return max(0.0, min(100.0, (v - lo) / span * 100))

    geo = {'marker': pct(s['current'])}
    inner = s.get('1y')
    if inner:
        left = pct(inner['low'])
        geo['inner_left'] = left
        geo['inner_width'] = max(0.0, pct(inner['high']) - left)
    return geo


def bulk_price_stats(stocks):
    """複数銘柄分をまとめて算出する（クエリ1回）。{stock_id: stats} を返す"""
    codes = [s.code for s in stocks]
    rows_by_stock = {}
    qs = (DailyPrice.objects.filter(stock_id__in=codes)
          .order_by('stock_id', 'date')
          .values_list('stock_id', 'date', 'close'))
    for code, d, c in qs:
        rows_by_stock.setdefault(code, []).append((d, c))

    result = {}
    for s in stocks:
        rows = rows_by_stock.get(s.code)
        result[s.code] = price_stats(s, rows=rows) if rows else None
    return result
