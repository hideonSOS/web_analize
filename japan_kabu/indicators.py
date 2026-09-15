"""銘柄の指標（PER/PBR/ROE/ROA/配当利回り/自己資本比率）と推移の計算

旧「銘柄別指標」ページ（全銘柄 5.8MB をページに埋め込み・3,704 銘柄）は 2026-09-16 に廃止し、
指標はカルテ（登録した銘柄だけ）に吸収した。ここは **1銘柄（または少数）分だけ** 計算する前提。
- `indicators_for_stocks(stocks)` … 渡した銘柄の FinancialReport だけを引いて {code: dict} を返す
- `indicator_for_stock(stock)`     … 1銘柄
返す dict の形（旧ページの payload 1件と同じ。JS `karte_indicators.js` が描く）:
  code/name/country/currency/close/price_date/fy_end/ind{per,pbr,...}/trend{labels,sales,op,np}/hist{...}
⚠️ 日本株と米国株で四半期の意味が違う（累計 vs 単独）。TTM の求め方は _ttm_np / _ttm_np_us（CLAUDE.md）
"""
from collections import defaultdict

from .models import FinancialReport, MacroIndicator

INDICATOR_DEFS = [
    # (キー, 表示名, 単位, min, max)
    ('per', 'PER（予想）', '倍', 0, 50),
    ('pbr', 'PBR', '倍', 0, 6),
    ('roe', 'ROE（実績）', '%', -10, 30),
    ('roa', 'ROA（実績）', '%', -5, 15),
    ('yield', '配当利回り', '%', 0, 5),
    ('equity_ratio', '自己資本比率', '%', 0, 100),
]


def _indicator_values(close, *, profit, eps, bps, equity, assets, dividend, equity_ratio):
    """指標を計算する（材料は build_stock_indicator が国別に揃える）。算出不可は None

    2026-09-16 に定義を揃えた（Yahoo・決算短信との突合で ROE が 2〜4 割低く出ていたため）:
    - ROE = 利益 ÷ **自己資本の期首・期末平均**（決算短信「自己資本当期純利益率」・Yahoo と同じ）
    - ROA = 利益 ÷ **総資産の期首・期末平均**（純利益ベース。Yahoo の ROA は税引後営業利益ベースで別物）
    - 利益は日米とも **直近12か月（TTM）の親会社帰属純利益**（日本株も 1Q 開示後は TTM に更新）
    - PER は 予想 EPS があれば予想（日本株・J-Quants の来期予想）、無ければ実績 TTM（米国株・予想無しの日本株）
    """
    values = {key: None for key, *_ in INDICATOR_DEFS}
    if close is None:
        return values
    values['per'] = close / eps if eps and eps > 0 else None
    values['pbr'] = close / bps if bps and bps > 0 else None
    values['roe'] = profit / equity * 100 if profit is not None and equity else None
    values['roa'] = profit / assets * 100 if profit is not None and assets else None
    values['yield'] = dividend / close * 100 if dividend else None
    values['equity_ratio'] = equity_ratio * 100 if equity_ratio is not None else None
    return {k: round(v, 3) if v is not None else None for k, v in values.items()}


def _rep_year_before(target, reps, kinds):
    """target の約1年前（350〜380日前）の同種のレポート（期首の自己資本・総資産に使う）"""
    for r in reversed(reps):
        if r.per_type in kinds and 350 <= (target.per_end - r.per_end).days <= 380:
            return r
    return None


def _avg(a, b):
    """期首・期末の平均。期首が無ければ期末だけ（新規上場など）"""
    if a is None:
        return None
    return (a + b) / 2 if b is not None else a


def _oku(v):
    """円 → 億円（整数）。Noneはそのまま"""
    return round(v / 1e8) if v is not None else None


def _mil(v):
    """ドル → 百万ドル（整数）。Noneはそのまま"""
    return round(v / 1e6) if v is not None else None


def _r2(v):
    return round(v, 2) if v is not None else None


