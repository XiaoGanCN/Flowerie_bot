"""Web UI 与 Config Service 测试（第四轮）：认证/脱敏/持久化/热更新/端口校验。"""
import json as _json
import tempfile

import pytest

from src.config import validate_config
from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from src.services.web_ui import WebUIServer
from tests.test_router_regression import make_config


# ---------- 端口冲突（用户特别要求：Web UI 回环端口不能与反向 WS 端口一致） ----------
def test_web_ui_port_must_differ_from_ws_port():
    cfg = make_config(DEEPSEEK_API_KEY="sk-real-key-123456", WS_PORT=3001,
                      WEB_UI_ENABLED=True, WEB_UI_PORT=3001, WEB_UI_PASSWORD="x")
    with pytest.raises(ValueError) as exc:
        validate_config(cfg)
    assert "WEB_UI_PORT" in str(exc.value)
    assert "WS_PORT" in str(exc.value)


def test_web_ui_port_ok_when_different():
    cfg = make_config(DEEPSEEK_API_KEY="sk-real-key-123456", WS_PORT=3001,
                      WEB_UI_ENABLED=True, WEB_UI_PORT=8080, WEB_UI_PASSWORD="secret123")
    validate_config(cfg)  # 不抛


def test_web_ui_requires_password():
    cfg = make_config(DEEPSEEK_API_KEY="sk-real-key-123456", WS_PORT=3001,
                      WEB_UI_ENABLED=True, WEB_UI_PORT=8080, WEB_UI_PASSWORD="")
    with pytest.raises(ValueError):
        validate_config(cfg)


def test_web_ui_disabled_skips_checks():
    cfg = make_config(DEEPSEEK_API_KEY="sk-real-key-123456", WS_PORT=3001,
                      WEB_UI_ENABLED=False, WEB_UI_PORT=3001, WEB_UI_PASSWORD="")
    validate_config(cfg)  # 未启用不校验


# ---------- 0.0.0.0 显式开关（WEB_UI_ALLOW_LAN） ----------
def test_effective_host_default_loopback():
    cfg = make_config(WEB_UI_HOST="127.0.0.1", WEB_UI_ALLOW_LAN=False)
    assert WebUIServer.effective_host(cfg) == "127.0.0.1"


def test_effective_host_allow_lan_forces_public():
    cfg = make_config(WEB_UI_HOST="127.0.0.1", WEB_UI_ALLOW_LAN=True)
    assert WebUIServer.effective_host(cfg) == "0.0.0.0"


def test_effective_host_custom_host_when_switch_off():
    cfg = make_config(WEB_UI_HOST="192.168.1.5", WEB_UI_ALLOW_LAN=False)
    assert WebUIServer.effective_host(cfg) == "192.168.1.5"


# ---------- Config Service ----------
@pytest.fixture()
def cs():
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(f"{tmp.name}/settings.db")
    config = make_config(
        DEEPSEEK_API_KEY="sk-abcdef1234567890",
        MAX_REPLY_LENGTH=40,
        WS_PORT=3001,
    )
    service = ConfigService(config, repo)
    yield config, repo, service, tmp
    repo.close()
    tmp.cleanup()


def test_secret_masking(cs):
    config, repo, service, _ = cs
    items = {c["key"]: c for c in service.list_configs()}
    assert items["DEEPSEEK_API_KEY"]["current"] == "sk-a****7890"  # 掩码
    assert "sk-abcdef1234567890" not in items["DEEPSEEK_API_KEY"]["current"]
    assert items["MAX_REPLY_LENGTH"]["current"] == "40"  # 非敏感明文


def test_config_update_and_validation(cs):
    config, repo, service, _ = cs
    ok, _ = service.update("MAX_REPLY_LENGTH", "abc")
    assert ok is False  # 非数字校验失败
    ok, msg = service.update("MAX_REPLY_LENGTH", "50")
    assert ok is True
    assert "生效" in msg
    assert config.MAX_REPLY_LENGTH == 50  # 热更新到运行实例


def test_secret_update_requires_value(cs):
    config, repo, service, _ = cs
    ok, _ = service.update("DEEPSEEK_API_KEY", "")
    assert ok is False  # 留空 = 不修改
    assert config.DEEPSEEK_API_KEY == "sk-abcdef1234567890"  # 未覆盖
    ok, _ = service.update("DEEPSEEK_API_KEY", "sk-new-key-1234567890")
    assert ok is True
    assert config.DEEPSEEK_API_KEY == "sk-new-key-1234567890"
    items = {c["key"]: c for c in service.list_configs()}
    assert items["DEEPSEEK_API_KEY"]["current"] == "sk-n****7890"


def test_persistent_config_priority(cs):
    config, repo, service, tmp = cs
    service.update("MAX_REPLY_LENGTH", "66")
    # 模拟重启：新 service 同库 → 持久化值优先于 env/默认
    config2 = make_config(MAX_REPLY_LENGTH=40)
    service2 = ConfigService(config2, SettingsRepository(f"{tmp.name}/settings.db"))
    assert service2.get_value("MAX_REPLY_LENGTH") == "66"
    service2.repository.close()


