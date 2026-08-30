"""SDK v1.4 能力测试：请求处理/调度/KV/工具/HTTP 扩展/AI/记忆/cool_down/args/等待消息。"""
import asyncio
import os
from pathlib import Path

import pytest

from src.plugins.manager import PluginManager
from src.repositories.settings_repository import SettingsRepository
from tests.test_plugin_manager import FakeConfig, FakeSender, _deploy  # noqa: F401


class FakeMemory:
    def __init__(self):
        self.calls = []

    async def update_memory(self, user_id, group_id, key, value):
        self.calls.append(("update", user_id, group_id, key, value))

    async def clear_user_memory(self, user_id, group_id):
        self.calls.append(("clear", user_id, group_id))
        return 3


class FakeAI:
    def __init__(self):
        self.calls = []

    async def chat_once(self, user_message, context, custom_prompt="", **kw):
        self.calls.append((user_message, custom_prompt))
        return "AI 回复"


async def _mgr(tmp, approved=None, memory=None, ai=None):
    tmp = Path(tmp)
    repo = SettingsRepository(os.path.join(tmp, "settings.db"))
    sender = FakeSender()
    plugin_dir = os.path.join(tmp, "plugins")
    os.makedirs(plugin_dir, exist_ok=True)
    cfg = FakeConfig()
    cfg.PLUGIN_DIR = plugin_dir
    mgr = PluginManager(cfg, repo, sender=sender, memory_manager=memory,
                        ai_client=ai)
    import shutil
    shutil.rmtree(plugin_dir, ignore_errors=True)
    _deploy(tmp, "sdk_plugin")
    mgr.discover()
    ok, msg = await mgr.enable("sdk_plugin", approved_permissions=approved or
                               ["read_message", "send_message", "scheduler", "storage"])
    assert ok, msg
    return mgr, repo, sender


# ---------- KV（storage） ----------
@pytest.mark.asyncio
async def test_kv_roundtrip(tmp_path):
    mgr, repo, _ = await _mgr(str(tmp_path))
    r = await mgr._handle_action("sdk_plugin", "kv_set", {"key": "k1", "value": "v1"})
    assert r == {"ok": True, "key": "k1"}
    r = await mgr._handle_action("sdk_plugin", "kv_get", {"key": "k1"})
    assert r["exists"] is True and r["value"] == "v1"
    # JSON 值往返
    await mgr._handle_action("sdk_plugin", "kv_set", {"key": "obj", "value": {"a": 1}})
    r = await mgr._handle_action("sdk_plugin", "kv_get", {"key": "obj"})
    assert r["value"] == '{"a": 1}'
    items = (await mgr._handle_action("sdk_plugin", "kv_list", {}))["items"]
    assert {i["key"] for i in items} == {"k1", "obj"}
    await mgr._handle_action("sdk_plugin", "kv_delete", {"key": "k1"})
    r = await mgr._handle_action("sdk_plugin", "kv_get", {"key": "k1"})
    assert r["exists"] is False
    # 插件命名空间隔离（存储层）：其他插件键不可见
    assert repo.get_plugin_kv("other", "obj") is None
    assert repo.get_plugin_kv("sdk_plugin", "obj") == '{"a": 1}'   # 未删键可读；跨插件隔离
    await mgr.shutdown()


# ---------- 好友/加群请求（request_handle） ----------
@pytest.mark.asyncio
async def test_request_handlers(tmp_path):
    mgr, _, sender = await _mgr(str(tmp_path), approved=["request_handle"])
    r = await mgr._handle_action("sdk_plugin", "handle_friend_request",
                                 {"flag": "f1", "approve": True, "remark": "你好"})
    assert r["ok"] and sender.friend_requests[-1] == ("f1", True, "你好")
    r = await mgr._handle_action("sdk_plugin", "handle_group_request",
                                 {"flag": "g1", "approve": False, "reason": "满了"})
    assert r["ok"] and sender.group_requests[-1] == ("g1", False, "满了")
    await mgr.shutdown()


# ---------- 调度（scheduler） ----------
@pytest.mark.asyncio
async def test_schedule_register_dispatch_and_cancel(tmp_path):
    mgr, _, _ = await _mgr(str(tmp_path), approved=["scheduler"])
    hits = []

    async def fake_dispatch(kind, payload):
        hits.append(payload)

    mgr.dispatch_event = fake_dispatch
    r = await mgr._handle_action("sdk_plugin", "schedule_register",
                                 {"name": "t1", "kind": "delay", "when": 0.1})
    assert r["ok"], r
    await asyncio.sleep(0.5)
    assert hits and hits[0]["name"] == "t1" and hits[0]["kind"] == "schedule"
    # interval（短周期验证多次触发）+ 幂等覆盖
    r = await mgr._handle_action("sdk_plugin", "schedule_register",
                                 {"name": "t2", "kind": "interval", "when": 1})
    assert r["ok"]
    await asyncio.sleep(2.6)
    t2_hits = [h for h in hits if h.get("name") == "t2"]
    assert len(t2_hits) >= 2
    # cancel
    sid = t2_hits[0]["schedule_id"]
    r = await mgr._handle_action("sdk_plugin", "schedule_cancel", {"schedule_id": sid})
    assert r["ok"]
    await asyncio.sleep(0.35)
    assert len([h for h in hits if h.get("name") == "t2"]) <= len(t2_hits) + 1
    # list：delay 触发后自动清理；tick（插件端到端注册）与剩余任务在列
    lst = (await mgr._handle_action("sdk_plugin", "schedule_list", {}))["schedules"]
    assert all(s["name"] != "t1" for s in lst)   # delay 一次性已清理
    assert any(s["name"] == "tick" for s in lst)
    await mgr.shutdown()
    assert mgr._schedules == {}  # shutdown 清理


