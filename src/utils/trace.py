"""Trace ID 基础设施：一次消息处理的唯一链路标识。

使用 contextvars.ContextVar 而非全局变量，确保 asyncio 并发处理多条
QQ 消息时 trace_id 互不污染；asyncio.to_thread 会随协程上下文自动传播。
"""
import contextvars
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")


def new_trace_id() -> str:
    """生成新的 trace_id（12 位 hex，足够区分并发请求）。"""
    return uuid.uuid4().hex[:12]


def get_trace_id() -> str:
    """读取当前上下文里的 trace_id；无则返回空串。"""
    return _trace_id_var.get()


def set_trace_id(tid: str) -> None:
    """在当前上下文设置 trace_id（由 trace_context 内部使用）。"""
    _trace_id_var.set(tid)


@contextmanager
def trace_context(tid: Optional[str] = None) -> Iterator[str]:
    """为一段逻辑建立 trace_id 上下文，退出时自动恢复原值。

    用法：:

        with trace_context() as tid:
            ...  # 此范围内所有日志/埋点共享 tid
    """
    token = _trace_id_var.set(tid or new_trace_id())
    try:
        yield _trace_id_var.get()
    finally:
        _trace_id_var.reset(token)
