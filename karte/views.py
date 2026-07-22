import json
from collections import OrderedDict

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from diary.models import DiaryEntry
from japan_kabu.models import DailyPrice, Stock
from japan_kabu.prices import bulk_price_stats, price_stats

from .models import (Executive, KpiEntry, MidTermTarget, ReferenceVideo,
                     Screenshot, StockKarte)

# カルテの雛形定義（全銘柄共通）。各セクションは自由記述1つ。
# 説明書きは付けない（項目名だけで書き始められるようにする方針）
SECTIONS = [
    ('経営陣の考課', [('mgmt_note', '経営陣の評価')]),
    ('事業理解', [('business_note', '事業内容')]),
    ('投資判断', [('invest_note', '投資判断')]),
    ('競争環境', [('competitive_note', '競争環境')]),
]
# フォーム保存対象のフィールド一覧
FIELDS = [f for _, items in SECTIONS for f, _ in items]

# 並び替え対象セクションのキーと既定順。銘柄ごとの並び順は karte.section_order に保存し、
# ここに無いキーは無視、欠けているキーは既定順で末尾に補う（セクション追加に強くする）。
DEFAULT_SECTION_ORDER = [
    'mgmt', 'business', 'videos', 'screenshots',
    'price', 'invest', 'competitive', 'kpi', 'targets',
]


def resolve_section_order(saved):
    """保存済みの並び順を正規化する。未知キー除去 + 欠落キーを既定順で補完。"""
    saved = saved or []
    order = [k for k in saved if k in DEFAULT_SECTION_ORDER]
    order += [k for k in DEFAULT_SECTION_ORDER if k not in order]
    return order


def index(request):
    """カルテ一覧 + 新規作成 + 押し目一覧（高値からの下落率）"""
    kartes = list(StockKarte.objects.select_related('stock').all())

    # 押し目一覧の母集団は「カルテ + 売買日記」。カルテ未作成でも保有中なら
    # リバランス対象になるため（株価取得バッチの対象範囲と揃えてある）
    karte_codes = {k.stock_id for k in kartes}
    diary_stocks = list(Stock.objects.filter(
        code__in=DiaryEntry.objects.values_list('stock_id', flat=True)
    ).exclude(code__in=karte_codes))
    target_stocks = [k.stock for k in kartes] + diary_stocks

    stats = bulk_price_stats(target_stocks)
    rows = []
    for k in kartes:
        filled = sum(1 for f in FIELDS if getattr(k, f).strip())
        rows.append({
            'k': k,
            'filled': filled,
            'total': len(FIELDS),
            'pct': round(filled / len(FIELDS) * 100),
            'price': stats.get(k.stock_id),
        })

    # 押し目が深い順（＝高値から最も下落している銘柄が先頭）。
    # 主役はドローダウンでレンジ内位置ではない（位置は期間を延ばすほど鈍化するため）。
    dips = []
    for s in target_stocks:
        p = stats.get(s.code)
        if not p or not p.get('1y'):
            continue
        dd = p['1y']['drawdown']
        dips.append({
            'stock': s,
            'dd': dd,
            # バー幅は下落率の絶対値（テンプレートでabsが使えないためここで出す）
            'width': min(100.0, abs(dd)),
            'dd_3y': p['3y']['drawdown'] if p.get('3y') else None,
            # カルテ未作成の銘柄は詳細ページが無い（開くと404になる）ためリンクしない
            'has_karte': s.code in karte_codes,
        })
    dips.sort(key=lambda d: d['dd'])

    context = {
        'rows': rows,
        'dips': dips,
        'total_fields': len(FIELDS),
    }
    return render(request, 'karte/index.html', context)


def stock_options(request):
    """新規カルテ作成用の銘柄検索リスト（JSON・1時間キャッシュ）"""
    from django.http import JsonResponse
    options = [
        {'code': s.code, 'ticker': s.display_code, 'name': s.name, 'country': s.country}
        for s in Stock.objects.all().order_by('country', 'code')
    ]
    resp = JsonResponse({'stocks': options})
    resp['Cache-Control'] = 'public, max-age=3600'
    return resp


@require_POST
def create(request):
    """銘柄を選んでカルテを作成（ウォッチリストへの自動登録を兼ねる）"""
    code = request.POST.get('stock_code', '').strip()
    stock = Stock.objects.filter(code=code).first()
    if stock is None:
        return redirect('karte:index')
    karte, _ = StockKarte.objects.get_or_create(stock=stock)
    return redirect('karte:detail', code=stock.display_code)


