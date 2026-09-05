"""支出分析（spending）の画面

目的は入金力の向上で、可視化はその手段。だから画面の出口は必ず
「節約候補 → 決定（SavingsPlan）→ 月あたり追加入金力」につながる。

⚠️ CSV のアップロードは書き込みエンドポイントなので、サイト全体の合言葉認証
（website.middleware.SitePasswordMiddleware）が有効であることが前提。
"""
from datetime import date

from django.contrib import messages
from django.db.models import Case, CharField, F, Q, Sum, When
from django.shortcuts import redirect, render
from django.urls import reverse

from . import monthly, services, summary
from .models import Budget, ImportLog, MonthlyIncome, SavingsPlan, Transaction


def index(request):
    """ダッシュボード。POST は アップロード / 取り込み / 節約の決定 を受ける。"""
    if request.method == 'POST':
        form_id = request.POST.get('form_id')

        if form_id == 'upload':
            files = request.FILES.getlist('csv_files')
            if not files:
                messages.error(request, 'ファイルが選択されていません。')
                return redirect('spending:index')
            saved, skipped = [], []
            for f in files:
                if f.size > services.MAX_UPLOAD_SIZE:
                    skipped.append(f'{f.name}（サイズ超過）')
                    continue
                kind = services.detect_csv_kind(services.read_head(f))
                if not kind:
                    skipped.append(f'{f.name}（Zaim・e-navi・Amazon注文履歴のいずれでもない形式）')
                    continue
                services.save_upload(f, kind)
                saved.append(f'{f.name}→{kind}')
            if saved:
                log = services.import_from_files()
                if log.ok:
                    messages.success(request, f'{len(saved)}件を取り込みました。{log.message}')
                else:
                    messages.error(request, log.message)
            if skipped:
                messages.error(request, '取り込めなかったファイル: ' + ' / '.join(skipped))
            return redirect('spending:index')

        if form_id == 'reimport':
            log = services.import_from_files()
            (messages.success if log.ok else messages.error)(request, log.message)
            return redirect('spending:index')

        if form_id == 'plan':
            # 節約候補に対する決定。これの合計が入金力になる
            merchant = request.POST.get('merchant', '').strip()
            status = request.POST.get('status', 'todo')
            if merchant and status in dict(SavingsPlan.STATUS):
                try:
                    annual = int(float(request.POST.get('annual_effect') or 0))
                except ValueError:
                    annual = 0
                plan, _ = SavingsPlan.objects.update_or_create(
                    merchant=merchant,
                    defaults={
                        'action': request.POST.get('action') or '解約',
                        'annual_effect': annual,
                        'status': status,
                        'decided_at': date.today() if status in ('doing', 'done') else None,
                    })
                messages.success(request, f'{plan.merchant} を「{plan.get_status_display()}」にしました。')
            return redirect('spending:index')

        if form_id == 'income':
            # 収入の手入力。Zaim に記録が無い月を埋めるための受け皿で、
            # 同じ月に Zaim の取込があっても手入力を優先する（MonthlyIncome.effective）
            ym = request.POST.get('ym', '').strip()
            raw = request.POST.get('amount', '').strip().replace(',', '')
            if not ym:
                messages.error(request, '年月を入力してください。')
            elif raw == '':
                MonthlyIncome.objects.filter(ym=ym, source='manual').delete()
                messages.success(request, f'{ym} の手入力を削除しました。')
            else:
                try:
                    amount = int(float(raw))
                except ValueError:
                    messages.error(request, '金額は数字で入力してください。')
                    return redirect('spending:index')
                MonthlyIncome.objects.update_or_create(
                    ym=ym, source='manual',
                    defaults={'amount': amount, 'note': request.POST.get('note', '')[:100]})
                messages.success(request, f'{ym} の収入を {amount:,}円 で登録しました。')
            return redirect('spending:index')

        if form_id == 'clear':
            services.clear_data()
            messages.success(request, '取り込み済みデータとCSVを削除しました。')
            return redirect('spending:index')

    data = summary.build()
    context = {
        **data,
        'plans': SavingsPlan.objects.all(),
        'last_import': ImportLog.objects.first(),
        'zaim_file': (services.latest_zaim().name if services.latest_zaim() else None),
        'enavi_count': len(list(services.ENAVI_DIR.glob('*.csv'))) if services.ENAVI_DIR.exists() else 0,
    }
    return render(request, 'spending/index.html', context)


