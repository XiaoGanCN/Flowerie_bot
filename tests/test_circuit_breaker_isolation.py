"""双层 Circuit Breaker 测试（第三轮）：故障隔离、状态机、生命周期、并发。"""
import asyncio

import pytest

from src.utils.circuit_breaker import CircuitBreaker
from src.utils.expiring_map import ExpiringMap
from tests.test_router_regression import build_router


class FakeClock:
    """可注入的 monotonic 时钟（测试用）。"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------- CircuitBreaker 状态机 ----------
def test_state_machine_closed_to_open():
    cb = CircuitBreaker("t", failure_threshold=3, cooldown_seconds=60)
    assert cb.state == "CLOSED"
    assert cb.allow() is True
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow() is False  # OPEN 阻止请求


def test_open_to_half_open_after_cooldown():
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=60, clock=clock)
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow() is False
    clock.advance(61)
    assert cb.state == "HALF_OPEN"
    assert cb.allow() is True  # probe 放行


def test_half_open_limits_concurrent_probes():
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=60, clock=clock, half_open_probes=1)
    cb.record_failure()
    clock.advance(61)
    assert cb.allow() is True   # 第一个 probe
    assert cb.allow() is False  # 并发 probe 被拒（独占）


def test_probe_success_closes():
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=60, clock=clock)
    cb.record_failure()
    clock.advance(61)
    assert cb.allow() is True
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.allow() is True


def test_probe_failure_reopens():
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=60, clock=clock)
    cb.record_failure()
    clock.advance(61)
    assert cb.allow() is True
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow() is False


def test_monotonic_clock_used():
    """熔断时间判断基于注入时钟（等价于 time.monotonic），不用 wall clock。"""
    clock = FakeClock()
    cb = CircuitBreaker("t", failure_threshold=1, cooldown_seconds=60, clock=clock)
    cb.record_failure()
    clock.advance(30)
    assert cb.allow() is False  # 冷却未到
    clock.advance(31)
    assert cb.allow() is True   # 冷却已到


# ---------- 双层熔断：故障隔离（Case A/B/C/D） ----------
@pytest.fixture()
def no_sleep(monkeypatch):
    async def fake_sleep(*a, **k):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


def _make_fail_ai():
    class FailAI:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            self._retryable = True
            self._api_backoff = 1.0
            return None, None

    return FailAI()


async def test_group_failure_does_not_affect_other_groups(no_sleep):
    """Case A：群 A 连续失败 → 群 A 熔断；群 B 完全不受影响。"""
    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(
        AI_MAX_RETRIES=0,
        AI_CIRCUIT_BREAKER_FAILURES=999,  # provider 级不触发（隔离验证点）
        GROUP_CIRCUIT_BREAKER_FAILURES=3,
        GROUP_CIRCUIT_BREAKER_PAUSE_SECONDS=60,
    ))

    fail_ai = _make_fail_ai()
    router.ai_client = fail_ai

    # 群 A 连续失败 3 次 → 群级熔断
    for i in range(3):
        await router.guarded_chat(1001, 9000 + i, user_message="x", context="ctx")
    assert router.provider_breaker.state == "CLOSED"  # provider 未熔断
    assert router._get_group_breaker(1001).state == "OPEN"  # 群 A 已熔断

    # 群 A 被拒，且不触发新 attempt
    calls_before = fail_ai.calls
    reply, mem, denied = await router.guarded_chat(1001, 9100, user_message="x", context="ctx")
    assert denied is True
    assert fail_ai.calls == calls_before

    # 群 B 正常工作（AI 返回空但未被熔断拦截）
    reply, mem, denied = await router.guarded_chat(1002, 9200, user_message="x", context="ctx")
    assert denied is False  # 群 B 正常放行（AI 返回空但未被熔断拦截）


async def test_provider_global_outage_no_retry_storm(no_sleep):
    """Case B：Provider 全局不可用 → provider 熔断后所有请求快速失败，不再产生 attempt。"""
    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(
        AI_MAX_RETRIES=2, AI_CIRCUIT_BREAKER_FAILURES=5, AI_CIRCUIT_BREAKER_PAUSE_SECONDS=60))

    fail_ai = _make_fail_ai()
    router.ai_client = fail_ai

    # 5 个逻辑请求（每个 3 次 attempt）→ provider 熔断
    for i in range(5):
        await router.guarded_chat(2000 + i, 8000 + i, user_message="x", context="ctx")
    assert router.provider_breaker.state == "OPEN"

    # 熔断后：任何群的请求快速失败，不产生新的 HTTP attempt
    calls_before = fail_ai.calls
    for i in range(20):
        reply, mem, denied = await router.guarded_chat(3000 + i, 7000 + i, user_message="x", context="ctx")
        assert denied is True
    assert fail_ai.calls == calls_before  # 零新增 attempt


async def test_group_spam_does_not_drag_other_groups(no_sleep):
    """Case C：群 A 疯狂请求不拖垮群 B（预算群级隔离 + 群级熔断）。"""
    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(
        AI_MAX_RETRIES=0,
        GROUP_DAILY_AI_CALL_BUDGET=5,  # 群 A 预算上限 5
        GROUP_CIRCUIT_BREAKER_FAILURES=999,  # 让预算先兜底
    ))

    # 群 A 发 20 条：预算只放行 5 条
    passed = 0
    for i in range(20):
        reply, mem, denied = await router.guarded_chat(4001, 6000 + i, user_message="x", context="ctx")
        if not denied:
            passed += 1
    assert passed == 5
    # 群 B 预算独立，不受影响
    reply, mem, denied = await router.guarded_chat(4002, 6100, user_message="x", context="ctx")
    assert denied is False


async def test_100_groups_concurrent_isolation(no_sleep):
    """Case D：100 个群并发，breaker/budget 状态互不污染。"""
    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(
        AI_MAX_RETRIES=0,
        AI_CIRCUIT_BREAKER_FAILURES=999,
        GROUP_CIRCUIT_BREAKER_FAILURES=2,
    ))

    fail_ai = _make_fail_ai()
    router.ai_client = fail_ai

    async def one(i):
        # 每个群 3 次失败（用户全局唯一，避免 per-user 限速干扰）→ 群级熔断
        for k in range(3):
            await router.guarded_chat(5000 + i, 100000 + i * 10 + k, user_message="x", context="ctx")

    await asyncio.gather(*(one(i) for i in range(100)))
    # 100 个群各有自己的熔断器
    assert len(router.group_breakers) == 100
    # 全部群熔断（每个群都失败了 3 次）
    for i in range(100):
        assert router._get_group_breaker(5000 + i).state == "OPEN"
    # provider 未熔断
    assert router.provider_breaker.state == "CLOSED"


# ---------- breaker 生命周期 ----------
def test_group_breakers_ttl_and_capacity():
    """群级熔断器容器：TTL 惰性过期 + max_size 淘汰，不会无限增长。"""
    clock = FakeClock()
    store = ExpiringMap(ttl_seconds=3600, max_size=3, clock=clock)
    for i in range(3):
        store.set(i, CircuitBreaker(f"g{i}", failure_threshold=1))
    assert len(store) == 3
    # 超容量：第 4 个挤掉最旧
    store.set(3, CircuitBreaker("g3", failure_threshold=1))
    assert len(store) == 3
    assert store.get(0) is None  # 最旧被淘汰
    assert store.get(3) is not None
    # TTL 过期：惰性失效
    clock.advance(3601)
    assert store.get(1) is None
    assert store.get(2) is None
    assert len(store) == 1  # 只剩 3（未过期）

    # cleanup 后完全清空过期项
    store.set(1, CircuitBreaker("g1", failure_threshold=1))
    clock.advance(3601)
    removed = store.cleanup()
    assert removed == 2
    assert len(store) == 0
