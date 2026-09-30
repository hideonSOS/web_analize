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
# エントリー時に選べる利確率（2026-09-30 ユーザー指示: 15日で +10% は難しい銘柄もあるので +5%・+7% も選べるように。
# 選んだ率はその取引に固定し、後から変えない＝規律）。損切りは設定のまま
TARGET_CHOICES = (5, 7, 10)


def pick_target(setting: ContraSetting, value) -> float:
    """フォームの利確率 → TARGET_CHOICES のどれか。無効なら設定の既定"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return setting.default_target_pct
    return v if v in TARGET_CHOICES else setting.default_target_pct


def open_from_entry(entry, setting: ContraSetting, risk_scenario: str = '', target_pct=None) -> Trade | None:
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
    target_pct = pick_target(setting, target_pct)     # 利確率はエントリー時に選んだもの（+5/+7/+10）
    entry.stop_price = round(price * (1 - stop_pct / 100), 4)
    entry.target_price = round(price * (1 + target_pct / 100), 4)
    entry.save(update_fields=['stop_price', 'target_price'])
    limit = max_shares(setting, price, stop_pct)
    t = Trade.objects.create(
        stock=entry.stock, stock_name=entry.stock_name, ticker=entry.stock.display_code,
        country=entry.stock.country, currency='USD' if entry.stock.country == 'US' else 'JPY',
        strategy='contra', entry_date=entry.recorded_at.date(), entry_price=price, shares=int(entry.shares),
        stop_pct=round(stop_pct, 2), target_pct=round(target_pct, 2),
        be_trigger_pct=(setting.be_trigger_pct if 0 < (setting.be_trigger_pct or 0) < target_pct else None),
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
    when = _entry_when(t)
    for text in reasons_of(t):
        add_note(t, text, 'info', when)
    try:
        call_command('update_trade_bars', trade=t.id)
    except Exception:   # noqa: BLE001
        pass
    return t


# 指値の丸めの許容幅（2026-09-29）: 利確線・損切り線の 0.5% 以内で約定したら、その線で決済したとみなす。
# ⚠️ NVDA を利確線 $232.738 に対し $232.00 の指値で売ったら「早期利確」に分類され、勝率に入らず勝率が — のままだった
#   （ユーザー指摘）。指値は切りのいい価格で置くのが普通なので、線ちょうどを要求しない
LINE_TOLERANCE = 0.005


def auto_exit_reason(trade: Trade, price: float) -> str:
    """決済価格から理由を推定。
    損切り線以下=損切り／利確線以上=利確／建値より上で利確線未満=早期利確／それ以外（損失側の裁量）=裁量。
    線の判定は LINE_TOLERANCE（0.5%）の幅を持たせる（指値の丸め）。
    建値ストップ移動後（be_moved_at あり）は、建値付近（+1% 以内・ギャップで下回った場合も）＝建値撤退"""
    target_hit = price >= trade.target_price * (1 - LINE_TOLERANCE)
    if trade.be_moved_at:
        if target_hit:
            return 'target'
        if price <= trade.entry_price * 1.01:
            return 'breakeven'
        return 'early'
    if price <= trade.stop_price * (1 + LINE_TOLERANCE):
        return 'stop'
    if target_hit:
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
    if reason == 'stop' and t.be_moved_at:
        reason = ''    # 建値へ上げた後の「損切り」は建値撤退として価格から判定する
    t.exit_reason = reason if reason in dict(Trade.EXIT) else auto_exit_reason(t, price)
    # 日記の売りが「利益確定」なのに価格が損切り線以下（ルール外の売り）は裁量として残す
    if reason == '' and getattr(entry, 'sell_kind', '') == 'profit' and t.exit_reason == 'stop':
        t.exit_reason = 'manual'
    t.exit_note, t.exit_diary = entry.reason, entry
    t.exit_expected = expected if expected in dict(Trade.EXPECTED) else ''   # 日記の売りでは 2026-09-19 から未使用
    t.save()
    entry.strategy, entry.trade = 'contra', t
    entry.save(update_fields=['strategy', 'trade'])
    return t


# --- 練習（仮想トレード・2026-09-10）。日記とはつながず、練習ページのフォームから直接起こす ----
def open_practice(setting: ContraSetting, stock, price: float, shares: int, entry_date, reason: str,
                  tags: str = '', mood: str = '', risk_scenario: str = '', reasons=None,
                  chart_image: str = '', target_pct=None) -> Trade:
    """「買ったつもり」の取引を起こす。ルール（損切り/利確の%）は練習用の設定から"""
    from django.core.management import call_command
    stop_pct, target_pct = setting.default_stop_pct, pick_target(setting, target_pct)
    limit = max_shares(setting, price, stop_pct)
    t = Trade.objects.create(
        stock=stock, stock_name=stock.name, ticker=stock.display_code, country=stock.country,
        currency='USD' if stock.country == 'US' else 'JPY', strategy='practice',
        entry_date=entry_date, entry_price=price, shares=int(shares),
        stop_pct=round(stop_pct, 2), target_pct=round(target_pct, 2),
        be_trigger_pct=(setting.be_trigger_pct if 0 < (setting.be_trigger_pct or 0) < target_pct else None),
        stop_price=round(price * (1 - stop_pct / 100), 4), target_price=round(price * (1 + target_pct / 100), 4),
        entry_note=reason, over_risk=int(shares) > limit['shares'], risk_scenario=risk_scenario,
    )
    t.risk_jpy = plan_risk(t)
    # 練習はタグ・心理を日記に持たないので exit_note の手前に置く（振り返り用）
    t.exit_note = ''
    t.save(update_fields=['risk_jpy'])
    if tags or mood:
        PracticeMeta.objects.update_or_create(trade=t, defaults={'tags': tags, 'mood': mood})
    # 購入時の理由（種別付き）を時系列の先頭に置く。reasons=[(text, kind), ...]
    when = _entry_when(t)
    for text, kind in (reasons or [(x, 'info') for x in reasons_of(t)]):
        add_note(t, text, kind, when)
    if chart_image:
        add_note(t, '購入時のチャート', 'info', when, image=chart_image)
    try:
        call_command('update_trade_bars', trade=t.id)
    except Exception:   # noqa: BLE001
        pass
    return t


def move_to_breakeven(t: Trade, on=True) -> Trade:
    """「変更した」ボタン: 証券会社で逆指値を建値に変更したことを記録（on=False で取り消し）"""
    t.be_moved_at = date.today() if on else None
    t.save(update_fields=['be_moved_at'])
    return t


def breakeven_state(t: Trade, bars: list[dict], cur: float | None) -> dict | None:
    """建値ストップの状態。wait=まだ届いていない／reached=届いた（逆指値の変更待ち）／moved=変更済み。
    無効（be_trigger_pct 無し）なら None"""
    trig = t.be_trigger_pct
    if not trig or t.stop_pct + t.target_pct <= 0:
        return None
    price = t.entry_price * (1 + trig / 100)
    hit = next((b for b in bars if b['date'] and b['date'] >= t.entry_date and b['high'] is not None
                and b['high'] >= price), None)
    if t.be_moved_at:
        state = 'moved'
    elif hit or (cur is not None and cur >= price):
        state = 'reached'
    else:
        state = 'wait'
    # 変更後に安値が建値に触れたか（＝建値で手仕舞いされているはず）
    touched = bool(t.be_moved_at) and any(b['date'] and b['date'] >= t.be_moved_at and b['low'] is not None
                                          and b['low'] <= t.entry_price for b in bars)
    return {'pct': trig, 'price': price, 'state': state, 'hit_date': hit['date'] if hit else None,
            'hit_high': hit['high'] if hit else None, 'moved_at': t.be_moved_at, 'touched': touched,
            'pos': (trig + t.stop_pct) / (t.stop_pct + t.target_pct) * 100,
            'to_go': (price / cur - 1) * 100 if cur else None}


def close_trade(t: Trade, price: float, exit_date, reason: str = '', note: str = '', expected: str = '') -> Trade:
    """取引を決済する（練習ページの決済フォーム用。理由が空なら価格から推定）"""
    t.exit_date, t.exit_price = exit_date, price
    t.exit_reason = reason if reason in dict(Trade.EXIT) else auto_exit_reason(t, price)
    t.exit_note = note
    t.exit_expected = expected if expected in dict(Trade.EXPECTED) else ''
    t.save()
    return t


MAX_IMAGE_CHARS = 2_500_000     # data URL の上限（約1.8MB）。ブラウザ側で 1200px 幅・JPEG に縮小してから送る


def add_note(t: Trade, text: str, kind: str = '', when=None, image: str = '') -> TradeNote | None:
    """コメントを追記（文字も画像も無ければ何もしない）。kind は good/bad/info。when を渡すとその日時にする
    （購入時の理由を建て日で入れるため。auto_now_add なので create 後に update で書く）"""
    text = (text or '').strip()
    image = image if (image or '').startswith('data:image/') and len(image) <= MAX_IMAGE_CHARS else ''
    if not text and not image:
        return None
    n = TradeNote.objects.create(trade=t, text=text or 'チャート', kind=kind if kind in dict(TradeNote.KINDS) else 'feel',
                                 at_entry=when is not None, image=image)
    if when is not None:
        TradeNote.objects.filter(pk=n.pk).update(created_at=when)
        n.created_at = when
    return n


def timeline(t: Trade) -> list[TradeNote]:
    """購入時の理由と購入後のコメントを区別せず、古い順に並べる（ユーザー方針: 同じ土俵で評価）。
    購入時のスクリーンショット（at_entry かつ image）は別扱い（shots_of）なので含めない"""
    return [n for n in t.notes.order_by('-at_entry', 'created_at', 'id') if not (n.at_entry and n.image)]


def split_notes(notes: list) -> tuple[list, list]:
    """timeline を (購入時の想定, その後の心情) に分ける（2026-09-29 ユーザー指示: 想定は想定、チャートを見て
    心理がどう動いたかは別）。チャートの番号の印と心情の番号は「その後の心情」だけの通し番号"""
    return [n for n in notes if n.at_entry], [n for n in notes if not n.at_entry]


def shots_of(t: Trade) -> list[TradeNote]:
    """購入時のスクリーンショット（掘り下げの最上部に出す）"""
    return list(t.notes.filter(at_entry=True).exclude(image='').order_by('created_at', 'id'))


def add_shot(t: Trade, image: str) -> TradeNote | None:
    """購入時のスクリーンショットを後から保存する（購入時扱い・情報）。画像が無ければ何もしない"""
    if not (image or '').startswith('data:image/'):
        return None
    return add_note(t, '購入時のチャート', 'info', _entry_when(t), image=image)


def _entry_when(t: Trade):
    from datetime import datetime, time
    from django.utils import timezone
    return timezone.make_aware(datetime.combine(t.entry_date, time(0, 0)))


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


CANDLE_W, CANDLE_H = 1000, 100   # SVG の座標系（preserveAspectRatio=none で横に伸ばす）


def candles(t: Trade, bars: list[dict]) -> dict | None:
    """取得日からのローソク足（2026-09-24 ユーザー要望）。横軸は期限の線と同じ（左端=取得日・右端=
    TIME_LIMIT_DAYS）、縦軸は 損切り線〜利確線（はみ出した日があればそこまで広げる）。
    サーバー側で SVG の座標まで作る（JS なし）。
    期限（20日）を過ぎて持ち続けたら横軸を今日まで伸ばし、全部の足を描く（2026-09-29 ユーザー決定。以前は
    20日で止めていて、期限超過の値動きと心情が見えなかった）。20日の位置に赤い縦線・右側は薄い赤の背景（over）"""
    rows = [b for b in bars if b['date'] and b['date'] >= t.entry_date
            and None not in (b['open'], b['high'], b['low'], b['close'])]
    if not rows:
        return None
    ymax = max([t.target_price] + [b['high'] for b in rows])
    ymin = min([t.stop_price] + [b['low'] for b in rows])
    pad = (ymax - ymin) * 0.04 or 1
    ymax, ymin = ymax + pad, ymin - pad

    def y(p):
        return round((ymax - p) / (ymax - ymin) * CANDLE_H, 2)
    span = max(TIME_LIMIT_DAYS, max((b['date'] - t.entry_date).days for b in rows) + 1)
    step = CANDLE_W / span
    w = round(step * 0.6, 2)
    out = []
    for b in rows:
        d = (b['date'] - t.entry_date).days
        cx = round(d * step, 2)
        top, bot = max(b['open'], b['close']), min(b['open'], b['close'])
        out.append({'cx': cx, 'x': round(cx - w / 2, 2), 'w': w,
                    'hi': y(b['high']), 'lo': y(b['low']),
                    'body_y': y(top), 'body_h': max(0.8, round(y(bot) - y(top), 2)),
                    'up': b['close'] >= b['open'], 'date': b['date'],
                    'o': b['open'], 'h': b['high'], 'l': b['low'], 'c': b['close']})
    be_y = y(t.entry_price * (1 + t.be_trigger_pct / 100)) if t.be_trigger_pct else None
    over = span > TIME_LIMIT_DAYS
    return {'items': out, 'W': CANDLE_W, 'H': CANDLE_H, 'step': step, 'span': span,
            'entry_y': y(t.entry_price), 'stop_y': y(t.stop_price), 'target_y': y(t.target_price),
            'be_y': be_y, 'half_x': round(TIME_WARN_DAYS * step, 2),
            'over': over, 'limit_x': round(TIME_LIMIT_DAYS * step, 2) if over else None,
            'over_w': round(CANDLE_W - TIME_LIMIT_DAYS * step, 2) if over else None}


def review_candles(t: Trade, bars: list[dict]) -> dict | None:
    """決済済みの取引の振り返り用ローソク足（2026-09-29 ユーザー要望「購入後のローソク足が非常に学習になる。
    利確・損切り後もチャートとコメントを振り返りたい」）。

    保有中の candles と同じ見た目（損切り・建値・利確の水平線、15日の点線）に、**売却日の縦線と売却価格の印**を足す。
    横軸は取得日から「期限（20日）と 売却後 AFTER_EXIT_DAYS 日の遅い方」まで。売却後の足は薄く描く
    （売った後にどう動いたか＝切り所・利確の早さの答え合わせ）。日足は update_trade_bars が売却後 30 日まで取り続け、
    消さないので後からでも見られる
    """
    if not t.exit_date:
        return None
    rows = [b for b in bars if b['date'] and b['date'] >= t.entry_date
            and None not in (b['open'], b['high'], b['low'], b['close'])]
    if not rows:
        return None
    exit_d = (t.exit_date - t.entry_date).days
    span = max(TIME_LIMIT_DAYS, exit_d + 1, max((b['date'] - t.entry_date).days for b in rows))
    span = min(span, max(TIME_LIMIT_DAYS, exit_d + AFTER_EXIT_DAYS))
    rows = [b for b in rows if (b['date'] - t.entry_date).days <= span]
    prices = [t.target_price, t.stop_price] + [b['high'] for b in rows] + [b['low'] for b in rows]
    if t.exit_price:
        prices.append(t.exit_price)
    ymax, ymin = max(prices), min(prices)
    pad = (ymax - ymin) * 0.04 or 1
    ymax, ymin = ymax + pad, ymin - pad

    def y(p):
        return round((ymax - p) / (ymax - ymin) * CANDLE_H, 2)
    step = CANDLE_W / span
    w = round(min(step * 0.6, CANDLE_W / TIME_LIMIT_DAYS * 0.6), 2)
    out = []
    for b in rows:
        d = (b['date'] - t.entry_date).days
        cx = round(d * step, 2)
        top, bot = max(b['open'], b['close']), min(b['open'], b['close'])
        out.append({'cx': cx, 'x': round(cx - w / 2, 2), 'w': w,
                    'hi': y(b['high']), 'lo': y(b['low']),
                    'body_y': y(top), 'body_h': max(0.8, round(y(bot) - y(top), 2)),
                    'up': b['close'] >= b['open'], 'date': b['date'], 'after': d > exit_d,
                    'o': b['open'], 'h': b['high'], 'l': b['low'], 'c': b['close']})
    return {'items': out, 'W': CANDLE_W, 'H': CANDLE_H, 'step': step,
            'entry_y': y(t.entry_price), 'stop_y': y(t.stop_price), 'target_y': y(t.target_price),
            'half_x': round(TIME_WARN_DAYS * step, 2), 'limit_x': round(TIME_LIMIT_DAYS * step, 2),
            'exit_x': round(exit_d * step, 2), 'exit_y': y(t.exit_price) if t.exit_price else None,
            'after_w': round(max(0, CANDLE_W - exit_d * step), 2),
            'span': span, 'after_n': sum(1 for c in out if c['after'])}


def chart_overlay(t: Trade, chart: dict | None, notes: list) -> None:
    """ローソク足に「日付の目盛り」と「心情・コメントの印」を足す（2026-09-29 ユーザー要望:
    購入後のローソク足と、それを見ている自分の心理を並べて記録・振り返りたい。軸に 9/29 のように日付を小さく）。

    chart['dates'] = [{'pos': 左からの%, 'label': '9/29'}]（足が多いときは間引く。最後の足は必ず出す）
    chart['marks'] = [{'pos': %, 'no': 番号, 'kind': 種類, 'text': 本文, 'when': 'n/j'}]
      番号はコメント一覧（timeline・古い順）と同じ通し番号。同じ日に複数あれば縦に積む（'stack'）
    notes は timeline(t)（古い順）。購入時の理由は取得日（左端）に置く
    """
    if not chart:
        return
    from django.utils import timezone as dj_tz
    W, step = chart['W'], chart['step']
    items = chart['items']
    every = max(1, -(-len(items) // 10))            # 10本を超えたら間引く（切り上げ）
    dates = []
    for i, c in enumerate(items):
        if i % every == 0 or i == len(items) - 1:
            dates.append({'pos': round(c['cx'] / W * 100, 2), 'label': f"{c['date'].month}/{c['date'].day}"})
    chart['dates'] = dates
    span_days = (W / step) if step else TIME_LIMIT_DAYS
    marks, per_day = [], {}
    for no, n in enumerate(notes, start=1):
        d = t.entry_date if n.at_entry else dj_tz.localtime(n.created_at).date()
        k = max(0, min((d - t.entry_date).days, int(span_days)))
        stack = per_day.get(k, 0)
        per_day[k] = stack + 1
        marks.append({'pos': round(k * step / W * 100, 2), 'no': no, 'kind': n.kind or 'info', 'stack': stack,
                      'kind_label': n.get_kind_display() or '情報',
                      'text': n.text, 'when': '購入時' if n.at_entry else f'{d.month}/{d.day}'})
    chart['marks'] = marks


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


TIME_LIMIT_DAYS = 20    # タイムリミット（2026-09-24 ユーザー要望・同日 30→20日に短縮）。細い線ゲージで出す
TIME_WARN_DAYS = 15     # ここで線が琥珀に変わり、目印を置く（20日の 3/4）


def time_gauge(days: int) -> dict:
    """経過日数 → 細い線ゲージの状態。pct=満了までの進み（0〜100）、state=ok/warn/over"""
    pct = max(0.0, min(100.0, days / TIME_LIMIT_DAYS * 100))
    state = 'over' if days >= TIME_LIMIT_DAYS else ('warn' if days >= TIME_WARN_DAYS else 'ok')
    return {'pct': pct, 'state': state, 'left': max(0, TIME_LIMIT_DAYS - days), 'limit': TIME_LIMIT_DAYS,
            'warn_pos': TIME_WARN_DAYS / TIME_LIMIT_DAYS * 100}


def open_rows(setting: ContraSetting, today: date | None = None, strategy: str = 'contra') -> list[dict]:
    """保有中の取引を UI 用に。現在値・損切り/利確までの距離・ザラ場で触れたか・経過日数"""
    today = today or date.today()
    fx_rate = latest_fx()[1]
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
            # 米国株は円換算を併記（2026-09-24 ユーザー要望。最新ドル円 latest_fx。戦略の判定には使わない）
            'pnl_now_jpy': ((cur - t.entry_price) * t.shares * fx_rate) if (cur and t.currency == 'USD') else None,
            'cur_jpy': (cur * fx_rate) if (cur and t.currency == 'USD') else None,   # 現在値の円換算（2026-09-24）
            # 右上の見出し用: 購入価格から1株あたりいくら動いたか（$ と ¥）。現在値そのものはバー上の吹き出しに出す
            'entry_jpy': (t.entry_price * fx_rate) if t.currency == 'USD' else None,   # 取得価格の円換算
            'gain_ps': (cur - t.entry_price) if cur else None,
            'gain_ps_jpy': ((cur - t.entry_price) * fx_rate) if (cur and t.currency == 'USD') else None,
            'time': time_gauge((today - t.entry_date).days),
            'candles': candles(t, bars),
            'be': breakeven_state(t, bars, cur),
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
            'timeline': timeline(t), 'shots': shots_of(t),
            'unit': '$' if t.currency == 'USD' else '円',
            'stale': (last is None or last['date'] is None) or (today - last['date']).days > 4,
            'fallback': fallback,      # 株価マスタの終値で代用中（日足が来れば自動で切り替わる）
        })
        entry_notes, feelings = split_notes(rows[-1]['timeline'])
        rows[-1]['entry_notes'], rows[-1]['feelings'] = entry_notes, feelings
        chart_overlay(t, rows[-1]['candles'], feelings)   # 日付の目盛り・心情の印（購入時の想定は印にしない）
        # 心情の欄に出す最新3件（チャートの印と同じ通し番号付き）
        rows[-1]['recent_notes'] = list(enumerate(feelings, start=1))[-3:]
    # 触れたものを先頭に（今日やることが上に来る）
    # 触れたもの・建値への変更待ちを先頭に（今日やることが上に来る）
    rows.sort(key=lambda r: (not (r['touched_stop'] or r['touched_target']
                                  or (r['be'] and (r['be']['state'] == 'reached' or r['be']['touched']))),
                             r['t'].entry_date))
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
        # 勝率に入れるのは 利確・早期利確・損切り（2026-09-29 ユーザー指示「早期利確も勝ちは勝ち」。
        # それまでは早期利確を勝率から外していた）。建値撤退・裁量・期限は入れない（損益だけ積算）
        if t.exit_reason in ('target', 'early', 'stop'):
            wins += is_win
            losses += (not is_win)
            (win_pcts if is_win else loss_pcts).append(net)
            streak = streak + 1 if not is_win else 0
            max_streak = max(max_streak, streak)
        if t.exit_reason in by_reason:
            by_reason[t.exit_reason]['n'] += 1
            by_reason[t.exit_reason]['sum'] += net
        rows.append({'t': t, 'net': net, 'gross': t.pnl_pct, 'amount': t.pnl_amount,
                     'amount_jpy': (t.pnl_amount * latest_fx()[1]) if t.currency == 'USD' else None,
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
             'breakeven': {'pct': 0.0, 'amount': 0.0, 'n': 0},
             'other': {'pct': 0.0, 'amount': 0.0, 'n': 0}}
    for t in closed:
        net = t.pnl_pct_net(c)
        amt = _amount_net(t)
        key = t.exit_reason if t.exit_reason in ('target', 'stop', 'early', 'breakeven') else 'other'
        total['pct'] += net
        total['amount'] += amt
        total[key]['pct'] += net
        total[key]['amount'] += amt
        total[key]['n'] += 1
    total['unit'] = '$'   # 米国株前提（日本株が混ざると通貨が混ざる。混ざったら分けて出すこと）
    fx_date, fx_rate = latest_fx()
    total['amount_jpy'] = total['amount'] * fx_rate     # 円換算（2026-09-24。表示だけ）
    total['fx_rate'], total['fx_date'] = fx_rate, fx_date
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
            'timeline': timeline(t), 'shots': shots_of(t),
            'unit': '$' if t.currency == 'USD' else '円',
            # 決済後もローソク足で振り返る（2026-09-29）。売却日の縦線・売却価格・売却後の足（薄く）
            'candles': review_candles(t, list(t.bars.order_by('date').values('date', 'open', 'high', 'low', 'close'))),
        }
    # (注) 日付の目盛りと心情の印は呼び出し側で chart_overlay を当てる
    # 勝ち／負け（2026-09-29 ユーザー要望: 勝ちトレードと負けトレードを分けて振り返る）。コスト込みの損益で分ける
    #   （建値撤退・期限・裁量も損益の符号で振り分ける）。stop/target/other は従来の理由別（集計用に残す）
    reflect = {'stop': [], 'target': [], 'other': [], 'win': [], 'loss': []}
    tag_stats = {}
    mood_stats = {}
    for r in rows:
        t = r['t']
        rr = _reflect_row(t, r['net'])
        rr['entry_notes'], rr['feelings'] = split_notes(rr['timeline'])
        chart_overlay(t, rr['candles'], rr['feelings'])
        key = t.exit_reason if t.exit_reason in ('stop', 'target') else ('target' if t.exit_reason == 'early' else 'other')
        reflect[key].append(rr)
        reflect['win' if r['net'] > 0 else 'loss'].append(rr)
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
