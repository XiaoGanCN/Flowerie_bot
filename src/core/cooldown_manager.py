import time
from typing import Dict
from loguru import logger

from src.config import Settings
from src.models import GroupState, GlobalState


class CooldownManager:
    """冷却管理：用户级冷却、机器人级冷却、连续回复惩罚（按群隔离）。"""

    def __init__(self, config: Settings, groups: Dict[int, GroupState], global_state: GlobalState):
        self.config = config
        self.groups = groups
        self.global_state = global_state

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState()
        return self.groups[group_id]

    # ---------- 用户冷却 ----------
    def can_user_reply(self, user_id: int, group_id: int) -> bool:
        state = self.get_group_state(group_id)
        last = state.user_last_time.get(str(user_id), 0.0)
        return (time.time() - last) >= self.config.USER_COOLDOWN

    def update_user_time(self, user_id: int, group_id: int) -> None:
        state = self.get_group_state(group_id)
        state.user_last_time[str(user_id)] = time.time()

    # ---------- 机器人冷却 ----------
    def can_bot_reply(self, group_id: int) -> bool:
        now = time.time()
        if now - self.global_state.bot_last_reply_global < self.config.BOT_COOLDOWN:
            logger.debug("Global bot cooldown")
            return False
        state = self.get_group_state(group_id)
        if now < state.block_until:
            logger.debug(f"Group {group_id} blocked until {state.block_until}")
            return False
        return True

    def record_bot_reply(self, group_id: int) -> None:
        now = time.time()
        self.global_state.bot_last_reply_global = now
        state = self.get_group_state(group_id)
        state.group_last_reply_time = now
        state.consecutive_replies += 1
        logger.debug(f"Group {group_id} consecutive replies: {state.consecutive_replies}")
        if state.consecutive_replies >= self.config.MAX_CONSECUTIVE_REPLIES:
            state.block_until = now + self.config.BOT_CONSECUTIVE_REPLY_COOLDOWN
            state.consecutive_replies = 0
            logger.info(f"Group {group_id} entered cooldown for {self.config.BOT_CONSECUTIVE_REPLY_COOLDOWN}s")
