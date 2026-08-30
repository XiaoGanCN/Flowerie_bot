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
    """OneBot11 发送桩：记录调用；支持消息类/群管 API（与 sender.py 同形）。"""

    def __init__(self):
        self.sent = []
        self.deleted = []
        self.sent_mid = 9000
        self.group_members = [{"user_id": 1, "role": "owner", "card": "", "nickname": "a"}]

    async def send_msg_raw(self, target, target_id, message, reply_id=None, retries=2):
        self.sent.append((target, target_id, message, reply_id))
        self.sent_mid += 1
        if target == "group" and isinstance(message, str):
            # 与旧 send_group_message 兼容的断言形式（旧测试依赖）
            pass
        return {"ok": True, "message_id": self.sent_mid}

    async def send_group_message(self, group_id, message=None, **kw):
        self.sent.append(("group", group_id, message))
        return True

    async def send_private_message(self, user_id, message=None, **kw):
        self.sent.append(("private", user_id, message))
        return True

    async def delete_msg(self, message_id):
        self.deleted.append(message_id)
        return True

    async def get_msg(self, message_id):
        return {"ok": True, "message_id": message_id, "user_id": 1, "time": 1, "text": "hi"}

    async def get_group_msg_history(self, group_id, count=15):
        return {"ok": True, "messages": [{"message_id": 1, "user_id": 2, "time": 1, "text": "x"}]}

    async def get_group_member_info(self, group_id, user_id):
        return {"ok": True, "group_id": group_id, "user_id": user_id, "role": "member",
                "card": "", "nickname": "n"}

    async def get_group_member_list(self, group_id):
        return {"ok": True, "group_id": group_id, "members": self.group_members}

    async def set_group_ban(self, group_id, user_id, duration_seconds):
        return True

    async def set_group_kick(self, group_id, user_id, reject_add=False):
        return True

    async def set_group_admin(self, group_id, user_id, enable):
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
    assert sender.sent and sender.sent[0] == ("group", 7, "你好 9", None)  # send_msg_raw(reply_id=None)
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
    assert sender.sent[0] == ("group", 42, "你好 100", None)  # send_msg_raw(reply_id=None)
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


