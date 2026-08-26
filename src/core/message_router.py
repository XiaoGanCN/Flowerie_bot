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
from src.core.sanitizer import validate_memory_content
from src.core.command_handler import CommandHandler
from src.core.budget_manager import BudgetManager


class MessageRouter:
    """事件分发与消息处理（流程编排）。

    上帝类拆分后只负责：
    - 事件分发（消息/上传/戳戳）与消息处理主流程
    - 回复决策、AI 调用编排、记忆记录
    - 后台循环（主动聊天 / 上下文备份）
    指令处理 → CommandHandler；AI 预算 → BudgetManager；消息组装 → MessageAssembler。
    """

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
        # 消息组装（文本/识图/转发/卡片/文件/存档）→ MessageAssembler
        self.assembler = MessageAssembler(config, ai_client, file_parser, self.global_state)
        # 指令处理 → CommandHandler
        self.commands = CommandHandler(config, sender, memory_manager)
        # AI 预算/限速 → BudgetManager
        self.budget = BudgetManager(config, self.global_state, sender)
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

        # 用户命令（P2-9 记忆管理：/help /memory /forget /forget_me；管理员 /memory_clear /memory_dump）
        if clean_text.strip().startswith("/") and await self.commands.handle(clean_text.strip(), user_id, group_id):
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

        # 每日 AI 调用预算（P1-5：全局+每群+每用户限速，超出即拦截（可选在群里提示））
        allowed, budget_reason = self.budget.check(group_id, user_id)
        if not allowed:
            if budget_reason in ("global", "group") and self.config.BUDGET_EXHAUSTED_NOTICE:
                await self.budget.notify_exhausted(group_id)
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
                # 代码层闸门：校验记忆内容（长度/QQ号/指令句式），拒绝即丢弃并记日志
                mem_content = validate_memory_content(mem_content)
                if mem_content is None:
                    logger.warning(f"记忆写入被代码层校验拒绝（疑似注入）: {memory_update[:60]}")
                else:
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
            claim = validate_memory_content(clean_text[:100])
            if claim is None:
                logger.warning(f"强制记忆被代码层校验拒绝（疑似注入）: {clean_text[:60]}")
                return
            await self.memory_manager.append_memory_text(
                user_id, group_id, claim,
                source_user=user_id,
                source_group=group_id,
                source_message_id=msg_id,
                confidence="self_claim",
            )
            logger.info(f"Force memory for user {user_id} in group {group_id}: {claim}")
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