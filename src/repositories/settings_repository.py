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
            CREATE TABLE IF NOT EXISTS plugins (
                id TEXT PRIMARY KEY,
                manifest_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                approved_permissions TEXT NOT NULL DEFAULT '',
                protection TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'discovered',
                install_source TEXT NOT NULL DEFAULT '',
                installed_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_bootstrap (
                key TEXT PRIMARY KEY,
                state TEXT NOT NULL,
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

    def get_config_meta(self, key: str) -> Optional[Tuple[str, float]]:
        """返回 (value, updated_at)；用于与本地 .env 的修改时间比较（防旧值覆盖）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value, updated_at FROM app_config WHERE key=?", (key,)).fetchone()
            return (row["value"], row["updated_at"]) if row else None

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

    def delete_config(self, key: str) -> bool:
        """删除某配置项（注销账号用：清除 settings.db 中的管理凭据）。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM app_config WHERE key=?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

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

    # ---------- 插件注册表（Plugin Manager） ----------
    def upsert_plugin(self, plugin: dict) -> None:
        """插入或更新插件注册行（manifest_json 为规范化 JSON 文本）。"""
        import time
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO plugins (id, manifest_json, enabled, approved_permissions,"
                " protection, status, install_source, installed_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET manifest_json=excluded.manifest_json,"
                " enabled=excluded.enabled, approved_permissions=excluded.approved_permissions,"
                " protection=excluded.protection, status=excluded.status,"
                " install_source=excluded.install_source, updated_at=excluded.updated_at",
                (plugin["id"], plugin["manifest_json"],
                 1 if plugin.get("enabled") else 0,
                 ",".join(plugin.get("approved_permissions", []) or []),
                 plugin.get("protection", "normal"),
                 plugin.get("status", "discovered"),
                 plugin.get("install_source", ""),
                 float(plugin.get("installed_at") or now), now),
            )
            self._conn.commit()

    def get_plugin(self, plugin_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, manifest_json, enabled, approved_permissions, protection,"
                " status, install_source, installed_at, updated_at FROM plugins WHERE id=?",
                (plugin_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_plugins(self) -> List[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, manifest_json, enabled, approved_permissions, protection,"
                " status, install_source, installed_at, updated_at FROM plugins ORDER BY id"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_plugin(self, plugin_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------- 管理员引导状态（Bootstrap Lock：单例状态行，原子 CAS） ----------
    def get_bootstrap_state(self) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM admin_bootstrap WHERE key='admin'").fetchone()
            return row["state"] if row else None

    def try_mark_bootstrap_initialized(self) -> bool:
        """原子 compare-and-set：仅当当前为 uninitialized 时置为 initialized。

        返回 True 表示本次调用成功拿到初始化权（并发注册只有一个成功）。
        """
        import time
        with self._lock:
            # 行不存在 → 插入 uninitialized 行（幂等），然后尝试 反条件更新
            self._conn.execute(
                "INSERT OR IGNORE INTO admin_bootstrap (key, state, updated_at) VALUES ('admin','uninitialized',?)",
                (time.time(),),
            )
            cur = self._conn.execute(
                "UPDATE admin_bootstrap SET state='initialized', updated_at=? "
                "WHERE key='admin' AND state='uninitialized'",
                (time.time(),),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def mark_bootstrap_uninitialized(self) -> None:
        """显式回退到 UNINITIALIZED（仅管理员在认证上下文内主动调用）。"""
        import time
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO admin_bootstrap (key, state, updated_at) VALUES ('admin','uninitialized',?)",
                (time.time(),),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
