"""kabutan — 日次スクリーニング結果の表示

サーバー側で計算して並べるだけ（JSなし方針）。
- 最新判定日の BUY_CANDIDATE を根拠（成立条件・損切り・利確目安）付きで表示
- 「あと1条件」の銘柄（unmet が1つだけの WAIT）を次点として表示
- 過去14日ぶんの BUY 候補履歴（フォワードテストの目視用）
"""
from datetime import date, timedelta

from django.db.models import Max
from django.shortcuts import render

from .logic import CONDITION_LABELS, RULE_VERSION
from .models import Jpx400Member, ScreenResult

# 判定日がこの日数より古ければ画面に赤い警告を出す（バッチ停止に気付くための保険。
# 「出力が止まっているのに気付かない」防止）。
# ⚠️ 7日未満にしないこと: 日本市場の最長休場ギャップは6日（シルバーウィーク
# 9/18金→9/24木、年末年始12/30→1/4等）で、実際に2026年SWで4日設定が誤発報した
STALE_DAYS = 7


def _labels(codes):
    return [CONDITION_LABELS.get(c, c) for c in codes]


def screen_context():
    """最新判定日の候補一式（kabutan の詳細ページと watch の候補パネルで共用）"""
    latest = (ScreenResult.objects.filter(rule_version=RULE_VERSION)
              .aggregate(m=Max('date'))['m'])

    buys, near, n_judged = [], [], 0
    if latest:
        day_qs = (ScreenResult.objects
                  .filter(date=latest, rule_version=RULE_VERSION)
                  .select_related('stock'))
        n_judged = day_qs.count()
        for r in day_qs:
            if r.judgment == 'BUY':
                buys.append(r)
            elif r.judgment == 'WAIT' and len(r.unmet) == 1:
                near.append(r)
        for r in buys + near:
            r.met_labels = _labels(r.met)
            r.unmet_labels = _labels(r.unmet)
            if r.close and r.stop_price:
                r.stop_pct = (r.stop_price / r.close - 1) * 100
            if r.close and r.tp_price:
                r.tp_pct = (r.tp_price / r.close - 1) * 100
        near.sort(key=lambda r: r.stock.display_code)

    # 「候補ゼロ」の理由を画面に明示する（2026-09-23 ユーザー要望:
    # 正常なリスクオフなのか不具合なのかを、聞かなくても画面で区別できること）。
    # 地合いNG = 判定した全銘柄の unmet に 'mkt' が入っている状態。
    # 連続日数は保存済みの判定日を新しい順に遡って数える
    mkt_ng, mkt_ng_streak = False, 0
    if latest:
        days = list(ScreenResult.objects.filter(rule_version=RULE_VERSION)
                    .values_list('date', flat=True).distinct().order_by('-date')[:60])
        for d in days:
            unmets = ScreenResult.objects.filter(
                date=d, rule_version=RULE_VERSION).values_list('unmet', flat=True)
            if unmets and all('mkt' in u for u in unmets):
                mkt_ng_streak += 1
            else:
                break
        mkt_ng = mkt_ng_streak > 0

    stale_days = (date.today() - latest).days if latest else None
    return {
        'latest': latest,
        'rule_version': RULE_VERSION,
        'buys': buys,
        'near': near,
        'n_judged': n_judged,
        'stale': bool(latest and stale_days > STALE_DAYS),
        'stale_days': stale_days,
        'mkt_ng': mkt_ng,
        'mkt_ng_streak': mkt_ng_streak,
    }


def index(request):
    ctx = screen_context()
    latest = ctx['latest']

    history = []
    if latest:
        history = list(
            ScreenResult.objects
            .filter(rule_version=RULE_VERSION, judgment='BUY',
                    date__gte=latest - timedelta(days=14), date__lt=latest)
            .select_related('stock').order_by('-date', 'stock_id'))

    ctx.update({
        'history': history,
        'n_jpx400': Jpx400Member.objects.count(),
    })
    return render(request, 'kabutan/index.html', ctx)
