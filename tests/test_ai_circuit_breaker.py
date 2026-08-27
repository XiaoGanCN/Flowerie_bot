"""AI 全局熔断测试（场景 4）：失败风暴保护。"""
import asyncio
import time

import pytest

from tests.test_router_regression import build_router


@pytest.fixture()
def no_sleep(monkeypatch):
    async def fake_sleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


async def test_circuit_breaker_opens_after_failures(no_sleep):
    """连续失败达到阈值 → 熔断打开：后续 AI 调用被拒绝且不再触发真实请求。"""
    config = None
    router, config, ai, sender, mm = build_router()
    config.AI_CIRCUIT_BREAKER_FAILURES = 3
    config.AI_CIRCUIT_BREAKER_PAUSE_SECONDS = 60
    config.AI_MAX_RETRIES = 0  # 一次失败即逻辑失败

    class AlwaysFail:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            return None, None

    fail_ai = AlwaysFail()
    router.ai_client = fail_ai

    # 3 次逻辑失败（不同 user，避免 per-user 限速干扰）→ 熔断打开
    for i in range(3):
        await router.guarded_chat(123, 1000 + i, user_message="x", context="ctx")
    assert router.global_state.ai_circuit_open_until > time.time()

    # 熔断中：不调用 AI、返回 denied
    calls_before = fail_ai.calls
    reply, mem, denied = await router.guarded_chat(123, 9999, user_message="x", context="ctx")
    assert denied is True
    assert reply is None
    assert fail_ai.calls == calls_before  # 没有新增 API 尝试


async def test_circuit_breaker_recovers_after_pause(no_sleep):
    """熔断窗口过后自动恢复；成功请求清零连续失败计数。"""
    router, config, ai, sender, mm = build_router()
    config.AI_CIRCUIT_BREAKER_FAILURES = 2
    config.AI_CIRCUIT_BREAKER_PAUSE_SECONDS = 60
    config.AI_MAX_RETRIES = 0

    class Flaky:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                return None, None
            return "恢复了", None

    flaky = Flaky()
    router.ai_client = flaky

    await router.guarded_chat(123, 1001, user_message="x", context="ctx")  # 失败1
    await router.guarded_chat(123, 1002, user_message="x", context="ctx")  # 失败2 → 熔断
    assert router.global_state.ai_circuit_open_until > time.time()

    # 熔断窗口过期 → 恢复
    router.global_state.ai_circuit_open_until = time.time() - 1
    reply, mem, denied = await router.guarded_chat(123, 1003, user_message="x", context="ctx")
    assert reply == "恢复了"
    assert router.global_state.ai_consecutive_failures == 0  # 成功清零


async def test_circuit_breaker_not_triggered_by_4xx(no_sleep):
    """4xx 业务错误不计入熔断（不是瞬时故障）。"""
    router, config, ai, sender, mm = build_router()
    config.AI_CIRCUIT_BREAKER_FAILURES = 2
    config.AI_MAX_RETRIES = 0

    class FourXX:
        def __init__(self):
            self._retryable = True
            self._api_backoff = 0.0

        async def chat_once(self, **kwargs):
            self._retryable = False
            return None, None

    router.ai_client = FourXX()
    await router.guarded_chat(123, 2001, user_message="x", context="ctx")
    await router.guarded_chat(123, 2002, user_message="x", context="ctx")
    # 4xx 不计入连续失败 → 熔断未打开
    assert router.global_state.ai_circuit_open_until == 0.0
