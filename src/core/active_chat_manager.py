import random
import time
from typing import Dict, Optional

from src.config import Settings
from src.core.cooldown_manager import CooldownManager
from src.models import GlobalState, GroupState
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class ActiveChatManager:
    """主动聊天决策：间隔触发、夜间静默、连续主动冷却。"""

    def __init__(
        self,
        config: Settings,
        groups: Dict[int, GroupState],
        global_state: GlobalState,
        cooldown: Optional[CooldownManager] = None,
    ):
        self.config = config
        self.groups = groups
        self.global_state = global_state
        self.cooldown = cooldown  # 由门面注入，避免重复实例化

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState(context_size=getattr(self.config, "CONTEXT_SIZE", 300))
        return self.groups[group_id]

    def can_bot_reply(self, group_id: int) -> bool:
        """复用机器人冷却判断（优先用注入的 CooldownManager）。"""
        if self.cooldown is None:
            self.cooldown = CooldownManager(self.config, self.groups, self.global_state)
        return self.cooldown.can_bot_reply(group_id)

    def should_active_chat(self, group_id: int) -> bool:
        now = time.time()
        if now - self.global_state.last_active_chat_time < self.config.ACTIVE_CHAT_COOLDOWN:
            return False
        if now < self.global_state.active_cooldown_until:
            return False
        if not self.can_bot_reply(group_id):
            return False
        hour = time.localtime(now).tm_hour
        if self.config.NIGHT_SILENCE_START <= hour < self.config.NIGHT_SILENCE_END:
            return False
        if random.random() < 0.10:
            return True
        return False

    def record_active_chat(self) -> None:
        now = time.time()
        prev = self.global_state.last_active_chat_time
        self.global_state.last_active_chat_time = now
        if now - prev < 600:
            self.global_state.consecutive_active_count += 1
        else:
            self.global_state.consecutive_active_count = 1
        if self.global_state.consecutive_active_count >= 2:
            self.global_state.active_cooldown_until = now + 1800
            logger.info("Entered 30min active cooldown after 2 consecutive active chats")
