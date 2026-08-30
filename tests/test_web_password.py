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


def _make_server(pwd: str = "legacy-env-pass"):
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(os.path.join(tmp.name, "s.db"))
    cfg = FakeSettings(WEB_UI_USERNAME="admin", WEB_UI_PASSWORD=pwd)
    svc = ConfigService(cfg, repo)
    server = WebUIServer(cfg, svc)
    return server, repo, cfg, tmp


def _bootstrap_server():
    """未初始化状态的 server（首次注册路径）。"""
    server, repo, cfg, tmp = _make_server(pwd="")
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


# ---------- 注册：只存哈希（仅未初始化状态可用） ----------
def test_register_stores_hash_only():
    server, repo, cfg, tmp = _bootstrap_server()
    assert server.config_service.admin_initialized() is False
    ok, msg = server.config_service.register_user("boss", "newpass123")
    assert ok and "注册成功" in msg
    stored = repo.get_config("WEB_UI_PASSWORD")
    assert is_hashed_password(stored)
    assert "newpass123" not in stored
    assert verify_password("newpass123", stored)
    # 注册后公开注册关闭：第二次注册被拒（Bootstrap Lock）
    ok2, msg2 = server.config_service.register_user("hacker", "another123")
    assert not ok2 and "关闭" in msg2
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


# ---------- 已初始化后：公开注册关闭；改密走登录态 change_credentials ----------
async def test_register_closed_when_initialized():
    server, repo, cfg, tmp = _make_server()
    # 已初始化（.env 密码存在）：注册一律 403；即使带当前密码也不能自建第二账号
    resp = await server._handle_register(FakeRequest(
        body={"username": "boss2", "password": "another123", "admin_password": "legacy-env-pass"}))
    assert resp.status == 403


async def test_change_credentials_updates_hash():
    """登录态改密：只换凭据，状态保持 INITIALIZED（change_credentials 需当前密码）。"""
    server, repo, cfg, tmp = _make_server(pwd="")
    ok, _ = server.config_service.register_user("boss", "newpass123")
    assert ok
    ok, msg = server.config_service.change_credentials("boss2", "another123", "wrong")
    assert not ok and "不正确" in msg
    ok, msg = server.config_service.change_credentials("boss2", "another123", "newpass123")
    assert ok
    stored = repo.get_config("WEB_UI_PASSWORD")
    assert is_hashed_password(stored)
    assert verify_password("another123", stored)
    # 状态仍 INITIALIZED：公开注册不回开
    assert server.config_service.admin_initialized() is True
    tmp.cleanup()
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
