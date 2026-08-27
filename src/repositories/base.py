"""存储仓库抽象接口。

业务层（MemoryManager）只依赖本接口，不感知任何 SQL / 存储引擎细节。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class MemoryNote:
    """一条记忆记录（存储无关的数据载体）。"""

    text: str
    user_id: int
    group_id: int
    note_id: Optional[int] = None
    source_user: Optional[int] = None
    source_group: Optional[int] = None
    source_message_id: Optional[int] = None
    created_at: Optional[float] = None
    confidence: str = "model"


class MemoryRepository(ABC):
    """记忆仓库抽象：CRUD + 检索 + 清理。"""

    @abstractmethod
    def list_notes(self, user_id: int, group_id: int, limit: Optional[int] = None) -> List[MemoryNote]:
        """按时间顺序列出某用户在群的记忆（limit 取最近 N 条）。"""

    @abstractmethod
    def search_notes(self, user_id: int, group_id: int, keyword: str) -> List[MemoryNote]:
        """按关键词（子串包含）检索某用户在群的记忆。"""

    @abstractmethod
    def list_all_notes(self) -> List[MemoryNote]:
        """列出全部记忆（供 TTL 清理等全局操作）。"""

    @abstractmethod
    def iter_user_groups(self) -> List[Tuple[int, int]]:
        """遍历所有存在数据的 (user_id, group_id) 组合。"""

    @abstractmethod
    def insert_note(self, note: MemoryNote) -> int:
        """插入一条记忆，返回 note_id。"""

    @abstractmethod
    def delete_note(self, note_id: int) -> None:
        """按 id 删除一条记忆。"""

    @abstractmethod
    def delete_user_notes(self, user_id: int, group_id: int) -> int:
        """删除某用户在群的全部记忆，返回删除条数。"""

    @abstractmethod
    def count_notes(self, user_id: int, group_id: int) -> int:
        """统计某用户在群的记忆条数。"""

    @abstractmethod
    def trim_notes(self, user_id: int, group_id: int, keep: int) -> int:
        """只保留最近 keep 条，删除更旧的，返回删除条数。"""

    @abstractmethod
    def kv_set(self, user_id: int, group_id: int, key: str, value: str) -> None:
        """写入通用键值。"""

    @abstractmethod
    def kv_list(self, user_id: int, group_id: int) -> List[Tuple[str, str]]:
        """列出通用键值。"""

    @abstractmethod
    def commit(self) -> None:
        """持久化未提交的写入（事务提交）。"""

    @abstractmethod
    def close(self) -> None:
        """释放底层资源（连接等）。"""
