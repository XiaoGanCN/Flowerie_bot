"""压力测试（第四阶段）：并发风暴 / 重试风暴 / shutdown during workload。

目标不是测 QPS，而是发现 race condition / deadlock / leaked task / leaked connection。
全部 mock，不碰真实 API。
"""
import asyncio
import tempfile

from src.repositories.sqlite_repository import SQLiteMemoryRepository
from src.services.memory_manager import MemoryManager
from tests.test_router_regression import build_router


async def test_100_concurrent_messages_full_pipeline():
    """100 条消息并发走完整消息处理管线（含 AI 调用），无死锁、无泄漏。"""
    router, config, ai, sender, mm = build_router()
    router.policy_engine.should_reply_by_context = lambda gid: True

    async def one(i):
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1000 + (i % 5),
            "user_id": 2000 + i,
            "message_id": 400000 + i,
            "time": 1700000000,
            "message": [{"type": "text", "data": {"text": f"压力消息{i}"}}],
        }
        await router.process_event(event)

    await asyncio.gather(*(one(i) for i in range(100)))
    # 全部消息都进入上下文（无丢失）
    total = sum(len(router.policy_engine.get_group_state(g).context) for g in range(1000, 1005))
    assert total >= 100
    # semaphore 无泄漏：可再次正常获取
    async with router.process_semaphore:
        pass


async def test_ai_failure_storm_no_deadlock():
    """AI 连续失败风暴（50 条消息 × 重试）：不卡死、熔断最终打开、后续被拒。"""
    router, config, ai, sender, mm = build_router()
    config.AI_MAX_RETRIES = 1
    config.AI_CIRCUIT_BREAKER_FAILURES = 5
    config.AI_CIRCUIT_BREAKER_PAUSE_SECONDS = 60

    async def fake_sleep(*a, **k):
        return None

    orig_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    try:
        class FailAI:
            async def chat_once(self, **kwargs):
                self._retryable = True
                self._api_backoff = 1.0
                return None, None

        router.ai_client = FailAI()

        async def one(i):
            await router.guarded_chat(3000 + (i % 3), 4000 + i, user_message="x", context="ctx")

        await asyncio.gather(*(one(i) for i in range(50)))
        # Provider 熔断打开：后续调用直接被拒，不再触发 API
        assert router.provider_breaker.state == "OPEN"
        reply, mem, denied = await router.guarded_chat(9999, 9999, user_message="x", context="ctx")
        assert denied is True
    finally:
        asyncio.sleep = orig_sleep


async def test_shutdown_during_heavy_workload():
    """活跃负载中 shutdown：后台任务全部取消、无 pending task 泄漏。"""
    router, config, ai, sender, mm = build_router()
    await router.start()

    async def workload():
        for _ in range(20):
            event = {
                "post_type": "message",
                "message_type": "group",
                "group_id": 777,
                "user_id": 888,
                "message_id": 500000 + _,
                "time": 1700000000,
                "message": [{"type": "text", "data": {"text": f"shutdown压力{_}"}}],
            }
            await router.process_event(event)
            await asyncio.sleep(0)

    task = asyncio.create_task(workload())
    await asyncio.sleep(0.01)
    await asyncio.wait_for(router.stop(), timeout=5)  # shutdown 不卡死
    await task  # 工作负载协程正常结束
    assert router.task_manager.running_count() == 0


async def test_50_concurrent_memory_writes_and_reads():
    """50 并发记忆写 + 50 并发读（to_thread commit 路径），无 database locked。"""
    import time

    from src.repositories.base import MemoryNote

    tmp = tempfile.TemporaryDirectory()
    try:
        repo = SQLiteMemoryRepository(f"{tmp.name}/m.db")
        mm = MemoryManager(f"{tmp.name}/unused.json", repository=repo)

        async def writer(i):
            repo.insert_note(MemoryNote(user_id=1, group_id=10, text=f"w{i:03d}", created_at=time.time()))
            await asyncio.to_thread(repo.commit)

        async def reader(i):
            await asyncio.sleep(0)
            mm.get_memory_context(1, 10, max_notes=20)

        await asyncio.gather(*(writer(i) for i in range(50)), *(reader(i) for i in range(50)))
        assert len(mm.get_user_notes(1, 10)) == 50
        mm.close()
    finally:
        tmp.cleanup()
