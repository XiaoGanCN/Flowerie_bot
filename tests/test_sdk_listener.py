"""SDK 中层测试：EventDispatcher（多监听/priority/异常隔离/stop/shutdown）。"""
import pytest

from src.sdk.event import BotEvent
from src.sdk.listener import EventDispatcher


def _event(kind="message"):
    return BotEvent({"kind": kind, "scope": "group", "user_id": 1, "group_id": 2, "text": "x"})


@pytest.mark.asyncio
async def test_dispatch_priority_and_isolation():
    d = EventDispatcher()
    order = []

    @d.on("message", priority=1)
    async def low(event):
        order.append("low")

    @d.on("message", priority=50)
    async def high(event):
        order.append("high")

    @d.on("message", priority=25)
    async def broken(event):
        order.append("broken")
        raise RuntimeError("插件崩了")

    @d.on("notice")
    async def notice_handler(event):  # noqa: ARG001
        order.append("notice")

    await d.dispatch(_event())
    # 大者先 + 崩溃不阻断其他 listener
    assert order == ["high", "broken", "low"]


@pytest.mark.asyncio
async def test_dispatch_stop_blocks_lower():
    d = EventDispatcher()

    @d.on("message", priority=100, stop=True)
    async def top(event):
        event.stop()

    @d.on("message", priority=50)
    async def low(event):
        raise AssertionError("不应执行（已 stop）")

    await d.dispatch(_event())  # 不抛 = low 未执行


@pytest.mark.asyncio
async def test_shutdown_clears():
    d = EventDispatcher()
    d.on("message")(lambda ev: None)
    assert d.count == 1
    d.shutdown()
    assert d.count == 0
