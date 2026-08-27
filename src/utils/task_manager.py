"""统一后台任务管理。

目标：
- 项目中所有 asyncio.create_task 都经由 BackgroundTaskManager.register
- 任务异常不静默死亡：done 回调统一记录 task_failed（含堆栈），不产生
  "coroutine was never awaited" 类警告
- 优雅关闭：cancel + await，超时强杀
- 生命周期事件：task_started / task_finished / task_failed / task_cancelled
"""
import asyncio
from typing import Awaitable, Callable, Dict

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)




class BackgroundTaskManager:
    """注册/跟踪/取消后台 asyncio 任务。"""

    def __init__(self) -> None:
        self._tasks: Dict[str, asyncio.Task] = {}
        self._closed = False

    def register(self, name: str, coro: Awaitable) -> asyncio.Task:
        """注册并启动一个后台任务。name 需唯一（重复注册会取消旧任务）。"""
        if self._closed:
            # 关闭未使用的协程，避免 "coroutine was never awaited" 泄漏警告
            close = getattr(coro, "close", None)
            if close is not None:
                close()
            raise RuntimeError(f"TaskManager 已关闭，无法注册任务 {name}")
        old = self._tasks.get(name)
        if old is not None and not old.done():
            logger.warning("task_duplicate name=%s：覆盖旧任务", name, extra={"event": "task_duplicate", "task": name})
            old.cancel()
        task = asyncio.create_task(coro)
        self._tasks[name] = task
        task.add_done_callback(self._make_done_callback(name))
        return task

    def _make_done_callback(self, name: str) -> Callable[[asyncio.Task], None]:
        def _on_done(task: asyncio.Task) -> None:
            # 从注册表移除自己（避免引用旧任务）
            if self._tasks.get(name) is task:
                self._tasks.pop(name, None)
            if task.cancelled():
                logger.info("task_cancelled name=%s", name, extra={"event": "task_finished", "task": name})
                return
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "task_failed name=%s err=%s", name, exc,
                    extra={"event": "task_failed", "task": name},
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                return
            logger.info("task_finished name=%s", name, extra={"event": "task_finished", "task": name})

        return _on_done

    async def shutdown(self, timeout: float = 10.0) -> None:
        """优雅关闭：停止注册新任务 → cancel 全部 → 等待；超时强杀。"""
        self._closed = True
        tasks = [t for t in self._tasks.values() if not t.done()]
        if not tasks:
            logger.info("task_manager_shutdown no_running_tasks")
            return
        for t in tasks:
            t.cancel()
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning("task_manager_shutdown timeout: %d 个任务未在 %ss 内退出，强制取消", len(pending), timeout)
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:  # noqa: BLE001
                    pass
        logger.info("task_manager_shutdown finished tasks=%d", len(done))

    def running_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done())

    def task_names(self) -> list:
        return list(self._tasks.keys())
