"""AI 请求可靠性测试：超时 / 重试 / 重试不绕过预算 / 4xx 不重试。

复用 test_router_regression 的 build_router（真实 PolicyEngine + BudgetManager）。
"""
import asyncio

import httpx
import pytest

from src.services.ai_client import AIClient
from tests.test_ai_client import make_config as ai_make_config
from tests.test_router_regression import build_router


# ---------- AI timeout（chat_once 层） ----------
async def test_chat_once_timeout_returns_none():
    """httpx 超时异常 → 返回 (None, None)，不崩溃、可重试。"""
    ai = AIClient(ai_make_config(), None)

    class TimeoutClient:
        async def post(self, *a, **k):
            raise httpx.TimeoutException("read timeout")

    ai.client = TimeoutClient()
    reply, mem = await ai.chat_once("你好", "（暂无历史聊天记录）")
    assert reply is None
    assert mem is None


async def test_chat_once_network_error_returns_none():
    ai = AIClient(ai_make_config(), None)

    class NetErrClient:
        async def post(self, *a, **k):
            raise httpx.ConnectError("connection refused")

    ai.client = NetErrClient()
    reply, mem = await ai.chat_once("你好", "（暂无历史聊天记录）")
    assert reply is None


# ---------- guarded_chat：重试 / 预算 / 4xx ----------
@pytest.fixture()
def no_sleep(monkeypatch):
    """把 asyncio.sleep 替换为立即返回（加速退避等待）。"""
    async def fake_sleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


async def test_ai_retry_succeeds(no_sleep):
    """前两次失败、第三次成功 → 共 3 次尝试，最终成功。"""
    router, config, ai, sender, mm = build_router()

    class RetryAI:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                return None, None
            return "终于成功了", None

    retry_ai = RetryAI()
    router.ai_client = retry_ai
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="x", context="ctx")
    assert retry_ai.calls == 3
    assert reply == "终于成功了"
    assert denied is False


async def test_ai_gives_up_after_max_retries(no_sleep):
    """始终失败 → 尝试 AI_MAX_RETRIES+1 次后放弃，返回 None。"""
    router, config, ai, sender, mm = build_router()
    config.AI_MAX_RETRIES = 2

    class AlwaysFail:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            return None, None

    fail_ai = AlwaysFail()
    router.ai_client = fail_ai
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="x", context="ctx")
    assert fail_ai.calls == 3
    assert reply is None


async def test_retry_does_not_bypass_budget(no_sleep):
    """回归：重试的每次尝试都单独过预算闸门（retry 永远不绕过额度）。"""
    router, config, ai, sender, mm = build_router()

    class AlwaysFail:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            return None, None

    fail_ai = AlwaysFail()
    router.ai_client = fail_ai
    budget_before = router.global_state.ai_budget_count
    await router.guarded_chat(123, 456, user_message="x", context="ctx")
    # 每次尝试扣一次预算：尝试次数 == budget 增量
    assert fail_ai.calls == router.global_state.ai_budget_count - budget_before
    assert fail_ai.calls == 4  # AI_MAX_RETRIES 默认 3 → 4 次


async def test_4xx_not_retried(no_sleep):
    """4xx 业务错误（_retryable=False）：只尝试一次，不白烧预算。"""
    router, config, ai, sender, mm = build_router()

    class FourXX:
        def __init__(self):
            self.calls = 0
            self._api_backoff = 0.0
            self._retryable = True

        async def chat_once(self, **kwargs):
            self.calls += 1
            self._retryable = False  # 模拟 chat_once 遇到 401/400
            return None, None

    fourxx = FourXX()
    router.ai_client = fourxx
    budget_before = router.global_state.ai_budget_count
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="x", context="ctx")
    assert fourxx.calls == 1
    assert router.global_state.ai_budget_count - budget_before == 1
    assert reply is None


async def test_budget_denied_skips_ai(no_sleep):
    """预算拒绝 → 不调用 AI、返回 denied=True。"""
    router, config, ai, sender, mm = build_router()
    calls = {"n": 0}

    class CountingAI:
        async def chat_once(self, **kwargs):
            calls["n"] += 1
            return "不应到达", None

    from datetime import datetime

    router.ai_client = CountingAI()
    # 当天额度已超（date=今天避免触发跨天重置）
    router.global_state.ai_budget_date = datetime.now().strftime("%Y-%m-%d")
    router.global_state.ai_budget_count = config.DAILY_AI_CALL_BUDGET + 1
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="x", context="ctx")
    assert denied is True
    assert calls["n"] == 0
    assert reply is None
