from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import (
    CashBaselineForm, CashFlowForm, FundHoldingForm, MetalHoldingForm,
    ProductEditForm, StockHoldingForm,
)
from .models import (
    CashFlow, DrillNote, Holding, PortfolioSetting, Product, TargetAllocation,
)
from .services import build_portfolio

# ドーナツ・棒グラフの大分類カラー（モックと同じ配色）
CLASS_COLORS = {
    'stock_jp': '#1e90ff',
    'stock_us': '#63b3ff',
    'fund': '#8b5cf6',
    'metal': '#fbbf24',
    'cash': '#94a3b8',
}

# クラス内訳ドーナツ用のパレット（銘柄数ぶん循環して使う）
SUB_PALETTE = ['#1e90ff', '#8b5cf6', '#34d399', '#fbbf24', '#f87171',
               '#63b3ff', '#c4b5fd', '#f9a8d4', '#5eead4', '#fdba74']


def _class_breakdown(items, kinds):
    """指定した大分類（複数指定で合算）内の銘柄別内訳を組む。

    kinds はタプル。('stock_jp', 'stock_us') のように渡すと日米合算になる。
    該当資産が無ければ None（テンプレート側でカードごと非表示にする）。
    """
    subset = sorted((i for i in items if i['kind'] in kinds),
                    key=lambda x: -x['value'])
    total = sum(i['value'] for i in subset)
    if not subset or total <= 0:
        return None
    rows = []
    for idx, i in enumerate(subset):
        color = SUB_PALETTE[idx % len(SUB_PALETTE)]
        rows.append({'name': i['name'], 'value': i['value'],
                     'pct': i['value'] / total * 100, 'color': color})
    return {'rows': rows, 'total': total}


def index(request):
    """資産ダッシュボード

    POSTは「日記の株数・約定価格の補記」のみ受け付ける（警告からその場で直すため）。
    日記の設計思想（判断=理由・タグ・心理は編集不可）は守り、事実データである
    株数・価格だけを update_fields 限定で保存する。
    """
    if request.method == 'POST' and request.POST.get('form_id') == 'diary_fix':
        from diary.models import DiaryEntry
        entry = DiaryEntry.objects.filter(pk=request.POST.get('entry_id')).first()
        if entry:
            try:
                shares = int(request.POST.get('shares', ''))
            except ValueError:
                shares = None
            try:
                price = float(request.POST.get('price', ''))
            except ValueError:
                price = None
            fields = []
            if shares is not None:
                entry.shares = shares
                fields.append('shares')
            if price is not None:
                entry.price = price
                fields.append('price')
            if fields:
                entry.save(update_fields=fields)
                messages.success(
                    request, f'{entry.stock_name} の{"・".join("株数" if f == "shares" else "約定価格" for f in fields)}を補記しました。')
            else:
                messages.error(request, '株数または約定価格を入力してください。')
        return redirect('portfolio:index')

    data = build_portfolio()
    items = data['items']

    # 大分類の凡例（値が0の分類は出さない）
    class_rows = [
        {**data['by_class'][key], 'color': CLASS_COLORS[key]}
        for key in CLASS_COLORS if data['by_class'][key]['value'] > 0
    ]

    # 保有額ランキング横棒: 最大値を100%とした相対幅
    # （現金がマイナスの場合など、負の幅はCSSとして無効なので0に丸める）
    max_value = max((i['value'] for i in items), default=0)
    bars = [
        {**i, 'width': max(0.0, i['value'] / max_value * 100) if max_value > 0 else 0,
         'color': CLASS_COLORS[i['kind']]}
        for i in items
    ]

    # 目標比較（TargetAllocation が設定されているときだけ）
    # 目標の大分類 'stock' は現状の stock_jp + stock_us を合算して比べる
    targets = list(TargetAllocation.objects.all())
    target_rows = []
    if targets:
        current_by_target = {
            'stock': data['by_class']['stock_jp']['pct'] + data['by_class']['stock_us']['pct'],
            'fund': data['by_class']['fund']['pct'],
            'metal': data['by_class']['metal']['pct'],
            'cash': data['by_class']['cash']['pct'],
        }
        for t in targets:
            cur = current_by_target.get(t.asset_class, 0)
            diff = t.ratio - cur
            target_rows.append({
                'label': t.get_asset_class_display(),
                'current': cur,
                'target': t.ratio,
                'diff': diff,
                'diff_yen': data['total'] * diff / 100 if data['total'] else 0,
            })

    # 大分類の中の銘柄別内訳（該当資産が無いものは出さない）。
    # 個別株系のドーナツはクリック/タイトルから対応する分析ページへ遷移できる
    from django.urls import reverse
    breakdowns = [
        {'title': title, 'link': link, 'data': _class_breakdown(items, kinds)}
        for kinds, title, link in [
            (('fund',), '投資信託の内訳', None),
            (('stock_jp', 'stock_us'), '個別株の内訳（日米合算）',
             reverse('portfolio:stock_focus', args=['all'])),
            (('stock_jp',), '日本株の内訳', reverse('portfolio:stock_focus', args=['jp'])),
            (('stock_us',), '米国株の内訳', reverse('portfolio:stock_focus', args=['us'])),
        ]
    ]
    breakdowns = [b for b in breakdowns if b['data']]

    # ECharts用のドーナツ仕様（描画・ツールチップはdashboard.jsがスペック駆動で行う。
    # macroページと同じ「ビューが仕様を組み、JSは触らない」方針）
    donut_spec = {
        'main': {
            'rows': [{'name': r['label'], 'value': round(r['value']), 'color': r['color']}
                     for r in class_rows],
            'center_label': '現金比率',
            'center_value': f"{data['cash_ratio']:.0f}%",
        },
        'subs': [
            {'rows': [{'name': r['name'], 'value': round(r['value']), 'color': r['color']}
                      for r in b['data']['rows']],
             'link': b['link']}
            for b in breakdowns
        ],
    }

    context = {
        'data': data,
        'items': items,
        'bars': bars,
        'class_rows': class_rows,
        'target_rows': target_rows,
        'breakdowns': breakdowns,
        'donut_spec': donut_spec,
        'has_assets': bool(items),
    }
    return render(request, 'portfolio/dashboard.html', context)


