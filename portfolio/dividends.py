"""配当金ページ（/portfolio/dividends/・2026-09-27）のデータ組み立て

対象は投資スタイル「配当狙い」の保有株だけ（ユーザー指示）。株数・取得単価は current_stock_holdings
（棚卸し＋日記連動）と同じ。配当は DividendRecord（update_dividends が yfinance から取る）。

⚠️ 受取額はすべて「現在の株数で換算した目安」。過去の権利落ち日に実際に何株持っていたかは記録が無い。
税: NISA（積立・成長投資枠）は国内課税なし。米国株は NISA でも米国源泉 10% は引かれる（外国税額控除も不可）。
    特定口座は 日本株 20.315%、米国株 10% 源泉の残りに 20.315%（外国税額控除は考えない＝やや保守的）。
支払状況: 支払日が分かる行はそれで判定。分からない行は権利落ち日からの経過で推定
    （米国株は約1か月、日本株は約2.5か月で入金。日本株は期末の権利落ち→株主総会後の支払いのため）。
"""
from collections import defaultdict
from datetime import date, timedelta

from .models import DividendRecord, Holding

DIV_STYLE = '配当狙い'
NISA_ACCOUNTS = {'積立投資枠', '成長投資枠'}
TAX = {('JP', True): 0.0, ('JP', False): 0.20315,
       ('US', True): 0.10, ('US', False): 1 - 0.9 * (1 - 0.20315)}
PAY_LAG_DAYS = {'US': 30, 'JP': 80}          # 支払日が不明なときの「権利落ち→入金」目安


