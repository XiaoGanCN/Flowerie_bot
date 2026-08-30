import asyncio
import random
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - 仅类型注解
    from src.plugins.manager import PluginManager

from src.config import Settings
from src.core.ai_gateway import AiGateway
from src.core.budget_manager import BudgetManager
from src.core.command_handler import CommandHandler
from src.core.message_assembler import MessageAssembler
from src.core.policy_engine import PolicyEngine
from src.core.sanitizer import validate_memory_content
from src.sdk.onebot.transformer import to_bot_event
from src.models import GroupMessage
from src.services.ai_client import AIClient
from src.services.file_parser import FileParser
from src.services.mcp_tool_manager import McpToolManager
from src.services.meme_knowledge_manager import MemeKnowledgeManager
from src.services.meme_summary import MemeSummaryService
from src.services.memory_manager import MemoryManager
from src.services.persona_manager import PersonaManager
from src.services.prompt_manager import PromptManager
from src.services.sender import Sender
from src.services.sticker_manager import StickerManager
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry
from src.utils.task_manager import BackgroundTaskManager

logger = get_logger(__name__)

# Metrics（进程内 registry 单例，不引入外部监控设施）
_M_RECEIVED = registry.counter("received_messages_total", "收到的群消息总数")
_M_PROCESSED = registry.counter("processed_messages_total", "通过去重、进入处理流程的消息总数")
_M_REJECTED = registry.counter("rejected_messages_total", "被拒绝的消息总数（按原因）", ["reason"])


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
        task_manager: Optional[BackgroundTaskManager] = None,
        prompt_manager: Optional["PromptManager"] = None,
        sticker_manager: Optional["StickerManager"] = None,
        tool_manager: Optional["McpToolManager"] = None,
        persona_manager: Optional["PersonaManager"] = None,
        meme_manager: Optional["MemeKnowledgeManager"] = None,
        meme_summary: Optional["MemeSummaryService"] = None,
        budget: Optional["BudgetManager"] = None,
        plugin_manager: Optional["PluginManager"] = None,
    ):
        self.config = config
        self.ai_client = ai_client
        self.memory_manager = memory_manager
        self.file_parser = file_parser
        self.sender = sender
        self.policy_engine = policy_engine
        self.global_state = self.policy_engine.global_state
        # 插件系统（Plugin System v1）：None 时不投递事件（不影响现有行为）
        self.plugin_manager = plugin_manager
        # 自定义 Prompt（全局/群聊）：None 时跳过（不影响现有行为）
        self.prompt_manager = prompt_manager
        # 表情包（Sticker）：None 时跳过（不影响现有行为）
        self.sticker_manager = sticker_manager
        # MCP 工具：None 或未启用时走纯聊天路径（不影响现有行为）
        self.tool_manager = tool_manager
        # 人格系统：None 时回退内置默认人格（不影响现有行为）
        self.persona_manager = persona_manager
        # 群聊梗知识：None 时不记录/不注入（不影响现有行为）
        self.meme_manager = meme_manager
        # 每日梗总结任务：None 或未启用时不注册
        self.meme_summary = meme_summary
        # 消息组装（文本/识图/转发/卡片/文件/存档）→ MessageAssembler
        self.assembler = MessageAssembler(config, ai_client, file_parser, self.global_state)
        # 指令处理 → CommandHandler
        self.commands = CommandHandler(config, sender, memory_manager, prompt_manager)
        # AI 预算/限速 → BudgetManager（外部可注入共享实例，供每日总结等复用同一计数）
        self.budget = budget or BudgetManager(config, self.global_state, sender)
        # 后台任务统一管理（TaskManager：注册/跟踪/异常记录/优雅关闭）
        self.task_manager = task_manager or BackgroundTaskManager()
        # AI 准入层（熔断/预算/人格/知识/重试）→ AiGateway（防上帝类）。
        # 可变依赖以 provider 传入：gateway 动态读取 router 当前属性
        # （测试常直接替换 router.ai_client / router.tool_manager，快照会失效）
        self.ai_gateway = AiGateway(
            config, lambda: self.ai_client, lambda: self.budget,
            prompt_manager=lambda: self.prompt_manager,
            tool_manager=lambda: self.tool_manager,
            persona_manager=lambda: self.persona_manager,
            meme_manager=lambda: self.meme_manager,
        )
        # 兼容属性：熔断器/群级熔断容器由 AiGateway 持有（旧调用路径）
        self.provider_breaker = self.ai_gateway.provider_breaker
        self.group_breakers = self.ai_gateway.group_breakers
        # 并发上限：同时处理的消息数（WS 层用它限制 AI/识图并发，防止突发消息打爆 API）
        # 惰性创建：Python 3.9 的 asyncio.Semaphore 构造时即绑定事件循环，
        # 延迟到 async 上下文中首次使用（保证有 running loop）更健壮。
        self._process_semaphore: Optional[asyncio.Semaphore] = None

    @property
    def process_semaphore(self) -> asyncio.Semaphore:
        if self._process_semaphore is None:
            self._process_semaphore = asyncio.Semaphore(max(1, self.config.MAX_CONCURRENT_AI))
        return self._process_semaphore

    async def start(self):
        """启动主动聊天循环（若配置允许）与上下文备份循环（经 TaskManager 注册）"""
        if not self.config.ONLY_REPLY_WHEN_AT:
            self.task_manager.register("active_chat", self._active_chat_loop())
            logger.info("Active chat loop started")
        # 周期备份上下文（意外去世后重启可恢复最近 50 条）
        self.task_manager.register("context_backup", self._context_backup_loop())
        logger.info("Context backup loop started (every %ss)", self.config.CONTEXT_BACKUP_INTERVAL)
        # 表情包 Vision 索引（一次性后台任务；失败不影响启动，单文件失败跳过）
        if self.sticker_manager and self.sticker_manager.is_enabled():
            self.task_manager.register("sticker_index", self.sticker_manager.scan_and_index())
        # MCP 工具列表同步（失败不阻塞启动；工具列表为空则不会注入 tools）
        if self.tool_manager is not None and self.tool_manager.is_enabled():
            self.task_manager.register("mcp_tools_sync", self.tool_manager.sync_tools())
        # 每日梗总结（MEME_LEARNING_ENABLED=true 时注册；批量、有界、可降级）
        if self.meme_summary is not None and self._meme_learning_enabled():
            self.task_manager.register("meme_summary", self.meme_summary.run_loop())
            logger.info("Meme summary loop started (every %sh)",
                        getattr(self.config, "MEME_SUMMARY_INTERVAL_HOURS", 24))

    def _meme_learning_enabled(self) -> bool:
        return bool(getattr(self.config, "MEME_LEARNING_ENABLED", False))

    async def stop(self):
        # 统一取消并等待所有后台任务（TaskManager 负责异常记录与超时强杀）
        await self.task_manager.shutdown(timeout=5.0)
        # 停前最后保存一次上下文
        await self.policy_engine.save_context_backup()

    async def _context_backup_loop(self):
        """周期性保存每群最近 50 条上下文 + 清理陈旧用户状态。"""
        interval = max(10, self.config.CONTEXT_BACKUP_INTERVAL)
        while True:
            try:
                await asyncio.sleep(interval)
                await self.policy_engine.save_context_backup()
                # 内存治理：清理过期 TTL 状态 + 超过 24h 无活动的群状态
                self.policy_engine.prune_stale_state()
                self.policy_engine.prune_stale_groups()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Context backup loop error: %s", e)

    async def process_event(self, data: Dict[str, Any]) -> None:
        post_type = data.get("post_type")
        # 插件事件投递（受控运行时；插件异常被隔离，不阻塞主消息流程）
        if self.plugin_manager is not None:
            try:
                await self.plugin_manager.dispatch_event(self._plugin_event_type(data), self._plugin_payload(data))
            except Exception as e:  # noqa: BLE001 - 插件系统异常绝不影响主流程
                logger.error("plugin_dispatch_error reason=%s", e, extra={"event": "plugin_error"})
        if post_type == "message":
            await self._handle_message(data)
        elif post_type == "notice":
            notice_type = data.get("notice_type")
            if notice_type == "group_upload":
                self._handle_group_upload(data)
            elif notice_type == "notify" and data.get("sub_type") == "poke":
                await self._handle_poke(data)

    @staticmethod
    def _plugin_event_type(data: Dict[str, Any]) -> str:
        post_type = str(data.get("post_type") or "unknown")
        if post_type == "message" and data.get("message_type") == "group":
            return "group_message"
        return post_type

    @staticmethod
    def _plugin_payload(data: Dict[str, Any]) -> Dict[str, Any]:
        """投递给插件的事件负载（只含必要字段，不透传原始段数组以外的敏感信息）。"""
        text = ""
        message_array = data.get("message", [])
        if isinstance(message_array, str):
            text = message_array
        elif isinstance(message_array, list):
            text = "".join(
                str(seg.get("data", {}).get("text", ""))
                for seg in message_array if isinstance(seg, dict) and seg.get("type") == "text"
            )
        payload = {
            "post_type": str(data.get("post_type") or "unknown"),
            "message_type": data.get("message_type") or "",
            "group_id": data.get("group_id"),
            "user_id": data.get("user_id"),
            "message_id": data.get("message_id"),
            "time": data.get("time"),
            "text": text[:2000],
        }
        notice_type = data.get("notice_type")
        if notice_type:
            payload["notice_type"] = notice_type
            payload["sub_type"] = data.get("sub_type", "")
        return payload

    # ---------- 消息处理 ----------
    def _in_whitelist(self, group_id: int) -> bool:
        """群白名单：空=放行所有群；设置后只有白名单群能触发任何行为（消息/戳戳/文件）。"""
        return not self.config.ALLOWED_GROUP_IDS or group_id in self.config.ALLOWED_GROUP_IDS

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        if data.get("message_type") != "group":
            return
        group_id = data.get("group_id")
        if not group_id:
            return

        logger.info(
            "message_received group=%s user=%s msg_id=%s",
            group_id, data.get("user_id"), data.get("message_id"),
            extra={"event": "message_received", "group_id": group_id},
        )
        _M_RECEIVED.inc()

        if not self._in_whitelist(group_id):
            logger.debug("Group %s not in whitelist, ignoring", group_id)
            _M_REJECTED.inc({"reason": "whitelist"})
            logger.info("message_rejected group=%s reason=whitelist", group_id, extra={"event": "message_rejected"})
            return

        message_array = data.get("message", [])
        # OneBot11 兼容：纯文本消息可能以字符串形式下发（而非段数组）
        if isinstance(message_array, str):
            message_array = [{"type": "text", "data": {"text": message_array}}]
        elif not isinstance(message_array, list):
            logger.debug("Unsupported message format: %s", type(message_array).__name__)
            message_array = []

        raw_time = data.get("time", int(time.time()))
        user_id = data.get("user_id")
        msg_id = data.get("message_id")
        if not user_id:
            _M_REJECTED.inc({"reason": "no_user"})
            return

        # 消息去重（在指令处理之前：NapCat 重投旧消息时，指令也不会重复执行）
        state = self.policy_engine.get_group_state(group_id)
        if msg_id in state.processed_msg_ids:
            logger.debug("Message %s already processed", msg_id)
            _M_REJECTED.inc({"reason": "duplicate"})
            return
        state.processed_msg_ids.append(msg_id)
        _M_PROCESSED.inc()

        # 消息组装（文本/识图/转发/卡片/文件/存档）交给 MessageAssembler
        full_text, image_descriptions, is_reply_to_bot, has_reply_to_other, has_at_others = await self.assembler.assemble(
            message_array, user_id, group_id, raw_time,
        )
        # 提取纯文本与是否@机器人（决策需要）
        clean_text, is_mentioned = self.file_parser.extract_mention_and_text(message_array, self.config.BOT_QQ)

        # 用户命令（P2-9 记忆管理：/help /memory /forget /forget_me；管理员 /memory_clear /memory_dump）
        if clean_text.strip().startswith("/") and await self.commands.handle(clean_text.strip(), user_id, group_id):
            return

        # 复读检测
        if full_text:
            if self.policy_engine.check_and_record_repeat(full_text, group_id):
                await self.sender.send_group_message(group_id, full_text)
                self.policy_engine.record_bot_reply(group_id)
                self.policy_engine.add_context(group_id, 0, full_text, is_bot=True)
                return

        # 引战检测（统一准入：走预算闸门）
        if self.config.TOXIC_GROUP_IDS and group_id in self.config.TOXIC_GROUP_IDS:
            if await self.guarded_is_toxic(group_id, user_id, full_text):
                now = time.time()
                last_warn = self.global_state.last_toxic_warning.get(group_id, 0)
                if now - last_warn >= self.config.TOXIC_WARNING_COOLDOWN:
                    await self.sender.send_group_message(group_id, "居然有人在引战喔（坏笑，马上发消息给群主咪）")
                    self.global_state.last_toxic_warning.set(group_id, now)
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

        # 梗知识消息缓冲（每日总结的数据源；MEME_LEARNING_ENABLED 时才有总结任务，
        # 但缓冲记录始终启用有界保护，避免开关切换瞬间丢数据）
        if self.meme_manager is not None and full_text and full_text.strip():
            self.meme_manager.record_message(group_id, user_id, full_text, raw_time)

        # ---------- 强制记忆（静默，先于回复决策） ----------
        # 用户明确表达个人偏好/特征但未被@：只记记忆、不回复、不烧 AI 调用。
        # 修复：此前静默记忆被"接话概率"随机闸门挡住，经常漏记；
        # 现在只要命中个人偏好句式就确定记录（记忆禁用群除外，降级为普通消息继续走回复流程）。
        force_memory = self.policy_engine.should_force_memory(clean_text, full_text, has_at_others)
        silent_memory_only = force_memory and not is_mentioned and not is_reply_to_bot
        if silent_memory_only and not self._memory_disabled(group_id):
            claim = validate_memory_content(clean_text[:100])
            if claim is None:
                logger.warning("memory_inject_rejected user=%s group=%s len=%d", user_id, group_id, len(clean_text), extra={"event": "memory_inject_rejected"})
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

        # ---------- 决定是否回复 ----------
        should_reply = self._should_reply(msg)
        if not should_reply:
            return

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
        # 注意：这里不再单独预检查预算——guarded_chat 是唯一准入点，
        # 避免同一条消息被扣两次预算 / 被自己的用户限速二次拦截
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        user_prompt = full_text if full_text.strip() else (
            f"用户刚刚发了一张图片，图片内容：{'; '.join(image_descriptions)}" if image_descriptions else "用户刚刚@了你，但没有说话。"
        )
        # 表情包上下文：可用表情包的"文字描述"（不传图片本体，防 token 与隐私浪费）
        if self.sticker_manager and self.sticker_manager.is_enabled():
            sticker_ctx = self.sticker_manager.build_sticker_context()
            if sticker_ctx:
                user_prompt = f"{user_prompt}\n\n{sticker_ctx}"
        # 输入截断已统一收敛到 AIClient.chat_once（覆盖主动聊天等所有路径）
        logger.info("policy_pass group=%s user=%s", group_id, user_id, extra={"event": "policy_pass"})
        reply, memory_update, denied = await self.guarded_chat(
            group_id,
            user_id,
            user_message=user_prompt,
            context=context_text,
            is_mentioned=is_mentioned or is_reply_to_bot,
        )
        if denied:
            # 预算/限速拦截：静默跳过（不发送"喵？"兜底）
            logger.info("budget_rejected group=%s user=%s", group_id, user_id, extra={"event": "budget_rejected"})
            _M_REJECTED.inc({"reason": "budget"})
            return

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
                    logger.info("memory_updated user=%s group=%s len=%d", target_uid, group_id, len(mem_content or ""), extra={"event": "memory_updated"})

        # 兜底：guarded_chat 已内部重试过（每次重试过预算），仍空则给个兜底回复
        if is_mentioned and (not reply or not reply.strip()):
            reply = "喵？"

        if reply:
            if self.policy_engine.is_duplicate_reply(group_id, reply):
                logger.debug("Duplicate reply, skip")
                return

            # 表情包：解析模型回复中的 [STICKER:filename] 标记并发送
            sticker_path = None
            if self.sticker_manager and self.sticker_manager.is_enabled():
                sticker_path = self.sticker_manager.extract_sticker(reply)
                if sticker_path:
                    reply = self.sticker_manager.strip_sticker_marker(reply)
            if sticker_path:
                if not self.sticker_manager.can_send(group_id):
                    logger.debug("Sticker cooldown, skip image (text only)")
                    sticker_path = None
                else:
                    self.sticker_manager.mark_sent(group_id)
                    success = await self.sender.send_group_message_with_image(
                        group_id, reply or None, sticker_path)
                    if success:
                        self.policy_engine.record_bot_reply(group_id)
                        self.policy_engine.add_context(group_id, 0, reply or "[表情包]", is_bot=True)
                        self.policy_engine.add_recent_reply(group_id, reply or "[表情包]")
                        logger.info("Sticker sent: %s", sticker_path, extra={"event": "sticker_selected"})
                    return

            success = await self.sender.send_group_message(group_id, reply)
            if success:
                self.policy_engine.record_bot_reply(group_id)
                self.policy_engine.add_context(group_id, 0, reply, is_bot=True)
                self.policy_engine.add_recent_reply(group_id, reply)
                logger.info("reply_sent group=%s len=%d", group_id, len(reply or ""), extra={"event": "reply_sent"})
            else:
                logger.error("Reply send failed")

    # ---------- 统一 AI 准入层（委托 AiGateway；防上帝类） ----------
    async def guarded_chat(self, group_id: int, user_id: int, **kwargs) -> Tuple[Optional[str], Optional[str], bool]:
        """统一 AI 对话入口（委托 AiGateway：熔断/预算/人格/知识/重试）。"""
        return await self.ai_gateway.guarded_chat(group_id, user_id, **kwargs)

    async def _ai_allowed(self, group_id: int, user_id: int, user_interval: bool = True) -> bool:
        """预算闸门（委托 AiGateway）。"""
        return await self.ai_gateway._ai_allowed(group_id, user_id, user_interval=user_interval)

    async def guarded_is_toxic(self, group_id: int, user_id: int, text: str) -> bool:
        """引战检测准入（委托 AiGateway）。"""
        return await self.ai_gateway.guarded_is_toxic(group_id, user_id, text)

    def _get_group_breaker(self, group_id: int) -> CircuitBreaker:
        """群级熔断器（委托 AiGateway；兼容旧调用）。"""
        return self.ai_gateway._get_group_breaker(group_id)

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
        group_id = data.get("group_id")
        if not self._in_whitelist(group_id):
            logger.debug(f"Upload from non-whitelisted group {group_id}, ignoring")
            return
        user_id = data.get("user_id")
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
            # 待配对文件缓存治理：超过 10 分钟没等到消息的条目丢弃 + 总数上限
            # （防"上传了但一直没发消息"导致 pending_files 无限增长）
            self._prune_pending_files()

    def _prune_pending_files(self) -> None:
        now = time.time()
        stale_keys = [
            k for k, v in self.global_state.pending_files.items()
            if now - float(v.get("time", 0) or 0) > 600
        ]
        for k in stale_keys:
            self.global_state.pending_files.pop(k, None)
        # 总数上限：超限丢最旧的（dict 保持插入序）
        if len(self.global_state.pending_files) > 100:
            for k in list(self.global_state.pending_files)[:50]:
                self.global_state.pending_files.pop(k, None)

    # ---------- 戳戳 ----------
    # 每用户戳戳冷却（秒）：防戳戳刷屏刷爆消息发送
    POKE_USER_COOLDOWN = 10

    async def _handle_poke(self, data: Dict[str, Any]):
        target_id = data.get("target_id") or data.get("target") or data.get("user_id")
        if target_id != self.config.BOT_QQ:
            return
        if not self.config.POKE_REPLY_ENABLED:
            return
        group_id = data.get("group_id")
        if not self._in_whitelist(group_id):
            logger.debug(f"Poke from non-whitelisted group {group_id}, ignoring")
            return
        user_id = data.get("user_id")
        # 每用户戳戳冷却：同一人连续猛戳只回一次
        now = time.time()
        if user_id:
            last = self.global_state.poke_last_time.get(user_id, 0.0)
            if now - last < self.POKE_USER_COOLDOWN:
                logger.debug(f"User {user_id} poke cooldown, skip")
                return
            self.global_state.poke_last_time.set(user_id, now)
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
            # 轮询间隔（原硬编码 random.randint(5,10)，现走配置，默认值不变）
            interval_min = max(1, int(getattr(self.config, "ACTIVE_CHAT_INTERVAL_MIN_SECONDS", 5) or 5))
            interval_max = max(interval_min, int(getattr(self.config, "ACTIVE_CHAT_INTERVAL_MAX_SECONDS", 10) or 10))
            await asyncio.sleep(random.randint(interval_min, interval_max))
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
        # 主动聊天也吃并发额度（与 WS 消息处理共用 process_semaphore），
        # 防止主动聊天与突发群消息叠加打爆 API
        async with self.process_semaphore:
            await self._do_active_chat_inner(group_id)

    async def _do_active_chat_inner(self, group_id: int):
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        if not context_text:
            logger.debug(f"No context for group {group_id}, skip active")
            return
        # 主动聊天提示词只保留通用框架；人格名/人格发言规则由人格系统动态注入
        # （原先硬编码「花璃」已移除，切换人格后主动聊天同样跟随当前 Persona）
        persona_name = self.persona_manager.resolve_persona_name(group_id) if self.persona_manager else "花璃"
        for _attempt in range(3):
            prompt = (
                f"你现在就是QQ群里的{persona_name}，正在自然地跟群友聊天。\n"
                "没有人在叫你。\n"
                "如果最近大家讨论一个话题，自然接一句，像平时一样简短而自然地说句话。\n"
                "如果群冷了，可以偶尔冒一句，简短就好。\n"
                "不要解释。\n"
                "不要说自己是AI。\n"
                "不要刻意活跃气氛。\n"
                "一句话即可，尽量短，自然。"
            )
            # 主动聊天同样过预算闸门（user_id=0 表示机器人主动发起；受群级/全局预算约束）
            reply, _active_mem, denied = await self.guarded_chat(
                group_id,
                0,
                user_message=prompt,
                context=context_text,
                is_mentioned=False,
            )
            if denied:  # 预算拦截：停止主动聊天
                logger.debug(f"Active chat blocked by budget for group {group_id}")
                break
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
                logger.info("active_chat_sent group=%s len=%d", group_id, len(reply or ""), extra={"event": "active_chat_sent"})
                break
            else:
                break
