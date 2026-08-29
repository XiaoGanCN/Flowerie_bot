"""AiGateway：AI 准入层（从 MessageRouter 拆分，防上帝类）。

统一所有消耗 AI 调用的入口：熔断（provider 级 + 群级）→ 预算闸门 →
人格解析 → 群聊知识注入 → 重试循环（每次重试单独过预算）。

职责边界：
- guarded_chat：唯一 AI 对话入口（逻辑请求层）
- guarded_is_toxic：引战检测准入（预算放行才调 AI）
- 熔断管理：provider 级全局 + 群级有界容器（TTL + 容量上限）
"""
import asyncio
import random
import time
from typing import Optional, Tuple

from src.utils.circuit_breaker import CircuitBreaker
from src.utils.expiring_map import ExpiringMap
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry

logger = get_logger(__name__)

_M_REJECTED = registry.counter("rejected_messages_total", "被拒绝的消息总数（按原因）", ["reason"])
_M_AI_REQ = registry.counter("ai_requests_total", "AI 逻辑请求总数（用户发起的逻辑操作）")
_M_AI_ATTEMPTS = registry.counter("ai_attempts_total", "实际发往 Provider 的 HTTP 尝试总数（含重试）")
_M_AI_RETRY = registry.counter("ai_retry_total", "AI 请求重试次数")
_M_AI_OK = registry.counter("ai_success_total", "AI 请求成功数")
_M_AI_FAIL = registry.counter("ai_failure_total", "AI 请求失败数")
_M_AI_LATENCY = registry.histogram("ai_latency_seconds", "AI 请求耗时（秒）")
_M_CIRCUIT_REJECT = registry.counter("ai_circuit_rejections_total", "熔断拒绝的 AI 请求数（按层级）", ["level"])


class AiGateway:
    """AI 准入层：熔断 / 预算 / 人格 / 知识 / 重试。"""

    def __init__(self, config, ai_client, budget, prompt_manager=None,
                 tool_manager=None, persona_manager=None, meme_manager=None):
        """ai_client/tool_manager 等可变依赖以 provider（可调用）传入：
        动态读取宿主（MessageRouter）当前属性，支持测试/运行期热替换。"""
        self.config = config
        self._ai_client = ai_client if callable(ai_client) else (lambda: ai_client)
        self.budget = budget
        self._prompt_manager = prompt_manager if callable(prompt_manager) else (lambda: prompt_manager)
        self._tool_manager = tool_manager if callable(tool_manager) else (lambda: tool_manager)
        self._persona_manager = persona_manager if callable(persona_manager) else (lambda: persona_manager)
        self._meme_manager = meme_manager if callable(meme_manager) else (lambda: meme_manager)

        # ---- Circuit Breaker（双层：provider 级全局 + 群级有界）----
        self.provider_breaker = CircuitBreaker(
            name="provider",
            failure_threshold=max(1, int(getattr(config, "AI_CIRCUIT_BREAKER_FAILURES", 10))),
            cooldown_seconds=max(5, int(getattr(config, "AI_CIRCUIT_BREAKER_PAUSE_SECONDS", 60))),
        )
        self.group_breakers: ExpiringMap = ExpiringMap(
            ttl_seconds=max(60, int(getattr(config, "GROUP_CIRCUIT_BREAKER_TTL_SECONDS", 604800))),
            max_size=max(10, int(getattr(config, "GROUP_CIRCUIT_BREAKER_MAX_GROUPS", 1000))),
        )


    @property
    def ai_client(self):
        return self._ai_client()

    @property
    def prompt_manager(self):
        return self._prompt_manager()

    @property
    def tool_manager(self):
        return self._tool_manager()

    @property
    def persona_manager(self):
        return self._persona_manager()

    @property
    def meme_manager(self):
        return self._meme_manager()
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

    async def guarded_is_toxic(self, group_id: int, user_id: int, text: str) -> bool:
        """统一引战检测入口：预算放行才调用 is_toxic()；拦截返回 False（放行消息，宁可漏检不烧钱）。

        user_interval=False：引战检测不占用/触发用户聊天限速（is_toxic 本身是单次调用）。
        """
        if not await self._ai_allowed(group_id, user_id, user_interval=False):
            return False
        return await self.ai_client.is_toxic(text)

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

