import json
import os
import random
import sqlite3
import time
from typing import Dict, Optional

from src.config import Settings
from src.core.sanitizer import sanitize_untrusted_text
from src.models import GlobalState, GroupState
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class ContextManager:
    """群的上下文管理：GroupState 生命周期、上下文读写、崩溃备份、接话概率、重复回复过滤。"""

    def __init__(self, config: Settings, groups: Dict[int, GroupState], global_state: GlobalState):
        self.config = config
        self.groups = groups
        self.global_state = global_state

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState(context_size=getattr(self.config, "CONTEXT_SIZE", 300))
        return self.groups[group_id]

    # ---------- 上下文 ----------
    def add_context(self, group_id: int, user_id: int, message: str, is_bot: bool = False) -> None:
        state = self.get_group_state(group_id)
        state.context.append({
            "user_id": user_id,
            "message": message,
            "is_bot": is_bot,
            "time": time.time()
        })

    def get_context_text(self, group_id: int, max_messages: int = 150) -> str:
        state = self.get_group_state(group_id)
        msgs = list(state.context)[-max_messages:]
        lines = []
        for idx, m in enumerate(msgs, 1):
            who = "机器人(花璃)" if m.get("is_bot", False) else f"用户{m.get('user_id', 0)}"
            # 代码层防注入：历史消息按不可信数据处理，清洗后再进上下文
            msg_text, _ = sanitize_untrusted_text(str(m.get("message", "")))
            lines.append(f"[{idx}] {who}: {msg_text}")
        return "\n".join(lines)

    # ---------- 回复概率（主动发言概率全部配置化，默认值=原硬编码，行为零变化） ----------
    @staticmethod
    def _prob(value, default: float) -> float:
        """防御性取值：非法配置（NaN/Inf/越界）兜底为默认值，不抛异常（运行期不炸）。"""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        if v != v or v in (float("inf"), float("-inf")):  # NaN / Inf
            return default
        if not (0.0 <= v <= 1.0):
            return default
        return v

    def should_reply_by_context(self, group_id: int) -> bool:
        state = self.get_group_state(group_id)
        cfg = self.config
        recent_msgs = list(state.context)[-5:]
        if not recent_msgs:
            prob = self._prob(
                getattr(cfg, "PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY", 0.02), 0.02)
            return random.random() < prob
        user_msgs = [m for m in recent_msgs if not m["is_bot"]]
        if not user_msgs:
            return False
        prob = self._prob(getattr(cfg, "PROACTIVE_MESSAGE_BASE_PROBABILITY", 0.03), 0.03)
        if len(user_msgs) >= 2:
            prob += self._prob(getattr(cfg, "PROACTIVE_MESSAGE_USER_BOOST", 0.01), 0.01)
        if len(set(m["user_id"] for m in user_msgs)) == 1:
            prob = self._prob(
                getattr(cfg, "PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY", 0.02), 0.02)
        last_msg = recent_msgs[-1]
        if last_msg and not last_msg.get("is_bot", False) and len(str(last_msg.get("message", ""))) < 2:
            prob = self._prob(
                getattr(cfg, "PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY", 0.02), 0.02)
        bot_count = sum(1 for m in recent_msgs[-3:] if m.get("is_bot", False))
        if bot_count >= 2:
            prob *= self._prob(getattr(cfg, "PROACTIVE_MESSAGE_BOT_MULTIPLIER", 0.3), 0.3)
        prob = max(
            self._prob(getattr(cfg, "PROACTIVE_MESSAGE_MIN_PROBABILITY", 0.01), 0.01),
            min(self._prob(getattr(cfg, "PROACTIVE_MESSAGE_MAX_PROBABILITY", 0.05), 0.05), prob),
        )
        roll = random.random()
        logger.debug(f"Context reply prob for group {group_id}: {prob:.2f}, roll={roll:.2f}")
        return roll < prob

    # ---------- 重复回复检测 ----------
    def is_duplicate_reply(self, group_id: int, reply: str) -> bool:
        state = self.get_group_state(group_id)
        recent = state.recent_bot_replies
        if not recent:
            return False
        if reply in recent:
            return True
        words = set(reply)
        for old in recent:
            old_words = set(old)
            if not old_words:
                continue
            overlap = len(words & old_words) / len(old_words)
            if overlap >= 0.9:
                return True
        return False

    def add_recent_reply(self, group_id: int, reply: str) -> None:
        state = self.get_group_state(group_id)
        state.recent_bot_replies.append(reply)

    # ---------- 上下文崩溃持久化（SQLite） ----------
    def _backup_db_path(self) -> Optional[str]:
        """备份库路径：旧 .json 配置自动映射到同目录 .db（兼容旧 .env）。"""
        path = self.config.CONTEXT_BACKUP_PATH
        if not path:
            return None
        if str(path).lower().endswith(".json"):
            return str(path)[:-5] + ".db"
        return path

    def _open_backup_conn(self, path: str, row_factory: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            pass  # 只读介质时静默降级
        return conn

    def _init_backup_db(self, conn) -> None:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS group_context (
            group_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            user_id INTEGER,
            message TEXT,
            is_bot INTEGER NOT NULL DEFAULT 0,
            time REAL,
            PRIMARY KEY (group_id, seq)
        );
        CREATE TABLE IF NOT EXISTS processed_ids (
            group_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (group_id, message_id)
        );
        """)
        conn.commit()

    def _migrate_backup_from_json(self, legacy_path: str) -> None:
        """把旧 context_backup.json 导入 SQLite（兼容纯数组与 {"messages":..., "processed_ids":...} 两种格式）。"""
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                backup = json.load(f)
        except Exception as e:
            logger.error(f"上下文备份迁移失败：读取旧 JSON 出错: {e}")
            return
        if not isinstance(backup, dict):
            return
        db_path = self._backup_db_path()
        conn = sqlite3.connect(db_path)
        try:
            self._init_backup_db(conn)
            restored = 0
            restored_ids = 0
            for group_id_str, value in backup.items():
                if isinstance(value, dict):
                    messages = value.get("messages", [])
                    processed_ids = value.get("processed_ids", [])
                elif isinstance(value, list):
                    messages = value
                    processed_ids = []
                else:
                    continue
                try:
                    group_id = int(group_id_str)
                except (TypeError, ValueError):
                    continue
                for seq, msg in enumerate(messages[-50:]):
                    if isinstance(msg, dict) and "message" in msg:
                        conn.execute(
                            "INSERT OR REPLACE INTO group_context (group_id, seq, user_id, message, is_bot, time)"
                            " VALUES (?,?,?,?,?,?)",
                            (group_id, seq,
                             msg.get("user_id", 0),
                             str(msg.get("message", "")),
                             1 if msg.get("is_bot", False) else 0,
                             msg.get("time", 0.0)),
                        )
                        restored += 1
                for mid in processed_ids[-200:]:
                    try:
                        conn.execute("INSERT OR IGNORE INTO processed_ids (group_id, message_id) VALUES (?,?)",
                                     (group_id, int(mid)))
                        restored_ids += 1
                    except (ValueError, TypeError):
                        continue
            conn.commit()
            try:
                os.replace(legacy_path, legacy_path + ".migrated")
                logger.info(f"旧上下文备份 JSON 已备份为: {legacy_path}.migrated")
            except OSError as e:
                logger.warning(f"旧上下文备份 JSON 备份改名失败（可手动删除）: {e}")
            logger.info(f"上下文备份已从 JSON 迁移到 SQLite: {restored} 条消息, {restored_ids} 条消息 id -> {db_path}")
        except Exception as e:
            logger.error(f"上下文备份迁移失败: {e}")
        finally:
            conn.close()

    def load_context_backup(self) -> None:
        """启动时从 SQLite 读取上次保存的上下文备份（每群最多恢复最近 50 条 + 最近 200 条已处理消息 id）。"""
        db_path = self._backup_db_path()
        if not db_path:
            return
        try:
            dirname = os.path.dirname(db_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            conn = self._open_backup_conn(db_path)
            try:
                self._init_backup_db(conn)
                cnt = conn.execute("SELECT COUNT(*) FROM group_context").fetchone()[0]
            finally:
                conn.close()
            # 旧 JSON 迁移：db 为空且旧 json 存在时导入一次
            legacy = self.config.CONTEXT_BACKUP_PATH
            if cnt == 0 and legacy and str(legacy).lower().endswith(".json") and os.path.exists(legacy):
                self._migrate_backup_from_json(legacy)

            conn = self._open_backup_conn(db_path, row_factory=True)
            try:
                restored = 0
                restored_ids = 0
                for r in conn.execute(
                        "SELECT group_id, user_id, message, is_bot, time FROM group_context ORDER BY group_id, seq"):
                    state = self.get_group_state(r["group_id"])
                    state.context.append({
                        "user_id": r["user_id"],
                        "message": r["message"],
                        "is_bot": bool(r["is_bot"]),
                        "time": r["time"] or 0.0,
                    })
                    restored += 1
                for r in conn.execute("SELECT group_id, message_id FROM processed_ids"):
                    self.get_group_state(r["group_id"]).processed_msg_ids.append(r["message_id"])
                    restored_ids += 1
                if restored or restored_ids:
                    logger.info(f"上下文备份已恢复: {restored} 条消息, {restored_ids} 条已处理消息 id")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"加载上下文备份失败: {e}")

    async def save_context_backup(self) -> None:
        """把每群最近 50 条上下文 + 最近 200 条已处理消息 id 写入 SQLite（单事务全量重写）。

        已处理消息 id 一起持久化：崩溃重启后 NapCat 重投旧消息时不会重复回复。
        """
        db_path = self._backup_db_path()
        if not db_path:
            return
        try:
            dirname = os.path.dirname(db_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)
            conn = self._open_backup_conn(db_path)
            try:
                self._init_backup_db(conn)
                conn.execute("BEGIN")
                conn.execute("DELETE FROM group_context")
                conn.execute("DELETE FROM processed_ids")
                for group_id, state in self.groups.items():
                    msgs = list(state.context)[-50:]
                    processed_ids = list(state.processed_msg_ids)[-200:]
                    if not msgs and not processed_ids:
                        continue
                    for seq, m in enumerate(msgs):
                        conn.execute(
                            "INSERT INTO group_context (group_id, seq, user_id, message, is_bot, time)"
                            " VALUES (?,?,?,?,?,?)",
                            (group_id, seq,
                             m.get("user_id", 0),
                             str(m.get("message", "")),
                             1 if m.get("is_bot", False) else 0,
                             m.get("time", time.time())),
                        )
                    for mid in processed_ids:
                        conn.execute("INSERT OR IGNORE INTO processed_ids (group_id, message_id) VALUES (?,?)",
                                     (group_id, mid))
                conn.commit()
                logger.debug(f"上下文备份已保存: {len(self.groups)} 个群")
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"保存上下文备份失败: {e}")
