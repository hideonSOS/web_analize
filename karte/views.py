from collections import OrderedDict

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from japan_kabu.models import Stock

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


def index(request):
    """カルテ一覧 + 新規作成"""
    kartes = StockKarte.objects.select_related('stock').all()
    rows = []
    for k in kartes:
        filled = sum(1 for f in FIELDS if getattr(k, f).strip())
        rows.append({
            'k': k,
            'filled': filled,
            'total': len(FIELDS),
            'pct': round(filled / len(FIELDS) * 100),
        })
    context = {
        'rows': rows,
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

    # 雛形セクション（値とヒントを添えて描画用に整形）
    sections = [
        {
            'group': group,
            'items': [
                {'field': f, 'label': label, 'value': getattr(karte, f)}
                for f, label in items
            ],
        }
        for group, items in SECTIONS
    ]
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

    context = {
        'stock': stock,
        'karte': karte,
        'executives': karte.executives.all(),
        'videos': karte.videos.all(),
        'screenshots': karte.screenshots.all(),
        'sections': sections,
        'filled': filled,
        'total_fields': len(FIELDS),
        'targets': karte.targets.all(),
        'kpi_groups': grouped,
        'kpi_chart': kpi_chart,
        'has_indicator_page': stock.country == 'JP',
    }
    return render(request, 'karte/detail.html', context)


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
