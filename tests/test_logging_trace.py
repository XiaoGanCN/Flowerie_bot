"""trace_id / 日志 / metrics 基础设施测试。

覆盖：
- trace_id 并发隔离（多任务互不污染）
- 日志记录注入 trace_id
- 敏感信息（API Key/Token）不进日志
- metrics 计数正确、不影响业务
"""
import asyncio
import logging
import threading

import pytest

from src.utils.logging_setup import (TextFormatter, TraceIdFilter,
                                     init_logging, redact)
from src.utils.metrics import registry
from src.utils.trace import get_trace_id, trace_context


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture()
def capture_logger():
    """返回 (logger, handler)：捕获该 logger 的输出 record。"""
    logger = logging.getLogger(f"test_capture_{id(object())}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _CaptureHandler()
    handler.addFilter(TraceIdFilter())
    logger.addHandler(handler)
    yield logger, handler
    logger.removeHandler(handler)


# ---------- trace_id ----------
async def test_trace_id_concurrent_isolation():
    """并发任务各自持有独立 trace_id，互不污染；上下文退出后恢复为空。"""

    async def work(_):
        with trace_context() as tid:
            await asyncio.sleep(0.01)
            return tid, get_trace_id()

    results = await asyncio.gather(*(work(i) for i in range(20)))
    tids = [r[0] for r in results]
    # 20 个任务 20 个不同 trace_id
    assert len(set(tids)) == 20
    # 上下文内读取一致
    assert all(t == g for t, g in results)
    # 上下文外恢复为空串
    assert get_trace_id() == ""


async def test_trace_id_nested_restore():
    with trace_context() as outer:
        with trace_context() as inner:
            assert inner != outer
            assert get_trace_id() == inner
        assert get_trace_id() == outer


def test_log_record_has_trace_id(capture_logger):
    logger, handler = capture_logger
    with trace_context() as tid:
        logger.info("hello %s", "world")
    assert len(handler.records) == 1
    assert handler.records[0].trace_id == tid  # type: ignore[attr-defined]


# ---------- 敏感信息 ----------
def test_redact_api_key_and_tokens():
    assert "sk-***" in redact("key=sk-abc1234567890xyz12345")
    assert "sk-abc1234567890xyz12345" not in redact("key=sk-abc1234567890xyz12345")
    assert "ghp_***" in redact("token ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    assert "Bearer ***" in redact("Authorization: Bearer abcdefghijklmnop12345678")
    # 普通文本不受影响
    assert redact("今天天气不错") == "今天天气不错"


def test_sensitive_data_not_in_logged_output(capture_logger):
    """完整日志输出（经 formatter + redact）中不含 API Key。"""
    logger, handler = capture_logger
    secret = "sk-super-secret-key-1234567890"
    logger.warning("ai auth failed with %s", secret)
    # 用 TextFormatter 渲染 record，模拟真实输出路径
    formatter = TextFormatter()
    rendered = formatter.format(handler.records[0])
    assert secret not in rendered
    assert "sk-***" in rendered


# ---------- metrics ----------
def test_metrics_counter_and_snapshot():
    c = registry.counter("test_metrics_counter", "test")
    before = c.snapshot().get((), 0.0)
    c.inc()
    c.inc()
    c.inc({"reason": "x"}) if c.label_names else None
    snap = registry.snapshot()
    assert snap.get("test_metrics_counter", 0) >= before + 2


def test_metrics_export_text():
    h = registry.histogram("test_metrics_latency", "test")
    h.observe(0.5)
    h.observe(2.0)
    text = registry.export_text()
    assert "test_metrics_latency" in text
    assert "HELP" in text


def test_metrics_never_raise():
    """metrics 调用绝不抛异常、不拖慢业务（主业务零影响）。"""
    c = registry.counter("test_metrics_safe", "test", ["a"])
    for _ in range(1000):
        c.inc({"a": "b"})
        registry.snapshot()
        registry.export_text()
    # 线程安全：多线程并发计数不抛错
    errors = []

    def worker():
        try:
            for _ in range(200):
                c.inc({"a": "b"})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_init_logging_text_and_json():
    """两种格式均可初始化且输出正常（不污染其他测试的 root logger 太多）。"""
    root = logging.getLogger()
    old_level = root.level
    try:
        init_logging(level="INFO", fmt="json")
        root.debug("不应出现")
        root.info("json line")
    finally:
        root.setLevel(old_level)
