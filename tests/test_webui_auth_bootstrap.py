"""Web UI 注册 Bootstrap Lock 黑盒测试（requirement 13：模拟攻击者，禁止绕过）。

- 未初始化：允许创建第一个管理员
- 已初始化：公开注册永久关闭（POST /register → 403；面板注册页显示关闭）
- 攻击者注册新账号 → 必须 403；登录攻击者账号 → 必须 401
- 并发注册（race）：两个请求只有一个成功
- 注销重置必须管理员密码验证（未登录/错误密码不可重置）
- 修改账号走登录态 /panel/account/credentials（需当前密码）
"""
import asyncio

from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from src.services.web_ui import WebUIServer
from tests.test_config_service import FakeSettings
from tests.test_web_ui import FakeRequest, _resp_data, _resp_text


def _make(cfg_over=None):
    import tempfile
    tmp = tempfile.TemporaryDirectory()
    repo = SettingsRepository(tmp.name + "/s.db")
    cfg = FakeSettings(WEB_UI_USERNAME="admin", WEB_UI_PASSWORD="")
    for k, v in (cfg_over or {}).items():
        setattr(cfg, k, v)
    svc = ConfigService(cfg, repo)
    server = WebUIServer(cfg, svc)
    return server, repo, cfg, tmp


# ---------- 未初始化 → 允许首次注册 ----------
async def test_first_bootstrap_register_success():
    server, repo, cfg, tmp = _make()
    assert server.config_service.admin_initialized() is False
    resp = await server._handle_register(FakeRequest(
        body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    assert server.config_service.admin_initialized() is True
    # 注册后登录成功
    resp = await server._handle_login(FakeRequest(body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    tmp.cleanup()


# ---------- 已初始化 → 公开注册永久关闭（核心绕过测试） ----------
async def test_second_admin_rejected_after_bootstrap():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    assert server.config_service.admin_initialized() is True
    # 攻击者不知道管理员密码：直接注册新账号 → 403（不再提供任何注册机会）
    resp = await server._handle_register(FakeRequest(
        body={"username": "attacker", "password": "xxx123456"}))
    assert resp.status == 403
    # 尝试带 admin_password 也不行（公开注册已永久关闭）
    resp = await server._handle_register(FakeRequest(
        body={"username": "attacker", "password": "xxx123456", "admin_password": "secret123"}))
    assert resp.status == 403
    # 攻击者登录新账号 → 401（绕不过去）
    resp = await server._handle_login(FakeRequest(body={"username": "attacker", "password": "xxx123456"}))
    assert resp.status == 401
    # 原管理员仍可登录
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 200
    tmp.cleanup()


async def test_registration_disabled_after_bootstrap():
    """Bootstrap 语义：一旦初始化，即使 .env 凭据后来被删（异常状态），也不重开注册。"""
    server, repo, cfg, tmp = _make()
    assert server.config_service.admin_initialized() is False
    await server._handle_register(FakeRequest(
        body={"username": "boss", "password": "newpass123"}))
    # 模拟非法状态：直接删掉注册表凭据
    repo.delete_config("WEB_UI_USERNAME")
    repo.delete_config("WEB_UI_PASSWORD")
    resp = await server._handle_register(FakeRequest(
        body={"username": "hacker", "password": "newpass123"}))
    # admin_bootstrap 已置 initialized：注册仍然关闭（CAS 状态持久，不因凭据删除而重开）
    assert resp.status == 403
    tmp.cleanup()


async def test_registration_race_only_one_succeeds():
    """两个并发 /register：原子 CAS 保证只有一个成功。"""
    server, repo, cfg, tmp = _make()

    async def try_register(name):
        return await server._handle_register(FakeRequest(
            body={"username": name, "password": "newpass123"}))

    results = await asyncio.gather(try_register("alpha"), try_register("beta"))
    statuses = sorted(r.status for r in results)
    # 一个成功（200），另一个要么 CAS 冲突（400）要么注册已关闭（403）
    assert statuses[0] == 200 and statuses[1] in (400, 403)
    # 只创建了一个管理员凭据
    assert server.config_service.admin_initialized() is True
    tmp.cleanup()


# ---------- 面板注册页 ----------
async def test_panel_register_page_closed_when_initialized():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    resp = await server._handle_panel_register_page(FakeRequest())
    text = await _resp_text(resp)
    assert "注册已关闭" in text
    assert 'name="username"' not in text  # 无表单可提交（防绕过）
    # POST /panel/register 也拒绝
    resp = await server._handle_panel_register(FakeRequest(form={
        "username": "attacker", "password": "xxx123456"}))
    text = await _resp_text(resp)
    assert "注册已关闭" in text
    tmp.cleanup()


# ---------- 注销重置必须管理员（密码验证；未登录不能重置） ----------
async def test_reset_requires_admin_authentication():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    # 未登录（无 token）→ 重定向，不能重置
    resp = await server._handle_panel_unregister(FakeRequest(form={"password": "secret123"}))
    assert resp.status == 302
    # 登录后但是错误密码 → 拒绝
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    token = (await _resp_data(resp))["token"]
    resp = await server._handle_panel_unregister(FakeRequest(
        cookies={"fb_token": token}, form={"password": "wrong"}))
    assert resp.status == 302
    assert "err=1" in resp.headers.get("Location", "")
    # 正确密码 + 登录态 → 注销成功（回到 UNINITIALIZED，重新允许首次注册）
    resp = await server._handle_panel_unregister(FakeRequest(
        cookies={"fb_token": token}, form={"password": "secret123"}))
    assert resp.status == 302
    assert server.config_service.admin_initialized() is False
    # 重新注册（显式重置后的合法 bootstrap）
    resp = await server._handle_register(FakeRequest(
        body={"username": "reborn", "password": "newpass123"}))
    assert resp.status == 200
    tmp.cleanup()


# ---------- 登录态修改账号（唯一改密路径） ----------
async def test_change_credentials_requires_auth_and_current_password():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    # 未登录 → 重定向
    resp = await server._handle_panel_change_credentials(FakeRequest(form={
        "username": "x", "password": "newpass123", "current_password": "secret123"}))
    assert resp.status == 302
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    token = (await _resp_data(resp))["token"]
    # 错误当前密码 → 拒绝
    resp = await server._handle_panel_change_credentials(FakeRequest(
        cookies={"fb_token": token},
        form={"username": "boss", "password": "newpass123", "current_password": "wrong"}))
    assert "err=1" in resp.headers.get("Location", "")
    # 正确 → 成功，新账号登录、旧账号失效
    resp = await server._handle_panel_change_credentials(FakeRequest(
        cookies={"fb_token": token},
        form={"username": "boss", "password": "newpass123", "current_password": "secret123"}))
    assert resp.status == 302
    resp = await server._handle_login(FakeRequest(body={"username": "boss", "password": "newpass123"}))
    assert resp.status == 200
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    assert resp.status == 401
    tmp.cleanup()


# ---------- 迁移兼容：已有数据自动视为 INITIALIZED ----------
async def test_legacy_data_migrated_to_initialized():
    """历史版本已有的注册账号（明文/哈希 .env 或 db）→ 升级后不开放注册。"""
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "legacy123"})
    assert server.config_service.admin_initialized() is True
    resp = await server._handle_register(FakeRequest(
        body={"username": "attacker", "password": "xxx123456"}))
    assert resp.status == 403
    tmp.cleanup()


# ---------- 改密/注销后旧会话必须失效（黑盒：token 撤销） ----------
async def test_change_credentials_invalidates_old_session():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    resp = await server._handle_login(
        FakeRequest(body={"username": "admin", "password": "secret123"}))
    token = (await _resp_data(resp))["token"]
    # 改密码成功后：旧 token 调用受保护接口 → 重定向（会话已撤销）
    resp = await server._handle_panel_change_credentials(FakeRequest(
        cookies={"fb_token": token},
        form={"username": "admin", "password": "newpass456", "current_password": "secret123"}))
    assert resp.status == 302 and "不正确" not in resp.headers.get("Location", "")
    resp = await server._handle_panel(FakeRequest(cookies={"fb_token": token}))
    assert resp.status == 200  # 旧 token 失效 → 渲染登录页（未认证语义）
    assert "登录" in await _resp_text(resp)
    # 新凭据可登录
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "newpass456"}))
    assert resp.status == 200
    tmp.cleanup()


async def test_unregister_invalidates_all_sessions():
    server, repo, cfg, tmp = _make(cfg_over={"WEB_UI_PASSWORD": "secret123"})
    resp = await server._handle_login(
        FakeRequest(body={"username": "admin", "password": "secret123"}))
    token = (await _resp_data(resp))["token"]
    resp = await server._handle_panel_unregister(
        FakeRequest(cookies={"fb_token": token}, form={"password": "secret123"}))
    assert resp.status == 302
    # 注销后旧 token 立即失效
    resp = await server._handle_panel(FakeRequest(cookies={"fb_token": token}))
    assert resp.status == 200  # 渲染登录页（未认证语义）
    assert "登录" in await _resp_text(resp)
    tmp.cleanup()
