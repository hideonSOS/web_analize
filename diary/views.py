from datetime import datetime

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from japan_kabu.models import Stock

from .models import DiaryEntry

# 判断理由タグと心理状態の選択肢（記録の構造化用）
TAGS = ['テクニカル', 'ファンダメンタルズ', '需給', 'ニュース・材料', 'テーマ・思惑', '直感']
MOODS = ['強気', '中立', '弱気', '不安', '焦り']


def index(request):
    entries = DiaryEntry.objects.select_related('stock').prefetch_related('reviews').all()

    action = request.GET.get('action', '')
    if action in dict(DiaryEntry.ACTION_CHOICES):
        entries = entries.filter(action=action)
    else:
        action = ''
    q = request.GET.get('q', '').strip()
    if q:
        entries = entries.filter(stock_name__icontains=q)

    # 売りの実現損益（2026-09-21 ユーザー決定）: 同じ銘柄の「売りより前の直近の買い」の価格を買値とみなし
    # （売値 − 買値）× 株数。同一銘柄で売買を繰り返す使い方なので直近の買いで実態に合う。
    # 旧「概算損益」（売却後の値動き）は売りでは出さず、「売却後 +x%」の%だけ残す
    buys_by_stock = {}
    for b in DiaryEntry.objects.filter(action='buy', price__isnull=False).exclude(stock__isnull=True).order_by('recorded_at'):
        buys_by_stock.setdefault(b.stock_id, []).append(b)

    rows = []
    for e in entries:
        change = pnl = rr = None
        realized = realized_pct = buy_ref = None
        hit = ''
        current_close = e.stock.close if e.stock else None
        if current_close and e.price:
            change = (current_close / e.price - 1) * 100
            if e.shares and e.action == 'buy':
                pnl = (current_close - e.price) * e.shares      # 買い: 含み損益（概算）
        if e.action == 'sell' and e.price and e.stock_id:
            prev = [b for b in buys_by_stock.get(e.stock_id, []) if b.recorded_at < e.recorded_at]
            if prev:
                buy_ref = prev[-1]
                realized_pct = (e.price / buy_ref.price - 1) * 100
                if e.shares:
                    realized = (e.price - buy_ref.price) * e.shares
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
            'realized': realized, 'realized_pct': realized_pct, 'buy_ref': buy_ref,
            'rr': rr,
            'hit': hit,
            'current_close': current_close,
            'tag_list': [t for t in e.tags.split(',') if t],
            # 通貨表示: 日本株は「…円」、米国株は「$…」
            'is_us': is_us,
            'cur_pre': '$' if is_us else '',
            'cur_suf': '' if is_us else '円',
            # 短期トレードの追跡状態（チェックボックス用）。買いで銘柄・株価・株数があれば追跡できる
            'trade': e.trade,
            'trackable': e.action == 'buy' and bool(e.stock and e.price and e.shares),
            'tracked_open': bool(e.trade and e.trade.exit_date is None),
            'tracked_closed': bool(e.trade and e.trade.exit_date is not None),
        })

    all_entries = DiaryEntry.objects.all()
    stats = {
        'total': all_entries.count(),
        'buy': all_entries.filter(action='buy').count(),
        'sell': all_entries.filter(action='sell').count(),
    }
    from .models import ContraSetting, Trade
    open_trades_n = Trade.objects.filter(exit_date__isnull=True, strategy='contra').count()

    context = {
        'rows': rows,
        'stats': stats,
        'open_trades_n': open_trades_n,
        'contra_setting': ContraSetting.get(),   # フォームの「追跡」チェックでルールを固定するため
        'exit_choices': Trade.EXIT,
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
    # 売りの新フォーム（2026-09-19）: 分類（利益確定/損切り）とルール遵守だけ。タグ・心理は買い・売りとも廃止
    sell_kind = request.POST.get('sell_kind', '') if action == 'sell' else ''
    if sell_kind not in dict(DiaryEntry.SELL_KINDS):
        sell_kind = ''
    rule_followed = None
    if action == 'sell':
        rf = request.POST.get('rule_followed', '')
        rule_followed = True if rf == 'yes' else (False if rf == 'no' else None)

    entry = DiaryEntry.objects.create(
        stock=stock,
        stock_name=name,
        stock_code=stock.display_code if stock else code,
        recorded_at=recorded_at,
        price=price,
        shares=shares,
        target_price=_float_or_none('target_price'),
        stop_price=_float_or_none('stop_price'),
        action=action,
        tags='',    # 判断理由タグ・心理は 2026-09-19 に廃止（フォームから削除。列は過去の記録のため残す）
        mood='',
        sell_kind=sell_kind,
        rule_followed=rule_followed,
        reason=request.POST.get('reason', '').strip(),
        impression='',   # 感想・メモは 2026-09-19 に廃止（フォームから削除）
    )
    # 短期との連動（入力は日記に統一・2026-09-09）:
    #   買い＋「短期トレードとして追跡」チェック → Trade を起こす（損切り/利確は日記の価格から）
    #   売り → 同じ銘柄の保有中の短期取引があれば決済（理由は選択、未選択なら価格から推定）
    from django.contrib import messages

    from . import contra as C
    from .models import ContraSetting
    if action == 'buy' and request.POST.get('track_contra'):
        t = C.open_from_entry(entry, ContraSetting.get(), request.POST.get('risk_scenario', '').strip())
        if t:
            messages.success(request, f'{t.ticker} を短期トレードとして追跡します（損切り {t.stop_price:g}／利確 {t.target_price:g}）。'
                             + ('⚠️ 許容株数を超えています（裁量として記録）。' if t.over_risk else ''))
        else:
            messages.error(request, '短期トレードの追跡には銘柄・株価・株数が必要です（日記の記録は保存しました）。')
    elif action == 'sell':
        # 短期の決済理由は「損切り」なら損切り、「利益確定」なら価格から利確／早期利確を判定
        t = C.close_from_entry(entry, 'stop' if sell_kind == 'loss' else '')
        if t:
            net = t.pnl_pct_net(ContraSetting.get().cost_pct)
            messages.success(request, f'短期トレードの {t.ticker} を{t.get_exit_reason_display()}で決済（コスト込み {net:+.2f}%）。')
    return redirect('diary:index')


@require_POST
def track(request, pk):
    """一覧のチェックボックス: 買いの記録を短期で追跡する／やめる（未決済のみ）"""
    from django.contrib import messages

    from . import contra as C
    from .models import ContraSetting

    entry = get_object_or_404(DiaryEntry, pk=pk)
    if request.POST.get('on'):
        t = C.open_from_entry(entry, ContraSetting.get())
        if t:
            messages.success(request, f'{t.ticker} を短期トレードとして追跡します（損切り {t.stop_price:g}／利確 {t.target_price:g}）。')
        else:
            messages.error(request, '追跡できるのは銘柄・株価・株数のある「買い」だけです。')
    else:
        if C.untrack(entry):
            messages.success(request, '追跡をやめました（日記の記録は残っています）。')
        else:
            messages.error(request, '決済済みの取引は追跡を外せません。')
    back = request.POST.get('next') or reverse('diary:index')
    return redirect(back)


def contra(request):
    """短期トレードのダッシュボード（2026-09-09）。

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

        # ⚠️ エントリー／決済の入力は売買日記に統一した（2026-09-09）。ここには置かない。
        #   買い＋「短期トレードとして追跡」→ contra.open_from_entry、売り → contra.close_from_entry
        if form_id == 'shot':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='contra')
            if C.add_shot(t, request.POST.get('image', '')):
                messages.success(request, f'{t.ticker} の購入時スクリーンショットを保存しました。')
            else:
                messages.error(request, '画像が貼り付けられていません（貼り付け欄をクリックして Ctrl+V）。')
            return redirect('diary:contra')

        if form_id == 'note':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='contra')
            if C.add_note(t, request.POST.get('text', ''), request.POST.get('kind', ''), image=request.POST.get('image', '')):
                messages.success(request, f'{t.ticker} にコメントを追記しました。')
            return redirect('diary:contra')

        if form_id == 'refresh':
            try:
                call_command('update_trade_bars')
                messages.success(request, '保有中の日足を更新しました。')
            except Exception as e:   # noqa: BLE001
                messages.error(request, f'更新に失敗: {e}')
            return redirect('diary:contra')

    be = C.breakeven(setting.default_stop_pct, setting.default_target_pct, setting.cost_pct)
    st = C.stats(setting)
    return render(request, 'diary/contra.html', {
        'setting': setting, 'be': be,
        # 円グラフ用（勝ち/負けの件数と、最低勝率＝コスト込みの分岐勝率）
        'donut': {'wins': st['wins'], 'losses': st['losses'], 'win_rate': st['win_rate'],
                  'min_rate': round(be['with_cost']), 'early': st['total']['early']['n']},
        # 振り返り一覧の切り替えパネル（テンプレートで同じ描画を3回書かないため）
        'reflect_panels': [('stop', st['reflect']['stop'], '損切り'), ('target', st['reflect']['target'], '利確'),
                           ('other', st['reflect']['other'], '裁量・期限')],
        'open_rows': C.open_rows(setting), 'stats': st,
        'after_rows': C.after_exit_rows('contra'),
        'moods': MOODS,   # 保有中のコメント追記フォーム用
        'exit_choices': Trade.EXIT, 'today': _date.today().isoformat(),
        'risk_budget': setting.capital * setting.risk_pct / 100,
    })


@require_POST
def review(request, pk):
    """振り返りを1件追記する（上書きしない・2026-09-22）。entry.review_* は最新の写し"""
    from .models import DiaryReview
    entry = get_object_or_404(DiaryEntry, pk=pk)
    result = request.POST.get('review_result', '')
    result = result if result in dict(DiaryEntry.RESULT_CHOICES) else ''
    note = request.POST.get('review_note', '').strip()
    if not note and not result:
        return redirect('diary:index')
    DiaryReview.objects.create(entry=entry, result=result, note=note)
    entry.review_result, entry.review_note, entry.reviewed_at = result, note, timezone.now()
    entry.save(update_fields=['review_result', 'review_note', 'reviewed_at'])
    return redirect('diary:index')


@require_POST
def review_delete(request, pk):
    """振り返り1件を削除。残りの最新を entry.review_* に写し直す"""
    from .models import DiaryReview
    rv = get_object_or_404(DiaryReview, pk=pk)
    entry = rv.entry
    rv.delete()
    last = entry.reviews.order_by('-created_at', '-id').first()
    entry.review_result = last.result if last else ''
    entry.review_note = last.note if last else ''
    entry.reviewed_at = last.created_at if last else None
    entry.save(update_fields=['review_result', 'review_note', 'reviewed_at'])
    return redirect('diary:index')


@require_POST
def delete(request, pk):
    entry = get_object_or_404(DiaryEntry, pk=pk)
    # 短期のエントリー記録を消したら、その取引（日足・決済の紐付け）も消す。
    # 決済側の記録だけ消した場合は取引を未決済に戻す
    t = entry.trade
    if t is not None:
        if t.entry_diary_id == entry.id:
            DiaryEntry.objects.filter(trade=t).exclude(id=entry.id).update(trade=None, strategy='')
            t.delete()
        elif t.exit_diary_id == entry.id:
            t.exit_date = t.exit_price = None
            t.exit_reason, t.exit_note, t.exit_diary = '', '', None
            t.save()
    entry.delete()
    return redirect('diary:index')


def practice(request):
    """練習（仮想トレード・2026-09-10）。先輩直伝の「買ったつもりで追う」練習法。

    短期トレードと同じレイアウト（勝率の円グラフ・保有中のレンジバー・振り返り・固定ルールバー）だが
    売買日記とは一切つながない。軍資金・銘柄・なぜ買ったかをこのページで直接入力し、決済もここで行う。
    データは Trade.strategy='practice' と ContraSetting.kind='practice' で完全に分離
    """
    from datetime import date as _date

    from django.contrib import messages
    from django.core.management import call_command

    from . import contra as C
    from .models import ContraSetting, PracticeMeta, Trade

    setting = ContraSetting.get('practice')

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
                messages.error(request, '設定の値を確認してください（軍資金>0・リスク0〜10%・幅>0）。')
            else:
                setting.capital, setting.risk_pct = int(cap), risk
                setting.default_stop_pct, setting.default_target_pct, setting.cost_pct = sp, tp, cost
                setting.save()
                messages.success(request, '練習の設定を保存しました。')
            return redirect('diary:practice')

        if form_id == 'open':
            stock = Stock.objects.filter(code=request.POST.get('stock_code', '').strip()).first()
            price, shares = _f('entry_price'), _f('shares')
            try:
                entry_date = datetime.strptime(request.POST.get('entry_date', ''), '%Y-%m-%d').date()
            except ValueError:
                entry_date = _date.today()
            # なぜ買ったかは「＋」で列挙した理由を1行1理由で持つ（旧 reason 1本も受ける）
            kinds = request.POST.getlist('reason_kinds')
            pairs = [(x.strip(), (kinds[i] if i < len(kinds) else 'info'))
                     for i, x in enumerate(request.POST.getlist('reasons')) if x.strip()]
            reasons = [x for x, _ in pairs]
            reason = '\n'.join(reasons) or request.POST.get('reason', '').strip()
            risk_scenario = request.POST.get('risk_scenario', '').strip()
            if stock is None or not price or price <= 0 or not shares or shares <= 0 or not reason or not risk_scenario:
                messages.error(request, '銘柄・価格・株数・なぜ買ったか・マイナスになる想定 をすべて入れてください。')
                return redirect('diary:practice')
            tags = ','.join(t for t in request.POST.getlist('tags') if t in TAGS)
            mood = request.POST.get('mood', '') if request.POST.get('mood', '') in MOODS else ''
            t = C.open_practice(setting, stock, price, int(shares), entry_date, reason, tags, mood, risk_scenario,
                                reasons=pairs or None, chart_image=request.POST.get('chart_image', ''))
            msg = f'{t.ticker} を {int(shares)}株 @{price:g} で買ったつもり。損切り {t.stop_price:g}／利確 {t.target_price:g}。'
            if t.over_risk:
                messages.warning(request, msg + ' ⚠️ 許容株数を超えています（裁量として記録）。')
            else:
                messages.success(request, msg)
            return redirect('diary:practice')

        if form_id == 'close':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='practice')
            price = _f('exit_price')
            try:
                exit_date = datetime.strptime(request.POST.get('exit_date', ''), '%Y-%m-%d').date()
            except ValueError:
                exit_date = _date.today()
            if not price or price <= 0:
                messages.error(request, '決済価格を入れてください。')
                return redirect('diary:practice')
            C.close_trade(t, price, exit_date, request.POST.get('exit_reason', ''), request.POST.get('exit_note', '').strip(),
                          request.POST.get('exit_expected', ''))
            messages.success(request, f'{t.ticker} を{t.get_exit_reason_display()}で決済（コスト込み {t.pnl_pct_net(setting.cost_pct):+.2f}%）。')
            return redirect('diary:practice')

        if form_id == 'shot':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='practice')
            if C.add_shot(t, request.POST.get('image', '')):
                messages.success(request, f'{t.ticker} の購入時スクリーンショットを保存しました。')
            else:
                messages.error(request, '画像が貼り付けられていません（貼り付け欄をクリックして Ctrl+V）。')
            return redirect('diary:practice')

        if form_id == 'note':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='practice')
            if C.add_note(t, request.POST.get('text', ''), request.POST.get('kind', ''), image=request.POST.get('image', '')):
                messages.success(request, f'{t.ticker} にコメントを追記しました。')
            return redirect('diary:practice')

        if form_id == 'delete':
            t = get_object_or_404(Trade, pk=request.POST.get('id'), strategy='practice')
            t.delete()
            messages.success(request, '練習の取引を削除しました。')
            return redirect('diary:practice')

        if form_id == 'refresh':
            try:
                call_command('update_trade_bars')
                messages.success(request, '日足を更新しました。')
            except Exception as e:   # noqa: BLE001
                messages.error(request, f'更新に失敗: {e}')
            return redirect('diary:practice')

    be = C.breakeven(setting.default_stop_pct, setting.default_target_pct, setting.cost_pct)
    st = C.stats(setting, strategy='practice')
    return render(request, 'diary/practice.html', {
        'setting': setting, 'be': be, 'stats': st,
        'donut': {'wins': st['wins'], 'losses': st['losses'], 'win_rate': st['win_rate'],
                  'min_rate': round(be['with_cost']), 'early': st['total']['early']['n']},
        'reflect_panels': [('stop', st['reflect']['stop'], '損切り'), ('target', st['reflect']['target'], '利確'),
                           ('other', st['reflect']['other'], '裁量・期限')],
        'open_rows': C.open_rows(setting, strategy='practice'),
        'after_rows': C.after_exit_rows('practice'),
        'exit_choices': Trade.EXIT, 'today': _date.today().isoformat(),
        'risk_budget': setting.capital * setting.risk_pct / 100,
        'tags': TAGS, 'moods': MOODS,
    })


def note_image(request, pk):
    """コメントに貼ったチャート画像を返す（data URL を復号）。ログイン必須（ミドルウェアが守る）。
    /media/ を使わないのは nginx が認証なしで配信するため（CLAUDE.md 参照）"""
    import base64

    from django.http import HttpResponse

    from .models import TradeNote
    n = get_object_or_404(TradeNote, pk=pk)
    if not n.image.startswith('data:image/'):
        raise Http404
    head, data = n.image.split(',', 1)
    mime = head[5:].split(';')[0]
    resp = HttpResponse(base64.b64decode(data), content_type=mime)
    resp['Cache-Control'] = 'private, max-age=86400'
    return resp
