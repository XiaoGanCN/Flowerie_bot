"""中层事件模型：BotEvent（领域语义，无 OneBot 命名）。

字段：kind（message/notice/request/lifecycle）、scope（group/private）、
group_id/user_id/message_id/time/text/at_list/images/reply_id/operator_id。
行为：reply()/recall()/stop()。
OneBot 的 post_type/sub_type/message_type 等命名绝不出现于此层。
"""
from typing import Any, Dict, List, Optional

from src.sdk.message import BotMessage

# 事件分类（领域枚举；与 OneBot 的 post_type 无关）
EVENT_KINDS = ("message", "notice", "request", "lifecycle")


class BotEvent:
    def __init__(self, data: Dict[str, Any], bot=None):
        self.kind: str = str(data.get("kind") or "unknown")
        self.scope: str = str(data.get("scope") or "")          # group / private
        self.notice_kind: str = str(data.get("notice_kind") or "")  # 领域通知类型
        self.request_kind: str = str(data.get("request_kind") or "")
        self.lifecycle_kind: str = str(data.get("lifecycle_kind") or "")
        self.user_id = data.get("user_id")
        self.group_id = data.get("group_id")
        self.operator_id = data.get("operator_id") or data.get("user_id")
        self.message_id = data.get("message_id")
        self.time = data.get("time")
        self.text: str = str(data.get("text") or "")[:4000]
        self.at_list: List[str] = [str(a) for a in (data.get("at_list") or [])]
        self.images: List[str] = [str(i) for i in (data.get("images") or [])]
        self.reply_id = data.get("reply_id")
        self.message = BotMessage(self.text, at_list=self.at_list, images=self.images,
                                  reply_id=self.reply_id)
        self._bot = bot
        self._stopped = False
        self._matcher_args = ""
        self._matcher_name = ""

    # ---------- 类型判定（领域语义） ----------
    @property
    def is_group(self) -> bool:
        return self.scope == "group"

    @property
    def is_private(self) -> bool:
        return self.scope == "private"

    @property
    def is_message(self) -> bool:
        return self.kind == "message"

    @property
    def is_notice(self) -> bool:
        return self.kind == "notice"

    @property
    def is_request(self) -> bool:
        return self.kind == "request"

    @property
    def is_lifecycle(self) -> bool:
        return self.kind == "lifecycle"

    # ---------- 行为 ----------
    def stop(self) -> None:
        self._stopped = True

    @property
    def stopped(self) -> bool:
        return self._stopped

    @property
    def matcher_args(self) -> str:
        return self._matcher_args

    @property
    def matcher_name(self) -> str:
        return self._matcher_name

    async def reply(self, message=None, **kwargs) -> Optional[int]:
        if self._bot is None:
            raise RuntimeError("BotEvent 未注入 bot（reply 不可用）")
        return await self._bot.reply(self, message, **kwargs)

    async def recall(self) -> None:
        if self._bot is None:
            raise RuntimeError("BotEvent 未注入 bot（recall 不可用）")
        if not self.message_id:
            raise ValueError("当前事件没有 message_id，无法撤回")
        await self._bot.recall(self.message_id)

    # ---------- 构造 ----------
    @classmethod
    def from_dict(cls, data: Dict[str, Any], bot=None) -> "BotEvent":
        return cls(data, bot=bot)
