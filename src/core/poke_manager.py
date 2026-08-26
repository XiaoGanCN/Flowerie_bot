import random
from typing import Dict
from loguru import logger

from src.config import Settings
from src.models import GlobalState


class PokeManager:
    """戳戳回复：预设回复池随机选择，缓存最近 5 条去重，避免连续说同一句。"""

    def __init__(self, config: Settings, global_state: GlobalState):
        self.config = config
        self.global_state = global_state

    def get_poke_reply(self) -> str:
        available = [r for r in self.config.POKE_REPLIES if r not in self.global_state.poke_recent_replies]
        if not available:
            reply = random.choice(self.config.POKE_REPLIES)
        else:
            reply = random.choice(available)
        self.global_state.poke_recent_replies.append(reply)
        return reply
