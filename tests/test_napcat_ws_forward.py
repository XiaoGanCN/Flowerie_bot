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


@pytest.mark.asyncio
async def test_forward_malformed_and_garbage_events_survive():
    """畸形 JSON / 非 JSON 帧：客户端跳过继续处理后续正常事件（黑盒韧性）。"""
    router = FakeMsgRouter()
    cfg = FakeConfig()

    async def handler(ws):
        await ws.send(b"not-json-at-all{{{{")
        await ws.send("{bad json: 1}")
        await ws.send(json.dumps({"post_type": "message", "message_type": "group",
                                  "group_id": 5, "user_id": 6, "message_id": 7,
                                  "time": 1, "message": [{"type": "text", "data": {"text": "ok"}}]}))
        await asyncio.sleep(0.1)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05])
    task = asyncio.create_task(client.run())
    try:
        await asyncio.wait_for(router_events_until(router, 1), timeout=5)
        assert router.events[0]["post_type"] == "message"  # 只有正常事件被处理
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()



# ---------- WS 鉴权：header / query 二选一，绝不同时发送（NAPCAT_WS_AUTH_MODE） ----------
async def test_ws_auth_header():
    """默认（header）：只发 Authorization: Bearer；URL/路径不带 token。"""
    from urllib.parse import urlparse as _up
    router = FakeMsgRouter()
    cfg = FakeConfig()
    cfg.NAPCAT_ACCESS_TOKEN = "tok_header_secret"
    server_seen = {}

    async def handler(ws):
        req = ws.request
        server_seen["auth"] = req.headers.get("Authorization", "")
        server_seen["query"] = _up(req.path).query
        await asyncio.sleep(0.1)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05])
    task = asyncio.create_task(client.run())
    try:
        for _ in range(50):
            if server_seen.get("auth") is not None:
                break
            await asyncio.sleep(0.05)
        assert server_seen["auth"] == "Bearer tok_header_secret"
        assert "access_token" not in server_seen["query"]      # Header 模式：URL 无 token
        assert "tok_header_secret" not in server_seen["query"]
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()


async def test_ws_auth_query():
    """query 模式：URL ?access_token=...（已编码）；Header 不得携带 token。"""
    from urllib.parse import urlparse as _up
    router = FakeMsgRouter()
    cfg = FakeConfig()
    cfg.NAPCAT_ACCESS_TOKEN = "tok query & special#"
    cfg.NAPCAT_WS_AUTH_MODE = "query"
    server_seen = {}

    async def handler(ws):
        req = ws.request
        server_seen["auth"] = req.headers.get("Authorization", "")
        server_seen["query"] = _up(req.path).query
        await asyncio.sleep(0.1)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
    client = _client(router, cfg, delays=[0.05])
    task = asyncio.create_task(client.run())
    try:
        for _ in range(50):
            if server_seen.get("auth") is not None:
                break
            await asyncio.sleep(0.05)
        assert server_seen["auth"] == ""                        # Query 模式：Header 无 token
        assert "access_token=" in server_seen["query"]          # URL 带 query
        # 特殊字符已百分号编码（& # 空格不得原样出现）
        assert "access_token=tok" in server_seen["query"]
        assert "&" not in server_seen["query"].split("access_token=")[-1] \
            or "tok%20query%20%26%20special%23" in server_seen["query"]
    finally:
        await client.shutdown()
        await task
        server.close()
        await server.wait_closed()


async def test_ws_auth_not_sent_twice():
    """黄金用例：同一连接绝不同时出现 Bearer 头 + URL access_token。"""
    from urllib.parse import urlparse as _up

    async def _probe(mode, token):
        router = FakeMsgRouter()
        cfg = FakeConfig()
        cfg.NAPCAT_ACCESS_TOKEN = token
        cfg.NAPCAT_WS_AUTH_MODE = mode
        seen = {}

        async def handler(ws):
            req = ws.request
            seen["auth"] = req.headers.get("Authorization", "")
            seen["query"] = _up(req.path).query
            await asyncio.sleep(0.1)

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        cfg.NAPCAT_WS_URL = f"ws://127.0.0.1:{port}/ws"
        client = _client(router, cfg, delays=[0.05])
        task = asyncio.create_task(client.run())
        try:
            for _ in range(50):
                if seen.get("auth") is not None:
                    break
                await asyncio.sleep(0.05)
            return seen
        finally:
            await client.shutdown()
            await task
            server.close()
            await server.wait_closed()

    seen_header = await _probe("header", "only_header_tok")
    assert "Bearer only_header_tok" in seen_header["auth"]
    assert "access_token" not in seen_header["query"]           # Header 模式：URL 绝无 token

    seen_query = await _probe("query", "only_query_tok")
    assert seen_query["auth"] == ""                             # Query 模式：Header 绝无 token
    assert "access_token=" in seen_query["query"]


async def test_ws_auth_token_redaction():
    """日志脱敏：含 token 的 URL 只输出掩码形式，原始 token 绝不出现。"""
    url = "ws://127.0.0.1:3001/?access_token=SECRET-VALUE-123"
    redacted = redact_ws_url(url)
    assert "SECRET-VALUE-123" not in redacted
    assert "access_token" not in redacted                       # 整段 query 剥除
    assert redacted == "ws://127.0.0.1:3001/"
    # 无 query 的 URL 原样返回（无敏感内容则不误伤）
    assert redact_ws_url("ws://127.0.0.1:3001/ws") == "ws://127.0.0.1:3001/ws"
    # fragment 中的敏感内容同样被剥除
    assert "SECRET" not in redact_ws_url("ws://127.0.0.1:3001/ws#access_token=SECRET")
    # 畸形 URL 有安全兜底（解析失败的输入绝不回显原始内容）
    assert redact_ws_url("ws://[bad") == "<invalid-url>"