# 投資スタイルの表示色（積み上げバー・凡例で共通に使う）
STYLE_COLORS = {
    'グロース': '#1e90ff',
    '大型': '#63b3ff',
    '配当狙い': '#fbbf24',
    'バリュー': '#34d399',
    '（未分類）': '#6b7280',
}

# 個別株分析ページの3軸（総合 / 日本株 / 米国株）
STOCK_SCOPES = {
    'all': {'label': '総合（日本＋米国）', 'kinds': ('stock_jp', 'stock_us')},
    'jp': {'label': '日本株', 'kinds': ('stock_jp',)},
    'us': {'label': '米国株', 'kinds': ('stock_us',)},
}


def stocks(request):
    """個別株の分析ページ（ポートフォリオの「攻め」部分の専用分析）

    ?scope=all|jp|us で 総合/日本株/米国株 の3軸を切り替える（既存の国別タブと同じ流儀）。
    入力データはダッシュボードと共通（build_portfolio）。構成比・集計はスコープ内で再計算する。
    """
    from japan_kabu.models import Stock
    from japan_kabu.prices import bulk_price_stats
    from karte.models import StockKarte

    scope = request.GET.get('scope', 'all')
    if scope not in STOCK_SCOPES:
        scope = 'all'
    kinds = STOCK_SCOPES[scope]['kinds']

    data = build_portfolio()
    stock_items = [i for i in data['items'] if i['kind'] in kinds]

    total_value = sum(i['value'] for i in stock_items)
    total_pnl = sum(i['pnl'] for i in stock_items if i['pnl'] is not None)
    total_cost = total_value - total_pnl

    # ドローダウン統計（DailyPrice履歴のある銘柄のみ。履歴は夜間バッチが自動蓄積）
    codes = [i['master_code'] for i in stock_items]
    stats = bulk_price_stats(list(Stock.objects.filter(code__in=codes)))

    # カルテ作成済み銘柄（明細からカルテへ深掘りリンクを出すため）
    karte_codes = set(StockKarte.objects.values_list('stock_id', flat=True))

    rows = []
    for i in sorted(stock_items, key=lambda x: -x['value']):
        st = stats.get(i['master_code'])
        rows.append({
            **i,
            'weight': i['value'] / total_value * 100 if total_value else 0,
            'dd1y': st['1y']['drawdown'] if st and st.get('1y') else None,
            'dd3y': st['3y']['drawdown'] if st and st.get('3y') else None,
            'has_karte': i['master_code'] in karte_codes,
        })

    def _aggregate(field, empty_label):
        """rows を指定フィールドで集計し、評価額・構成比・損益率の横棒データを返す"""
        agg_map = {}
        for r in rows:
            key = r[field] or empty_label
            agg = agg_map.setdefault(key, {'name': key, 'value': 0.0, 'pnl': 0.0, 'has_pnl': False})
            agg['value'] += r['value']
            if r['pnl'] is not None:
                agg['pnl'] += r['pnl']
                agg['has_pnl'] = True
        groups = sorted(agg_map.values(), key=lambda x: -x['value'])
        max_value = groups[0]['value'] if groups else 0
        for g in groups:
            g['weight'] = g['value'] / total_value * 100 if total_value else 0
            g['width'] = g['value'] / max_value * 100 if max_value else 0
            cost = g['value'] - g['pnl']
            g['pnl_pct'] = g['pnl'] / cost * 100 if (g['has_pnl'] and cost) else None
        return groups

    # セクター別（市場の軸: インパルス逆引き→公式業種）と
    # 投資スタイル別（自分の戦略の軸: グロース/大型/配当狙い…・手動分類）
    sectors = _aggregate('sector', '（未分類）')
    styles = _aggregate('style', '（未分類）')

    # 散布図スペック（横軸=スコープ内の構成比% / 縦軸=損益率% / 点の大きさ=評価額）。
    # 点をクリックすると銘柄別指標ページへ遷移する（url）
    from django.urls import reverse
    scatter_spec = {
        'points': [
            {'name': r['name'], 'weight': round(r['weight'], 2),
             'pnl_pct': round(r['pnl_pct'], 2), 'value': round(r['value']),
             'url': reverse('japan_kabu:stock_detail', args=[r['code']])}
            for r in rows if r['pnl_pct'] is not None
        ],
    }

    # 押し目度ランキング（深い順。履歴なしは末尾。Noneを含むdictsortは使えない）
    dd_rows = sorted(rows, key=lambda r: r['dd1y'] if r['dd1y'] is not None else 999)

    context = {
        'data': data,
        'rows': rows,
        'dd_rows': dd_rows,
        'sectors': sectors,
        'styles': styles,
        'total_value': total_value,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl / total_cost * 100 if total_cost else None,
        'offense_ratio': total_value / data['total'] * 100 if data['total'] else 0,
        'scatter_spec': scatter_spec,
        'has_stocks': bool(rows),
        'missing_dd': sum(1 for r in rows if r['dd1y'] is None and r['dd3y'] is None),
        'scope': scope,
        'scope_label': STOCK_SCOPES[scope]['label'],
        'scope_tabs': [(key, cfg['label']) for key, cfg in STOCK_SCOPES.items()],
    }
    return render(request, 'portfolio/stocks.html', context)


