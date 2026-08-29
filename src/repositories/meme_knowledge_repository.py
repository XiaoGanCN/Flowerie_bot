"""MemeKnowledgeRepository：群聊梗/黑话知识库（SQLite，按群完全隔离）。

表结构：
- meme_knowledge(id, group_id, term, normalized_term, meaning, examples,
  source, confidence, status, created_at, updated_at, last_seen_at)
  - UNIQUE(group_id, normalized_term)：并发/重复发现同一梗时去重合并，
    不会产生无限重复记录（同群同词唯一）。
  - 所有查询/写入都强制带 group_id 作用域 —— 群 A 永远看不到群 B 的知识。
- meme_summary_state(group_id, last_run)：每日总结任务状态（防重复执行/便于观测）

线程安全模式同 SettingsRepository（check_same_thread=False + RLock）。
"""
import os
import sqlite3
import threading
import time
from typing import List, Optional


class MemeKnowledgeRepository:
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
        with self._lock:
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error:
                pass

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS meme_knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL,
                term TEXT NOT NULL,
                normalized_term TEXT NOT NULL,
                meaning TEXT NOT NULL DEFAULT '',
                examples TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'manual',
                confidence TEXT NOT NULL DEFAULT 'low',
                status TEXT NOT NULL DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                UNIQUE (group_id, normalized_term)
            );
            CREATE INDEX IF NOT EXISTS idx_meme_group ON meme_knowledge (group_id);
            CREATE TABLE IF NOT EXISTS meme_summary_state (
                group_id INTEGER PRIMARY KEY,
                last_run REAL NOT NULL
            );
            """)
            self._conn.commit()

    # ---------- 查询（一律按 group_id 作用域） ----------
    def list_by_group(self, group_id: int, search: Optional[str] = None,
                      limit: int = 200) -> List[dict]:
        with self._lock:
            if search:
                # LIKE 通配符转义（% _ \ 按字面匹配，防搜索词变成通配符）
                escaped = (search.replace("\\", "\\\\")
                           .replace("%", "\\%").replace("_", "\\_"))
                like = f"%{escaped}%"
                rows = self._conn.execute(
                    "SELECT * FROM meme_knowledge WHERE group_id=? AND (term LIKE ? ESCAPE '\\'"
                    " OR meaning LIKE ? ESCAPE '\\') ORDER BY last_seen_at DESC LIMIT ?",
                    (group_id, like, like, max(1, int(limit))),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM meme_knowledge WHERE group_id=? ORDER BY last_seen_at DESC LIMIT ?",
                    (group_id, max(1, int(limit))),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_by_term(self, group_id: int, term: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meme_knowledge WHERE group_id=? AND normalized_term=?",
                (group_id, self._normalize(term)),
            ).fetchone()
            return dict(row) if row else None

    def get_by_id(self, knowledge_id: int, group_id: int) -> Optional[dict]:
        """按 id + group_id 双条件查询：即使拿到其他群的 id 也查不到（隔离）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM meme_knowledge WHERE id=? AND group_id=?", (knowledge_id, group_id),
            ).fetchone()
            return dict(row) if row else None

    def list_all_terms(self, group_id: int, limit: int = 500) -> List[str]:
        """返回某群全部 term（用于消息检索匹配；有界）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT term FROM meme_knowledge WHERE group_id=? AND status='active' LIMIT ?",
                (group_id, max(1, int(limit))),
            ).fetchall()
            return [r["term"] for r in rows]

    def count_by_group(self, group_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM meme_knowledge WHERE group_id=?", (group_id,)).fetchone()
            return int(row["c"])

    def list_all(self) -> List[dict]:
        """全部知识（供全局治理/上限清理；行数有界由调用方控制）。"""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM meme_knowledge ORDER BY group_id, last_seen_at").fetchall()
            return [dict(r) for r in rows]

    # ---------- 写入 ----------
    def insert_knowledge(self, group_id: int, term: str, meaning: str,
                         examples: str = "", source: str = "manual",
                         confidence: str = "low", status: str = "active",
                         now: Optional[float] = None) -> int:
        """插入一条知识（term 冲突时更新理解并返回已存在 id 语义）。

        并发安全：UNIQUE(group_id, normalized_term) + INSERT OR IGNORE 保证
        两个任务同时发现同一梗时只有一条记录；冲突时按"更新理解"处理。
        返回记录 id。
        """
        now = now if now is not None else time.time()
        norm = self._normalize(term)
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, meaning, examples, confidence, source FROM meme_knowledge"
                " WHERE group_id=? AND normalized_term=?", (group_id, norm),
            ).fetchone()
            if existing is not None:
                self._conn.execute(
                    "UPDATE meme_knowledge SET meaning=?, examples=?, confidence=?, source=?,"
                    " status=?, updated_at=?, last_seen_at=? WHERE id=?",
                    (meaning or existing["meaning"], examples or existing["examples"],
                     confidence, source, status, now, now, existing["id"]),
                )
                self._conn.commit()
                return int(existing["id"])
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO meme_knowledge (group_id, term, normalized_term, meaning,"
                " examples, source, confidence, status, created_at, updated_at, last_seen_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (group_id, term, norm, meaning, examples, source, confidence, status, now, now, now),
            )
            self._conn.commit()
            if cur.rowcount:
                return int(cur.lastrowid)
            # 并发窗口内被插入（另一连接先写入）→ 读取已有记录 id
            row = self._conn.execute(
                "SELECT id FROM meme_knowledge WHERE group_id=? AND normalized_term=?",
                (group_id, norm),
            ).fetchone()
            return int(row["id"]) if row else 0

    def update_knowledge(self, knowledge_id: int, group_id: int, **fields) -> bool:
        """更新某条知识（id + group_id 双条件，防跨群修改）。"""
        allowed = {"term", "meaning", "examples", "source", "confidence", "status"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        with self._lock:
            sets = []
            args = []
            if "term" in updates:
                norm = self._normalize(updates["term"])
                updates["normalized_term"] = norm
            for k, v in updates.items():
                sets.append(f"{k}=?")
                args.append(v)
            sets.append("updated_at=?")
            args.append(time.time())
            args.extend([knowledge_id, group_id])
            cur = self._conn.execute(
                f"UPDATE meme_knowledge SET {', '.join(sets)} WHERE id=? AND group_id=?", args)
            self._conn.commit()
            return cur.rowcount > 0

    def touch_last_seen(self, knowledge_id: int, now: Optional[float] = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE meme_knowledge SET last_seen_at=? WHERE id=?",
                (now if now is not None else time.time(), knowledge_id),
            )
            self._conn.commit()

    def delete_by_id(self, knowledge_id: int, group_id: int) -> bool:
        """按 id + group_id 双条件删除（防跨群删除）。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM meme_knowledge WHERE id=? AND group_id=?", (knowledge_id, group_id))
            self._conn.commit()
            return cur.rowcount > 0

    def delete_all_by_group(self, group_id: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM meme_knowledge WHERE group_id=?", (group_id,))
            self._conn.commit()
            return cur.rowcount

    # ---------- 上限治理 ----------
    def trim_group_to_max(self, group_id: int, max_items: int) -> int:
        """超出上限时删除该群最旧（last_seen_at 最小）的条目，返回删除数。

        不删除活跃知识：先按 last_seen_at 升序删到不超限；同时间戳按 id 升序。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM meme_knowledge WHERE group_id=? ORDER BY last_seen_at ASC, id ASC",
                (group_id,),
            ).fetchall()
            if len(rows) <= max_items:
                return 0
            remove_ids = [r["id"] for r in rows[: len(rows) - max_items]]
            for rid in remove_ids:
                self._conn.execute("DELETE FROM meme_knowledge WHERE id=?", (rid,))
            self._conn.commit()
            return len(remove_ids)

    # ---------- 总结任务状态 ----------
    def get_last_run(self, group_id: int) -> Optional[float]:
        with self._lock:
            row = self._conn.execute(
                "SELECT last_run FROM meme_summary_state WHERE group_id=?", (group_id,)).fetchone()
            return row["last_run"] if row else None

    def set_last_run(self, group_id: int, now: Optional[float] = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO meme_summary_state (group_id, last_run) VALUES (?,?)",
                (group_id, now if now is not None else time.time()),
            )
            self._conn.commit()

    # ---------- 其他 ----------
    @staticmethod
    def _normalize(term: str) -> str:
        """term 归一化：去首尾空白、统一小写（英文/数字梗）、NFKC 归一化全角。"""
        import unicodedata
        t = unicodedata.normalize("NFKC", (term or "").strip().lower())
        return t[:100]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
