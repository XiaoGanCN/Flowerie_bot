"""PluginManager 黑盒测试：发现≠执行 / 启用批准权限 / 权限拒绝 / 事件分发 /
崩溃隔离 / 超时 / 卸载 / 声明式 JSON 插件 / 保护开关。"""
import asyncio
import json
import os
import shutil

import pytest

from src.plugins.manager import PluginManager
from src.repositories.settings_repository import SettingsRepository

TESTS_PLUGINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send_group_message(self, group_id, message=None, **kw):
        self.sent.append(("group", group_id, message))
        return True

    async def send_private_message(self, user_id, message=None, **kw):
        self.sent.append(("private", user_id, message))
        return True


class FakeConfig:
    PLUGIN_DIR = "./plugins"
    PLUGIN_PROTECTION = "normal"
    PLUGIN_MAX_COUNT = 100
    PLUGIN_URL_MAX_BYTES = 5242880
    PLUGIN_URL_TIMEOUT = 15
    PLUGIN_ZIP_MAX_UNZIPPED_BYTES = 52428800
    PLUGIN_ZIP_MAX_FILES = 200


@pytest.fixture()
async def env(tmp_path):
    repo = SettingsRepository(os.path.join(tmp_path, "settings.db"))
    sender = FakeSender()
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    config = FakeConfig()
    config.PLUGIN_DIR = str(plugin_dir)
    mgr = PluginManager(config, repo, sender=sender)
    yield mgr, repo, sender, tmp_path
    await mgr.shutdown()  # 结束所有子进程（防残留）


def _deploy(tmp_path, name):
    src = os.path.join(TESTS_PLUGINS, name)
    dst = tmp_path / "plugins" / name
    shutil.copytree(src, dst)


# ---------- 发现 ≠ 自动执行 ----------
@pytest.mark.asyncio
async def test_discover_registers_disabled(env):
    mgr, repo, _s, tmp = env
    _deploy(tmp, "minimal_plugin")
    discovered = mgr.discover()
    assert discovered == ["minimal_plugin"]
    row = repo.get_plugin("minimal_plugin")
    assert row["enabled"] == 0
    assert row["status"] == "discovered"
    # 未启用则不投递事件
    summary = await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
    assert summary == []


