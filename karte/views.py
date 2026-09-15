import json
from collections import OrderedDict

from django.db.models import Count, Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from japan_kabu.indicators import INDICATOR_DEFS, indicator_for_stock, indicators_for_stocks
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
    'price', 'indicators', 'invest', 'competitive', 'kpi', 'targets',
]
# 'indicators' は 2026-09-16 に旧「銘柄別指標」ページを吸収して追加。既存カルテの section_order には
# 無いキーだが resolve_section_order が既定順で補うので、手直し不要（price の次に出る）


def resolve_section_order(saved):
    """保存済みの並び順を正規化する。未知キー除去 + 欠落キーを既定順で補完。"""
    saved = saved or []
    order = [k for k in saved if k in DEFAULT_SECTION_ORDER]
    order += [k for k in DEFAULT_SECTION_ORDER if k not in order]
    return order


def index(request):
    """カルテ一覧 + 新規作成 + 押し目一覧（高値からの下落率）"""
    # 並び: 手で並べたもの（sort_order 1,2,3…）が先、未設定（0）は更新日の新しい順で後ろ
    kartes = sorted(StockKarte.objects.select_related('stock').all(),
                    key=lambda k: (k.sort_order == 0, k.sort_order, -k.updated_at.timestamp()))

    # 1年ドローダウン（比較表の列）。旧「押し目一覧」は 2026-09-16 に削除（比較表と重複・ユーザー指示）
    stats = bulk_price_stats([k.stock for k in kartes])
    # 指標の比較表（2026-09-16・旧「銘柄別指標」を吸収）。カルテ銘柄だけ計算するので軽い
    inds = indicators_for_stocks([k.stock for k in kartes])
    rows = []
    for k in kartes:
        filled = sum(1 for f in FIELDS if getattr(k, f).strip())
        p = stats.get(k.stock_id)
        ind = inds.get(k.stock_id)
        rows.append({
            'k': k,
            'filled': filled,
            'total': len(FIELDS),
            'pct': round(filled / len(FIELDS) * 100),
            'price': p,
            'ind': ind['ind'] if ind else None,
            'close': ind['close'] if ind else k.stock.close,
            # 米国株は円換算を併記（最新ドル円。indicator に fx が無ければ出さない）
            'close_jpy': (ind['close'] * ind['fx']['rate']) if (ind and ind.get('fx') and ind['close']) else None,
            'dd': p['1y']['drawdown'] if p and p.get('1y') else None,
        })
    # 比較表は押し目が深い順（None は末尾）。ユーザーが決めた5列: PER/PBR/ROE/配当利回り/1年DD
    compare = sorted(rows, key=lambda r: (r['dd'] is None, r['dd'] if r['dd'] is not None else 0))

    context = {
        'rows': rows,
        'compare': compare,
        'total_fields': len(FIELDS),
        # ランキング等から「カルテが無い銘柄」を開いたとき、検索窓にコードを入れて候補を出す
        'prefill': request.GET.get('q', '').strip()[:20],
    }
    return render(request, 'karte/index.html', context)


def stock_options(request):
    """新規カルテ作成用の銘柄検索リスト（JSON・ETagで条件付きGET）

    銘柄マスタの件数や最終更新時刻を ETag にして、変わらなければ 304 で返す。
    max-age を長く取ると US 銘柄の初回取り込み直後などに古いJSONが1時間貼り付き、
    検索候補に反映されない事故が起きるため、Cache-Control は no-cache で
    毎回サーバに再確認させる（1.7MBの本文は無変更なら 304 で転送されない）。
    """
    from django.http import HttpResponseNotModified, JsonResponse
    agg = Stock.objects.aggregate(n=Count('code'), mx=Max('updated_at'))
    mx = agg['mx'].strftime('%Y%m%d%H%M%S') if agg['mx'] else '0'
    etag = f'W/"{agg["n"] or 0}-{mx}"'
    if request.META.get('HTTP_IF_NONE_MATCH') == etag:
        resp = HttpResponseNotModified()
        resp['ETag'] = etag
        resp['Cache-Control'] = 'no-cache'
        return resp
    options = [
        {'code': s.code, 'ticker': s.display_code, 'name': s.name, 'country': s.country}
        for s in Stock.objects.all().order_by('country', 'code')
    ]
    resp = JsonResponse({'stocks': options})
    resp['ETag'] = etag
    resp['Cache-Control'] = 'no-cache'
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

    # ワラント（新株予約権）や優先株を普通株と取り違えて登録した場合の案内（2026-09-09）。
    # 実際に Rigetti の RGTIW（warrants）でカルテを作り、株価が2日分しか無く
    # 「取得ボタンを押してもグラフが出ない」となった。普通株のコードを添える
    warrant_common = None
    name_l = (stock.name or '').lower()
    if stock.country == 'US' and ('warrant' in name_l or 'unit' in name_l or 'right' in name_l) \
            and len(stock.display_code) > 1:
        cand = Stock.objects.filter(country='US', display_code=stock.display_code[:-1]).first()
        if cand:
            warrant_common = {'stock': cand,
                              'has_karte': StockKarte.objects.filter(stock=cand).exists()}

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
    if price_chart:
        # アンダーウォーターチャート用: その日までの最高値（取得済み履歴内）からの
        # 下落率%の系列。0%=高値更新中。「この銘柄の普通の痛みの深さ」を較正する
        # ためのグラフで、1点の下落率数値（上の表）の時系列版
        peak = None
        dd = []
        for _, c in price_rows:
            peak = c if peak is None or c > peak else peak
            dd.append(round((c / peak - 1) * 100, 2))
        price_chart['drawdown'] = dd

    context = {
        'stock': stock,
        'warrant_common': warrant_common,
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
        # 指標（旧「銘柄別指標」ページを吸収・2026-09-16）。この1銘柄分だけ計算する
        'indicator': indicator_for_stock(stock),
        'indicator_defs': [{'key': k, 'label': label, 'unit': unit, 'min': mn, 'max': mx}
                           for k, label, unit, mn, mx in INDICATOR_DEFS],
    }
    return render(request, 'karte/detail.html', context)


