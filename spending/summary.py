"""画面用サマリーの組み立て（DB の Transaction → 集計 → テンプレート/ECharts 用の dict）

card_insight.analyze の判定ロジックをそのまま使う。ここは DataFrame への橋渡しと、
画面に出す形（ECharts のスペックと表の行）への整形だけを行う。

集計対象は in_total=True の行のみ。手動編集（manual_*）があればそれを優先する。
"""
from __future__ import annotations

import pandas as pd

from card_insight import analyze

from .models import SavingsPlan, Transaction

RECENT_MONTHS = 12


def _frame() -> pd.DataFrame:
    """Transaction → analyze が期待する形の DataFrame

    analyze 側は category_final / rule_hit などの列名で参照するので合わせる。
    手動で直した分類・必要度はここで反映する（再取込では上書きされない値）。
    """
    rows = list(Transaction.objects.values(
        'date', 'ym', 'amount', 'source_kind', 'source_name', 'merchant', 'shop_norm', 'label',
        'category', 'subcategory', 'category_source', 'kind', 'necessity',
        'in_total', 'exclude_reason', 'row_type', 'match_status', 'dup_flag',
        'manual_category', 'manual_subcategory', 'manual_necessity', 'exclude_override',
    ))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    # 手動編集を優先
    m = df['manual_category'].astype(str).str.strip() != ''
    df.loc[m, 'category'] = df.loc[m, 'manual_category']
    m = df['manual_subcategory'].astype(str).str.strip() != ''
    df.loc[m, 'subcategory'] = df.loc[m, 'manual_subcategory']
    m = df['manual_necessity'].astype(str).str.strip() != ''
    df.loc[m, 'necessity'] = df.loc[m, 'manual_necessity']
    # 集計対象の上書き（True=強制的に含める / False=強制除外）
    ov = df['exclude_override'].notna()
    df.loc[ov, 'in_total'] = df.loc[ov, 'exclude_override'].astype(bool)

    df['category_final'] = df['category']
    df['subcategory_final'] = df['subcategory']
    df['rule_hit'] = df['category_source'] == 'rule'
    # analyze 側が参照する列を補う。DB には持たない（ルール由来の付随情報）ので
    # 空で用意する。無いと groupby(...).agg で KeyError になる
    df['rule_note'] = ''
    df['shop'] = df['shop_norm']
    return df


