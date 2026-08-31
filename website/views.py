import time

from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from .middleware import SESSION_KEY, check_password

# 総当たり対策: 失敗が続いたら待たせる
MAX_ATTEMPTS = 5
LOCK_SECONDS = 60

# ナビゲーション・トップページ共通の機能一覧（実装が決まり次第、名称と説明を差し替える）
FEATURES = {
    1: {'name': '時価総額ランキング', 'description': '日本株の時価総額TOPを横棒グラフで表示', 'url': 'japan_kabu:index'},
    2: {'name': '出来高急増ランキング', 'description': '出来高の統計的異常度（対数z-score）ランキング', 'url': 'japan_kabu:volume'},
    3: {'name': '銘柄カルテ', 'description': 'IR資料を読みながら共通雛形に記入する詳細分析', 'url': 'karte:index'},
    4: {'name': '売買日記', 'description': '売買判断の記録と振り返り。その後の値動きを自動表示', 'url': 'diary:index'},
    5: {'name': '銘柄別指標', 'description': 'PER/PBR/ROE/ROA等を棒グラフで表示（現在はソニーグループのみ）',
        'url': 'japan_kabu:stock_detail', 'url_args': ['6758']},
    6: {'name': 'セクター別インパルス', 'description': '独自セクターの日次騰落を時系列で並べモメンタムを見る', 'url': 'japan_kabu:impulse'},
    7: {'name': 'セクター別ドローダウン', 'description': '高値からの下落率で「下がりきったセクター」を探す（逆張りの入口）', 'url': 'japan_kabu:drawdown'},
    8: {'name': 'マクロ指標', 'description': '日米のCPI・失業率の長期時系列と「基準値」の読み方（学習用）', 'url': 'japan_kabu:macro'},
    9: {'name': 'ポートフォリオ', 'description': '保有資産・現金の総合ダッシュボード（株・投信・金銀を円換算で自動評価）', 'url': 'portfolio:index'},
    10: {'name': '下落上等（爆下げプログラム）', 'description': '毎日読む合言葉と下落メーター。暴落の日に慌てないための反復訓練', 'url': 'portfolio:drill'},
}


def login(request):
    """合言葉を入力してサイト全体を開く"""
    # 既に認証済みならトップへ
    if request.session.get(SESSION_KEY):
        return redirect('website:index')

    error = ''
    locked_for = 0
    locked_until = request.session.get('login_locked_until', 0)
    now = time.time()
    if locked_until > now:
        locked_for = int(locked_until - now)

    if request.method == 'POST' and not locked_for:
        if check_password(request.POST.get('password', '')):
            request.session[SESSION_KEY] = True
            request.session.pop('login_attempts', None)
            request.session.pop('login_locked_until', None)
            # 開こうとしていたページへ戻す（外部サイトへ飛ばさないよう内部パスに限定）
            nxt = request.POST.get('next') or request.GET.get('next') or ''
            if nxt.startswith('/') and not nxt.startswith('//'):
                return redirect(nxt)
            return redirect('website:index')

        attempts = request.session.get('login_attempts', 0) + 1
        request.session['login_attempts'] = attempts
        if attempts >= MAX_ATTEMPTS:
            request.session['login_locked_until'] = now + LOCK_SECONDS
            request.session['login_attempts'] = 0
            locked_for = LOCK_SECONDS
            error = f'試行回数が多すぎます。{LOCK_SECONDS}秒待ってください。'
        else:
            error = '合言葉が違います。'

    context = {
        'error': error,
        'locked_for': locked_for,
        'next': request.GET.get('next', ''),
    }
    return render(request, 'website/login.html', context)


def logout(request):
    request.session.flush()
    return redirect(reverse('website:login'))


def index(request):
    return render(request, 'website/index.html', {'features': FEATURES})


def feature(request, num):
    if num not in FEATURES:
        raise Http404
    context = {'features': FEATURES, 'num': num, 'feature': FEATURES[num]}
    return render(request, 'website/feature.html', context)
