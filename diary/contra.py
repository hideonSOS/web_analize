"""短期トレード（Trade）の計算: 建玉サイズ・保有中の状態・成績（2026-09-09）。

ルール（ユーザーと合意済み・申し送り「短期トレードのルール設計」）:
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

from .models import ContraSetting, PracticeMeta, Trade, TradeBar, TradeNote

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
    # 「何勝何敗でトントンか」を回数で示す（ユーザー要望 2026-09-09: 忘れないように明記）。
    # 10回・20回あたりの分岐勝ち数は切り上げ（それ未満の勝ち数なら損）
    import math as _m
    def need(n):
        return _m.ceil(n * with_cost - 1e-9)
    examples = []
    for n in (5, 10, 20):
        w = need(n)
        examples.append({'n': n, 'win': w, 'lose': n - w,
                         'pnl': round(w * win - (n - w) * lose, 1),          # その勝敗での累積%
                         'pnl_minus1': round((w - 1) * win - (n - w + 1) * lose, 1)})
    return {'nominal': nominal * 100, 'with_cost': with_cost * 100, 'win': win, 'lose': lose,
            'ratio': (win / lose) if lose else None,   # 1勝で何敗ぶん取り返せるか
            'examples': examples}


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


# --- 売買日記との連動（入力は日記に統一・ユーザー決定 2026-09-09） ----------------------
def open_from_entry(entry, setting: ContraSetting, risk_scenario: str = '') -> Trade | None:
    """日記の「買い」から短期取引を起こす（チェック「短期トレードとして追跡」）。

    損切り/利確は日記に入れた価格から%を逆算。無ければ設定の既定%で線を引く。
    取引を起こしたら日足を即取る（失敗しても取引は残す。画面の「日足を更新」で取り直せる）
    """
    from django.core.management import call_command
    if entry.action != 'buy' or not entry.price or not entry.shares or entry.stock is None:
        return None
    if entry.trade_id:
        return entry.trade
    price = float(entry.price)
    # ⚠️ ルールは固定（ユーザー決定 2026-09-09）: 追跡対象にした時点で必ず設定の既定
    # （利確 +10% / 損切り −5%）で線を引く。日記に入れた目標・損切り価格は使わない。
    # 日記側の出口計画もルールの価格に揃える（画面の「目標」「損切り」が帳簿と食い違わないように）
    stop_pct = setting.default_stop_pct
    target_pct = setting.default_target_pct
    entry.stop_price = round(price * (1 - stop_pct / 100), 4)
    entry.target_price = round(price * (1 + target_pct / 100), 4)
    entry.save(update_fields=['stop_price', 'target_price'])
    limit = max_shares(setting, price, stop_pct)
    t = Trade.objects.create(
        stock=entry.stock, stock_name=entry.stock_name, ticker=entry.stock.display_code,
        country=entry.stock.country, currency='USD' if entry.stock.country == 'US' else 'JPY',
        strategy='contra', entry_date=entry.recorded_at.date(), entry_price=price, shares=int(entry.shares),
        stop_pct=round(stop_pct, 2), target_pct=round(target_pct, 2),
        stop_price=round(price * (1 - stop_pct / 100), 4), target_price=round(price * (1 + target_pct / 100), 4),
        entry_note=entry.reason, over_risk=int(entry.shares) > limit['shares'], entry_diary=entry,
        risk_scenario=risk_scenario,
    )
    t.risk_jpy = plan_risk(t)
    t.save(update_fields=['risk_jpy'])
    entry.strategy, entry.trade = 'contra', t
    tags = [x for x in entry.tags.split(',') if x]
    if '短期' not in tags:
        tags.append('短期')          # 日記の一覧で短期トレードと分かるように
    entry.tags = ','.join(tags)
    entry.save(update_fields=['strategy', 'trade', 'tags'])
    try:
        call_command('update_trade_bars', trade=t.id)
    except Exception:   # noqa: BLE001
        pass
    return t


def auto_exit_reason(trade: Trade, price: float) -> str:
    """決済価格から理由を推定。
    損切り線以下=損切り／利確線以上=利確／建値より上で利確線未満=早期利確／それ以外（損失側の裁量）=裁量"""
    if price <= trade.stop_price:
        return 'stop'
    if price >= trade.target_price:
        return 'target'
    if price > trade.entry_price:
        return 'early'
    return 'manual'


def close_from_entry(entry, reason: str = '', expected: str = '') -> Trade | None:
    """日記の「売り」で、同じ銘柄の保有中の短期取引を決済する（古いものから1件）"""
    if entry.action != 'sell' or not entry.price or entry.stock is None:
        return None
    t = (Trade.objects.filter(stock=entry.stock, exit_date__isnull=True, strategy='contra')
         .order_by('entry_date').first())
    if t is None:
        return None
    price = float(entry.price)
    t.exit_date, t.exit_price = entry.recorded_at.date(), price
    t.exit_reason = reason if reason in dict(Trade.EXIT) else auto_exit_reason(t, price)
    t.exit_note, t.exit_diary = entry.reason, entry
    t.exit_expected = expected if expected in dict(Trade.EXPECTED) else ''
    t.save()
    entry.strategy, entry.trade = 'contra', t
    entry.save(update_fields=['strategy', 'trade'])
    return t


# --- 練習（仮想トレード・2026-09-10）。日記とはつながず、練習ページのフォームから直接起こす ----
def open_practice(setting: ContraSetting, stock, price: float, shares: int, entry_date, reason: str,
                  tags: str = '', mood: str = '', risk_scenario: str = '') -> Trade:
    """「買ったつもり」の取引を起こす。ルール（損切り/利確の%）は練習用の設定から"""
    from django.core.management import call_command
    stop_pct, target_pct = setting.default_stop_pct, setting.default_target_pct
    limit = max_shares(setting, price, stop_pct)
    t = Trade.objects.create(
        stock=stock, stock_name=stock.name, ticker=stock.display_code, country=stock.country,
        currency='USD' if stock.country == 'US' else 'JPY', strategy='practice',
        entry_date=entry_date, entry_price=price, shares=int(shares),
        stop_pct=round(stop_pct, 2), target_pct=round(target_pct, 2),
        stop_price=round(price * (1 - stop_pct / 100), 4), target_price=round(price * (1 + target_pct / 100), 4),
        entry_note=reason, over_risk=int(shares) > limit['shares'], risk_scenario=risk_scenario,
    )
    t.risk_jpy = plan_risk(t)
    # 練習はタグ・心理を日記に持たないので exit_note の手前に置く（振り返り用）
    t.exit_note = ''
    t.save(update_fields=['risk_jpy'])
    if tags or mood:
        PracticeMeta.objects.update_or_create(trade=t, defaults={'tags': tags, 'mood': mood})
    try:
        call_command('update_trade_bars', trade=t.id)
    except Exception:   # noqa: BLE001
        pass
    return t


def close_trade(t: Trade, price: float, exit_date, reason: str = '', note: str = '', expected: str = '') -> Trade:
    """取引を決済する（練習ページの決済フォーム用。理由が空なら価格から推定）"""
    t.exit_date, t.exit_price = exit_date, price
    t.exit_reason = reason if reason in dict(Trade.EXIT) else auto_exit_reason(t, price)
    t.exit_note = note
    t.exit_expected = expected if expected in dict(Trade.EXPECTED) else ''
    t.save()
    return t


def add_note(t: Trade, text: str, kind: str = '') -> TradeNote | None:
    """保有中のコメントを追記（空なら何もしない）。kind は good（好材料）/bad（悪材料）"""
    text = (text or '').strip()
    if not text:
        return None
    return TradeNote.objects.create(trade=t, text=text, kind=kind if kind in dict(TradeNote.KINDS) else '')


AFTER_EXIT_DAYS = 30     # 売却後に日足を追い続ける日数（update_trade_bars も同じ値を見る）


def after_exit_rows(strategy: str = 'contra', limit: int = 30, today: date | None = None) -> list[dict]:
    """売却した取引の「その後」（ユーザー要望 2026-09-10: 損切り・利確に関わらず追跡して学ぶ）。

    売却日より後の日足から 現在値・売却後の高値/安値・売却価格からの騰落 を出し、
    「損切り後に反発した」「利確後さらに上げた」などの一言を付ける。日足は売却後 AFTER_EXIT_DAYS 日まで
    update_trade_bars が取り続ける（それ以降は最後に取れた値のまま）
    """
    today = today or date.today()
    rows = []
    qs = Trade.objects.filter(strategy=strategy, exit_date__isnull=False).order_by('-exit_date', '-id')[:limit]
    for t in qs:
        after = list(t.bars.filter(date__gt=t.exit_date).order_by('date').values('date', 'high', 'low', 'close'))
        last = after[-1] if after else None
        cur = last['close'] if last else None
        hi = max(b['high'] for b in after) if after else None
        lo = min(b['low'] for b in after) if after else None
        chg = (cur / t.exit_price - 1) * 100 if cur and t.exit_price else None
        hi_chg = (hi / t.exit_price - 1) * 100 if hi and t.exit_price else None
        lo_chg = (lo / t.exit_price - 1) * 100 if lo and t.exit_price else None
        # 一言（学び）: 売った判断がどう転んだか
        word, tone = '', ''
        if chg is not None:
            if t.exit_reason == 'stop':
                if hi_chg is not None and hi_chg >= t.stop_pct:
                    word, tone = f'損切り後に反発（高値 +{hi_chg:.1f}%）。切り所が早かったか', 'warn'
                elif chg <= -t.stop_pct / 2:
                    word, tone = f'損切り後さらに下落（{chg:+.1f}%）。切って正解', 'ok'
                else:
                    word, tone = f'損切り後は横ばい（{chg:+.1f}%）', ''
            elif t.exit_reason in ('target', 'early'):
                if hi_chg is not None and hi_chg >= 5:
                    word, tone = f'売却後さらに上昇（高値 +{hi_chg:.1f}%）。伸ばせた', 'warn'
                elif lo_chg is not None and lo_chg <= -5:
                    word, tone = f'売却後に反落（安値 {lo_chg:+.1f}%）。降りて正解', 'ok'
                else:
                    word, tone = f'売却後は小動き（{chg:+.1f}%）', ''
            else:
                word = f'売却後 {chg:+.1f}%'
        rows.append({
            't': t, 'cur': cur, 'cur_date': last['date'] if last else None,
            'chg': chg, 'hi': hi, 'lo': lo, 'hi_chg': hi_chg, 'lo_chg': lo_chg,
            'days_after': (today - t.exit_date).days, 'tracking': (today - t.exit_date).days <= AFTER_EXIT_DAYS,
            'word': word, 'tone': tone,
            'net': t.pnl_pct_net(ContraSetting.get('practice' if strategy == 'practice' else 'contra').cost_pct),
            'unit': '$' if t.currency == 'USD' else '円',
        })
    return rows


def reasons_of(t: Trade) -> list[str]:
    """なぜ買ったか（1行1理由）。旧データの長文は1要素になる"""
    return [x.strip() for x in (t.entry_note or '').splitlines() if x.strip()]


def untrack(entry) -> bool:
    """追跡をやめる（未決済の取引だけ）。日記の行は残す"""
    t = entry.trade
    if t is None or t.exit_date is not None:
        return False
    entry.trade, entry.strategy = None, ''
    entry.tags = ','.join(x for x in entry.tags.split(',') if x and x != '短期')
    entry.save(update_fields=['trade', 'strategy', 'tags'])
    t.delete()
    return True


def _ticks(stop_pct: float, target_pct: float) -> list[dict]:
    """レンジバーの目盛り。位置は 損切り線=0% 〜 利確線=100%。
    主目盛り: 損切り／0（建値）／利確の半分／利確。副目盛り: 損切りの半分／利確の 1/4・3/4"""
    span = stop_pct + target_pct
    if span <= 0:
        return []
    def pos(pct):   # 建値からの% → バー上の位置%
        return (pct + stop_pct) / span * 100
    def lab(pct):
        return '0' if pct == 0 else (f'+{pct:g}%' if pct > 0 else f'−{-pct:g}%')
    items = [(-stop_pct, 'stop'), (-stop_pct / 2, 'minor'), (0, 'entry'),
             (target_pct / 4, 'minor'), (target_pct / 2, 'half'), (target_pct * 3 / 4, 'minor'), (target_pct, 'target')]
    return [{'pos': pos(p), 'label': lab(p), 'kind': k} for p, k in items]


def open_rows(setting: ContraSetting, today: date | None = None, strategy: str = 'contra') -> list[dict]:
    """保有中の取引を UI 用に。現在値・損切り/利確までの距離・ザラ場で触れたか・経過日数"""
    today = today or date.today()
    rows = []
    for t in (Trade.objects.filter(exit_date__isnull=True, strategy=strategy)
              .select_related('stock').order_by('entry_date')):
        bars = list(t.bars.order_by('date').values('date', 'open', 'high', 'low', 'close'))
        last = bars[-1] if bars else None
        cur = last['close'] if last else None
        # ⚠️ 建てた当日は日足がまだ無い（米国の引け前）。現在値マーカーが消えて「反映されていない」と
        # 見えた実例（2026-09-10 GOOG）。日足が無いときは株価マスタの終値（Stock.close）で代用する
        fallback = False
        if cur is None and t.stock is not None and t.stock.close:
            cur = float(t.stock.close)
            last = {'date': t.stock.price_date, 'high': cur, 'low': cur, 'close': cur}
            fallback = True
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
            # 目盛り（ユーザー要望 2026-09-10）: 損切り線／建値(0)／利確の半分／利確線 の位置とラベル。
            # 「半分で降りるか」「＋に動いたときの達成率」を見るため。位置は損切り線〜利確線を 0〜100% として
            # 刻みは 損切り／その半分／0／利確の 1/4・1/2・3/4／利確（ユーザー要望: −2.5・+2.5・+7.5 も）
            'ticks': _ticks(t.stop_pct, t.target_pct),
            # 達成率: 利確幅に対して今どこまで来たか（＋なら利確までの進み、−なら損切りへの進み）
            'progress': (change / t.target_pct * 100) if change is not None and change >= 0 and t.target_pct else None,
            'drawdown': (-change / t.stop_pct * 100) if change is not None and change < 0 and t.stop_pct else None,
            'days': (today - t.entry_date).days,
            'bars_n': len(bars),
            'risk': plan_risk(t),
            'reasons': reasons_of(t),
            'notes': list(t.notes.all()[:5]), 'notes_n': t.notes.count(),
            'unit': '$' if t.currency == 'USD' else '円',
            'stale': (last is None or last['date'] is None) or (today - last['date']).days > 4,
            'fallback': fallback,      # 株価マスタの終値で代用中（日足が来れば自動で切り替わる）
        })
    # 触れたものを先頭に（今日やることが上に来る）
    rows.sort(key=lambda r: (not (r['touched_stop'] or r['touched_target']), r['t'].entry_date))
    return rows


def stats(setting: ContraSetting, strategy: str = 'contra') -> dict:
    """決済済み（短期）の成績。

    勝率は**ルール決済（利確・損切り）だけ**で数える（ユーザー決定 2026-09-09）。
    +10% に届く前に +5〜6% で降りた「早期利確」は、+10% を前提にした分岐勝率と競合するので
    勝ち負けの数には入れない。ただし損益はトータルに積算する。裁量・期限も同じ扱い
    """
    c = setting.cost_pct
    closed = list(Trade.objects.filter(strategy=strategy, exit_date__isnull=False).order_by('exit_date', 'id'))
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
        # ⚠️ 勝率に入れるのはルール決済だけ（利確線に達した／損切り線で切った）
        if t.exit_reason in ('target', 'stop'):
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
    rule_n = wins + losses                       # 勝率の分母＝ルール決済の件数
    win_rate = wins / rule_n * 100 if rule_n else None
    be = breakeven(setting.default_stop_pct, setting.default_target_pct, c)

    # --- トータル（ユーザー要望 2026-09-09: 利確と損切りの＋−を積算して表示） ---------------
    # % は各取引のコスト込み損益の単純合計。金額は 取引の通貨のまま（為替は考えない方針）で
    # コスト＝約定金額×片道%を買い・売りの両方で引く。理由別（利確／損切り／裁量・期限）にも分ける
    def _amount_net(t):
        gross = (t.exit_price - t.entry_price) * t.shares
        cost = (t.entry_price + t.exit_price) * t.shares * c / 100
        return gross - cost
    total = {'pct': 0.0, 'amount': 0.0, 'n': n,
             'target': {'pct': 0.0, 'amount': 0.0, 'n': 0},
             'stop': {'pct': 0.0, 'amount': 0.0, 'n': 0},
             'early': {'pct': 0.0, 'amount': 0.0, 'n': 0},
             'other': {'pct': 0.0, 'amount': 0.0, 'n': 0}}
    for t in closed:
        net = t.pnl_pct_net(c)
        amt = _amount_net(t)
        key = t.exit_reason if t.exit_reason in ('target', 'stop', 'early') else 'other'
        total['pct'] += net
        total['amount'] += amt
        total[key]['pct'] += net
        total[key]['amount'] += amt
        total[key]['n'] += 1
    total['unit'] = '$'   # 米国株前提（日本株が混ざると通貨が混ざる。混ざったら分けて出すこと）
    avg_win = sum(win_pcts) / len(win_pcts) if win_pcts else 0
    avg_loss = sum(loss_pcts) / len(loss_pcts) if loss_pcts else 0
    expectancy = (sum(win_pcts) + sum(loss_pcts)) / rule_n if rule_n else None

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
    cond1 = None if rule_n < EXPENSE_MIN_N else (win_rate > be['with_cost'])
    cond2 = not big_losses
    # 期待値（コスト込み・1取引あたり）がプラスかも併記。①と同じ意味だが金額の重みが入る
    verdict = ('pending' if cond1 is None else
               'ok' if (cond1 and cond2) else 'ng')
    expense = {
        'verdict': verdict,                       # pending / ok / ng
        'min_n': EXPENSE_MIN_N, 'need_more': max(0, EXPENSE_MIN_N - rule_n),
        'rule_n': rule_n, 'early': by_reason['early'],
        'cond1': cond1, 'cond2': cond2,
        'win_rate': win_rate, 'breakeven': be['with_cost'],
        'stops': stops, 'stops_pct': by_reason['stop']['sum'],   # 損切りの回数と合計%（=経費の総額）
        'big_losses': big_losses, 'limit_amt': limit_amt, 'over_risk_n': over_risk_n,
        'manual': by_reason['manual']['n'],
        # いま何勝何敗で、分岐勝率に対してあと何敗まで許されるか（1勝あたり）
        'allowed_losses': (be['win'] / be['lose']) if be['lose'] else None,
        'losses_per_win': (losses / wins) if wins else None,
    }
    # --- 振り返り用の一覧（ユーザー要望 2026-09-09: 損切り／利確ごとに銘柄と判断理由を並べ、
    #     自分の癖を客観視する）。判断理由・タグ・心理はエントリー時の日記から取る
    def _reflect_row(t, net):
        e = t.entry_diary
        meta = getattr(t, 'practice_meta', None) if t.strategy == 'practice' else None
        return {
            't': t, 'net': net,
            'reason': (e.reason if e else t.entry_note) or '',
            'tags': [x for x in ((e.tags if e else (meta.tags if meta else ''))).split(',') if x and x != '短期'],
            'mood': e.mood if e else (meta.mood if meta else ''),
            'exit_note': t.exit_note,
            'reasons': reasons_of(t) if not e else [x.strip() for x in e.reason.splitlines() if x.strip()],
            'risk_scenario': t.risk_scenario,
            'expected': t.exit_expected,
            'notes': list(t.notes.all()),
            'unit': '$' if t.currency == 'USD' else '円',
        }
    reflect = {'stop': [], 'target': [], 'other': []}
    tag_stats = {}
    mood_stats = {}
    for r in rows:
        t = r['t']
        rr = _reflect_row(t, r['net'])
        key = t.exit_reason if t.exit_reason in ('stop', 'target') else ('target' if t.exit_reason == 'early' else 'other')
        reflect[key].append(rr)
        for tag in rr['tags']:
            d = tag_stats.setdefault(tag, {'tag': tag, 'wins': 0, 'losses': 0, 'sum': 0.0})
            d['wins' if r['win'] else 'losses'] += 1
            d['sum'] += r['net']
        if rr['mood']:
            d = mood_stats.setdefault(rr['mood'], {'mood': rr['mood'], 'wins': 0, 'losses': 0, 'sum': 0.0})
            d['wins' if r['win'] else 'losses'] += 1
            d['sum'] += r['net']
    for k in reflect:
        reflect[k].reverse()          # 新しい順
    reflect['tags'] = sorted(tag_stats.values(), key=lambda d: -(d['wins'] + d['losses']))
    reflect['moods'] = sorted(mood_stats.values(), key=lambda d: -(d['wins'] + d['losses']))

    return {
        'total': total,
        'reflect': reflect,
        'expense': expense,
        'n': n, 'rule_n': rule_n, 'wins': wins, 'losses': losses, 'win_rate': win_rate,
        'breakeven': be, 'above_breakeven': (win_rate is not None and win_rate > be['with_cost']),
        'expectancy': expectancy, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'max_loss_streak': max_streak,
        'manual': by_reason['manual'], 'by_reason': by_reason,
        'rows': list(reversed(rows)), 'curve': curve,
        # 分岐勝率から逆算した「許される負けの数（1勝あたり）」
        'allowed_losses': (be['win'] / be['lose']) if be['lose'] else None,
    }