def _ttm_np(rep, by_key, fy_ends):
    """日本株のTTM純利益 = 直前FY通期 + 当期累計 − 前年同期累計

    J-Quantsの四半期は期初からの累計値なので、この式で12か月分に換算する。
    通期（FY）レコードはそのまま通期純利益。
    """
    if rep.per_type == 'FY':
        return rep.np
    prev_ends = [d for d in fy_ends if d < rep.fy_end]
    if not prev_ends:
        return None
    prev_fy = by_key.get((max(prev_ends), 'FY'))
    prev_cum = by_key.get((max(prev_ends), rep.per_type))
    if (prev_fy is None or prev_fy.np is None
            or prev_cum is None or prev_cum.np is None or rep.np is None):
        return None
    return prev_fy.np + rep.np - prev_cum.np


def _ttm_np_us(rep, quarters):
    """米国株のTTM純利益 = 直近4四半期の単純合計

    yfinanceの四半期は各四半期単独の数値なので、そのまま足す。
    FYレコードはそのまま通期純利益。
    """
    if rep.per_type == 'FY':
        return rep.np
    idx = quarters.index(rep)
    window = quarters[max(0, idx - 3):idx + 1]
    if len(window) < 4 or any(q.np is None for q in window):
        return None
    return sum(q.np for q in window)


HISTORY_PERIODS = 20  # 推移グラフに表示する期数（四半期×5年）


def _build_history(reps, is_us=False, close_conv=None):
    """四半期ごとの指標推移（TTMベース）。repsはper_end昇順の全レコード

    米国株は四半期が単独値、日本株は累計値なのでTTMの求め方を分ける。
    """
    by_key = {(r.fy_end, r.per_type): r for r in reps}
    fy_ends = [r.fy_end for r in reps if r.per_type == 'FY']
    # 米国株は四半期(Q)だけを時系列に並べてTTMを計算する
    us_quarters = [r for r in reps if r.per_type == 'Q'] if is_us else []
    # 米国株の推移は四半期のみを使う（FYと混ぜると同じ期が二重に並ぶため）
    series = us_quarters if is_us else reps

    # 発行済株式数・年間配当は開示がある期の値を引き継ぐ。
    # ⚠️ 日本株は J-Quants の株価が分割調整済みなのに、引き継いだ配当が分割前のまま残る（ソニー 5:1 の後
    # 4四半期の利回りが 0.6%→3% に跳ねた・2026-09-16）。株式数が前回から 1.5 倍超／0.67 倍未満に
    # 変わった期を分割とみなし、引き継ぎ中の配当を同じ比率で割る（J-Quants 無料枠に分割情報が無いための近似）
    last_shares = last_div = None
    enriched = []
    for r in series:
        if r.shares:
            if last_shares and last_div is not None and not is_us:
                ratio = r.shares / last_shares
                if ratio > 1.5 or ratio < 0.67:
                    last_div = last_div / ratio
            last_shares = r.shares
        if r.div_ann is not None:
            last_div = r.div_ann
        enriched.append((r, last_shares, last_div))

    hist = {k: [] for k in
            ('labels', 'per', 'pbr', 'roe', 'roa', 'yield', 'equity_ratio')}
    for r, shares, div in enriched[-HISTORY_PERIODS:]:
        close = close_conv(r.close, r.per_end) if close_conv else r.close   # 円建て財務なら株価を円に
        ttm = _ttm_np_us(r, us_quarters) if is_us else _ttm_np(r, by_key, fy_ends)
        eps_ttm = ttm / shares if (ttm is not None and shares) else None
        bps = r.bps if (r.bps and r.bps > 0) else (
            r.equity / shares if (r.equity and shares) else None)
        hist['labels'].append(r.per_end.strftime('%y/%m'))
        hist['per'].append(_r2(close / eps_ttm) if (close and eps_ttm and eps_ttm > 0) else None)
        hist['pbr'].append(_r2(close / bps) if (close and bps) else None)
        hist['roe'].append(_r2(ttm / r.equity * 100) if (ttm is not None and r.equity) else None)
        hist['roa'].append(_r2(ttm / r.total_assets * 100) if (ttm is not None and r.total_assets) else None)
        hist['yield'].append(_r2(div / close * 100) if (div and close) else None)
        hist['equity_ratio'].append(_r2(r.equity_ratio * 100) if r.equity_ratio is not None else None)
    return hist


