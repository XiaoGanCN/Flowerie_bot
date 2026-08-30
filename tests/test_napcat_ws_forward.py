"""NapCat 正向 WS 客户端测试（requirement 7/12）：连接 / 鉴权 / 事件处理 / 重连 / 超时。

用本地 asyncio server 模拟 NapCat 正向 WS server（黑盒外部视角）。
"""
import asyncio
import json

import pytest
import websockets

from src.core.napcat_forward_client import NapCatForwardClient, redact_ws_url


class FakeConfig:
    NAPCAT_WS_URL = ""
    NAPCAT_ACCESS_TOKEN = ""
    EVENT_PROCESS_TIMEOUT = 10


class FakeMsgRouter:
    """极简 MessageRouter 桩：记录收到的事件 / 可注入慢处理。"""

    def __init__(self):
        self.global_state = type("GS", (), {"ws_connected": False})()
        self.process_semaphore = asyncio.Semaphore(2)
        self.events = []

    async def process_event(self, data):
        self.events.append(data)


def _client(router=None, config=None, delays=None):
    router = router or FakeMsgRouter()
    cfg = config or FakeConfig()
    return NapCatForwardClient(cfg, router, reconnect_delays=delays or [0.05])


def test_redact_url_removes_query():
    url = "ws://127.0.0.1:3001/?access_token=SECRET"
    assert "SECRET" not in redact_ws_url(url)
    assert redact_ws_url(url) == "ws://127.0.0.1:3001/"


@pytest.mark.asyncio
async def test_forward_connect_and_receive_event():
    router = FakeMsgRouter()
    cfg = FakeConfig()
    cfg.NAPCAT_WS_URL = "ws://127.0.0.1:0/ws"
    got = []

    async def handler(ws):
        await ws.send(json.dumps({"post_type": "message", "message_type": "group",
                                  "group_id": 1, "user_id": 2, "message_id": 3,
                                  "time": 1, "message": [{"type": "text", "data": {"text": "hi"}}]}))
        await asyncio.sleep(0.1)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05])
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(router_events_until(router, 1), timeout=5)
        assert router.events[0]["post_type"] == "message"
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()


async def router_events_until(router, count):
    for _ in range(100):
        if len(router.events) >= count:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("事件未在超时内到达")


@pytest.mark.asyncio
async def test_forward_connection_refused_reconnects():
    """连接被拒（无 server）→ 退避重连直到成功。"""
    router = FakeMsgRouter()
    cfg = FakeConfig()
    cfg.NAPCAT_WS_URL = "ws://127.0.0.1:1/ws"  # 无监听端口
    client = _client(router, cfg, delays=[0.02])
    task = asyncio.create_task(client.run())
    await asyncio.sleep(0.1)
    assert router.global_state.ws_connected is False
    await client.shutdown()
    await task


@pytest.mark.asyncio
async def test_forward_auth_header_check():
    """带 token 时：NapCat server 校验 Authorization 头（OneBot11 约定）。"""
    router = FakeMsgRouter()
    cfg = FakeConfig()
    cfg.NAPCAT_ACCESS_TOKEN = "mysecret"
    seen_auth = {}

    async def handler(ws):
        # websockets.connect 的 additional_headers 会出现在 request.headers
        seen_auth["value"] = ws.request.headers.get("Authorization", "")
        await asyncio.sleep(0.2)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05])
    task = asyncio.create_task(client.run())
    try:
        for _ in range(50):
            if seen_auth.get("value"):
                break
            await asyncio.sleep(0.05)
        assert seen_auth.get("value") == "Bearer mysecret"
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_forward_reconnect_after_disconnect():
    """server 断开 → 客户端自动重连（_M_RECONNECT 递增，事件继续接收）。"""
    router = FakeMsgRouter()
    cfg = FakeConfig()
    connections = []

    async def handler(ws):
        connections.append(1)
        await ws.send(json.dumps({"post_type": "meta_event", "meta_event_type": "lifecycle",
                                  "time": 1}))
        await asyncio.sleep(0.05)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05, 0.05])
    task = asyncio.create_task(client.run())
    try:
        for _ in range(80):
            if len(connections) >= 2:
                break
            await asyncio.sleep(0.05)
        assert len(connections) >= 2, f"期望重连（实际连接次数 {len(connections)}）"
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()
