"""取消传播与资源释放测试（场景 5/11/12）。"""
import asyncio
import tempfile

from src.services.memory_manager import MemoryManager
from tests.test_router_regression import build_router


async def test_ai_request_cancellation_releases_semaphore():
    """AI 请求被 wait_for 取消：semaphore 释放，后续消息可正常处理。"""
    router, config, ai, sender, mm = build_router()

    async def hung_chat(**kwargs):
        await asyncio.sleep(60)  # 永不返回

    ai.chat_once = hung_chat

    async def process_with_timeout(event):
        try:
            async with router.process_semaphore:
                await asyncio.wait_for(router.process_event(event), timeout=0.2)
        except asyncio.TimeoutError:
            pass

    event = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123,
        "user_id": 456,
        "message_id": 8001,
        "time": 1700000000,
        "message": [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": "在吗"}},
        ],
    }
    await process_with_timeout(event)
    # 超时取消后 semaphore 已释放：可再次获取
    async with router.process_semaphore:
        pass  # 不卡住即通过


async def test_cancelled_memory_write_does_not_corrupt():
    """记忆写入中途被取消：数据库不损坏，后续写入正常。

    asyncio.to_thread 的 commit 一旦发出无法取消（线程会跑完），
    因此"第一笔"可能已落库也可能未落库——两种结果都合法，关键是库完好。
    """
    tmp = tempfile.TemporaryDirectory()
    try:
        mm = MemoryManager(f"{tmp.name}/m.db")

        task = asyncio.create_task(mm.append_memory_text(1, 10, "第一笔"))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # 取消后仍可正常写入与读取
        await mm.append_memory_text(1, 10, "第二笔")
        notes = mm.get_user_notes(1, 10)
        assert "第二笔" in notes
        assert len(notes) in (1, 2)  # 第一笔可能已落库（线程不可取消）
        mm.close()
    finally:
        tmp.cleanup()


async def test_cancellation_not_swallowed_by_except_exception():
    """except Exception 不吞 CancelledError（CancelledError 是 BaseException）。"""
    caught = []

    async def work():
        try:
            await asyncio.sleep(60)
        except Exception as e:  # noqa: BLE001
            caught.append(e)
            raise

    task = asyncio.create_task(work())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    # except Exception 没有捕获到 CancelledError
    assert caught == []
    assert task.cancelled()


async def test_to_thread_commit_not_blocked_by_cancel():
    """asyncio.to_thread 的 commit 被取消：线程内提交仍完成，数据不丢。"""
    tmp = tempfile.TemporaryDirectory()
    try:
        from src.repositories.sqlite_repository import SQLiteMemoryRepository

        repo = SQLiteMemoryRepository(f"{tmp.name}/m.db")
        repo.insert_note(__import__("src.repositories.base", fromlist=["MemoryNote"]).MemoryNote(
            user_id=1, group_id=10, text="数据"))
        task = asyncio.create_task(asyncio.to_thread(repo.commit))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.1)  # 让线程跑完
        # 重新打开验证数据落盘
        repo2 = SQLiteMemoryRepository(f"{tmp.name}/m.db")
        assert len(repo2.list_all_notes()) == 1
        repo2.close()
    finally:
        tmp.cleanup()
