import bisect
import math
import statistics
from collections import defaultdict
from datetime import timedelta

from django.shortcuts import render

from .models import DailyPrice, MacroIndicator, Stock

RANKING_LIMIT = 100  # 常に上位100件を表示（TOP選択UIは廃止）

# 国別タブ（時価総額・出来高ランキング共通）。主戦場が米国株なのでUSを先頭・既定にする
COUNTRIES = [('US', '米国株'), ('JP', '日本株')]


def _parse_country(request):
    c = request.GET.get('country', 'US')
    return c if c in dict(COUNTRIES) else 'US'

# 指標ごとの表示レンジ（棒グラフのx軸min/max）。日本株の一般的な水準を目安に設定


def _karte_codes():
    """カルテがある銘柄の code 集合（ランキングの銘柄リンク先の出し分け用・2026-09-16）"""
    from karte.models import StockKarte
    return set(StockKarte.objects.values_list('stock_id', flat=True))


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
        'karte_codes': _karte_codes(),
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
        'karte_codes': _karte_codes(),
        'total_count': qs.count(),
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/volume.html', context)




def _mad_sigma(values):
    """外れ値に強いσの推定（中央絶対偏差 × 1.4826）

    単純な標準偏差だと1日の異常値でσが跳ね上がり、その行が全部「中立」に潰れて
    無音になる（実測: IBMの7/14に-25.2%という日があり、σが2.16→4.50と倍増した。
    その1日を含むだけで判定幅が倍になり、通常の値動きが全て中立扱いになる）。
    """
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    # MADが0になるのは値が全く動かない場合。そのときだけ標準偏差で代替する
    return mad * 1.4826 if mad > 0 else statistics.pstdev(values)


def _impulse_band(values):
    """セクター自身のボラティリティから色の判定幅（±%）を決める"""
    from .impulse import MIN_BAND, MIN_HISTORY, NEUTRAL_BAND, SIGMA_BAND

    if len(values) < MIN_HISTORY:
        return NEUTRAL_BAND        # 履歴不足では推定できないので固定値に退避
    return max(_mad_sigma(values) * SIGMA_BAND, MIN_BAND)


def _impulse_state(z):
    """z（そのセクターのσの何倍動いたか）を7段階のセル状態に分類する

    返り値は 'flat' / 'up1'〜'up3' / 'down1'〜'down3'。数字が大きいほど強い。
    境界は impulse.py の SIGMA_STEPS（(0.5, 1.2, 2.0)）。3値では「継続中の弱い
    上昇」と「転換の号砲になる大陽線」が同色に潰れてモメンタムの強弱が読めない。
    """
    from .impulse import SIGMA_STEPS

    az = abs(z)
    if az <= SIGMA_STEPS[0]:
        return 'flat'
    level = 1
    for t in SIGMA_STEPS[1:]:
        if az > t:
            level += 1
    return ('up' if z > 0 else 'down') + str(level)


def _impulse_cells(full, dates):
    """{date: 値} の系列を7段階のセル列に変換する。戻り値は (判定幅, セル列)

    判定幅（σ）は**表示期間ではなく取得済み全履歴**で推定する。表示窓だけだと
    標本が少なく、窓がずれるたび判定幅が動いて色がちらつく。
    """
    from .impulse import SIGMA_BAND

    band = _impulse_band(list(full.values()))
    cells = []
    for d in dates:
        pct = full.get(d)
        if pct is None:
            cells.append({'state': 'na', 'chg': None, 'sigma': None})  # 休場・データ未取得
            continue
        z = pct / band * SIGMA_BAND                   # 何σ動いたか
        cells.append({'state': _impulse_state(z), 'chg': round(pct, 2), 'sigma': round(z, 1)})
    return band, cells


def _dispersion_cells(disp, dates):
    """分散度（セクター間ばらつき）を4段階の**片側**スケールに変換する

    ⚠️ 分散度は常に正なので、上下に発散する7段階ランプは使えない（無理に当てると
    「低い＝下落」と誤読される）。自分自身の**過去分布のパーセンタイル**で
    低/並/高/極 に分ける。σ基準にしない理由は impulse.py の DISPERSION_PCTS 参照。
    戻り値は (中央値, セル列)。各セルの sigma には「過去の何%点か」を入れる。
    """
    from .impulse import DISPERSION_PCTS

    vals = sorted(disp.values())
    if not vals:
        return 0.0, [{'state': 'na', 'chg': None, 'sigma': None} for _ in dates]

    def pct_of(v):
        """v が全履歴の何パーセンタイルに当たるか（0〜100）"""
        return bisect.bisect_left(vals, v) / len(vals) * 100

    med = statistics.median(vals)
    cells = []
    for d in dates:
        v = disp.get(d)
        if v is None:
            cells.append({'state': 'na', 'chg': None, 'sigma': None})
            continue
        p = pct_of(v)
        level = sum(1 for t in DISPERSION_PCTS if p > t)
        cells.append({'state': f'd{level}', 'chg': round(v, 2), 'sigma': round(p)})
    return round(med, 2), cells