def _fx():
    """最新ドル円 {'rate': float, 'date': 'YYYY/MM/DD'}。未取得なら None（円併記を出さないだけ）"""
    try:
        from portfolio.services import latest_fx_rate
        rate, d = latest_fx_rate()
    except Exception:   # noqa: BLE001
        return None
    return {'rate': rate, 'date': d.strftime('%Y/%m/%d')} if rate else None


def _fx_history():
    """月次ドル円 {date(月初): rate}（マクロの USDJPY=FRED EXJPUS・長期）。過去の期末株価を円に直す用。
    portfolio.FxRate は日次だが1か月分しか無いので履歴には使えない（実測 2026-09-16）"""
    return dict(MacroIndicator.objects.filter(series='USDJPY').order_by('date').values_list('date', 'value'))


def _fx_at(hist, latest, d):
    """日付 d 時点のドル円: その月（無ければ直前の月）の月次平均 → 無ければ最新レート"""
    if hist:
        past = [k for k in hist if k <= d]
        if past:
            return hist[max(past)]
    return latest


def _period_label(end, months):
    """期間そのものを書くラベル（2026-09-16 ユーザー指摘「2026/3期」でも1年ズレて見える）。
    通期: '25/4–26/3'、四半期: '26/4–6'。呼び方（2025年度／FY2025／2026年3月期）に依存しない"""
    m = end.month - months + 1
    y = end.year
    while m <= 0:
        m += 12
        y -= 1
    if y == end.year:
        return f'{y % 100}/{m}–{end.month}'
    return f'{y % 100}/{m}–{end.year % 100}/{end.month}'


def _jp_single_quarters(reps):
    """日本株（J-Quants）の四半期は期初からの累計なので、同じ会計年度内で前の累計を引いて
    単独値にする。4Q = FY − 3Q 累計。前の期が欠けていれば差分不能として None"""
    by_fy = defaultdict(dict)
    for r in reps:
        by_fy[r.fy_end][r.per_type] = r
    out = []
    for fy_end in sorted(by_fy):
        d = by_fy[fy_end]
        prev = None
        for pt in ('1Q', '2Q', '3Q', 'FY'):
            r = d.get(pt)
            if r is None:
                prev = None
                continue

            def single(field):
                v = getattr(r, field)
                if v is None:
                    return None
                if pt == '1Q':
                    return v
                if prev is None or getattr(prev, field) is None:
                    return None
                return v - getattr(prev, field)
            out.append({'end': r.per_end, 'sales': single('sales'), 'op': single('op'), 'np': single('np')})
            prev = r
    return out


def _yoy(cur, prev):
    """前年同期比（%）。前年が 0 以下（赤字・無し）なら比率に意味が無いので None（表は '—'）"""
    if cur is None or prev is None or prev <= 0:
        return None
    return round((cur / prev - 1) * 100, 1)


def _margin(op, sales):
    if op is None or not sales:
        return None
    return round(op / sales * 100, 1)


