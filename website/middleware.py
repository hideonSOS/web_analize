"""サイト全体を1つの合言葉で保護する簡易認証

個人用サイトを「関係者以外が開けない」程度に守るための仕組み。
Djangoのユーザー管理は使わず、config.json の site_password と一致すれば
セッションに印を付けて以後の閲覧を許可する。

注意:
- パスワードは config.json（git管理外）に置く。コードに直接書かない。
- HTTPSでない場合、パスワードは平文で流れる。公開運用ではSSLを併用すること。
"""
import secrets

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

SESSION_KEY = 'site_authed'

# 認証なしで通す接頭辞（ログイン画面自体と、その表示に必要な静的ファイル）
EXEMPT_PREFIXES = ('/static/',)


class SitePasswordMiddleware:
    """未認証ならログイン画面へ飛ばす"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_auth(request):
            return redirect(f"{reverse('website:login')}?next={request.get_full_path()}")
        return self.get_response(request)

    @staticmethod
    def _needs_auth(request):
        # パスワード未設定なら認証をかけない（開発時の利便性のため）
        if not settings.SITE_PASSWORD:
            return False
        if request.session.get(SESSION_KEY):
            return False
        path = request.path
        if path.startswith(EXEMPT_PREFIXES):
            return False
        # ログイン画面は当然除外
        if path == reverse('website:login'):
            return False
        return True


def check_password(raw):
    """入力値の照合。タイミング差で推測されないよう定数時間で比較する"""
    if not settings.SITE_PASSWORD:
        return False
    return secrets.compare_digest(str(raw), str(settings.SITE_PASSWORD))