def _impulse_series(country):
    """インパルス定義セクターの日次系列一式を作る（インパルス/ドローダウン共用）

    戻り値: (stocks, price_series, sector_series, all_sorted)
      stocks        {display_code: Stock}
      price_series  {display_code: [(date, close), ...]}  調整後終値の生系列
      sector_series [(sec定義, is_dummy, {date: 騰落率%}), ...]  構成銘柄の単純平均
      all_sorted    観測された全営業日の昇順リスト

    セクター系列は**全履歴**で作る。インパルスはσ推定に、ドローダウンは指数の
    複利積み上げに、どちらも表示期間より長い履歴を必要とするため。
    """
    from .impulse import IMPULSE_SECTORS, dummy_change, impulse_universe

    codes = impulse_universe(country)
    if country == 'JP':
        # ⚠️ display_code で引かないこと。優先株が同じ表示コードを持つ銘柄がある
        # （9434=ソフトバンクは普通株94340+優先株94345/94346の3行）ため、display_code
        # で辞書化するとどれを掴むかがDBの返却順次第になる。J-Quantsの5桁コードは
        # 末尾0が普通株なので code で確定させる
        stocks = {s.display_code: s for s in
                  Stock.objects.filter(country='JP', code__in=[f'{c}0' for c in codes])}
    else:
        stocks = {s.display_code: s for s in
                  Stock.objects.filter(country='US', code__in=[f'US-{c}' for c in codes])}

    # 銘柄ごとの騰落率系列 {display_code: {date: pct}}（その銘柄自身の直前営業日比）
    changes = {}
    all_dates = set()
    price_series = defaultdict(list)
    qs = DailyPrice.objects.filter(
        stock__in=stocks.values()).order_by('stock_id', 'date')
    for p in qs.values_list('stock__display_code', 'date', 'close'):
        price_series[p[0]].append((p[1], p[2]))
    for code, rows_ in price_series.items():
        chg = {}
        for (d0, c0), (d1, c1) in zip(rows_, rows_[1:]):
            if c0 > 0:
                chg[d1] = (c1 / c0 - 1) * 100
        changes[code] = chg
        all_dates.update(chg)

    all_sorted = sorted(all_dates)

    sector_series = []
    for sec in IMPULSE_SECTORS.get(country, []):
        sec_codes = sec.get('codes') or []
        is_dummy = not sec_codes      # 構成銘柄が未定義の行は擬似データで描く
        full = {}
        for d in all_sorted:
            if is_dummy:
                full[d] = dummy_change(sec['name'], d)
                continue
            vals = [changes[c][d] for c in sec_codes if c in changes and d in changes[c]]
            if vals:
                full[d] = sum(vals) / len(vals)
        sector_series.append((sec, is_dummy, full))
    return stocks, price_series, sector_series, all_sorted


def _parse_impulse_mode(request):
    """?mode=abs|rel。未知の値は既定（絶対）に落とす"""
    from .impulse import DEFAULT_MODE, IMPULSE_MODES

    m = request.GET.get('mode')
    return m if m in dict(IMPULSE_MODES) else DEFAULT_MODE