def typical_interval(ex_dates):
    """権利落ちの間隔（日）の中央値。2回未満なら None"""
    ds = sorted(ex_dates)
    gaps = sorted((b - a).days for a, b in zip(ds, ds[1:]) if (b - a).days > 0)
    return gaps[len(gaps) // 2] if gaps else None


def plausible_next(ex, past_dates):
    """calendar の「次の権利落ち日」がありえる日付か

    yfinance の calendar は他社・古い情報が混ざることがある（2026-09-27 に OWL で、8/13 に落ちたばかり
    なのに 9/30 が「次回」と出ていた。実際の OWL は四半期決算と同時に発表・11月ごろ）。直前の権利落ちから
    いつもの間隔の6割も経っていない日付は採らない
    """
    past = [d for d in past_dates if d < ex]
    step = typical_interval(past_dates)
    if not past or not step:
        return True
    return (ex - max(past)).days >= step * 0.6


def _estimate_next(past, today):
    """次の権利落ち日の推定: 1年前の同じ回の日付＋364日（曜日がそろう）

    平均間隔で足すと、年内で間隔が揃わない会社（PFE は 1月・5月・7月・11月）で2週間ずれる
    """
    cands = [d.ex_date + timedelta(days=364) for d in past if d.ex_date + timedelta(days=364) > today]
    return min(cands) if cands else None


def _tax_rate(stock):
    rows = list(Holding.objects.filter(stock=stock).values_list('account', 'quantity'))
    total = sum(q for _, q in rows) or 0
    nisa = sum(q for a, q in rows if a in NISA_ACCOUNTS)
    ratio = (nisa / total) if total else 0.0
    c = stock.country if stock.country in ('JP', 'US') else 'US'
    return ratio * TAX[(c, True)] + (1 - ratio) * TAX[(c, False)], ratio


def _status(rec, today, country):
    if rec.ex_date > today:
        return 'upcoming', '予定'
    if rec.pay_date:
        return ('paid', '支払済み') if rec.pay_date <= today else ('pending', f'{rec.pay_date.month}/{rec.pay_date.day} 入金予定')
    lag = PAY_LAG_DAYS.get(country, 30)
    if (today - rec.ex_date).days > lag:
        return 'paid', '支払済み（推定）'
    est = rec.ex_date + timedelta(days=lag)
    return 'pending', f'{est.month}/{est.day}頃 入金（推定）'


def _pay_month(rec, country):
    """入金月（支払日が無ければ権利落ち日＋目安日数）"""
    d = rec.pay_date or (rec.ex_date + timedelta(days=PAY_LAG_DAYS.get(country, 30)))
    return d.year, d.month


def build(rows, fx_rate, today=None):
    """rows = current_stock_holdings の行。配当狙いだけを対象に画面用データを返す"""
    today = today or date.today()
    targets = [r for r in rows if r['stock'] and r.get('style') == DIV_STYLE and r['quantity'] > 0]
    recs_by = defaultdict(list)
    for d in DividendRecord.objects.filter(stock__in=[r['stock'] for r in targets]).order_by('ex_date'):
        recs_by[d.stock_id].append(d)

    items, events = [], []
    month_net = defaultdict(float)          # (年, 月) → 税引後・円
    year_paid_net = 0.0
    for r in targets:
        s, qty, cost = r['stock'], r['quantity'], r['avg_cost']
        is_us = s.country == 'US'
        rate = fx_rate if (is_us and fx_rate) else 1.0
        tax, nisa_ratio = _tax_rate(s)
        recs = recs_by.get(s.code, [])
        past = [d for d in recs if d.ex_date <= today and d.amount]
        # 金額未定の予定は、いつもの間隔と合わないもの（calendar の誤り）を捨てる
        future = [d for d in recs if d.ex_date > today
                  and (d.amount or plausible_next(d.ex_date, [p.ex_date for p in past]))]
        recs = [d for d in recs if d.ex_date <= today or d in future]
        last = past[-1] if past else None
        ttm = [d for d in past if d.ex_date > today - timedelta(days=365)]
        dps = sum(d.amount for d in ttm)            # 直近12か月の1株配当
        freq = len(ttm)
        # 増配・減配: 直近の1回と、約1年前の同じ回
        change = None
        if last:
            prev = [d for d in past if 330 <= (last.ex_date - d.ex_date).days <= 400]
            if prev and prev[-1].amount:
                change = (last.amount / prev[-1].amount - 1) * 100
        # 次回の権利落ち: calendar の予定 → 無ければ1年前の同じ回から推定
        nxt, nxt_est = (future[0], False) if future else (None, False)
        nxt_date = nxt.ex_date if nxt else None
        if not nxt_date and last and freq:
            nxt_date = _estimate_next(past, today) or (last.ex_date + timedelta(days=round(365 / freq)))
            nxt_est = True
        nxt_amount = (nxt.amount if (nxt and nxt.amount) else (last.amount if last else None))
        annual_gross = dps * qty                    # 取引通貨
        annual_net_jpy = annual_gross * rate * (1 - tax)
        close = s.close
        hist = []
        for d in reversed(recs[-10:]):
            code, label = _status(d, today, s.country)
            amt = d.amount if d.amount else nxt_amount
            gross = (amt or 0) * qty
            hist.append({'rec': d, 'status': code, 'label': label, 'amount': amt, 'estimated_amount': not d.amount,
                         'gross': gross, 'net_jpy': gross * rate * (1 - tax)})
            if code == 'paid' and d.amount:
                y, m = _pay_month(d, s.country)
                if y == today.year:
                    year_paid_net += gross * rate * (1 - tax)
        # 今後12か月の入金見込み（直近12か月の配当を1年後にずらす＋確定している予定）
        for d in ttm:
            y, m = _pay_month(d, s.country)
            month_net[(y + 1, m)] += d.amount * qty * rate * (1 - tax)
        for d in recs:
            code, label = _status(d, today, s.country)
            if code == 'pending':
                y, m = _pay_month(d, s.country)
                month_net[(y, m)] += (d.amount or 0) * qty * rate * (1 - tax)
                pay = d.pay_date or (d.ex_date + timedelta(days=PAY_LAG_DAYS.get(s.country, 30)))
                events.append({'date': pay, 'kind': 'pay', 'stock': s, 'label': '入金' + ('' if d.pay_date else '（推定）'),
                               'amount': (d.amount or 0) * qty, 'is_us': is_us,
                               'net_jpy': (d.amount or 0) * qty * rate * (1 - tax)})
        if nxt_date and nxt_date >= today:
            events.append({'date': nxt_date, 'kind': 'ex', 'stock': s,
                           'label': '権利落ち' + ('（推定）' if nxt_est else ''),
                           'amount': (nxt_amount or 0) * qty, 'is_us': is_us,
                           'net_jpy': (nxt_amount or 0) * qty * rate * (1 - tax),
                           'last_day': nxt_date - timedelta(days=1)})
        items.append({
            'stock': s, 'qty': qty, 'cost': cost, 'close': close, 'is_us': is_us,
            'tax': tax, 'nisa': nisa_ratio >= 0.999, 'nisa_ratio': nisa_ratio,
            'dps': dps, 'freq': freq, 'last': last, 'change': change,
            'next_date': nxt_date, 'next_est': nxt_est, 'next_amount': nxt_amount,
            'annual_gross': annual_gross, 'annual_net_jpy': annual_net_jpy,
            'yield_now': (dps / close * 100) if (close and dps) else None,
            'yield_cost': (dps / cost * 100) if (cost and dps) else None,
            'hist': hist, 'no_data': not recs,
        })
    # 並びは取得単価ベースの利回りが高い順（ユーザー指示 2026-09-27）。配当データが無い銘柄は末尾
    items.sort(key=lambda x: -(x['yield_cost'] or -1))
    events = sorted([e for e in events if today <= e['date'] <= today + timedelta(days=120)], key=lambda e: e['date'])
    # 月別（今月から12か月）
    months = []
    y, m = today.year, today.month
    for _ in range(12):
        months.append({'label': f'{m}月', 'year': y, 'net': month_net.get((y, m), 0.0)})
        m += 1
        if m > 12:
            y, m = y + 1, 1
    mx = max([x['net'] for x in months] + [1])
    for x in months:
        x['pct'] = x['net'] / mx * 100
    total_net = sum(i['annual_net_jpy'] for i in items)
    cost_jpy = sum(i['qty'] * i['cost'] * (fx_rate if (i['is_us'] and fx_rate) else 1) for i in items)
    value_jpy = sum(i['qty'] * (i['close'] or 0) * (fx_rate if (i['is_us'] and fx_rate) else 1) for i in items)
    total_gross_jpy = sum(i['annual_gross'] * (fx_rate if (i['is_us'] and fx_rate) else 1) for i in items)
    return {
        'items': items, 'events': events, 'months': months,
        'total_net_jpy': total_net, 'total_gross_jpy': total_gross_jpy, 'monthly_net_jpy': total_net / 12,
        'year_paid_net': year_paid_net, 'year': today.year,
        'yield_cost': (total_gross_jpy / cost_jpy * 100) if cost_jpy else None,
        'yield_now': (total_gross_jpy / value_jpy * 100) if value_jpy else None,
        'next_event': next((e for e in events if e['kind'] == 'ex'), None),
        'fx_rate': fx_rate, 'today': today,
    }
