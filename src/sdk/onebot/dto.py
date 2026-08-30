"""下层：OneBot DTO 瘦身（只映射本 bot 用到的字段，其余一律忽略）。

OneBot v11 事件有 20+ 字段，这里只取领域需要的少数；多余字段不进入
任何结构体定义（map 兜底仅用于内部低级需求，不向上传播）。
"""
from typing import Any, Dict


class EventDTO:
    """OneBot 事件的最小数据槽（字段名保留 OneBot 字面量，仅存在于下层）。"""

    __slots__ = ("post_type", "message_type", "notice_type", "request_type",
                 "meta_event_type", "sub_type", "user_id", "group_id",
                 "operator_id", "message_id", "time", "message", "raw_message", "extras")

    def __init__(self, raw: Dict[str, Any]):
        self.post_type = str(raw.get("post_type") or "unknown")
        self.message_type = str(raw.get("message_type") or "")
        self.notice_type = str(raw.get("notice_type") or "")
        self.request_type = str(raw.get("request_type") or "")
        self.meta_event_type = str(raw.get("meta_event_type") or "")
        self.sub_type = str(raw.get("sub_type") or "")
        self.user_id = raw.get("user_id")
        self.group_id = raw.get("group_id")
        self.operator_id = raw.get("operator_id") or raw.get("user_id")
        self.message_id = raw.get("message_id")
        self.time = raw.get("time")
        self.message = raw.get("message")            # str（CQ 码）或 list（段数组）
        self.raw_message = str(raw.get("raw_message") or "")[:4000]
        self.extras: Dict[str, Any] = raw            # 兜底（仅下层内部使用）