def stock_focus(request, scope):
    """3軸（総合/日本株/米国株）の個別分析ページ（拡充中）

    ダッシュボードの「クラス内の保有割合」ドーナツのクリック、および
    タイトルリンクからここへ遷移する。分析ブロックはユーザーと定義しながら
    順次追加していく（現在: 投資スタイル構成の積み上げバー）。
    """
    if scope not in STOCK_SCOPES:
        from django.http import Http404
        raise Http404

    data = build_portfolio()
    kinds = STOCK_SCOPES[scope]['kinds']
    stock_items = [i for i in data['items'] if i['kind'] in kinds]
    total_value = sum(i['value'] for i in stock_items)

    # 投資スタイル構成（積み上げ100%横棒。円グラフより省スペースというユーザー要望）
    style_map = {}
    for i in stock_items:
        key = i['style'] or '（未分類）'
        agg = style_map.setdefault(key, {'name': key, 'value': 0.0, 'pnl': 0.0, 'has_pnl': False})
        agg['value'] += i['value']
        if i['pnl'] is not None:
            agg['pnl'] += i['pnl']
            agg['has_pnl'] = True
    style_bar = sorted(style_map.values(), key=lambda x: -x['value'])
    for s in style_bar:
        s['pct'] = s['value'] / total_value * 100 if total_value else 0
        s['color'] = STYLE_COLORS.get(s['name'], '#8b5cf6')
        cost = s['value'] - s['pnl']
        s['pnl_pct'] = s['pnl'] / cost * 100 if (s['has_pnl'] and cost) else None

    # 個別株の保有額ランキング（横棒・円換算・評価額順。色は日本株/米国株で塗り分け）
    ranked = sorted(stock_items, key=lambda x: -x['value'])
    max_value = ranked[0]['value'] if ranked else 0
    stock_bars = [
        {**i,
         'width': i['value'] / max_value * 100 if max_value else 0,
         'pct': i['value'] / total_value * 100 if total_value else 0,
         'color': CLASS_COLORS[i['kind']]}
        for i in ranked
    ]

    context = {
        'scope': scope,
        'scope_label': STOCK_SCOPES[scope]['label'],
        'scope_tabs': [(key, cfg['label']) for key, cfg in STOCK_SCOPES.items()],
        'total_value': total_value,
        'style_bar': style_bar,
        'stock_bars': stock_bars,
        'fx_rate': data['fx_rate'],
        'has_stocks': bool(stock_items),
    }
    return render(request, 'portfolio/stock_focus.html', context)


