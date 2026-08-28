"""Web UI 与 Config Service 测试（第四轮）：认证/脱敏/持久化/热更新/端口校验。"""
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
    """最小 request 桩：headers / remote / json()。"""

    def __init__(self, headers=None, remote="127.0.0.1", body=None):
        self.headers = headers or {}
        self.remote = remote
        self._body = body or {}

    async def json(self):
        return self._body


@pytest.fixture()
def webapp(cs):
    config, repo, service, tmp = cs
    config.WEB_UI_USERNAME = "admin"
    config.WEB_UI_PASSWORD = "secret123"
    config.WEB_UI_TOKEN_TTL_SECONDS = 3600
    server = WebUIServer(config, service)
    yield server
    server._tokens.clear()


async def _login(server):
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    data = await resp.json()
    return data["token"]


async def test_login_and_unauthorized(webapp):
    server = webapp
    resp = await server._handle_get_config(FakeRequest())
    assert resp.status == 401  # 未认证
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "wrong"}))
    assert resp.status == 401
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 200
    assert "token" in await resp.json()


async def test_config_read_with_auth(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_get_config(FakeRequest(headers={"Authorization": f"Bearer {token}"}))
    assert resp.status == 200
    keys = [c["key"] for c in (await resp.json())["configs"]]
    assert "DEEPSEEK_API_KEY" in keys
    assert "MAX_REPLY_LENGTH" in keys


async def test_config_update_via_api(webapp):
    server = webapp
    token = await _login(server)
    resp = await server._handle_update_config(
        FakeRequest(headers={"Authorization": f"Bearer {token}"}, body={"key": "MAX_REPLY_LENGTH", "value": "55"}))
    assert resp.status == 200
    assert (await resp.json())["ok"] is True
    assert server.config_service.config.MAX_REPLY_LENGTH == 55  # 热更新


async def test_login_rate_limit(webapp):
    server = webapp
    for _ in range(5):
        await server._handle_login(FakeRequest(body={"username": "admin", "password": "wrong"}))
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 429  # 锁住