# ---------- 启用 + 事件投递 + action 执行（test action 无副作用、经权限门） ----------
@pytest.mark.asyncio
async def test_enable_and_event_dispatch(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("minimal_plugin", approved_permissions=["read_message"])
    assert ok, msg
    row = repo.get_plugin("minimal_plugin")
    assert row["enabled"] == 1
    summary = await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
    # 插件返回 test action（无副作用），manager 捕获并验证权限（test 无需权限）
    assert summary and summary[0]["action"] == "test"
    assert summary[0]["ok"] is True
    assert sender.sent == []


# ---------- 权限拒绝（运行时强制，不是提示文字） ----------
@pytest.mark.asyncio
async def test_permission_denied_at_runtime(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "declarative_plugin")
    mgr.discover()
    # 只批准 send_message，未批准 http_request：声明式插件直接内置 action 执行
    ok, msg = await mgr.enable("declarative_plugin",
                               approved_permissions=["send_message", "read_message"])
    assert ok, msg
    summary = await mgr.dispatch_event("message", {"group_id": 7, "user_id": 9, "text": "hello world"})
    # 匹配规则 → send_message 执行成功
    assert any(s["action"] == "send_message" and s["ok"] for s in summary)
    assert sender.sent and sender.sent[0] == ("group", 7, "你好 9")
    # 未批准 http_request → 强制拒绝（即使转发给 _execute_action 也一样）
    denied = await mgr._execute_action("declarative_plugin", {"type": "http_request", "payload": {}})
    assert denied[0]["denied"] is True and denied[0]["ok"] is False


# ---------- 未批准 read_message 则不投递事件 ----------
@pytest.mark.asyncio
async def test_event_not_delivered_without_read_permission(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    await mgr.enable("minimal_plugin", approved_permissions=["send_message"])  # 无 read_message
    summary = await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
    assert summary == []


# ---------- 崩溃隔离：一个插件崩不影响其他插件 ----------
@pytest.mark.asyncio
async def test_plugin_crash_isolated_from_others(env):
    mgr, repo, sender, tmp = env
    # 崩溃插件
    crash = tmp / "plugins" / "crash_plugin"
    crash.mkdir()
    (crash / "manifest.json").write_text(json.dumps({
        "id": "crash_plugin", "name": "Crash", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": ["read_message"]}), encoding="utf-8")
    (crash / "plugin.py").write_text("import os\n"
                                     "def on_message(event, api=None):\n    os._exit(1)\n",
                                     encoding="utf-8")
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    # 加快崩溃检测：缩短事件超时
    await mgr.enable("minimal_plugin", approved_permissions=["read_message"])
    await mgr.enable("crash_plugin", approved_permissions=["read_message"])
    rt_crash = mgr._runtimes["crash_plugin"]
    rt_crash._limits["event_timeout"] = 1.0
    # 崩溃插件的 dispatch 异常被 Manager 隔离（不向外抛；Flowerie 继续）
    await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "x"})
    # 等 crash 标记
    for _ in range(40):
        if mgr.get_plugin("crash_plugin")["status"] in ("crashed",):
            break
        await asyncio.sleep(0.05)
    assert mgr.get_plugin("crash_plugin")["status"] == "crashed"
    # 另一个插件仍然健康可以投递
    summary = await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
    assert summary and any(s["plugin"] == "minimal_plugin" for s in summary)


# ---------- 超时：超时插件被终止（Flowerie 继续） ----------
@pytest.mark.asyncio
async def test_plugin_timeout_marked_crashed(env):
    mgr, repo, sender, tmp = env
    slow = tmp / "plugins" / "slow_plugin"
    slow.mkdir()
    (slow / "manifest.json").write_text(json.dumps({
        "id": "slow_plugin", "name": "Slow", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": ["read_message"]}), encoding="utf-8")
    (slow / "plugin.py").write_text("import time\n"
                                    "def on_message(event, api=None):\n    time.sleep(30)\n",
                                    encoding="utf-8")
    mgr.discover()
    await mgr.enable("slow_plugin", approved_permissions=["read_message"])
    mgr._runtimes["slow_plugin"]._limits["event_timeout"] = 1.0
    await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "x"})
    row = mgr.get_plugin("slow_plugin")
    assert row["status"] in ("crashed", "error")


# ---------- 启用时权限校验 ----------
@pytest.mark.asyncio
async def test_enable_requires_approved_permissions(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("minimal_plugin", approved_permissions=[])
    assert not ok and "至少批准" in msg
    # 批准未声明的权限 → 拒绝
    ok, msg = await mgr.enable("minimal_plugin", approved_permissions=["execute_process"])
    assert not ok and "未声明" in msg
    # 不存在 → 拒绝
    ok, msg = await mgr.enable("ghost_plugin", approved_permissions=[])
    assert not ok


# ---------- 禁用与卸载 ----------
@pytest.mark.asyncio
async def test_disable_and_uninstall(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    await mgr.enable("minimal_plugin", approved_permissions=["read_message"])
    ok, msg = mgr.disable("minimal_plugin")
    assert ok
    assert repo.get_plugin("minimal_plugin")["enabled"] == 0
    ok, msg = mgr.uninstall("minimal_plugin")
    assert ok
    assert repo.get_plugin("minimal_plugin") is None
    assert not (tmp / "plugins" / "minimal_plugin").exists()


# ---------- 保护级别（开关） ----------
def test_protection_switch(env):
    mgr, _repo, _s, _tmp = env
    ok, msg = mgr.set_protection("unsafe")
    assert ok
    assert mgr._protection_level() == "unsafe"
    ok, msg = mgr.set_protection("bogus")
    assert not ok


# ---------- 声明式 JSON 插件（无代码执行） ----------
@pytest.mark.asyncio
async def test_declarative_plugin_greeting(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "declarative_plugin")
    mgr.discover()
    await mgr.enable("declarative_plugin", approved_permissions=["send_message", "read_message"])
    summary = await mgr.dispatch_event("message", {"group_id": 42, "user_id": 100, "text": "hello there"})
    assert summary and any(s["action"] == "send_message" and s["ok"] for s in summary)
    assert sender.sent[0] == ("group", 42, "你好 100")
    # 不匹配前缀 → 无动作
    summary = await mgr.dispatch_event("message", {"group_id": 42, "user_id": 100, "text": "bye"})
    assert summary == []


# ---------- 插件目录自动发现 + 刷新 ----------
@pytest.mark.asyncio
async def test_refresh_discovers_and_updates(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    discovered, changed = mgr.refresh()
    assert discovered == ["minimal_plugin"]
    # manifest 变更 → refresh 同步 + 停旧运行时
    manifest_path = tmp / "plugins" / "minimal_plugin" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["version"] = "2.0.0"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    discovered, changed = mgr.refresh()
    assert changed == ["minimal_plugin"]
    assert json.loads(repo.get_plugin("minimal_plugin")["manifest_json"])["version"] == "2.0.0"
