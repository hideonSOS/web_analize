import math
import statistics
from collections import defaultdict

from django.shortcuts import render

from .models import DailyPrice, FinancialReport, Stock

RANKING_LIMIT = 100  # 常に上位100件を表示（TOP選択UIは廃止）

# 国別タブ（時価総額・出来高ランキング共通）。主戦場が米国株なのでUSを先頭・既定にする
COUNTRIES = [('US', '米国株'), ('JP', '日本株')]


def _parse_country(request):
    c = request.GET.get('country', 'US')
    return c if c in dict(COUNTRIES) else 'US'

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
    """時価総額ランキング（横棒グラフ）。国別タブで日本株/米国株を切替。
    常に上位100件を表示（TOP選択・セクター絞り込みUIは廃止）。"""
    country = _parse_country(request)
    qs = Stock.objects.filter(country=country, market_cap__isnull=False)

    stocks = list(qs.order_by('-market_cap')[:RANKING_LIMIT])
    chart_data = {
        'labels': [f'{s.display_code} {s.name}' for s in stocks],
        'values': [s.market_cap for s in stocks],
        'markets': [s.market for s in stocks],
        'sectors': [s.sector33 for s in stocks],
        'currency': 'USD' if country == 'US' else 'JPY',
    }
    price_date = stocks[0].price_date if stocks else None
    context = {
        'stocks': stocks,
        'chart_data': chart_data,
        'price_date': price_date,
        'total_count': qs.count(),
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/index.html', context)


def volume_ranking(request):
    """出来高急増ランキング

    並び順は対数出来高のz-score（標準化された異常度）、
    表示は倍率（過去20日平均比）と確率値 p = Φ(z) を併記する。
    常に上位100件を表示（TOP選択UIは廃止）。
    """
    country = _parse_country(request)
    qs = Stock.objects.filter(country=country, volume_z__isnull=False)
    stocks = list(qs.order_by('-volume_z')[:RANKING_LIMIT])

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
        'volume_date': stocks[0].volume_date if stocks else None,
        'total_count': qs.count(),
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/volume.html', context)


IMPULSE_MEMBERS_SHOWN = 3   # セクター名の下に出す銘柄コードの数。以降は「他N」に畳む


def _impulse_band(values):
    """セクター自身のボラティリティから色の判定幅（±%）を決める

    σは**外れ値に強いMAD基準**（中央絶対偏差×1.4826）で推定する。単純な標準偏差だと、
    1日の異常値でσが跳ね上がり、その行が全部「中立（青）」に潰れて無音になる
    （実測: IBMの7/14に-25.2%という日があり、σが2.16→4.50と倍増した。
    その1日を含むだけで判定幅が倍になり、通常の値動きが全て中立扱いになる）。
    """
    from .impulse import MIN_BAND, MIN_HISTORY, NEUTRAL_BAND, SIGMA_BAND

    if len(values) < MIN_HISTORY:
        return NEUTRAL_BAND        # 履歴不足では推定できないので固定値に退避
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    # MADが0になるのは値が全く動かない場合。そのときだけ標準偏差で代替する
    sigma = mad * 1.4826 if mad > 0 else statistics.pstdev(values)
    return max(sigma * SIGMA_BAND, MIN_BAND)


def _members_label(members):
    """"AAPL,MSFT,GOOGL 他2" 形式の短いラベル

    構成銘柄を全部並べるとセクター列が横に伸び、スマホでヒートマップ本体を
    押し出してしまうため先頭数件で打ち切る（全銘柄は title 属性で見られる）。
    """
    codes = [m.display_code for m in members]
    head = ','.join(codes[:IMPULSE_MEMBERS_SHOWN])
    rest = len(codes) - IMPULSE_MEMBERS_SHOWN
    return f'{head} 他{rest}' if rest > 0 else head


def impulse(request):
    """独自セクター別インパルス（時系列ヒートマップ）。国別タブで日本株/米国株を切替。

    横軸=直近20営業日、縦軸=独自セクター（impulse.py で定義した数銘柄のグループ）。
    各セルはその日のセクター騰落率（構成銘柄の単純平均）を3値分類して色で示す。
    **判定はセクター自身のボラティリティ基準（±SIGMA_BAND σ）** で行う。理由は
    impulse.py の SIGMA_BAND のコメント参照（固定%だと行間比較が壊れる）。
    数日並べることでモメンタムの継続・転換を見る（1日分ならTradingViewで足りる）。

    データは update_impulse_prices が DailyPrice に蓄積した調整後終値。
    サーバー側で全計算し、テンプレートは色分けセルを並べるだけ（JS不要）。
    """
    from .impulse import (DAYS_SHOWN, IMPULSE_SECTORS, SIGMA_BAND,
                          dummy_change, impulse_universe)

    country = _parse_country(request)
    codes = impulse_universe(country)
    if country == 'JP':
        stocks = {s.display_code: s for s in
                  Stock.objects.filter(country='JP', display_code__in=codes)}
    else:
        stocks = {s.display_code: s for s in
                  Stock.objects.filter(country='US', code__in=[f'US-{c}' for c in codes])}

    # 銘柄ごとの騰落率系列 {display_code: {date: pct}}（その銘柄自身の直前営業日比）
    changes = {}
    all_dates = set()
    prices = DailyPrice.objects.filter(
        stock__in=stocks.values()).order_by('stock_id', 'date')
    series = defaultdict(list)
    for p in prices.values_list('stock__display_code', 'date', 'close'):
        series[p[0]].append((p[1], p[2]))
    for code, rows in series.items():
        chg = {}
        for (d0, c0), (d1, c1) in zip(rows, rows[1:]):
            if c0 > 0:
                chg[d1] = (c1 / c0 - 1) * 100
        changes[code] = chg
        all_dates.update(chg)

    all_sorted = sorted(all_dates)
    dates = all_sorted[-DAYS_SHOWN:]

    rows = []
    for sec in IMPULSE_SECTORS.get(country, []):
        codes = sec.get('codes') or []
        # 構成銘柄が未定義の行はダミー値で描く（レイアウト確認用。銘柄を入れれば実データに）
        is_dummy = not codes

        # セクター日次騰落率の**全履歴**（σ推定用）。表示20日だけで推定すると
        # 標本が少なく、窓がずれるたびに判定幅が動いて色がちらつく
        full = {}
        for d in all_sorted:
            if is_dummy:
                full[d] = dummy_change(sec['name'], d)
                continue
            vals = [changes[c][d] for c in codes if c in changes and d in changes[c]]
            if vals:
                full[d] = sum(vals) / len(vals)

        band = _impulse_band(list(full.values()))
        cells = []
        for d in dates:
            pct = full.get(d)
            if pct is None:
                cells.append({'state': 'na', 'chg': None, 'sigma': None})  # 休場・データ未取得
                continue
            cells.append({
                'state': 'up' if pct > band else 'down' if pct < -band else 'flat',
                'chg': round(pct, 2),
                'sigma': round(pct / band * SIGMA_BAND, 1),   # 何σ動いたか
            })

        members = [stocks[c] for c in codes if c in stocks]
        rows.append({
            'name': sec['name'],
            'members': members,
            'members_label': _members_label(members),
            'is_dummy': is_dummy,
            'band': round(band, 2),
            'cells': cells,
        })

    context = {
        'rows': rows,
        'dates': dates,
        'has_data': bool(dates),
        'sigma_band': SIGMA_BAND,
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/impulse.html', context)


# セクターヒートマップ: 1セクターに個別表示する銘柄数の上限。
# それ以下の小型株は「その他」1タイルに合算する（タイルが米粒化して読めなくなるため）
HEATMAP_TILES_PER_SECTOR = 20


def heatmap(request):
    """セクター別ヒートマップ（Finviz風ツリーマップ）。国別タブで日本株/米国株を切替。

    面積=時価総額、色=前日比（change_pct）。セクターは JP=17業種 / US=GICS（sector17）。
    セクターの前日比は時価総額加重平均（全構成銘柄で算出）。
    個別タイルは時価総額上位 HEATMAP_TILES_PER_SECTOR 銘柄まで、残りは「その他」に合算。
    描画は自前JS（heatmap.js の squarified treemap）で外部ライブラリ依存なし。
    """
    country = _parse_country(request)
    qs = Stock.objects.filter(
        country=country, market_cap__isnull=False, change_pct__isnull=False,
    ).exclude(sector17='')

    by_sector = defaultdict(list)
    for s in qs.only('code', 'display_code', 'name', 'sector17',
                     'market_cap', 'change_pct', 'price_date'):
        by_sector[s.sector17].append(s)

    sectors = []
    price_date = None
    for name, stocks in by_sector.items():
        stocks.sort(key=lambda s: s.market_cap, reverse=True)
        total_cap = sum(s.market_cap for s in stocks)
        if total_cap <= 0:
            continue
        # セクターの前日比 = 時価総額加重平均（全構成銘柄）
        w_change = sum(s.market_cap * s.change_pct for s in stocks) / total_cap
        tiles = [{
            'code': s.display_code,
            'name': s.name,
            'cap': s.market_cap,
            'chg': round(s.change_pct, 2),
        } for s in stocks[:HEATMAP_TILES_PER_SECTOR]]
        rest = stocks[HEATMAP_TILES_PER_SECTOR:]
        if rest:
            rest_cap = sum(s.market_cap for s in rest)
            rest_chg = sum(s.market_cap * s.change_pct for s in rest) / rest_cap
            tiles.append({'code': '', 'name': f'その他{len(rest)}銘柄',
                          'cap': rest_cap, 'chg': round(rest_chg, 2)})
        sectors.append({
            'name': name,
            'cap': total_cap,
            'chg': round(w_change, 2),
            'count': len(stocks),
            'tiles': tiles,
        })
        d = stocks[0].price_date
        if d and (price_date is None or d > price_date):
            price_date = d

    sectors.sort(key=lambda x: x['cap'], reverse=True)

    context = {
        'heatmap_data': {
            'sectors': sectors,
            'currency': 'USD' if country == 'US' else 'JPY',
        },
        'sectors': sectors,
        'price_date': price_date,
        'total_count': qs.count(),
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/heatmap.html', context)


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
