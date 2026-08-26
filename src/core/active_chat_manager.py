import time
import random
from typing import Dict
from loguru import logger

from src.config import Settings
from src.models import GroupState, GlobalState


class ActiveChatManager:
    """主动聊天决策：间隔触发、夜间静默、连续主动冷却。"""

    def __init__(self, config: Settings, groups: Dict[int, GroupState], global_state: GlobalState):
        self.config = config
        self.groups = groups
        self.global_state = global_state

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState()
        return self.groups[group_id]

    def can_bot_reply(self, group_id: int) -> bool:
        """复用机器人冷却判断（与 CooldownManager 同一逻辑，避免循环依赖）。"""
        from src.core.cooldown_manager import CooldownManager
        return CooldownManager(self.config, self.groups, self.global_state).can_bot_reply(group_id)

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