def impulse(request):
    """独自セクター別インパルス（時系列ヒートマップ）。国別タブで日本株/米国株を切替。

    横軸=直近30営業日、縦軸=独自セクター（impulse.py で定義した数銘柄のグループ）。
    各セルはその日のセクター騰落率（構成銘柄の単純平均）を**7段階**（中立＋上下各3段）
    に分類して色で示す。**判定はセクター自身のボラティリティ基準（|z|を SIGMA_STEPS で
    区切る）** で行う。理由は impulse.py のコメント参照（固定%だと行間比較が壊れる）。
    数日並べることでモメンタムの継続・転換を見る（1日分ならTradingViewで足りる）。

    グリッド上部に**マクロ2行**（市場＝全セクター等加重平均／分散度＝セクター間ばらつき）
    を置き、`?mode=rel` で**市場ぶんを差し引いた残差**に切り替えられる。狙いは
    impulse.py の IMPULSE_MODES のコメント参照（absだけだとベータ差を逆相関と誤読する）。

    データは update_impulse_prices が DailyPrice に蓄積した調整後終値。
    サーバー側で全計算し、テンプレートは色分けセルを並べるだけ（JS不要）。
    """
    from .impulse import (DAYS_SHOWN, DISPERSION_ROW_NAME, IMPULSE_MODES,
                          MARKET_ROW_NAME, SIGMA_STEPS)

    country = _parse_country(request)
    mode = _parse_impulse_mode(request)
    stocks, _, sector_series, all_sorted = _impulse_series(country)
    dates = all_sorted[-DAYS_SHOWN:]

    # --- マクロ2行。⚠️ ダミー行は市場・分散度に寄与させない（擬似乱数が地合いを汚す）
    market, dispersion = {}, {}
    for d in all_sorted:
        vals = [f[d] for _, dum, f in sector_series if not dum and d in f]
        if not vals:
            continue
        market[d] = sum(vals) / len(vals)
        if len(vals) >= 2:            # ばらつきは2行以上ないと定義できない
            dispersion[d] = statistics.pstdev(vals)

    # --- 対市場モード: 各セクターからその日の市場ぶんを引く。
    # 分散度は定数を引いても変わらないので手を加えない（両モードで同じ値）
    if mode == 'rel':
        for _, _, full in sector_series:
            for d in list(full):
                if d in market:
                    full[d] -= market[d]
                else:
                    del full[d]       # 市場が定義できない日は相対値も出せない

    rows = []
    for sec, is_dummy, full in sector_series:
        band, cells = _impulse_cells(full, dates)
        members = [stocks[c] for c in (sec.get('codes') or []) if c in stocks]
        rows.append({
            'name': sec['name'],
            'members': members,
            'is_dummy': is_dummy,
            'band': round(band, 2),
            # 7段階の各境界を「そのセクターでは何%か」に直したもの（行ツールチップ用）。
            # band は中立幅＝SIGMA_STEPS[0]σ ぶんなので、比で他の境界も%に戻せる
            'bands': [{'sigma': t, 'pct': round(band * t / SIGMA_STEPS[0], 2)}
                      for t in SIGMA_STEPS],
            'cells': cells,
        })

    # マクロ行。市場行は rel モードでも**絶対値のまま**出す（引き算の基準そのものなので、
    # ここまで相対にすると全部ゼロになって情報が消える）
    macro_rows = []
    if market:
        m_band, m_cells = _impulse_cells(market, dates)
        macro_rows.append({
            # rel でもこの行だけは絶対値（引き算の基準そのもの）。その但し書きは
            # ⚠️ sub ではなく **name 側**に出す。sub は .imp-members の max-width で
            # 省略記号に切られる（日本語は約8文字で切れる）が、name は nowrap で切れない
            'name': MARKET_ROW_NAME + ('（絶対）' if mode == 'rel' else ''),
            'sub': '全セクター平均',
            'band': round(m_band, 2), 'cells': m_cells, 'kind': 'market',
            'hint': 'その日の地合い。全行が同じ色ならこの行の動きで説明がつく',
        })
    if dispersion:
        d_med, d_cells = _dispersion_cells(dispersion, dates)
        macro_rows.append({
            'name': DISPERSION_ROW_NAME, 'sub': 'セクター間の差',
            'band': d_med, 'cells': d_cells, 'kind': 'dispersion',
            'hint': f'中央値 {d_med}%。明るい日=セクターの明暗が割れた（ローテーション）、'
                    f'暗い日=全セクターが同方向（マクロ要因が支配）',
        })

    context = {
        'rows': rows,
        'macro_rows': macro_rows,
        'dates': dates,
        'has_data': bool(dates),
        'sigma_steps': SIGMA_STEPS,
        'country': country,
        'countries': COUNTRIES,
        'is_us': country == 'US',
        'mode': mode,
        'modes': IMPULSE_MODES,
        'is_rel': mode == 'rel',
    }
    return render(request, 'japan_kabu/impulse.html', context)


DRAWDOWN_MIN_POINTS = 30   # これ未満の履歴しかないセクターはレンジを語れないので注記付きにする


def _dd_stats(idx):
    """指数系列 [(date, value), ...] からドローダウン統計を出す

    設計は japan_kabu/prices.py（銘柄版）と同じ思想:
    - **主役はドローダウン（高値からの下落率）**。レンジ内位置は上昇トレンドで鈍る
    - **1年と全期間を必ず併記**。乖離するのは「天井を打って未回復」だけで、その差が情報
    """
    vals = [v for _, v in idx]
    current = vals[-1]
    high, low = max(vals), min(vals)
    out = {
        'dd_all': (current / high - 1) * 100,
        'pos': (current - low) / (high - low) * 100 if high > low else 100.0,
        'since': idx[0][0],
        'days': len(idx),
    }
    cutoff = idx[-1][0] - timedelta(days=365)
    w = [v for d, v in idx if d >= cutoff]
    if len(w) >= 2:
        out['dd_1y'] = (current / max(w) - 1) * 100
        out['chg_1y'] = (current / w[0] - 1) * 100
    return out


