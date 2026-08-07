from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Deque
from collections import deque
import time

@dataclass
class BotDependencies:
    config: Any
    memory_manager: Any
    ai_client: Any
    file_parser: Any
    sender: Any
    policy_engine: Any
    message_router: Any
    ws_server: Any

# ---------- 群状态（按群隔离） ----------
@dataclass
class GroupState:
    context: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=300))
    user_last_time: Dict[str, float] = field(default_factory=dict)  # key: f"{user_id}"
    group_last_reply_time: float = 0.0
    consecutive_replies: int = 0
    block_until: float = 0.0
    processed_msg_ids: Deque[int] = field(default_factory=lambda: deque(maxlen=1000))
    recent_bot_replies: Deque[str] = field(default_factory=lambda: deque(maxlen=30))
    repeat_cache: Dict[str, float] = field(default_factory=dict)    # key: f"{content}"
    msg_timestamps: Dict[str, Deque[float]] = field(default_factory=dict)

@dataclass
class GlobalState:
    bot_last_reply_global: float = 0.0
    last_active_chat_time: float = 0.0
    next_random_active_time: float = 0.0
    consecutive_active_count: int = 0
    active_cooldown_until: float = 0.0
    last_user_message_time: float = time.time()
    ws_connected: bool = False
    poke_recent_replies: Deque[str] = field(default_factory=lambda: deque(maxlen=5))
    last_toxic_warning: Dict[int, float] = field(default_factory=dict)
    pending_files: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # key: f"{user_id}_{group_id}"

# ---------- 消息结构 ----------
@dataclass
class GroupMessage:
    group_id: int
    user_id: int
    message_id: int
    raw_message: str
    message_array: List[Dict[str, Any]]
    time: int
    clean_text: str = ""
    is_mentioned: bool = False
    is_reply_to_bot: bool = False
    has_reply_to_other: bool = False
    has_at_others: bool = False
    full_text: str = ""  # after parsing files/forward/card