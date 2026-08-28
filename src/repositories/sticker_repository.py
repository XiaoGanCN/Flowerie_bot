"""StickerRepository：表情包 Vision 索引的 SQLite 持久化。

核心：以 SHA-256(file bytes) 作为图片内容身份——
文件被替换（同名不同 hash）必须重新 Vision；hash 相同则复用缓存描述，
重启后不重复调用 Vision API。
"""
import os
import sqlite3
import threading
from typing import List, Optional


class StickerRepository:
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
            CREATE TABLE IF NOT EXISTS sticker_index (
                file_hash TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                mime_type TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',   -- ok / failed
                error TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """)
            self._conn.commit()

    def get_by_hash(self, file_hash: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sticker_index WHERE file_hash=?", (file_hash,)).fetchone()
            return dict(row) if row else None

    def get_by_path(self, file_path: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sticker_index WHERE file_path=?", (file_path,)).fetchone()
            return dict(row) if row else None

    def list_ok(self, limit: int = 100) -> List[dict]:
        """返回描述成功的表情包（供模型选择）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sticker_index WHERE status='ok' ORDER BY updated_at DESC LIMIT ?",
                (limit,)).fetchall()
            return [dict(r) for r in rows]

    def list_failed(self, older_than: float) -> List[dict]:
        """返回失败且超过 older_than 秒未重试的条目（允许后续 retry）。"""
        import time
        cutoff = time.time() - older_than
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sticker_index WHERE status='failed' AND updated_at < ?",
                (cutoff,)).fetchall()
            return [dict(r) for r in rows]

    def upsert(self, file_hash: str, filename: str, file_path: str,
               description: str = "", mime_type: str = "", width: int = 0, height: int = 0,
               status: str = "ok", error: str = "") -> None:
        import time
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO sticker_index (file_hash, filename, file_path, description,"
                " mime_type, width, height, status, error, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (file_hash, filename, file_path, description, mime_type, width, height,
                 status, error, now, now),
            )
            self._conn.commit()

    def delete(self, file_path: str) -> None:
        """删除某路径的索引（文件被移除时调用）。"""
        with self._lock:
            self._conn.execute("DELETE FROM sticker_index WHERE file_path=?", (file_path,))
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM sticker_index").fetchone()
            return int(row["c"])

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