def drawdown(request):
    """セクター別ドローダウン（高値からの下落率）。国別タブで日本株/米国株を切替。

    **「下がりきったセクター」を探すためのページ**（逆張り・長期反発狙いの入口）。
    セクター別インパルスと役割分担する2段構え:
      1. ここで「どのセクターが深く下がったか」を水準で絞り込む
      2. インパルスの対市場モードで「まだ落ちているか、下げ止まったか」を勢いで確認する
    インパルスは日次騰落をσ正規化するため**累積の下落が構造的に写らない**。
    その欠けを埋めるのがこのページ（インパルスに水準表示を足しても両立しない）。

    セクター指数は構成銘柄の日次騰落率の単純平均を複利で積んだもの
    （毎日等ウェートにリバランスする合成指数）。インパルスのセル値と同じ系列を
    積むだけなので、2ページの数字は必ず整合する。上場が新しい銘柄（SPCX等）は
    データが現れた日から自然に平均へ加わる。

    銘柄版ドローダウン（prices.py・カルテの押し目一覧）と同じ思想:
    主役はドローダウン、1年と全期間を併記、終値ベース・調整後終値。
    """
    country = _parse_country(request)
    stocks, price_series, sector_series, all_sorted = _impulse_series(country)

    rows = []
    for sec, is_dummy, full in sector_series:
        if is_dummy or not full:
            continue      # ダミー行（擬似乱数）に水準の意味は無いので出さない

        # 等加重指数（毎日リバランス・起点100）
        idx = []
        v = 100.0
        for d in sorted(full):
            v *= 1 + full[d] / 100
            idx.append((d, v))

        st = _dd_stats(idx)

        # 直近5営業日の累積騰落（下げ止まりの気配を見る早見。詳細はインパルスで）
        last5 = [full[d] for d in sorted(full)[-5:]]
        chg_5d = 1.0
        for x in last5:
            chg_5d *= 1 + x / 100
        chg_5d = (chg_5d - 1) * 100

        # 構成銘柄ごとの内訳（生の終値から算出。平均が1銘柄に引っ張られていないかの確認用）
        members = []
        for c in sec.get('codes') or []:
            pr = price_series.get(c)
            if not pr:
                continue
            closes = [float(x) for _, x in pr]
            members.append({
                'stock': stocks.get(c), 'code': c,
                'dd': (closes[-1] / max(closes) - 1) * 100,
                'since': pr[0][0],
            })
        members.sort(key=lambda m: m['dd'])

        rows.append({
            'name': sec['name'],
            'members': members,
            'chg_5d': round(chg_5d, 1),
            'short_history': st['days'] < DRAWDOWN_MIN_POINTS,
            **{k: (round(vv, 1) if isinstance(vv, float) else vv) for k, vv in st.items()},
        })

    # 深い順（＝最も下がりきったセクターが先頭）。このページの主目的なので固定
    rows.sort(key=lambda r: r['dd_all'])
    deepest = min((r['dd_all'] for r in rows), default=0)
    for r in rows:
        r['bar'] = round(r['dd_all'] / deepest * 100, 1) if deepest < 0 else 0

    context = {
        'rows': rows,
        'has_data': bool(rows),
        'country': country,
        'countries': COUNTRIES,
    }
    return render(request, 'japan_kabu/drawdown.html', context)


# ---- 米国マクロ指標（CPI・失業率） ------------------------------------------

def _macro_yoy(rows):
    """指数系列 [(date, value)] → 前年同月比%の系列。ニュースの「CPI +3.1%」はこれ"""
    by = {(d.year, d.month): v for d, v in rows}
    out = []
    for d, v in rows:
        prev = by.get((d.year - 1, d.month))
        if prev:
            out.append((d, (v / prev - 1) * 100))
    return out


def _macro_sahm(un_rows):
    """サム・ルール指標: 失業率3ヶ月移動平均 − 直近12ヶ月のその最小値

    +0.50pt以上で「景気後退がすでに始まっている」シグナル（過去の後退局面をほぼ
    全て捉えた経験則）。エコノミストのClaudia Sahmが提唱。
    """
    vals = [v for _, v in un_rows]
    ma3 = [sum(vals[i - 2:i + 1]) / 3 for i in range(2, len(vals))]  # ma3[i] ↔ un_rows[i+2]
    out = []
    for i in range(11, len(ma3)):
        out.append((un_rows[i + 2][0], ma3[i] - min(ma3[i - 11:i + 1])))
    return out


