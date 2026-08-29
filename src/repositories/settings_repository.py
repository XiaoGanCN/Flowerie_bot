"""通用设置仓库：自定义 Prompt 与应用配置的 SQLite 持久化。

表结构：
- prompt_config(scope, group_id, content, updated_at)：scope ∈ {'global','group'}
- app_config(key, value, updated_at)：Web UI 可编辑的应用配置（热更新项）
- personas(id, name, description, system_prompt, vocabulary, behavior_rules,
  response_style, builtin, created_at, updated_at)：人格资源（内置/自定义）
- group_persona(group_id, persona_id, updated_at)：群聊人格映射（群 > 全局）
- persona_global(key, persona_id, updated_at)：全局人格（单行 key='global'）

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
            CREATE TABLE IF NOT EXISTS webui_prefs (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS personas (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                system_prompt TEXT NOT NULL DEFAULT '',
                vocabulary TEXT NOT NULL DEFAULT '',
                behavior_rules TEXT NOT NULL DEFAULT '',
                response_style TEXT NOT NULL DEFAULT '',
                builtin INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_persona (
                group_id INTEGER PRIMARY KEY,
                persona_id TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS persona_global (
                key TEXT PRIMARY KEY,
                persona_id TEXT NOT NULL,
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

    # ---------- Web UI 外观偏好（主题/背景，复用同一 SQLite 持久化机制） ----------
    def get_pref(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM webui_prefs WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

    def set_pref(self, key: str, value: str) -> None:
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO webui_prefs (key, value, updated_at) VALUES (?,?,?)",
                (key, value, time.time()),
            )
            self._conn.commit()

    def delete_pref(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM webui_prefs WHERE key=?", (key,))
            self._conn.commit()

    def list_prefs(self) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM webui_prefs").fetchall()
            return [(r["key"], r["value"]) for r in rows]

    # ---------- Persona（人格资源 / 全局人格 / 群聊人格） ----------
    def list_personas(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, description, system_prompt, vocabulary, behavior_rules,"
                " response_style, builtin, created_at, updated_at FROM personas ORDER BY builtin DESC, name"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_persona(self, persona_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, description, system_prompt, vocabulary, behavior_rules,"
                " response_style, builtin, created_at, updated_at FROM personas WHERE id=?",
                (persona_id,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_persona(self, persona: dict) -> None:
        import time
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO personas (id, name, description, system_prompt, vocabulary,"
                " behavior_rules, response_style, builtin, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET name=excluded.name,"
                " description=excluded.description, system_prompt=excluded.system_prompt,"
                " vocabulary=excluded.vocabulary, behavior_rules=excluded.behavior_rules,"
                " response_style=excluded.response_style, builtin=excluded.builtin,"
                " updated_at=excluded.updated_at",
                (persona["id"], persona["name"], persona.get("description", ""),
                 persona.get("system_prompt", ""), persona.get("vocabulary", ""),
                 persona.get("behavior_rules", ""), persona.get("response_style", ""),
                 1 if persona.get("builtin") else 0,
                 persona.get("created_at", now), now),
            )
            self._conn.commit()

    def delete_persona(self, persona_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM personas WHERE id=? AND builtin=0", (persona_id,))
            # 级联清理引用该人格的全局/群映射（避免悬挂引用回退逻辑处理）
            if cur.rowcount:
                self._conn.execute("DELETE FROM persona_global WHERE persona_id=?", (persona_id,))
                self._conn.execute("DELETE FROM group_persona WHERE persona_id=?", (persona_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def get_global_persona_id(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT persona_id FROM persona_global WHERE key='global'").fetchone()
            return row["persona_id"] if row else None

    def set_global_persona_id(self, persona_id: str) -> None:
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO persona_global (key, persona_id, updated_at) VALUES ('global',?,?)",
                (persona_id, time.time()),
            )
            self._conn.commit()

    def clear_global_persona_id(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM persona_global WHERE key='global'")
            self._conn.commit()

    def get_group_persona_id(self, group_id: int) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT persona_id FROM group_persona WHERE group_id=?", (group_id,)).fetchone()
            return row["persona_id"] if row else None

    def set_group_persona_id(self, group_id: int, persona_id: str) -> None:
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO group_persona (group_id, persona_id, updated_at) VALUES (?,?,?)",
                (group_id, persona_id, time.time()),
            )
            self._conn.commit()

    def delete_group_persona_id(self, group_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM group_persona WHERE group_id=?", (group_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_group_bindings(self) -> List[dict]:
        """全部群人格绑定（Web UI 管理页展示用；有界表）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT group_id, persona_id, updated_at FROM group_persona ORDER BY group_id").fetchall()
            return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