def month(request):
    """月単位の分析。?ym=2026-08 で対象月を切り替える（省略時は最新の完了月）。

    トップが「全期間の傾向」なのに対し、ここは「その月に何が起きたか」を
    前月・平常月（直近12か月の中央値）との差で読む。
    POST はカテゴリ別の月次予算（上限）の設定を受ける。
    """
    if request.method == 'POST':
        form_id = request.POST.get('form_id')
        ym = request.POST.get('ym', '')
        back = f"{reverse('spending:month')}?ym={ym}" if ym else reverse('spending:month')

        if form_id == 'budget':
            category = request.POST.get('category', '').strip()
            raw = request.POST.get('monthly_limit', '').strip()
            if not category:
                messages.error(request, 'カテゴリを選んでください。')
                return redirect(back)
            if raw == '':
                # 空で保存＝予算の削除
                Budget.objects.filter(category=category).delete()
                messages.success(request, f'{category} の予算を削除しました。')
                return redirect(back)
            try:
                limit = max(0, int(float(raw)))
            except ValueError:
                messages.error(request, '予算は数値で入力してください。')
                return redirect(back)
            Budget.objects.update_or_create(
                category=category,
                defaults={'monthly_limit': limit, 'note': request.POST.get('note', '').strip()})
            messages.success(request, f'{category} の予算を月 ¥{limit:,} に設定しました。')
            return redirect(back)

        if form_id == 'budget_seed':
            # 平常月（中央値）を初期値として一括で入れる。まず基準を作るための機能
            data = monthly.build(ym or None)
            created = 0
            for c in data.get('cat_rows', []):
                base = c.get('baseline') or 0
                if base > 0 and not Budget.objects.filter(category=c['name']).exists():
                    Budget.objects.create(category=c['name'], monthly_limit=base,
                                          note='平常月から自動設定')
                    created += 1
            messages.success(request, f'{created}件の予算を平常月の水準で設定しました。必要に応じて調整してください。')
            return redirect(back)

    return render(request, 'spending/month.html', monthly.build(request.GET.get('ym')))


# 実効カテゴリ = 画面で直した分類（manual_category）があればそれ、無ければ自動分類。
# ⚠️ 画面の表示（manual_category|default:category）と同じ規則にすること。ずれると
# 「カテゴリで絞ったのに、その分類で表示されている行が出てこない」ことになる。
_EFFECTIVE_CATEGORY = Case(
    When(manual_category='', then=F('category')),
    default=F('manual_category'),
    output_field=CharField(),
)


# 分類の出どころ。e-navi（カード明細）には**カテゴリ列が無い**ので、
# カード行の分類は「突合できた Zaim 行の分類」か「加盟店ルールの推定」のどちらか。
# 推定は当たっていないことがあるのに、Zaim 由来の分類と同じ見た目で並んでいると
# どれを疑えばいいのか分からない（実際「遊びに誤りが多い」と指摘を受けた）。
CATEGORY_SOURCE_FILTERS = {
    'zaim': Q(category_source='zaim', manual_category=''),
    'rule': Q(category_source='rule', manual_category=''),
    # 品目ルール（LabelRule）で Zaim の誤学習を取り込み時に打ち消した行
    'fix': Q(category_source='fix', manual_category=''),
    'none': Q(category_source='none', manual_category=''),
    'manual': ~Q(manual_category=''),
}
CATEGORY_SOURCE_LABELS = [
    ('zaim', 'Zaim由来'), ('rule', '自動推定'), ('fix', '品目ルールで訂正'),
    ('none', '手がかりなし'), ('manual', '手動で修正済み'),
]


def _category_choices() -> list[str]:
    """絞り込み用のカテゴリ候補。

    Budget の登録カテゴリではなく**台帳に実在するカテゴリ**から作る。
    予算を付けていない分類（＝見直したい分類）が候補から漏れないようにするため。
    """
    cats = (Transaction.objects.annotate(cat=_EFFECTIVE_CATEGORY)
            .values_list('cat', flat=True).distinct())
    return sorted({c for c in cats if c})


# レシート付随行（外税・割引・袋代など）の扱い。既定は商品行だけを見せる。
# ⚠️ 「隠す」であって「除外する」ではない。合計は常に全行で計算する
#（外税55,913円は実際に払った消費税で、落とすと支出が実態より小さくなる）
ROW_VIEW_CHOICES = [('item', '商品行のみ'), ('all', 'レシートの付随行も含む')]