# マクロページ専用（株ではないので「〜株」表記にしない）。
# 'SP'（特殊）は 2026-09-08 追加。ユーザー独自の分析機能を置くタブで、右端に固定。
# 中身はまだ雛形（「特殊」と表示するだけ）。国別のデータ・チップ・チャートは使わない
MACRO_COUNTRIES = [('US', '米国'), ('JP', '日本'), ('SP', '特殊')]


def _macro_fx(series, pack):
    """ドル円（月中平均）のチップとチャート。日米どちらのタブにも同じものを出す（2026-09-08）。

    基準値は置かない（為替に「正常水準」は無い）。代わりに前年同月比で円安/円高の向きと
    速さを出す。±10%/年を超えると企業業績・物価への影響が大きい、を注意の目安にする
    """
    fx = list(series.get('USDJPY', []))
    # ⚠️ FRED の月中平均（EXJPUS）は翌月に出るので最大1か月強古い（「情報が古い」と指摘された）。
    # 直近は夜間バッチが毎日取っている portfolio.FxRate（yfinance USDJPY=X の終値）で補う:
    #   チップ = 最新の日次終値、チャート = 当月の途中平均を末尾に足す
    daily = []
    try:
        from portfolio.models import FxRate
        daily = list(FxRate.objects.filter(pair='USDJPY').order_by('date').values_list('date', 'rate'))
    except Exception:   # noqa: BLE001  portfolio 未導入でもマクロページは落とさない
        daily = []
    last_month = fx[-1][0] if fx else None
    if daily:
        cur = [(d, r) for d, r in daily if last_month is None or (d.year, d.month) > (last_month.year, last_month.month)]
        if cur:
            d0 = cur[-1][0].replace(day=1)
            fx.append((d0, sum(r for _, r in cur) / len(cur)))   # 当月（途中）の平均
    chip = None
    if fx:
        d, v = fx[-1]
        prev = [x for x in fx if x[0].year == d.year - 1 and x[0].month == d.month]
        yoy = f'前年同月比 {(v / prev[0][1] - 1) * 100:+.1f}%' if prev else ''
        if daily:
            dd, dv = daily[-1]
            m_prev = [x for x in fx if x[0] < dd.replace(day=1)]
            base = m_prev[-1] if m_prev else None
            chg = (dv / base[1] - 1) * 100 if base else 0.0
            direction = '円安' if chg > 0 else '円高'
            state = 'warn' if abs(chg) >= 5 else 'ok'
            note = ((f'{base[0].month}月平均 ¥{base[1]:.2f} から {chg:+.1f}%（{direction}方向）' if base else '日次終値')
                    + ('・月内5%超の急変' if state == 'warn' else '') + (f'／{yoy}' if yoy else ''))
            chip = {'label': 'ドル円（終値）', 'value': f'¥{dv:.2f}', 'date': dd, 'state': state,
                    'note': note, 'date_fmt': 'day'}
        else:
            chip = {'label': 'ドル円', 'value': f'¥{v:.2f}', 'date': d, 'state': 'ok', 'note': yoy or '月中平均'}
    chart = {
        'el': 'chart-fx', 'title': 'ドル円（月中平均・当月は途中平均）', 'unit': '円',
        'desc': '1ドル＝何円か（上に行くほど円安）。基本は「日米の金利差」で動く: 米金利高・日本金利低なら円安、'
                '日本の利上げ・米の利下げなら円高。円安は輸出株・海外資産の円換算に追い風、輸入物価（食品・エネルギー）には逆風。'
                '末尾の当月分は日次終値の途中平均（毎晩更新）。1985年プラザ合意の240円→120円、2011年の75円、'
                '2022年以降の150円台まで遡れる。',
        'series': [{'name': 'ドル円', 'color': '#facc15', 'data': pack(fx)}],
    }
    return chip, chart


