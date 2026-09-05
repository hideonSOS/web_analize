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

from .models import Budget, FixedCostEntry, Transaction

# 理想テンプレートに置く「基礎支出」の候補。台帳に無くても項目として選べるようにする
# （口座振替で払うものは CSV に出てこない。実測で家賃・ガス・水道は台帳に0件）
BASIC_ITEMS = ['家賃', '電気', 'ガス', '水道', '通信', '保険', '駐車場']

# 円グラフの色。理想額の大きい順に割り当て、理想と実際の2つの円で同じ項目は同じ色
PIE_PALETTE = ['#1e90ff', '#8b5cf6', '#34d399', '#fbbf24', '#f87171', '#63b3ff',
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
    daily_spec = {
        'days': list(range(1, last_day + 1)),
        'values': [daily.get(d, 0) for d in range(1, last_day + 1)],
        'cumulative': [],
        'baseline_daily': round(median / 30) if median else 0,
    }
    acc = 0
    for d in range(1, last_day + 1):
        acc += daily.get(d, 0)
        daily_spec['cumulative'].append(acc)

    # --- サブスク・固定費の比率 ---------------------------------------------
    fixed = int(qs.filter(kind__in=['サブスク', '年会費']).aggregate(t=Sum('amount'))['t'] or 0)

    # --- 予算 vs 実績 --------------------------------------------------------
    # ⚠️ 支出の予算は「上限」。資産配分の目標（近づけたい値）と向きが逆で、
    # 超過が赤・未達が緑になる。バー幅は予算を100%とした消化率
    budgets = list(Budget.objects.all())
    # 口座振替など CSV に出ない基礎支出の手入力分。台帳の同名カテゴリ実績に足す
    # （家賃は台帳に無いので実質これだけが実績になる）
    manual = {e.item: e.amount for e in FixedCostEntry.objects.filter(ym=ym)}
    budget_rows = []
    for b in budgets:
        actual = cur_cat.get(b.category, 0) + manual.get(b.category, 0)
        limit = b.monthly_limit or 0
        rate = round(actual / limit * 100, 1) if limit else None
        budget_rows.append({
            'category': b.category,
            'limit': limit,
            'actual': actual,
            'diff': actual - limit,           # 正=超過
            'rate': rate,
            # バーの幅はトラックの 2/3 を予算=100% とする座標系に写す。
            # 150%（=予算の1.5倍）でトラック右端に届き、それ以上は頭打ち
            'width': round(min((rate or 0), 150) / 1.5, 1),
            # 超過分の斜線帯（予算線から右へ）。同じ座標系
            'over_width': round(min(max((rate or 0) - 100, 0), 50) / 1.5, 1),
            'over': actual > limit,
            'note': b.note,
        })
    budget_rows.sort(key=lambda r: -(r['rate'] or 0))

    # --- 理想テンプレートと実際（円グラフ2つ） -------------------------------
    # 理想額の大きい順に色を振り、「理想の構成」と「この月の実際」で同じ項目は同じ色。
    # ⚠️ 実際の円は「予算を設定した項目」だけで作る（未設定カテゴリを混ぜると
    # 理想の円と項目が揃わず比較にならない）。未設定分は unbudgeted で別に出す
    by_ideal = sorted(budget_rows, key=lambda r: -r['limit'])
    color_of = {r['category']: PIE_PALETTE[i % len(PIE_PALETTE)] for i, r in enumerate(by_ideal)}
    template_spec = {
        'items': [{'name': r['category'], 'ideal': r['limit'], 'actual': r['actual'],
                   'color': color_of[r['category']]} for r in by_ideal],
        'ideal_total': sum(r['limit'] for r in budget_rows),
        'actual_total': sum(r['actual'] for r in budget_rows),
    }
    # 手入力の対象＝理想テンプレートにあって台帳には無い項目（家賃・電気 など）
    ledger_cats = set(cur_cat) | set(base_cat)
    manual_items = [b.category for b in budgets if b.category not in ledger_cats]
    fixed_entries = list(FixedCostEntry.objects.filter(ym=ym).values('item', 'amount', 'note'))
    budget_limit_total = sum(r['limit'] for r in budget_rows)
    budget_actual_total = sum(r['actual'] for r in budget_rows)
    budget_summary = {
        'limit': budget_limit_total,
        'actual': budget_actual_total,
        'diff': budget_actual_total - budget_limit_total,
        'rate': round(budget_actual_total / budget_limit_total * 100, 1) if budget_limit_total else None,
        'over_count': sum(1 for r in budget_rows if r['over']),
    } if budget_rows else None

    # 予算未設定のカテゴリ（設定を促すため、金額の大きい順に出す）
    unbudgeted = [
        {'name': n, 'amount': a, 'baseline': base_cat.get(n, 0)}
        for n, a in sorted(cur_cat.items(), key=lambda x: -x[1])
        if n not in {b.category for b in budgets}
    ][:8]

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
        'budget_rows': budget_rows,
        'budget_summary': budget_summary,
        'unbudgeted': unbudgeted,
        # 予算の項目候補: 台帳のカテゴリ ＋ 既に予算のある項目 ＋ 基礎支出の雛形（家賃・電気…）
        'all_categories': sorted(set(cur_cat) | set(base_cat) | {b.category for b in budgets} | set(BASIC_ITEMS)),
        'template_spec': template_spec,
        'manual_items': manual_items,
        'fixed_entries': fixed_entries,
    }