# ══════════════════════════════════════════════════════════════════
# 避難訓練（爆下げプログラム）
# ══════════════════════════════════════════════════════════════════

# 下落の段階定義（52週高値からの下落率）。頻度は実測値:
# S&P500 1950〜(77年) / 日経平均 1965〜(62年) の日次終値から、
# 「一度-5%圏内へ回復してから再度割り込んだら別イベント」として集計した。
# ⚠️ しきい値を変えたら頻度・過去例も再集計すること（数字だけの独り歩きを防ぐ）
DRILL_LEVELS = [
    {'th': 10, 'name': '調整', 'tone': 'watch',
     'freq': '1〜2年に1回', 'note': '日常の範囲。ここで大きく投じない（弾切れになる）'},
    {'th': 20, 'name': '弱気相場', 'tone': 'fire',
     'freq': '3〜7年に1回', 'note': '発動ライン。予定された行動に入る'},
    {'th': 30, 'name': '暴落', 'tone': 'crash',
     'freq': '12年に1回', 'note': 'コロナ・ブラックマンデー級'},
    {'th': 40, 'name': '歴史的暴落', 'tone': 'epic',
     'freq': '20〜40年に1回', 'note': 'リーマン・ITバブル・オイルショック級'},
]
DRILL_METER_MAX = 50   # メーターの右端（-50%）

# 発動ライン(-20%)の歴史的実測（このページの合言葉に説得力を持たせる根拠）。
# 出典: 上記と同じ集計。発動日に買った場合の「その後の追加下落」と「1年後リターン」
DRILL_EVIDENCE = {
    'JP': {'label': '日経平均（1965〜の62年間）', 'fires': 20, 'freq': '3.1年に1回',
           'add_fall': '-4.3%', 'worst': '-53.6%', 'ret1y': '+17.8%', 'win': '65%'},
    'US': {'label': 'S&P500（1950〜の77年間）', 'fires': 11, 'freq': '7.0年に1回',
           'add_fall': '-6.3%', 'worst': '-45.6%', 'ret1y': '+23.0%', 'win': '73%'},
}

# 過去の主要エピソード（学習用の静的データ。深さは52週高値でなく高値→底の実測）
DRILL_EPISODES = [
    ('リーマン・ショック', 'S&P500', '2007-2009', '-57%', '下落17ヶ月・回復49ヶ月'),
    ('ITバブル崩壊', 'S&P500', '2000-2002', '-49%', '下落31ヶ月・回復56ヶ月'),
    ('コロナ・ショック', 'S&P500', '2020', '-34%', '下落1ヶ月・回復5ヶ月（例外的な速さ）'),
    ('利上げ相場', 'S&P500', '2022', '-25%', '下落9ヶ月・回復15ヶ月'),
    ('植田ショック→関税ショック', '日経平均', '2024-2025', '-26%', '下落9ヶ月・回復4ヶ月'),
    ('関税ショック', 'S&P500', '2025', '-19%', '下落2ヶ月・回復3ヶ月。-20%には届かず'),
]

