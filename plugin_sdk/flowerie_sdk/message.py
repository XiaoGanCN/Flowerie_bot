"""插件侧消息模型：BotMessage（领域语义 + Builder，与中层对称）。

插件构造：msg = BotMessage().text("hi").at(123).image(url)
"""
from typing import Any, Iterator, List, Optional


class BotMessage:
    def __init__(self, text: str = "", *, at_list: Optional[List] = None,
                 images: Optional[List[str]] = None, reply_id: Optional[int] = None):
        self.text = str(text or "")
        self.at_list: List[str] = [str(a) for a in (at_list or [])]
        self.images: List[str] = [str(i) for i in (images or [])]
        self.reply_id = int(reply_id) if reply_id is not None else None

    def add_text(self, value: Any) -> "BotMessage":
        """Builder：追加文本（读取请用 .text 属性）。"""
        self.text = str(self.text or "") + str(value or "")
        return self

    def at(self, user_id: Any) -> "BotMessage":
        self.at_list.append(str(user_id))
        return self

    def image(self, url_or_file: str) -> "BotMessage":
        self.images.append(str(url_or_file))
        return self

    def reply(self, message_id: int) -> "BotMessage":
        self.reply_id = int(message_id)
        return self

    def has(self, kind: str) -> bool:
        if kind in ("at", "mention"):
            return bool(self.at_list)
        if kind in ("image", "img"):
            return bool(self.images)
        if kind == "reply":
            return self.reply_id is not None
        if kind == "text":
            return bool(self.text)
        return False

    def __bool__(self) -> bool:
        return bool(self.text or self.at_list or self.images or self.reply_id)

    def __iter__(self) -> Iterator[Any]:
        if self.text:
            yield ("text", self.text)
        for a in self.at_list:
            yield ("at", a)
        for img in self.images:
            yield ("image", img)

    def merge(self, other: "BotMessage") -> "BotMessage":
        """合并另一消息（链式组合）。"""
        self.text = str(self.text or "") + str(other.text or "")
        self.at_list.extend(other.at_list)
        self.images.extend(other.images)
        return self