def build(months: int = RECENT_MONTHS) -> dict:
    """画面に渡す全データ。Transaction が空なら has_data=False のみ返す。"""
    df = _frame()
    if df.empty:
        return {'has_data': False}

    total = df[df['in_total']].copy()
    recent = analyze._recent(total, months)

    # --- KPI --------------------------------------------------------------
    monthly_sum = total.groupby('ym')['amount'].sum().sort_index()
    recent_sum = int(recent['amount'].sum())
    months_n = max(recent['ym'].nunique(), 1)

    subs = analyze.detect_subscriptions(total)
    candidates = analyze.savings_candidates(subs, total)
    distortions = analyze.detect_distortions(total)

    # ⚠️ card_insight.analyze の出力列は日本語（候補/想定効果_年/年額換算 など）。
    # 画面側では英字キーに直して扱う（テンプレートで日本語キーは引きにくいため）
    machine_annual = int(candidates['想定効果_年'].sum()) if len(candidates) else 0
    decided_monthly = SavingsPlan.monthly_capacity()

    kpi = {
        'recent_total': recent_sum,
        'monthly_avg': recent_sum // months_n,
        'months': months_n,
        'subs_count': len(subs),
        'subs_annual': int(subs['年額換算'].sum()) if len(subs) else 0,
        'machine_annual': machine_annual,
        'machine_monthly': machine_annual // 12,
        'decided_monthly': decided_monthly,
        'decided_annual': decided_monthly * 12,
    }

    # --- 月次推移（支払元別の積み上げ） -------------------------------------
    piv = (total.pivot_table(index='ym', columns='source_kind', values='amount',
                             aggfunc='sum', fill_value=0).sort_index())
    labels = {'card': '楽天カード', 'cash': '現金', 'bank': '銀行', 'unset': '支払元未設定'}
    colors = {'card': '#1e90ff', 'cash': '#fbbf24', 'bank': '#8b5cf6', 'unset': '#34d399'}
    monthly_spec = {
        'months': list(piv.index),
        'series': [
            {'name': labels.get(k, k), 'color': colors.get(k, '#63b3ff'),
             'data': [int(v) for v in piv[k]]}
            for k in ('card', 'cash', 'bank', 'unset') if k in piv.columns
        ],
        'totals': [int(v) for v in piv.sum(axis=1)],
    }

    # --- カテゴリ内訳（直近） ---------------------------------------------
    cat = (recent.groupby('category_final')['amount'].sum()
           .sort_values(ascending=False).head(12))
    palette = ['#1e90ff', '#8b5cf6', '#34d399', '#fbbf24', '#f87171', '#63b3ff',
               '#c4b5fd', '#f9a8d4', '#5eead4', '#fdba74', '#94a3b8', '#a3e635']
    cat_total = int(cat.sum()) or 1
    category_rows = [
        {'name': n or '未分類', 'value': int(v), 'pct': round(int(v) / cat_total * 100, 1),
         'color': palette[i % len(palette)]}
        for i, (n, v) in enumerate(cat.items())
    ]

    # --- 加盟店ランキング（直近） -------------------------------------------
    mer = (recent.groupby('merchant')
           .agg(total=('amount', 'sum'), n=('amount', 'count'),
                kind=('kind', 'first'), necessity=('necessity', 'first'),
                category=('category_final', 'first'), source=('source_kind', 'first'))
           .sort_values('total', ascending=False).head(20).reset_index())
    max_m = int(mer['total'].max()) if len(mer) else 1
    merchant_rows = [
        {**r, 'total': int(r['total']), 'n': int(r['n']),
         'width': round(int(r['total']) / max_m * 100, 1)}
        for r in mer.to_dict('records')
    ]

    # --- サブスク一覧（日本語列 → 英字キーへ） -------------------------------
    sub_rows = []
    if len(subs):
        for r in subs.sort_values('年額換算', ascending=False).to_dict('records'):
            sub_rows.append({
                'merchant': r.get('merchant', ''),
                'monthly': int(r.get('月額換算') or 0),
                'annual': int(r.get('年額換算') or 0),
                'necessity': r.get('necessity', ''),
                'kind': r.get('kind', ''),
                'months': int(r.get('months') or 0),
                'active': bool(r.get('直近月に発生')),
                'note': r.get('note', '') or '',
            })

    # --- 節約候補（同上）。本人の決定（SavingsPlan）を突き合わせる -------------
    planned = {p.merchant: p for p in SavingsPlan.objects.all()}
    cand_rows = []
    if len(candidates):
        for r in candidates.sort_values('想定効果_年', ascending=False).to_dict('records'):
            name = r.get('候補', '')
            p = planned.get(name)
            cand_rows.append({
                'merchant': name,
                'action': r.get('前提', '解約した場合'),
                'kind': r.get('種別', ''),
                'annual_effect': int(r.get('想定効果_年') or 0),
                'current_annual': int(r.get('現状年額') or 0),
                'necessity': r.get('優先度', ''),
                'reason': r.get('メモ', '') or '',
                'plan_id': p.id if p else None,
                'status': p.status if p else 'todo',
                'status_label': p.get_status_display() if p else '検討中',
            })

    # --- 歪みの指摘 -----------------------------------------------------------
    # ⚠️ analyze は対象ごとに1件返すため、小口頻発などは数十件に膨らむ。
    # そのまま並べると読めないので**種別でまとめ、代表例と件数**にする
    distortion_rows = []
    if len(distortions):
        for kind_name, g in distortions.groupby('種別', sort=False):
            targets = [str(t) for t in g['対象'].head(3) if str(t).strip()]
            distortion_rows.append({
                'type': kind_name,
                'count': int(len(g)),
                'targets': targets,
                'more': max(int(len(g)) - len(targets), 0),
                'detail': str(g.iloc[0].get('所見', '') or ''),
            })
        distortion_rows.sort(key=lambda d: -d['count'])

    # --- 突合・除外のサマリー ------------------------------------------------
    card = df[df['source_kind'] == 'card']
    reconcile_rows = [
        {'status': k, 'label': {'matched': '両方に存在', 'zaim_only': 'Zaimのみ',
                                'enavi_only': 'e-naviのみ'}.get(k, k or '—'),
         'n': int(v)}
        for k, v in card['match_status'].value_counts().items() if k
    ]
    ex = df[~df['in_total']]
    ex_labels = {
        'zaim_exclude': 'Zaimで集計対象外', 'card_settlement': 'カード引落（二重）',
        'investment_deposit': '証券への入金', 'dup_cross_card': '二重計上の疑い',
    }
    exclusion_rows = [
        {'reason': k, 'label': ex_labels.get(k, k), 'n': int(len(g)), 'total': int(g['amount'].sum())}
        for k, g in ex.groupby('exclude_reason') if k
    ]

    return {
        'has_data': True,
        'kpi': kpi,
        'monthly_spec': monthly_spec,
        'category_rows': category_rows,
        'merchant_rows': merchant_rows,
        'sub_rows': sub_rows,
        'cand_rows': cand_rows,
        'distortion_rows': distortion_rows,
        'reconcile_rows': reconcile_rows,
        'exclusion_rows': exclusion_rows,
        'period': {'from': str(total['ym'].min()), 'to': str(total['ym'].max()),
                   'rows': int(len(df)), 'counted': int(len(total))},
    }
