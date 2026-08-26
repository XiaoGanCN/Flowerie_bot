import asyncio
import time
import random
from typing import Dict, Any, Optional, List
from loguru import logger

from src.config import Settings
from src.models import GroupMessage, GlobalState
from src.services.ai_client import AIClient
from src.services.memory_manager import MemoryManager
from src.services.file_parser import FileParser
from src.services.sender import Sender
from src.core.policy_engine import PolicyEngine
from src.core.message_assembler import MessageAssembler


class MessageRouter:
    def __init__(
        self,
        config: Settings,
        ai_client: AIClient,
        memory_manager: MemoryManager,
        file_parser: FileParser,
        sender: Sender,
        policy_engine: PolicyEngine,
    ):
        self.config = config
        self.ai_client = ai_client
        self.memory_manager = memory_manager
        self.file_parser = file_parser
        self.sender = sender
        self.policy_engine = policy_engine
        self.global_state = self.policy_engine.global_state
        # 消息组装（文本/识图/转发/卡片/文件/存档）拆分到 MessageAssembler
        self.assembler = MessageAssembler(config, ai_client, file_parser, self.global_state)
        self._active_chat_task: Optional[asyncio.Task] = None
        self._context_backup_task: Optional[asyncio.Task] = None
        # 并发上限：同时处理的消息数（WS 层用它限制 AI/识图并发，防止突发消息打爆 API）
        self.process_semaphore = asyncio.Semaphore(max(1, config.MAX_CONCURRENT_AI))

    async def start(self):
        """启动主动聊天循环（若配置允许）与上下文备份循环"""
        if not self.config.ONLY_REPLY_WHEN_AT:
            self._active_chat_task = asyncio.create_task(self._active_chat_loop())
            logger.info("Active chat loop started")
        # 周期备份上下文（意外去世后重启可恢复最近 50 条）
        self._context_backup_task = asyncio.create_task(self._context_backup_loop())
        logger.info(f"Context backup loop started (every {self.config.CONTEXT_BACKUP_INTERVAL}s)")

    async def stop(self):
        if self._active_chat_task:
            self._active_chat_task.cancel()
            try:
                await self._active_chat_task
            except asyncio.CancelledError:
                pass
        if self._context_backup_task:
            self._context_backup_task.cancel()
            try:
                await self._context_backup_task
            except asyncio.CancelledError:
                pass
        # 停前最后保存一次上下文
        await self.policy_engine.save_context_backup()

    async def _context_backup_loop(self):
        """周期性保存每群最近 50 条上下文。"""
        interval = max(10, self.config.CONTEXT_BACKUP_INTERVAL)
        while True:
            try:
                await asyncio.sleep(interval)
                await self.policy_engine.save_context_backup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Context backup loop error: {e}")

    async def process_event(self, data: Dict[str, Any]) -> None:
        post_type = data.get("post_type")
        if post_type == "message":
            await self._handle_message(data)
        elif post_type == "notice":
            notice_type = data.get("notice_type")
            if notice_type == "group_upload":
                self._handle_group_upload(data)
            elif notice_type == "notify" and data.get("sub_type") == "poke":
                await self._handle_poke(data)

    # ---------- 消息处理 ----------
    async def _handle_message(self, data: Dict[str, Any]) -> None:
        if data.get("message_type") != "group":
            return
        group_id = data.get("group_id")
        if not group_id:
            return

        if self.config.ALLOWED_GROUP_IDS and group_id not in self.config.ALLOWED_GROUP_IDS:
            logger.debug(f"Group {group_id} not in whitelist, ignoring")
            return

        message_array = data.get("message", [])
        raw_time = data.get("time", int(time.time()))
        user_id = data.get("user_id")
        msg_id = data.get("message_id")
        if not user_id:
            return

        # 消息组装（文本/识图/转发/卡片/文件/存档）交给 MessageAssembler
        full_text, image_descriptions, is_reply_to_bot, has_reply_to_other, has_at_others = await self.assembler.assemble(
            message_array, user_id, group_id, raw_time,
        )
        # 提取纯文本与是否@机器人（决策需要）
        clean_text, is_mentioned = self.file_parser.extract_mention_and_text(message_array, self.config.BOT_QQ)

        # 用户命令（P2-9 记忆管理：/memory /forget /forget_me；管理员 /memory_clear /memory_dump）
        if clean_text.strip().startswith("/") and await self._handle_user_command(clean_text.strip(), user_id, group_id):
            return

        # 去重
        state = self.policy_engine.get_group_state(group_id)
        if msg_id in state.processed_msg_ids:
            logger.debug(f"Message {msg_id} already processed")
            return
        state.processed_msg_ids.append(msg_id)

        # 复读检测
        if full_text:
            if self.policy_engine.check_and_record_repeat(full_text, group_id):
                await self.sender.send_group_message(group_id, full_text)
                self.policy_engine.record_bot_reply(group_id)
                self.policy_engine.add_context(group_id, 0, full_text, is_bot=True)
                return

        # 引战检测
        if self.config.TOXIC_GROUP_IDS and group_id in self.config.TOXIC_GROUP_IDS:
            if await self.ai_client.is_toxic(full_text):
                now = time.time()
                last_warn = self.global_state.last_toxic_warning.get(group_id, 0)
                if now - last_warn >= self.config.TOXIC_WARNING_COOLDOWN:
                    await self.sender.send_group_message(group_id, "居然有人在引战喔（坏笑，马上发消息给群主咪）")
                    self.global_state.last_toxic_warning[group_id] = now
                    self.policy_engine.record_bot_reply(group_id)
                return

        # 构建消息对象
        msg = GroupMessage(
            group_id=group_id,
            user_id=user_id,
            message_id=msg_id,
            raw_message=full_text,
            message_array=message_array,
            time=raw_time,
            clean_text=clean_text,
            is_mentioned=is_mentioned,
            is_reply_to_bot=is_reply_to_bot,
            has_reply_to_other=has_reply_to_other,
            has_at_others=has_at_others,
            full_text=full_text,
        )

        # 更新上下文
        self.policy_engine.add_context(group_id, user_id, full_text[:200], is_bot=False)

        # ---------- 决定是否回复 ----------
        should_reply = self._should_reply(msg)
        if not should_reply:
            return

        # 强制记忆检测
        force_memory = self.policy_engine.should_force_memory(clean_text, full_text, has_at_others)
        silent_memory_only = force_memory and not is_mentioned and not is_reply_to_bot

        # 机器人冷却检查
        if not self.policy_engine.can_bot_reply(group_id):
            logger.debug("Bot cooldown, skip reply")
            return

        # 用户冷却检查
        if not (is_mentioned or is_reply_to_bot):
            if not self.policy_engine.can_user_reply(user_id, group_id):
                logger.debug(f"User {user_id} in cooldown, skip reply")
                return
            self.policy_engine.update_user_time(user_id, group_id)
        else:
            self.policy_engine.update_user_time(user_id, group_id)

        # 每日 AI 调用预算（P1-5）：全局+每群+每用户限速，超出即拦截（可选在群里提示）
        allowed, budget_reason = self._ai_budget_available(group_id, user_id)
        if not allowed:
            if budget_reason in ("global", "group") and self.config.BUDGET_EXHAUSTED_NOTICE:
                await self._notify_budget_exhausted(group_id)
            logger.warning(f"AI 预算/限速拦截: group={group_id} user={user_id} reason={budget_reason}")
            return

        # ---------- 调用 AI ----------
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        user_prompt = full_text if full_text.strip() else (
            f"用户刚刚发了一张图片，图片内容：{'; '.join(image_descriptions)}" if image_descriptions else "用户刚刚@了你，但没有说话。"
        )
        # AI 输入截断（P1-5）：防止超长文件/转发内容一次性烧掉大量 token
        max_input = max(500, self.config.MAX_AI_INPUT_CHARS)
        if len(user_prompt) > max_input:
            user_prompt = user_prompt[:max_input] + "\n...(输入过长已截断)"
        if len(context_text) > max_input:
            context_text = context_text[-max_input:] + "\n...(上下文过长已截断)"
        reply, memory_update = await self.ai_client.chat(
            user_message=user_prompt,
            context=context_text,
            user_id=user_id,
            group_id=group_id,
            is_mentioned=is_mentioned or is_reply_to_bot,
        )

        # 处理记忆更新（P1 权限边界：target 恒为当前用户；P3 隐私：禁用群不写记忆）
        if memory_update and user_id and not self._memory_disabled(group_id):
            target_uid, mem_content = self.policy_engine.parse_memory_update(memory_update, user_id)
            if mem_content:
                await self.memory_manager.append_memory_text(
                    target_uid, group_id, mem_content,
                    source_user=user_id,
                    source_group=group_id,
                    source_message_id=msg_id,
                    confidence="model",
                )
                logger.info(f"Memory updated for user {target_uid} in group {group_id}: {mem_content}")

        # 静默记忆模式（用户明确表达偏好但未被@）：只记记忆，不回复
        if silent_memory_only and not self._memory_disabled(group_id):
            await self.memory_manager.append_memory_text(
                user_id, group_id, clean_text[:80],
                source_user=user_id,
                source_group=group_id,
                source_message_id=msg_id,
                confidence="self_claim",
            )
            logger.info(f"Force memory for user {user_id} in group {group_id}: {clean_text[:80]}")
            return

        # 重试兜底
        if is_mentioned and (not reply or not reply.strip()):
            for _ in range(3):
                reply, _ = await self.ai_client.chat(
                    user_message=user_prompt,
                    context=context_text,
                    user_id=user_id,
                    group_id=group_id,
                    is_mentioned=True,
                )
                if reply and reply.strip():
                    break
            if not reply or not reply.strip():
                reply = "喵？"

        if reply:
            if self.policy_engine.is_duplicate_reply(group_id, reply):
                logger.debug("Duplicate reply, skip")
                return

            success = await self.sender.send_group_message(group_id, reply)
            if success:
                self.policy_engine.record_bot_reply(group_id)
                self.policy_engine.add_context(group_id, 0, reply, is_bot=True)
                self.policy_engine.add_recent_reply(group_id, reply)
                logger.info(f"Reply sent: {reply[:30]}...")
            else:
                logger.error("Reply send failed")

    # ---------- 决策逻辑 ----------
    def _should_reply(self, msg: GroupMessage) -> bool:
        if self.config.ONLY_REPLY_WHEN_AT:
            if msg.is_mentioned or msg.is_reply_to_bot:
                return True
            return False
        else:
            if msg.is_mentioned or msg.is_reply_to_bot:
                return True
            if self.policy_engine.should_reply_by_context(msg.group_id):
                return True
            return False

    # ---------- 用户命令：记忆管理（P2-9 用户数据控制权） ----------
    async def _handle_user_command(self, text: str, user_id: int, group_id: int) -> bool:
        """处理记忆相关命令，返回 True 表示已处理（不再走正常流程）。"""
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        is_admin = self.config.ADMIN_QQ_IDS and user_id in self.config.ADMIN_QQ_IDS

        if cmd == "/help":
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
            return True

        if cmd == "/memory":
            notes = self.memory_manager.get_user_notes(user_id, group_id)
            if not notes:
                await self.sender.send_group_message(group_id, "关于你的记忆：目前一条都没有哦")
            else:
                snippet = "；".join(notes[-10:])[:200]
                await self.sender.send_group_message(group_id, f"关于你的记忆：{snippet}")
            return True

        if cmd == "/forget":
            if not arg:
                await self.sender.send_group_message(group_id, "用法：/forget 关键词（删除包含该词的记忆）")
                return True
            removed = await self.memory_manager.remove_notes_containing(user_id, group_id, arg)
            if removed:
                await self.sender.send_group_message(group_id, f"已删除 {removed} 条包含「{arg[:20]}」的记忆")
            else:
                await self.sender.send_group_message(group_id, f"没找到包含「{arg[:20]}」的记忆")
            return True

        if cmd == "/forget_me":
            removed = await self.memory_manager.clear_user_memory(user_id, group_id)
            if removed:
                await self.sender.send_group_message(group_id, f"已清空关于你的 {removed} 条记忆")
            else:
                await self.sender.send_group_message(group_id, "你还没有被我记住什么")
            return True

        # 管理员命令（P3-15）
        if is_admin and cmd == "/memory_clear":
            group_cleared = 0
            for key in list(self.memory_manager.memory.keys()):
                if key.endswith(f"_{group_id}"):
                    gid_part = key.rsplit("_", 1)[-1]
                    if str(gid_part) == str(group_id):
                        uid_part = key.split("_", 1)[0]
                        try:
                            group_cleared += await self.memory_manager.clear_user_memory(int(uid_part), group_id)
                        except (ValueError, TypeError):
                            continue
            await self.sender.send_group_message(group_id, f"已清空本群 {group_cleared} 条记忆")
            return True

        if is_admin and cmd == "/memory_dump":
            lines = []
            for key, mem in self.memory_manager.memory.items():
                uid_part, gid_part = key.split("_", 1)
                if str(gid_part) == str(group_id):
                    notes = self.memory_manager.get_user_notes(int(uid_part), group_id)
                    if notes:
                        lines.append(f"用户{uid_part}: " + "；".join(notes[-5:])[:100])
            dump = "\n".join(lines) if lines else "本群暂无记忆"
            await self.sender.send_group_message(group_id, dump[:400])
            return True

        return False

    # ---------- 每日 AI 预算（P1-5：全局 + 每群 + 每用户限速） ----------
    def _ai_budget_available(self, group_id: int, user_id: int) -> Tuple[bool, str]:
        """返回 (是否允许, 拒绝原因)。原因: ''(允许) / 'user'(用户限速) / 'global'(全局预算) / 'group'(群预算)。"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if self.global_state.ai_budget_date != today:
            self.global_state.ai_budget_date = today
            self.global_state.ai_budget_count = 0
            self.global_state.group_ai_budget_count.clear()
            self.global_state.budget_notified_groups.clear()

        # 用户级限速（per-user rate limit，不是预算，不触发提示）
        if self.config.USER_AI_CALL_MIN_INTERVAL > 0:
            last = self.global_state.user_ai_last_call.get(user_id, 0.0)
            if time.time() - last < self.config.USER_AI_CALL_MIN_INTERVAL:
                return False, "user"

        # 全局预算
        self.global_state.ai_budget_count += 1
        if self.config.DAILY_AI_CALL_BUDGET > 0 and self.global_state.ai_budget_count > self.config.DAILY_AI_CALL_BUDGET:
            return False, "global"

        # 群级预算：防止一个群把全局额度刷光
        gcount = self.global_state.group_ai_budget_count.get(group_id, 0) + 1
        self.global_state.group_ai_budget_count[group_id] = gcount
        if self.config.GROUP_DAILY_AI_CALL_BUDGET > 0 and gcount > self.config.GROUP_DAILY_AI_CALL_BUDGET:
            return False, "group"

        self.global_state.user_ai_last_call[user_id] = time.time()
        return True, ""

    async def _notify_budget_exhausted(self, group_id: int) -> None:
        """额度用尽提示：每天每群最多发一次，避免刷屏。"""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if self.global_state.budget_notified_groups.get(group_id) == today:
            return
        self.global_state.budget_notified_groups[group_id] = today
        cap = self.config.GROUP_DAILY_AI_CALL_BUDGET or self.config.DAILY_AI_CALL_BUDGET or 0
        used = self.global_state.ai_budget_count
        try:
            await self.sender.send_group_message(
                group_id, f"今日AI额度已用尽（已用{used}次/上限{cap}次），明天再来找花璃玩吧～"
            )
        except Exception as e:
            logger.error(f"额度提示发送失败: {e}")

    # ---------- 群级记忆隐私开关（P3-13） ----------
    def _memory_disabled(self, group_id: int) -> bool:
        disabled = self.config.MEMORY_DISABLED_GROUPS
        return bool(disabled) and group_id in disabled

    # ---------- 存档 ----------
    # ---------- 文件上传 ----------
    def _handle_group_upload(self, data: Dict[str, Any]):
        file_data = data.get("file", {})
        if not file_data:
            return
        user_id = data.get("user_id")
        group_id = data.get("group_id")
        if user_id and group_id:
            pending_key = f"{user_id}_{group_id}"
            self.global_state.pending_files[pending_key] = {
                "file_name": file_data.get("name", "未命名文件"),
                "file_id": file_data.get("id", ""),
                "file_size": file_data.get("size", 0),
                "busid": file_data.get("busid", 0),
                "user_id": user_id,
                "group_id": group_id,
                "time": data.get("time", time.time())
            }
            logger.debug(f"File upload cached: {file_data.get('name')} from {user_id} in {group_id}")

    # ---------- 戳戳 ----------
    async def _handle_poke(self, data: Dict[str, Any]):
        target_id = data.get("target_id") or data.get("target") or data.get("user_id")
        if target_id != self.config.BOT_QQ:
            return
        if not self.config.POKE_REPLY_ENABLED:
            return
        user_id = data.get("user_id")
        group_id = data.get("group_id")
        reply = self.policy_engine.get_poke_reply()
        if len(reply) > self.config.MAX_REPLY_LENGTH:
            reply = reply[:self.config.MAX_REPLY_LENGTH] + "..."
        if group_id:
            await self.sender.send_group_message(group_id, reply)
        else:
            await self.sender.send_private_message(user_id, reply)
        logger.info(f"Poke reply to {user_id} in {group_id}: {reply}")

    # ---------- 主动聊天循环（✅ 修复：区分留空和未配置） ----------
    async def _active_chat_loop(self):
        logger.info("Active chat loop started")
        while True:
            await asyncio.sleep(random.randint(5, 10))
            if not self.global_state.ws_connected:
                continue

            allowed_groups = self.config.ALLOWED_GROUP_IDS

            # ✅ 修复：如果白名单为空（None 或 []），表示允许所有群
            if not allowed_groups:
                # 从所有有上下文的群中随机选一个
                active_groups = list(self.policy_engine.groups.keys())
                if not active_groups:
                    logger.debug("No active groups with context, skip active chat")
                    continue
                target_group = random.choice(active_groups)
            else:
                # 有白名单则从白名单中随机选
                target_group = random.choice(allowed_groups)

            if not self.policy_engine.should_active_chat(target_group):
                continue
            await self._do_active_chat(target_group)

    async def _do_active_chat(self, group_id: int):
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        if not context_text:
            logger.debug(f"No context for group {group_id}, skip active")
            return
        for attempt in range(3):
            prompt = "你现在就是QQ群里的花璃，一个17岁高中生，正在自然地跟群友聊天。\n没有人在叫你。\n如果最近大家讨论一个话题，自然接一句，像平时一样简短而自然地说句话。\n如果群冷了，可以偶尔冒一句，简短就好。\n不要解释。\n不要说自己是AI。\n不要刻意活跃气氛。\n一句话即可，尽量短，自然。"
            reply, _ = await self.ai_client.chat(
                user_message=prompt,
                context=context_text,
                user_id=None,
                group_id=group_id,
                is_mentioned=False,
            )
            if not reply:
                break
            if self.policy_engine.is_duplicate_reply(group_id, reply):
                await asyncio.sleep(1)
                continue
            success = await self.sender.send_group_message(group_id, reply)
            if success:
                self.policy_engine.record_active_chat()
                self.policy_engine.record_bot_reply(group_id)
                self.policy_engine.add_context(group_id, 0, reply, is_bot=True)
                self.policy_engine.add_recent_reply(group_id, reply)
                logger.info(f"Active chat sent: {reply[:30]}...")
                break
            else:
                break