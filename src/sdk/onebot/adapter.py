"""下层：OneBotAdapter（OneBot11/NapCat HTTP 实现）——唯一 import OneBot 语义的适配处。

机械职责：BotMessage → OneBot 段数组（transformer.to_bot_message_payload）；
sender 返回结果 → 统一 BotError 体系（retcode/超时/消息不存在/不支持）。
"""
import asyncio
from typing import Any, Dict, List, Optional

from src.sdk.adapter import BotAdapter
from src.sdk.errors import (
    BotAPIError,
    BotTimeoutError,
    MessageNotFoundError,
    UnsupportedOperationError,
)
from src.sdk.message import BotMessage
from src.sdk.onebot.transformer import to_bot_message_payload


class OneBotAdapter(BotAdapter):
    def __init__(self, sender, context_manager=None, timeout: float = 15.0):
        self._sender = sender
        self._context_manager = context_manager
        self._timeout = float(timeout)

    @staticmethod
    def _ensure(ok: Any, message: str = "平台返回失败") -> None:
        if not ok:
            raise BotAPIError(message)

    async def _call(self, coro, *, timeout: Optional[float] = None):
        try:
            return await asyncio.wait_for(coro, timeout=timeout or self._timeout)
        except asyncio.TimeoutError:
            raise BotTimeoutError("平台调用超时") from None

    # ---------- BotAdapter 实现 ----------
    async def send(self, target: str, target_id: int, message,
                   reply_id: Optional[int] = None) -> int:
        payload = to_bot_message_payload(message)
        segmented = to_bot_message_payload(BotMessage("", reply_id=reply_id)) if reply_id is not None else []
        # reply 段已由 to_bot_message_payload 处理（BotMessage.reply_id）；str 场景显式补 reply
        if reply_id is not None and not isinstance(payload, list):
            payload = [{"type": "reply", "data": {"id": int(reply_id)}}, {"type": "text", "data": {"text": str(payload)}}]
        elif reply_id is not None and isinstance(payload, list):
            payload = [{"type": "reply", "data": {"id": int(reply_id)}}] + payload
        result = await self._call(self._sender.send_msg_raw(target, int(target_id), payload))
        if not result.get("ok"):
            raise BotAPIError(f"发送失败（target={target}）: {result.get('error', '')}")
        mid = result.get("message_id")
        if mid is None:
            raise BotAPIError("发送成功但服务端未返回 message_id")
        return int(mid)

    async def recall(self, message_id: int) -> None:
        self._ensure(await self._call(self._sender.delete_msg(message_id)),
                     f"撤回失败 message_id={message_id}（可能已不存在或无权操作）")

    async def get_message(self, message_id: int) -> BotMessage:
        d = await self._call(self._sender.get_msg(message_id))
        if not d.get("ok"):
            raise MessageNotFoundError(f"消息不存在 message_id={message_id}")
        return BotMessage(str(d.get("text") or ""))

    async def get_user_info(self, user_id: int) -> Dict[str, Any]:
        raise UnsupportedOperationError("当前平台不支持独立 get_user_info（可用 get_group_member）")

    async def get_group_info(self, group_id: int) -> Dict[str, Any]:
        raise UnsupportedOperationError("当前平台不支持独立 get_group_info（可用 get_group_member 查询）")

    async def get_group_member(self, group_id: int, user_id: int) -> Dict[str, Any]:
        d = await self._call(self._sender.get_group_member_info(group_id, user_id))
        if not d.get("ok"):
            raise BotAPIError(f"成员信息查询失败: {d.get('error', '')}")
        return d

    async def get_group_members(self, group_id: int) -> List[Dict[str, Any]]:
        d = await self._call(self._sender.get_group_member_list(group_id))
        if not d.get("ok"):
            raise BotAPIError(f"成员列表查询失败: {d.get('error', '')}")
        return d.get("members", [])

    async def mute(self, group_id: int, user_id: int, duration_seconds: int) -> None:
        self._ensure(await self._call(self._sender.set_group_ban(group_id, user_id, duration_seconds)),
                     f"禁言失败 group={group_id} user={user_id}")

    async def kick(self, group_id: int, user_id: int) -> None:
        self._ensure(await self._call(self._sender.set_group_kick(group_id, user_id)),
                     f"移出群成员失败 group={group_id} user={user_id}")

    async def get_context(self, group_id: int, max_messages: int = 10) -> List[Dict[str, Any]]:
        """复用 Flowerie 现有 ContextManager（领域数据源，不调平台历史接口）。"""
        if self._context_manager is None:
            return await super().get_context(group_id, max_messages)
        state = self._context_manager.get_group_state(int(group_id))
        entries = list(state.context)[-max(1, min(int(max_messages), 50)):]
        return [{"user_id": e.get("user_id"), "message": str(e.get("message", "")),
                 "is_bot": bool(e.get("is_bot")), "time": e.get("time")} for e in entries]


def make_onebot_adapter(sender, context_manager=None) -> OneBotAdapter:
    """下层工厂（上层/主进程按需创建）。"""
    return OneBotAdapter(sender, context_manager)
