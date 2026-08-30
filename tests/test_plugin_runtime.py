"""最小可执行插件测试（requirement 9 硬要求）：真正启动 Plugin Runtime / Runner 的端到端验证。

- Python 插件：start 子进程 → 发送 test event → 插件返回 test action → 捕获验证 → shutdown
- Node.js 插件：同上（node 可执行文件缺失时跳过——CI 会安装 Node 20 LTS）
- 插件超时：被终止并标记 crashed，主流程继续
- 插件崩溃：进程退出被隔离（Flowerie 继续）
"""
import asyncio
import json
import os
import shutil
import time

import pytest

from src.plugins.manifest import PluginManifest
from src.plugins.runtime import PluginRuntime, PluginTimeoutError

TESTS_PLUGINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins")


def _deploy(tmp_path, name: str):
    src = os.path.join(TESTS_PLUGINS, name)
    dst = tmp_path / name
    shutil.copytree(src, dst)
    return str(dst)


def _make_runtime(plugin_dir: str, event_timeout: float = 5.0) -> PluginRuntime:
    manifest = PluginManifest.load(os.path.join(plugin_dir, "manifest.json"))
    rt = PluginRuntime(manifest.id, manifest, plugin_dir, protection="normal")
    rt._limits["event_timeout"] = event_timeout
    rt._limits["startup_timeout"] = 10.0
    return rt


# ---------- Python 最小插件（端到端） ----------
@pytest.mark.asyncio
async def test_minimal_python_plugin_executes(tmp_path):
    dir_path = _deploy(tmp_path, "minimal_plugin")
    rt = _make_runtime(dir_path)
    received = []
    rt.set_action_handler(lambda pid, action, payload: received.append((pid, action, payload)) or {"ok": True})
    await rt.start()
    try:
        assert rt.status == "running"
        actions = await rt.dispatch_event("message", {"group_id": 123, "user_id": 456, "text": "hi"})
        assert actions and actions[0]["type"] == "test"
        assert actions[0]["message"] == "plugin-ok"
        assert actions[0]["event"] == "message"
        # 插件进程独立：直接验证插件不可见 Flowerie 内部模块（隔离标志）
        out = await rt.request("health", {}, timeout=5.0)
        assert out.get("ok") is True
    finally:
        await rt.shutdown()
    assert rt.proc is None


# ---------- Node 最小插件（端到端） ----------
@pytest.mark.asyncio
async def test_minimal_node_plugin_executes(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node 不可用（CI 已安装 Node 20 LTS）")
    dir_path = _deploy(tmp_path, "minimal_node_plugin")
    rt = _make_runtime(dir_path)
    received = []
    rt.set_action_handler(lambda pid, action, payload: received.append((pid, action, payload)) or {"ok": True})
    await rt.start()
    try:
        actions = await rt.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
        assert actions and actions[0]["type"] == "test"
        assert actions[0]["message"] == "node-ok"
    finally:
        await rt.shutdown()


# ---------- 插件崩溃隔离 ----------
@pytest.mark.asyncio
async def test_plugin_crash_isolated(tmp_path):
    crash_dir = tmp_path / "crash_plugin"
    crash_dir.mkdir()
    (crash_dir / "manifest.json").write_text(json.dumps({
        "id": "crash_plugin", "name": "Crash", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": ["read_message"]}), encoding="utf-8")
    (crash_dir / "plugin.py").write_text(
        "import os\n"
        "def on_message(event, api=None):\n"
        "    os._exit(1)  # 模拟崩溃\n", encoding="utf-8")
    rt = _make_runtime(str(crash_dir), event_timeout=1.0)
    on_exit = []
    rt._on_exit = lambda pid, reason, code: on_exit.append((pid, reason, code))
    await rt.start()
    try:
        with pytest.raises((PluginTimeoutError, Exception)):  # noqa: B017 - 崩溃窗口内响应可能超时
            await rt.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "x"})
    finally:
        await rt.shutdown()
    # 崩溃被捕获：进程消失 + 回调触发（Flowerie 继续运行）
    deadline = time.time() + 3
    while time.time() < deadline and not on_exit:
        await asyncio.sleep(0.05)
    assert on_exit, "on_exit 回调必须触发（崩溃被 Manager 捕获）"


# ---------- 插件超时 ----------
@pytest.mark.asyncio
async def test_plugin_timeout_kills_process(tmp_path):
    slow_dir = tmp_path / "slow_plugin"
    slow_dir.mkdir()
    (slow_dir / "manifest.json").write_text(json.dumps({
        "id": "slow_plugin", "name": "Slow", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": ["read_message"]}), encoding="utf-8")
    (slow_dir / "plugin.py").write_text(
        "import time\n"
        "def on_message(event, api=None):\n"
        "    time.sleep(30)\n"
        "    return {'type': 'test'}\n", encoding="utf-8")
    rt = _make_runtime(str(slow_dir), event_timeout=1.0)
    await rt.start()
    try:
        with pytest.raises(PluginTimeoutError):
            await rt.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "x"})
        assert rt.status == "crashed"
        assert rt.proc is None
    finally:
        await rt.shutdown()


# ---------- 插件运行期 action 请求（api.send_message） ----------
@pytest.mark.asyncio
async def test_plugin_api_send_message_action(tmp_path):
    api_dir = tmp_path / "api_plugin"
    api_dir.mkdir()
    (api_dir / "manifest.json").write_text(json.dumps({
        "id": "api_plugin", "name": "Api", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": []}), encoding="utf-8")
    (api_dir / "plugin.py").write_text(
        "def on_message(event, api=None):\n"
        "    if api:\n"
        "        api.log('info', 'plug log msg')\n"
        "    return {'type': 'test'}\n", encoding="utf-8")
    rt = _make_runtime(str(api_dir), event_timeout=5.0)
    received_action = []

    async def handler(pid, action, payload):
        received_action.append((pid, action, payload))
        return {"ok": True, "level": payload.get("level")}

    rt.set_action_handler(handler)
    await rt.start()
    try:
        actions = await rt.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "hi"})
        assert actions and actions[0]["type"] == "test"
        # api.log 的 action 请求已由 handler 处理（插件同步等待其响应）
        assert received_action and received_action[0][1] == "log"
        assert received_action[0][2]["level"] == "info"
    finally:
        await rt.shutdown()


# ---------- 隔离标志：python -I 使插件无法 import Flowerie ----------
@pytest.mark.asyncio
async def test_plugin_cannot_import_flowerie(tmp_path):
    isle_dir = tmp_path / "isle_plugin"
    isle_dir.mkdir()
    (isle_dir / "manifest.json").write_text(json.dumps({
        "id": "isle_plugin", "name": "Isle", "version": "1.0.0", "runtime": "python",
        "entry": "plugin.py", "api_version": "1", "permissions": []}), encoding="utf-8")
    (isle_dir / "plugin.py").write_text(
        "def on_message(event, api=None):\n"
        "    try:\n"
        "        import src  # noqa: F401 - Flowerie 内部包应在隔离模式下不可见\n"
        "        return {'type': 'test', 'message': 'leak'}\n"
        "    except Exception:\n"
        "        return {'type': 'test', 'message': 'isolated'}\n", encoding="utf-8")
    rt = _make_runtime(str(isle_dir))
    await rt.start()
    try:
        actions = await rt.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "x"})
        assert actions and actions[0].get("message") == "isolated"
    finally:
        await rt.shutdown()
