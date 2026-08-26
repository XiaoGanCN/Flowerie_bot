import time
from collections import deque
from typing import Dict
from loguru import logger

from src.config import Settings
from src.models import GroupState


class RepeatDetector:
    """复读检测：时间窗口内同一条消息出现 N 次触发复读（带复读冷却，防刷屏）。"""

    def __init__(self, config: Settings, groups: Dict[int, GroupState]):
        self.config = config
        self.groups = groups

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState()
        return self.groups[group_id]

    def check_and_record_repeat(self, content: str, group_id: int) -> bool:
        """返回 True 表示应复读"""
        if not content or len(content.strip()) < 2 or content.startswith("/"):
            return False
        state = self.get_group_state(group_id)
        cache_key = content
        now = time.time()
        if cache_key not in state.msg_timestamps:
            state.msg_timestamps[cache_key] = deque()
        queue = state.msg_timestamps[cache_key]
        window = self.config.REPEAT_WINDOW
        while queue and now - queue[0] > window:
            queue.popleft()
        queue.append(now)
        if len(queue) >= self.config.REPEAT_THRESHOLD:
            last_repeat = state.repeat_cache.get(cache_key, 0)
            if now - last_repeat > window:
                state.repeat_cache[cache_key] = now
                return True
        return False