def macro(request):
    """マクロ指標（日米のCPI・失業率）の時系列と「基準値」の解説ページ

    学習用途が主目的（ユーザー要望）。ニュースの「CPIが市場予想を上回り株安」を
    自分で解釈できるよう、(1)長期の時系列チャート (2)基準値との照合チップ
    (3)読み方の解説 をワンページにまとめ、?country=US|JP で日米を切り替える。

    データは update_macro が FRED/DBnomics から取得して MacroIndicator に蓄積した
    月次の原数値。前年比・サム・ルールなどの加工はすべてここで計算する。

    ⚠️ 日米で「コア」の定義が違う（米=食品・エネルギー除く / 日=生鮮食品のみ除く）。
    チップやチャートの系列名を安易に共通化しないこと。学習ページとして
    この違い自体が見どころなので、テンプレート側の解説にも明記してある。
    """
    country = request.GET.get('country')
    country = country if country in dict(MACRO_COUNTRIES) else 'US'
    if country == 'SP':
        # 特殊タブ: 複数系列を標準化して重ね、相関を見る（japan_kabu/macro_special.py）
        from . import macro_special
        sp = macro_special.build(request.GET)
        return render(request, 'japan_kabu/macro.html', {
            **sp, 'chips': [], 'has_data': True, 'country': country,
            'countries': MACRO_COUNTRIES, 'is_us': False, 'is_special': True,
        })

    series = defaultdict(list)
    for sid, d, v in MacroIndicator.objects.order_by('date').values_list('series', 'date', 'value'):
        series[sid].append((d, v))

    def pack(rows):
        return [[d.strftime('%Y-%m'), round(v, 2)] for d, v in rows]

    def cpi_state(v):
        return 'ok' if v <= 2.5 else 'warn' if v <= 3.5 else 'alert'

    chips, charts = [], []

    if country == 'US':
        cpi = _macro_yoy(series.get('CPIAUCSL', []))
        core = _macro_yoy(series.get('CPILFESL', []))
        un = series.get('UNRATE', [])
        sahm = _macro_sahm(un) if len(un) > 20 else []

        if cpi:
            d, v = cpi[-1]
            state = cpi_state(v)
            note = ('目標圏（FRBの2%目標と整合）' if state == 'ok' else
                    'やや高い（利下げの逆風）' if state == 'warn' else '高い（インフレ警戒）')
            if v < 1.0:
                state, note = 'warn', '低すぎ（デフレ警戒側）'
            chips.append({'label': 'CPI 前年比（総合）', 'value': f'+{v:.1f}%', 'date': d,
                          'state': state, 'note': note})
        if core:
            d, v = core[-1]
            chips.append({'label': 'コアCPI 前年比', 'value': f'+{v:.1f}%', 'date': d,
                          'state': cpi_state(v),
                          'note': 'FRBが重視する基調（食品・エネルギー除く）'})
        if un:
            d, v = un[-1]
            state = 'ok' if 3.5 <= v <= 4.5 else 'warn' if v <= 5.5 else 'alert'
            note = ('完全雇用圏（自然失業率4%前後と整合）' if state == 'ok' else
                    '過熱気味（賃金インフレ圧力）' if v < 3.5 else
                    'やや高い（景気減速側）' if state == 'warn' else '高い（景気後退圏）')
            chips.append({'label': '失業率（U-3）', 'value': f'{v:.1f}%', 'date': d,
                          'state': state, 'note': note})
        if sahm:
            d, v = sahm[-1]
            state = 'ok' if v < 0.3 else 'warn' if v < 0.5 else 'alert'
            chips.append({'label': 'サム・ルール指標', 'value': f'+{v:.2f}pt', 'date': d,
                          'state': state,
                          'note': '0.50pt以上で景気後退シグナル' + ('（点灯中）' if state == 'alert' else '（未点灯）')})

        gs10 = series.get('GS10', [])
        gs2 = series.get('GS2', [])
        if gs10:
            d, v = gs10[-1]
            state = 'ok' if v < 4.0 else 'warn' if v <= 5.0 else 'alert'
            note = ('株のバリュエーションに中立圏' if state == 'ok' else
                    '高め（PERを圧迫する逆風）' if state == 'warn' else '高い（株から債券へ資金が逃げる水準）')
            chips.append({'label': '10年国債利回り', 'value': f'{v:.2f}%', 'date': d,
                          'state': state, 'note': note})
        ff = series.get('FEDFUNDS', [])
        if ff:
            d, v = ff[-1]
            state = 'ok' if v < 3.5 else 'warn' if v <= 5.0 else 'alert'
            note = ('中立水準（3%前後）の近く' if state == 'ok' else
                    '引き締め気味（利下げ余地あり）' if state == 'warn' else '強い引き締め（インフレ抑制局面）')
            chips.append({'label': '政策金利（FF金利）', 'value': f'{v:.2f}%', 'date': d,
                          'state': state, 'note': note})
        if gs10 and gs2 and gs10[-1][0] == gs2[-1][0]:
            d = gs10[-1][0]
            sp = gs10[-1][1] - gs2[-1][1]
            state = 'alert' if sp <= 0 else 'warn' if sp <= 0.2 else 'ok'
            note = ('逆イールド（景気後退の古典的前兆）' if state == 'alert' else
                    'フラット化（後退警戒の入口）' if state == 'warn' else '順イールド（平常）')
            chips.append({'label': '長短金利差（10年−2年）', 'value': f'{sp:+.2f}pt', 'date': d,
                          'state': state, 'note': note})

        charts = [
            {'el': 'chart-cpi', 'title': 'CPI 前年比（インフレ率）',
             'desc': 'ニュースの「CPI +3.1%」はこの前年同月比のこと。点線がFRBの物価目標のめやす（2%）。'
                     '初期表示は直近10年。下のスライダーで1948年まで遡れる（1970年代の大インフレ、2021-22年の急騰が見どころ）。',
             'series': [{'name': '総合CPI', 'color': '#1e90ff', 'data': pack(cpi)},
                        {'name': 'コアCPI', 'color': '#f97316', 'data': pack(core)}],
             'mark': {'v': 2, 'label': 'FRB目標のめやす 2%'}},
            {'el': 'chart-unrate', 'title': '失業率（U-3）',
             'desc': '点線が自然失業率のめやす（4%前後）。景気後退のたびに急上昇し、回復に数年かかる'
                     '「ゆっくり下がって一気に上がる」形が特徴。',
             'series': [{'name': '失業率', 'color': '#4ade80', 'data': pack(un), 'area': True}],
             'mark': {'v': 4, 'label': '自然失業率のめやす 4%前後'}},
            {'el': 'chart-rates', 'title': '金利（国債利回り・政策金利）',
             'desc': '10年金利=市場が決める長期金利（株のバリュエーションの分母）。2年金利=政策金利の先行き予想。'
                     'FF金利=FRBが決める政策金利。2年が10年を上回る「逆イールド」（線の上下逆転）は景気後退の古典的な前兆。'
                     '1980年前後の20%近い金利や、2009-21年のゼロ金利も遡って見られる。',
             'series': [{'name': '10年国債', 'color': '#1e90ff', 'data': pack(gs10)},
                        {'name': '2年国債', 'color': '#f97316', 'data': pack(gs2)},
                        {'name': 'FF金利（政策金利）', 'color': '#9ca3af', 'data': pack(series.get('FEDFUNDS', []))}]},
        ]
        fx_chip, fx_chart = _macro_fx(series, pack)
        if fx_chip:
            chips.append(fx_chip)
        charts.append(fx_chart)
    else:
        cpi = _macro_yoy(series.get('JPCPI_ALL', []))
        core = _macro_yoy(series.get('JPCPI_CORE', []))
        corecore = _macro_yoy(series.get('JPCPI_CORECORE', []))
        un = series.get('JPUNRATE', [])

        if core:
            d, v = core[-1]
            state = cpi_state(v)
            note = ('日銀の2%目標と整合' if state == 'ok' else
                    'やや高い（利上げ観測の材料）' if state == 'warn' else '高い（利上げ圧力）')
            if v < 0.5:
                state, note = 'warn', '低すぎ（デフレ再燃警戒）'
            chips.append({'label': 'コアCPI（生鮮除く）前年比', 'value': f'+{v:.1f}%', 'date': d,
                          'state': state, 'note': note})
        if cpi:
            d, v = cpi[-1]
            chips.append({'label': 'CPI 前年比（総合）', 'value': f'+{v:.1f}%', 'date': d,
                          'state': cpi_state(v), 'note': '生鮮食品込み。天候で振れやすい'})
        if corecore:
            d, v = corecore[-1]
            chips.append({'label': 'コアコアCPI 前年比', 'value': f'+{v:.1f}%', 'date': d,
                          'state': cpi_state(v),
                          'note': '生鮮・エネルギー除く基調（米国のコアに近い概念）'})
        if un:
            d, v = un[-1]
            state = 'ok' if v <= 3.0 else 'warn' if v <= 4.0 else 'alert'
            note = ('完全雇用圏（2%台は人手不足側）' if state == 'ok' else
                    'やや高い（景気減速側）' if state == 'warn' else '高い（雇用悪化）')
            chips.append({'label': '完全失業率', 'value': f'{v:.1f}%', 'date': d,
                          'state': state, 'note': note})
        # ⚠️ サム・ルールは米国の経験則なので日本には出さない（テンプレの解説参照）

        jp10 = series.get('JP10Y', [])
        if jp10:
            d, v = jp10[-1]
            state = 'ok' if v < 1.0 else 'warn'
            note = ('低金利圏' if state == 'ok' else
                    '「金利のある世界」へ正常化中（銀行株に追い風・不動産/グロースに逆風）')
            chips.append({'label': '10年国債利回り', 'value': f'{v:.2f}%', 'date': d,
                          'state': state, 'note': note})
        # 政策金利は無担保コール翌日物の月平均で代用（日銀の誘導目標にほぼ一致。
        # 中銀金利そのものの系列は 2023-12 で配信停止のため）。約2か月遅れ
        jpcall = series.get('JPCALL', [])
        if jpcall:
            d, v = jpcall[-1]
            state = 'ok' if v < 0.5 else 'warn'
            note = ('ゼロ金利圏' if v < 0.1 else
                    '利上げ局面の入口' if state == 'ok' else '利上げ進行中（円高・銀行株高の要因）')
            chips.append({'label': '政策金利（コールレート）', 'value': f'{v:.2f}%', 'date': d,
                          'state': state, 'note': note})

        charts = [
            {'el': 'chart-cpi', 'title': 'CPI 前年比（インフレ率）',
             'desc': '日本のニュース・日銀が見る「コアCPI」は生鮮食品を除く系列（オレンジ）。点線が日銀の物価安定目標（2%）。'
                     'スライダーで1971年まで遡れる（第一次オイルショックの+20%超、90年代以降のデフレ期が見どころ）。',
             'series': [{'name': '総合', 'color': '#1e90ff', 'data': pack(cpi)},
                        {'name': 'コア（生鮮除く）', 'color': '#f97316', 'data': pack(core)},
                        {'name': 'コアコア（生鮮・エネルギー除く）', 'color': '#a78bfa', 'data': pack(corecore)}],
             'mark': {'v': 2, 'label': '日銀目標 2%'}},
            {'el': 'chart-unrate', 'title': '完全失業率',
             'desc': '日本の失業率は雇用慣行（解雇ではなく残業・賞与で調整）のため米国より水準が低く変動も小さい。'
                     '2%台なら完全雇用圏。点線は構造失業率のめやす。',
             'series': [{'name': '完全失業率', 'color': '#4ade80', 'data': pack(un), 'area': True}],
             'mark': {'v': 2.5, 'label': '構造失業率のめやす 2%台半ば'}},
            {'el': 'chart-rates', 'title': '10年国債利回り（長期金利）',
             'desc': '1990年の8%から30年かけてゼロへ沈み、2016年からはYCC（イールドカーブ・コントロール）で'
                     '0%近辺に固定されていた。2024年のYCC撤廃・マイナス金利解除で「金利のある世界」へ正常化中。'
                     'この上昇が銀行株高・不動産株安・円高圧力の源泉。',
             'series': [{'name': '10年国債', 'color': '#1e90ff', 'data': pack(jp10)},
                        {'name': '政策金利（無担保コール翌日物）', 'color': '#9ca3af', 'data': pack(jpcall)}],
             'mark': {'v': 1, 'label': 'YCC時代の上限のめやす 1%'}},
        ]
        charts[-1]['title'] = '金利（10年国債利回り・政策金利）'
        charts[-1]['desc'] += 'グレーが政策金利（無担保コール翌日物の月平均。日銀の誘導目標にほぼ一致）。'
        fx_chip, fx_chart = _macro_fx(series, pack)
        if fx_chip:
            chips.append(fx_chip)
        charts.append(fx_chart)

    context = {
        'charts': charts,
        'chips': chips,
        'has_data': any(c['series'] and c['series'][0]['data'] for c in charts),
        'country': country,
        'countries': MACRO_COUNTRIES,
        'is_us': country == 'US',
    }
    return render(request, 'japan_kabu/macro.html', context)


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


def stock_detail(request, code=None):
    """旧「銘柄別指標」ページの URL（2026-09-16 廃止）→ その銘柄のカルテへ転送する

    指標はカルテに吸収した（登録した銘柄だけ見ればよい、というユーザー方針）。
    カルテが無い銘柄は、一覧の検索窓にコードを入れた状態で開く（登録画面を経由する方針）。
    ランキング・日記・ポートフォリオからの銘柄リンクはこの URL のままでよい。
    """
    from django.shortcuts import redirect
    from django.urls import reverse

    from karte.models import StockKarte

    stock = Stock.objects.filter(display_code=code).first() if code else None
    if stock and StockKarte.objects.filter(stock=stock).exists():
        return redirect('karte:detail', code=stock.display_code)
    url = reverse('karte:index')
    return redirect(f'{url}?q={stock.display_code}' if stock else url)
