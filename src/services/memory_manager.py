"""记忆业务层（MemoryManager）：只负责业务规则，不感知存储细节。

业务职责：
- 去重 / 矛盾替换 / 数量上限 / TTL 分级清理 / 审计日志
- 旧版 memory.json 一次性迁移（写入走 repository 接口）
- 对外保持原有接口（get_user_memory / append_memory_text / ...）

存储职责：全部委托给 MemoryRepository（当前实现 SQLiteMemoryRepository）。
未来替换 PostgresRepository / RedisRepository 时无需修改本类。
"""
import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from src.repositories.base import MemoryNote, MemoryRepository
from src.repositories.sqlite_repository import SQLiteMemoryRepository
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry

logger = get_logger(__name__)


# Metrics
_M_READ = registry.counter("memory_read_total", "记忆库读取次数")
_M_WRITE = registry.counter("memory_write_total", "记忆库写入次数")
_M_ERROR = registry.counter("memory_error_total", "记忆库操作异常次数")


def _resolve_db_path(path: str) -> str:
    """兼容旧配置：以 .json 结尾的路径自动映射到同目录 .db 文件。"""
    if path and str(path).lower().endswith(".json"):
        return str(path)[:-5] + ".db"
    return path


class MemoryManager:
    """按 (user_id, group_id) 隔离的记忆库（业务层，存储经 repository 注入）。"""

    def __init__(self, memory_path: str, ttl_days: int = 0, audit_log_path: Optional[str] = None,
                 model_memory_ttl_days: int = 30, repository: Optional[MemoryRepository] = None):
        self.memory_path = memory_path
        self.db_path = _resolve_db_path(memory_path)
        self.ttl_days = max(0, int(ttl_days or 0))
        self.model_memory_ttl_days = max(0, int(model_memory_ttl_days or 0))
        self.audit_log_path = audit_log_path
        # 存储层注入：默认 SQLite；测试或未来替换可传其他实现
        self.repository: MemoryRepository = repository or SQLiteMemoryRepository(self.db_path)
        self._migrate_from_json()
        self._prune_expired()

    def close(self) -> None:
        self.repository.close()

    # ---------- 旧版 JSON 迁移（一次性） ----------
    def _migrate_from_json(self) -> None:
        """首次启动时把旧 memory.json 导入仓库（已有数据则跳过），迁移后原文件改名备份。"""
        legacy = self.memory_path
        if not legacy or not str(legacy).lower().endswith(".json"):
            return
        if not os.path.exists(legacy):
            return
        if self.repository.list_all_notes():
            logger.info("SQLite 记忆库已有数据，跳过 JSON 迁移")
            return
        try:
            with open(legacy, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error("记忆库迁移失败：读取旧 JSON 出错: %s", e)
            return
        if not isinstance(data, dict):
            logger.warning("旧记忆库 JSON 格式异常，跳过迁移")
            return
        inserted = 0
        for key, mem in data.items():
            if "_" not in key:
                continue
            uid_s, gid_s = key.split("_", 1)
            try:
                uid, gid = int(uid_s), int(gid_s)
            except (ValueError, TypeError):
                continue
            notes = mem.get("notes", []) if isinstance(mem, dict) else []
            if not isinstance(notes, list):
                continue
            for note in notes:
                if isinstance(note, str):
                    text, created, conf, src = note, None, "model", {}
                elif isinstance(note, dict):
                    text = note.get("text", "")
                    created = note.get("created_at")
                    conf = note.get("confidence", "model")
                    src = note
                else:
                    continue
                if not text or not text.strip():
                    continue
                # 无可靠时间戳的旧数据 → 存 None（永不因 TTL 删除，不误删）
                if not isinstance(created, (int, float)):
                    created = None
                self.repository.insert_note(MemoryNote(
                    user_id=uid, group_id=gid, text=text.strip(),
                    source_user=src.get("source_user"),
                    source_group=src.get("source_group"),
                    source_message_id=src.get("source_message_id"),
                    created_at=created,
                    confidence=conf,
                ))
                inserted += 1
        self.repository.commit()
        try:
            os.replace(legacy, legacy + ".migrated")
            logger.info("旧记忆库 JSON 已备份为: %s.migrated", legacy)
        except OSError as e:
            logger.warning("旧记忆库 JSON 备份改名失败（可手动删除）: %s", e)
        logger.info("记忆库已从 JSON 迁移到 SQLite: %d 条记忆 -> %s", inserted, self.db_path)

    # ---------- TTL 过期清理（P3 数据治理） ----------
    def _prune_expired(self) -> None:
        if self.ttl_days <= 0 and self.model_memory_ttl_days <= 0:
            return
        now = time.time()
        expired = []
        for note in self.repository.list_all_notes():
            ttl = self.model_memory_ttl_days if note.confidence == "model" else self.ttl_days
            if ttl <= 0:
                continue
            created = note.created_at
            # 无时间戳（None/脏数据）无法判断年龄 → 保留，不误删
            if not isinstance(created, (int, float)):
                continue
            if (now - created) >= ttl * 86400:
                expired.append(note.note_id)
        if expired:
            for nid in expired:
                try:
                    self.repository.delete_note(nid)
                except Exception as e:  # noqa: BLE001
                    _M_ERROR.inc()
                    logger.error("memory_error delete_note=%s err=%s", nid, e, extra={"event": "memory_error"})
            self.repository.commit()
            logger.info("记忆 TTL 清理完成: 删除 %d 条", len(expired))

    # ---------- 审计日志（P3） ----------
    def _audit(self, action: str, user_id: int, group_id: int, text: str) -> None:
        if not self.audit_log_path:
            return
        try:
            dirname = os.path.dirname(self.audit_log_path)
            if dirname and not os.path.exists(dirname):
                os.makedirs(dirname, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{ts}] {action} user={user_id} group={group_id} text={text!r}\n"
            with open(self.audit_log_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error("审计日志写入失败: %s", e)

    # ---------- 查询 ----------
    def get_user_memory(self, user_id: int, group_id: int) -> Dict:
        """返回某用户在群的记忆结构 {"notes": [{text, source_user, ...}]}（兼容旧接口）。"""
        notes = [{
            "text": n.text,
            "source_user": n.source_user,
            "source_group": n.source_group,
            "source_message_id": n.source_message_id,
            "created_at": n.created_at,
            "confidence": n.confidence,
        } for n in self.repository.list_notes(user_id, group_id)]
        return {"notes": notes}

    def get_user_notes(self, user_id: int, group_id: int) -> List[str]:
        """返回某用户在群里的记忆文本列表（供 /memory 命令展示）。"""
        notes = self.repository.list_notes(user_id, group_id)
        _M_READ.inc()
        return [n.text for n in notes if n.text]

    def iter_user_groups(self) -> List[Tuple[int, int]]:
        """遍历所有 (user_id, group_id) 组合（供管理员 /memory_clear /memory_dump 使用）。"""
        return self.repository.iter_user_groups()

    async def remove_notes_containing(self, user_id: int, group_id: int, keyword: str) -> int:
        """删除包含关键词的记忆，返回删除条数（供 /forget 命令）。"""
        if not keyword:
            return 0
        hits = self.repository.search_notes(user_id, group_id, keyword)
        removed = 0
        for note in hits:
            self.repository.delete_note(note.note_id)
            removed += 1
            self._audit("FORGET", user_id, group_id, note.text)
        if removed:
            await self.save()
        return removed

    async def clear_user_memory(self, user_id: int, group_id: int) -> int:
        """清空某用户在群的记忆，返回清空条数（供 /forget_me /memory_clear）。"""
        count = self.repository.count_notes(user_id, group_id)
        if count:
            self.repository.delete_user_notes(user_id, group_id)
            self._audit("CLEAR", user_id, group_id, f"{count} 条")
            await self.save()
        return count

    async def update_memory(self, user_id: int, group_id: int, key: str, value: Any) -> None:
        """通用键值写入（委托 repository.kv_set）。"""
        self.repository.kv_set(user_id, group_id, str(key), str(value))
        await self.save()

    # ---------- 记忆矛盾检测（治 misinformation） ----------
    _NEGATION_WORDS = ("不", "没", "讨厌", "退游", "弃坑", "戒了", "不再", "卸载", "退", "弃", "戒")
    _POSITIVE_WORDS = ("喜欢", "爱", "玩", "打", "吃", "喝", "穿", "戴", "看", "听", "用", "做")

    @classmethod
    def _core_words(cls, s: str) -> str:
        """去掉正反倾向词后的核心词（用于比较两条记忆是否在讲同一件事）。"""
        for w in cls._NEGATION_WORDS + cls._POSITIVE_WORDS:
            s = s.replace(w, "")
        s = re.sub(r"[\s，。！？、,.!?;；:：]+", "", s)
        for w in ("现在", "最近", "以前", "之前", "当初", "了", "呢", "吧", "啊", "哦", "呀"):
            s = s.replace(w, "")
        return s

    @classmethod
    def _is_contradiction(cls, a: str, b: str) -> bool:
        """a 与 b 是否构成"肯定↔否定"矛盾：一方含否定词、另一方不含，且核心词重叠 ≥0.6。"""
        if not a or not b:
            return False
        a_neg = any(w in a for w in cls._NEGATION_WORDS)
        b_neg = any(w in b for w in cls._NEGATION_WORDS)
        if a_neg == b_neg:
            return False  # 同为肯定或同为否定 → 交给去重逻辑，不算矛盾
        core_a, core_b = cls._core_words(a), cls._core_words(b)
        if not core_a or not core_b:
            return False
        from difflib import SequenceMatcher
        return SequenceMatcher(None, core_a, core_b).ratio() >= 0.6

    async def append_memory_text(
        self,
        user_id: int,
        group_id: int,
        text: str,
        source_user: Optional[int] = None,
        source_group: Optional[int] = None,
        source_message_id: Optional[int] = None,
        confidence: str = "model",
    ) -> None:
        """写入一条记忆（去重 + 矛盾替换 + 数量上限，存储委托 repository）。

        安全边界（P1）：user_id 是唯一的寻址键，调用方（程序层）传入，
        模型输出中的任何 QQ 号都不会进入这里。
        """
        if not text or not text.strip():
            return
        text = text.strip()
        notes = self.repository.list_notes(user_id, group_id)

        # 矛盾替换（治 misinformation）：新记忆是否定/退出、旧记忆是肯定/进行，且核心词重叠 → 旧被新顶掉。
        replaced_old = None
        for i, existing in enumerate(notes):
            if self._is_contradiction(existing.text, text):
                replaced_old = existing.text
                self.repository.delete_note(existing.note_id)
                notes.pop(i)
                break
        if replaced_old is not None:
            logger.info("记忆矛盾替换: 旧=[%s] 新=[%s]", replaced_old, text)
            self._audit("REPLACE", user_id, group_id, f"旧={replaced_old} 新={text}")

        # 高相似度去重（完全相同/互为子串/相似度≥0.85/字符包含率≥80% 都跳过）
        from difflib import SequenceMatcher

        def _norm(s: str) -> str:
            return re.sub(r"[\s，。！？、,.!?;；:：()（）「」『』【】\[\]]+", "", s)

        text_norm = _norm(text)
        for existing in notes:
            existing_norm = _norm(existing.text)
            if not existing_norm:
                continue
            if existing_norm == text_norm:
                return
            if existing_norm in text_norm or text_norm in existing_norm:
                return
            if SequenceMatcher(None, existing_norm, text_norm).ratio() >= 0.85:
                return
            if len(text_norm) <= len(existing_norm):
                short_chars, long_chars = set(text_norm), set(existing_norm)
            else:
                short_chars, long_chars = set(existing_norm), set(text_norm)
            if short_chars and sum(1 for ch in short_chars if ch in long_chars) / len(short_chars) >= 0.8:
                return

        self.repository.insert_note(MemoryNote(
            user_id=user_id,
            group_id=group_id,
            text=text,
            source_user=source_user if source_user is not None else user_id,
            source_group=source_group if source_group is not None else group_id,
            source_message_id=source_message_id,
            created_at=time.time(),
            confidence=confidence,
        ))
        # 数量上限：超过 50 条只保留最近 25 条
        if self.repository.count_notes(user_id, group_id) > 50:
            self.repository.trim_notes(user_id, group_id, keep=25)
        self._audit("WRITE", user_id, group_id, text)
        _M_WRITE.inc()
        logger.info(
            "memory_write user=%s group=%s confidence=%s", user_id, group_id, confidence,
            extra={"event": "memory_write"},
        )
        await self.save()

    def get_memory_context(self, user_id: int, group_id: int, max_notes: int = 20, max_length: int = 500) -> str:
        notes = self.repository.list_notes(user_id, group_id, limit=max_notes)
        _M_READ.inc()
        logger.debug(
            "memory_read user=%s group=%s notes=%d", user_id, group_id, len(notes),
            extra={"event": "memory_read"},
        )
        lines = []
        texts = [n.text for n in notes]
        if texts:
            lines.append("关于该用户的记录: " + "; ".join(texts))
        for key, value in self.repository.kv_list(user_id, group_id):
            lines.append(f"{key}: {value}")
        full_text = "；".join(lines)
        if len(full_text) > max_length:
            full_text = full_text[:max_length] + "...（已截断）"
        return full_text

    # ---------- 持久化 ----------
    async def save(self) -> None:
        """提交未落库的写入（to_thread，不阻塞事件循环）。"""
        await asyncio.to_thread(self.repository.commit)
