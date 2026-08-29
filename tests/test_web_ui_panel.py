"""Web UI 无 JS 面板测试：全量配置表单 / 分组保存 / 主题 / 背景颜色/图片 / 图片安全。

覆盖任务要求：
- 全部配置变量出现在 Web UI
- bool（未勾选=false）、int、string、secret、textarea 保存
- .env 持久化（保存后真实写入）
- 非法值拒绝且 .env 不变
- 主题切换 / 自定义颜色 / 恢复默认
- 图片上传校验（合法/非法/超大/路径穿越）+ 删除 + 持久化
"""
import io
import os
import tempfile

from aiohttp import web

from src.repositories.env_store import EnvFileStore
from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from src.services.web_ui import MAX_UPLOAD_BYTES, WebUIServer
from tests.test_config_service import FakeSettings

PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
HTML_BODY = b"<html><body>hi</body></html>"


class FakeMulti(dict):
    """模拟 aiohttp MultiDictProxy：支持同名多值（hidden false + checkbox true）。"""

    def __init__(self, items):
        super().__init__()
        self._items = list(items)

    def keys(self):
        return [k for k, _ in self._items]

    def __contains__(self, k):
        return any(kk == k for kk, _ in self._items)

    def get(self, k, default=None):
        vals = [v for kk, v in self._items if kk == k]
        return vals[-1] if vals else default

    def getall(self, k):
        return [v for kk, v in self._items if kk == k]


class FakeRequest:
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


def _resp_text(resp):
    t = getattr(resp, "text", None)
    if isinstance(t, str):
        return t
    body = getattr(resp, "body", b"")
    if isinstance(body, bytes):
        return body.decode("utf-8", "replace")
    return str(body or "")


def _make_stack(tmp):
    repo = SettingsRepository(os.path.join(tmp, "settings.db"))
    config = FakeSettings(DEEPSEEK_API_KEY="sk-secret-key-1234567890")
    config.WEB_UI_USERNAME = "admin"
    config.WEB_UI_PASSWORD = "secret123"
    config.WEB_UI_TOKEN_TTL_SECONDS = 3600
    svc = ConfigService(config, repo, env_path=os.path.join(tmp, ".env"))
    server = WebUIServer(config, svc, data_dir=os.path.join(tmp, "webui"))
    return config, repo, svc, server


async def _login(server):
    resp = await server._handle_panel_login(FakeRequest(form={"username": "admin", "password": "secret123"}))
    assert resp.status == 302
    return resp.cookies.get("fb_token").value


# ---------- 全量配置出现在面板 ----------
async def test_panel_contains_all_config_keys():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}))
        text = _resp_text(resp)
        for key in ConfigService.SCHEMA.keys():
            assert f'name="{key}"' in text, f"面板缺少配置项 {key}"
        assert "保存本组" in text  # 分组保存按钮


async def test_panel_secret_masked():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}))
        text = _resp_text(resp)
        assert "sk-secret-key-1234567890" not in text  # 明文不泄漏
        assert "sk-s****7890" in text  # 掩码


async def test_panel_group_save_writes_env_and_db():
    with tempfile.TemporaryDirectory() as td:
        _, repo, svc, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("BOT_NICKNAME", "小璃"),
            ("MAX_REPLY_LENGTH", "77"),
            ("ONLY_REPLY_WHEN_AT", "false"),
            ("ONLY_REPLY_WHEN_AT", "true"),  # checkbox 勾选 → 取最后值
        ])
        resp = await server._handle_panel_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert svc.config.BOT_NICKNAME == "小璃"
        assert svc.config.MAX_REPLY_LENGTH == 77
        assert svc.config.ONLY_REPLY_WHEN_AT is True
        assert repo.get_config("MAX_REPLY_LENGTH") == "77"
        env_vals = EnvFileStore(os.path.join(td, ".env")).read_values()
        assert env_vals["MAX_REPLY_LENGTH"] == "77"
        assert env_vals["BOT_NICKNAME"] == "小璃"
        assert env_vals["ONLY_REPLY_WHEN_AT"] == "true"