def transactions(request):
    """明細一覧（絞り込みつき）。分類を直すのはここ。"""
    qs = Transaction.objects.annotate(cat=_EFFECTIVE_CATEGORY)
    q = request.GET.get('q', '').strip()
    ym = request.GET.get('ym', '').strip()
    src = request.GET.get('source', '').strip()
    cat = request.GET.get('cat', '').strip()
    csrc = request.GET.get('csrc', '').strip()
    rowview = request.GET.get('rows', 'item').strip()
    only_excluded = request.GET.get('excluded') == '1'

    if q:
        qs = qs.filter(Q(merchant__icontains=q) | Q(label__icontains=q) | Q(shop__icontains=q))
    if ym:
        qs = qs.filter(ym=ym)
    if src:
        qs = qs.filter(source_kind=src)
    if cat:
        # 分類が付かなかった行は「未分類」という名前のカテゴリになるので、
        # 空欄用の選択肢は要らない（実測でも空欄は0件）
        qs = qs.filter(cat=cat)
    if csrc in CATEGORY_SOURCE_FILTERS:
        qs = qs.filter(CATEGORY_SOURCE_FILTERS[csrc])
    qs = qs.filter(in_total=False) if only_excluded else qs.filter(in_total=True)
    # 合計は付随行も含めた全部で出す（画面から隠すだけ）
    total_all = qs.aggregate(s=Sum('amount'))['s'] or 0
    meta = qs.exclude(label_kind__in=['item', ''])
    meta_n, meta_sum = meta.count(), (meta.aggregate(s=Sum('amount'))['s'] or 0)
    if rowview != 'all':
        qs = qs.filter(Q(label_kind='item') | Q(label_kind=''))

    if request.method == 'POST' and request.POST.get('form_id') == 'bulk':
        # まとめて分類を直す。Zaim のレシート取込がスーパーの食料品を「遊び/風俗」に
        # 分類していた件が実際にあり、1行ずつ直すのは現実的でないため用意した。
        # ⚠️ 直すのは manual_* だけ。元の category は触らない（再取込で復元できる
        # ようにしておくため。何が自動分類で何を手で直したかも追えなくなる）
        ids = request.POST.getlist('ids')
        new_cat = request.POST.get('bulk_category', '').strip()
        new_nec = request.POST.get('bulk_necessity', '').strip()
        if not ids:
            messages.error(request, '行が選択されていません。')
        elif not new_cat and not new_nec:
            messages.error(request, '変更後のカテゴリか必要度を指定してください。')
        else:
            fields = {}
            if new_cat:
                fields['manual_category'] = new_cat
            if new_nec:
                fields['manual_necessity'] = new_nec
            n = Transaction.objects.filter(id__in=ids).update(**fields)
            what = ' / '.join(filter(None, [new_cat, new_nec]))
            messages.success(request, f'{n}件を「{what}」に変更しました。')
        return redirect(request.get_full_path())

    if request.method == 'POST' and request.POST.get('form_id') == 'edit':
        t = Transaction.objects.filter(pk=request.POST.get('id')).first()
        if t:
            t.manual_category = request.POST.get('category', '').strip()
            t.manual_necessity = request.POST.get('necessity', '').strip()
            ov = request.POST.get('exclude_override', '')
            t.exclude_override = None if ov == '' else (ov == '1')
            t.save(update_fields=['manual_category', 'manual_necessity', 'exclude_override'])
            messages.success(request, f'{t.label or t.merchant} を更新しました。')
        return redirect(request.get_full_path())

    months = list(Transaction.objects.values_list('ym', flat=True).distinct().order_by('-ym'))
    context = {
        # Amazon 行にぶら下がる品目。1請求に複数商品があるので prefetch で1回にまとめる
        'rows': qs.prefetch_related('amazon_items')[:300],
        'count': qs.count(),
        # 絞り込んだ結果がいくらだったかは、カテゴリ単位で見直すときの主役になる数字
        'total': total_all,
        'shown_total': qs.aggregate(s=Sum('amount'))['s'] or 0,
        'meta_n': meta_n, 'meta_sum': meta_sum,
        'rowview': rowview, 'rowview_choices': ROW_VIEW_CHOICES,
        'months': months,
        'q': q, 'ym': ym, 'source': src, 'cat': cat, 'csrc': csrc, 'only_excluded': only_excluded,
        'source_choices': Transaction.SOURCE_KIND,
        'category_choices': _category_choices(),
        'category_source_choices': CATEGORY_SOURCE_LABELS,
        'necessity_choices': ['必須', '準必須', '裁量', '要確認'],
    }
    return render(request, 'spending/transactions.html', context)
