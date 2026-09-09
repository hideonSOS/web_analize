"""逆張りトレード（Trade）の計算: 建玉サイズ・保有中の状態・成績（2026-09-09）。

ルール（ユーザーと合意済み・申し送り「逆張りトレードのルール設計」）:
- 底は当てない。損切り・利確ラインをエントリー時に決め、損切りは経費として扱う
- 経費として成立する条件: 期待値がプラス（勝率 > 分岐勝率）、1回の損失が資金の1〜2%以内
- 「今回は特別に損切りを見送る」は裁量介入。記録して回数を監視する
- 分岐勝率 = 損切り幅 ÷（損切り幅 + 利確幅）。コスト込みは 勝ち=利確−2c、負け=損切り+2c
- 判定は**ザラ場**（日足の安値/高値）。指値を入れる運用なので終値は合わない（ユーザー決定）
- 資金は手入力（遊び・学習目的）。ポートフォリオとは連動させない（ユーザー決定）
- **為替は考えない**（ユーザー決定）。一度ドルに替えたら円に戻さず運用する。資金・損益・
  リスクはすべて取引の通貨のまま。戦略の精度だけを見る
"""
from __future__ import annotations

import math
from datetime import date

from .models import ContraSetting, Trade, TradeBar

# 「損切りは経費」の判定に必要な最低件数。これ未満は勝率が偶然で大きく振れるので断定しない
# （10件で勝率±15pt程度は普通に動く。分岐勝率37%との差が出るまで待つ）
EXPENSE_MIN_N = 10


def latest_fx() -> tuple[date | None, float]:
    """ドル円の最新終値（portfolio.FxRate）。無ければ 150 を仮置き"""
    try:
        from portfolio.models import FxRate
        r = FxRate.objects.filter(pair='USDJPY').order_by('-date').values_list('date', 'rate').first()
        if r:
            return r[0], float(r[1])
    except Exception:   # noqa: BLE001
        pass
    return None, 150.0


def breakeven(stop_pct: float, target_pct: float, cost_pct: float) -> dict:
    """分岐勝率（名目・コスト込み）"""
    nominal = stop_pct / (stop_pct + target_pct) if stop_pct + target_pct else 0
    lose = stop_pct + 2 * cost_pct
    win = target_pct - 2 * cost_pct
    with_cost = lose / (lose + win) if lose + win > 0 else 0
    return {'nominal': nominal * 100, 'with_cost': with_cost * 100, 'win': win, 'lose': lose}


def max_shares(setting: ContraSetting, price: float, stop_pct: float) -> dict:
    """1〜2%ルールから許容株数を逆算。損失 = 株数 × 価格 × 損切り幅（取引の通貨のまま）"""
    if not price or not stop_pct:
        return {'shares': 0, 'risk_budget': 0, 'per_share': 0}
    per_share = price * stop_pct / 100
    budget = setting.capital * setting.risk_pct / 100
    return {'shares': int(math.floor(budget / per_share)) if per_share > 0 else 0,
            'risk_budget': budget, 'per_share': per_share}


def plan_risk(trade: Trade) -> float:
    return trade.shares * (trade.entry_price - trade.stop_price)


def open_rows(setting: ContraSetting, today: date | None = None) -> list[dict]:
    """保有中の取引を UI 用に。現在値・損切り/利確までの距離・ザラ場で触れたか・経過日数"""
    today = today or date.today()
    rows = []
    for t in Trade.objects.filter(exit_date__isnull=True).select_related('stock').order_by('entry_date'):
        bars = list(t.bars.order_by('date').values('date', 'open', 'high', 'low', 'close'))
        last = bars[-1] if bars else None
        cur = last['close'] if last else None
        hi = max(b['high'] for b in bars) if bars else None
        lo = min(b['low'] for b in bars) if bars else None
        touched_stop = bool(bars) and lo <= t.stop_price
        touched_target = bool(bars) and hi >= t.target_price
        span = t.target_price - t.stop_price
        pos = None
        if cur is not None and span > 0:
            pos = max(0.0, min(1.0, (cur - t.stop_price) / span)) * 100
        entry_pos = (t.entry_price - t.stop_price) / span * 100 if span > 0 else 50
        change = (cur / t.entry_price - 1) * 100 if cur else None
        rows.append({
            't': t,
            'cur': cur, 'cur_date': last['date'] if last else None,
            'last_low': last['low'] if last else None, 'last_high': last['high'] if last else None,
            'change': change,
            'pnl_now': (cur - t.entry_price) * t.shares if cur else None,
            'to_stop': (cur / t.stop_price - 1) * 100 if cur else None,     # 損切りまでの余裕（%）
            'to_target': (t.target_price / cur - 1) * 100 if cur else None,  # 利確までの距離（%）
            'hi': hi, 'lo': lo,
            'touched_stop': touched_stop, 'touched_target': touched_target,
            'pos': pos, 'entry_pos': entry_pos,
            'days': (today - t.entry_date).days,
            'bars_n': len(bars),
            'risk': plan_risk(t),
            'unit': '$' if t.currency == 'USD' else '円',
            'stale': (last is None) or (today - last['date']).days > 4,
        })
    # 触れたものを先頭に（今日やることが上に来る）
    rows.sort(key=lambda r: (not (r['touched_stop'] or r['touched_target']), r['t'].entry_date))
    return rows