async def test_panel_checkbox_unchecked_is_false():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server = _make_stack(td)
        cookie = await _login(server)
        # 未勾选 → 只提交 hidden false
        form = FakeMulti([("ONLY_REPLY_WHEN_AT", "false")])
        await server._handle_panel_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert svc.config.ONLY_REPLY_WHEN_AT is False
        assert open(os.path.join(td, ".env"), encoding="utf-8").read().split("ONLY_REPLY_WHEN_AT=")[1].split("\n")[0] == "false"


async def test_panel_invalid_rejected_env_untouched():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, ".env"), "w", encoding="utf-8") as f:
            f.write("MAX_REPLY_LENGTH=40\n")
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([("MAX_REPLY_LENGTH", "abc"), ("BOT_NICKNAME", "新昵称")])
        resp = await server._handle_panel_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err=1" in str(resp.headers.get("Location", ""))
        env_text = open(os.path.join(td, ".env"), encoding="utf-8").read()
        assert "MAX_REPLY_LENGTH=40" in env_text  # .env 未动
        assert "新昵称" not in env_text


# ---------- 主题 / 背景 ----------
async def test_appearance_theme_save_and_render():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([("theme", "sakura"), ("bg_color", "#ff7eb3"),
                          ("bg_image_opacity", "70"), ("bg_size", "cover"), ("bg_position", "center")])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert repo.get_pref("theme") == "sakura"
        assert repo.get_pref("bg_color") == "#ff7eb3"
        assert repo.get_pref("bg_image_opacity") == "70"
        # 渲染：body class + 选中态 + 颜色
        resp2 = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}, query={"tab": "appearance"}))
        text = _resp_text(resp2)
        assert 'class="theme-sakura"' in text
        assert 'value="sakura" checked' in text
        assert "background-color: #ff7eb3" in text


async def test_appearance_persists_across_restart():
    """主题/颜色存 settings.db：新 WebUIServer（模拟重启）仍读到。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([("theme", "ocean"), ("bg_color", "#0ea5e9"),
                          ("bg_image_opacity", "50"), ("bg_size", "contain"), ("bg_position", "top")])
        await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        # 模拟重启：同 settings.db + 同 data 目录的新 server
        repo2 = SettingsRepository(os.path.join(td, "settings.db"))
        config2 = FakeSettings()
        config2.WEB_UI_USERNAME = "admin"
        config2.WEB_UI_PASSWORD = "secret123"
        server2 = WebUIServer(config2, ConfigService(config2, repo2), data_dir=os.path.join(td, "webui"))
        cookie2 = await _login(server2)
        resp = await server2._handle_panel(FakeRequest(cookies={"fb_token": cookie2}, query={"tab": "appearance"}))
        text = _resp_text(resp)
        assert 'class="theme-ocean"' in text
        assert "background-color: #0ea5e9" in text
        repo2.close()


async def test_appearance_invalid_color_rejected():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([("theme", "sakura"), ("bg_color", "not-a-color"),
                          ("bg_image_opacity", "100"), ("bg_size", "cover"), ("bg_position", "center")])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err=1" in str(resp.headers.get("Location", ""))
        assert repo.get_pref("bg_color") is None  # 未保存


async def test_appearance_restore_default():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([("theme", "amoled"), ("bg_color", "#000000"),
                          ("bg_image_opacity", "10"), ("bg_size", "contain"), ("bg_position", "bottom")])
        await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        resp = await server._handle_panel_appearance_restore(FakeRequest(cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert repo.get_pref("theme") == "default"
        assert repo.get_pref("bg_color") == ""
        assert repo.get_pref("bg_image_opacity") == "100"
        assert repo.get_pref("bg_size") == "cover"


# ---------- 背景图片上传与安全 ----------
def _file_field(data: bytes, filename: str = "bg.png"):
    return web.FileField("bg_image", filename, io.BytesIO(data), "image/png")


async def test_upload_valid_png():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "80"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(PNG_HEAD)),
        ])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err" not in str(resp.headers.get("Location", ""))
        saved = os.path.join(td, "webui", "background", "background.png")
        assert os.path.isfile(saved)
        assert repo.get_pref("bg_image") == "background.png"
        # 渲染时引用图片 URL
        resp2 = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}, query={"tab": "appearance"}))
        assert "/panel/background" in _resp_text(resp2)


async def test_upload_rejects_html():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(HTML_BODY, "evil.html")),
        ])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err=1" in str(resp.headers.get("Location", ""))
        assert repo.get_pref("bg_image") in (None, "")
        assert not os.path.exists(os.path.join(td, "webui", "background", "background.html"))


async def test_upload_rejects_oversize():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        big = PNG_HEAD + b"\x00" * (MAX_UPLOAD_BYTES + 1)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(big, "big.png")),
        ])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err=1" in str(resp.headers.get("Location", ""))


async def test_upload_rejects_wrong_extension():
    """扩展名合法但内容是脚本 → 拒绝（魔数校验，不信扩展名）。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(b"#!/bin/sh\nrm -rf /\n", "script.png")),
        ])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err=1" in str(resp.headers.get("Location", ""))


