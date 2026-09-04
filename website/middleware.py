"""サイト全体を認証で保護する

**2026-09-05 に「合言葉1つ」から Django 標準のユーザー認証へ移行した。**
移行の理由:
- 合言葉は config.json に**平文**で置かれており、サーバー侵入時にそのまま漏れた
- 誰がいつログインしたかの記録が残らなかった
- パスワード変更に config.json の編集とサービス再起動が必要だった

現在は Django の User（パスワードはハッシュ保存）を使い、未ログインなら
ログイン画面へ飛ばす。将来 TOTP による2段階認証を足す土台にもなる。

⚠️ 旧セッション（site_authed）は**意図的に無効**にしている。弱い合言葉で
作られたセッションを生かし続けると、移行の意味が薄れるため。移行後は全端末で
ログインし直しになる（利用者は1人なので影響は小さい）。
"""
from django.shortcuts import redirect
from django.urls import reverse

# 旧・合言葉方式のセッションキー（既存ログインを切らないために参照だけ残す）
SESSION_KEY = 'site_authed'

# 認証なしで通す接頭辞（ログイン画面のCSS等）
EXEMPT_PREFIXES = ('/static/',)


class SitePasswordMiddleware:
    """未ログインならログイン画面へ飛ばす

    クラス名は旧実装からのURL・設定互換のため据え置き（settings.py の
    MIDDLEWARE に書かれているため）。中身はユーザー認証に置き換わっている。
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_auth(request):
            return redirect(f"{reverse('website:login')}?next={request.get_full_path()}")
        return self.get_response(request)

    @staticmethod
    def _needs_auth(request):
        if getattr(request, 'user', None) and request.user.is_authenticated:
            return False
        path = request.path
        if path.startswith(EXEMPT_PREFIXES):
            return False
        # ⚠️ 2段階認証の確認画面（login_verify）も除外が必須。
        # ここを保護対象にすると「パスワードは通ったがまだ未ログイン」の状態で
        # ログイン画面へ戻され、コードを入力できず永久に入れなくなる（実際に踏んだ）。
        # この画面自体はセッションの PENDING_USER_KEY が無いと何も表示しない。
        if path in (reverse('website:login'), reverse('website:login_verify'), '/admin/login/'):
            return False
        return True


def client_ip(request):
    """クライアントIP。nginx 経由なので X-Forwarded-For を優先する

    ⚠️ このヘッダは偽装できるが、**nginx が自分で付け直す構成**なら信頼できる。
    直アクセスも受ける構成なら REMOTE_ADDR を使うこと。
    """
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')
