"""月単位の分析

支出分析トップが「全期間の傾向」なのに対し、ここは **その月に何が起きたか** を読む。
月次で見たいのは合計額そのものより「いつもと違う点」なので、次を出す:

- 前月比と、平常月（直近12か月の中央値）との差。**中央値を基準にする**のは、
  年払いや大型出費のある月に平均が引っ張られて「いつも」を表さなくなるため
- カテゴリ別の増減（何が増えて何が減ったか）
- その月だけの突出支出（平常月に無い出費）
- 日別の推移（月内のどこで使ったか）
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date

from django.db.models import Count, Sum

from .models import FixedCostEntry, SpendingSetting, TemplateItem, Transaction

# 理想テンプレートの積み上げ横棒の色。テンプレートの並び順に割り当て、
# 「理想」「実際」の2本で同じ項目は同じ色
TEMPLATE_PALETTE = ['#1e90ff', '#8b5cf6', '#34d399', '#fbbf24', '#f87171', '#63b3ff',
                    '#c4b5fd', '#f9a8d4', '#5eead4', '#fdba74', '#94a3b8', '#a3e635']

BASELINE_MONTHS = 12          # 「平常月」を決める窓
TOP_N = 12                    # カテゴリ・明細の表示件数


def available_months() -> list[str]:
    """データのある年月（新しい順）"""
    return list(
        Transaction.objects.filter(in_total=True)
        .values_list('ym', flat=True).distinct().order_by('-ym')
    )


def _prev_ym(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f'{y - 1}-12' if m == 1 else f'{y}-{m - 1:02d}'


def _sum_by(ym: str, field: str) -> dict[str, int]:
    rows = (Transaction.objects.filter(in_total=True, ym=ym)
            .values(field).annotate(t=Sum('amount')))
    return {(r[field] or '未分類'): int(r['t'] or 0) for r in rows}


BANK_SCHEDULE_MIN_MONTHS = 2   # 1回きりの振込は「毎月の引き落とし」ではないので出さない


def _cycle_order(rows, salary_day, today_day, is_current):
    """引き落としの行を**給与日起点**に並べ直し、累計と今日の印を付け直す（ユーザー要望 2026-09-06）。

    月初起点だと「25日に給料が入って、翌月の24日までにいくら出るか」が読めない。
    並び順は (日 − 給与日) mod 31 で、給与日の行が先頭、給与日より前の日は翌月扱い
    （next_month=True・表示で「翌月」）。累計は給与日でゼロに戻る。
    今日の印も同じ座標で付ける（今日が給与日より前なら「翌月側」にいる）。
    給与日が未設定なら日付順のまま。
    """
    if not salary_day:
        for r in rows:
            r['next_month'] = False
        return rows
    pos = lambda d: (d - salary_day) % 31
    rows = sorted(rows, key=lambda r: (pos(r['day']), 0 if r.get('kind') == 'salary' else 1, -r.get('amount', 0)))
    acc = 0
    today_pos = pos(today_day) if is_current else None
    for r in rows:
        r['next_month'] = r['day'] < salary_day
        if r.get('kind') == 'salary':
            r['cum'] = None
            acc = 0
        else:
            acc += r['amount']
            r['cum'] = acc
        if is_current:
            r['passed'] = pos(r['day']) < today_pos
            r['today'] = r['day'] == today_day
        else:
            r['passed'] = r['today'] = False
    return rows


def bank_recurring(min_months: int = BANK_SCHEDULE_MIN_MONTHS) -> list[dict]:
    """銀行明細の「毎月の引き落とし」一覧。カレンダーと理想テンプレートの雛形の共通材料。

    1件＝摘要辞書で付いた表示名。月あたり平均額（合計÷出現月数）、典型的な日（各回の
    日の中央値）、出現月数、最終日、種別（expense / card_settlement / cash_withdrawal /
    investment_transfer）。収入・利息は含めない。min_months 未満のものは除く。
    """
    from .services import load_bank_frame
    bank = load_bank_frame()
    if bank is None:
        return []
    import pandas as pd
    pay = bank[(bank['amount'] > 0) & ~bank['treat'].isin(['income', 'ignore'])].copy()
    if pay.empty:
        return []
    pay['day'] = pay['date'].dt.day
    pay['name'] = pay['merchant'].where(pay['merchant'] != '', pay['summary'])
    g = (pay.groupby('name')
            .agg(months=('ym', 'nunique'), total=('amount', 'sum'), n=('amount', 'size'),
                 day=('day', 'median'), last=('date', 'max'), treat=('treat', 'first'),
                 category=('category', 'first'))
            .reset_index())
    g = g[g['months'] >= min_months]
    return [{
        'name': r['name'], 'day': int(round(r['day'])),
        'amount': int(round(r['total'] / r['months'])),
        'months': int(r['months']), 'n': int(r['n']),
        'last': pd.Timestamp(r['last']).strftime('%Y-%m-%d'),
        'treat': r['treat'], 'category': r['category'],
        'period': (pay['ym'].min(), pay['ym'].max(), int(pay['ym'].nunique())),
    } for r in g.sort_values(['day', 'total'], ascending=[True, False]).to_dict('records')]


def _bank_schedule(setting, today, is_current):
    """銀行明細から引き落としカレンダーを作る（平均額・典型的な日・引き落とし日順）。

    ユーザー要望: 手入力ではなく UFJ_bank.csv の実績から。金額順ではなく**引き落とし日順**に
    並べ、次に何が引かれるかが分かるように。

    - 1件＝摘要辞書で付いた表示名（家賃・関西電力・楽天カード引き落とし・ATM引き出し…）
    - 平均額は「月あたり」（合計÷出現月数）。ATM のように月に複数回あるものも月額で揃える
    - 日は各回の日の**中央値**（月末や休日で前後にずれるので平均より安定する）
    - 収入・利息は出さない。除外扱い（カード引落・ATM・証券振込）も口座からは実際に出て
      いくので**出す**（このカレンダーの目的は口座残高の動き）。行に種別を添える
    """
    recurring = bank_recurring()
    if not recurring:
        return None
    treat_label = {'expense': '', 'card_settlement': 'カード', 'cash_withdrawal': 'ATM',
                   'investment_transfer': '投資'}
    palette = TEMPLATE_PALETTE
    rows = [{
        'day': r['day'], 'name': r['name'], 'amount': r['amount'],
        'months': r['months'], 'n': r['n'], 'last': r['last'],
        'kind': r['treat'], 'kind_label': treat_label.get(r['treat'], ''),
        'color': palette[i % len(palette)],
    } for i, r in enumerate(recurring)]
    period = recurring[0]['period']
    if setting.salary_day:
        rows.append({'day': setting.salary_day, 'name': '給与日', 'amount': 0, 'cum': None,
                     'color': '#34d399', 'kind': 'salary', 'kind_label': ''})
    # 給与日起点で並べ直す（給与日が先頭・それより前の日は翌月扱い・累計は給与日でゼロ）
    rows = _cycle_order(rows, setting.salary_day, today.day, is_current)
    total = sum(r['amount'] for r in rows)
    return {
        'rows': rows, 'undated': [], 'total': total, 'dated_total': total,
        'card_day': setting.card_debit_day, 'salary_day': setting.salary_day,
        'is_current': is_current, 'today': today.day if is_current else None,
        # 今日以降（給与サイクル上で今日より後）にまだ出ていく額
        'remaining': sum(r['amount'] for r in rows if not r['passed']) if is_current else None,
        'source': 'bank',
        'period': period,
    }


def build(ym: str | None = None) -> dict:
    """指定月（省略時は最新月）の分析データ"""
    months = available_months()
    if not months:
        return {'has_data': False, 'months': []}
    if ym not in months:
        # 既定は「直近の完了月」。当月は数日分しか無く前月比が -99% になって
        # 読めないため、当月がデータの先頭なら1つ前を初期表示にする
        ym = months[0]
        if len(months) > 1 and ym == date.today().strftime('%Y-%m'):
            ym = months[1]

    qs = Transaction.objects.filter(in_total=True, ym=ym)
    total = int(qs.aggregate(t=Sum('amount'))['t'] or 0)
    count = qs.count()

    # --- 平常月（直近12か月の中央値）との比較 ------------------------------
    # ⚠️ 平均ではなく中央値。年払いや大型出費のある月に引っ張られないため
    idx = months.index(ym)
    baseline_months = months[idx + 1: idx + 1 + BASELINE_MONTHS]
    hist = (Transaction.objects.filter(in_total=True, ym__in=baseline_months)
            .values('ym').annotate(t=Sum('amount')))
    hist_vals = [int(r['t'] or 0) for r in hist]
    median = int(statistics.median(hist_vals)) if hist_vals else 0

    prev_ym = _prev_ym(ym)
    prev_total = int(
        Transaction.objects.filter(in_total=True, ym=prev_ym)
        .aggregate(t=Sum('amount'))['t'] or 0)

    def diff(a, b):
        return {'value': a - b, 'pct': round((a / b - 1) * 100, 1) if b else None}

    # --- カテゴリ別の増減 --------------------------------------------------
    cur_cat = _sum_by(ym, 'category')
    prev_cat = _sum_by(prev_ym, 'category')
    # 平常月のカテゴリ別中央値（月ごとに集計してから中央値を取る）
    base_rows = (Transaction.objects.filter(in_total=True, ym__in=baseline_months)
                 .values('category', 'ym').annotate(t=Sum('amount')))
    per_cat = defaultdict(list)
    for r in base_rows:
        per_cat[r['category'] or '未分類'].append(int(r['t'] or 0))
    base_cat = {k: int(statistics.median(v)) for k, v in per_cat.items()}

    cat_rows = []
    for name, amount in sorted(cur_cat.items(), key=lambda x: -x[1])[:TOP_N]:
        b = base_cat.get(name, 0)
        cat_rows.append({
            'name': name,
            'amount': amount,
            'prev': prev_cat.get(name, 0),
            'baseline': b,
            'vs_prev': amount - prev_cat.get(name, 0),
            'vs_base': amount - b,
            'pct': round(amount / total * 100, 1) if total else 0,
        })
    # 平常月にはあったのに今月ゼロのカテゴリも「減った」として拾う
    for name, b in base_cat.items():
        if name not in cur_cat and b > 0:
            cat_rows.append({'name': name, 'amount': 0, 'prev': prev_cat.get(name, 0),
                             'baseline': b, 'vs_prev': -prev_cat.get(name, 0),
                             'vs_base': -b, 'pct': 0})
    max_cat = max([r['amount'] for r in cat_rows], default=0) or 1
    for r in cat_rows:
        r['width'] = round(r['amount'] / max_cat * 100, 1)

    # 増減の大きい順（絶対値）。「いつもと違う点」を上に出す
    movers = sorted([r for r in cat_rows if r['vs_base']],
                    key=lambda r: -abs(r['vs_base']))[:6]

    # --- その月の大きな支出 ------------------------------------------------
    big = list(qs.order_by('-amount')[:TOP_N].values(
        'date', 'label', 'merchant', 'amount', 'category', 'source_kind'))

    # --- 支払元別 -----------------------------------------------------------
    labels = {'card': '楽天カード', 'cash': '現金', 'bank': '銀行', 'unset': '支払元未設定'}
    src_rows = [
        {'name': labels.get(r['source_kind'], r['source_kind']),
         'amount': int(r['t'] or 0), 'n': r['n'],
         'pct': round(int(r['t'] or 0) / total * 100, 1) if total else 0}
        for r in qs.values('source_kind').annotate(t=Sum('amount'), n=Count('id')).order_by('-t')
    ]

    # --- 日別の推移（月内のどこで使ったか） ----------------------------------
    daily = defaultdict(int)
    for r in qs.values('date').annotate(t=Sum('amount')):
        daily[r['date'].day] += int(r['t'] or 0)
    y, m = int(ym[:4]), int(ym[5:7])
    last_day = (date(y + (m == 12), (m % 12) + 1, 1) - date.resolution).day
    setting = SpendingSetting.get()
    today = date.today()
    is_current = ym == today.strftime('%Y-%m')
    # 目安線（ユーザー指示 2026-09-06・確定）: 直近12か月それぞれの「日別累計」を日ごとに
    # 平均した曲線。家賃・カード引き落としの日で段が付く右肩上がりで、下がらない。
    # 棒＝今月の支出の累積。両方とも累積で同じスケール1つ。
    # ⚠️ 直線（1日平均×日数）にしないこと（「引き落としのある日は増加するやろ」）。
    # 月の目標額があれば、形はそのまま月末を目標額に縮尺する
    per_month = defaultdict(lambda: defaultdict(int))
    for r in (Transaction.objects.filter(in_total=True, ym__in=baseline_months)
              .values('ym', 'date').annotate(t=Sum('amount'))):
        per_month[r['ym']][r['date'].day] += int(r['t'] or 0)
    curves = []
    for days_of in per_month.values():
        acc, curve = 0, []
        for d in range(1, last_day + 1):
            acc += days_of.get(d, 0)      # 30日の月の31日目は前日と同じ（増えない）
            curve.append(acc)
        curves.append(curve)
    n_base = len(curves)
    ref = [sum(c[i] for c in curves) / n_base for i in range(last_day)] if n_base else [0] * last_day
    ref_end = ref[-1] if ref else 0
    if setting.monthly_target and ref_end:
        ref = [v * setting.monthly_target / ref_end for v in ref]
    ref = [round(v) for v in ref]
    target = setting.monthly_target or round(ref_end)
    avg_daily = round(target / last_day) if last_day else 0
    cum_values, acc = [], 0
    for d in range(1, last_day + 1):
        acc += daily.get(d, 0)
        cum_values.append(None if is_current and d > today.day else acc)   # 未来日は描かない
    daily_spec = {
        'days': list(range(1, last_day + 1)),
        'values': cum_values,
        'reference': ref,
        'avg_daily': avg_daily,
        'target': target,
        'target_source': 'manual' if setting.monthly_target else 'average',
        'base_months': n_base,
        'today': today.day if is_current else None,
    }
    # 今日時点の目安との差（当月だけ）
    pace_status = None
    if is_current and ref_end:
        ref_today = ref[today.day - 1]
        cum_today = cum_values[today.day - 1] or 0
        over_days = [d for d in range(1, today.day + 1) if (cum_values[d - 1] or 0) > ref[d - 1]]
        pace_status = {
            'target': target, 'source': daily_spec['target_source'], 'base_months': n_base,
            'avg_daily': avg_daily,
            'today': today.day, 'ref_today': ref_today, 'cum_today': cum_today,
            'margin': abs(ref_today - cum_today),
            'over': cum_today > ref_today,
            'over_days': over_days,
            'projected': cum_today + (target - ref_today),   # 残りを平常月の形で使った場合
        }

    # --- サブスク・固定費の比率 ---------------------------------------------
    fixed = int(qs.filter(kind__in=['サブスク', '年会費']).aggregate(t=Sum('amount'))['t'] or 0)

    # --- 理想の支出テンプレートと実際（積み上げ横棒2本・すべて手入力） -----------
    # ⚠️ 台帳の数字は一切使わない（ユーザー方針）。理想は TemplateItem、実際はその月の
    # FixedCostEntry。2本の棒は「大きい方の合計」を100%として描くので長さで比べられる。
    # 色はテンプレートの並び順に振り、理想と実際で同じ項目は同じ色
    items = list(TemplateItem.objects.all())
    actual_of = {e.item: e.amount for e in FixedCostEntry.objects.filter(ym=ym)}
    color_of = {t.name: TEMPLATE_PALETTE[i % len(TEMPLATE_PALETTE)] for i, t in enumerate(items)}
    ideal_total = sum(t.ideal for t in items)
    actual_total = sum(actual_of.get(t.name, 0) for t in items)
    scale = max(ideal_total, actual_total) or 1

    def _segments(key):
        segs = []
        for t in items:
            v = t.ideal if key == 'ideal' else actual_of.get(t.name, 0)
            if v > 0:
                segs.append({'name': t.name, 'amount': v, 'width': round(v / scale * 100, 2),
                             'color': color_of[t.name]})
        return segs

    template_bars = [
        {'label': '理想', 'total': ideal_total, 'width': round(ideal_total / scale * 100, 1),
         'segments': _segments('ideal')},
        {'label': f'実際（{ym}）', 'total': actual_total, 'width': round(actual_total / scale * 100, 1),
         'segments': _segments('actual')},
    ] if items else []
    template_items = [{
        'name': t.name, 'ideal': t.ideal, 'actual': actual_of.get(t.name, 0),
        'diff': actual_of.get(t.name, 0) - t.ideal,          # 正=理想より使いすぎ
        'entered': t.name in actual_of, 'color': color_of[t.name],
    } for t in items]
    template_summary = {
        'ideal': ideal_total, 'actual': actual_total, 'diff': actual_total - ideal_total,
        'entered': sum(1 for r in template_items if r['entered']), 'n': len(items),
    } if items else None

    # --- 引き落としカレンダー（理想額を日付順に並べ、累計で「その日までにいくら出るか」） ---
    # ⚠️ カード払いの項目は各サービスの日ではなく**カードの引き落とし日に1本**にまとめる
    # （実際の口座の動きに合わせる）。金額は理想額を使う（実際は月ごとに揃わないため）。
    dated, undated, card_items = [], [], []
    for t in items:
        if t.via_card:
            card_items.append(t)
        elif t.debit_day:
            dated.append({'day': t.debit_day, 'name': t.name, 'amount': t.ideal, 'color': color_of[t.name],
                          'kind': 'item'})
        else:
            undated.append({'name': t.name, 'amount': t.ideal, 'color': color_of[t.name]})
    if card_items:
        dated.append({'day': setting.card_debit_day, 'name': 'カード引き落とし',
                      'amount': sum(t.ideal for t in card_items), 'color': '#94a3b8', 'kind': 'card',
                      'members': [t.name for t in card_items]})
    if items and setting.salary_day:
        dated.append({'day': setting.salary_day, 'name': '給与日', 'amount': 0, 'cum': None,
                      'color': '#34d399', 'kind': 'salary'})
    # 給与日起点で並べ直す（給与日が先頭・それより前の日は翌月扱い・累計は給与日でゼロ）
    dated = _cycle_order(dated, setting.salary_day, today.day, is_current) if items else dated
    dated_total = sum(r['amount'] for r in dated)
    schedule = {
        'rows': dated,
        'undated': undated,
        'total': dated_total + sum(u['amount'] for u in undated),
        'dated_total': dated_total,
        'card_day': setting.card_debit_day,
        'salary_day': setting.salary_day,
        'is_current': is_current,
        'today': today.day if is_current else None,
        # 当月なら「今日以降（給与サイクル上で今日より後）にまだ出ていく額」
        'remaining': sum(r['amount'] for r in dated if not r['passed']) if is_current else None,
        'source': 'template',
    } if items else None

    # 銀行明細があるときは、手入力の理想ではなく**実際の引き落とし実績**からカレンダーを作る
    # （ユーザー要望 2026-09-06: 平均引き落とし額を引き落とし日順に。次に何が引かれるかを見る）
    bank_schedule = _bank_schedule(setting, today, is_current)
    if bank_schedule:
        schedule = bank_schedule
    return {
        'has_data': True,
        'ym': ym,
        'months': months,
        'prev_ym': prev_ym if prev_ym in months else None,
        'next_ym': months[idx - 1] if idx > 0 else None,
        'total': total,
        'count': count,
        'median': median,
        'prev_total': prev_total,
        'vs_prev': diff(total, prev_total),
        'vs_median': diff(total, median),
        'fixed': fixed,
        'fixed_pct': round(fixed / total * 100, 1) if total else 0,
        'cat_rows': cat_rows[:TOP_N],
        'movers': movers,
        'big_rows': [{**b, 'source_label': labels.get(b['source_kind'], '')} for b in big],
        'src_rows': src_rows,
        'daily_spec': daily_spec,
        'pace_status': pace_status,
        'all_categories': sorted(set(cur_cat) | set(base_cat)),
        'template_bars': template_bars,
        'template_items': template_items,
        'template_summary': template_summary,
        'schedule': schedule,
        'spending_setting': setting,
    }
