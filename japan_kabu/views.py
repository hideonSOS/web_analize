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


def _indicator_values(close, rep):
    """終値と通期決算（FinancialReport）から指標を計算する。算出不可はNone"""
    values = {key: None for key, *_ in INDICATOR_DEFS}
    if rep is None or close is None:
        return values
    forecast_eps = rep.nx_np / rep.shares if rep.nx_np and rep.shares else None
    dividend = rep.nx_div_ann or rep.div_ann
    values['per'] = close / forecast_eps if forecast_eps and forecast_eps > 0 else None
    values['pbr'] = close / rep.bps if rep.bps and rep.bps > 0 else None
    values['roe'] = rep.np / rep.equity * 100 if rep.np is not None and rep.equity else None
    values['roa'] = rep.np / rep.total_assets * 100 if rep.np is not None and rep.total_assets else None
    values['yield'] = dividend / close * 100 if dividend else None
    values['equity_ratio'] = rep.equity_ratio * 100 if rep.equity_ratio is not None else None
    return {k: round(v, 3) if v is not None else None for k, v in values.items()}


def _oku(v):
    """円 → 億円（整数）。Noneはそのまま"""
    return round(v / 1e8) if v is not None else None


def _r2(v):
    return round(v, 2) if v is not None else None


def _ttm_np(rep, by_key, fy_ends):
    """TTM（直近12か月）純利益 = 直前FY通期 + 当期累計 − 前年同期累計

    四半期の純利益は期初からの累計値なので、この式で12か月分に換算する。
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


HISTORY_PERIODS = 20  # 推移グラフに表示する期数（四半期×5年）


def _build_history(reps):
    """四半期ごとの指標推移（TTMベース）。repsはper_end昇順の全レコード"""
    by_key = {(r.fy_end, r.per_type): r for r in reps}
    fy_ends = [r.fy_end for r in reps if r.per_type == 'FY']

    # 発行済株式数・年間配当は開示がある期の値を引き継ぐ
    last_shares = last_div = None
    enriched = []
    for r in reps:
        if r.shares:
            last_shares = r.shares
        if r.per_type == 'FY' and r.div_ann is not None:
            last_div = r.div_ann
        enriched.append((r, last_shares, last_div))

    hist = {k: [] for k in
            ('labels', 'per', 'pbr', 'roe', 'roa', 'yield', 'equity_ratio')}
    for r, shares, div in enriched[-HISTORY_PERIODS:]:
        close = r.close
        ttm = _ttm_np(r, by_key, fy_ends)
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
        latest = fy_reps[-1]
        trend_reps = fy_reps[-5:]
        payload.append({
            'code': s.display_code,
            'name': s.name,
            'market': s.market,
            'sector': s.sector33,
            'close': s.close,
            'price_date': s.price_date.strftime('%Y/%m/%d') if s.price_date else None,
            'fy_end': latest.fy_end.strftime('%Y/%m/%d'),
            'ind': _indicator_values(s.close, latest),
            'trend': {
                'labels': [f'{r.fy_end.year}年度' for r in trend_reps],
                'sales': [_oku(r.sales) for r in trend_reps],
                'op': [_oku(r.op) for r in trend_reps],
                'np': [_oku(r.np) for r in trend_reps],
            },
            'hist': _build_history(reps),
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
