"""Web UI 管理员密码安全测试（P0 修复）：scrypt 哈希落库 + 明文兼容迁移 + 登录安全验证。

验证：
- hash_password / verify_password（哈希 + 旧明文兼容）
- register_user 只存哈希，DB 无明文
- 登录用哈希验证成功
- 旧明文（DB/.env）登录后自动迁移为哈希
- 首次启动行为不变（.env 明文可直接登录）
- 密码/哈希不进日志
"""
import os
import tempfile

from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService, hash_password, is_hashed_password, verify_password
from src.services.web_ui import WebUIServer
from tests.test_config_service import FakeSettings
from tests.test_web_ui import FakeRequest, _resp_data


def _make_server():
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(os.path.join(tmp.name, "s.db"))
    cfg = FakeSettings(WEB_UI_USERNAME="admin", WEB_UI_PASSWORD="legacy-env-pass")
    svc = ConfigService(cfg, repo)
    server = WebUIServer(cfg, svc)
    return server, repo, cfg, tmp


# ---------- 哈希原语 ----------
def test_hash_never_contains_plaintext():
    h = hash_password("secret123")
    assert "secret123" not in h
    assert h.startswith("scrypt$")
    assert is_hashed_password(h)


def test_verify_hash_roundtrip():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False
    assert verify_password("secret123", "") is False


def test_verify_legacy_plaintext():
    assert verify_password("abc", "abc") is True
    assert verify_password("abc", "abd") is False
    assert verify_password("", "") is False


def test_verify_corrupt_hash_safe():
    assert verify_password("x", "scrypt$garbage") is False
    assert verify_password("x", "scrypt$1$2$3$zz$yy") is False


# ---------- 注册：只存哈希 ----------
def test_register_stores_hash_only():
    server, repo, cfg, tmp = _make_server()
    ok, msg = server.config_service.register_user("boss", "newpass123")
    assert ok and "注册成功" in msg
    stored = repo.get_config("WEB_UI_PASSWORD")
    assert is_hashed_password(stored)
    assert "newpass123" not in stored
    assert verify_password("newpass123", stored)
    tmp.cleanup()


async def test_login_verifies_against_hash():
    server, repo, cfg, tmp = _make_server()
    server.config_service.register_user("boss", "newpass123")
    resp = await server._handle_login(FakeRequest(body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    assert "token" in await _resp_data(resp)
    # 错误密码
    resp = await server._handle_login(FakeRequest(body={"username": "boss", "password": "wrong"}))
    assert resp.status == 401
    tmp.cleanup()


# ---------- 明文迁移 ----------
async def test_legacy_plaintext_migrated_on_login():
    server, repo, cfg, tmp = _make_server()
    # 模拟历史版本：DB 里存着明文
    repo.set_config("WEB_UI_USERNAME", "admin")
    repo.set_config("WEB_UI_PASSWORD", "secret123")
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 200
    stored = repo.get_config("WEB_UI_PASSWORD")
    assert is_hashed_password(stored)          # 已迁移为哈希
    assert stored != "secret123"               # 明文不再落库
    assert verify_password("secret123", stored)
    tmp.cleanup()


async def test_env_plaintext_login_still_works():
    """首次启动/已有配置行为不被破坏：未注册时 .env 明文可直接登录。"""
    server, repo, cfg, tmp = _make_server()
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "legacy-env-pass"}))
    assert resp.status == 200
    # .env 明文登录成功后也会迁移到 DB（此后走哈希）
    stored = repo.get_config("WEB_UI_PASSWORD")
    assert stored is None or is_hashed_password(stored)
    tmp.cleanup()


# ---------- 注册的 admin_password 校验（同样走哈希/迁移） ----------
async def test_register_requires_current_hashed_password():
    server, repo, cfg, tmp = _make_server()
    server.config_service.register_user("boss", "newpass123")
    # 用正确当前密码（哈希校验）注册新账号成功
    resp = await server._handle_register(FakeRequest(
        body={"username": "boss2", "password": "another123", "admin_password": "newpass123"}))
    assert resp.status == 200
    # 错误当前密码被拒
    resp = await server._handle_register(FakeRequest(
        body={"username": "hacker", "password": "x123456", "admin_password": "wrong"}))
    assert resp.status == 403
    tmp.cleanup()


# ---------- 密码/哈希不进普通日志 ----------
def test_password_never_logged(caplog):
    import logging
    server, repo, cfg, tmp = _make_server()
    with caplog.at_level(logging.INFO):
        server.config_service.register_user("boss", "super-secret-pw-123")
    logs = caplog.text
    assert "super-secret-pw-123" not in logs
    assert "scrypt$" not in logs.split("web_ui account registered")[1] if "web_ui account registered" in logs else True
    tmp.cleanup()
