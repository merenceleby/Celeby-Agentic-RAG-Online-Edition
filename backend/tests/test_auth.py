import pytest
from fastapi.testclient import TestClient

import main
from config import settings
from services.auth import DAY_SECS, AuthBanError, AuthInvalidError, AuthService
from services.chat_history import ChatHistory


@pytest.fixture
def auth_enabled():
    orig = (settings.auth_enabled, settings.auth_username, settings.auth_password, settings.auth_secret)
    object.__setattr__(settings, "auth_enabled", True)
    object.__setattr__(settings, "auth_username", "admin")
    object.__setattr__(settings, "auth_password", "secret123")
    object.__setattr__(settings, "auth_secret", "test-secret")
    try:
        yield
    finally:
        for k, v in zip(("auth_enabled", "auth_username", "auth_password", "auth_secret"), orig):
            object.__setattr__(settings, k, v)


def test_escalating_ban_1_day_then_2_days_and_reset_on_success(auth_enabled):
    now = [1000.0]
    svc = AuthService(clock=lambda: now[0])

    # İlk 2 yanlış deneme izinli
    with pytest.raises(AuthInvalidError) as e1:
        svc.login("1.2.3.4", "admin", "bad")
    assert e1.value.remaining == 2
    with pytest.raises(AuthInvalidError) as e2:
        svc.login("1.2.3.4", "admin", "bad")
    assert e2.value.remaining == 1

    # 3. yanlış deneme (2'den fazla) -> 1 gün ban
    with pytest.raises(AuthBanError) as b1:
        svc.login("1.2.3.4", "admin", "bad")
    assert b1.value.ban_level == 1 and b1.value.retry_after == DAY_SECS

    # Ban süresince doğru şifre bile engelli
    with pytest.raises(AuthBanError):
        svc.login("1.2.3.4", "admin", "secret123")

    # 1 gün geçtikten sonra tekrar >2 yanlış -> 2 gün ban
    now[0] += DAY_SECS + 1
    for _ in range(2):
        with pytest.raises(AuthInvalidError):
            svc.login("1.2.3.4", "admin", "bad")
    with pytest.raises(AuthBanError) as b2:
        svc.login("1.2.3.4", "admin", "bad")
    assert b2.value.ban_level == 2 and b2.value.retry_after == 2 * DAY_SECS

    # 2 gün geçtikten sonra doğru giriş -> ban seviyesi sıfırlanır
    now[0] += 2 * DAY_SECS + 1
    token = svc.login("1.2.3.4", "admin", "secret123")
    assert svc.verify_token(token) == "admin"
    assert svc._get_ban_row("1.2.3.4") == (0, 0, 0.0)


def test_auth_middleware_blocks_unauthenticated_and_allows_after_login(auth_enabled):
    main.app.state.auth = AuthService()
    main.app.state.history = ChatHistory()
    c = TestClient(main.app)

    assert c.get("/api/health").status_code == 200
    assert c.get("/api/auth/status").json() == {"enabled": True, "authenticated": False, "username": None}
    assert c.get("/api/chats").status_code == 401

    assert c.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r = c.post("/api/auth/login", json={"username": "admin", "password": "secret123"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/api/auth/status").json()["authenticated"] is True
    assert c.get("/api/chats").status_code == 200

    c.post("/api/auth/logout")
    assert c.get("/api/chats").status_code == 401
