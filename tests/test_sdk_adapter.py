"""SDK 下层测试：OneBotAdapter（消息段转换/错误语义/context 复用）。"""
import asyncio

import pytest

from src.sdk.errors import BotAPIError, BotTimeoutError, MessageNotFoundError
from src.sdk.message import BotMessage
from src.sdk.onebot.adapter import OneBotAdapter


class FakeSender:
    async def send_msg_raw(self, target, target_id, message, reply_id=None, retries=2):
        self.last = (target, target_id, message, reply_id)
        return {"ok": True, "message_id": 1000 + target_id}

    async def delete_msg(self, message_id):
        return True

    async def get_msg(self, message_id):
        return {"ok": True, "message_id": message_id, "user_id": 1, "time": 1, "text": "hello"}

    async def get_group_member_info(self, group_id, user_id):
        return {"ok": True, "group_id": group_id, "user_id": user_id, "role": "admin"}

    async def get_group_member_list(self, group_id):
        return {"ok": True, "members": [{"user_id": 1, "role": "owner", "card": "", "nickname": "a"}]}

    async def set_group_ban(self, group_id, user_id, duration_seconds):
        return True

    async def set_group_kick(self, group_id, user_id, reject_add=False):
        return True


class FakeContextManager:
    class State:
        def __init__(self):
            self.context = []

    def __init__(self):
        self._state = self.State()
        self._state.context.append({"user_id": 7, "message": "早", "is_bot": False, "time": 1.0})
        self._state.context.append({"user_id": 8, "message": "哈哈", "is_bot": False, "time": 2.0})

    def get_group_state(self, group_id):
        return self._state


@pytest.mark.asyncio
async def test_adapter_send_domain_message():
    sender = FakeSender()
    adapter = OneBotAdapter(sender)
    mid = await adapter.send("group", 42, BotMessage("hi").at(5).image("f.png").reply(77))
    assert mid == 1042
    target, target_id, segs, reply_id = sender.last
    assert target == "group" and target_id == 42 and reply_id is None
    # BotMessage → OneBot 段数组（下层出站转换）
    assert segs == [
        {"type": "reply", "data": {"id": 77}},
        {"type": "text", "data": {"text": "hi"}},
        {"type": "at", "data": {"qq": "5"}},
        {"type": "image", "data": {"file": "f.png"}},
    ]


@pytest.mark.asyncio
async def test_adapter_reply_no_duplicate_segment():
    """BotMessage 自带 reply_id 且显式传 reply_id：reply 段唯一（不双发）。"""
    sender = FakeSender()
    adapter = OneBotAdapter(sender)
    await adapter.send("group", 1, BotMessage("hi").reply(77), reply_id=77)
    segs = sender.last[2]
    replys = [s for s in segs if s["type"] == "reply"]
    assert len(replys) == 1 and replys[0]["data"]["id"] == 77
    # str 场景显式 reply_id：正常补段
    await adapter.send("group", 1, "plain", reply_id=88)
    assert sender.last[2][0] == {"type": "reply", "data": {"id": 88}}


@pytest.mark.asyncio
async def test_adapter_error_semantics():
    class FailSender(FakeSender):
        async def send_msg_raw(self, target, target_id, message, reply_id=None, retries=2):
            return {"ok": False, "error": "retcode=100"}

        async def delete_msg(self, message_id):
            return False

        async def get_msg(self, message_id):
            return {"ok": False}

    adapter = OneBotAdapter(FailSender())
    with pytest.raises(BotAPIError):
        await adapter.send("group", 1, "x")
    with pytest.raises(BotAPIError):
        await adapter.recall(1)
    with pytest.raises(MessageNotFoundError):
        await adapter.get_message(1)


@pytest.mark.asyncio
async def test_adapter_timeout_maps_to_bot_timeout():
    class SlowSender(FakeSender):
        async def send_msg_raw(self, target, target_id, message, reply_id=None, retries=2):
            await asyncio.sleep(5)
            return {"ok": True, "message_id": 1}

    adapter = OneBotAdapter(SlowSender(), timeout=0.05)
    with pytest.raises(BotTimeoutError):
        await adapter.send("group", 1, "x")


@pytest.mark.asyncio
async def test_adapter_get_context_reuses_context_manager():
    cm = FakeContextManager()
    adapter = OneBotAdapter(FakeSender(), context_manager=cm)
    ctx = await adapter.get_context(1, max_messages=10)
    assert [e["message"] for e in ctx] == ["早", "哈哈"]
    assert ctx[0]["user_id"] == 7 and ctx[0]["is_bot"] is False


@pytest.mark.asyncio
async def test_adapter_group_roles():
    adapter = OneBotAdapter(FakeSender())
    member = await adapter.get_group_member(1, 2)
    assert member["role"] == "admin"
    members = await adapter.get_group_members(1)
    assert members[0]["role"] == "owner"