# 初回アクセス時にスローガン欄へ入れる初期値（1行=1項目。ユーザーが自由に編集する）
DRILL_DEFAULT_SLOGAN = """売らない。下落で売った瞬間、この訓練は失敗する
暴落は異常事態ではない。数年に1回必ず来る「予定されたイベント」
底は当てられない。発動後もさらに下がるのが普通。慌てず段階的に
現金は攻めの弾薬。平時に貯めた者だけが暴落で買える
下落は数ヶ月かけて進む。今日ぜんぶ買う必要はない"""


def _drill_meters():
    """日経・S&P500の「52週高値からの下落率」メーターを組む

    IndexPrice（update_index_prices が蓄積）から直近252営業日の高値と最新終値で算出。
    データ未取得なら None を返し、テンプレートで案内を出す。
    """
    from japan_kabu.models import IndexPrice

    meters = []
    for symbol, label in [('N225', '日経平均'), ('GSPC', 'S&P500')]:
        rows = list(IndexPrice.objects.filter(symbol=symbol)
                    .order_by('-date').values_list('date', 'close')[:252])
        if len(rows) < 30:
            meters.append({'symbol': symbol, 'label': label, 'ok': False})
            continue
        latest_date, latest = rows[0]
        high_date, high = max(reversed(rows), key=lambda r: r[1])
        dd = (latest / high - 1) * 100 if high else 0
        depth = min(max(-dd, 0), DRILL_METER_MAX)

        # 現在どの段階か（超えた最深ライン）。どれも超えていなければ平時
        level = None
        for lv in DRILL_LEVELS:
            if -dd >= lv['th']:
                level = lv
        meters.append({
            'symbol': symbol, 'label': label, 'ok': True,
            'latest': latest, 'latest_date': latest_date,
            'high': high, 'high_date': high_date,
            'dd': dd,
            'pos': depth / DRILL_METER_MAX * 100,   # メーター上のマーカー位置(%)
            'level': level,
            'fired': level is not None and level['th'] >= 20,
        })
    return meters


def drill(request):
    """避難訓練（爆下げプログラム）

    暴落が来た日にパニックにならないための「毎日読む」ページ。
    数字の管理ではなく心構えの反復訓練が主目的（ユーザー要望）:
    1. スローガン（合言葉）を毎日読む
    2. 下落メーターで「いま歴史のどの位置か」を毎日見る
    3. 弾薬（現金）ゲージで確保のモチベーションを保つ
    """
    note = DrillNote.get()
    if not note.slogan:
        # 初回だけ雛形を入れる（空のテキストエリアでは何を書くべきか分からないため）
        note.slogan = DRILL_DEFAULT_SLOGAN
        note.save(update_fields=['slogan'])

    if request.method == 'POST':
        form_id = request.POST.get('form_id')
        if form_id == 'drill_note':
            note.slogan = request.POST.get('slogan', '').strip()
            note.lessons = request.POST.get('lessons', '').strip()
            note.save()
            messages.success(request, 'スローガンと教訓を保存しました。')
            return redirect('portfolio:drill')
        if form_id == 'drill_cash_target':
            try:
                note.cash_target = max(0.0, float(request.POST.get('cash_target', '') or 0))
                note.save(update_fields=['cash_target'])
                messages.success(request, '現金の目標額を保存しました。')
            except ValueError:
                messages.error(request, '目標額は数値で入力してください。')
            return redirect('portfolio:drill')

    data = build_portfolio()
    cash = data['by_class']['cash']['value']
    ammo_pct = (cash / note.cash_target * 100) if note.cash_target > 0 else None

    context = {
        'note': note,
        'slogans': [s for s in note.slogan.splitlines() if s.strip()],
        'meters': _drill_meters(),
        'levels': DRILL_LEVELS,
        'meter_max': DRILL_METER_MAX,
        'evidence': DRILL_EVIDENCE,
        'episodes': DRILL_EPISODES,
        'cash': cash,
        'cash_ratio': data['cash_ratio'],
        'total': data['total'],
        'ammo_pct': ammo_pct,
        'ammo_width': min(ammo_pct, 100) if ammo_pct is not None else 0,
        'ammo_remaining': max(0.0, note.cash_target - cash),
    }
    return render(request, 'portfolio/drill.html', context)


