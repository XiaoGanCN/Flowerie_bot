"""下层：OneBotV11Transformer —— OneBot 原始事件 → 中层 BotEvent。

职责（机械转换，无业务逻辑）：
- DTO 瘦身取值（只用到的字段）
- CQ 段码 → 结构化 at_list / images（**中间层阉割**：插件永远看不到 CQ 码）
- 纯文本提取（text 段拼接）
"""
import re
from typing import Any, Dict, List

from src.sdk.event import BotEvent
from src.sdk.message import BotMessage
from src.sdk.onebot.dto import EventDTO

# [CQ:at,qq=123] / [CQ:at,qq=123,name=x]（qq 可能为 all）
_CQ = re.compile(r"\[CQ:([a-zA-Z0-9_]+)((?:,[^\[\]]*)*)\]")


def extract_text(message: Any) -> str:
    """OneBot message（str/CQ 码或段数组）→ 纯文本（text 段拼接；CQ 段跳过）。"""
    if isinstance(message, str):
        return _strip_cq(message)
    if isinstance(message, list):
        parts = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            if seg.get("type") == "text":
                parts.append(str(seg.get("data", {}).get("text", "")))
        return "".join(parts)
    return str(message or "")


def _strip_cq(text: str) -> str:
    out = []
    pos = 0
    for m in _CQ.finditer(text):
        out.append(text[pos:m.start()])
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def extract_at_list(message: Any) -> List[str]:
    """提取 @ 用户列表（CQ at 段/码；qq=all 记为 "all"）。"""
    result: List[str] = []
    if isinstance(message, str):
        for m in _CQ.finditer(message):
            if m.group(1) != "at":
                continue
            data = dict(_parse_params(m.group(2)))
            qq = data.get("qq")
            if qq:
                result.append(str(qq))
    elif isinstance(message, list):
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "at":
                qq = seg.get("data", {}).get("qq")
                if qq:
                    result.append(str(qq))
    return result


def extract_images(message: Any) -> List[str]:
    """提取图片列表（图 URL 或本地路径）。"""
    result: List[str] = []
    if isinstance(message, str):
        for m in _CQ.finditer(message):
            if m.group(1) != "image":
                continue
            data = dict(_parse_params(m.group(2)))
            url = data.get("url") or data.get("file")
            if url:
                result.append(str(url))
    elif isinstance(message, list):
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "image":
                data = seg.get("data", {}) or {}
                url = data.get("url") or data.get("file")
                if url:
                    result.append(str(url))
    return result


def extract_reply_id(message: Any) -> Any:
    """引用回复的 message_id（reply 段/CQ）。"""
    if isinstance(message, str):
        for m in _CQ.finditer(message):
            if m.group(1) == "reply":
                data = dict(_parse_params(m.group(2)))
                if data.get("id"):
                    return data["id"]
    elif isinstance(message, list):
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                return seg.get("data", {}).get("id")
    return None


def _parse_params(param_str: str) -> List[tuple]:
    out = []
    for kv in param_str.strip(",").split(","):
        if not kv.strip():
            continue
        k, _, v = kv.partition("=")
        if k.strip():
            out.append((k.strip(), v.lstrip("=")))
    return out


def to_bot_event(raw: Dict[str, Any]) -> BotEvent:
    """OneBot 原始事件 dict → 中层 BotEvent（唯一转换入口）。"""
    dto = EventDTO(raw)
    kind = dto.post_type
    message = dto.message
    text = extract_text(message) or dto.raw_message
    if kind == "message":
        kind_domain = "message"
    elif kind == "meta_event":
        kind_domain = "lifecycle"
    else:
        kind_domain = kind if kind in ("notice", "request") else "unknown"
    data: Dict[str, Any] = {
        "kind": kind_domain,
        "scope": dto.message_type or ("group" if dto.group_id else ""),
        "notice_kind": dto.notice_type,
        "request_kind": dto.request_type,
        "lifecycle_kind": dto.meta_event_type,
        "user_id": dto.user_id,
        "group_id": dto.group_id,
        "operator_id": dto.operator_id,
        "message_id": dto.message_id,
        "time": dto.time,
        "text": text[:4000],
        "at_list": extract_at_list(message)[:20],
        "images": extract_images(message)[:10],
        "reply_id": extract_reply_id(message),
    }
    return BotEvent.from_dict(data)


def to_bot_message_payload(message: Any) -> Any:
    """中层向下一层发送转换：BotMessage/str → OneBot 段数组（下层唯一出站转换）。

    CQ 码在此由下层拼装：插件构造的领域消息结构（text/at/image/reply）→ 段数组。
    """
    if isinstance(message, BotMessage):
        segments: List[Dict[str, Any]] = []
        if message.reply_id is not None:
            segments.append({"type": "reply", "data": {"id": int(message.reply_id)}})
        if message.text:
            segments.append({"type": "text", "data": {"text": message.text}})
        for uid in message.at_list:
            segments.append({"type": "at", "data": {"qq": str(uid)}})
        for img in message.images:
            segments.append({"type": "image", "data": {"file": str(img)}})
        for v in message.videos:
            segments.append({"type": "video", "data": {"file": str(v)}})
        for v in message.voices:
            segments.append({"type": "record", "data": {"file": str(v)}})
        for f in message.files:
            seg: Dict[str, Any] = {"type": "file", "data": {"file": str(f)}}
            name = message._extra.get("file_names", {}).get(str(f))
            if name:
                seg["data"]["name"] = str(name)
            segments.append(seg)
        segments.extend(message.segments)   # 进阶/平台相关段（keyboard/json 等）
        return segments
    return message  # str/CQ 或段数组原样透传（由 OneBot 服务端解析）
