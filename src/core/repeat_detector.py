import time
from collections import deque
from typing import Dict
from loguru import logger

from src.config import Settings
from src.models import GroupState


class RepeatDetector:
    """复读检测：时间窗口内同一条消息出现 N 次触发复读（带复读冷却，防刷屏）。

    内存防护：只跟踪前 _MAX_TRACKED_CONTENT 长度的内容，且每个群最多缓存
    _MAX_CACHE_ENTRIES 个不同内容（超限淘汰最旧），防止长时间运行内存无限增长。
    """

    _MAX_TRACKED_CONTENT = 500
    _MAX_CACHE_ENTRIES = 200
    _EVICT_BATCH = 50

    def __init__(self, config: Settings, groups: Dict[int, GroupState]):
        self.config = config
        self.groups = groups

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState(context_size=getattr(self.config, "CONTEXT_SIZE", 300))
        return self.groups[group_id]

    def check_and_record_repeat(self, content: str, group_id: int) -> bool:
        """返回 True 表示应复读"""
        if not content or len(content.strip()) < 2 or content.startswith("/"):
            return False
        state = self.get_group_state(group_id)
        # 超长内容不参与复读（文件/转发全文不应触发"复读"，也避免超长 key 占内存）
        if len(content) > self._MAX_TRACKED_CONTENT:
            return False
        cache_key = content
        now = time.time()
        if cache_key not in state.msg_timestamps:
            state.msg_timestamps[cache_key] = deque()
        queue = state.msg_timestamps[cache_key]
        window = self.config.REPEAT_WINDOW
        while queue and now - queue[0] > window:
            queue.popleft()
        queue.append(now)
        # 缓存上限：淘汰最旧条目（dict 保持插入序，近似 LRU）
        if len(state.msg_timestamps) > self._MAX_CACHE_ENTRIES:
            for k in list(state.msg_timestamps)[:self._EVICT_BATCH]:
                state.msg_timestamps.pop(k, None)
        if len(state.repeat_cache) > self._MAX_CACHE_ENTRIES:
            for k in list(state.repeat_cache)[:self._EVICT_BATCH]:
                state.repeat_cache.pop(k, None)
        if len(queue) >= self.config.REPEAT_THRESHOLD:
            last_repeat = state.repeat_cache.get(cache_key, 0)
            if now - last_repeat > window:
                state.repeat_cache[cache_key] = now
                return True
        return False
