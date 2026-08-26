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
        self._active_chat_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动主动聊天循环（若配置允许）"""
        if not self.config.ONLY_REPLY_WHEN_AT:
            self._active_chat_task = asyncio.create_task(self._active_chat_loop())
            logger.info("Active chat loop started")

    async def stop(self):
        if self._active_chat_task:
            self._active_chat_task.cancel()
            try:
                await self._active_chat_task
            except asyncio.CancelledError:
                pass

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

        # 提取文本和@
        clean_text, is_mentioned = self.file_parser.extract_mention_and_text(message_array, self.config.BOT_QQ)
        full_text = clean_text

        # 识别图片/表情包（OneBot11 image 段，NapCat 消息里带 url）
        image_descriptions = []
        for seg in message_array:
            if seg.get("type") == "image":
                seg_data = seg.get("data") or {}
                url = seg_data.get("url", "")
                if url:
                    desc = await self.ai_client.describe_image(url)
                    if desc:
                        image_descriptions.append(desc)
                    else:
                        logger.warning(f"Vision describe failed for image url: {url[:80]}")
        if image_descriptions:
            full_text += f"\n[用户发送了一张图片，内容如下：]\n{'; '.join(image_descriptions)}\n[图片内容结束]"
            logger.debug(f"Image descriptions: {image_descriptions}")

        # 检查是否被回复或提及
        is_reply_to_bot = False
        has_reply_to_other = False
        has_at_others = False
        for seg in message_array:
            if seg.get("type") == "reply":
                reply_data = seg.get("data", {})
                replied_qq = str(reply_data.get("qq", ""))
                if replied_qq == str(self.config.BOT_QQ):
                    is_reply_to_bot = True
                else:
                    has_reply_to_other = True
            elif seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq != str(self.config.BOT_QQ):
                    has_at_others = True

        # 解析文件、转发、卡片
        forward_text, has_forward = await self.file_parser.extract_forward_messages(message_array)
        if has_forward and forward_text:
            full_text += f"\n[用户转发了多条消息，内容如下：]\n{forward_text}\n[转发内容结束]"

        card_text, has_card = self.file_parser.extract_json_card_content(message_array)
        if has_card and card_text:
            full_text += f"\n[用户分享了一个卡片，内容如下：]\n{card_text}\n[卡片内容结束]"

        # 处理待解析文件
        pending_key = f"{user_id}_{group_id}"
        if pending_key in self.global_state.pending_files:
            file_info = self.global_state.pending_files.pop(pending_key)
            file_id = file_info.get("file_id")
            file_name = file_info.get("file_name", "未命名文件")
            file_size = file_info.get("file_size", 0)

            if file_id and file_size <= 1 * 1024 * 1024:
                file_content, success = await self.file_parser.fetch_and_parse_file(file_id, file_name)
                if success and file_content:
                    full_text += f"\n[用户上传了一个文件，内容如下：]\n{file_content}\n[文件内容结束]"
                    logger.debug(f"File parsed: {file_name} ({len(file_content)} chars)")
                else:
                    logger.warning(f"Failed to parse file: {file_name}")
            elif file_size > 1 * 1024 * 1024:
                logger.warning(f"File too large, skipped: {file_name} ({file_size} bytes)")
            else:
                logger.debug(f"No file_id for pending file: {file_name}")

        # 存档消息
        self._archive_message(group_id, user_id, full_text, raw_time)

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

        # ---------- 调用 AI ----------
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        user_prompt = full_text if full_text.strip() else (
            f"用户刚刚发了一张图片，图片内容：{'; '.join(image_descriptions)}" if image_descriptions else "用户刚刚@了你，但没有说话。"
        )
        reply, memory_update = await self.ai_client.chat(
            user_message=user_prompt,
            context=context_text,
            user_id=user_id,
            group_id=group_id,
            is_mentioned=is_mentioned or is_reply_to_bot,
        )

        # 处理记忆更新
        if memory_update and user_id:
            target_uid, mem_content = self.policy_engine.parse_memory_update(memory_update, user_id)
            if mem_content:
                await self.memory_manager.append_memory_text(target_uid, group_id, mem_content)
                logger.info(f"Memory updated for user {target_uid} in group {group_id}: {mem_content}")

        # 静默记忆模式不回复
        if silent_memory_only:
            logger.debug("Silent memory mode, no reply")
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

    # ---------- 存档 ----------
    def _archive_message(self, group_id: int, user_id: int, text: str, raw_time: int):
        if not text:
            return
        try:
            import os
            from datetime import datetime
            base = self.config.ARCHIVE_BASE_DIR
            if not os.path.exists(base):
                os.makedirs(base, exist_ok=True)
            group_dir = os.path.join(base, str(group_id))
            if not os.path.exists(group_dir):
                os.makedirs(group_dir, exist_ok=True)
            filename = os.path.join(group_dir, f"{datetime.now().strftime('%Y-%m-%d')}.txt")
            time_str = datetime.fromtimestamp(raw_time).strftime("%H:%M:%S")
            line = f"[{time_str}] 用户{user_id}：{text}\n"
            with open(filename, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            logger.error(f"Archive error: {e}")

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