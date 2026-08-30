"""插件 Web UI 面板测试（黑盒，零 JS 表单）：未认证拦截 / 扫描 / 上传 / URL / 启用 /
禁用 / 卸载 / 保护开关 / 页面渲染。"""
import io
import json
import os

from src.plugins.manager import PluginManager
from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from src.services.web_ui import WebUIServer
from tests.test_config_service import FakeSettings
from tests.test_web_ui import FakeRequest

TESTS_PLUGINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")


def _make(tmp_path, plugins_dir=None):
    repo = SettingsRepository(os.path.join(tmp_path, "settings.db"))
    cfg = FakeSettings(WEB_UI_USERNAME="admin", WEB_UI_PASSWORD="secret123")
    svc = ConfigService(cfg, repo)
    pdir = plugins_dir or os.path.join(tmp_path, "plugins")
    os.makedirs(pdir, exist_ok=True)
    cfg.PLUGIN_DIR = pdir
    sender = type("S", (), {"sent": []})
    sender.send_group_message = async_sender(sender)
    sender.send_private_message = async_sender(sender)
    mgr = PluginManager(cfg, repo, sender=sender)
    server = WebUIServer(cfg, svc, plugin_manager=mgr)
    return server, mgr, repo, cfg, tmp_path


def async_sender(sender):
    async def _send(group_id, message=None, **kw):
        sender.sent.append((group_id, message))
        return True
    return _send


def _copy_plugin(tmp_path, name):
    import shutil
    src = os.path.join(TESTS_PLUGINS, name)
    dst = os.path.join(tmp_path, "plugins", name)
    shutil.copytree(src, dst)


def _auth(token):
    return {"headers": {"Authorization": f"Bearer {token}"}}


async def _login(server):
    from tests.test_web_ui import _resp_data
    resp = await server._handle_login(FakeRequest(body={"username": "admin", "password": "secret123"}))
    return (await _resp_data(resp))["token"]


async def test_plugin_routes_require_auth(tmp_path):
    server, *_ = _make(tmp_path)
    form = {"protection": "unsafe"}
    resp = await server._handle_panel_plugins_protection(FakeRequest(form=form))
    assert resp.status == 302  # 未认证 → 重定向 /panel
    resp = await server._handle_panel_plugins_enable(FakeRequest(form={"id": "x"}))
    assert resp.status == 302


async def test_plugin_page_renders(tmp_path):
    server, *_ = _make(tmp_path)
    html = server._render_plugin_page()
    assert "插件保护措施" in html
    assert "插件列表" in html
    assert "上传并安装" in html
    assert "下载并安装" in html


async def test_refresh_discovers_disabled(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    _copy_plugin(tmp_path, "minimal_plugin")
    token = await _login(server)
    resp = await server._handle_panel_plugins_refresh(FakeRequest(**_auth(token)))
    assert resp.status == 302
    row = repo.get_plugin("minimal_plugin")
    assert row and row["enabled"] == 0


async def test_upload_install_disabled_by_default(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    token = await _login(server)
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({
            "id": "uploaded_plugin", "name": "Uploaded", "version": "1.0.0",
            "runtime": "python", "entry": "plugin.py", "api_version": "1",
            "permissions": ["read_message"]}))
        zf.writestr("plugin.py", "def on_message(event, api=None):\n    return {'type': 'test'}\n")
    data = buf.getvalue()

    class _File:
        def __init__(self, d):
            self._d = d
            self.filename = "plugin.zip"
            self.file = _Reader(d)

    class _Reader:
        def __init__(self, d):
            self._d = d

        def read(self, n=None):
            if n is None:
                return self._d
            return self._d[:n]

    form = {"plugin_file": _File(data)}
    resp = await server._handle_panel_plugins_upload(FakeRequest(form=form, **_auth(token)))
    assert resp.status == 302
    assert repo.get_plugin("uploaded_plugin")["enabled"] == 0  # 安装后默认禁用


async def test_enable_disable_uninstall_flow(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    _copy_plugin(tmp_path, "minimal_plugin")
    token = await _login(server)
    await server._handle_panel_plugins_refresh(FakeRequest(**_auth(token)))
    resp = await server._handle_panel_plugins_enable(
        FakeRequest(form={"id": "minimal_plugin", "perm": ["read_message"]}, **_auth(token)))
    assert resp.status == 302
    assert repo.get_plugin("minimal_plugin")["enabled"] == 1
    resp = await server._handle_panel_plugins_disable(FakeRequest(form={"id": "minimal_plugin"}, **_auth(token)))
    assert resp.status == 302
    assert repo.get_plugin("minimal_plugin")["enabled"] == 0
    resp = await server._handle_panel_plugins_uninstall(FakeRequest(form={"id": "minimal_plugin"}, **_auth(token)))
    assert resp.status == 302
    assert repo.get_plugin("minimal_plugin") is None


async def test_protection_switch_via_panel(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    token = await _login(server)
    resp = await server._handle_panel_plugins_protection(
        FakeRequest(form={"protection": "unsafe"}, **_auth(token)))
    assert resp.status == 302
    assert cfg.PLUGIN_PROTECTION == "unsafe"
    assert mgr._protection_level() == "unsafe"
    resp = await server._handle_panel_plugins_protection(
        FakeRequest(form={"protection": "bogus"}, **_auth(token)))
    assert resp.status == 302
    assert cfg.PLUGIN_PROTECTION == "unsafe"  # 非法值不生效


async def test_plugin_config_save(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    token = await _login(server)
    resp = await server._handle_panel_plugins_config(FakeRequest(form={
        "PLUGIN_URL_MAX_BYTES": "999999", "PLUGIN_ZIP_MAX_FILES": "42"}, **_auth(token)))
    assert resp.status == 302
    assert cfg.PLUGIN_URL_MAX_BYTES == 999999
    assert cfg.PLUGIN_ZIP_MAX_FILES == 42


async def test_upload_rejects_oversize(tmp_path):
    server, mgr, repo, cfg, tmp = _make(tmp_path)
    token = await _login(server)
    big = b"x" * (7 * 1024 * 1024)

    class _File:
        filename = "big.zip"
        file = type("R", (), {"read": lambda self, n=None: big})()

    from urllib.parse import unquote
    resp = await server._handle_panel_plugins_upload(
        FakeRequest(form={"plugin_file": _File()}, **_auth(token)))
    assert resp.status == 302
    location = unquote(resp.headers.get("Location", ""))
    assert "err=1" in location
    assert "大小上限" in location
