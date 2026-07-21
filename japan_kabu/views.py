import math
from collections import defaultdict

from django.shortcuts import render

from .models import FinancialReport, Stock

RANKING_LIMIT_DEFAULT = 50
RANKING_LIMIT_MAX = 200

# 指標ごとの表示レンジ（棒グラフのx軸min/max）。日本株の一般的な水準を目安に設定
INDICATOR_DEFS = [
    # (キー, 表示名, 単位, min, max)
    ('per', 'PER（予想）', '倍', 0, 50),
    ('pbr', 'PBR', '倍', 0, 6),
    ('roe', 'ROE（実績）', '%', -10, 30),
    ('roa', 'ROA（実績）', '%', -5, 15),
    ('yield', '配当利回り', '%', 0, 5),
    ('equity_ratio', '自己資本比率', '%', 0, 100),
]


def index(request):
    """日本株 時価総額ランキング（横棒グラフ）"""
    try:
        limit = int(request.GET.get('limit', RANKING_LIMIT_DEFAULT))
    except ValueError:
        limit = RANKING_LIMIT_DEFAULT
    limit = max(1, min(limit, RANKING_LIMIT_MAX))

    # セクター（17業種）フィルタ
    sectors = list(
        Stock.objects.filter(market_cap__isnull=False)
        .exclude(sector17='')
        .values_list('sector17', flat=True)
        .distinct().order_by('sector17')
    )
    sector = request.GET.get('sector', '')
    if sector not in sectors:
        sector = ''

    qs = Stock.objects.filter(market_cap__isnull=False)
    if sector:
        qs = qs.filter(sector17=sector)

    stocks = list(qs.order_by('-market_cap')[:limit])
    chart_data = {
        'labels': [f'{s.display_code} {s.name}' for s in stocks],
        'values': [s.market_cap for s in stocks],
        'markets': [s.market for s in stocks],
        'sectors': [s.sector33 for s in stocks],
    }
    price_date = stocks[0].price_date if stocks else None
    context = {
        'stocks': stocks,
        'chart_data': chart_data,
        'limit': limit,
        'price_date': price_date,
        'total_count': qs.count(),
        'sectors': sectors,
        'sector': sector,
    }
    return render(request, 'japan_kabu/index.html', context)


def _parse_limit(request):
    try:
        limit = int(request.GET.get('limit', RANKING_LIMIT_DEFAULT))
    except ValueError:
        limit = RANKING_LIMIT_DEFAULT
    return max(1, min(limit, RANKING_LIMIT_MAX))


def volume_ranking(request):
    """出来高急増ランキング

    並び順は対数出来高のz-score（標準化された異常度）、
    表示は倍率（過去20日平均比）と確率値 p = Φ(z) を併記する。
    """
    limit = _parse_limit(request)
    qs = Stock.objects.filter(volume_z__isnull=False)
    stocks = list(qs.order_by('-volume_z')[:limit])

    rows = []
    for rank, s in enumerate(stocks, 1):
        p = 0.5 * (1 + math.erf(s.volume_z / math.sqrt(2)))  # Φ(z)
        rows.append({'rank': rank, 'stock': s, 'p': p * 100})

    chart_data = {
        'labels': [f'{s.display_code} {s.name}' for s in stocks],
        'z': [round(s.volume_z, 2) for s in stocks],
        'ratios': [round(s.volume_ratio, 2) for s in stocks],
        'p': [round(r['p'], 2) for r in rows],
        'sectors': [s.sector33 for s in stocks],
    }
    context = {
        'rows': rows,
        'chart_data': chart_data,
        'limit': limit,
        'volume_date': stocks[0].volume_date if stocks else None,
        'total_count': qs.count(),
    }
    return render(request, 'japan_kabu/volume.html', context)


def _indicator_values(close, rep, ttm_np=None):
    """終値と決算データから指標を計算する。算出不可はNone

    ttm_np を渡すと、それを利益として使う（米国株のTTM実績ベース）。
    渡さない場合は日本株の想定で、来期予想EPS/通期純利益を使う。
    """
    values = {key: None for key, *_ in INDICATOR_DEFS}
    if rep is None or close is None:
        return values
    # 日本株は来期予想EPSベース（予想PER）。米国株は予想が無いのでTTM実績を渡す
    if ttm_np is not None:
        eps = ttm_np / rep.shares if rep.shares else None
        profit = ttm_np
    else:
        eps = rep.nx_np / rep.shares if rep.nx_np and rep.shares else None
        profit = rep.np
    dividend = rep.nx_div_ann or rep.div_ann
    values['per'] = close / eps if eps and eps > 0 else None
    values['pbr'] = close / rep.bps if rep.bps and rep.bps > 0 else None
    values['roe'] = profit / rep.equity * 100 if profit is not None and rep.equity else None
    values['roa'] = profit / rep.total_assets * 100 if profit is not None and rep.total_assets else None
    values['yield'] = dividend / close * 100 if dividend else None
    values['equity_ratio'] = rep.equity_ratio * 100 if rep.equity_ratio is not None else None
    return {k: round(v, 3) if v is not None else None for k, v in values.items()}


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


