"""ログイン試行の記録（総当たり対策）

⚠️ 旧実装はセッション（Cookie）に失敗回数を持っていたため、**Cookieを捨てれば
即リセット**でき、総当たり対策として機能していなかった。攻撃者はCookieを送らない。
そのため IP 単位で DB に記録する方式へ変更した。

パスワードの強度を上げにくい事情（ユーザーが覚えやすさを優先）を、
ここでのロックアウトで補う設計。
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class LoginAttempt(models.Model):
    """ログイン試行の記録。成功・失敗の両方を残す（不審なアクセスの確認用）"""
    ip = models.GenericIPAddressField(db_index=True)
    username = models.CharField(max_length=254, blank=True)
    success = models.BooleanField(default=False)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['ip', '-created_at'])]

    def __str__(self):
        return f'{self.created_at:%m/%d %H:%M} {self.ip} {"OK" if self.success else "NG"}'

    @classmethod
    def recent_failures(cls, ip, minutes):
        """直近N分の連続失敗数（最後の成功以降のみ数える）"""
        since = timezone.now() - timezone.timedelta(minutes=minutes)
        rows = list(cls.objects.filter(ip=ip, created_at__gte=since).order_by('-created_at'))
        n = 0
        for r in rows:
            if r.success:
                break
            n += 1
        return n

    @classmethod
    def purge_old(cls, days=90):
        """古い記録を消す（個人情報を無期限には持たない）"""
        cutoff = timezone.now() - timezone.timedelta(days=days)
        cls.objects.filter(created_at__lt=cutoff).delete()


def _new_recovery_codes(n=8):
    """リカバリコード。端末紛失時に自分を締め出さないための最後の鍵"""
    return [f'{secrets.token_hex(2)}-{secrets.token_hex(2)}' for _ in range(n)]


class TotpDevice(models.Model):
    """認証アプリ（Google Authenticator 等）による2段階認証

    SMS ではなく TOTP を選んだ理由（ユーザーと合意済み）:
    - SMS は SIM スワップに弱く、2FA の中で最も強度が低い
    - SMS 業者の API キーをサーバーに置くことになり「サーバーに秘密を置かない」
      方針と矛盾する。さらに月額費用がかかる
    - TOTP は外部サービス不要・費用ゼロで、利用者の体感は SMS と同じ

    ⚠️ secret はこの DB にある。DB が漏れれば 2FA は突破されうるので、
    2FA は「パスワードが漏れた場合」の保険であって万能ではない。
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name='totp')
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField(default=False, help_text='初回の6桁確認が通ったか')
    # 使い捨てのリカバリコード（JSON配列）。使ったものはリストから消す
    recovery_codes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.user} の認証アプリ（{"有効" if self.confirmed else "設定中"}）'

    def issue_recovery_codes(self):
        self.recovery_codes = _new_recovery_codes()
        self.save(update_fields=['recovery_codes'])
        return self.recovery_codes

    def verify(self, code):
        """6桁コードの検証。前後1ステップ（±30秒）の時計ずれを許容する"""
        import pyotp
        code = (code or '').replace(' ', '').replace('-', '')
        if not code:
            return False
        if pyotp.TOTP(self.secret).verify(code, valid_window=1):
            self.last_used_at = timezone.now()
            self.save(update_fields=['last_used_at'])
            return True
        return False

    def use_recovery_code(self, code):
        """リカバリコードは1回限り。使ったら消す"""
        code = (code or '').strip().lower()
        codes = list(self.recovery_codes or [])
        if code in codes:
            codes.remove(code)
            self.recovery_codes = codes
            self.last_used_at = timezone.now()
            self.save(update_fields=['recovery_codes', 'last_used_at'])
            return True
        return False

    def provisioning_uri(self):
        """認証アプリに読み込ませる otpauth:// URI（QRの中身）"""
        import pyotp
        return pyotp.TOTP(self.secret).provisioning_uri(
            name=self.user.get_username(), issuer_name='KABU ANALYZE')
