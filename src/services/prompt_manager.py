"""PromptManager：自定义 Prompt 业务层（全局 / 群聊两级，SQLite 持久化）。

优先级（由调用方组装时决定）：群 Prompt > 全局 Prompt > 内置人设 Prompt。

安全边界：
- 自定义 Prompt 仅作为"人格/行为补充"，永远低于系统安全规则（组装位置在
  【输入安全声明】之前、系统规则之后）
- 长度受限（MAX_CUSTOM_PROMPT_LENGTH）
- 修改权限由 CommandHandler 的管理员校验负责
"""
from typing import Optional

from src.repositories.settings_repository import SettingsRepository
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class PromptManager:
    """自定义 Prompt 管理：读取/设置/重置全局与群级 Prompt。"""

    def __init__(self, repository: SettingsRepository, max_length: int = 2000):
        self.repository = repository
        self.max_length = max(100, int(max_length))

    # ---------- 读取 ----------
    def get_effective_prompt(self, group_id: Optional[int]) -> str:
        """返回群聊生效的自定义 Prompt（群 > 全局 > 空）。"""
        if group_id is not None:
            group_prompt = self.repository.get_prompt("group", group_id)
            if group_prompt:
                return group_prompt
        global_prompt = self.repository.get_prompt("global")
        return global_prompt or ""

    def get_global_prompt(self) -> str:
        return self.repository.get_prompt("global") or ""

    def get_group_prompt(self, group_id: int) -> str:
        return self.repository.get_prompt("group", group_id) or ""

    # ---------- 写入（长度校验；权限由调用方负责） ----------
    def set_global_prompt(self, content: str) -> str:
        """设置全局 Prompt，返回规范化后的内容。"""
        content = content.strip()
        if len(content) > self.max_length:
            raise ValueError(f"Prompt 过长：{len(content)} 字（上限 {self.max_length}）")
        self.repository.set_prompt("global", content)
        logger.info("prompt_updated scope=global", extra={"event": "prompt_updated"})
        return content

    def reset_global_prompt(self) -> bool:
        removed = self.repository.delete_prompt("global")
        if removed:
            logger.info("prompt_updated scope=global action=reset", extra={"event": "prompt_updated"})
        return removed

    def set_group_prompt(self, group_id: int, content: str) -> str:
        content = content.strip()
        if len(content) > self.max_length:
            raise ValueError(f"Prompt 过长：{len(content)} 字（上限 {self.max_length}）")
        self.repository.set_prompt("group", content, group_id)
        logger.info("prompt_updated scope=group group=%s", group_id, extra={"event": "prompt_updated"})
        return content

    def reset_group_prompt(self, group_id: int) -> bool:
        removed = self.repository.delete_prompt("group", group_id)
        if removed:
            logger.info("prompt_updated scope=group group=%s action=reset", group_id, extra={"event": "prompt_updated"})
        return removed
