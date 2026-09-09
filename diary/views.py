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


def contra(request):
    """逆張りトレードのダッシュボード（2026-09-09）。

    保有中の一覧（損切り線・利確線・現在価格・経過日数を1本のレンジバーで）、新規エントリー
    （1〜2%ルールから許容株数を逆算）、決済、成績（勝率 vs 分岐勝率・裁量回数・累積損益）。
    POST は form_id で分岐: setting / open / close / refresh / delete
    """
    from datetime import date as _date

    from django.contrib import messages
    from django.core.management import call_command

    from . import contra as C
    from .models import ContraSetting, Trade

    setting = ContraSetting.get()

    if request.method == 'POST':
        form_id = request.POST.get('form_id', '')

        def _f(name, default=None):
            try:
                return float(str(request.POST.get(name, '')).replace(',', ''))
            except ValueError:
                return default

        if form_id == 'setting':
            cap = _f('capital'); risk = _f('risk_pct'); sp = _f('default_stop_pct')
            tp = _f('default_target_pct'); cost = _f('cost_pct')
            if None in (cap, risk, sp, tp, cost) or cap <= 0 or not (0 < risk <= 10) or sp <= 0 or tp <= 0:
                messages.error(request, '設定の値を確認してください（資金>0・リスク0〜10%・幅>0）。')
            else:
                setting.capital, setting.risk_pct = int(cap), risk
                setting.default_stop_pct, setting.default_target_pct, setting.cost_pct = sp, tp, cost
                setting.save()
                messages.success(request, '設定を保存しました。')
            return redirect('diary:contra')

        if form_id == 'open':
            code = request.POST.get('stock_code', '').strip()
            stock = Stock.objects.filter(code=code).first()
            price, shares = _f('entry_price'), _f('shares')
            sp, tp = _f('stop_pct', setting.default_stop_pct), _f('target_pct', setting.default_target_pct)
            try:
                entry_date = datetime.strptime(request.POST.get('entry_date', ''), '%Y-%m-%d').date()
            except ValueError:
                entry_date = _date.today()
            if stock is None or not price or price <= 0 or not shares or shares <= 0 or sp <= 0 or tp <= 0:
                messages.error(request, '銘柄・約定価格・株数・損切り幅・利確幅をすべて入れてください。')
                return redirect('diary:contra')
            currency = 'USD' if stock.country == 'US' else 'JPY'
            fx_date, fx = C.latest_fx()
            limit = C.max_shares(setting, price, sp, currency, fx)
            t = Trade.objects.create(
                stock=stock, stock_name=stock.name, ticker=stock.display_code, country=stock.country,
                currency=currency, strategy='contra', entry_date=entry_date, entry_price=price,
                shares=int(shares), stop_pct=sp, target_pct=tp,
                stop_price=round(price * (1 - sp / 100), 4), target_price=round(price * (1 + tp / 100), 4),
                entry_note=request.POST.get('entry_note', '').strip(),
                fx_at_entry=fx if currency == 'USD' else None,
                over_risk=int(shares) > limit['shares'],
            )
            t.risk_jpy = C.plan_risk(t, fx)
            t.save(update_fields=['risk_jpy'])
            # 日記にも「買い」として残す（全部の記録を日記で読めるように）
            t.entry_diary = DiaryEntry.objects.create(
                stock=stock, stock_name=stock.name, stock_code=stock.display_code,
                recorded_at=timezone.now(), price=price, shares=int(shares),
                target_price=t.target_price, stop_price=t.stop_price, action='buy',
                tags='逆張り', mood='', reason=t.entry_note or '逆張りルールでエントリー',
                strategy='contra', trade=t)
            t.save(update_fields=['entry_diary'])
            try:
                call_command('update_trade_bars', trade=t.id)
            except Exception as e:   # noqa: BLE001
                messages.warning(request, f'日足の取得に失敗しました（後で「更新」を押してください）: {e}')
            msg = f'{stock.display_code} を {int(shares)}株 @{price} で記録。損切り {t.stop_price:g}／利確 {t.target_price:g}。'
            if t.over_risk:
                messages.warning(request, msg + f' ⚠️ 許容株数 {limit["shares"]} を超えています（裁量として記録）。')
            else:
                messages.success(request, msg)
            return redirect('diary:contra')

        if form_id == 'close':
            t = get_object_or_404(Trade, pk=request.POST.get('id'))
            price = _f('exit_price')
            reason = request.POST.get('exit_reason', '')
            try:
                exit_date = datetime.strptime(request.POST.get('exit_date', ''), '%Y-%m-%d').date()
            except ValueError:
                exit_date = _date.today()
            if not price or price <= 0 or reason not in dict(Trade.EXIT):
                messages.error(request, '決済価格と理由を入れてください。')
                return redirect('diary:contra')
            t.exit_date, t.exit_price, t.exit_reason = exit_date, price, reason
            t.exit_note = request.POST.get('exit_note', '').strip()
            t.exit_diary = DiaryEntry.objects.create(
                stock=t.stock, stock_name=t.stock_name, stock_code=t.ticker,
                recorded_at=timezone.now(), price=price, shares=t.shares, action='sell',
                tags='逆張り', reason=f'{t.get_exit_reason_display()}で決済' + (f'：{t.exit_note}' if t.exit_note else ''),
                strategy='contra', trade=t)
            t.save()
            net = t.pnl_pct_net(setting.cost_pct)
            messages.success(request, f'{t.ticker} を {t.get_exit_reason_display()}で決済（コスト込み {net:+.2f}%）。')
            return redirect('diary:contra')

        if form_id == 'refresh':
            try:
                call_command('update_trade_bars')
                messages.success(request, '保有中の日足を更新しました。')
            except Exception as e:   # noqa: BLE001
                messages.error(request, f'更新に失敗: {e}')
            return redirect('diary:contra')

        if form_id == 'delete':
            t = get_object_or_404(Trade, pk=request.POST.get('id'))
            DiaryEntry.objects.filter(trade=t).delete()
            t.delete()
            messages.success(request, '取引を削除しました（日記の自動記録も消しました）。')
            return redirect('diary:contra')

    fx_date, fx = C.latest_fx()
    be = C.breakeven(setting.default_stop_pct, setting.default_target_pct, setting.cost_pct)
    return render(request, 'diary/contra.html', {
        'setting': setting, 'fx': fx, 'fx_date': fx_date, 'be': be,
        'open_rows': C.open_rows(setting), 'stats': C.stats(setting),
        'exit_choices': Trade.EXIT, 'today': _date.today().isoformat(),
        'risk_budget': setting.capital * setting.risk_pct / 100,
    })


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
