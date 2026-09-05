"""画面用サマリーの組み立て（DB の Transaction → 集計 → テンプレート/ECharts 用の dict）

card_insight.analyze の判定ロジックをそのまま使う。ここは DataFrame への橋渡しと、
画面に出す形（ECharts のスペックと表の行）への整形だけを行う。

集計対象は in_total=True の行のみ。手動編集（manual_*）があればそれを優先する。
"""
from __future__ import annotations

import pandas as pd

from card_insight import analyze

from .models import MonthlyIncome, SavingsPlan, Transaction

RECENT_MONTHS = 12

# 月次推移の積み上げに出すカテゴリ数。残りは「その他」に畳む。
# 12色を全部積むと凡例が読めず、スマホでは判別不能になる
MONTHLY_STACK_TOP = 7

# 「毎月引き落とし」と見なす連続月数。⚠️ 出現月数の合計で判定しないこと。
# 12か月中8か月でも、途中で一度解約して再契約したものは毎月払いに見えず弾かれる
# （実際 Claude は 2025-11〜2026-01 に空白があり「不定期」に落ちた）。
# 見るのは**直近から何か月続いているか**。
MONTHLY_MIN_STREAK = 3


def _frame() -> pd.DataFrame:
    """Transaction → analyze が期待する形の DataFrame

    analyze 側は category_final / rule_hit などの列名で参照するので合わせる。
    手動で直した分類・必要度はここで反映する（再取込では上書きされない値）。
    """
    rows = list(Transaction.objects.values(
        'date', 'ym', 'amount', 'source_kind', 'source_name', 'merchant', 'shop', 'shop_norm', 'label',
        'category', 'subcategory', 'category_source', 'kind', 'necessity',
        'in_total', 'exclude_reason', 'row_type', 'match_status', 'dup_flag',
        'manual_category', 'manual_subcategory', 'manual_necessity', 'exclude_override',
        # ⚠️ label_kind を落とすと _zaim_mislearn の「商品行だけ」の絞りが黙って無効になり、
        # 外税・レジ袋が誤学習として大量に検出される（実際にそうなった）
        'label_kind',
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
    return df


def _monthly_profile(recent: pd.DataFrame) -> dict:
    """加盟店ごとに「直近から何か月連続で出ているか」と「平常月の金額」を出す。

    平常月の金額に**平均ではなく中央値**を使うのは、年額プランへの切替や初月の
    入会金が1回混ざるだけで平均が跳ねるため（実測: Claude 8月 17,580円、
    chocoZAP 初月 9,008円。平均だと毎月の固定費が実態の2倍近くになる）。
    """
    piv = recent.pivot_table(index='merchant', columns='ym', values='amount', aggfunc='sum')
    yms = sorted(piv.columns)
    out = {}
    for merchant, row in piv.iterrows():
        i = len(yms) - 1
        # 当月はまだ引き落とし日が来ていないことがあるので、1か月だけ猶予を見る
        if i >= 0 and pd.isna(row.get(yms[i])):
            i -= 1
        streak = 0
        while i >= 0 and pd.notna(row.get(yms[i])):
            streak += 1
            i -= 1
        # 平常月の金額は連続している区間で見る。連続していない（＝止まっている）
        # ものは区間が無いので、出現した月すべてから取る
        window = yms[len(yms) - streak - 1:] if streak else yms
        vals = [float(row[y]) for y in window if pd.notna(row.get(y))]
        if streak:
            vals = vals[-streak:]
        out[merchant] = {
            'streak': streak,
            'typical': int(pd.Series(vals).median()) if vals else 0,
        }
    return out


def _record_quality(total: pd.DataFrame, months: int) -> tuple[list, dict]:
    """記録の質（支払元がどれだけ埋まっているか）を月ごとに出す。

    なぜ見るか: Zaim のレシート撮影で取り込んだ行には**支払元も店名も付かない**。
    実測で支出の62%・112万円が「どの財布から出たか分からない」状態だった。
    金額の合計は正しい（レシートとカード請求の二重計上は0件と確認済み）ので
    これは完全性の問題ではないが、店名まで無い行は後から見直しようがない。

    レシート撮影をやめて「1回の買い物＝1行」で記録すれば消える問題なので、
    続けられているかを月次で追えるようにする。**下がっていくのが見えること**が目的。
    """
    if total.empty:
        return [], {}
    g = total.groupby('ym').apply(
        lambda d: pd.Series({
            'total': int(d['amount'].sum()),
            # 取り込みで unset→cash に倒しているので source_kind では数えられない。
            # Zaim 側で支払元が空だった行は source_name の「未設定」で識別する
            'unset': int(d.loc[d['source_name'].fillna('').str.contains('未設定'), 'amount'].sum()),
            'noshop': int(d.loc[d['shop'].fillna('').str.strip() == '', 'amount'].sum()),
        }), include_groups=False).sort_index().tail(months)

    # ⚠️ 当月は締まっていない。5日時点の数字をそのまま並べると「0%に改善した」と
    # 読めてしまう（実際そう出た）。行としては出すが、判定からは必ず外す
    this_month = pd.Timestamp.today().strftime('%Y-%m')
    rows = []
    for ym, r in g.iterrows():
        tot = int(r['total']) or 1
        rows.append({
            'ym': ym, 'total': int(r['total']), 'unset': int(r['unset']),
            'noshop': int(r['noshop']),
            'pct': round(int(r['unset']) / tot * 100, 1),
            'noshop_pct': round(int(r['noshop']) / tot * 100, 1),
            'partial': ym >= this_month,
        })

    closed = [r for r in rows if not r['partial']]
    if not closed:
        return rows, {}
    latest = closed[-1]
    # 直近月だけだと偶然の上下に振り回されるので、それ以前の中央値と比べる
    before = [r['pct'] for r in closed[:-1]]
    base = float(pd.Series(before).median()) if before else latest['pct']
    delta = round(latest['pct'] - base, 1)
    summary = {
        'latest': latest, 'base': round(base, 1), 'delta': delta,
        'improving': delta < -3, 'worsening': delta > 3,
        'unset_total': sum(r['unset'] for r in rows),
        'noshop_total': sum(r['noshop'] for r in rows),
    }
    return rows, summary


def _income_rows(total: pd.DataFrame, months: int) -> tuple[list, dict]:
    """月ごとの 収入 / 支出 / 余力 / 貯蓄率。**これが入金力そのもの**。

    ⚠️ 収入が記録されていない月は「余力ゼロ」ではなく**判定不能**として扱うこと。
    0円として混ぜると貯蓄率が大きくマイナスに出て、実態と正反対の結論になる。
    """
    income = MonthlyIncome.effective()
    if not income or total.empty:
        return [], {'has_income': False, 'has_any': bool(income)}

    spend = total.groupby('ym')['amount'].sum().to_dict()
    yms = sorted(set(income) | set(spend))[-months:]
    this_month = pd.Timestamp.today().strftime('%Y-%m')

    rows = []
    for ym in yms:
        inc, exp = int(income.get(ym, 0)), int(spend.get(ym, 0))
        known = ym in income and inc > 0
        rows.append({
            'ym': ym, 'income': inc, 'spend': exp,
            'surplus': (inc - exp) if known else None,
            'rate': round((inc - exp) / inc * 100, 1) if known else None,
            'known': known, 'partial': ym >= this_month,
            # 収入を100%として支出の占める割合。横棒の塗り幅に使う
            'spend_width': round(min(exp / inc * 100, 100), 1) if known else 0,
        })

    usable = [r for r in rows if r['known'] and not r['partial']]
    if not usable:
        # 分析期間に収入が無い＝記録が途切れている。過去の水準を目安として出し、
        # 「いくら入れればいいか」の手がかりにする（空の画面を見せても動けない）
        past = sorted(income.items())[-12:]
        avg = sum(v for _, v in past) // len(past) if past else 0
        return rows, {
            'has_income': False, 'has_any': True,
            'income_last': max(income), 'spend_last': max(spend) if spend else None,
            'ref_months': len(past), 'ref_avg': avg,
            'ref_from': past[0][0] if past else None, 'ref_to': past[-1][0] if past else None,
        }

    inc_sum = sum(r['income'] for r in usable)
    exp_sum = sum(r['spend'] for r in usable)
    return rows, {
        'has_income': True,
        'months': len(usable),
        'income_avg': inc_sum // len(usable),
        'spend_avg': exp_sum // len(usable),
        'surplus_avg': (inc_sum - exp_sum) // len(usable),
        'rate': round((inc_sum - exp_sum) / inc_sum * 100, 1) if inc_sum else 0,
        'latest': usable[-1],
        # 収入の記録が支出より古いところで止まっていないか（実際に2025-04で止まっていた）
        'income_last': max(income),
        'spend_last': max(spend) if spend else None,
        'stale': bool(spend) and max(income) < max(spend),
    }


MISLEARN_MIN_ROWS = 3       # 同じ品目名がこれ以上あるときだけ疑う（1〜2件では偶然と区別できない）
MISLEARN_FOOD_HINT = ('食費', '飲料')


def _zaim_mislearn(total: pd.DataFrame) -> list:
    """Zaim の誤学習の疑いがある品目を、**ルールを書かずに**データから見つける。

    Zaim は品目名から分類を学習するため、誤ると同じ品目が毎回同じ誤りで入る
    （ごぼう→通信 が18回）。品目ルール（LabelRule）は知っている誤りにしか効かないので、
    **未知の誤り**を拾う検出が別に要る。

    判定: 同じ品目名（商品行・Zaim由来）が複数のカテゴリに割れていて、少数派が
    通信・遊び・教育など**食料品ではないカテゴリ**にあるもの。
    ⚠️ 自動では直さない。人が見て LabelRule に足すか、明細で直す。
    """
    if total.empty:
        return []
    df = total[(total['category_source'] == 'zaim')]
    if 'label_kind' in df.columns:
        df = df[df['label_kind'].eq('item')]
    df = df[df['label'].fillna('').str.strip() != '']
    if df.empty:
        return []
    g = df.groupby(['label', 'category_final'])['amount'].agg(['size', 'sum']).reset_index()
    out = []
    for label, grp in g.groupby('label'):
        if grp['size'].sum() < MISLEARN_MIN_ROWS or len(grp) < 2:
            continue
        grp = grp.sort_values('size', ascending=False)
        major = grp.iloc[0]
        minors = grp.iloc[1:]
        # 多数派か少数派のどちらかが食料品なら「食べ物が別カテゴリに紛れた」疑い
        cats = set(grp['category_final'])
        if not cats & set(MISLEARN_FOOD_HINT):
            continue
        odd = grp[~grp['category_final'].isin(MISLEARN_FOOD_HINT)]
        if odd.empty:
            continue
        out.append({
            'label': label,
            'major': major['category_final'], 'major_n': int(major['size']),
            'odd': ' / '.join(f"{r['category_final']}×{int(r['size'])}" for _, r in odd.iterrows()),
            'odd_n': int(odd['size'].sum()), 'odd_sum': int(odd['sum'].sum()),
        })
    out.sort(key=lambda r: -r['odd_n'])
    return out[:12]


RECEIPT_MIN_LINES = 4                       # これ以上の商品行があれば「レシート」とみなす
RECEIPT_NONFOOD = ('交通', '通信', '遊び', '教育・教養', '医療・保険', '大型出費', '未分類')


def _receipt_suspects(total: pd.DataFrame) -> list:
    """レシートごと別カテゴリに紛れた疑いを、行数の形から見つける。

    _zaim_mislearn は「同じ品目名が繰り返し誤る」ケースしか拾えない。
    実際にはスーパーのレシート1枚（10行）が丸ごと「交通」に入っていた
    （2026-01-02・定期代と同じ日）。品目はどれも1回しか出ないので品目単位では見えない。

    交通・通信・医療のレシートは1〜2行で終わる。**4行以上の商品行が非食料品カテゴリに
    並んでいたら、それは食料品のレシート**の可能性が高い。閾値は実測から
    （2026-01-02 交通10行 / スギ薬局 医療・保険5行 / ダイソー 健康6行）。
    ⚠️ 自動では直さない。ダイソーの「健康」6行のように本物のこともある。
    """
    if total.empty or 'label_kind' not in total.columns:
        return []
    df = total[total['label_kind'].eq('item') & total['category_source'].isin(['zaim', 'fix'])].copy()
    if df.empty:
        return []
    df['shop'] = df['shop'].fillna('').astype(str)
    df['cat'] = df['category_final']
    g = (df.groupby(['ym', 'date', 'shop', 'cat'])
           .agg(n=('amount', 'size'), s=('amount', 'sum'), sample=('label', lambda x: ' / '.join(str(v)[:8] for v in list(x)[:4])))
           .reset_index())
    g = g[(g['n'] >= RECEIPT_MIN_LINES) & g['cat'].isin(RECEIPT_NONFOOD)]
    out = [{
        'date': pd.Timestamp(r['date']).strftime('%Y-%m-%d'),
        'ym': r['ym'], 'shop': r['shop'] or '(店名なし)',
        'category': r['cat'], 'n': int(r['n']), 'sum': int(r['s']), 'sample': r['sample'],
    } for _, r in g.sort_values(['n', 's'], ascending=False).iterrows()]
    return out[:12]


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

    # --- カテゴリの順位と色（月次推移と内訳で共用） ---------------------------
    # ⚠️ 2つのカードで同じカテゴリは同じ色にすること。別々に採番すると
    # 「推移の青」と「内訳の青」が違うカテゴリを指して読み違える
    cat = (recent.groupby('category_final')['amount'].sum()
           .sort_values(ascending=False).head(12))
    palette = ['#1e90ff', '#8b5cf6', '#34d399', '#fbbf24', '#f87171', '#63b3ff',
               '#c4b5fd', '#f9a8d4', '#5eead4', '#fdba74', '#94a3b8', '#a3e635']
    cat_color = {(n or '未分類'): palette[i % len(palette)] for i, (n, _) in enumerate(cat.items())}

    # --- 月次推移（カテゴリ別の積み上げ） -----------------------------------
    # 以前は支払元（カード/現金/未設定）で積んでいたが、支払元未設定が62%を占めるため
    # ほぼ単色になり「何のグラフか分からない」とユーザー指摘。支払元の内訳は
    # 「記録の質」カードが担うので、ここは**何に使ったか**を見せる
    piv = (total.pivot_table(index='ym', columns='category_final', values='amount',
                             aggfunc='sum', fill_value=0).sort_index())
    piv.columns = [(c or '未分類') for c in piv.columns]
    # 積み上げは上位だけにし、残りは「その他」に畳む（12色を全部積むと凡例が読めない）
    top = [c for c in cat_color if c in piv.columns][:MONTHLY_STACK_TOP]
    rest = [c for c in piv.columns if c not in top]
    series = [{'name': c, 'color': cat_color[c], 'data': [int(v) for v in piv[c]]} for c in top]
    if rest:
        series.append({'name': 'その他', 'color': '#4b5563',
                       'data': [int(v) for v in piv[rest].sum(axis=1)]})
    monthly_spec = {
        'months': list(piv.index),
        'series': series,
        'totals': [int(v) for v in piv.sum(axis=1)],
    }

    # --- カテゴリ内訳（直近） ---------------------------------------------
    cat_total = int(cat.sum()) or 1
    category_rows = [
        {'name': n or '未分類', 'value': int(v), 'pct': round(int(v) / cat_total * 100, 1),
         'color': cat_color[n or '未分類']}
        for n, v in cat.items()
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

    # --- サブスク一覧 -------------------------------------------------------
    # ⚠️ 「毎月引き落とし」と「年払い」「止まっているもの」を同じ表に並べると誤読する。
    # 実際 Amazon Prime（年1回）に月額492円と出て毎月払っているように見えていた。
    # 出現月数で3つに分ける（ユーザー要望）:
    #   monthly  直近12か月で10か月以上 → 毎月確実に出ていく固定費
    #   yearly   出現は少ないが kind=年会費、または年1〜2回の課金
    #   check    数か月だけ出て止まっている → 解約済み？ 使っていない？ の確認対象
    sub_rows, sub_monthly, sub_yearly, sub_check = [], [], [], []
    profile = _monthly_profile(recent)
    if len(subs):
        for r in subs.sort_values('年額換算', ascending=False).to_dict('records'):
            name = r.get('merchant', '')
            prof = profile.get(name, {'streak': 0, 'typical': 0})
            kind = r.get('kind', '')
            active = bool(r.get('直近月に発生'))
            streak = prof['streak']
            row = {
                'merchant': name,
                # ⚠️ 月額換算は「窓内の総額÷12」。途中で始めたものは実額より小さく出るし、
                # 年1回払いは払っていない月も払っているように見える。毎月の固定費として
                # 足し上げてよいのは平常月の実額（typical）だけ
                'monthly': int(prof['typical']),
                'annual': int(r.get('年額換算') or 0),
                'annual_at_current': int(prof['typical']) * 12,
                'necessity': r.get('necessity', ''),
                'kind': kind,
                'months': int(r.get('months') or 0),
                'streak': streak,
                'active': active,
                'first': str(pd.Timestamp(r['first']).to_period('M')),
                'last': str(pd.Timestamp(r['last']).to_period('M')),
                'note': r.get('note', '') or '',
            }
            sub_rows.append(row)

            if streak >= MONTHLY_MIN_STREAK:
                sub_monthly.append(row)      # 直近から続いている＝毎月きっちり出ていく
            elif kind == '年会費' or active:
                sub_yearly.append(row)       # 年1回、または飛び飛びだが今も課金されている
            else:
                sub_check.append(row)        # 止まっている＝解約済み？ の確認対象

    sub_monthly.sort(key=lambda r: -r['monthly'])
    sub_yearly.sort(key=lambda r: -r['annual'])
    sub_check.sort(key=lambda r: -r['annual'])

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
        # 銀行明細由来（2026-09-06）。別経路で持っている／支出ではないので除外
        'cash_withdrawal': 'ATM引き出し（使途はZaimのレシート）',
        'investment_transfer': '証券口座との入出金（投資）',
    }
    exclusion_rows = [
        {'reason': k, 'label': ex_labels.get(k, k), 'n': int(len(g)), 'total': int(g['amount'].sum())}
        for k, g in ex.groupby('exclude_reason') if k
    ]

    quality_rows, quality = _record_quality(total, months)
    suspect_rows = _zaim_mislearn(total)
    receipt_rows = _receipt_suspects(total)
    income_rows, income = _income_rows(total, months)

    return {
        'has_data': True,
        'kpi': kpi,
        'quality_rows': quality_rows,
        'quality': quality,
        'income_rows': income_rows,
        'income': income,
        'suspect_rows': suspect_rows,
        'receipt_rows': receipt_rows,
        'monthly_spec': monthly_spec,
        'monthly_stack_top': MONTHLY_STACK_TOP,
        'category_rows': category_rows,
        'merchant_rows': merchant_rows,
        'sub_rows': sub_rows,
        'sub_monthly': sub_monthly,
        'sub_yearly': sub_yearly,
        'sub_check': sub_check,
        # 「毎月確実に出ていく固定費」の実額。ここが節約判断の起点になる
        'sub_monthly_total': sum(r['monthly'] for r in sub_monthly),
        'sub_monthly_annual': sum(r['annual_at_current'] for r in sub_monthly),
        'cand_rows': cand_rows,
        'distortion_rows': distortion_rows,
        'reconcile_rows': reconcile_rows,
        'exclusion_rows': exclusion_rows,
        'period': {'from': str(total['ym'].min()), 'to': str(total['ym'].max()),
                   'rows': int(len(df)), 'counted': int(len(total))},
    }
