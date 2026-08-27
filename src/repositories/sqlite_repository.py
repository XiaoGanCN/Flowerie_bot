"""SQLite 记忆仓库实现。

职责边界：
- 只做数据存取（CRUD / 检索 / 清理 / kv）
- 线程安全：check_same_thread=False + RLock（save 走 to_thread 不阻塞事件循环）
- 不包含任何业务规则（去重/矛盾替换/TTL 判断/审计都在 MemoryManager）
"""
import os
import sqlite3
import threading
from typing import List, Optional, Tuple

from src.repositories.base import MemoryNote, MemoryRepository


def _as_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (ValueError, TypeError):
        return None


def _row_to_note(row: sqlite3.Row) -> MemoryNote:
    return MemoryNote(
        note_id=row["note_id"],
        user_id=row["user_id"],
        group_id=row["group_id"],
        text=row["text"],
        source_user=row["source_user"],
        source_group=row["source_group"],
        source_message_id=row["source_message_id"],
        created_at=row["created_at"],
        confidence=row["confidence"],
    )


class SQLiteMemoryRepository(MemoryRepository):
    def __init__(self, db_path: str):
        dirname = os.path.dirname(db_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._pragma()

    def _pragma(self) -> None:
        # WAL：读写不互斥；busy_timeout：并发写等待而非立刻报 database locked
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error:
                pass  # 只读介质/权限不足时静默降级（不影响功能）

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

    # ---------- 查询 ----------
    def list_notes(self, user_id: int, group_id: int, limit: Optional[int] = None) -> List[MemoryNote]:
        with self._lock:
            if limit:
                rows = self._conn.execute(
                    "SELECT * FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id DESC LIMIT ?",
                    (user_id, group_id, limit),
                ).fetchall()
                rows = list(reversed(rows))  # 时间正序
            else:
                rows = self._conn.execute(
                    "SELECT * FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id",
                    (user_id, group_id),
                ).fetchall()
            return [_row_to_note(r) for r in rows]

    def search_notes(self, user_id: int, group_id: int, keyword: str) -> List[MemoryNote]:
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory WHERE user_id=? AND group_id=? AND text LIKE ? ESCAPE '\\'",
                (user_id, group_id, f"%{escaped}%"),
            ).fetchall()
            return [_row_to_note(r) for r in rows]

    def list_all_notes(self) -> List[MemoryNote]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM memory").fetchall()
            return [_row_to_note(r) for r in rows]

    def iter_user_groups(self) -> List[Tuple[int, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT user_id, group_id FROM memory"
                " UNION SELECT DISTINCT user_id, group_id FROM memory_kv"
            ).fetchall()
            return [(r["user_id"], r["group_id"]) for r in rows]

    # ---------- 写入 ----------
    def insert_note(self, note: MemoryNote) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memory (user_id, group_id, text, source_user, source_group, source_message_id, created_at, confidence)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (int(note.user_id), int(note.group_id), note.text,
                 _as_int(note.source_user), _as_int(note.source_group), _as_int(note.source_message_id),
                 note.created_at, note.confidence),
            )
            return int(cur.lastrowid)

    def delete_note(self, note_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memory WHERE note_id=?", (note_id,))

    def delete_user_notes(self, user_id: int, group_id: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory WHERE user_id=? AND group_id=?", (user_id, group_id))
            return cur.rowcount

    def count_notes(self, user_id: int, group_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM memory WHERE user_id=? AND group_id=?", (user_id, group_id)).fetchone()
            return int(row["c"])

    def trim_notes(self, user_id: int, group_id: int, keep: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memory WHERE user_id=? AND group_id=? AND note_id NOT IN ("
                "SELECT note_id FROM memory WHERE user_id=? AND group_id=? ORDER BY note_id DESC LIMIT ?)",
                (user_id, group_id, user_id, group_id, keep),
            )
            return cur.rowcount

    def kv_set(self, user_id: int, group_id: int, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO memory_kv (user_id, group_id, key, value) VALUES (?,?,?,?)",
                (int(user_id), int(group_id), str(key), str(value)),
            )

    def kv_list(self, user_id: int, group_id: int) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM memory_kv WHERE user_id=? AND group_id=?", (user_id, group_id)).fetchall()
            return [(r["key"], r["value"]) for r in rows]

    # ---------- 生命周期 ----------
    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败无副作用
                pass