@require_POST
def fetch_financials(request, code):
    """カルテ詳細の「決算を取得する」ボタン（米国株のみ・2026-09-16）。

    米国株は登録した銘柄だけ update_us_financials（yfinance）で決算を取る設計なので、
    登録直後は指標が空。株価取得ボタンと同じく、その場で1銘柄だけ同期実行する（数秒）。
    日本株は J-Quants の夜バッチ（update_marketcap）が全銘柄分を取り込むのでボタンは出さない。
    """
    from io import StringIO

    from django.contrib import messages
    from django.core.management import call_command

    stock = get_object_or_404(Stock, display_code=code)
    if stock.country != 'US':
        messages.error(request, '日本株の決算は夜バッチ（update_marketcap）が取り込みます。')
        return redirect('karte:detail', code=code)
    out, err = StringIO(), StringIO()
    try:
        call_command('update_us_financials', ticker=stock.display_code, stdout=out, stderr=err)
    except Exception as e:   # noqa: BLE001
        messages.error(request, f'決算の取得に失敗しました: {e}')
        return redirect('karte:detail', code=code)
    if indicator_for_stock(stock):
        messages.success(request, '決算を取得しました。' + out.getvalue().strip()[-160:])
    else:
        messages.error(request, '決算を取得できませんでした（yfinance に財務データが無い可能性）。'
                                + (err.getvalue().strip() or out.getvalue().strip())[-200:])
    return redirect('karte:detail', code=code)


@require_POST
def fetch_prices(request, code):
    """カルテ詳細の「株価を取得」ボタン → サーバーで update_daily_prices --code をその場で実行する。

    ユーザー要望（2026-09-09）: 新しく登録した銘柄（例: Rigetti）は翌朝のバッチまで株価が空で
    「未取得です。コマンドを実行してください」と出る。SSH せずにブラウザから埋めたい。
    - 1銘柄・差分同期なので数秒（yfinance 1コール）。同期実行で結果をメッセージに出す
    - JP 銘柄は J-Quants 無料プランで直近が取れないため、コマンド側で yfinance に代替する
    - 本番は gunicorn のタイムアウト（既定30秒）内に収まる。全銘柄一括はここからは回さない
    """
    from io import StringIO

    from django.contrib import messages
    from django.core.management import call_command

    stock = get_object_or_404(Stock, display_code=code)
    out, err = StringIO(), StringIO()
    try:
        call_command('update_daily_prices', code=code, years=3, stdout=out, stderr=err)
    except Exception as e:   # noqa: BLE001
        messages.error(request, f'株価の取得に失敗しました: {e}')
        return redirect('karte:detail', code=code)
    n = DailyPrice.objects.filter(stock=stock).count()
    latest = DailyPrice.objects.filter(stock=stock).order_by('-date').values_list('date', flat=True).first()
    summary = ' '.join(line.strip() for line in out.getvalue().splitlines() if line.strip())[-160:]
    if n:
        messages.success(request, f'株価を取得しました（{n:,}日分・最新 {latest}）。{summary}')
    else:
        messages.error(request, '株価を取得できませんでした（ティッカーが yfinance に無い可能性）。'
                                + (err.getvalue().strip()[-200:] or summary))
    return redirect('karte:detail', code=code)


@require_POST
def reorder_cards(request):
    """一覧のカードの並び順を保存（JS からドラッグ後に呼ぶ）。ボディ: {"order": ["NVDA", "6758", ...]}
    渡された順に 1,2,3… を振る。渡されなかったカルテは末尾（既存の値を保つ）"""
    try:
        payload = json.loads(request.body or '{}')
        codes = [str(c) for c in payload.get('order', [])]
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)
    kartes = {k.stock.display_code: k for k in StockKarte.objects.select_related('stock')}
    # 渡された順 → 渡されなかったもの（現在の並び）の順で 1,2,3… を全件に振る
    # （未設定 0 が残ると「0 が先頭」になり並びが崩れる。実際に起きた）
    rest = [k for k in sorted(kartes.values(), key=lambda k: (k.sort_order == 0, k.sort_order, -k.updated_at.timestamp()))
            if k.stock.display_code not in codes]
    ordered = [kartes[c] for c in codes if c in kartes] + rest
    n = 0
    for i, k in enumerate(ordered, 1):
        if k.sort_order != i:
            k.sort_order = i
            k.save(update_fields=['sort_order'])   # updated_at は動かさない（記入の更新日ではない）
            n += 1
    return JsonResponse({'ok': True, 'saved': n})


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
