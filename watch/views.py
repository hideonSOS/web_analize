"""監視（/watch/）。仕様は models.py の docstring。

横棒の意味: 左端＝1年高値（0%）、右へ行くほど高値から下落。赤い帯＝現在の下落率、シアンの線＝買値
（目標価格）の位置。現在値が買値以下なら帯が緑になり「買値到達」。
"""
import json
import math

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from japan_kabu.models import DailyPrice, Stock
from japan_kabu.prices import bulk_price_stats

from .models import WatchItem

MIN_SCALE = 30      # 横棒の右端は最低でも −30%。下落や買値がそれより深ければ 10% 刻みで広げる


def _rows():
    items = list(WatchItem.objects.select_related('stock'))
    stats = bulk_price_stats([i.stock for i in items])
    rows = []
    for it in items:
        s = it.stock
        st = stats.get(s.code)
        is_us = s.country == 'US'
        row = {'item': it, 'stock': s, 'is_us': is_us, 'unit_pre': '$' if is_us else '', 'unit_suf': '' if is_us else '円',
               'stats': st, 'reached': False, 'dd': None, 'target_dd': None, 'distance': None, 'bar': None}
        y = st['1y'] if st and st.get('1y') else None
        if st and y:
            cur, high = st['current'], y['high']
            row['current'], row['as_of'], row['high'], row['low'] = cur, st['as_of'], high, y['low']
            row['dd'] = y['drawdown']                                   # 負の%（高値更新中なら 0）
            row['dd3'] = st['3y']['drawdown'] if st.get('3y') else None
            if it.target_price:
                row['target_dd'] = (it.target_price / high - 1) * 100    # 買値が高値から何%下か
                row['distance'] = (cur / it.target_price - 1) * 100      # 現在値が買値より何%上か（マイナスなら到達）
                row['reached'] = cur <= it.target_price
            # 横棒の目盛り: 下落率・買値の深い方を 10% 刻みで切り上げ（最低 30%）
            deepest = max(abs(row['dd']), abs(row['target_dd'] or 0), MIN_SCALE)
            scale = math.ceil(deepest / 10) * 10
            row['bar'] = {
                'scale': scale,
                'fill': min(100.0, abs(row['dd']) / scale * 100),
                'target': (min(100.0, abs(row['target_dd']) / scale * 100) if row['target_dd'] is not None else None),
                'ticks': [{'pct': p, 'pos': p / scale * 100} for p in range(10, scale + 1, 10)],
            }
        rows.append(row)
    return rows


def index(request):
    rows = _rows()
    return render(request, 'watch/index.html', {
        'rows': rows,
        'reached_n': sum(1 for r in rows if r['reached']),
    })


@require_POST
def add(request):
    code = request.POST.get('stock_code', '').strip()
    stock = Stock.objects.filter(code=code).first()
    if stock is None:
        messages.error(request, '銘柄を候補から選んでください。')
        return redirect('watch:index')
    target = _float(request.POST.get('target_price'))
    item, created = WatchItem.objects.get_or_create(stock=stock, defaults={'target_price': target,
                                                                            'note': request.POST.get('note', '').strip()[:200]})
    if not created:
        if target is not None:
            item.target_price = target
            item.save(update_fields=['target_price', 'updated_at'])
        messages.info(request, f'{stock.display_code} はすでに監視中です。' + ('買値を更新しました。' if target else ''))
    else:
        messages.success(request, f'{stock.display_code} {stock.name} を監視に追加しました。')
        # 日足が無ければその場で取る（カルテの「株価を取得」と同じ。数秒）
        if not DailyPrice.objects.filter(stock=stock).exists():
            _fetch(stock, request)
    return redirect('watch:index')


@require_POST
def update(request, pk):
    """買値・一言の変更（一覧のインライン編集）"""
    item = get_object_or_404(WatchItem, pk=pk)
    target = _float(request.POST.get('target_price'))
    item.target_price = target
    if 'note' in request.POST:
        item.note = request.POST.get('note', '').strip()[:200]
    item.save()
    return redirect('watch:index')


@require_POST
def delete(request, pk):
    item = get_object_or_404(WatchItem, pk=pk)
    code = item.stock.display_code
    item.delete()
    messages.success(request, f'{code} を監視から外しました。')
    return redirect('watch:index')


@require_POST
def fetch_prices(request, pk):
    """「株価を更新」ボタン: この銘柄の日足を差分取得（update_daily_prices --code）"""
    item = get_object_or_404(WatchItem, pk=pk)
    _fetch(item.stock, request)
    return redirect('watch:index')


@require_POST
def reorder(request):
    """ドラッグ後の並びを保存。ボディ: {"order": [pk, ...]}"""
    try:
        order = [int(x) for x in json.loads(request.body or '{}').get('order', [])]
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'ok': False}, status=400)
    for i, pk in enumerate(order, 1):
        WatchItem.objects.filter(pk=pk).update(sort_order=i)
    return JsonResponse({'ok': True})


def _float(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch(stock, request):
    from io import StringIO

    from django.core.management import call_command
    out, err = StringIO(), StringIO()
    try:
        call_command('update_daily_prices', code=stock.display_code, years=3, stdout=out, stderr=err)
    except Exception as e:   # noqa: BLE001
        messages.error(request, f'{stock.display_code} の株価取得に失敗: {e}')
        return
    n = DailyPrice.objects.filter(stock=stock).count()
    if n:
        messages.success(request, f'{stock.display_code} の株価を取得しました（{n:,}日分）。')
    else:
        messages.error(request, f'{stock.display_code} の株価を取得できませんでした。' + (err.getvalue().strip()[-160:]))
