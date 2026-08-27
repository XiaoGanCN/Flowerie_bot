import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List

from src.utils.expiring_map import ExpiringMap


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
class GroupState:
    """群级运行状态。context 容量由 CONTEXT_SIZE 配置决定（默认 300）。"""

    def __init__(self, context_size: int = 300):
        self.context: Deque[Dict[str, Any]] = deque(maxlen=max(10, int(context_size or 300)))
        # 用户冷却：ExpiringMap 自治 TTL（24h），不依赖外部清理循环
        self.user_last_time: ExpiringMap = ExpiringMap(ttl_seconds=86400)  # key: f"{user_id}"
        self.group_last_reply_time: float = 0.0
        self.last_activity: float = time.time()  # 群最近活动时间（inactive 群清理用）
        self.consecutive_replies: int = 0
        self.block_until: float = 0.0
        self.processed_msg_ids: Deque[int] = deque(maxlen=1000)
        self.recent_bot_replies: Deque[str] = deque(maxlen=30)
        self.repeat_cache: Dict[str, float] = {}  # key: f"{content}"
        self.msg_timestamps: Dict[str, Deque[float]] = {}


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
    # 以下 TTL 状态用 ExpiringMap 自治生命周期（24h），不依赖 backup loop
    poke_last_time: ExpiringMap = field(default_factory=lambda: ExpiringMap(ttl_seconds=86400))
    last_toxic_warning: ExpiringMap = field(default_factory=lambda: ExpiringMap(ttl_seconds=86400))
    pending_files: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # key: f"{user_id}_{group_id}"
    # 每日 AI 调用预算（P1-5）：记录"哪天"用了"多少次"
    ai_budget_date: str = ""
    ai_budget_count: int = 0
    group_ai_budget_count: Dict[int, int] = field(default_factory=dict)  # 每群每日 AI 调用次数
    user_ai_last_call: ExpiringMap = field(default_factory=lambda: ExpiringMap(ttl_seconds=86400))
    budget_notified_groups: Dict[int, str] = field(default_factory=dict)  # 群 -> 已提示额度的日期
    # 熔断状态已迁移到 MessageRouter 持有的 CircuitBreaker（provider 级 + 群级），
    # 不再放在全局状态里（详见 src/core/message_router.py 与 src/utils/circuit_breaker.py）


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