# ---------- 未知动作：无权限映射的 action 一律拒绝（白盒） ----------
@pytest.mark.asyncio
async def test_unknown_action_rejected_not_executed(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "rogue_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("rogue_plugin", approved_permissions=["read_message"])
    assert ok, msg
    summary = await mgr.dispatch_event("message", {"group_id": 1, "user_id": 2, "text": "do evil"})
    assert summary, "事件被投递"
    assert summary[0]["action"] == "do_evil"
    assert summary[0]["denied"] is True and summary[0]["ok"] is False
    # 未定义动作绝不落到 _run_action（无副作用）：sender 未被调用
    assert sender.sent == []


# ---------- 声明式插件：匹配到规则但未批准权限 → 动作被拒 ----------
@pytest.mark.asyncio
async def test_declarative_action_denied_without_approved_permission(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "declarative_plugin")
    mgr.discover()
    # 只批准 read_message（事件投递），不批准 send_message（动作执行被拒）
    ok, msg = await mgr.enable("declarative_plugin", approved_permissions=["read_message"])
    assert ok, msg
    summary = await mgr.dispatch_event("message", {"group_id": 7, "user_id": 9, "text": "hello world"})
    assert summary and any(s["action"] == "send_message" for s in summary)
    assert all(s["denied"] is True and s["ok"] is False for s in summary)
    assert sender.sent == []


# ---------- 保护级别 unsafe 也不豁免权限 ----------
@pytest.mark.asyncio
async def test_unsafe_level_still_enforces_permissions(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "declarative_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("declarative_plugin",
                               approved_permissions=["read_message"], protection="unsafe")
    assert ok, msg
    summary = await mgr.dispatch_event("message", {"group_id": 3, "user_id": 4, "text": "hello there"})
    assert summary and all(s["denied"] is True for s in summary)
    assert sender.sent == []


# ---------- 消息类 API（v1.2+）：回复引用 / 撤回白名单 / 详情·历史·上下文 ----------
@pytest.mark.asyncio
async def test_send_reply_and_record_message_id(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    await mgr.enable("minimal_plugin", approved_permissions=["read_message", "send_message"])
    res = await mgr._execute_action("minimal_plugin", {
        "type": "send_reply",
        "payload": {"group_id": 1, "message": "收到", "reply_id": 12345}})
    assert res[0]["ok"] and res[0]["result"]["message_id"]
    # reply 段通过 send_msg_raw(reply_id=12345) 透传
    assert sender.sent[-1] == ("group", 1, "收到", 12345)
    # 发送记录被维护（可撤回）
    mid = res[0]["result"]["message_id"]
    assert mid in mgr._sent_message_ids


@pytest.mark.asyncio
async def test_delete_message_only_own_sent(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    await mgr.enable("minimal_plugin",
                     approved_permissions=["read_message", "send_message", "delete_message"])
    # 未知 message_id（不是本 bot 发的）→ 拒绝（防删他人消息）
    res = await mgr._execute_action("minimal_plugin", {"type": "delete_message", "payload": {"message_id": 88888}})
    # 业务级拒绝（result.denied）——权限已批但 message_id 未记录：只能撤回本 bot 发的
    assert res[0]["ok"] is False and res[0]["result"].get("denied") is True
    assert sender.deleted == []
    # 先发一条，再撤回自己的 → 成功
    sent = await mgr._execute_action("minimal_plugin", {
        "type": "send_message", "payload": {"group_id": 2, "message": "hi"}})
    mid = sent[0]["result"]["message_id"]
    res2 = await mgr._execute_action("minimal_plugin", {"type": "delete_message", "payload": {"message_id": mid}})
    assert res2[0]["ok"] is True and sender.deleted == [mid]


@pytest.mark.asyncio
async def test_message_query_actions(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "minimal_plugin")
    mgr.discover()
    await mgr.enable("minimal_plugin", approved_permissions=["read_message", "read_message_history"])
    get_msg = await mgr._execute_action("minimal_plugin", {"type": "get_message", "payload": {"message_id": 1}})
    assert get_msg[0]["ok"] and get_msg[0]["result"]["text"] == "hi"
    hist = await mgr._execute_action("minimal_plugin", {"type": "get_group_history", "payload": {"group_id": 1}})
    assert hist[0]["ok"] and hist[0]["result"]["messages"][0]["text"] == "x"
    ctx = await mgr._execute_action("minimal_plugin", {"type": "get_context", "payload": {"group_id": 1}})
    assert ctx[0]["ok"] and ctx[0]["result"]["messages"]


# ---------- 匹配扩展：正则 / 优先级 / Matcher 阻断 ----------
def _decl(rule):
    import json as _json
    import tempfile as _tf
    d = _tf.mkdtemp()
    p = os.path.join(d, "m.json")
    open(p, "w", encoding="utf-8").write(_json.dumps({
        "id": "m", "name": "M", "version": "1.0.0", "runtime": "json", "entry": "",
        "api_version": "1", "permissions": ["read_message"], "declarations": rule}))
    return p, d


@pytest.mark.asyncio
async def test_declarative_regex_and_priority_stop(env):
    mgr, repo, sender, tmp = env
    pdir = tmp / "plugins" / "m"
    pdir.mkdir(parents=True)
    manifest = {
        "id": "m", "name": "M", "version": "1.0.0", "runtime": "json", "entry": "",
        "api_version": "1", "permissions": ["read_message", "send_message"],
        "declarations": [
            {"event": "message", "priority": 10, "stop": True, "match": {"text_regex": "^!hi\\s"},
             "actions": [{"type": "send_message",
                          "payload": {"group_id": "${group_id}", "message": "regex hit"}}]},
            {"event": "message", "priority": 1, "match": {"text_prefix": "!hi"},
             "actions": [{"type": "send_message",
                          "payload": {"group_id": "${group_id}", "message": "prefix hit"}}]},
        ]}
    (pdir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    mgr.discover()
    ok, msg = await mgr.enable("m", approved_permissions=["read_message", "send_message"])
    assert ok, msg
    summary = await mgr.dispatch_event("message", {"group_id": 3, "user_id": 9, "text": "!hi 世界"})
    # stop=true 高优先级命中 → 仅有 regex 规则执行（Matcher 阻断低优先级规则）
    raws = [s.get("result", {}).get("raw_text", "") for s in summary]
    assert "regex hit" in raws and "prefix hit" not in raws


# ---------- SDK 模式端到端：matcher 注册 → 命中投递（matched）→ 插件 reply ----------
@pytest.mark.asyncio
async def test_sdk_plugin_matcher_end_to_end(env):
    mgr, repo, sender, tmp = env
    _deploy(tmp, "sdk_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("sdk_plugin",
                               approved_permissions=["read_message", "send_message"])
    assert ok, msg
    # 等子进程就绪 + on_startup 完成 matcher 注册（重试直至注册表非空）
    for _ in range(40):
        if mgr._matchers.get("sdk_plugin"):
            break
        await asyncio.sleep(0.1)
    assert mgr._matchers.get("sdk_plugin"), "SDK matcher 未注册"
    # 命中事件：!hi 世界（command 匹配）→ 插件 SDK 内联回复（send_reply action）
    await mgr.dispatch_event("message", {
        "group_id": 7, "user_id": 9, "message_id": 100,
        "text": "!hi 世界", "scope": "group"})
    assert sender.sent and sender.sent[-1] == ("group", 7, "你好呀", 100)
    # 未命中事件：不投递，也不会误回复（SDK 模式只投递匹配事件）
    before = len(sender.sent)
    summary2 = await mgr.dispatch_event("message", {
        "group_id": 7, "user_id": 9, "message_id": 101, "text": "无关内容", "scope": "group"})
    assert summary2 == [] and len(sender.sent) == before