def _trend_pack(rows, months, scale, keep):
    """rows=[{end, sales, op, np}] 昇順 → 表示用 dict。前年同期比は「12か月前の期」（four periods back for
    quarters / one back for FY）を end の差で探す（欠けた期があってもズレない）"""
    by_end = {r['end']: r for r in rows}

    def prev_of(end):
        for k in by_end:
            dd = (end - k).days
            if 350 <= dd <= 380:
                return by_end[k]
        return None
    sel = rows[-keep:]
    pack = {'labels': [], 'sales': [], 'op': [], 'np': [], 'yoy_sales': [], 'yoy_op': [], 'yoy_np': [], 'margin': []}
    for r in sel:
        p = prev_of(r['end']) or {}
        pack['labels'].append(_period_label(r['end'], months))
        pack['sales'].append(scale(r['sales']))
        pack['op'].append(scale(r['op']))
        pack['np'].append(scale(r['np']))
        pack['yoy_sales'].append(_yoy(r['sales'], p.get('sales')))
        pack['yoy_op'].append(_yoy(r['op'], p.get('op')))
        pack['yoy_np'].append(_yoy(r['np'], p.get('np')))
        pack['margin'].append(_margin(r['op'], r['sales']))
    return pack


TREND_QUARTERS = 8   # 四半期の業績推移に出す期数（2年分）
TREND_YEARS = 5


