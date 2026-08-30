"""SDK 中层测试：PermissionChecker（bot_admin 复用 ADMIN_QQ_IDS；群角色走 adapter）。"""
import pytest

from src.sdk.event import BotEvent
from src.sdk.permissions import PermissionChecker


class FakeConfig:
    ADMIN_QQ_IDS = [10001]


class FakeAdapter:
    async def get_group_member(self, group_id, user_id):
        roles = {2: "owner", 3: "admin", 4: "member", 5: "owner"}
        return {"ok": True, "role": roles.get(user_id, "member")}


@pytest.mark.asyncio
async def test_bot_admin_reuses_config():
    pc = PermissionChecker(config=FakeConfig())
    assert await pc.check(BotEvent({"kind": "message", "user_id": 10001}), "bot_admin") is True
    assert await pc.check(BotEvent({"kind": "message", "user_id": 999}), "bot_admin") is False
    assert await pc.check(BotEvent({"kind": "message", "user_id": 10001}), "bot_owner") is True


@pytest.mark.asyncio
async def test_group_roles_via_adapter():
    pc = PermissionChecker(adapter=FakeAdapter())
    assert await pc.check(BotEvent({"kind": "message", "group_id": 1, "user_id": 2}), "group_owner") is True
    assert await pc.check(BotEvent({"kind": "message", "group_id": 1, "user_id": 3}), "group_admin") is True
    assert await pc.check(BotEvent({"kind": "message", "group_id": 1, "user_id": 4}), "group_admin") is False
    assert await pc.check(BotEvent({"kind": "message", "group_id": 1, "user_id": 4}), "group_member") is True
    # 缺少 group_id → False（无适配可查）
    assert await pc.check(BotEvent({"kind": "message", "user_id": 2}), "group_admin") is False


@pytest.mark.asyncio
async def test_invalid_kind_false():
    pc = PermissionChecker(config=FakeConfig())
    assert await pc.check(BotEvent({"kind": "message", "user_id": 10001}), "whatever") is False
