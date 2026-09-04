"""支出分析（spending）の画面

目的は入金力の向上で、可視化はその手段。だから画面の出口は必ず
「節約候補 → 決定（SavingsPlan）→ 月あたり追加入金力」につながる。

⚠️ CSV のアップロードは書き込みエンドポイントなので、サイト全体の合言葉認証
（website.middleware.SitePasswordMiddleware）が有効であることが前提。
"""
from datetime import date

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

from . import monthly, services, summary
from .models import Budget, ImportLog, SavingsPlan, Transaction


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
                    skipped.append(f'{f.name}（Zaimでもe-naviでもない形式）')
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


def transactions(request):
    """明細一覧（絞り込みつき）。分類を直すのはここ。"""
    qs = Transaction.objects.all()
    q = request.GET.get('q', '').strip()
    ym = request.GET.get('ym', '').strip()
    src = request.GET.get('source', '').strip()
    only_excluded = request.GET.get('excluded') == '1'

    if q:
        qs = qs.filter(merchant__icontains=q) | qs.filter(label__icontains=q) | qs.filter(shop__icontains=q)
    if ym:
        qs = qs.filter(ym=ym)
    if src:
        qs = qs.filter(source_kind=src)
    qs = qs.filter(in_total=False) if only_excluded else qs.filter(in_total=True)

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
        'rows': qs.select_related()[:300],
        'count': qs.count(),
        'months': months,
        'q': q, 'ym': ym, 'source': src, 'only_excluded': only_excluded,
        'source_choices': Transaction.SOURCE_KIND,
        'necessity_choices': ['必須', '準必須', '裁量', '要確認'],
    }
    return render(request, 'spending/transactions.html', context)
