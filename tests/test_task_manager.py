"""BackgroundTaskManager 测试：任务失败捕获 / 优雅关闭 / 生命周期事件。"""
import asyncio
import logging

import pytest

from src.utils.task_manager import BackgroundTaskManager


@pytest.fixture()
def tm():
    manager = BackgroundTaskManager()
    yield manager


async def test_task_finished_normal(tm):
    done = asyncio.Event()

    async def work():
        done.set()

    tm.register("normal", work())
    await asyncio.wait_for(done.wait(), timeout=2)
    await asyncio.sleep(0.02)  # 让 _wrap 收尾
    assert tm.running_count() == 0


async def test_task_failure_captured(tm, caplog):
    """任务抛异常：不静默死亡，日志记录 task_failed。"""
    caplog.set_level(logging.ERROR)

    async def boom():
        raise RuntimeError("boom-task")

    with caplog.at_level(logging.ERROR, logger="src.utils.task_manager"):
        tm.register("boom_task", boom())
        await asyncio.sleep(0.1)
    assert tm.running_count() == 0
    assert any("task_failed" in r.message for r in caplog.records)


async def test_graceful_shutdown_cancels_tasks(tm):
    """shutdown 取消全部长期任务并等待退出。"""

    async def long_running():
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise

    tm.register("loop1", long_running())
    tm.register("loop2", long_running())
    assert tm.running_count() == 2
    await tm.shutdown(timeout=2)
    assert tm.running_count() == 0
    # 关闭后禁止再注册
    with pytest.raises(RuntimeError):
        tm.register("late", long_running())


async def test_shutdown_without_tasks(tm):
    await tm.shutdown(timeout=1)  # 无任务也不抛错


async def test_duplicate_name_cancels_old(tm):
    """同名注册：旧任务被取消，新任务接管。"""

    async def work():
        try:
            while True:
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise

    tm.register("dup", work())
    old = tm._tasks["dup"]
    tm.register("dup", work())
    assert tm._tasks["dup"] is not old
    # 等待旧任务完成取消（cancel 请求已发出，任务处于 cancelling → cancelled）
    try:
        await old
    except asyncio.CancelledError:
        pass
    assert old.cancelled()
    await tm.shutdown(timeout=1)
