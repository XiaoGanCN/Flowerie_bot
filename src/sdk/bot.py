"""Bot 门面：插件面向的统一入口（bot.send / bot.reply / bot.recall / ...）。

构造：Bot(adapter, config=None, permission_checker=None)；adapter 为 BotAdapter 实例。
所有网络 API 保持 async；失败抛统一 BotError 体系。
"""
from typing import Any, Dict, List, Optional, Union

from src.sdk.event import BotEvent
from src.sdk.message import BotMessage
from src.sdk.permissions import PermissionChecker


class Bot:
    def __init__(self, adapter, config=None, permission_checker: Optional[PermissionChecker] = None):
        self._adapter = adapter
        self._config = config
        self._permission = permission_checker or PermissionChecker(config=config, bot=self,
                                                                   adapter=adapter)

    # ---------- 消息 ----------
    async def send(self, target, message, *, reply_id: Optional[int] = None) -> int:
        """发送消息：target 为元组 ("group"|"private", id) 或群号 int（默认群）。

        message: str / BotMessage。返回 message_id。
        """
        if isinstance(target, int) or str(target).isdigit():
            target = ("group", int(target))
        kind, target_id = target
        return await self._adapter.send(kind, int(target_id), message, reply_id=reply_id)

    async def reply(self, event_or_target, message, **kwargs) -> int:
        """回复：传入 BotEvent 自动推导目标（群/私聊）。"""
        if isinstance(event_or_target, BotEvent):
            kind = "group" if event_or_target.is_group else "private"
            target_id = event_or_target.group_id or event_or_target.user_id
            reply_id = kwargs.pop("reply_id", event_or_target.message_id)
            return await self._adapter.send(kind, int(target_id), message, reply_id=reply_id)
        return await self.send(event_or_target, message, **kwargs)

    async def recall(self, message_id: int) -> None:
        await self._adapter.recall(int(message_id))

    async def get_message(self, message_id: int) -> BotMessage:
        return await self._adapter.get_message(int(message_id))

    async def get_context(self, group_id: int, max_messages: int = 10) -> List[Dict[str, Any]]:
        return await self._adapter.get_context(int(group_id), max_messages)

    # ---------- 用户 ----------
    async def get_user_info(self, user_id: int) -> Dict[str, Any]:
        return await self._adapter.get_user_info(int(user_id))

    async def is_admin(self, event) -> bool:
        """bot 管理员（复用 Flowerie ADMIN_QQ_IDS）。"""
        return await self._permission.check(event, "bot_admin")

    async def is_owner(self, event) -> bool:
        """bot owner（当前与 admin 同源，见 docs/sdk.md 说明）。"""
        return await self._permission.check(event, "bot_owner")

    # ---------- 群 ----------
    async def get_group_info(self, group_id: int) -> Dict[str, Any]:
        return await self._adapter.get_group_info(int(group_id))

    async def get_group_member(self, group_id: int, user_id: int) -> Dict[str, Any]:
        return await self._adapter.get_group_member(int(group_id), int(user_id))

    async def get_group_members(self, group_id: int) -> List[Dict[str, Any]]:
        return await self._adapter.get_group_members(int(group_id))

    async def is_group_admin(self, group_id: int, user_id: int) -> bool:
        member = await self._adapter.get_group_member(int(group_id), int(user_id))
        role = str((member or {}).get("role") or "member")
        return role in ("owner", "admin")

    async def is_group_owner(self, group_id: int, user_id: int) -> bool:
        member = await self._adapter.get_group_member(int(group_id), int(user_id))
        return str((member or {}).get("role") or "member") == "owner"

    # ---------- 群管理 ----------
    async def mute(self, group_id: int, user_id: int, duration_seconds: int) -> None:
        await self._adapter.mute(int(group_id), int(user_id), int(duration_seconds))

    async def kick(self, group_id: int, user_id: int) -> None:
        await self._adapter.kick(int(group_id), int(user_id))

    # ---------- 权限检查（require_permission 装饰器配套） ----------
    async def check_permission(self, event, kind: str) -> bool:
        return await self._permission.check(event, kind)

    def permission(self, event, kind: str) -> bool:
        raise RuntimeError("请使用 await bot.check_permission(event, kind)")  # pragma: no cover
