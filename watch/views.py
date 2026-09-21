"""監視（/watch/）。仕様は models.py の docstring。

横棒（ゲージ）の意味（2026-09-21 ユーザー指示で向きを反転。「ゲージが上がる＝下がり切る」では直感が鈍る）:
  左端＝買値（目標価格）、右端＝1年高値。帯＝「買値までの残り」なので **株価が下がるほど帯が縮む**。
  帯の右端が現在値。帯が空（現在値≦買値）になったら緑で「買値到達」。買値が未設定なら左端は1年安値。
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
            # ゲージ: 左端=買値（無ければ1年安値）、右端=1年高値。帯の長さ=買値までの残り（下がるほど縮む）
            # ゲージは「買値まであと何%」。左端=買値（0%）。右端は全銘柄で同じスケール（後で揃える）
            left = it.target_price if it.target_price else y['low']
            row['bar'] = {
                'left_label': '買値' if it.target_price else '1年安値',
                'left': left,
                'pct': (cur / left - 1) * 100 if left > 0 else 0.0,   # 現在値が左端より何%上か
            }
        rows.append(row)
    # 全銘柄で同じ目盛り（ユーザー指示 2026-09-21: 高値を右端にする必要はない・スケールを揃える）。
    # 右端 = 最も遠い銘柄の「あと%」を 10% 刻みで切り上げ（最低 30%）。刻みは 30% 以下なら 5%、それ以上は 10%
    pcts = [r['bar']['pct'] for r in rows if r['bar']]
    scale = max(30, math.ceil(max(pcts + [0]) / 10) * 10)
    step = 5 if scale <= 30 else 10
    ticks = [{'pct': p, 'pos': p / scale * 100} for p in range(step, scale + 1, step)]
    for r in rows:
        if r['bar']:
            r['bar'].update({'scale': scale, 'ticks': ticks,
                             'fill': max(0.0, min(100.0, r['bar']['pct'] / scale * 100))})
    # 並びは「買値までの残り%」が小さい順（＝そろそろ買えそうなものが上。到達済み＝マイナスが最上位）。
    # 買値未設定や株価無しは末尾（ユーザー指示 2026-09-21。手動の並び替えはやめた）
    rows.sort(key=lambda r: (r['distance'] is None, r['distance'] if r['distance'] is not None else 0, r['stock'].display_code))
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
    item, created = WatchItem.objects.get_or_create(stock=stock, defaults={'target_price': target})
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
    """買値の変更（一覧のインライン編集）。一言は 2026-09-22 に廃止（列は残置・未使用）"""
    item = get_object_or_404(WatchItem, pk=pk)
    item.target_price = _float(request.POST.get('target_price'))
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