async def test_upload_path_traversal_contained():
    """用户文件名（含 ../）绝不用于落盘：固定文件名保存在 background/ 内。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(PNG_HEAD, "../../../evil.png")),
        ])
        resp = await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert resp.status == 302
        assert "err" not in str(resp.headers.get("Location", ""))
        # 文件只出现在持久化目录内，且是固定文件名
        assert os.path.isfile(os.path.join(td, "webui", "background", "background.png"))
        assert not os.path.exists(os.path.join(td, "evil.png"))
        assert not os.path.exists(os.path.join(td, "webui", "evil.png"))
        for root, _dirs, files in os.walk(td):
            for f in files:
                assert not f.startswith("evil")


async def test_serve_background_image():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(PNG_HEAD, "bg.png")),
        ])
        await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        resp = await server._handle_panel_background(FakeRequest(cookies={"fb_token": cookie}))
        assert resp.status == 200
        assert resp.content_type == "image/png"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        # 未认证 → 403
        resp2 = await server._handle_panel_background(FakeRequest())
        assert resp2.status == 403


async def test_delete_background_image():
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(PNG_HEAD, "bg.png")),
        ])
        await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        assert os.path.isfile(os.path.join(td, "webui", "background", "background.png"))
        resp = await server._handle_panel_appearance_delete_image(FakeRequest(cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert repo.get_pref("bg_image") == ""
        assert not os.path.exists(os.path.join(td, "webui", "background", "background.png"))


async def test_background_image_survives_restart():
    """图片落盘持久化：新 server 实例（模拟重启）仍能读取/服务。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server = _make_stack(td)
        cookie = await _login(server)
        form = FakeMulti([
            ("theme", "default"), ("bg_color", "#1e2229"), ("bg_image_opacity", "100"),
            ("bg_size", "cover"), ("bg_position", "center"),
            ("bg_image", _file_field(PNG_HEAD, "bg.png")),
        ])
        await server._handle_panel_appearance_save(FakeRequest(cookies={"fb_token": cookie}, form=form))
        # 新 server
        repo2 = SettingsRepository(os.path.join(td, "settings.db"))
        config2 = FakeSettings()
        config2.WEB_UI_USERNAME = "admin"
        config2.WEB_UI_PASSWORD = "secret123"
        server2 = WebUIServer(config2, ConfigService(config2, repo2), data_dir=os.path.join(td, "webui"))
        cookie2 = await _login(server2)
        resp = await server2._handle_panel_background(FakeRequest(cookies={"fb_token": cookie2}))
        assert resp.status == 200
        assert resp.content_type == "image/png"
        repo2.close()


async def test_old_single_key_form_still_works():
    """兼容旧版单键表单（key/value）。"""
    with tempfile.TemporaryDirectory() as td:
        _, repo, _, server = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel_save(FakeRequest(
            cookies={"fb_token": cookie}, form=FakeMulti([("key", "MAX_REPLY_LENGTH"), ("value", "55")])))
        assert resp.status == 302
        assert repo.get_config("MAX_REPLY_LENGTH") == "55"
