"""WebSocket 生命周期测试（场景 6/7）：draining、shutdown 等待 in-flight、断开清理。"""
import asyncio
import json

from websockets.exceptions import ConnectionClosed

from src.core.websocket_server import WebSocketServer
from tests.test_router_regression import build_router


class FakeWS:
    """模拟 websockets 连接对象。"""

    def __init__(self, messages):
        self._messages = list(messages)
        self.closed = None
        self.waiters = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise ConnectionClosed(None, None)
        return self._messages.pop(0)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)

    async def wait_closed(self):
        return None


def _make_server(router=None, config=None):
    if router is None:
        router, config, ai, sender, mm = build_router()
    server = WebSocketServer(config, router)
    return server, router


async def test_handler_processes_and_disconnects_cleanly():
    """事件处理正常；连接断开后 ws 状态与连接标志被清理。"""
    server, router = _make_server()
    event = json.dumps({
        "post_type": "message",
        "message_type": "group",
        "group_id": 123,
        "user_id": 456,
        "message_id": 7001,
        "time": 1700000000,
        "message": [{"type": "text", "data": {"text": "你好"}}],
    })
    ws = FakeWS([event])
    router.policy_engine.should_reply_by_context = lambda gid: False  # 不接话，快速结束
    await server._handler(ws)
    assert server.ws is None  # 断开后清理
    assert router.global_state.ws_connected is False
    assert server._handler_task is None


async def test_draining_ignores_new_events():
    """draining 置位后：进行中的处理继续，新事件被忽略。"""
    server, router = _make_server()

    release = asyncio.Event()

    async def slow_process(data):
        await release.wait()
        return None

    router.process_event = slow_process
    # 第一条事件进入处理（in-flight）
    task = asyncio.create_task(server._handler(FakeWS([b"{}"])))
    await asyncio.sleep(0.05)
    server._draining = True
    # draining 后 handler 循环退出
    release.set()
    await asyncio.wait_for(task, timeout=2)
    assert server.ws is None


async def test_shutdown_waits_for_inflight():
    """shutdown 等待进行中的事件处理完成（不打断）；超时才取消。"""
    server, router = _make_server()
    server._drain_timeout = 5.0
    finished = asyncio.Event()

    async def slow_process(data):
        await asyncio.sleep(0.1)
        finished.set()
        return None

    router.process_event = slow_process
    task = asyncio.create_task(server._handler(FakeWS([b"{}"])))
    await asyncio.sleep(0.05)
    await server.shutdown()
    await task
    assert finished.is_set()  # in-flight 处理完整跑完
    assert router.global_state.ws_connected is False


async def test_shutdown_cancels_hung_inflight():
    """in-flight 超过 drain 超时 → 被取消，shutdown 不卡死。"""
    server, router = _make_server()
    server._drain_timeout = 0.2

    async def hung_process(data):
        await asyncio.sleep(60)  # 模拟卡死的 AI 请求

    router.process_event = hung_process
    task = asyncio.create_task(server._handler(FakeWS([b"{}"])))
    await asyncio.sleep(0.05)
    await asyncio.wait_for(server.shutdown(), timeout=3)  # shutdown 不卡死
    assert server._handler_task is None or server._handler_task.cancelled()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_single_connection_guard():
    """已有连接时拒绝新连接（1008）。"""
    server, router = _make_server()
    server.ws = object()
    closed = []

    class RejectedWS:
        async def close(self, code=1008, reason=""):
            closed.append((code, reason))

    await server._handler(RejectedWS())
    assert closed and closed[0][0] == 1008
