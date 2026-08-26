import time
from typing import Tuple
from datetime import datetime
from loguru import logger

from src.config import Settings
from src.models import GlobalState
from src.services.sender import Sender


class BudgetManager:
    """AI 调用预算（从 MessageRouter 拆分）：全局 + 每群 + 每用户三层限速。

    - 全局预算：整个 Bot 每日 AI 调用上限，防 API 额度被刷爆
    - 群级预算：防止一个群刷光全局额度
    - 用户限速：同一用户两次 AI 回复的最小间隔
    - 额度用尽提示：每天每群最多发一次，避免刷屏
    """

    def __init__(self, config: Settings, global_state: GlobalState, sender: Sender):
        self.config = config
        self.global_state = global_state
        self.sender = sender

    def check(self, group_id: int, user_id: int, user_interval: bool = True) -> Tuple[bool, str]:
        """返回 (是否允许, 拒绝原因)。原因: ''(允许) / 'user'(用户限速) / 'global'(全局预算) / 'group'(群预算)。

        user_interval=False：不检查也不更新用户聊天限速（用于引战检测等旁路调用，
        避免"先 toxic 后 chat"把同一条消息自己的限速触发掉）。
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self.global_state.ai_budget_date != today:
            # 跨天重置
            self.global_state.ai_budget_date = today
            self.global_state.ai_budget_count = 0
            self.global_state.group_ai_budget_count.clear()
            self.global_state.budget_notified_groups.clear()

        # 用户级限速（per-user rate limit，只对聊天主路径生效）
        if user_interval and self.config.USER_AI_CALL_MIN_INTERVAL > 0:
            last = self.global_state.user_ai_last_call.get(user_id, 0.0)
            if time.time() - last < self.config.USER_AI_CALL_MIN_INTERVAL:
                return False, "user"

        # 全局预算
        self.global_state.ai_budget_count += 1
        if self.config.DAILY_AI_CALL_BUDGET > 0 and self.global_state.ai_budget_count > self.config.DAILY_AI_CALL_BUDGET:
            return False, "global"

        # 群级预算
        gcount = self.global_state.group_ai_budget_count.get(group_id, 0) + 1
        self.global_state.group_ai_budget_count[group_id] = gcount
        if self.config.GROUP_DAILY_AI_CALL_BUDGET > 0 and gcount > self.config.GROUP_DAILY_AI_CALL_BUDGET:
            return False, "group"

        if user_interval:
            self.global_state.user_ai_last_call[user_id] = time.time()
        return True, ""

    async def notify_exhausted(self, group_id: int) -> None:
        """额度用尽提示：每天每群最多发一次，避免刷屏。"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.global_state.budget_notified_groups.get(group_id) == today:
            return
        self.global_state.budget_notified_groups[group_id] = today
        cap = self.config.GROUP_DAILY_AI_CALL_BUDGET or self.config.DAILY_AI_CALL_BUDGET or 0
        used = self.global_state.ai_budget_count
        try:
            await self.sender.send_group_message(
                group_id, f"今日AI额度已用尽（已用{used}次/上限{cap}次），明天再来找花璃玩吧～"
            )
        except Exception as e:
            logger.error(f"额度提示发送失败: {e}")
