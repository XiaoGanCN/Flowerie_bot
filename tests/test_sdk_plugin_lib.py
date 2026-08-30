"""插件侧 flowerie_sdk 库级测试（直接 import，不经协议）：wait_for/ask/confirm/select/
cool_down/args/schedule 装饰器/多媒体 Builder。"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin_sdk"))

from flowerie_sdk import BotMessage, FlowerieBot, command  # noqa: E402
from flowerie_sdk.event import BotEvent  # noqa: E402


class FakeApi:
    def __init__(self):
        self.sent = []

    def log(self, level, message):
        return {"ok": True}

    def matcher_register(self, matchers):
        return {"ok": True, "count": len(matchers)}

    def schedule_register(self, kind, when, name):
        return {"ok": True, "schedule_id": f"{name}:x"}

    def send_reply(self, payload):
        self.sent.append(payload)
        return {"ok": True, "message_id": 1}

    def send_message(self, payload):
        self.sent.append(payload)
        return {"ok": True, "message_id": 1}

    def kv_set(self, key, value):
        return {"ok": True}

    def random_int(self, low, high):
        return {"ok": True, "value": 7}

    def _dispatch(self, event, payload):
        return {"ok": True}


def _ev(text="hi", scope="group", user_id=1, group_id=2, message_id=5, kind="message"):
    return BotEvent({"kind": kind, "scope": scope, "user_id": user_id,
                     "group_id": group_id, "message_id": message_id, "text": text}, bot)


bot = FlowerieBot()


# ---------- 等待消息 / ask / confirm / select ----------
@pytest.mark.asyncio
async def test_wait_for_receives_and_timeout():
    bot._waiters.clear()
    task = asyncio.create_task(bot.wait_for(lambda e: e.text == "go", timeout=5))
    await asyncio.sleep(0.05)
    await bot.route({"kind": "message", "scope": "group", "user_id": 1,
                     "group_id": 2, "message_id": 9, "text": "go"})
    got = await asyncio.wait_for(task, timeout=1)
    assert got is not None and got.text == "go"
    # 超时
    t2 = asyncio.create_task(bot.wait_for(lambda e: e.text == "never", timeout=0.2))
    assert await asyncio.wait_for(t2, timeout=1.0) is None


# ---------- cool_down ----------
@pytest.mark.asyncio
async def test_cool_down():
    bot._cooldowns.clear()
    assert await bot.cool_down("k", 60) is True
    assert await bot.cool_down("k", 60) is False
    assert await bot.cool_down("k2", 0) is True     # 0 秒永不冷却


# ---------- args（shlex 拆分） ----------
@pytest.mark.asyncio
async def test_args_with_quotes():
    e = BotEvent({"kind": "message", "scope": "group", "text": "x"}, bot)
    e._bot = bot
    bot._last_args = 'a "b c" d'
    assert e.args == ["a", "b c", "d"]
    bot._last_args = ""
    assert e.args == []


# ---------- schedule 装饰器注册 ----------
@pytest.mark.asyncio
async def test_schedule_decorator_and_route():
    bot._schedules.clear()
    fired = []

    @bot.schedule(interval=60, name="job1")
    async def job1(event):
        fired.append(event.schedule_id if event else None)

    assert len(bot._schedules) == 1 and bot._schedules[0][0] == "job1"
    await bot.route_schedule({"name": "job1", "schedule_id": "s:1", "trigger": "interval"})
    assert fired == ["s:1"]


# ---------- 多媒体 Builder（插件侧） ----------
def test_plugin_message_multimedia():
    m = BotMessage("看！").at("all").image("http://a/i.png").video("http://a/v.mp4")
    assert m.has("video") and m.has("all") is False
    assert m.has("at") and m.at_list == ["all"]
    assert [k for k, _ in m] == ["text", "at", "image", "video"]


# ---------- 路由：匹配 handler（含 block 语义）与监听器（kind） ----------
@pytest.mark.asyncio
async def test_route_matched_and_listener_kind():
    api = FakeApi()
    bot.attach(api)

    @command("hi", block=True)
    async def hi(event):
        await event.reply("你好")

    ms = hi.__flowerie_matchers__
    bot._handlers = [(ms[0], hi)]  # 直接注入（模拟 register 收集后）
    await bot.route({"kind": "message", "scope": "group", "user_id": 1, "group_id": 2,
                     "message_id": 10, "text": "!hi",
                     "matched": [{"name": "hi", "kind": "command", "args": ""}]})
    assert api.sent and api.sent[-1]["reply_id"] == 10
    # listener kind（非 post_type）分发
    seen = []

    @bot.listen("notice", priority=1)
    async def notice_listener(event):
        seen.append(event.notice_kind)

    async def noop(event):
        return None

    await bot.route({"kind": "notice", "notice_kind": "group_increase", "scope": "group",
                     "user_id": 3, "group_id": 2, "message_id": 0, "text": ""})
    assert seen == ["group_increase"]
    bot._schedules.clear()