def stats(setting: ContraSetting) -> dict:
    """決済済み（逆張り）の成績。勝ち＝コスト込みで損益がプラス"""
    c = setting.cost_pct
    closed = list(Trade.objects.filter(strategy='contra', exit_date__isnull=False).order_by('exit_date', 'id'))
    rows, cum, curve = [], 0.0, []
    wins = losses = 0
    streak = max_streak = 0
    by_reason = {k: {'n': 0, 'sum': 0.0} for k, _ in Trade.EXIT}
    win_pcts, loss_pcts = [], []
    for t in closed:
        net = t.pnl_pct_net(c)
        cum += net
        curve.append([t.exit_date.strftime('%Y-%m-%d'), round(cum, 2)])
        is_win = net > 0
        wins += is_win
        losses += (not is_win)
        (win_pcts if is_win else loss_pcts).append(net)
        streak = streak + 1 if not is_win else 0
        max_streak = max(max_streak, streak)
        if t.exit_reason in by_reason:
            by_reason[t.exit_reason]['n'] += 1
            by_reason[t.exit_reason]['sum'] += net
        rows.append({'t': t, 'net': net, 'gross': t.pnl_pct, 'amount': t.pnl_amount,
                     'win': is_win, 'unit': '$' if t.currency == 'USD' else '円'})
    n = len(closed)
    win_rate = wins / n * 100 if n else None
    be = breakeven(setting.default_stop_pct, setting.default_target_pct, c)
    avg_win = sum(win_pcts) / len(win_pcts) if win_pcts else 0
    avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0
    expectancy = (sum(win_pcts) + sum(loss_pcts)) / n if n else None

    # --- 損切りが「経費」として成立しているか（合意したルールの2条件） ---------------
    # ①勝率 > 分岐勝率（コスト込み） ②1回の損失が資金の risk_pct 以内
    # ①は件数が少ないと偶然で上下するので、MIN_N 件までは「判定中」とし、成立/不成立を断定しない
    limit_amt = setting.capital * setting.risk_pct / 100
    big_losses = []
    for t in closed:
        amt = t.pnl_amount
        if amt is not None and amt < 0 and -amt > limit_amt * 1.05:   # 5% はスリッページの許容
            big_losses.append({'t': t, 'amount': -amt})
    over_risk_n = sum(1 for t in closed if t.over_risk)
    stops = by_reason['stop']['n']
    cond1 = None if n < EXPENSE_MIN_N else (win_rate > be['with_cost'])
    cond2 = not big_losses
    # 期待値（コスト込み・1取引あたり）がプラスかも併記。①と同じ意味だが金額の重みが入る
    verdict = ('pending' if cond1 is None else
               'ok' if (cond1 and cond2) else 'ng')
    expense = {
        'verdict': verdict,                       # pending / ok / ng
        'min_n': EXPENSE_MIN_N, 'need_more': max(0, EXPENSE_MIN_N - n),
        'cond1': cond1, 'cond2': cond2,
        'win_rate': win_rate, 'breakeven': be['with_cost'],
        'stops': stops, 'stops_pct': by_reason['stop']['sum'],   # 損切りの回数と合計%（=経費の総額）
        'big_losses': big_losses, 'limit_amt': limit_amt, 'over_risk_n': over_risk_n,
        'manual': by_reason['manual']['n'],
        # いま何勝何敗で、分岐勝率に対してあと何敗まで許されるか（1勝あたり）
        'allowed_losses': (be['win'] / be['lose']) if be['lose'] else None,
        'losses_per_win': (losses / wins) if wins else None,
    }
    return {
        'expense': expense,
        'n': n, 'wins': wins, 'losses': losses, 'win_rate': win_rate,
        'breakeven': be, 'above_breakeven': (win_rate is not None and win_rate > be['with_cost']),
        'expectancy': expectancy, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'max_loss_streak': max_streak,
        'manual': by_reason['manual'], 'by_reason': by_reason,
        'rows': list(reversed(rows)), 'curve': curve,
        # 分岐勝率から逆算した「許される負けの数（1勝あたり）」
        'allowed_losses': (be['win'] / be['lose']) if be['lose'] else None,
    }
