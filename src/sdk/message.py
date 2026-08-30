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
                 videos: Optional[List[str]] = None, voices: Optional[List[str]] = None,
                 files: Optional[List[str]] = None, raw: Optional[Any] = None):
        self.text = str(text or "")
        self.at_list: List[str] = [str(a) for a in (at_list or [])]
        self.images: List[str] = [str(i) for i in (images or [])]
        self.videos: List[str] = [str(v) for v in (videos or [])]
        self.voices: List[str] = [str(v) for v in (voices or [])]
        self.files: List[str] = [str(f) for f in (files or [])]
        self.reply_id = int(reply_id) if reply_id is not None else None
        # 进阶/平台相关段（如 keyboard/json 卡片）：仅高级用路由出，不保证所有平台支持
        self.segments: List[Dict[str, Any]] = list(raw if isinstance(raw, list) else [])
        self._extra: Dict[str, Any] = {}

    # ---------- Builder（链式） ----------
    def add_text(self, value: Any) -> "BotMessage":
        """Builder：追加文本（读取请用 .text 属性）。"""
        self.text = str(self.text or "") + str(value or "")
        return self

    def at(self, user_id: Any) -> "BotMessage":
        self.at_list.append(str(user_id))
        return self

    def image(self, url_or_file: str) -> "BotMessage":
        """图片：http(s) URL 或本地文件路径（file:// 前缀或绝对路径）。"""
        self.images.append(str(url_or_file))
        return self

    def video(self, url_or_file: str) -> "BotMessage":
        self.videos.append(str(url_or_file))
        return self

    def voice(self, url_or_file: str) -> "BotMessage":
        """语音：file:// 路径或 URL（OneBot record 段）。"""
        self.voices.append(str(url_or_file))
        return self

    def file(self, url_or_file: str, name: Optional[str] = None) -> "BotMessage":
        """文件：远程 URL 或本地路径；可附显示名（部分平台支持 name）。"""
        self.files.append(str(url_or_file))
        if name:
            self._extra.setdefault("file_names", {})[str(url_or_file)] = str(name)
        return self

    def add_segment(self, seg_type: str, data: Dict[str, Any]) -> "BotMessage":
        """通用段（高级）：如键盘 UI 等平台相关扩展。兼容性以平台为准。"""
        self.segments.append({"type": str(seg_type), "data": dict(data or {})})
        return self

    def reply(self, message_id: int) -> "BotMessage":
        self.reply_id = int(message_id)
        return self

    # ---------- 查询 ----------
    def has(self, kind: str) -> bool:
        alias = {"at": "at_list", "mention": "at_list", "image": "images",
                 "img": "images", "video": "videos", "voice": "voices",
                 "record": "voices", "file": "files", "reply": "_reply_flag",
                 "text": "text"}
        key = alias.get(kind)
        if key == "_reply_flag":
            return self.reply_id is not None
        if key == "text":
            return bool(self.text)
        if key and getattr(self, key, None) is not None:
            return bool(getattr(self, key))
        return False

    def __bool__(self) -> bool:
        return bool(self.text or self.at_list or self.images or self.videos or
                    self.voices or self.files or self.reply_id or self.segments)

    def __iter__(self) -> Iterator[Any]:
        """迭代：按顺序产出 [(kind, value)]——text/at/image/video/voice/file/reply。"""
        if self.text:
            yield ("text", self.text)
        for a in self.at_list:
            yield ("at", a)
        for img in self.images:
            yield ("image", img)
        for v in self.videos:
            yield ("video", v)
        for v in self.voices:
            yield ("voice", v)
        for f in self.files:
            yield ("file", f)

    def merge(self, other: "BotMessage") -> "BotMessage":
        """合并另一消息（链式组合）。"""
        self.text = str(self.text or "") + str(other.text or "")
        self.at_list.extend(other.at_list)
        self.images.extend(other.images)
        self.videos.extend(other.videos)
        self.voices.extend(other.voices)
        self.files.extend(other.files)
        self.segments.extend(other.segments)
        return self

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BotMessage text={self.text[:40]!r} at={self.at_list} img={len(self.images)}>"
