from typing import Dict, Tuple

from src.config import Settings
from src.models import GroupState, GlobalState
from src.services.memory_manager import MemoryManager

from src.core.context_manager import ContextManager
from src.core.cooldown_manager import CooldownManager
from src.core.repeat_detector import RepeatDetector
from src.core.memory_parser import MemoryParser
from src.core.poke_manager import PokeManager
from src.core.active_chat_manager import ActiveChatManager


class PolicyEngine:
    """策略门面：聚合各职责管理器，对外保持原有 API（MessageRouter 无需改动）。

    原上帝类拆分为：
    - ContextManager     上下文读写 / 接话概率 / 重复回复 / 崩溃备份
    - CooldownManager    用户与机器人冷却 / 连续回复惩罚
    - RepeatDetector     复读检测
    - MemoryParser       记忆指令解析 / 强制记忆触发
    - PokeManager        戳戳回复去重
    - ActiveChatManager  主动聊天决策
    """

    def __init__(self, config: Settings, memory_manager: MemoryManager):
        self.config = config
        self.memory_manager = memory_manager
        self.global_state = GlobalState()
        self.groups: Dict[int, GroupState] = {}

        self.context = ContextManager(config, self.groups, self.global_state)
        self.cooldown = CooldownManager(config, self.groups, self.global_state)
        self.repeat = RepeatDetector(config, self.groups)
        self.memory = MemoryParser()
        self.poke = PokeManager(config, self.global_state)
        self.active_chat = ActiveChatManager(config, self.groups, self.global_state, cooldown=self.cooldown)

        self.context.load_context_backup()  # 意外去世后重启：恢复上次保存的上下文

    # ---------- 委托：群状态 / 上下文 ----------
    def get_group_state(self, group_id: int) -> GroupState:
        return self.context.get_group_state(group_id)

    def add_context(self, group_id: int, user_id: int, message: str, is_bot: bool = False) -> None:
        return self.context.add_context(group_id, user_id, message, is_bot)

    def get_context_text(self, group_id: int, max_messages: int = 150) -> str:
        return self.context.get_context_text(group_id, max_messages)

    def should_reply_by_context(self, group_id: int) -> bool:
        return self.context.should_reply_by_context(group_id)

    def is_duplicate_reply(self, group_id: int, reply: str) -> bool:
        return self.context.is_duplicate_reply(group_id, reply)

    def add_recent_reply(self, group_id: int, reply: str) -> None:
        return self.context.add_recent_reply(group_id, reply)

    async def save_context_backup(self) -> None:
        return await self.context.save_context_backup()

    # ---------- 委托：冷却 ----------
    def can_user_reply(self, user_id: int, group_id: int) -> bool:
        return self.cooldown.can_user_reply(user_id, group_id)

    def update_user_time(self, user_id: int, group_id: int) -> None:
        return self.cooldown.update_user_time(user_id, group_id)

    def can_bot_reply(self, group_id: int) -> bool:
        return self.cooldown.can_bot_reply(group_id)

    def record_bot_reply(self, group_id: int) -> None:
        return self.cooldown.record_bot_reply(group_id)

    # ---------- 委托：复读 ----------
    def check_and_record_repeat(self, content: str, group_id: int) -> bool:
        return self.repeat.check_and_record_repeat(content, group_id)

    # ---------- 委托：记忆解析 ----------
    def parse_memory_update(self, memory_update: str, default_user_id: int) -> Tuple[int, str]:
        return self.memory.parse_memory_update(memory_update, default_user_id)

    def should_force_memory(self, clean_text: str, full_text: str, has_at_others: bool) -> bool:
        return self.memory.should_force_memory(clean_text, full_text, has_at_others)

    # ---------- 委托：戳戳 ----------
    def get_poke_reply(self) -> str:
        return self.poke.get_poke_reply()

    # ---------- 委托：主动聊天 ----------
    def should_active_chat(self, group_id: int) -> bool:
        return self.active_chat.should_active_chat(group_id)

    def record_active_chat(self) -> None:
        return self.active_chat.record_active_chat()
