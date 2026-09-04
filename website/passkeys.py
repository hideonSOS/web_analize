"""パスキー（WebAuthn）の登録・認証

ユーザー体験:
- スマホ: ログイン画面で顔/指紋 → 完了（パスワード入力なし）
- PC: ログイン画面のボタンを押すとブラウザがQRを出す → スマホで読んで顔認証 → PCがログイン
  （QRの表示と近接通信はブラウザとOSの機能。こちらで実装するものではない）

セキュリティ上の性質:
- 秘密鍵は端末から出ない。サーバーは公開鍵しか持たないので、**DBが漏れても
  なりすましログインはできない**（TOTPのsecretとの決定的な違い）
- ブラウザがオリジンを検証するためフィッシングに強い

⚠️ RP_ID（Relying Party ID）は「ホスト名」で、ポートやスキームを含めない。
このサイトはドメインが無くIPアドレス運用なので RP_ID も IP になる。
開発は localhost。**ここが実際のホストと1文字でも違うとブラウザが拒否する。**
"""
from __future__ import annotations

import base64

from django.conf import settings

# 認証中の状態（チャレンジ）を置くセッションキー。
# チャレンジは1回限りの使い捨てで、リプレイ攻撃を防ぐためにサーバー側で保持する
REG_CHALLENGE_KEY = 'webauthn_reg_challenge'
AUTH_CHALLENGE_KEY = 'webauthn_auth_challenge'

RP_NAME = 'KABU ANALYZE'


def rp_id(request) -> str:
    """このサイトのホスト名。ポートは含めない（WebAuthnの仕様）"""
    return request.get_host().split(':')[0]


def is_available(request) -> bool:
    """この環境でパスキーが使えるか

    ⚠️ **WebAuthn の RP ID はドメイン名でなければならず、IPアドレスは使えない**
    （仕様上 "valid domain string" が要求される）。IPで開くとブラウザが
    "The effective domain of the document is not a valid domain" で拒否する。
    実装は完成しているので、**ドメインを取得すればコード変更なしで有効になる**。
    localhost は仕様上の例外として許可されている。
    """
    host = rp_id(request)
    if host == 'localhost':
        return True
    if not request.is_secure():
        return False          # HTTPS必須
    # IPアドレス（v4/v6）は不可。ドット区切りが全部数字ならIPv4、コロンを含めばIPv6
    if all(part.isdigit() for part in host.split('.')) or ':' in host:
        return False
    return '.' in host        # ドメイン名らしきもの


def origin(request) -> str:
    """スキーム込みのオリジン。ブラウザが送るものと完全一致する必要がある"""
    scheme = 'https' if request.is_secure() else 'http'
    return f'{scheme}://{request.get_host()}'


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def b64url_decode(s: str) -> bytes:
    s = s + '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode('ascii'))


# ── 登録（パスキーを作る） ────────────────────────────

def registration_options(request, user):
    """ブラウザに渡す登録用オプションを作る。戻り値は JSON 文字列"""
    import json

    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
        ResidentKeyRequirement, UserVerificationRequirement,
    )
    from .models import Passkey

    # 同じ端末で二重登録しないよう、登録済みの鍵を除外リストで渡す
    existing = [
        PublicKeyCredentialDescriptor(id=b64url_decode(p.credential_id))
        for p in Passkey.objects.filter(user=user)
    ]

    opts = generate_registration_options(
        rp_id=rp_id(request),
        rp_name=RP_NAME,
        user_id=str(user.pk).encode('utf-8'),
        user_name=user.get_username(),
        user_display_name=user.get_username(),
        exclude_credentials=existing,
        authenticator_selection=AuthenticatorSelectionCriteria(
            # resident_key=required にすると「ユーザー名を入力せずログイン」ができる。
            # これがないとパスキーの利点（タップだけ）が半減する
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    request.session[REG_CHALLENGE_KEY] = b64url_encode(opts.challenge)
    return options_to_json(opts)


def verify_registration(request, user, credential_json: str, name: str = ''):
    """ブラウザから返ってきた登録レスポンスを検証し、Passkey を保存する"""
    from webauthn import verify_registration_response
    from .models import Passkey

    challenge = request.session.pop(REG_CHALLENGE_KEY, None)
    if not challenge:
        raise ValueError('登録の手続きが期限切れです。もう一度お試しください。')

    verification = verify_registration_response(
        credential=credential_json,
        expected_challenge=b64url_decode(challenge),
        expected_rp_id=rp_id(request),
        expected_origin=origin(request),
    )

    cred_id = b64url_encode(verification.credential_id)
    if Passkey.objects.filter(credential_id=cred_id).exists():
        raise ValueError('この端末のパスキーは既に登録されています。')

    return Passkey.objects.create(
        user=user,
        name=(name or '').strip()[:60] or _guess_device_name(request),
        credential_id=cred_id,
        public_key=b64url_encode(verification.credential_public_key),
        sign_count=verification.sign_count or 0,
    )


def _guess_device_name(request):
    """User-Agent から端末名をざっくり推定（あくまで初期値。後から変更できる）"""
    ua = request.META.get('HTTP_USER_AGENT', '')
    for key, label in [('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
                       ('Macintosh', 'Mac'), ('Windows', 'Windows PC')]:
        if key in ua:
            return label
    return 'パスキー'


# ── 認証（パスキーでログイン） ──────────────────────────

def authentication_options(request):
    """ログイン用オプション。**ユーザーを特定せずに**発行する

    allow_credentials を空にすることで、ブラウザ側が端末内の鍵から選ばせる
    （＝メールアドレスの入力すら不要になる）。
    """
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import UserVerificationRequirement

    opts = generate_authentication_options(
        rp_id=rp_id(request),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    request.session[AUTH_CHALLENGE_KEY] = b64url_encode(opts.challenge)
    return options_to_json(opts)


def verify_authentication(request, credential_json: str):
    """認証レスポンスを検証し、対応する Passkey を返す（失敗は ValueError）"""
    import json

    from webauthn import verify_authentication_response
    from .models import Passkey

    challenge = request.session.pop(AUTH_CHALLENGE_KEY, None)
    if not challenge:
        raise ValueError('ログインの手続きが期限切れです。もう一度お試しください。')

    data = json.loads(credential_json)
    passkey = Passkey.objects.filter(credential_id=data.get('id')).first()
    if passkey is None:
        raise ValueError('このパスキーは登録されていません。')

    verification = verify_authentication_response(
        credential=credential_json,
        expected_challenge=b64url_decode(challenge),
        expected_rp_id=rp_id(request),
        expected_origin=origin(request),
        credential_public_key=b64url_decode(passkey.public_key),
        credential_current_sign_count=passkey.sign_count,
    )
    passkey.touch(verification.new_sign_count)
    return passkey