def test_env_fallback_when_no_override(cs):
    config, repo, service, _ = cs
    assert service.get_value("MAX_REPLY_LENGTH") == 40  # 未持久化 → 回退 env
    assert service.get_value("NON_EXISTENT") is None


def test_restart_required_flag(cs):
    config, repo, service, _ = cs
    ok, msg = service.update("WS_PORT", "4000")
    assert ok is True
    assert "重启" in msg  # Advanced 类明确提示需重启
    assert config.WS_PORT == 4000  # 持久化与运行实例仍更新


# ---------- Web UI（handler 层集成，本地与 CI 行为一致） ----------
class FakeRequest:
    """最小 request 桩：headers / remote / json() / post() / cookies / query。"""

    def __init__(self, headers=None, remote="127.0.0.1", body=None, query=None,
                 cookies=None, form=None):
        self.headers = headers or {}
        self.remote = remote
        self.query = query or {}
        self._body = body or {}
        self.cookies = cookies or {}
        self._form = form or {}

    async def json(self):
        return self._body

    async def post(self):
        return self._form


@pytest.fixture()
def webapp(cs):
    config, repo, service, tmp = cs
    config.WEB_UI_USERNAME = "admin"
    config.WEB_UI_PASSWORD = "secret123"
    config.WEB_UI_TOKEN_TTL_SECONDS = 3600
    server = WebUIServer(config, service)
    yield server
    server._tokens.clear()


async def _resp_data(resp):
    """兼容本地桩（_body 为 bytes JSON）与真 aiohttp（web.Response.body 为 bytes）。"""
    body = getattr(resp, "body", None)
    if body is None:
        body = getattr(resp, "_body", None)
    if body is None:
        return await resp.json()
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        return _json.loads(body.decode())
    return _json.loads(body)


async def _resp_text(resp):
    """取响应文本：真实 aiohttp 的 .text 是属性，本地桩是协程。"""
    t = getattr(resp, "text", None)
    if isinstance(t, str):
        return t
    if callable(t):
        return await t()
    body = getattr(resp, "body", b"")
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return str(body or "")


async def _login(server):
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    return (await _resp_data(resp))["token"]


async def test_login_and_unauthorized(webapp):
    server = webapp
    resp = await server._handle_get_config(FakeRequest())
    assert resp.status == 401  # 未认证
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "wrong"}))
    assert resp.status == 401
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 200
    assert "token" in await _resp_data(resp)


# ---------- 注册（Bootstrap Lock：仅未初始化时创建第一个管理员） ----------
async def test_register_closed_when_initialized(webapp):
    """.env 已配置管理员密码（=已初始化）：公开注册永久关闭 → 403，攻击者无法自建账号。"""
    server = webapp
    resp = await server._handle_register(FakeRequest(
        body={"username": "attacker", "password": "newpass123"}))
    assert resp.status == 403
    # 攻击者登录自己（未被创建的）账号 → 401
    resp = await server._handle_login(FakeRequest(body={"username": "attacker", "password": "newpass123"}))
    assert resp.status == 401


