"""Deploy için basit giriş + artan IP ban sistemi (ek kütüphane gerektirmez).

- Lokalde AUTH_ENABLED=false iken tamamen devre dışıdır.
- 2'den fazla hatalı girişte (3. yanlışta) 1 gün ban, ban bitip tekrar olursa 2 gün, 3 gün... şeklinde artar.
- Başarılı girişte IP'nin ban/hata sayacı sıfırlanır.
"""
import base64
import hashlib
import hmac
import json
import math
import time
from typing import Optional

from config import settings
from services.db import connect

COOKIE_NAME = "celeby_session"
DAY_SECS = 86400


class AuthInvalidError(Exception):
    def __init__(self, remaining: int, message: str):
        super().__init__(message)
        self.remaining = remaining
        self.message = message


class AuthBanError(Exception):
    def __init__(self, retry_after: int, ban_level: int, message: str):
        super().__init__(message)
        self.retry_after = max(1, int(retry_after))
        self.ban_level = ban_level
        self.message = message


def client_ip(request) -> str:
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return (request.client.host if request.client else "unknown") or "unknown"


class AuthService:
    def __init__(self, clock=time.time):
        self._clock = clock

    @staticmethod
    def _secret() -> bytes:
        raw = settings.auth_secret or f"{settings.auth_username}:{settings.auth_password}:celeby-auth"
        return hashlib.sha256(raw.encode()).digest()

    def create_token(self, username: str) -> str:
        payload = {"u": username, "exp": int(self._clock()) + settings.auth_session_days * DAY_SECS}
        body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
        sig = hmac.new(self._secret(), body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def verify_token(self, token: Optional[str]) -> Optional[str]:
        if not token or "." not in token:
            return None
        body, sig = token.rsplit(".", 1)
        expected = hmac.new(self._secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        try:
            payload = json.loads(base64.urlsafe_b64decode(body.encode()).decode())
        except Exception:  # noqa: BLE001
            return None
        if payload.get("exp", 0) <= self._clock():
            return None
        return payload.get("u")

    def _get_ban_row(self, ip: str) -> tuple[int, int, float]:
        with connect() as c:
            row = c.execute(
                "SELECT fails, ban_level, banned_until FROM login_bans WHERE ip=?", (ip,)
            ).fetchone()
        return (row["fails"], row["ban_level"], row["banned_until"]) if row else (0, 0, 0.0)

    def _save_ban_row(self, ip: str, fails: int, ban_level: int, banned_until: float) -> None:
        with connect() as c:
            c.execute(
                "INSERT INTO login_bans(ip, fails, ban_level, banned_until) VALUES(?,?,?,?) "
                "ON CONFLICT(ip) DO UPDATE SET fails=excluded.fails, ban_level=excluded.ban_level, "
                "banned_until=excluded.banned_until",
                (ip, fails, ban_level, banned_until),
            )

    def _clear_ban(self, ip: str) -> None:
        with connect() as c:
            c.execute("DELETE FROM login_bans WHERE ip=?", (ip,))

    @staticmethod
    def _format_wait(secs: float) -> str:
        hours = max(1, int(math.ceil(secs / 3600)))
        if hours >= 24:
            days = int(math.ceil(hours / 24))
            return f"{days} gün"
        return f"{hours} saat"

    def login(self, ip: str, username: str, password: str) -> str:
        now = self._clock()
        fails, ban_level, banned_until = self._get_ban_row(ip)
        if banned_until > now:
            wait = int(math.ceil(banned_until - now))
            raise AuthBanError(
                wait, ban_level,
                f"Çok fazla hatalı giriş nedeniyle erişiminiz engellendi. Kalan süre: ~{self._format_wait(wait)}.",
            )

        configured = bool(settings.auth_username and settings.auth_password)
        ok = (
            configured
            and hmac.compare_digest(username.strip(), settings.auth_username)
            and hmac.compare_digest(password, settings.auth_password)
        )
        if ok:
            self._clear_ban(ip)
            return self.create_token(settings.auth_username)

        fails += 1
        if fails > settings.auth_max_attempts:
            ban_level += 1
            days = ban_level  # 1. ban: 1 gün, 2. ban: 2 gün, 3. ban: 3 gün...
            wait = days * DAY_SECS
            self._save_ban_row(ip, fails=0, ban_level=ban_level, banned_until=now + wait)
            raise AuthBanError(
                wait, ban_level,
                f"Çok fazla hatalı giriş yaptınız. {days} gün boyunca engellendiniz.",
            )

        self._save_ban_row(ip, fails=fails, ban_level=ban_level, banned_until=0.0)
        remaining = settings.auth_max_attempts - fails + 1
        raise AuthInvalidError(
            remaining,
            f"Hatalı kullanıcı adı veya şifre. Kalan deneme hakkı: {remaining}.",
        )