def build_stock_indicator(stock, reps):
    """1銘柄分の指標・推移。reps は per_end 昇順の FinancialReport。通期決算が無ければ None"""
    fy_reps = [r for r in reps if r.per_type == 'FY']
    if not fy_reps:
        return None
    is_us = stock.country == 'US'
    latest = fy_reps[-1]
    trend_reps = fy_reps[-5:]
    quarters = [r for r in reps if r.per_type == 'Q']
    latest_ind_src = (quarters[-1] if quarters else latest) if is_us else latest
    # 財務の通貨。米国上場でも日本企業の ADR（PayPay）は JPY（FinancialReport.fin_currency）。
    # その場合: 業績は億円のまま（為替を掛けない）、PER/PBR は株価（USD）側を円に直して計算する
    fin_ccy = ((latest_ind_src.fin_currency or 'USD').upper() if is_us else 'JPY')
    foreign_fin = is_us and fin_ccy != 'USD'
    fx = _fx() if is_us else None
    fx_hist = _fx_history() if foreign_fin else {}
    fx_latest = fx['rate'] if fx else None

    def close_in_fin(close, d, current=False):
        """株価（取引通貨=USD）を財務の通貨へ。JPY 建て財務なら ×ドル円。換算できなければ None
        current=True（現在値の指標）は最新の日次レート、過去の期末はその月の月次平均を使う。
        ⚠️ 現在値に月次平均を使うと PER が 15.2→15.6 のようにズレる（実際に起きた 2026-09-16）"""
        if close is None or not foreign_fin:
            return close
        if fin_ccy != 'JPY':
            return None   # JPY 以外の外貨建て財務は未対応（算出不可にする）
        rate = fx_latest if current else _fx_at(fx_hist, fx_latest, d)
        return close * rate if rate else None

    # 現在値の指標（定義は _indicator_values の docstring）。利益は日米とも TTM、自己資本・総資産は期首期末平均
    if is_us:
        src = latest_ind_src
        ttm = _ttm_np_us(src, quarters) if quarters else latest.np
        prev = _rep_year_before(src, reps, {'Q', 'FY'})
        eps = (ttm / src.shares) if (ttm is not None and src.shares) else None
        per_basis = 'actual'
        close_now = close_in_fin(stock.close, stock.price_date, current=True)
        bps = src.bps if (src.bps and src.bps > 0) else ((src.equity / src.shares) if (src.equity and src.shares) else None)
        dividend = src.div_ann
        eq_ratio = src.equity_ratio
    else:
        by_key = {(r.fy_end, r.per_type): r for r in reps}
        fy_ends = [r.fy_end for r in fy_reps]
        src = reps[-1]                              # 直近の開示（1Q 等でも TTM に更新する）
        ttm = _ttm_np(src, by_key, fy_ends)
        if ttm is None:
            src, ttm = latest, latest.np            # 累計の差分が取れないときは通期実績
        prev = _rep_year_before(src, reps, {src.per_type})
        shares = src.shares or latest.shares
        if latest.nx_np and shares:                 # J-Quants の来期予想があれば予想 PER（従来どおり）
            eps, per_basis = latest.nx_np / shares, 'forecast'
        else:                                       # 予想が無い会社（NEC・ソフトバンクG 等）は実績 TTM
            eps, per_basis = ((ttm / shares) if (ttm is not None and shares) else None), 'actual'
        close_now = stock.close
        bps = src.bps if (src.bps and src.bps > 0) else ((src.equity / shares) if (src.equity and shares) else latest.bps)
        dividend = latest.nx_div_ann or latest.div_ann
        eq_ratio = src.equity_ratio if src.equity_ratio is not None else latest.equity_ratio
    ind = _indicator_values(
        close_now, profit=ttm, eps=eps, bps=bps,
        equity=_avg(src.equity, prev.equity if prev else None),
        assets=_avg(src.total_assets, prev.total_assets if prev else None),
        dividend=dividend, equity_ratio=eq_ratio)
    if fin_ccy == 'JPY':
        scale, trend_unit = _oku, '億円'
    else:
        scale, trend_unit = _mil, ('百万ドル' if fin_ccy == 'USD' else f'百万{fin_ccy}')
    return {
        'code': stock.display_code,
        'name': stock.name,
        'country': stock.country,
        'currency': 'USD' if is_us else 'JPY',
        'fin_currency': fin_ccy,          # 業績・指標の元データの通貨（JS は USD のときだけ円換算する）
        'per_basis': per_basis,           # 'forecast'（来期予想 EPS）/ 'actual'（実績 TTM）。JS がラベルに使う
        'basis_note': f'利益は {src.per_end:%Y/%m} までの12か月、自己資本・総資産は期首期末平均',
        'trend_unit': trend_unit,
        # 米国株は円換算の併記用に最新ドル円（portfolio.FxRate・夜バッチ）を添える（2026-09-16 ユーザー要望）。
        # 値そのものはドルのまま。換算は JS（業績推移の Y 軸=億円、現在値=円併記）とテンプレで行う
        'fx': fx,
        'close': stock.close,
        'price_date': stock.price_date.strftime('%Y/%m/%d') if stock.price_date else None,
        'fy_end': latest_ind_src.per_end.strftime('%Y/%m/%d'),
        'ind': ind,
        # 出典（yf を含めば「推定値」の注記を出す。通期だけ公表値のときは四半期に限った注記）
        'sources': sorted({r.source for r in reps if r.source}),
        'trend_sources': sorted({r.source or '' for r in trend_reps}),
        # 業績推移（2026-09-16 大幅更新・ユーザー指摘「直近の決算が入らない通期だけの棒は価値が低い／
        # 前年同期比も比べられない」）: 通期と四半期（単独値）の両方に 前年同期比・営業利益率 を付ける。
        # 既定表示は四半期。ラベルは期間そのもの（'25/4–26/3'・'26/4–6'）で呼び方（年度/FY）に依存しない
        'trend': _trend_pack([{'end': (r.per_end if is_us else r.fy_end), 'sales': r.sales, 'op': r.op, 'np': r.np}
                              for r in fy_reps], 12, scale, TREND_YEARS),
        'trend_q': _trend_pack(
            [{'end': r.per_end, 'sales': r.sales, 'op': r.op, 'np': r.np} for r in quarters] if is_us
            else _jp_single_quarters(reps), 3, scale, TREND_QUARTERS),
        'hist': _build_history(reps, is_us=is_us, close_conv=close_in_fin if foreign_fin else None),
    }


def indicators_for_stocks(stocks):
    """渡した銘柄だけの指標 {code: dict|None}。全銘柄を読まない（カルテ一覧の比較表・詳細用）"""
    stocks = list(stocks)
    reports = defaultdict(list)
    qs = (FinancialReport.objects
          .filter(stock_id__in=[s.code for s in stocks], per_end__isnull=False)
          .order_by('per_end'))
    for rep in qs:
        reports[rep.stock_id].append(rep)
    return {s.code: build_stock_indicator(s, reports.get(s.code, [])) for s in stocks}


def indicator_for_stock(stock):
    return indicators_for_stocks([stock]).get(stock.code)
