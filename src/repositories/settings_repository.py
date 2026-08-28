"""通用设置仓库：自定义 Prompt 与应用配置的 SQLite 持久化。

表结构：
- prompt_config(scope, group_id, content, updated_at)：scope ∈ {'global','group'}
- app_config(key, value, updated_at)：Web UI 可编辑的应用配置（热更新项）

线程安全模式同 SQLiteMemoryRepository（check_same_thread=False + RLock）。
"""
import os
import sqlite3
import threading
from typing import List, Optional, Tuple


class SettingsRepository:
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
            CREATE TABLE IF NOT EXISTS prompt_config (
                scope TEXT NOT NULL,
                group_id INTEGER,
                content TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, group_id)
            );
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            """)
            self._conn.commit()

    # ---------- Prompt ----------
    def get_prompt(self, scope: str, group_id: Optional[int] = None) -> Optional[str]:
        with self._lock:
            if scope == "global":
                row = self._conn.execute(
                    "SELECT content FROM prompt_config WHERE scope='global' AND group_id IS NULL").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT content FROM prompt_config WHERE scope='group' AND group_id=?", (group_id,)).fetchone()
            return row["content"] if row else None

    def set_prompt(self, scope: str, content: str, group_id: Optional[int] = None) -> None:
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO prompt_config (scope, group_id, content, updated_at) VALUES (?,?,?,?)",
                (scope, group_id, content, time.time()),
            )
            self._conn.commit()

    def delete_prompt(self, scope: str, group_id: Optional[int] = None) -> bool:
        with self._lock:
            if scope == "global":
                cur = self._conn.execute(
                    "DELETE FROM prompt_config WHERE scope='global' AND group_id IS NULL")
            else:
                cur = self._conn.execute(
                    "DELETE FROM prompt_config WHERE scope='group' AND group_id=?", (group_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------- 应用配置（Web UI 热更新项） ----------
    def get_config(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_config(self, key: str, value: str) -> None:
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?,?,?)",
                (key, value, time.time()),
            )
            self._conn.commit()

    def list_configs(self) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM app_config").fetchall()
            return [(r["key"], r["value"]) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
