from typing import Any, Optional
from loguru import logger

from src.config import Settings
from src.services.sender import Sender
from src.services.memory_manager import MemoryManager


class CommandHandler:
    """指令处理：用户指令（/help /memory /forget /forget_me）+ 管理员指令（/memory_clear /memory_dump）。

    从 MessageRouter 拆分（上帝类第二拆）：指令与流程控制解耦，新增指令只需改这里。
    所有指令都只操作调用者自己的记忆（或管理员清群），不经过 LLM，无注入面。
    """

    def __init__(self, config: Settings, sender: Sender, memory_manager: MemoryManager):
        self.config = config
        self.sender = sender
        self.memory_manager = memory_manager

    async def handle(self, text: str, user_id: int, group_id: int) -> bool:
        """处理指令，返回 True 表示已处理（不再走正常聊天流程）。"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        is_admin = self.config.ADMIN_QQ_IDS and user_id in self.config.ADMIN_QQ_IDS

        if cmd == "/help":
            await self._cmd_help(group_id, is_admin)
            return True
        if cmd == "/memory":
            await self._cmd_memory(user_id, group_id)
            return True
        if cmd == "/forget":
            await self._cmd_forget(user_id, group_id, arg)
            return True
        if cmd == "/forget_me":
            await self._cmd_forget_me(user_id, group_id)
            return True
        if is_admin and cmd == "/memory_clear":
            await self._cmd_memory_clear(group_id)
            return True
        if is_admin and cmd == "/memory_dump":
            await self._cmd_memory_dump(group_id)
            return True
        return False

    # ---------- 各指令实现 ----------
    async def _cmd_help(self, group_id: int, is_admin: bool) -> None:
        lines = [
            "花璃指令菜单：",
            "/help 显示本菜单",
            "/memory 看看我记住了你什么",
            "/forget 关键词 删掉包含该词的记忆",
            "/forget_me 清空我对你的全部记忆",
        ]
        if is_admin:
            lines.append("/memory_clear 清空本群所有记忆（管理员）")
            lines.append("/memory_dump 导出本群记忆（管理员）")
        lines.append("另外 @花璃 或在群里聊天就有机会被她接话～")
        await self.sender.send_group_message(group_id, "\n".join(lines))

    async def _cmd_memory(self, user_id: int, group_id: int) -> None:
        notes = self.memory_manager.get_user_notes(user_id, group_id)
        if not notes:
            await self.sender.send_group_message(group_id, "关于你的记忆：目前一条都没有哦")
        else:
            snippet = "；".join(notes[-10:])[:200]
            await self.sender.send_group_message(group_id, f"关于你的记忆：{snippet}")

    async def _cmd_forget(self, user_id: int, group_id: int, arg: str) -> None:
        if not arg:
            await self.sender.send_group_message(group_id, "用法：/forget 关键词（删除包含该词的记忆）")
            return
        removed = await self.memory_manager.remove_notes_containing(user_id, group_id, arg)
        if removed:
            await self.sender.send_group_message(group_id, f"已删除 {removed} 条包含「{arg[:20]}」的记忆")
        else:
            await self.sender.send_group_message(group_id, f"没找到包含「{arg[:20]}」的记忆")

    async def _cmd_forget_me(self, user_id: int, group_id: int) -> None:
        removed = await self.memory_manager.clear_user_memory(user_id, group_id)
        if removed:
            await self.sender.send_group_message(group_id, f"已清空关于你的 {removed} 条记忆")
        else:
            await self.sender.send_group_message(group_id, "你还没有被我记住什么")

    async def _cmd_memory_clear(self, group_id: int) -> None:
        group_cleared = 0
        for uid, gid in self.memory_manager.iter_user_groups():
            if gid == group_id:
                group_cleared += await self.memory_manager.clear_user_memory(uid, group_id)
        await self.sender.send_group_message(group_id, f"已清空本群 {group_cleared} 条记忆")

    async def _cmd_memory_dump(self, group_id: int) -> None:
        lines = []
        for uid, gid in self.memory_manager.iter_user_groups():
            if gid == group_id:
                notes = self.memory_manager.get_user_notes(uid, group_id)
                if notes:
                    lines.append(f"用户{uid}: " + "；".join(notes[-5:])[:100])
        dump = "\n".join(lines) if lines else "本群暂无记忆"
        await self.sender.send_group_message(group_id, dump[:400])