def register(request):
    """資産の登録（棚卸し）と入出金の記録

    1ページに小フォームを並べ、hidden の form_id でどのフォームの送信か判定する。
    タブUI（JS）は後回しにして、まず全タイプを縦に並べて動くものにする。
    """
    setting = PortfolioSetting.get()
    today = date.today()

    forms_map = {
        'stock': StockHoldingForm(),
        'fund': FundHoldingForm(),
        'metal': MetalHoldingForm(),
        'cash': CashBaselineForm(initial={
            # 100000.0 のような小数表示を避ける（整数なら整数で見せる）
            'amount': (int(setting.baseline_cash)
                       if setting.baseline_cash and float(setting.baseline_cash).is_integer()
                       else setting.baseline_cash or None),
        }),
        'cashflow': CashFlowForm(initial={'date': today}),
    }

    if request.method == 'POST':
        form_id = request.POST.get('form_id')

        if form_id == 'stock':
            form = StockHoldingForm(request.POST)
            if form.is_valid():
                stock = form.cleaned_data['stock']
                Holding.objects.update_or_create(
                    stock=stock,
                    account=form.cleaned_data.get('account') or '',
                    defaults={
                        'quantity': form.cleaned_data['quantity'],
                        'avg_cost': form.cleaned_data['avg_cost'],
                        'baseline_date': today,
                        'style': form.cleaned_data.get('style') or '',
                        # sector は触らない（自動判定に任せる。手動値があれば保持される）
                    })
                messages.success(request, f'{stock.display_code} {stock.name} を登録しました。')
                return redirect('portfolio:register')
            forms_map['stock'] = form

        elif form_id == 'fund':
            form = FundHoldingForm(request.POST)
            if form.is_valid():
                product = form.cleaned_data['product']
                Holding.objects.update_or_create(
                    product=product,
                    account=form.cleaned_data.get('account') or '',
                    defaults={
                        'quantity': form.cleaned_data['quantity'],
                        'avg_cost': form.cleaned_data['avg_cost'],
                        'baseline_date': today,
                    })
                messages.success(request, f'{product.display_name} を登録しました。')
                return redirect('portfolio:register')
            forms_map['fund'] = form

        elif form_id == 'metal':
            form = MetalHoldingForm(request.POST)
            if form.is_valid():
                product = form.get_or_create_product()
                Holding.objects.update_or_create(
                    product=product,
                    account=form.cleaned_data.get('account') or '',
                    defaults={
                        'quantity': form.cleaned_data['quantity'],
                        'avg_cost': form.cleaned_data['avg_cost'],
                        'baseline_date': today,
                    })
                messages.success(request, f'{product.display_name} を登録しました。')
                return redirect('portfolio:register')
            forms_map['metal'] = form

        elif form_id == 'cash':
            form = CashBaselineForm(request.POST)
            if form.is_valid():
                setting.baseline_cash = form.cleaned_data['amount']
                setting.baseline_cash_date = today
                setting.save()
                messages.success(request, '現金の期首残高を保存しました。')
                return redirect('portfolio:register')
            forms_map['cash'] = form

        elif form_id == 'cashflow':
            form = CashFlowForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, '入出金を記録しました。')
                return redirect('portfolio:register')
            forms_map['cashflow'] = form

        elif form_id == 'product_edit':
            product = Product.objects.filter(pk=request.POST.get('product_id')).first()
            if product:
                form = ProductEditForm(request.POST, instance=product)
                if form.is_valid():
                    form.save()
                    messages.success(request, f'{product.display_name} の商品情報を更新しました。')
                    return redirect('portfolio:register')
                messages.error(request, '商品情報の更新に失敗しました。入力を確認してください。')

        elif form_id == 'targets':
            total = 0.0
            values = {}
            for key, _label in TargetAllocation.ASSET_CLASS_CHOICES:
                try:
                    v = float(request.POST.get(f'target_{key}', '') or 0)
                except ValueError:
                    v = 0.0
                values[key] = max(0.0, v)
                total += values[key]
            for key, v in values.items():
                TargetAllocation.objects.update_or_create(
                    asset_class=key, defaults={'ratio': v})
            if abs(total - 100) < 0.01:
                messages.success(request, '目標ポートフォリオを保存しました（合計100%）。')
            else:
                messages.success(
                    request,
                    f'目標ポートフォリオを保存しました。⚠ 合計が{total:g}%です'
                    '（100%になるよう調整をおすすめします）。')
            return redirect('portfolio:register')

        elif form_id == 'link_settings':
            setting.link_diary_to_holdings = bool(request.POST.get('link_holdings'))
            setting.link_diary_to_cash = bool(request.POST.get('link_cash'))
            setting.save()
            messages.success(request, '売買日記との連動設定を保存しました。')
            return redirect('portfolio:register')

        elif form_id == 'product_delete':
            product = Product.objects.filter(pk=request.POST.get('product_id')).first()
            if product:
                if product.holdings.exists():
                    messages.error(request,
                                   f'{product.display_name} は保有登録があるため削除できません。'
                                   '先に保有行を削除してください。')
                else:
                    name = product.display_name
                    product.delete()  # 価格履歴(ProductPrice)もCASCADEで消える
                    messages.success(request, f'商品「{name}」を削除しました。')
            return redirect('portfolio:register')

        elif form_id == 'holding_edit':
            # 登録済み一覧のインライン編集（数量・取得単価・口座区分）
            holding = Holding.objects.filter(pk=request.POST.get('holding_id')).first()
            if holding:
                try:
                    holding.quantity = float(request.POST.get('quantity', ''))
                    holding.avg_cost = float(request.POST.get('avg_cost', ''))
                except ValueError:
                    messages.error(request, '数量と取得単価は数値で入力してください。')
                    return redirect('portfolio:register')
                account = request.POST.get('account', holding.account)
                if account in dict(Holding.ACCOUNT_CHOICES):
                    holding.account = account
                style = request.POST.get('style', holding.style)
                if style in dict(Holding.STYLE_CHOICES):
                    holding.style = style
                from django.db import IntegrityError
                try:
                    holding.save()
                    messages.success(request, f'{holding} を更新しました。')
                except IntegrityError:
                    messages.error(
                        request,
                        '同じ銘柄・同じ口座区分の行が既にあります。片方を削除するか、'
                        '既存の行の数量を編集してください。')
            return redirect('portfolio:register')

        elif form_id == 'holding_delete':
            holding = Holding.objects.filter(pk=request.POST.get('holding_id')).first()
            if holding:
                name = str(holding)
                holding.delete()
                messages.success(request, f'{name} を削除しました。')
            return redirect('portfolio:register')

    holdings = (Holding.objects
                .select_related('stock', 'product')
                .order_by('id'))
    recent_flows = CashFlow.objects.all()[:5]

    # 目標ポートフォリオ入力フォームの現在値（未設定の分類は空欄）
    target_values = {t.asset_class: t.ratio for t in TargetAllocation.objects.all()}
    target_fields = [
        {'key': key, 'label': label, 'value': target_values.get(key, '')}
        for key, label in TargetAllocation.ASSET_CLASS_CHOICES
    ]
    target_total = sum(target_values.values())

    fund_products = list(Product.objects.filter(category='fund'))
    # 商品情報の編集フォーム（ISIN・協会コードを後から追記して自動取得へ乗せる）
    product_edit_forms = [(p, ProductEditForm(instance=p)) for p in fund_products]

    context = {
        'forms': forms_map,
        'holdings': holdings,
        'recent_flows': recent_flows,
        'setting': setting,
        # 商品が0件のときは「登録済みから選ぶ」プルダウンを出さない
        # （空のプルダウンが先頭にあると初回登録の導線が分からなくなるため）
        'has_fund_products': bool(fund_products),
        'product_edit_forms': product_edit_forms,
        'target_fields': target_fields,
        'target_total': target_total,
    }
    return render(request, 'portfolio/register.html', context)
