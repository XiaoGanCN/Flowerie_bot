"""插件侧事件模型：BotEvent（领域语义：kind/scope/text/at_list/images）。"""

from typing import Any, Dict, Optional

from flowerie_sdk.message import BotMessage


class BotEvent:
    def __init__(self, data: Dict[str, Any], bot=None):
        self.kind = str(data.get("kind") or "unknown")
        self.scope = str(data.get("scope") or "")
        self.notice_kind = str(data.get("notice_kind") or "")
        self.request_kind = str(data.get("request_kind") or "")
        self.user_id = data.get("user_id")
        self.group_id = data.get("group_id")
        self.message_id = data.get("message_id")
        self.time = data.get("time")
        self.text = str(data.get("text") or "")[:4000]
        self.at_list = [str(a) for a in (data.get("at_list") or [])]
        self.images = [str(i) for i in (data.get("images") or [])]
        self.reply_id = data.get("reply_id")
        self.message = BotMessage(self.text, at_list=self.at_list,
                                  images=self.images, reply_id=self.reply_id)
        self._bot = bot
        self._stopped = False
        self._event_dict = dict(data)

    @property
    def is_group(self) -> bool:
        return self.scope == "group"

    @property
    def is_private(self) -> bool:
        return self.scope == "private"

    @property
    def matcher_name(self) -> str:
        return self._bot._matched_name if self._bot is not None else ""

    @property
    def args(self) -> list:
        """命令参数（shlex 拆分；@command 命中后）。"""
        import shlex
        try:
            return shlex.split(str(self.matcher_args or ""))
        except ValueError:
            return str(self.matcher_args or "").split()

    @property
    def raw_message(self) -> str:
        return str(self.text or "")

    @property
    def schedule_id(self) -> str:
        return str(self._event_dict.get("schedule_id") or "")

    @property
    def trigger(self) -> str:
        return str(self._event_dict.get("trigger") or "")

    @property
    def matcher_args(self) -> str:
        return self._bot._last_args if self._bot is not None else ""

    def stop(self) -> None:
        self._stopped = True

    @property
    def stopped(self) -> bool:
        return self._stopped

    async def reply(self, message=None, **kwargs) -> Optional[int]:
        if self._bot is None:
            raise RuntimeError("Event 未绑定 bot")
        return await self._bot.reply(self, message, **kwargs)

    async def recall(self) -> None:
        if self._bot is None:
            raise RuntimeError("Event 未绑定 bot")
        if not self.message_id:
            raise ValueError("当前事件没有 message_id")
        await self._bot.recall(self.message_id)
