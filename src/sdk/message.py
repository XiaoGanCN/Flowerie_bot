"""中层消息模型：BotMessage（领域语义，无 OneBot 命名）。

插件（上层）看到的永远是干净的结构：text / at_list / images / reply_id。
OneBot 段/CQ 的处理只发生在下层 src/sdk/onebot/。
提供 Builder（链式构造）：msg = BotMessage().text("hi").at(123).image(url)
"""
from typing import Any, Dict, Iterator, List, Optional


class BotMessage:
    """领域消息：纯文本 + 结构化附件（at/image/reply/mention）。"""

    def __init__(self, text: str = "", *, at_list: Optional[List] = None,
                 images: Optional[List[str]] = None, reply_id: Optional[int] = None,
                 raw: Optional[Any] = None):
        self.text = str(text or "")
        self.at_list: List[str] = [str(a) for a in (at_list or [])]
        self.images: List[str] = [str(i) for i in (images or [])]
        self.reply_id = int(reply_id) if reply_id is not None else None
        # 下层/进阶用：原始段信息（默认不向外暴露 OneBot 语义）
        self._extra: Dict[str, Any] = dict(raw or {})

    # ---------- Builder（链式） ----------
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

    # ---------- 查询 ----------
    def has(self, kind: str) -> bool:
        if kind in ("at", "mention"):
            return bool(self.at_list)
        if kind in ("image", "img"):
            return bool(self.images)
        if kind == "reply":
            return self.reply_id is not None
        if kind == "text":
            return bool(self.text)
        return kind in self._extra

    def __bool__(self) -> bool:
        return bool(self.text or self.at_list or self.images or self.reply_id)

    def __iter__(self) -> Iterator[Any]:
        """迭代：按顺序产出 [(kind, value)]（text/at/image/reply 在 onebot 下层定型）。"""
        if self.text:
            yield ("text", self.text)
        for a in self.at_list:
            yield ("at", a)
        for img in self.images:
            yield ("image", img)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BotMessage text={self.text[:40]!r} at={self.at_list} img={len(self.images)}>"
