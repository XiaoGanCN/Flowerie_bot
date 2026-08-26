import os
import json
import re
import time
import asyncio
from typing import Dict, Any, Optional, List
from loguru import logger


class MemoryManager:
    """按 (user_id, group_id) 隔离的记忆库，持久化到 JSON。

    安全审计加固：
    - P1 权限边界：写入目标恒为调用方传入的 user_id（LLM 无法指定他人）
    - P2 来源元数据：每条记忆记录 source_user / source_group / created_at /
      source_message_id / confidence，避免"模型推断当事实"
    - P2 用户控制：支持 /memory /forget /forget_me（用户主动查看/删除）
    - P3 数据治理：TTL 过期清理 + 审计日志
    """

    def __init__(self, memory_path: str, ttl_days: int = 0, audit_log_path: Optional[str] = None, model_memory_ttl_days: int = 30):
        self.memory_path = memory_path
        self.ttl_days = max(0, int(ttl_days or 0))
        self.model_memory_ttl_days = max(0, int(model_memory_ttl_days or 0))
        self.audit_log_path = audit_log_path
        self.memory: Dict[str, Dict] = {}
        self._save_lock = asyncio.Lock()
        self._load()

    def _key(self, user_id: int, group_id: int) -> str:
        return f"{user_id}_{group_id}"

    def _load(self) -> None:
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, 'r', encoding='utf-8') as f:
                    self.memory = json.load(f)
                logger.info(f"记忆库已加载，共 {len(self.memory)} 个用户-群组合")
                self._prune_expired()
            except Exception as e:
                logger.error(f"加载记忆库失败: {e}")
                self.memory = {}
        else:
            self.memory = {}
            self._save_sync()

    def _save_sync(self) -> None:
        dirname = os.path.dirname(self.memory_path)
        if dirname and not os.path.exists(dirname):
            os.makedirs(dirname, exist_ok=True)
        try:
            # 原子写入：先写 tmp 再 os.replace，防止写一半断电/被杀导致整个 JSON 损坏、记忆全丢
            tmp_path = self.memory_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.memory_path)
        except Exception as e:
            logger.error(f"保存记忆库失败: {e}")

    async def save(self) -> None:
        async with self._save_lock:
            await asyncio.to_thread(self._save_sync)

    # ---------- TTL 过期清理（P3 数据治理） ----------
    def _note_effective_ttl(self, note) -> int:
        """按置信度分级 TTL：AI 推断记忆(model)低信任、默认 30 天过期；用户原话/无标记按 ttl_days。"""
        if isinstance(note, dict) and note.get("confidence") == "model":
            return self.model_memory_ttl_days
        return self.ttl_days

    def _prune_expired(self) -> None:
        if self.ttl_days <= 0 and self.model_memory_ttl_days <= 0:
            return
        now = time.time()
        changed = False
        for key, mem in self.memory.items():
            notes = mem.get("notes")
            if not notes or not isinstance(notes, list):
                continue
            kept = []
            for note in notes:
                ttl = self._note_effective_ttl(note)
                if ttl <= 0:
                    kept.append(note)
                    continue
                created = None
                if isinstance(note, dict):
                    created = note.get("created_at")
                # 没有时间戳的旧数据无法判断年龄 → 保留
                if created is None or (now - created) < ttl * 86400:
                    kept.append(note)
                else:
                    changed = True
            if len(kept) != len(notes):
                mem["notes"] = kept
        if changed:
            logger.info("记忆 TTL 清理完成")
            self._save_sync()

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

    def get_user_memory(self, user_id: int, group_id: int) -> Dict:
        key = self._key(user_id, group_id)
        return self.memory.get(key, {})

    def get_user_notes(self, user_id: int, group_id: int) -> List[str]:
        """返回某用户在群里的记忆文本列表（供 /memory 命令展示）。"""
        mem = self.get_user_memory(user_id, group_id)
        notes = mem.get("notes", [])
        texts = []
        for note in notes:
            if isinstance(note, dict):
                texts.append(note.get("text", ""))
            elif isinstance(note, str):
                texts.append(note)
        return [t for t in texts if t]

    async def remove_notes_containing(self, user_id: int, group_id: int, keyword: str) -> int:
        """删除包含关键词的记忆，返回删除条数（供 /forget 命令）。"""
        if not keyword:
            return 0
        key = self._key(user_id, group_id)
        mem = self.memory.get(key)
        if not mem:
            return 0
        notes = mem.get("notes", [])
        kept = []
        removed = 0
        for note in notes:
            text = note.get("text", "") if isinstance(note, dict) else note
            if keyword in text:
                removed += 1
                self._audit("FORGET", user_id, group_id, text)
            else:
                kept.append(note)
        if removed:
            mem["notes"] = kept
            await self.save()
        return removed

    async def clear_user_memory(self, user_id: int, group_id: int) -> int:
        """清空某用户在群的记忆，返回清空条数（供 /forget_me /memory_clear）。"""
        key = self._key(user_id, group_id)
        mem = self.memory.get(key)
        if not mem:
            return 0
        notes = mem.get("notes", [])
        count = len(notes)
        if count:
            mem["notes"] = []
            self._audit("CLEAR", user_id, group_id, f"{count} 条")
            await self.save()
        return count

    async def update_memory(self, user_id: int, group_id: int, key: str, value: Any) -> None:
        uid = self._key(user_id, group_id)
        if uid not in self.memory:
            self.memory[uid] = {}
        self.memory[uid][key] = value
        await self.save()

    def _note_text(self, note: Any) -> str:
        return note.get("text", "") if isinstance(note, dict) else (note or "")

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
        key = self._key(user_id, group_id)
        if key not in self.memory:
            self.memory[key] = {}
        if 'notes' not in self.memory[key]:
            self.memory[key]['notes'] = []
        notes = self.memory[key]['notes']

        # 高相似度去重：完全相同、互为子串、相似度 >= 0.85、
        # 或较短一条的字符集 ≥80% 被较长一条包含（可容忍错别字）的旧记忆不再重复记录
        from difflib import SequenceMatcher

        def _norm(s: str) -> str:
            # 去掉空白与常见标点，只比内容骨架
            return re.sub(r"[\s，。！？、,.!?;；:：()（）「」『』【】\[\]]+", "", s)

        text_norm = _norm(text)
        for existing in notes:
            existing_norm = _norm(self._note_text(existing))
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

        note = {
            "text": text,
            "source_user": source_user if source_user is not None else user_id,
            "source_group": source_group if source_group is not None else group_id,
            "source_message_id": source_message_id,
            "created_at": time.time(),
            "confidence": confidence,
        }
        notes.append(note)
        if len(notes) > 50:
            self.memory[key]['notes'] = notes[-25:]
        self._audit("WRITE", user_id, group_id, text)
        await self.save()

    def get_memory_context(self, user_id: int, group_id: int, max_notes: int = 20, max_length: int = 500) -> str:
        mem = self.get_user_memory(user_id, group_id)
        if not mem:
            return ""
        lines = []
        for key, value in mem.items():
            if key == 'notes':
                if value and isinstance(value, list):
                    recent_notes = value[-max_notes:]
                    if recent_notes:
                        texts = [self._note_text(n) for n in recent_notes]
                        lines.append("关于该用户的记录: " + "; ".join(texts))
            else:
                lines.append(f"{key}: {value}")
        full_text = "；".join(lines)
        if len(full_text) > max_length:
            full_text = full_text[:max_length] + "...（已截断）"
        return full_text
