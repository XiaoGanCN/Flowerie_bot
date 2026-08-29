import asyncio
import random
import time
from typing import Any, Dict, Optional, Tuple

from src.config import Settings
from src.core.budget_manager import BudgetManager
from src.core.command_handler import CommandHandler
from src.core.message_assembler import MessageAssembler
from src.core.policy_engine import PolicyEngine
from src.core.sanitizer import validate_memory_content
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
from src.utils.expiring_map import ExpiringMap
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry
from src.utils.task_manager import BackgroundTaskManager

logger = get_logger(__name__)

# Metrics（进程内 registry 单例，不引入外部监控设施）
_M_RECEIVED = registry.counter("received_messages_total", "收到的群消息总数")
_M_PROCESSED = registry.counter("processed_messages_total", "通过去重、进入处理流程的消息总数")
_M_REJECTED = registry.counter("rejected_messages_total", "被拒绝的消息总数（按原因）", ["reason"])
_M_AI_REQ = registry.counter("ai_requests_total", "AI 逻辑请求总数（用户发起的逻辑操作）")
_M_AI_ATTEMPTS = registry.counter("ai_attempts_total", "实际发往 Provider 的 HTTP 尝试总数（含重试）")
_M_AI_RETRY = registry.counter("ai_retry_total", "AI 请求重试次数")
_M_AI_OK = registry.counter("ai_success_total", "AI 请求成功数")
_M_AI_FAIL = registry.counter("ai_failure_total", "AI 请求失败数")
_M_AI_LATENCY = registry.histogram("ai_latency_seconds", "AI 请求耗时（秒）")
_M_CIRCUIT_REJECT = registry.counter("ai_circuit_rejections_total", "熔断拒绝的 AI 请求数（按层级）", ["level"])


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
    ):
        self.config = config
        self.ai_client = ai_client
        self.memory_manager = memory_manager
        self.file_parser = file_parser
        self.sender = sender
        self.policy_engine = policy_engine
        self.global_state = self.policy_engine.global_state
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
        # AI 预算/限速 → BudgetManager
        self.budget = BudgetManager(config, self.global_state, sender)
        # 后台任务统一管理（TaskManager：注册/跟踪/异常记录/优雅关闭）
        self.task_manager = task_manager or BackgroundTaskManager()
        # ---- Circuit Breaker（双层：provider 级全局 + 群级有界）----
        # Provider 级：全局唯一，计可重试瞬时失败（超时/网络/429/5xx）
        self.provider_breaker = CircuitBreaker(
            name="provider",
            failure_threshold=max(1, int(getattr(config, "AI_CIRCUIT_BREAKER_FAILURES", 10))),
            cooldown_seconds=max(5, int(getattr(config, "AI_CIRCUIT_BREAKER_PAUSE_SECONDS", 60))),
        )
        # 群级：ExpiringMap 容器（TTL + 容量上限），防单群故障拖垮其他群，
        # 且不会因历史群无限增长（TTL 7 天 + max 1000 淘汰最旧）
        self.group_breakers: ExpiringMap = ExpiringMap(
            ttl_seconds=max(60, int(getattr(config, "GROUP_CIRCUIT_BREAKER_TTL_SECONDS", 604800))),
            max_size=max(10, int(getattr(config, "GROUP_CIRCUIT_BREAKER_MAX_GROUPS", 1000))),
        )
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
        if post_type == "message":
            await self._handle_message(data)
        elif post_type == "notice":
            notice_type = data.get("notice_type")
            if notice_type == "group_upload":
                self._handle_group_upload(data)
            elif notice_type == "notify" and data.get("sub_type") == "poke":
                await self._handle_poke(data)

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

    # ---------- 统一 AI 准入层（所有消耗 AI 的路径都必须走这里，防预算绕过） ----------
    async def _ai_allowed(self, group_id: int, user_id: int, user_interval: bool = True) -> bool:
        """预算闸门：返回是否允许调用 AI（不允许时按需提示并记录）。

        user_interval=False 时跳过用户聊天限速（供引战检测等旁路调用）。
        """
        allowed, reason = self.budget.check(group_id, user_id, user_interval=user_interval)
        if not allowed:
            if reason in ("global", "group") and self.config.BUDGET_EXHAUSTED_NOTICE:
                await self.budget.notify_exhausted(group_id)
            logger.warning(f"AI 预算/限速拦截: group={group_id} user={user_id} reason={reason}")
        return allowed

    async def guarded_chat(self, group_id: int, user_id: int, **kwargs) -> Tuple[Optional[str], Optional[str], bool]:
        """统一 AI 对话入口（logical request 层）。

        调用顺序（与 Retry/Circuit/Budget 协作）：
          1. Circuit admission（逻辑请求层，只检查一次，不随 retry 重复）：
             provider 级熔断 → 群级熔断；被拒不消耗预算
          2. attempts 循环（每个 attempt 单独过预算闸门——retry 永不绕过额度）
          3. 结果回写 Circuit（成功/可重试失败/4xx 永久错误分类计数）

        返回 (reply, memory_update, denied)。denied=True 表示预算/熔断拦截。
        """
        max_retries = max(0, int(getattr(self.config, "AI_MAX_RETRIES", 3)))
        attempts = max_retries + 1  # 首次 + 重试
        started = time.monotonic()
        model = getattr(self.config, "DEEPSEEK_MODEL", "-")

        # ---- 1) Circuit admission（逻辑请求层一次）----
        if not self.provider_breaker.allow():
            _M_CIRCUIT_REJECT.inc({"level": "provider"})
            logger.warning(
                "ai_circuit_rejected level=provider group=%s user=%s state=%s",
                group_id, user_id, self.provider_breaker.state,
                extra={"event": "ai_circuit_rejected", "level": "provider"},
            )
            return None, None, True
        group_breaker = self._get_group_breaker(group_id)
        if not group_breaker.allow():
            _M_CIRCUIT_REJECT.inc({"level": "group"})
            logger.warning(
                "ai_circuit_rejected level=group group=%s user=%s state=%s",
                group_id, user_id, group_breaker.state,
                extra={"event": "ai_circuit_rejected", "level": "group"},
            )
            return None, None, True

        logger.info(
            "ai_request_started group=%s user=%s model=%s",
            group_id, user_id, model,
            extra={"event": "ai_request_started", "model": model},
        )
        _M_AI_REQ.inc()  # logical request 计数
        # 人格解析（动态决定，绝不写入记忆/上下文；Group > Global > 内置默认）
        if self.persona_manager is not None and group_id:
            persona = self.persona_manager.resolve_persona(group_id)
            if persona:
                kwargs = {**kwargs, "persona_text": self.persona_manager.compose_system_prompt(persona)}
        # 群聊知识检索注入（只注入当前消息命中的本群梗，作为不可信上下文知识）
        if self.meme_manager is not None and group_id:
            user_message = kwargs.get("user_message", "")
            meme_ctx = self.meme_manager.build_context_block(group_id, user_message)
            if meme_ctx:
                kwargs = {**kwargs, "meme_context": meme_ctx}
        reply, memory_update = None, None
        retryable_failure = False  # 是否发生了可重试的瞬时失败（用于熔断计数）
        # P2-1：MCP 工具额度是一次 logical request 的硬上限——在重试循环前创建，
        # 跨 attempt 复用；retry 不会重新获得新额度（tool_quota.used 持续累加）。
        mcp_max_calls = max(0, int(getattr(self.config, "MCP_MAX_TOOL_CALLS", 5)))
        tool_quota: Optional[dict] = None
        for attempt in range(attempts):
            # 用户聊天限速只在首次尝试检查（重试是同一逻辑调用的延续，
            # 若每次都查，会被自己刚更新的 user_ai_last_call 拦掉）
            if not await self._ai_allowed(group_id, user_id, user_interval=(attempt == 0)):
                logger.info("budget_rejected group=%s user=%s", group_id, user_id, extra={"event": "budget_rejected"})
                _M_REJECTED.inc({"reason": "budget"})
                return None, None, True
            _M_AI_ATTEMPTS.inc()  # 实际 HTTP attempt 计数
            if self.prompt_manager is not None and group_id:
                kwargs = {**kwargs, "custom_prompt": self.prompt_manager.get_effective_prompt(group_id)}
            # MCP 工具：仅当启用、存在 allowlist 工具且额度 > 0 时注入
            # （模型自主判断是否需要工具；MCP_MAX_TOOL_CALLS=0 视为禁用工具）
            if self.tool_manager is not None and self.tool_manager.is_enabled() and mcp_max_calls > 0:
                tool_payload = self.tool_manager.build_tools_payload()
                if tool_payload:
                    if tool_quota is None:
                        tool_quota = {"max": mcp_max_calls, "used": 0}
                    kwargs = {
                        **kwargs,
                        "tools": tool_payload,
                        "tool_caller": self.tool_manager.call_tool,
                        "max_tool_calls": mcp_max_calls,
                        "tool_quota": tool_quota,
                    }
            reply, memory_update = await self.ai_client.chat_once(**kwargs)
            if reply and reply.strip():
                latency = time.monotonic() - started
                _M_AI_OK.inc()
                _M_AI_LATENCY.observe(latency)
                # 成功：回写 Circuit（CLOSED 清零 / HALF_OPEN probe 成功 → CLOSED）
                self.provider_breaker.record_success()
                group_breaker.record_success()
                logger.info(
                    "ai_request_finished group=%s user=%s latency_ms=%.0f attempts=%d",
                    group_id, user_id, latency * 1000, attempt + 1,
                    extra={"event": "ai_request_finished", "latency_ms": round(latency * 1000), "attempts": attempt + 1},
                )
                return reply, memory_update, False
            # 4xx 业务错误（chat_once 标记不可重试）：永久性错误，不计入任何熔断
            if not getattr(self.ai_client, "_retryable", True):
                logger.warning(
                    "ai_request_failed group=%s user=%s attempt=%d/%d retryable=false",
                    group_id, user_id, attempt + 1, attempts,
                    extra={"event": "ai_request_failed", "attempt": attempt + 1, "max_attempts": attempts, "retryable": False},
                )
                break
            retryable_failure = True  # 超时/网络/429/5xx/空回复等可重试失败
            if attempt + 1 < attempts:
                _M_AI_RETRY.inc()
            # 指数退避：429（chat_once 置 _api_backoff=8）→ 8/16/30s 封顶；
            # 其他失败 → 1/2/4s。加少量抖动避免惊群。
            base = getattr(self.ai_client, "_api_backoff", 0) or 1.0
            backoff = min(base * (2 ** attempt) + random.uniform(0, 0.5), 30)
            logger.warning(
                "ai_request_failed group=%s user=%s attempt=%d/%d retry_in=%.1fs",
                group_id, user_id, attempt + 1, attempts, backoff,
                extra={"event": "ai_request_failed", "attempt": attempt + 1, "max_attempts": attempts},
            )
            await asyncio.sleep(backoff)
        _M_AI_FAIL.inc()
        _M_AI_LATENCY.observe(time.monotonic() - started)
        # ---- 3) 结果回写 Circuit ----
        # 只统计可重试的瞬时失败（超时/网络/5xx/空回复）；
        # 4xx 永久错误、预算不足、用户输入问题都不算 Provider/群级故障
        if retryable_failure:
            self.provider_breaker.record_failure()
            group_breaker.record_failure()
            if self.provider_breaker.state == "OPEN":
                logger.warning(
                    "ai_circuit_opened level=provider failures=%d pause=%ss",
                    self.provider_breaker.failure_threshold,
                    self.provider_breaker.cooldown_seconds,
                    extra={"event": "ai_circuit_opened", "level": "provider"},
                )
            if group_breaker.state == "OPEN":
                logger.warning(
                    "ai_circuit_opened level=group group=%s failures=%d pause=%ss",
                    group_id, group_breaker.failure_threshold, group_breaker.cooldown_seconds,
                    extra={"event": "ai_circuit_opened", "level": "group"},
                )
        return reply, memory_update, False

    def _get_group_breaker(self, group_id: int) -> CircuitBreaker:
        """获取群级熔断器（惰性创建，容器有 TTL 与容量上限，不会无限增长）。"""
        breaker = self.group_breakers.get(group_id)
        if breaker is None:
            breaker = CircuitBreaker(
                name=f"group:{group_id}",
                failure_threshold=max(1, int(getattr(self.config, "GROUP_CIRCUIT_BREAKER_FAILURES", 5))),
                cooldown_seconds=max(5, int(getattr(self.config, "GROUP_CIRCUIT_BREAKER_PAUSE_SECONDS", 30))),
            )
            self.group_breakers.set(group_id, breaker)
        return breaker

    async def guarded_is_toxic(self, group_id: int, user_id: int, text: str) -> bool:
        """统一引战检测入口：预算放行才调用 is_toxic()；拦截返回 False（放行消息，宁可漏检不烧钱）。

        user_interval=False：引战检测不占用/触发用户聊天限速（is_toxic 本身是单次调用）。
        """
        if not await self._ai_allowed(group_id, user_id, user_interval=False):
            return False
        return await self.ai_client.is_toxic(text)

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
        # 主动聊天也吃并发额度（与 WS 消息处理共用 process_semaphore），
        # 防止主动聊天与突发群消息叠加打爆 API
        async with self.process_semaphore:
            await self._do_active_chat_inner(group_id)

    async def _do_active_chat_inner(self, group_id: int):
        context_text = self.policy_engine.get_context_text(group_id, max_messages=150)
        if not context_text:
            logger.debug(f"No context for group {group_id}, skip active")
            return
        for _attempt in range(3):
            prompt = "你现在就是QQ群里的花璃，一个17岁高中生，正在自然地跟群友聊天。\n没有人在叫你。\n如果最近大家讨论一个话题，自然接一句，像平时一样简短而自然地说句话。\n如果群冷了，可以偶尔冒一句，简短就好。\n不要解释。\n不要说自己是AI。\n不要刻意活跃气氛。\n一句话即可，尽量短，自然。"
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
