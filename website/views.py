from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from .middleware import client_ip
from .models import LoginAttempt, TotpDevice

# 2段階認証の途中経過を持つセッションキー（パスワードは通ったがコード未入力の状態）
PENDING_USER_KEY = 'totp_pending_user_id'

# 総当たり対策（IP単位・DB記録）。
# ⚠️ 旧実装はセッションに回数を持っていたため Cookie を捨てれば無効化できた。
# パスワードを覚えやすい文字列にしている分、ここで補う設計。
MAX_ATTEMPTS = 5           # この回数を失敗するとロック
LOCK_MINUTES = 15          # ロック時間（旧: 60秒 → 15分に延長）
WINDOW_MINUTES = 60        # 失敗回数を数える窓

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
    11: {'name': '支出分析', 'description': '家計CSVから支出の歪みを見つけ、節約の決定を投資への入金力に変える', 'url': 'spending:index'},
    12: {'name': '月次支出', 'description': 'その月に何が起きたかを前月・平常月（中央値）と比べて読む', 'url': 'spending:month'},
}


def login(request):
    """メールアドレス（ユーザー名）とパスワードでログインする

    パスワードは Django がハッシュ化して保存しており、平文はどこにも残らない。
    失敗は IP 単位で DB に記録し、一定回数でロックする。
    """
    if request.user.is_authenticated:
        return redirect('website:index')

    ip = client_ip(request)
    error = ''
    locked_for = 0

    # 直近の失敗回数からロック状態を判定する
    fails = LoginAttempt.recent_failures(ip, WINDOW_MINUTES)
    if fails >= MAX_ATTEMPTS:
        last = LoginAttempt.objects.filter(ip=ip).first()
        if last:
            elapsed = (timezone.now() - last.created_at).total_seconds()
            remain = LOCK_MINUTES * 60 - elapsed
            if remain > 0:
                locked_for = int(remain)
                error = f'試行回数が多すぎます。{int(remain // 60) + 1}分ほど待ってください。'

    if request.method == 'POST' and not locked_for:
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        user = authenticate(request, username=username, password=password)
        LoginAttempt.objects.create(
            ip=ip, username=username[:254], success=user is not None,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:200])

        if user is not None:
            # 2段階認証が有効なら、ここではログインさせず確認画面へ回す
            device = TotpDevice.objects.filter(user=user, confirmed=True).first()
            if device:
                request.session[PENDING_USER_KEY] = user.pk
                request.session['totp_next'] = request.POST.get('next') or ''
                return redirect('website:login_verify')
            auth_login(request, user)
            # 開こうとしていたページへ戻す（外部サイトへ飛ばさないよう内部パスに限定）
            nxt = request.POST.get('next') or request.GET.get('next') or ''
            if nxt.startswith('/') and not nxt.startswith('//'):
                return redirect(nxt)
            return redirect('website:index')

        # 残り試行回数を伝える（本人が入力ミスに気付けるように）
        left = MAX_ATTEMPTS - LoginAttempt.recent_failures(ip, WINDOW_MINUTES)
        error = ('メールアドレスまたはパスワードが違います。'
                 + (f'（あと{left}回でロックされます）' if 0 < left <= 3 else ''))

    context = {
        'error': error,
        'locked_for': locked_for,
        'next': request.GET.get('next', ''),
    }
    return render(request, 'website/login.html', context)


def login_verify(request):
    """2段階認証の2段目。認証アプリの6桁コード、またはリカバリコードを受ける。

    ⚠️ この画面に来られるのは「パスワードが通った直後」だけ（セッションに
    PENDING_USER_KEY がある状態）。直リンクでは来られない。
    """
    from django.contrib.auth.models import User

    user_id = request.session.get(PENDING_USER_KEY)
    if not user_id:
        return redirect('website:login')
    user = User.objects.filter(pk=user_id).first()
    device = TotpDevice.objects.filter(user=user, confirmed=True).first() if user else None
    if not device:
        request.session.pop(PENDING_USER_KEY, None)
        return redirect('website:login')

    ip = client_ip(request)
    error = ''
    # コード入力も総当たり対象。パスワード側と同じ窓・回数で数える
    if LoginAttempt.recent_failures(ip, WINDOW_MINUTES) >= MAX_ATTEMPTS:
        error = '試行回数が多すぎます。しばらく待ってください。'
    elif request.method == 'POST':
        code = request.POST.get('code', '')
        ok = device.verify(code) or device.use_recovery_code(code)
        LoginAttempt.objects.create(
            ip=ip, username=user.get_username()[:254], success=ok,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:200])
        if ok:
            request.session.pop(PENDING_USER_KEY, None)
            nxt = request.session.pop('totp_next', '') or ''
            auth_login(request, user)
            if nxt.startswith('/') and not nxt.startswith('//'):
                return redirect(nxt)
            return redirect('website:index')
        error = 'コードが違います。認証アプリの表示を確認してください。'

    return render(request, 'website/login_verify.html',
                  {'error': error, 'recovery_left': len(device.recovery_codes or [])})


def security(request):
    """2段階認証の設定画面（有効化 / 解除 / リカバリコードの再発行）"""
    import pyotp
    import segno

    if not request.user.is_authenticated:
        return redirect('website:login')

    device = TotpDevice.objects.filter(user=request.user).first()
    message = error = ''
    new_codes = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'start':
            # 未確認の設定途中は作り直す（前回の残骸で混乱しないように）
            TotpDevice.objects.filter(user=request.user, confirmed=False).delete()
            device = TotpDevice.objects.create(user=request.user, secret=pyotp.random_base32())
            message = '認証アプリでQRコードを読み取り、表示された6桁を入力してください。'

        elif action == 'confirm' and device and not device.confirmed:
            if device.verify(request.POST.get('code', '')):
                device.confirmed = True
                device.save(update_fields=['confirmed'])
                new_codes = device.issue_recovery_codes()
                message = '2段階認証を有効にしました。下のリカバリコードを保管してください。'
            else:
                error = 'コードが違います。アプリの表示と端末の時刻を確認してください。'

        elif action == 'disable' and device:
            # 解除は現在のコードを要求する（乗っ取られた状態で外させないため）
            code = request.POST.get('code', '')
            if not device.confirmed or device.verify(code) or device.use_recovery_code(code):
                device.delete()
                device = None
                message = '2段階認証を解除しました。'
            else:
                error = 'コードが違います。解除するには現在のコードが必要です。'

        elif action == 'regen' and device and device.confirmed:
            if device.verify(request.POST.get('code', '')):
                new_codes = device.issue_recovery_codes()
                message = 'リカバリコードを再発行しました。古いコードは無効です。'
            else:
                error = 'コードが違います。'

    qr_svg = None
    if device and not device.confirmed:
        qr = segno.make(device.provisioning_uri(), error='m')
        qr_svg = qr.svg_inline(scale=5, dark='#e5e7eb', light='#0b1220')

    return render(request, 'website/security.html', {
        'device': device,
        'qr_svg': qr_svg,
        'secret': device.secret if device and not device.confirmed else None,
        'new_codes': new_codes,
        'message': message,
        'error': error,
        'recent_logins': LoginAttempt.objects.filter(
            username=request.user.get_username())[:10],
    })


def logout(request):
    auth_logout(request)
    request.session.flush()
    return redirect(reverse('website:login'))


def index(request):
    return render(request, 'website/index.html', {'features': FEATURES})


def feature(request, num):
    if num not in FEATURES:
        raise Http404
    context = {'features': FEATURES, 'num': num, 'feature': FEATURES[num]}
    return render(request, 'website/feature.html', context)
