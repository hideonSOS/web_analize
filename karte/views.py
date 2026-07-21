from collections import OrderedDict

from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from japan_kabu.models import Stock

from .models import Executive, KpiEntry, MidTermTarget, StockKarte

# カルテの雛形定義（全銘柄共通）。
# hint は「IR資料のどこを見るか」の手引き。読みながら埋めることで頭に入れるのが狙い。
SECTIONS = [
    ('投資判断', [
        ('hypothesis', '投資仮説',
         'なぜ今買うのか。市場が見落としている点は何か'),
        ('disconfirm', '仮説が崩れる条件',
         '「これが起きたら撤退」と言える具体的な条件。事前に決めておく'),
        ('next_check', '次回決算で確認すること',
         '次に見るべき数字を1〜3個。決算後にここを見返す'),
    ]),
    ('経営陣の考課', [
        ('mgmt_track_record', '実績・公約の達成度',
         '過去の中計や公約は達成されたか。未達のときの説明は誠実か、言い訳に終始していないか'),
        ('mgmt_capital', '資本配分の巧拙',
         'M&A・自社株買い・設備投資の判断は的確か。ROIC/資本コストを意識した発言があるか'),
        ('mgmt_stance', '姿勢・開示の質',
         '経歴と在任期間、自社株の保有状況、株主との対話姿勢。都合の悪い情報も開示しているか'),
    ]),
    ('事業理解', [
        ('business_model', '事業内容・稼ぎ方',
         '何を、誰に売って稼いでいるか。決算説明資料の冒頭「事業概要」を自分の言葉で書く'),
        ('revenue_structure', '収益構造',
         'ストック型かフロー型か。単価×数量の何が効くか。粗利率の高い事業はどれか'),
    ]),
    ('競争環境', [
        ('strengths', '強み・参入障壁',
         'なぜ競合に真似されないか。シェア・技術・ブランド・スイッチングコスト'),
        ('competition', '競合・市場シェア',
         '誰と戦っているか。市場は伸びているか、シェアを取れているか'),
        ('risks', 'リスク・弱み',
         '何が起きたら業績が崩れるか。顧客集中・規制・為替・原材料'),
    ]),
    ('参照', [
        ('memo', 'その他メモ', '気づいたこと、経営陣の発言、質疑応答で気になった点など'),
    ]),
    ('財務・還元', [
        ('financial_policy', '財務方針・株主還元',
         '配当方針（性向/DOE）、自己株買い、設備投資・研究開発の計画'),
    ]),
]
# フォーム保存対象のフィールド一覧
FIELDS = [f for _, items in SECTIONS for f, _, _ in items]


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
                {'field': f, 'label': label, 'hint': hint, 'value': getattr(karte, f)}
                for f, label, hint in items
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
    karte.save()
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
def delete_kpi(request, code, pk):
    get_object_or_404(KpiEntry, pk=pk, karte__stock__display_code=code).delete()
    return redirect('karte:detail', code=code)


@require_POST
def delete(request, code):
    get_object_or_404(StockKarte, stock__display_code=code).delete()
    return redirect('karte:index')
