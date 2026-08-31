from datetime import datetime

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from japan_kabu.models import Stock

from .models import DiaryEntry

# 判断理由タグと心理状態の選択肢（記録の構造化用）
TAGS = ['テクニカル', 'ファンダメンタルズ', '需給', 'ニュース・材料', 'テーマ・思惑', '直感']
MOODS = ['強気', '中立', '弱気', '不安', '焦り']


def index(request):
    entries = DiaryEntry.objects.select_related('stock').all()

    action = request.GET.get('action', '')
    if action in dict(DiaryEntry.ACTION_CHOICES):
        entries = entries.filter(action=action)
    else:
        action = ''
    q = request.GET.get('q', '').strip()
    if q:
        entries = entries.filter(stock_name__icontains=q)

    rows = []
    for e in entries:
        change = pnl = rr = None
        hit = ''
        current_close = e.stock.close if e.stock else None
        if current_close and e.price:
            change = (current_close / e.price - 1) * 100
            if e.shares:
                # 概算損益: 買いは値上がりがプラス、売りは売却後の値下がり（回避額）がプラス
                diff = current_close - e.price
                pnl = diff * e.shares if e.action == 'buy' else -diff * e.shares if e.action == 'sell' else None
        if e.action == 'buy':
            # リスクリワード比 = (目標 − 記録時株価) ÷ (記録時株価 − 損切り)
            if e.price and e.target_price and e.stop_price and e.price > e.stop_price:
                rr = (e.target_price - e.price) / (e.price - e.stop_price)
            # 出口計画への到達判定
            if current_close:
                if e.target_price and current_close >= e.target_price:
                    hit = 'target'
                elif e.stop_price and current_close <= e.stop_price:
                    hit = 'stop'
        is_us = bool(e.stock and e.stock.country == 'US')
        rows.append({
            'e': e,
            'change': change,
            'pnl': pnl,
            'rr': rr,
            'hit': hit,
            'current_close': current_close,
            'tag_list': [t for t in e.tags.split(',') if t],
            # 通貨表示: 日本株は「…円」、米国株は「$…」
            'is_us': is_us,
            'cur_pre': '$' if is_us else '',
            'cur_suf': '' if is_us else '円',
        })

    all_entries = DiaryEntry.objects.all()
    stats = {
        'total': all_entries.count(),
        'buy': all_entries.filter(action='buy').count(),
        'sell': all_entries.filter(action='sell').count(),
    }

    context = {
        'rows': rows,
        'stats': stats,
        'tags': TAGS,
        'moods': MOODS,
        'filter_action': action,
        'filter_q': q,
        'view': 'list' if request.GET.get('view') == 'list' else '',
        'action_choices': DiaryEntry.ACTION_CHOICES,
        'result_choices': DiaryEntry.RESULT_CHOICES,
    }
    return render(request, 'diary/index.html', context)


def stock_options(request):
    """銘柄検索用マスタ（JP+US）をJSONで返す（ETagで条件付きGET・karte側と同じ実装）

    旧実装の max-age=3600 は「サーバーで import_us_master を実行した直後、
    ブラウザに古いJSONが1時間貼り付き検索にヒットしない」事故を起こす
    （karte側で実際に発生）。ETag + no-cache なら毎回サーバに再確認しつつ、
    無変更なら 304 / 0バイトで済み、約2MBの本文は転送されない。
    ⚠️ close を含むため、株価バッチ後の bulk_update では updated_at が動かず
    ETag が変わらないが、close はサジェストの参考表示であり実害はない
    （銘柄の増減=検索ヒットの問題は件数で確実に検知できる）。
    """
    from django.db.models import Count, Max
    from django.http import HttpResponseNotModified

    agg = Stock.objects.aggregate(n=Count('code'), mx=Max('updated_at'))
    mx = agg['mx'].strftime('%Y%m%d%H%M%S') if agg['mx'] else '0'
    etag = f'W/"{agg["n"] or 0}-{mx}"'
    if request.META.get('HTTP_IF_NONE_MATCH') == etag:
        resp = HttpResponseNotModified()
        resp['ETag'] = etag
        resp['Cache-Control'] = 'no-cache'
        return resp
    options = [
        {'code': s.code, 'ticker': s.display_code, 'name': s.name,
         'close': s.close, 'country': s.country}
        for s in Stock.objects.all().order_by('country', 'code')
    ]
    resp = JsonResponse({'stocks': options})
    resp['ETag'] = etag
    resp['Cache-Control'] = 'no-cache'
    return resp


@require_POST
def create(request):
    # stock_code はマスタのPK（JP:数字コード / US:"US-<ticker>"）
    code = request.POST.get('stock_code', '').strip()
    stock = Stock.objects.filter(code=code).first()
    name = stock.name if stock else request.POST.get('stock_name', '').strip() or '（銘柄未指定）'

    try:
        recorded_at = timezone.make_aware(
            datetime.strptime(request.POST.get('recorded_at', ''), '%Y-%m-%dT%H:%M'))
    except ValueError:
        recorded_at = timezone.now()
    try:
        price = float(request.POST.get('price', ''))
    except ValueError:
        price = None
    try:
        shares = int(request.POST.get('shares', ''))
    except ValueError:
        shares = None

    def _float_or_none(name):
        try:
            return float(request.POST.get(name, ''))
        except ValueError:
            return None
    action = request.POST.get('action', '')
    if action not in dict(DiaryEntry.ACTION_CHOICES):
        action = 'buy'

    DiaryEntry.objects.create(
        stock=stock,
        stock_name=name,
        stock_code=stock.display_code if stock else code,
        recorded_at=recorded_at,
        price=price,
        shares=shares,
        target_price=_float_or_none('target_price'),
        stop_price=_float_or_none('stop_price'),
        action=action,
        tags=','.join(t for t in request.POST.getlist('tags') if t in TAGS),
        mood=request.POST.get('mood', '') if request.POST.get('mood', '') in MOODS else '',
        reason=request.POST.get('reason', '').strip(),
        impression=request.POST.get('impression', '').strip(),
    )
    return redirect('diary:index')


@require_POST
def review(request, pk):
    entry = get_object_or_404(DiaryEntry, pk=pk)
    result = request.POST.get('review_result', '')
    entry.review_result = result if result in dict(DiaryEntry.RESULT_CHOICES) else ''
    entry.review_note = request.POST.get('review_note', '').strip()
    entry.reviewed_at = timezone.now()
    entry.save(update_fields=['review_result', 'review_note', 'reviewed_at'])
    return redirect('diary:index')


@require_POST
def delete(request, pk):
    entry = get_object_or_404(DiaryEntry, pk=pk)
    entry.delete()
    return redirect('diary:index')
