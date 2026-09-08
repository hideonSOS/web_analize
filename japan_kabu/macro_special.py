"""マクロ指標「特殊」タブ: 複数の時系列を標準化して重ね、相関を見る（2026-09-08）。

ユーザー要望: 「S&P500 と政策金利のようにスケールが違う指標を −1〜+1 に標準化して
1つの折れ線グラフで重ね、相関を視覚的に調べたい」。

設計:
- 系列のカタログ（CATALOG）は MacroIndicator の保存キーに「変換」を組み合わせたもの。
  CPI は指数のままだと右肩上がりで相関が意味を持たないので前年比も用意する
- 標準化は表示期間の中だけで行う（期間を変えると形が変わる。これは仕様。全期間で
  標準化すると直近10年が平坦に潰れる）
    minmax: −1〜+1（最小→−1、最大→+1）。ユーザー指定の既定
    z     : 平均0・標準偏差1（外れ値の影響を見るとき用）
- 相関係数（ピアソン）は**生の値**で計算する。線形変換で不変なので標準化前後で同じ。
  重なっている月だけで計算し、月数も出す（短いと信用できない、はセクター相関で踏んだ教訓）
- ⚠️ 相関は因果ではない。両方が時間トレンドを持つだけで高相関になる（見せかけの相関）。
  画面の注意書きに明記する。ラグ相関（何か月先行するか）は v1 では持たない
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

from .models import MacroIndicator

# key, 表示名, 保存キー, 変換, 色
CATALOG = [
    ('SP500',     'S&P500（月末値）',            'SP500',        'level', '#1e90ff'),
    ('N225',      '日経平均（月末値）',           'N225',         'level', '#f97316'),
    ('USDJPY',    'ドル円（月中平均）',           'USDJPY',       'level', '#facc15'),
    ('FEDFUNDS',  '米 政策金利（FF金利）',        'FEDFUNDS',     'level', '#9ca3af'),
    ('GS10',      '米 10年国債利回り',           'GS10',         'level', '#60a5fa'),
    ('GS2',       '米 2年国債利回り',            'GS2',          'level', '#93c5fd'),
    ('UNRATE',    '米 失業率',                   'UNRATE',       'level', '#4ade80'),
    ('US_CPI',    '米 CPI 前年比',               'CPIAUCSL',     'yoy',   '#f87171'),
    ('US_CORE',   '米 コアCPI 前年比',           'CPILFESL',     'yoy',   '#fb923c'),
    ('JPCALL',    '日 政策金利（コールレート）',  'JPCALL',       'level', '#a78bfa'),
    ('JP10Y',     '日 10年国債利回り',           'JP10Y',        'level', '#c4b5fd'),
    ('JPUNRATE',  '日 失業率',                   'JPUNRATE',     'level', '#86efac'),
    ('JP_CORE',   '日 コアCPI 前年比',           'JPCPI_CORE',   'yoy',   '#fca5a5'),
]
CATALOG_BY_KEY = {c[0]: c for c in CATALOG}
DEFAULT_KEYS = ['SP500', 'FEDFUNDS']
MAX_SERIES = 5
NORMS = [('minmax', '−1〜+1（最小・最大）'), ('z', 'zスコア（平均0・標準偏差1）')]

# カルテ銘柄（ユーザー要望 2026-09-08）。キーは 'K:<code>'。月末終値を DailyPrice から組む。
# DailyPrice は登録銘柄の調整後終値で約3年ぶん（update_impulse_prices / update_daily_prices）
KARTE_PALETTE = ['#22d3ee', '#f472b6', '#a3e635', '#fb7185', '#34d399', '#fbbf24',
                 '#818cf8', '#f59e0b', '#2dd4bf', '#e879f9', '#84cc16', '#38bdf8']


def karte_catalog() -> list[tuple]:
    """(key, label, code, 'level', color) をカルテ登録順で。カルテ未導入でも落とさない"""
    try:
        from karte.models import StockKarte
        rows = list(StockKarte.objects.select_related('stock').order_by('id')
                    .values_list('stock__code', 'stock__name', 'stock__display_code'))
    except Exception:   # noqa: BLE001
        return []
    out = []
    for i, (code, name, disp) in enumerate(rows):
        label = f'{name}（{disp or code}）'
        out.append((f'K:{code}', label, code, 'level', KARTE_PALETTE[i % len(KARTE_PALETTE)]))
    return out


def _karte_monthly(code: str) -> list[tuple[date, float]]:
    """DailyPrice → 月末終値の月次系列 [(月初日, 終値)]"""
    from .models import DailyPrice
    last = {}
    for d, c in (DailyPrice.objects.filter(stock__code=code).order_by('date')
                 .values_list('date', 'close')):
        last[date(d.year, d.month, 1)] = float(c)     # 同じ月は後勝ち＝月末
    return sorted(last.items())


def _yoy(rows):
    by = {(d.year, d.month): v for d, v in rows}
    return [(d, (v / by[(d.year - 1, d.month)] - 1) * 100)
            for d, v in rows if by.get((d.year - 1, d.month))]


def _normalize(vals: list[float], how: str) -> list[float]:
    if not vals:
        return []
    if how == 'z':
        n = len(vals)
        mean = sum(vals) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0
        return [(v - mean) / sd if sd else 0.0 for v in vals]
    lo, hi = min(vals), max(vals)
    return [((v - lo) / (hi - lo)) * 2 - 1 if hi > lo else 0.0 for v in vals]


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _corr_word(r):
    a = abs(r)
    strength = '強い' if a >= 0.7 else 'やや' if a >= 0.4 else '弱い' if a >= 0.2 else 'ほぼ無'
    sign = '正' if r > 0 else '負'
    return f'{strength}{sign}の相関' if a >= 0.2 else '相関ほぼ無し'


HEAT_WINDOWS = [('sel', '選択期間'), ('5y', '直近5年'), ('3y', '直近3年'), ('1y', '直近1年')]
HEAT_MIN_N = 12          # これ未満の月数は計算しない（1年未満の相関は偶然と区別できない）


def _series_dict(key, lookup, raw):
    """key → {'YYYY-MM': 生の値}（変換込み）"""
    _, _, sid, tf, _ = lookup[key]
    rows = _karte_monthly(sid) if key.startswith('K:') else raw.get(sid, [])
    if tf == 'yoy':
        rows = _yoy(rows)
    return {d.strftime('%Y-%m'): v for d, v in rows}


def _heat_color(r):
    """相関係数 → 背景色。正=赤系・負=青系、|r| が大きいほど濃い（発散ランプ）。None は無色"""
    if r is None:
        return 'transparent'
    a = min(abs(r), 1.0)
    alpha = 0.08 + 0.72 * a
    return f'rgba(239,68,68,{alpha:.2f})' if r > 0 else f'rgba(59,130,246,{alpha:.2f})'


def heatmap(base_key: str, lookup: dict, raw: dict, year_from: int, today: date) -> dict:
    """基準系列 × 他の全系列 × 期間窓 の相関ヒートマップ（ユーザー要望 2026-09-08）。

    行=他の系列（選択期間の |r| 順）、列=期間窓。色は正=赤・負=青、濃さ=|r|。
    月数が HEAT_MIN_N 未満の窓は空欄、36 未満は薄字で「短い」と分かるようにする。
    ⚠️ 全系列を毎回読むので、系列が増えたら DailyPrice の読み込みが重くなる（現状20銘柄で数千行）
    """
    base = _series_dict(base_key, lookup, raw)
    if not base:
        return {'base': lookup[base_key][1], 'rows': [], 'windows': HEAT_WINDOWS}
    starts = {
        'sel': f'{year_from:04d}-01',
        '5y': f'{today.year - 5:04d}-{today.month:02d}',
        '3y': f'{today.year - 3:04d}-{today.month:02d}',
        '1y': f'{today.year - 1:04d}-{today.month:02d}',
    }
    rows = []
    for key, (_, label, _sid, _tf, color) in lookup.items():
        if key == base_key:
            continue
        other = _series_dict(key, lookup, raw)
        cells = []
        for wkey, wlabel in HEAT_WINDOWS:
            common = sorted(m for m in base if m in other and m >= starts[wkey])
            r = _pearson([base[m] for m in common], [other[m] for m in common]) if len(common) >= HEAT_MIN_N else None
            cells.append({'w': wkey, 'r': r, 'n': len(common), 'color': _heat_color(r),
                          'weak': len(common) < 36})
        rows.append({'key': key, 'label': label, 'color': color, 'cells': cells,
                     'is_karte': key.startswith('K:'),
                     'sort': abs(cells[0]['r']) if cells[0]['r'] is not None else -1})
    rows.sort(key=lambda x: -x['sort'])
    return {'base': lookup[base_key][1], 'base_key': base_key, 'rows': rows, 'windows': HEAT_WINDOWS}


def build(params) -> dict:
    """GET パラメータ → 画面データ。s=<key>（複数）, from=<年>, norm=minmax|z"""
    karte = karte_catalog()
    lookup = {**CATALOG_BY_KEY, **{c[0]: c for c in karte}}
    keys = [k for k in params.getlist('s') if k in lookup][:MAX_SERIES] or list(DEFAULT_KEYS)
    norm = params.get('norm') if params.get('norm') in dict(NORMS) else 'minmax'
    this_year = date.today().year
    try:
        year_from = int(params.get('from', this_year - 10))
    except ValueError:
        year_from = this_year - 10
    year_from = max(1950, min(year_from, this_year))

    # ヒートマップが全系列を使うので MacroIndicator は全部読む（1万行強・数十ms）
    raw = defaultdict(list)
    for sid, d, v in MacroIndicator.objects.order_by('date').values_list('series', 'date', 'value'):
        raw[sid].append((d, v))
    heat_key = params.get('h') if params.get('h') in lookup else keys[0]
    heat = heatmap(heat_key, lookup, raw, year_from, date.today())

    chart_series, items, by_key = [], [], {}
    for k in keys:
        _, label, sid, tf, color = lookup[k]
        rows = _karte_monthly(sid) if k.startswith('K:') else raw.get(sid, [])
        if tf == 'yoy':
            rows = _yoy(rows)
        rows = [(d, v) for d, v in rows if d.year >= year_from]
        vals = _normalize([v for _, v in rows], norm)
        by_key[k] = {d.strftime('%Y-%m'): v for d, v in rows}
        items.append({'key': k, 'label': label, 'color': color, 'n': len(rows),
                      'first': rows[0][0] if rows else None, 'last': rows[-1][0] if rows else None,
                      'last_value': rows[-1][1] if rows else None,
                      'missing': not rows})
        chart_series.append({'name': label, 'color': color,
                             'data': [[d.strftime('%Y-%m'), round(nv, 3)] for (d, _), nv in zip(rows, vals)]})

    # 相関（重なる月・生の値）
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = keys[i], keys[j]
            common = sorted(set(by_key[a]) & set(by_key[b]))
            r = _pearson([by_key[a][m] for m in common], [by_key[b][m] for m in common])
            pairs.append({'a': lookup[a][1], 'b': lookup[b][1],
                          'r': r, 'n': len(common),
                          'word': _corr_word(r) if r is not None else '計算不能',
                          'weak_n': len(common) < 36,
                          'state': ('alert' if r is not None and abs(r) >= 0.7 else
                                    'warn' if r is not None and abs(r) >= 0.4 else 'ok')})

    chart = {
        'el': 'chart-special', 'title': '標準化した時系列（重ね描き）',
        'unit': '', 'y_min': -1 if norm == 'minmax' else None, 'y_max': 1 if norm == 'minmax' else None,
        'zoom_all': True,
        'desc': (f'{year_from}年以降の各系列を' + ('最小→−1・最大→+1' if norm == 'minmax' else '平均0・標準偏差1')
                 + 'に揃えて重ねたもの。同じ向きに動けば正の相関、逆なら負の相関。'
                 '縦軸の値そのものに意味は無く、形と向きだけを見る。'),
        'series': chart_series,
    }
    return {
        'catalog': CATALOG, 'karte_catalog': karte, 'selected': keys, 'norm': norm, 'norms': NORMS,
        'heat': heat, 'heat_key': heat_key,
        'heat_options': [(c[0], c[1]) for c in CATALOG] + [(c[0], c[1]) for c in karte],
        'year_from': year_from, 'year_options': list(range(this_year - 1, 1949, -1)),
        'items': items, 'pairs': pairs, 'charts': [chart] if any(s['data'] for s in chart_series) else [],
        'max_series': MAX_SERIES,
    }