def detail(request, code):
    """カルテ詳細（雛形フォーム + 中計進捗 + KPIグラフ）"""
    stock = Stock.objects.filter(display_code=code).first()
    if stock is None:
        raise Http404
    karte = StockKarte.objects.filter(stock=stock).first()
    if karte is None:
        raise Http404

    filled = sum(1 for f in FIELDS if getattr(karte, f).strip())

    # KPIをname別にまとめてグラフ用データにする
    grouped = OrderedDict()
    for e in karte.kpis.all():
        grouped.setdefault(e.name, {'unit': e.unit, 'periods': [], 'values': [], 'ids': []})
        grouped[e.name]['periods'].append(e.period)
        grouped[e.name]['values'].append(e.value)
        grouped[e.name]['ids'].append(e.id)
    kpi_chart = [
        {'name': name, 'unit': d['unit'], 'periods': d['periods'], 'values': d['values']}
        for name, d in grouped.items()
    ]

    # 銘柄ごとに保存された表示順（無ければ既定順）
    section_keys = resolve_section_order(karte.section_order)

    # 買い場判断用のレンジ統計（高値からの下落率とレンジ内位置）
    price_rows = list(DailyPrice.objects.filter(stock=stock)
                      .order_by('date').values_list('date', 'close'))
    price = price_stats(stock, rows=price_rows) if price_rows else None
    # 3年チャート用（日付は軸ラベルにするので文字列に変換しておく）
    price_chart = {
        'dates': [d.isoformat() for d, _ in price_rows],
        'values': [c for _, c in price_rows],
        'high_1y': price['1y']['high'] if price and price.get('1y') else None,
        'low_1y': price['1y']['low'] if price and price.get('1y') else None,
    } if price_rows else None

    context = {
        'stock': stock,
        'karte': karte,
        'executives': karte.executives.all(),
        'videos': karte.videos.all(),
        'screenshots': karte.screenshots.all(),
        'section_keys': section_keys,
        'price': price,
        'price_chart': price_chart,
        'filled': filled,
        'total_fields': len(FIELDS),
        'targets': karte.targets.all(),
        'kpi_groups': grouped,
        'kpi_chart': kpi_chart,
        'has_indicator_page': stock.country == 'JP',
    }
    return render(request, 'karte/detail.html', context)


@require_POST
def reorder(request, code):
    """セクションの並び順を保存する（ドラッグ&ドロップ後にJSから呼ぶ）。

    リクエストボディは JSON: {"order": ["mgmt", "targets", ...]}。
    未知キーは捨て、既定順で欠けを補ってから保存する。
    """
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    try:
        payload = json.loads(request.body or '{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)
    order = resolve_section_order(payload.get('order'))
    karte.section_order = order
    karte.save(update_fields=['section_order', 'updated_at'])
    return JsonResponse({'ok': True, 'order': order})


@require_POST
def save(request, code):
    """雛形フォームの保存（空欄のままでも保存できる）"""
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    for f in FIELDS:
        setattr(karte, f, request.POST.get(f, '').strip())
    karte.ir_url = request.POST.get('ir_url', '').strip()
    next_earnings_date = request.POST.get('next_earnings_date', '').strip()
    karte.next_earnings_date = next_earnings_date or None
    karte.save()

    # 動画の要約も同じ保存ボタンでまとめて更新する（video_note_<pk> で送られてくる）
    updated = []
    for video in karte.videos.all():
        key = f'video_note_{video.pk}'
        if key in request.POST:
            video.note = request.POST[key].strip()
            updated.append(video)
    if updated:
        ReferenceVideo.objects.bulk_update(updated, ['note'])
    return redirect('karte:detail', code=code)


@require_POST
def add_target(request, code):
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    try:
        target_value = float(request.POST.get('target_value', ''))
    except ValueError:
        return redirect('karte:detail', code=code)
    try:
        current_value = float(request.POST.get('current_value', ''))
    except ValueError:
        current_value = None
    label = request.POST.get('label', '').strip()
    if label:
        MidTermTarget.objects.create(
            karte=karte, label=label, target_value=target_value,
            current_value=current_value,
            unit=request.POST.get('unit', '').strip(),
            target_fy=request.POST.get('target_fy', '').strip(),
        )
    return redirect('karte:detail', code=code)


@require_POST
def delete_target(request, code, pk):
    get_object_or_404(MidTermTarget, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def add_kpi(request, code):
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    try:
        value = float(request.POST.get('value', ''))
    except ValueError:
        return redirect('karte:detail', code=code)
    name = request.POST.get('name', '').strip()
    period = request.POST.get('period', '').strip()
    if name and period:
        KpiEntry.objects.update_or_create(
            karte=karte, name=name, period=period,
            defaults={'value': value, 'unit': request.POST.get('unit', '').strip()},
        )
    return redirect('karte:detail', code=code)


@require_POST
def add_executive(request, code):
    """経営陣を追加（写真とコメントのみ。どちらか一方でも登録できる）"""
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    photo = request.FILES.get('photo')
    note = request.POST.get('note', '').strip()
    if photo or note:
        Executive.objects.create(
            karte=karte,
            photo=photo,
            note=note,
            order=karte.executives.count(),
        )
    return redirect('karte:detail', code=code)


@require_POST
def delete_executive(request, code, pk):
    get_object_or_404(Executive, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def add_video(request, code):
    """参照動画を追加（YouTubeのURLと要約）"""
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    url = request.POST.get('url', '').strip()
    if url:
        ReferenceVideo.objects.create(
            karte=karte,
            url=url,
            title=request.POST.get('title', '').strip(),
            note=request.POST.get('note', '').strip(),
            order=karte.videos.count(),
        )
    return redirect('karte:detail', code=code)


@require_POST
def delete_video(request, code, pk):
    get_object_or_404(ReferenceVideo, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def add_screenshot(request, code):
    """スクリーンショットを追加（画像とコメント）"""
    karte = get_object_or_404(StockKarte, stock__display_code=code)
    image = request.FILES.get('image')
    note = request.POST.get('note', '').strip()
    if image or note:
        Screenshot.objects.create(
            karte=karte,
            image=image,
            note=note,
            order=karte.screenshots.count(),
        )
    return redirect('karte:detail', code=code)


@require_POST
def delete_screenshot(request, code, pk):
    get_object_or_404(Screenshot, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def delete_kpi(request, code, pk):
    get_object_or_404(KpiEntry, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def delete(request, code):
    get_object_or_404(StockKarte, stock__display_code=code).delete()
    return redirect('karte:index')