def _build_history(reps, is_us=False):
    """四半期ごとの指標推移（TTMベース）。repsはper_end昇順の全レコード

    米国株は四半期が単独値、日本株は累計値なのでTTMの求め方を分ける。
    """
    by_key = {(r.fy_end, r.per_type): r for r in reps}
    fy_ends = [r.fy_end for r in reps if r.per_type == 'FY']
    # 米国株は四半期(Q)だけを時系列に並べてTTMを計算する
    us_quarters = [r for r in reps if r.per_type == 'Q'] if is_us else []
    # 米国株の推移は四半期のみを使う（FYと混ぜると同じ期が二重に並ぶため）
    series = us_quarters if is_us else reps

    # 発行済株式数・年間配当は開示がある期の値を引き継ぐ
    last_shares = last_div = None
    enriched = []
    for r in series:
        if r.shares:
            last_shares = r.shares
        if r.div_ann is not None:
            last_div = r.div_ann
        enriched.append((r, last_shares, last_div))

    hist = {k: [] for k in
            ('labels', 'per', 'pbr', 'roe', 'roa', 'yield', 'equity_ratio')}
    for r, shares, div in enriched[-HISTORY_PERIODS:]:
        close = r.close
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


def _build_indicator_payload():
    """全銘柄分の指標・推移データ（フロント側で銘柄切替するための一括データ）"""
    reports = defaultdict(list)
    for rep in FinancialReport.objects.filter(per_end__isnull=False).order_by('per_end').iterator():
        reports[rep.stock_id].append(rep)

    payload = []
    for s in Stock.objects.all():  # Metaのorderingで時価総額の大きい順
        reps = reports.get(s.code)
        if not reps:
            continue
        fy_reps = [r for r in reps if r.per_type == 'FY']
        if not fy_reps:
            continue
        is_us = s.country == 'US'
        latest = fy_reps[-1]
        trend_reps = fy_reps[-5:]

        # 米国株は最新の四半期を現在値の算出に使う（yfinanceに来期予想が無いため
        # PERは実績TTMベースになる。日本株は予想EPSベース）
        quarters = [r for r in reps if r.per_type == 'Q']
        if is_us:
            latest_ind_src = quarters[-1] if quarters else latest
            ttm = _ttm_np_us(latest_ind_src, quarters) if quarters else latest.np
            ind = _indicator_values(s.close, latest_ind_src, ttm_np=ttm)
        else:
            ind = _indicator_values(s.close, latest)

        # 通貨と単位が異なる（日本株=億円 / 米国株=百万ドル）
        scale = _mil if is_us else _oku
        payload.append({
            'code': s.display_code,
            'name': s.name,
            'market': s.market,
            'sector': s.sector33,
            'country': s.country,
            'currency': 'USD' if is_us else 'JPY',
            'trend_unit': '百万ドル' if is_us else '億円',
            'close': s.close,
            'price_date': s.price_date.strftime('%Y/%m/%d') if s.price_date else None,
            'fy_end': (latest_ind_src if is_us else latest).per_end.strftime('%Y/%m/%d'),
            'ind': ind,
            'trend': {
                'labels': [f'{r.per_end.year}年' if is_us else f'{r.fy_end.year}年度'
                           for r in trend_reps],
                'sales': [scale(r.sales) for r in trend_reps],
                'op': [scale(r.op) for r in trend_reps],
                'np': [scale(r.np) for r in trend_reps],
            },
            'hist': _build_history(reps, is_us=is_us),
        })
    return payload


def stock_detail(request, code=None):
    """銘柄別指標ダッシュボード

    全銘柄分のデータをページに一括埋め込みし、銘柄の切替（銘柄名で検索できる
    ドロップダウン）はフロント側だけで行う。バックエンドへの再アクセスは不要。
    """
    payload = _build_indicator_payload()
    codes = {d['code'] for d in payload}
    initial = code if code in codes else (payload[0]['code'] if payload else None)
    context = {
        'payload': payload,
        'indicator_defs': [
            {'key': k, 'label': label, 'unit': unit, 'min': mn, 'max': mx}
            for k, label, unit, mn, mx in INDICATOR_DEFS
        ],
        'initial_code': initial,
        'count': len(payload),
    }
    return render(request, 'japan_kabu/stock_detail.html', context)
