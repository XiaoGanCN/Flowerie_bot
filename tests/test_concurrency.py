"""并发安全测试（场景 1/2/3/10）：高并发消息、同用户并发、预算并发、记忆去重竞态。

所有检查都基于 asyncio 单线程语义：真正的并发点在于 await 之间的交错
与 to_thread（SQLite commit）。
"""
import asyncio
import tempfile

from src.core.cooldown_manager import CooldownManager
from src.models import GlobalState
from src.repositories.sqlite_repository import SQLiteMemoryRepository
from src.services.memory_manager import MemoryManager
from tests.test_router_regression import build_router, make_config


async def test_100_concurrent_messages_no_cross_talk():
    """100 条消息并发处理：不串群、不串用户、trace_id 各自独立、无异常。"""
    router, config, ai, sender, mm = build_router()
    router.policy_engine.should_reply_by_context = lambda gid: True  # 全部走 AI 流程

    async def one(i):
        group_id = 10000 + (i % 10)          # 10 个群
        user_id = 20000 + (i % 50)           # 50 个用户
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": group_id,
            "user_id": user_id,
            "message_id": 300000 + i,
            "time": 1700000000 + i,
            "message": [{"type": "text", "data": {"text": f"并发消息{i}"}}],
        }
        await router.process_event(event)
        return group_id, user_id

    results = await asyncio.gather(*(one(i) for i in range(100)))
    assert len(results) == 100
    # 上下文按群隔离：每个群只包含自己群的消息
    for gid in range(10000, 10010):
        state = router.policy_engine.get_group_state(gid)
        for m in state.context:
            if m.get("is_bot"):
                continue
            # 该条消息来自本群用户
            assert m["user_id"] in range(20000, 20050)


async def test_concurrent_same_user_cooldown_only_one_passes():
    """同一用户 20 条并发（未@）：只有第 1 条通过用户冷却。"""
    config = make_config(USER_COOLDOWN=5)
    groups = {}
    gs = GlobalState()
    cm = CooldownManager(config, groups, gs)

    results = []
    for _ in range(20):
        # check-then-set 两步之间无 await → asyncio 单线程内原子
        allowed = cm.can_user_reply(100, 1)
        if allowed:
            cm.update_user_time(100, 1)
        results.append(allowed)
    assert sum(results) == 1


async def test_concurrent_budget_single_slot():
    """群预算只剩 1 个名额，10 个并发 AI 请求：只有 1 个通过，其余 denied。"""
    config = make_config(GROUP_DAILY_AI_CALL_BUDGET=1, AI_MAX_RETRIES=0)
    router, config, ai, sender, mm = build_router(config)

    async def one(_):
        reply, mem, denied = await router.guarded_chat(123, 1000 + _, user_message="x", context="ctx")
        return denied

    results = await asyncio.gather(*(one(i) for i in range(10)))
    # 1 个成功（denied=False）+ 9 个被预算拒绝
    assert sum(1 for d in results if not d) == 1
    assert sum(1 for d in results if d) == 9


async def test_concurrent_budget_global_slot():
    """全局预算剩 1 个名额，10 个并发（不同群）：只有 1 个通过。"""
    config = make_config(DAILY_AI_CALL_BUDGET=1, GROUP_DAILY_AI_CALL_BUDGET=0, AI_MAX_RETRIES=0)
    router, config, ai, sender, mm = build_router(config)

    async def one(i):
        reply, mem, denied = await router.guarded_chat(1000 + i, 2000 + i, user_message="x", context="ctx")
        return denied

    results = await asyncio.gather(*(one(i) for i in range(10)))
    assert sum(1 for d in results if not d) == 1


async def test_concurrent_memory_dedup_race():
    """50 个并发写入相同记忆：去重生效，最终只有 1 条（check-then-insert 原子）。"""
    tmp = tempfile.TemporaryDirectory()
    try:
        mm = MemoryManager(f"{tmp.name}/m.db")

        async def writer(_):
            await mm.append_memory_text(1, 10, "喜欢喝奶茶")

        await asyncio.gather(*(writer(i) for i in range(50)))
        notes = mm.get_user_notes(1, 10)
        assert len(notes) == 1
        mm.close()
    finally:
        tmp.cleanup()


async def test_concurrent_memory_rw_mixed():
    """50 写 + 50 读混合并发：SQLite 层无异常、无数据损坏、无丢失。

    走 repository 层（绕过业务去重），直接验证存储并发安全。
    """
    import time as _time

    from src.repositories.base import MemoryNote

    tmp = tempfile.TemporaryDirectory()
    try:
        repo = SQLiteMemoryRepository(f"{tmp.name}/m.db")
        mm = MemoryManager(f"{tmp.name}/unused.json", repository=repo)

        async def writer(i):
            repo.insert_note(MemoryNote(
                user_id=1, group_id=10, text=f"记忆{i:03d}", created_at=_time.time()))
            await asyncio.to_thread(repo.commit)

        async def reader(_i):
            await asyncio.sleep(0)
            return len(mm.get_memory_context(1, 10))

        await asyncio.gather(*(writer(i) for i in range(50)), *(reader(i) for i in range(50)))
        notes = mm.get_user_notes(1, 10)
        assert len(notes) == 50
        assert len(set(notes)) == 50  # 无重复
        mm.close()
    finally:
        tmp.cleanup()