# ---------- 工具 ----------
@pytest.mark.asyncio
async def test_tool_actions(tmp_path):
    mgr, _, _ = await _mgr(str(tmp_path))
    for _ in range(10):
        v = (await mgr._handle_action("sdk_plugin", "random_int", {"low": 5, "high": 9}))["value"]
        assert 5 <= v <= 9
    r = await mgr._handle_action("sdk_plugin", "random_choice", {"choices": ["a", "b"]})
    assert r["choice"] in ("a", "b")
    r = await mgr._handle_action("sdk_plugin", "now", {})
    assert r["ok"] and r["timestamp"] > 0
    r = await mgr._handle_action("sdk_plugin", "format_time",
                                 {"timestamp": 0, "format": "%Y"})
    assert r["text"] == "1970"
    await mgr.shutdown()


# ---------- HTTP 扩展（PUT/DELETE/HEAD 走统一防线；下载路径校验） ----------
@pytest.mark.asyncio
async def test_http_ext(tmp_path, monkeypatch):
    async def fake_plugin_http_request(payload):
        return {"ok": True, "status": 200, "body": f"{payload.get('method')}-ok"}

    monkeypatch.setattr("src.plugins.http_action.plugin_http_request", fake_plugin_http_request)
    mgr, _, _ = await _mgr(str(tmp_path), approved=["http_request"])
    r = await mgr._handle_action("sdk_plugin", "http_put",
                                 {"url": "http://example.com/a", "body": "x"})
    assert r["ok"] and r["body"] == "PUT-ok"
    r = await mgr._handle_action("sdk_plugin", "http_head", {"url": "http://example.com/h"})
    assert r["body"] == "HEAD-ok"
    # 非 http url 拒绝
    r = await mgr._handle_action("sdk_plugin", "http_put", {"url": "file:///etc/passwd"})
    assert r["ok"] is False
    # 下载 save_to 越界拒绝（须先过 SSRF——用合法 url，SSRF 校验拦 localhost 前先拦 url）
    r = await mgr._handle_action("sdk_plugin", "http_download",
                                 {"url": "http://example.com/f", "save_to": "../x.bin"})
    assert r["ok"] is False and "save_to" in str(r.get("error"))
    await mgr.shutdown()


# ---------- AI（ai_chat：注入调用；未注入报错） ----------
@pytest.mark.asyncio
async def test_ai_chat(tmp_path):
    ai = FakeAI()
    mgr, _, _ = await _mgr(str(tmp_path), approved=["ai_chat"], ai=ai)
    r = await mgr._handle_action("sdk_plugin", "ai_chat",
                                 {"message": "你好", "system": "你是助手"})
    assert r["ok"] and r["reply"] == "AI 回复"
    assert ai.calls[0] == ("你好", "你是助手")
    await mgr.shutdown()
    mgr2, _, _ = await _mgr(str(tmp_path), approved=["ai_chat"])
    r = await mgr2._handle_action("sdk_plugin", "ai_chat", {"message": "hi"})
    assert r["ok"] is False and "ai_chat" in str(r.get("error"))
    await mgr2.shutdown()


# ---------- 记忆（update/clear） ----------
@pytest.mark.asyncio
async def test_mem_actions(tmp_path):
    mem = FakeMemory()
    mgr, _, _ = await _mgr(str(tmp_path), approved=["read_memory"], memory=mem)
    r = await mgr._handle_action("sdk_plugin", "mem_update",
                                 {"user_id": 1, "group_id": 2, "key": "nick", "value": "小璃"})
    assert r["ok"] and mem.calls[0] == ("update", 1, 2, "nick", "小璃")
    r = await mgr._handle_action("sdk_plugin", "mem_clear", {"user_id": 1, "group_id": 2})
    assert r["ok"] and r["cleared"] == 3
    await mgr.shutdown()


# ---------- SDK 插件端到端：args/cool_down/schedule 触达 ----------
@pytest.mark.asyncio
async def test_sdk_plugin_end_to_end_capabilities(tmp_path):
    mgr, repo, sender = await _mgr(str(tmp_path),
                                   approved=["read_message", "send_message", "scheduler",
                                             "storage", "read_memory"])
    for _ in range(40):
        if mgr._matchers.get("sdk_plugin"):
            break
        await asyncio.sleep(0.1)
    assert mgr._matchers.get("sdk_plugin")
    # !add 1 2 → args 拆分 → 3
    await mgr.dispatch_event("message", {"group_id": 7, "user_id": 9, "message_id": 200,
                                         "text": "!add 1 2", "scope": "group"})
    assert sender.sent[-1] == ("group", 7, "3", 200)
    # 冷却：第一发 OK，第二发冷却中
    await mgr.dispatch_event("message", {"group_id": 7, "user_id": 9, "message_id": 201,
                                         "text": "!cool", "scope": "group"})
    assert sender.sent[-1] == ("group", 7, "OK", 201)
    await mgr.dispatch_event("message", {"group_id": 7, "user_id": 9, "message_id": 202,
                                         "text": "!cool", "scope": "group"})
    assert sender.sent[-1] == ("group", 7, "冷却中", 202)
    # 调度触达：interval=1 的 tick 已注册（scheduler 批准）；等待触发后 KV 无副作用——
    # 用 manager 侧 schedule_list 确认已注册
    lst = (await mgr._handle_action("sdk_plugin", "schedule_list", {}))["schedules"]
    assert any(s["name"] == "tick" and s["kind"] == "interval" for s in lst)
    await mgr.shutdown()
