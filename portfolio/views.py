from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import (
    CashBaselineForm, CashFlowForm, FundHoldingForm, MetalHoldingForm,
    ProductEditForm, StockHoldingForm,
)
from .models import CashFlow, Holding, PortfolioSetting, Product, TargetAllocation
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

    # 大分類の中の銘柄別内訳（該当資産が無いものは出さない）
    breakdowns = [
        {'title': title, 'data': _class_breakdown(items, kinds)}
        for kinds, title in [
            (('fund',), '投資信託の内訳'),
            (('stock_jp', 'stock_us'), '個別株の内訳（日米合算）'),
            (('stock_jp',), '日本株の内訳'),
            (('stock_us',), '米国株の内訳'),
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
                      for r in b['data']['rows']]}
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


def stocks(request):
    """個別株の分析ページ（ポートフォリオの「攻め」部分の専用分析）

    入力データはダッシュボードと共通（build_portfolio）。ここでは個別株だけを
    取り出して、構成比×損益率の散布図・セクター別集計・ドローダウン（押し目度）を出す。
    """
    from japan_kabu.models import Stock
    from japan_kabu.prices import bulk_price_stats

    data = build_portfolio()
    stock_items = [i for i in data['items'] if i['kind'] in ('stock_jp', 'stock_us')]

    total_value = sum(i['value'] for i in stock_items)
    total_pnl = sum(i['pnl'] for i in stock_items if i['pnl'] is not None)
    total_cost = total_value - total_pnl

    # ドローダウン統計（DailyPrice履歴のある銘柄のみ。履歴は夜間バッチが自動蓄積）
    codes = [i['master_code'] for i in stock_items]
    stats = bulk_price_stats(list(Stock.objects.filter(code__in=codes)))

    rows = []
    for i in sorted(stock_items, key=lambda x: -x['value']):
        st = stats.get(i['master_code'])
        rows.append({
            **i,
            'weight': i['value'] / total_value * 100 if total_value else 0,
            'dd1y': st['1y']['drawdown'] if st and st.get('1y') else None,
            'dd3y': st['3y']['drawdown'] if st and st.get('3y') else None,
        })

    # セクター別集計（保有フォームで選んだテーマ、未指定は公式業種）
    sector_map = {}
    for r in rows:
        key = r['sector'] or '（未分類）'
        agg = sector_map.setdefault(key, {'name': key, 'value': 0.0, 'pnl': 0.0, 'has_pnl': False})
        agg['value'] += r['value']
        if r['pnl'] is not None:
            agg['pnl'] += r['pnl']
            agg['has_pnl'] = True
    sectors = sorted(sector_map.values(), key=lambda x: -x['value'])
    max_sector = sectors[0]['value'] if sectors else 0
    for s in sectors:
        s['weight'] = s['value'] / total_value * 100 if total_value else 0
        s['width'] = s['value'] / max_sector * 100 if max_sector else 0
        cost = s['value'] - s['pnl']
        s['pnl_pct'] = s['pnl'] / cost * 100 if (s['has_pnl'] and cost) else None

    # 散布図スペック（横軸=個別株内の構成比% / 縦軸=損益率% / 点の大きさ=評価額）
    scatter_spec = {
        'points': [
            {'name': r['name'], 'weight': round(r['weight'], 2),
             'pnl_pct': round(r['pnl_pct'], 2), 'value': round(r['value'])}
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
        'total_value': total_value,
        'total_pnl': total_pnl,
        'total_pnl_pct': total_pnl / total_cost * 100 if total_cost else None,
        'offense_ratio': total_value / data['total'] * 100 if data['total'] else 0,
        'scatter_spec': scatter_spec,
        'has_stocks': bool(rows),
        'missing_dd': sum(1 for r in rows if r['dd1y'] is None and r['dd3y'] is None),
    }
    return render(request, 'portfolio/stocks.html', context)


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
