"""カレンダー（1か月表示・予定の追加／編集／削除）"""
import calendar as pycal
from datetime import date

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from .models import CalendarEvent

WEEKDAYS = ['月', '火', '水', '木', '金', '土', '日']   # 月曜始まり（取引日が左に並ぶ）


def _parse_ym(s):
    try:
        y, m = (int(x) for x in (s or '').split('-'))
        return date(y, m, 1)
    except (ValueError, TypeError):
        t = date.today()
        return date(t.year, t.month, 1)


def _shift(d, months):
    y, m = divmod(d.month - 1 + months, 12)
    return date(d.year + y, m + 1, 1)


def _save(request, ev=None):
    """フォームの内容で予定を作る／直す。日付とタイトルは必須"""
    try:
        d = date.fromisoformat(request.POST.get('date', ''))
    except ValueError:
        messages.error(request, '日付を入れてください。')
        return None
    title = request.POST.get('title', '').strip()
    if not title:
        messages.error(request, 'タイトルを入れてください（例: NVDA 決算、FOMC）。')
        return None
    ev = ev or CalendarEvent()
    ev.date = d
    ev.title = title[:100]
    cat = request.POST.get('category', 'other')
    ev.category = cat if cat in dict(CalendarEvent.CATEGORIES) else 'other'
    ev.ticker = request.POST.get('ticker', '').strip().upper()[:20]
    ev.time_note = request.POST.get('time_note', '').strip()[:20]
    ev.memo = request.POST.get('memo', '').strip()
    ev.important = bool(request.POST.get('important'))
    ev.save()
    return ev


def index(request):
    month = _parse_ym(request.GET.get('ym'))
    back = f"{request.path}?ym={month:%Y-%m}"

    if request.method == 'POST':
        form_id = request.POST.get('form_id')
        if form_id == 'add':
            ev = _save(request)
            if ev:
                messages.success(request, f'{ev.date.month}/{ev.date.day} に「{ev.title}」を追加しました。')
                back = f"{request.path}?ym={ev.date:%Y-%m}"
        elif form_id == 'edit':
            ev = _save(request, get_object_or_404(CalendarEvent, pk=request.POST.get('id')))
            if ev:
                messages.success(request, f'「{ev.title}」を更新しました。')
                back = f"{request.path}?ym={ev.date:%Y-%m}"
        elif form_id == 'delete':
            ev = get_object_or_404(CalendarEvent, pk=request.POST.get('id'))
            messages.success(request, f'「{ev.title}」を削除しました。')
            ev.delete()
        return redirect(back)

    # 月曜始まりの週ごとのマス（前月・翌月の日も薄く出す）
    weeks_raw = pycal.Calendar(firstweekday=0).monthdatescalendar(month.year, month.month)
    start, end = weeks_raw[0][0], weeks_raw[-1][-1]
    by_day = {}
    for ev in CalendarEvent.objects.filter(date__range=(start, end)):
        by_day.setdefault(ev.date, []).append(ev)
    today = date.today()
    weeks = [[{'date': d, 'in_month': d.month == month.month, 'today': d == today,
               'weekend': d.weekday() >= 5, 'events': by_day.get(d, [])} for d in wk] for wk in weeks_raw]
    month_events = [ev for d in sorted(by_day) if d.month == month.month for ev in by_day[d]]
    upcoming = list(CalendarEvent.objects.filter(date__gte=today).order_by('date', '-important')[:8])
    return render(request, 'market_calendar/index.html', {
        'month': month, 'weeks': weeks, 'weekdays': WEEKDAYS,
        'prev_ym': f'{_shift(month, -1):%Y-%m}', 'next_ym': f'{_shift(month, 1):%Y-%m}',
        'this_ym': f'{today:%Y-%m}', 'is_this_month': (month.year, month.month) == (today.year, today.month),
        'categories': CalendarEvent.CATEGORIES, 'month_events': month_events, 'upcoming': upcoming,
        'today': today,
    })
