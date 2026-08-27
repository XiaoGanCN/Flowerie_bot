"""Metrics cardinality 审计（第三轮十三节）：
禁止把 group_id / user_id / message_id / trace_id 作为 label。"""
from src.utils.metrics import registry
from tests.test_router_regression import build_router


def test_no_high_cardinality_labels_in_export():
    """导出文本中不得出现任何数字 QQ 号 / 群号作为 label 值。"""
    text = registry.export_text()
    # label 值集合：应为低基数枚举（reason/target/level/post_type）
    import re

    label_values = set(re.findall(r'\{[^}]*\}', text))
    joined = " ".join(label_values)
    # 不允许出现 5~12 位纯数字（QQ/群号）
    assert not re.search(r'="\d{5,12}"', joined), f"发现高基数 label: {joined[:200]}"


async def test_ai_metrics_distinguish_logical_vs_attempts():
    """ai_requests_total（逻辑）与 ai_attempts_total（HTTP 尝试）分层统计。"""
    import asyncio as aio

    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(AI_MAX_RETRIES=2))

    class Flaky:
        def __init__(self):
            self.calls = 0

        async def chat_once(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                return None, None
            return "ok", None

    router.ai_client = Flaky()
    orig_sleep = aio.sleep

    async def fake_sleep(*a, **k):
        return None

    aio.sleep = fake_sleep
    try:
        reply, mem, denied = await router.guarded_chat(123, 456, user_message="x", context="ctx")
        assert reply == "ok"
    finally:
        aio.sleep = orig_sleep

    snap = registry.snapshot()
    # 1 个逻辑请求、3 次 attempt、2 次 retry
    assert snap["ai_requests_total"] >= 1
    assert snap["ai_attempts_total"] >= 3
    assert snap["ai_retry_total"] >= 2


async def test_circuit_rejection_metric_labeled_by_level():
    """熔断拒绝按 level 标记（provider/group），不按群标记。"""
    from tests.test_router_regression import make_config as mkcfg

    router, config, ai, sender, mm = build_router(mkcfg(
        AI_MAX_RETRIES=0, AI_CIRCUIT_BREAKER_FAILURES=1, AI_CIRCUIT_BREAKER_PAUSE_SECONDS=60))

    class FailAI:
        async def chat_once(self, **kwargs):
            self._retryable = True
            self._api_backoff = 1.0
            return None, None

    router.ai_client = FailAI()
    await router.guarded_chat(100, 200, user_message="x", context="ctx")  # 失败 → provider OPEN
    await router.guarded_chat(101, 201, user_message="x", context="ctx")  # 被拒

    text = registry.export_text()
    assert 'ai_circuit_rejections_total{level="provider"}' in text
    # 不允许出现按群标记的 label
    assert "group_id" not in text
