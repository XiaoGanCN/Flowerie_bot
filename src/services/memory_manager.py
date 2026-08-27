import os
import json
import re
import time
import asyncio
import sqlite3
import threading
from typing import Dict, Any, Optional, List, Tuple
from loguru import logger


def _resolve_db_path(path: str) -> str:
    """兼容旧配置：以 .json 结尾的路径自动映射到同目录 .db 文件。"""
    if path and str(path).lower().endswith(".json"):
        return str(path)[:-5] + ".db"
    return path


def _as_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


class MemoryManager:
    """按 (user_id, group_id) 隔离的记忆库，SQLite 持久化。

    存储结构：
    - memory 表：每条记忆一行，带来源元数据（source_user / source_group /
      source_message_id / created_at / confidence），避免"模型推断当事实"
    - memory_kv 表：通用键值（update_memory 用）

    迁移：旧版 memory.json 首次启动自动导入（原文件改名为 .migrated 备份），
    升级不丢数据；配置里仍写 .json 路径也兼容（自动映射 .db）。

    线程安全：check_same_thread=False + RLock，save() 走 to_thread 不阻塞事件循环。
    """

    def __init__(self, memory_path: str, ttl_days: int = 0, audit_log_path: Optional[str] = None, model_memory_ttl_days: int = 30):
        self.memory_path = memory_path
        self.db_path = _resolve_db_path(memory_path)
        self.ttl_days = max(0, int(ttl_days or 0))
        self.model_memory_ttl_days = max(0, int(model_memory_ttl_days or 0))
        self.audit_log_path = audit_log_path
        self._lock = threading.RLock()
        dirname = os.path.dirname(self.db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_from_json()
        self._prune_expired()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ---------- 内部：SQL 辅助 ----------
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                source_user INTEGER,
                source_group INTEGER,
                source_message_id INTEGER,
                created_at REAL,
                confidence TEXT NOT NULL DEFAULT 'model'
            );
            CREATE INDEX IF NOT EXISTS idx_memory_ug ON memory(user_id, group_id);
            CREATE TABLE IF NOT EXISTS memory_kv (
                user_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT,
                PRIMARY KEY (user_id, group_id, key)
            );
            """)
            self._conn.commit()

    def _query_all(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def _commit(self) -> None:
        with self._lock:
            self._conn.commit()

    async def save(self) -> None:
        await asyncio.to_thread(self._commit)

    def _insert_note(self, user_id: int, group_id: int, text: str,
                     source_user=None, source_group=None, source_message_id=None,
                     created_at=None, confidence: str = "model") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO memory (user_id, group_id, text, source_user, source_group, source_message_id, created_at, confidence)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(user_id), int(group_id), text,
                 _as_int(source_user), _as_int(source_group), _as_int(source_message_id),
                 created_at, confidence),
            )

    # ---------- 旧版 JSON 迁移 ----------
    def _migrate_from_json(self) -> None:
        """首次启动时把旧 memory.json 导入 SQLite（db 已有数据则跳过），迁移后原文件改名备份。"""
        legacy = self.memory_path
        if not legacy or not str(legacy).lower().endswith(".json"):
            return
        if not os.path.exists(legacy):
            return
        with self._lock:
            cnt = self._conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
        if cnt:
            logger.info("SQLite 记忆库已有数据，跳过 JSON 迁移")
            return
        try:
            with open(legacy, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            logger.error(f"记忆库迁移失败：读取旧 JSON 出错: {e}")
            return
        if not isinstance(data, dict):
            logger.warning("旧记忆库 JSON 格式异常，跳过迁移")
            return
        inserted = 0
        for key, mem in data.items():
            if "_" not in key:
                continue
            uid_s, gid_s = key.split("_", 1)
            uid, gid = _as_int(uid_s), _as_int(gid_s)
            if uid is None or gid is None:
                continue
            notes = mem.get("notes", []) if isinstance(mem, dict) else []
            if not isinstance(notes, list):
                continue
            for note in notes:
                if isinstance(note, str):
                    text, created, conf = note, None, "model"
                elif isinstance(note, dict):
                    text = note.get("text", "")
                    created = note.get("created_at")
                    conf = note.get("confidence", "model")
                else:
                    continue
                if not text or not text.strip():
                    continue
                # 无可靠时间戳的旧数据 → 存 NULL（永不因 TTL 删除，不误删）
                if not isinstance(created, (int, float)):
                    created = None
                src = note if isinstance(note, dict) else {}
                self._insert_note(
                    uid, gid, text.strip(),
                    source_user=src.get("source_user"),
                    source_group=src.get("source_group"),
                    source_message_id=src.get("source_message_id"),
                    created_at=created,
                    confidence=conf,
                )
                inserted += 1
        self._commit()
        try:
            os.replace(legacy, legacy + ".migrated")
            logger.info(f"旧记忆库 JSON 已备份为: {legacy}.migrated")
        except OSError as e:
            logger.warning(f"旧记忆库 JSON 备份改名失败（可手动删除）: {e}")
        logger.info(f"记忆库已从 JSON 迁移到 SQLite: {inserted} 条记忆 -> {self.db_path}")

    # ---------- TTL 过期清理（P3 数据治理） ----------
    def _prune_expired(self) -> None:
        if self.ttl_days <= 0 and self.model_memory_ttl_days <= 0:
            return
        now = time.time()
        rows = self._query_all("SELECT note_id, created_at, confidence FROM memory")
        expired = []
        for r in rows:
            ttl = self.model_memory_ttl_days if r["confidence"] == "model" else self.ttl_days
            if ttl <= 0:
                continue
            created = r["created_at"]
            # 无时间戳（NULL/脏数据）无法判断年龄 → 保留，不误删
            if not isinstance(created, (int, float)):
                continue
            if (now - created) >= ttl * 86400:
                expired.append(r["note_id"])
        if expired:
            for nid in expired:
                self._execute("DELETE FROM memory WHERE note_id=?", (nid,))
            self._commit()
            logger.info(f"记忆 TTL 清理完成: 删除 {len(expired)} 条")

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
            logger.error(f"审计日志写入失败: {e}")

    # ---------- 查询 ----------
    def get_user_memory(self, user_id: int, group_id: int) -> Dict:
        """返回某用户在群的记忆结构 {"notes": [{text, source_user, ...}]}（兼容旧接口）。"""
        rows = self._query_all(
            "SELECT text, source_user, source_group, source_message_id, created_at, confidence"
            " FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id",
            (user_id, group_id),
        )
        notes = [{
            "text": r["text"],
            "source_user": r["source_user"],
            "source_group": r["source_group"],
            "source_message_id": r["source_message_id"],
            "created_at": r["created_at"],
            "confidence": r["confidence"],
        } for r in rows]
        return {"notes": notes}

    def get_user_notes(self, user_id: int, group_id: int) -> List[str]:
        """返回某用户在群里的记忆文本列表（供 /memory 命令展示）。"""
        rows = self._query_all(
            "SELECT text FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id",
            (user_id, group_id),
        )
        return [r["text"] for r in rows if r["text"]]

    def iter_user_groups(self) -> List[Tuple[int, int]]:
        """遍历所有 (user_id, group_id) 组合（供管理员 /memory_clear /memory_dump 使用）。"""
        rows = self._query_all(
            "SELECT DISTINCT user_id, group_id FROM memory"
            " UNION SELECT DISTINCT user_id, group_id FROM memory_kv"
        )
        return [(r["user_id"], r["group_id"]) for r in rows]

    async def remove_notes_containing(self, user_id: int, group_id: int, keyword: str) -> int:
        """删除包含关键词的记忆，返回删除条数（供 /forget 命令）。"""
        if not keyword:
            return 0
        # LIKE 通配符转义，保持"子串包含"语义
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._query_all(
            "SELECT note_id, text FROM memory WHERE user_id=? AND group_id=? AND text LIKE ? ESCAPE '\\'",
            (user_id, group_id, f"%{escaped}%"),
        )
        removed = 0
        for r in rows:
            self._execute("DELETE FROM memory WHERE note_id=?", (r["note_id"],))
            removed += 1
            self._audit("FORGET", user_id, group_id, r["text"])
        if removed:
            await self.save()
        return removed

    async def clear_user_memory(self, user_id: int, group_id: int) -> int:
        """清空某用户在群的记忆，返回清空条数（供 /forget_me /memory_clear）。"""
        count = len(self._query_all(
            "SELECT note_id FROM memory WHERE user_id=? AND group_id=?", (user_id, group_id)))
        if count:
            self._execute("DELETE FROM memory WHERE user_id=? AND group_id=?", (user_id, group_id))
            self._audit("CLEAR", user_id, group_id, f"{count} 条")
            await self.save()
        return count

    async def update_memory(self, user_id: int, group_id: int, key: str, value: Any) -> None:
        """通用键值写入（memory_kv 表）。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_kv (user_id, group_id, key, value) VALUES (?,?,?,?)",
                (int(user_id), int(group_id), str(key), str(value)),
            )
        await self.save()

    def _note_text(self, note: Any) -> str:
        return note.get("text", "") if isinstance(note, dict) else (note or "")

    # ---------- 记忆矛盾检测（治 misinformation） ----------
    _NEGATION_WORDS = ("不", "没", "讨厌", "退游", "弃坑", "戒了", "不再", "卸载", "退", "弃", "戒")
    _POSITIVE_WORDS = ("喜欢", "爱", "玩", "打", "吃", "喝", "穿", "戴", "看", "听", "用", "做")

    @classmethod
    def _core_words(cls, s: str) -> str:
        """去掉正反倾向词后的核心词（用于比较两条记忆是否在讲同一件事）。"""
        for w in cls._NEGATION_WORDS + cls._POSITIVE_WORDS:
            s = s.replace(w, "")
        s = re.sub(r"[\s，。！？、,.!?;；:：]+", "", s)
        # 去掉时态词与语气词（现在/最近/以前/了/呢 等），只留核心名词短语
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
        """写入一条记忆。

        安全边界（P1）：user_id 是唯一的寻址键，调用方（程序层）传入，
        模型输出中的任何 QQ 号都不会进入这里。
        元数据（P2）：记录来源用户/群/消息 id/时间/置信度，防止推断当事实。
        """
        if not text or not text.strip():
            return
        text = text.strip()
        rows = self._query_all(
            "SELECT note_id, text FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id",
            (user_id, group_id),
        )
        notes = [dict(r) for r in rows]  # [{"note_id":.., "text":..}]

        # 矛盾替换（治 misinformation）：新记忆是否定/退出、旧记忆是肯定/进行，且核心词重叠 → 旧被新顶掉。
        # 例如「喜欢打三角洲」→「退游了 不打三角洲了」：只留新的，不让过时信息继续污染画像。
        replaced_old = None
        for i, existing in enumerate(notes):
            if self._is_contradiction(existing["text"], text):
                replaced_old = existing["text"]
                self._execute("DELETE FROM memory WHERE note_id=?", (existing["note_id"],))
                notes.pop(i)
                break
        if replaced_old is not None:
            logger.info(f"记忆矛盾替换: 旧=[{replaced_old}] 新=[{text}]")
            self._audit("REPLACE", user_id, group_id, f"旧={replaced_old} 新={text}")

        # 高相似度去重：完全相同、互为子串、相似度 >= 0.85、
        # 或较短一条的字符集 ≥80% 被较长一条包含（可容忍错别字）的旧记忆不再重复记录
        from difflib import SequenceMatcher

        def _norm(s: str) -> str:
            # 去掉空白与常见标点，只比内容骨架
            return re.sub(r"[\s，。！？、,.!?;；:：()（）「」『』【】\[\]]+", "", s)

        text_norm = _norm(text)
        for existing in notes:
            existing_norm = _norm(existing["text"])
            if not existing_norm:
                continue
            if existing_norm == text_norm:
                return
            if existing_norm in text_norm or text_norm in existing_norm:
                return
            if SequenceMatcher(None, existing_norm, text_norm).ratio() >= 0.85:
                return
            # 字符包含率（容忍错别字）：短句字符集 ≥80% 出现在长句里 → 视为重复
            if len(text_norm) <= len(existing_norm):
                short_chars, long_chars = set(text_norm), set(existing_norm)
            else:
                short_chars, long_chars = set(existing_norm), set(text_norm)
            if short_chars and sum(1 for ch in short_chars if ch in long_chars) / len(short_chars) >= 0.8:
                return

        self._insert_note(
            user_id, group_id, text,
            source_user=source_user if source_user is not None else user_id,
            source_group=source_group if source_group is not None else group_id,
            source_message_id=source_message_id,
            created_at=time.time(),
            confidence=confidence,
        )
        # 数量上限：超过 50 条只保留最近 25 条
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM memory WHERE user_id=? AND group_id=?", (user_id, group_id)).fetchone()[0]
        if total > 50:
            self._execute(
                "DELETE FROM memory WHERE user_id=? AND group_id=? AND note_id NOT IN ("
                "SELECT note_id FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id DESC LIMIT 25)",
                (user_id, group_id, user_id, group_id),
            )
        self._audit("WRITE", user_id, group_id, text)
        await self.save()

    def get_memory_context(self, user_id: int, group_id: int, max_notes: int = 20, max_length: int = 500) -> str:
        rows = self._query_all(
            "SELECT text FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id DESC LIMIT ?",
            (user_id, group_id, max_notes),
        )
        lines = []
        texts = [r["text"] for r in reversed(rows)]  # 最近 max_notes 条（时间正序展示）
        if texts:
            lines.append("关于该用户的记录: " + "; ".join(texts))
        for r in self._query_all(
                "SELECT key, value FROM memory_kv WHERE user_id=? AND group_id=?", (user_id, group_id)):
            lines.append(f"{r['key']}: {r['value']}")
        full_text = "；".join(lines)
        if len(full_text) > max_length:
            full_text = full_text[:max_length] + "...（已截断）"
        return full_text
