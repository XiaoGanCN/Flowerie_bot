import os
import json
import re
import asyncio
from typing import Dict, Any, Optional
from loguru import logger

class MemoryManager:
    """按 (user_id, group_id) 隔离的记忆库，持久化到 JSON"""
    def __init__(self, memory_path: str):
        self.memory_path = memory_path
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
            with open(self.memory_path, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存记忆库失败: {e}")

    async def save(self) -> None:
        async with self._save_lock:
            await asyncio.to_thread(self._save_sync)

    def get_user_memory(self, user_id: int, group_id: int) -> Dict:
        key = self._key(user_id, group_id)
        return self.memory.get(key, {})

    async def update_memory(self, user_id: int, group_id: int, key: str, value: Any) -> None:
        uid = self._key(user_id, group_id)
        if uid not in self.memory:
            self.memory[uid] = {}
        self.memory[uid][key] = value
        await self.save()

    async def append_memory_text(self, user_id: int, group_id: int, text: str) -> None:
        if not text or not text.strip():
            return
        text = text.strip()
        key = self._key(user_id, group_id)
        if key not in self.memory:
            self.memory[key] = {}
        if 'notes' not in self.memory[key]:
            self.memory[key]['notes'] = []
        notes = self.memory[key]['notes']

        # 高相似度去重：完全相同、互为子串、或字符相似度 >= 0.85 的旧记忆不再重复记录
        from difflib import SequenceMatcher

        def _norm(s: str) -> str:
            # 去掉空白与常见标点，只比内容骨架
            return re.sub(r"[\s，。！？、,.!?;；:：()（）「」『』【】\[\]]+", "", s)

        text_norm = _norm(text)
        for existing in notes:
            existing_norm = _norm(existing)
            if not existing_norm:
                continue
            if existing_norm == text_norm:
                return
            if existing_norm in text_norm or text_norm in existing_norm:
                return
            if SequenceMatcher(None, existing_norm, text_norm).ratio() >= 0.85:
                return

        notes.append(text)
        if len(notes) > 50:
            self.memory[key]['notes'] = notes[-25:]
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
                        lines.append("关于该用户的记录: " + "; ".join(recent_notes))
            else:
                lines.append(f"{key}: {value}")
        full_text = "；".join(lines)
        if len(full_text) > max_length:
            full_text = full_text[:max_length] + "...（已截断）"
        return full_text