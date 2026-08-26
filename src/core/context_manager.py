import os
import json
import time
import random
from typing import Dict, List
from loguru import logger

from src.core.sanitizer import sanitize_untrusted_text
from src.config import Settings
from src.models import GroupState, GlobalState


class ContextManager:
    """群的上下文管理：GroupState 生命周期、上下文读写、崩溃备份、接话概率、重复回复过滤。"""

    def __init__(self, config: Settings, groups: Dict[int, GroupState], global_state: GlobalState):
        self.config = config
        self.groups = groups
        self.global_state = global_state

    def get_group_state(self, group_id: int) -> GroupState:
        if group_id not in self.groups:
            self.groups[group_id] = GroupState()
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
            who = "机器人(花璃)" if m["is_bot"] else f"用户{m['user_id']}"
            # 代码层防注入：历史消息按不可信数据处理，清洗后再进上下文
            msg_text, _ = sanitize_untrusted_text(str(m["message"]))
            lines.append(f"[{idx}] {who}: {msg_text}")
        return "\n".join(lines)

    # ---------- 回复概率 ----------
    def should_reply_by_context(self, group_id: int) -> bool:
        state = self.get_group_state(group_id)
        recent_msgs = list(state.context)[-5:]
        if not recent_msgs:
            return random.random() < 0.02
        user_msgs = [m for m in recent_msgs if not m["is_bot"]]
        if not user_msgs:
            return False
        prob = 0.03
        if len(user_msgs) >= 2:
            prob += 0.01
        if len(set(m["user_id"] for m in user_msgs)) == 1:
            prob = 0.02
        last_msg = recent_msgs[-1]
        if last_msg and not last_msg["is_bot"] and len(last_msg["message"]) < 2:
            prob = 0.02
        bot_count = sum(1 for m in recent_msgs[-3:] if m.get("is_bot", False))
        if bot_count >= 2:
            prob *= 0.3
        prob = max(0.01, min(0.05, prob))
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

    # ---------- 上下文崩溃持久化 ----------
    def load_context_backup(self) -> None:
        """启动时读取上次保存的上下文备份（每群最多恢复最近 50 条 + 最近 200 条已处理消息 id）。"""
        path = self.config.CONTEXT_BACKUP_PATH
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                backup = json.load(f)
            restored = 0
            restored_ids = 0
            for group_id_str, value in backup.items():
                # 兼容旧格式（纯消息数组）与新格式（{"messages": [...], "processed_ids": [...]}）
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
                state = self.get_group_state(group_id)
                for msg in messages[-50:]:
                    if isinstance(msg, dict) and "message" in msg:
                        state.context.append(msg)
                        restored += 1
                for mid in processed_ids[-200:]:
                    state.processed_msg_ids.append(mid)
                    restored_ids += 1
            if restored or restored_ids:
                logger.info(f"上下文备份已恢复: {len(backup)} 个群共 {restored} 条消息, {restored_ids} 条已处理消息 id")
        except Exception as e:
            logger.error(f"加载上下文备份失败: {e}")

    async def save_context_backup(self) -> None:
        """把每群最近 50 条上下文 + 最近 200 条已处理消息 id 写入备份（原子写入）。

        已处理消息 id 一起持久化：崩溃重启后 NapCat 重投旧消息时不会重复回复。
        """
        path = self.config.CONTEXT_BACKUP_PATH
        if not path:
            return
        try:
            backup = {}
            for group_id, state in self.groups.items():
                msgs = list(state.context)[-50:]
                processed_ids = list(state.processed_msg_ids)[-200:]
                if msgs or processed_ids:
                    backup[str(group_id)] = {
                        "messages": msgs,
                        "processed_ids": processed_ids,
                    }
            if not backup:
                return
            dirname = os.path.dirname(path)
            if dirname and not os.path.exists(dirname):
                os.makedirs(dirname, exist_ok=True)
            tmp_path = path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(backup, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            logger.debug(f"上下文备份已保存: {len(backup)} 个群")
        except Exception as e:
            logger.error(f"保存上下文备份失败: {e}")
