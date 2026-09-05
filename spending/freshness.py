"""ソースごとの「いつまでデータがあるか」（鮮度）と、月ごとの「揃い具合」。

なぜ要るか（2026-09-06）: Zaim は API で毎晩自動更新されるが、楽天カード（e-navi）・
三菱UFJ（銀行 CSV）・Amazon は手動アップロードのまま。何もしないとレシート分だけが
新しくなり、カードや家賃の無い月が「支出が減った」ように見える。だから
  1. ソースごとの最終日を出して、古いものを画面で明示する（何をアップすればよいかも）
  2. 全ソースが揃っている月（full_through_ym）までを平常月・記録の質・サブスク判定の
     根拠にし、揃っていない月は根拠から外す（表示はする）
  3. ファイルが更新されれば取り込みで最終日が進み、注意書きは自動で消える
"""
from __future__ import annotations

from datetime import date, timedelta

from django.db.models import Max

from .models import AmazonOrderItem, ImportLog, Transaction

# 何日遅れたら「古い」と言うか。カード・銀行は月1回の手動なので 35 日、
# Zaim（レシート）は毎晩の自動なので 4 日止まれば API が壊れている疑い
# Amazon は「データをリクエスト」の仕様で最終注文が約2.5か月遅れる（実測）ので 100 日
STALE_DAYS = {'cash': 4, 'card': 35, 'bank': 35, 'amazon': 100}

SOURCES = [
    # key, 表示名, どこから来るか, 古いときに何をするか
    ('cash', '現金・レシート（Zaim）', 'Zaim API・毎晩自動', 'サーバーの fetch_zaim のログを確認'),
    ('card', '楽天カード', 'e-navi CSV／Zaim の連携行', 'e-navi の明細 CSV（当月分）か Zaim の CSV をアップロード'),
    ('bank', '三菱UFJ銀行', '銀行 CSV', '三菱UFJダイレクトの入出金明細 CSV をアップロード'),
    ('amazon', 'Amazon 品目', '注文履歴 CSV', 'Amazon「データをリクエスト」→ 注文履歴をアップロード'),
]


def _month_end(d: date) -> date:
    return (date(d.year + (d.month == 12), d.month % 12 + 1, 1) - timedelta(days=1))


def source_freshness(today: date | None = None) -> dict:
    """{'rows': [...], 'full_through': date|None, 'full_through_ym': 'YYYY-MM'|None,
        'stale': [rows], 'zaim_mode': 'api'|'manual'|None, 'last_import': ImportLog|None}"""
    today = today or date.today()
    last = {
        'cash': Transaction.objects.filter(source_kind='cash').aggregate(d=Max('date'))['d'],
        'card': Transaction.objects.filter(source_kind='card').aggregate(d=Max('date'))['d'],
        'bank': Transaction.objects.filter(source_kind='bank').aggregate(d=Max('date'))['d'],
        'amazon': AmazonOrderItem.objects.aggregate(d=Max('order_date'))['d'],
    }
    rows = []
    for key, label, origin, todo in SOURCES:
        d = last.get(key)
        behind = (today - d).days if d else None
        rows.append({
            'key': key, 'label': label, 'origin': origin, 'todo': todo,
            'last_date': d, 'last_ym': d.strftime('%Y-%m') if d else None,
            'days_behind': behind,
            'stale': (behind is None) or behind > STALE_DAYS[key],
            'missing': d is None,
        })
    # 全ソース（Amazon は品目の補足なので除く）が揃っている最終日。
    # その日が月末でなければ、その月は「揃っていない月」＝直前の月までが full
    core = [last[k] for k in ('cash', 'card', 'bank') if last.get(k)]
    full_through = min(core) if core else None
    full_ym = None
    if full_through:
        full_ym = (full_through.strftime('%Y-%m') if full_through == _month_end(full_through)
                   else (full_through.replace(day=1) - timedelta(days=1)).strftime('%Y-%m'))
    latest_zaim = None
    try:
        from . import services
        p = services.latest_zaim()
        latest_zaim = p.name if p else None
    except Exception:   # noqa: BLE001
        pass
    return {
        'rows': rows,
        'by_key': {r['key']: r for r in rows},
        'full_through': full_through,
        'full_through_ym': full_ym,
        'stale': [r for r in rows if r['stale'] and r['key'] != 'cash' or (r['key'] == 'cash' and r['stale'])],
        'zaim_mode': ('api' if latest_zaim and '.api.' in latest_zaim else ('manual' if latest_zaim else None)),
        'zaim_file': latest_zaim,
        'last_import': ImportLog.objects.first(),
        'today': today,
    }


def month_coverage(ym: str, fresh: dict | None = None) -> dict:
    """その月にソースがどこまで入っているか。テンプレートで注意書きに使う。

    各ソース: 'full'（月末まで）／'partial'（X日まで）／'none'（この月の行が無い）
    """
    fresh = fresh or source_freshness()
    y, m = int(ym[:4]), int(ym[5:7])
    end = _month_end(date(y, m, 1))
    rows = []
    for r in fresh['rows']:
        d = r['last_date']
        if d is None or d < date(y, m, 1):
            state = 'none'
        elif d >= end:
            state = 'full'
        else:
            state = 'partial'
        rows.append({**r, 'state': state, 'through_day': d.day if state == 'partial' else None})
    core = [r for r in rows if r['key'] in ('cash', 'card', 'bank')]
    return {
        'ym': ym,
        'rows': rows,
        'complete': all(r['state'] == 'full' for r in core),
        'incomplete': [r for r in core if r['state'] != 'full'],
        'is_current': ym == fresh['today'].strftime('%Y-%m'),
    }