async def test_register_first_bootstrap_then_lock(webapp):
    """未初始化 → 首次注册成功；再注册第二个 → 403（第二管理员被阻止）。"""
    server = webapp
    server.config_service.repository.delete_config("WEB_UI_USERNAME")
    server.config_service.repository.delete_config("WEB_UI_PASSWORD")
    server.config_service.repository.mark_bootstrap_uninitialized()
    server.config.WEB_UI_PASSWORD = ""
    assert server.config_service.admin_initialized() is False
    resp = await server._handle_register(FakeRequest(
        body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    # 用新注册账号登录成功
    resp = await server._handle_login(FakeRequest(body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    # 第二管理员：公开注册关闭（不再有"再注册一个"的机会）
    resp = await server._handle_register(FakeRequest(
        body={"username": "hacker", "password": "newpass123"}))
    assert resp.status == 403


async def test_register_requires_current_password(webapp):
    """已初始化系统：注册一律 403（不再提供"带当前密码注册"的后门）。"""
    server = webapp
    resp = await server._handle_register(FakeRequest(
        body={"username": "hacker", "password": "newpass123", "admin_password": "secret123"}))
    assert resp.status == 403


async def test_register_closed_does_not_count_rate_limit(webapp):
    """已初始化后注册被 403 拒绝且不消耗登录限流（接口已关闭）。"""
    server = webapp
    for _ in range(5):
        resp = await server._handle_register(FakeRequest(
            remote="10.0.0.9",
            body={"username": "hacker", "password": "newpass123"}))
        assert resp.status == 403
    # 登录限流不受影响：管理员仍可登录
    resp = await server._handle_login(FakeRequest(
        remote="10.0.0.9", body={"username": "admin", "password": "secret123"}))
    assert resp.status == 200


async def test_register_validates_username_password(webapp):
    """未初始化时：用户名/密码格式校验（400）。"""
    server = webapp
    server.config_service.repository.delete_config("WEB_UI_USERNAME")
    server.config_service.repository.delete_config("WEB_UI_PASSWORD")
    server.config.WEB_UI_PASSWORD = ""
    resp = await server._handle_register(FakeRequest(
        body={"username": "ab", "password": "newpass123"}))
    assert resp.status == 400  # 用户名太短
    resp = await server._handle_register(FakeRequest(
        body={"username": "boss", "password": "123"}))
    assert resp.status == 400  # 密码太短


async def test_config_read_with_auth(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_get_config(FakeRequest(headers={"Authorization": f"Bearer {token}"}))
    assert resp.status == 200
    keys = [c["key"] for c in (await _resp_data(resp))["configs"]]
    assert "DEEPSEEK_API_KEY" in keys
    assert "MAX_REPLY_LENGTH" in keys


# ---------- 无 JS 面板（/panel，服务端渲染） ----------
async def test_panel_login_page_without_auth(webapp):
    server = webapp
    resp = await server._handle_panel(FakeRequest())
    assert resp.status == 200
    assert "登录" in await _resp_text(resp)
    assert "注册管理员账号" in await _resp_text(resp)


async def test_panel_login_sets_cookie_and_enters(webapp):
    server = webapp
    resp = await server._handle_panel_login(FakeRequest(form={"username": "admin", "password": "secret123"}))
    assert resp.status == 302  # 重定向到 /panel
    cookie = resp.cookies.get("fb_token")
    assert cookie is not None
    # 带 cookie 访问面板成功
    resp2 = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie.value}))
    assert resp2.status == 200
    assert "配置管理" in await _resp_text(resp2)
    # 错误密码
    resp3 = await server._handle_panel_login(FakeRequest(form={"username": "admin", "password": "wrong"}))
    assert "用户名或密码错误" in await _resp_text(resp3)


async def test_panel_register_closed_via_form(webapp):
    """已初始化：面板注册页显示「注册已关闭」，不再有表单。"""
    server = webapp
    text = await _resp_text(await server._handle_panel_register_page(FakeRequest()))
    assert "注册已关闭" in text
    assert 'name="username"' not in text
    resp = await server._handle_panel_register(FakeRequest(
        form={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    assert "注册已关闭" in await _resp_text(resp)


async def test_panel_save_config_via_form(webapp):
    server = webapp
    resp = await server._handle_panel_login(FakeRequest(form={"username": "admin", "password": "secret123"}))
    cookie = resp.cookies.get("fb_token").value
    resp2 = await server._handle_panel_save(FakeRequest(
        cookies={"fb_token": cookie}, form={"key": "MAX_REPLY_LENGTH", "value": "55"}))
    assert resp2.status == 302
    assert server.config_service.repository.get_config("MAX_REPLY_LENGTH") == "55"


async def test_config_update_via_api(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_update_config(
        FakeRequest(headers={"Authorization": f"Bearer {token}"}, body={"key": "MAX_REPLY_LENGTH", "value": "55"}))
    assert resp.status == 200
    assert (await _resp_data(resp))["ok"] is True
    assert server.config_service.config.MAX_REPLY_LENGTH == 55  # 热更新


async def test_login_rate_limit(webapp):
    server = webapp
    for _ in range(5):
        await server._handle_login(FakeRequest(body={"username": "admin", "password": "wrong"}))
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 429  # 锁住


# ---------- 新增：状态页 / 日志页 API ----------
async def test_status_api(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_status(FakeRequest(headers={"Authorization": f"Bearer {token}"}))
    assert resp.status == 200
    data = await _resp_data(resp)
    assert "uptime_seconds" in data
    assert "metrics" in data
    assert "version" in data


async def test_status_unauthorized(webapp):
    server = webapp
    resp = await server._handle_status(FakeRequest())
    assert resp.status == 401


async def test_logs_api(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_logs(FakeRequest(headers={"Authorization": f"Bearer {token}"}))
    assert resp.status == 200
    data = await _resp_data(resp)
    assert isinstance(data.get("logs"), list)


async def test_status_provider_ws_connected(cs):
    """status_provider 注入的状态（如 WS 连接）出现在 /api/status。"""
    config, repo, service, tmp = cs
    config.WEB_UI_USERNAME = "admin"
    config.WEB_UI_PASSWORD = "secret123"
    server = WebUIServer(config, service, status_provider=lambda: {"ws_connected": True})
    token = (await _resp_data(await server._handle_login(
        FakeRequest(body={"username": "admin", "password": "secret123"}))))["token"]
    resp = await server._handle_status(FakeRequest(headers={"Authorization": f"Bearer {token}"}))
    data = await _resp_data(resp)
    assert data["ws_connected"] is True
    server._tokens.clear()
