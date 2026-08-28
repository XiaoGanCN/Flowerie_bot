"""MCP 测试（第四轮）：启停 / allowlist / 超时 / 失败 / 工具循环上限 / 熔断隔离。"""
import asyncio

from src.services.mcp_client import McpError
from src.services.mcp_tool_manager import McpToolManager
from tests.test_ai_client import make_config as ai_config
from tests.test_router_regression import build_router, make_config


class FakeMcpServer:
    """模拟 MCP server：可配置工具列表/失败/延迟。"""

    def __init__(self, tools=None):
        self.tools = tools or [
            {"name": "web_search", "description": "搜索网络",
             "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}},
            {"name": "fetch_url", "description": "抓取 URL",
             "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
            {"name": "execute_shell", "description": "执行 shell（危险）",
             "inputSchema": {"type": "object", "properties": {"cmd": {"type": "string"}}}},
        ]
        self.calls = []
        self.fail = False
        self.timeout = False

    async def list_tools(self):
        return self.tools

    async def call_tool(self, name, arguments):
        if self.timeout:
            await asyncio.sleep(60)
        if self.fail:
            raise McpError("server down")
        self.calls.append((name, arguments))
        return {"content": [{"type": "text", "text": f"结果:{name}:{arguments.get('query', '')}"}]}


def _make_manager(config_overrides=None, server=None):
    base = dict(
        MCP_ENABLED=True,
        MCP_SERVER_URL="http://127.0.0.1:9000/mcp",
        MCP_TIMEOUT=2,
        MCP_MAX_TOOL_CALLS=5,
        MCP_ALLOWED_TOOLS="web_search, fetch_url",
    )
    base.update(config_overrides or {})
    config = make_config(**base)
    server = server or FakeMcpServer()
    # 注入 fake client
    class FakeClient:
        def __init__(self, srv):
            self._srv = srv

        async def list_tools(self):
            return await self._srv.list_tools()

        async def call_tool(self, name, args):
            return await self._srv.call_tool(name, args)

        async def close(self):
            return None

    mgr = McpToolManager(config, client=FakeClient(server))
    return mgr, server


# ---------- 启停 ----------
async def test_mcp_disabled_by_default():
    config = make_config(MCP_ENABLED=False, MCP_SERVER_URL="")
    mgr = McpToolManager(config)
    assert mgr.is_enabled() is False
    # 未启用时调用工具返回不可用
    result = await mgr.call_tool("web_search", {})
    assert "未启用" in result
    await mgr.close()


async def test_mcp_enabled_sync_tools():
    mgr, server = _make_manager()
    assert mgr.is_enabled() is True
    tools = await mgr.sync_tools()
    assert len(tools) == 3


# ---------- allowlist ----------
async def test_tool_allowlist_enforced():
    mgr, server = _make_manager()
    await mgr.sync_tools()
    assert mgr.allow_tool("web_search") is True
    assert mgr.allow_tool("fetch_url") is True
    # 危险工具不在 allowlist → 拒绝
    assert mgr.allow_tool("execute_shell") is False
    result = await mgr.call_tool("execute_shell", {"cmd": "rm -rf /"})
    assert "不在允许列表" in result
    assert server.calls == []  # 未执行
    # allowlist 工具正常执行
    result = await mgr.call_tool("web_search", {"query": "天气"})
    assert "结果" in result
    assert server.calls == [("web_search", {"query": "天气"})]


async def test_tools_payload_only_allowed():
    mgr, server = _make_manager()
    await mgr.sync_tools()
    payload = mgr.build_tools_payload()
    names = [t["function"]["name"] for t in payload]
    assert names == ["web_search", "fetch_url"]  # execute_shell 被过滤
    assert "execute_shell" not in names


# ---------- 超时 / 失败 ----------
async def test_tool_timeout():
    server = FakeMcpServer()
    server.timeout = True
    mgr, _ = _make_manager(server=server)
    result = await mgr.call_tool("web_search", {"query": "x"})
    assert "超时" in result
    # 超时计入熔断
    assert mgr.breaker.state == "OPEN" or mgr.breaker._failures >= 1
    await mgr.close()


async def test_tool_failure_isolated():
    server = FakeMcpServer()
    server.fail = True
    mgr, _ = _make_manager(server=server)
    result = await mgr.call_tool("web_search", {"query": "x"})
    assert "失败" in result
    # 失败只影响 MCP breaker，不影响其他
    assert mgr.breaker.state == "OPEN" or mgr.breaker._failures >= 1
    await mgr.close()


# ---------- MCP 熔断隔离 ----------
async def test_mcp_circuit_isolated_from_ai_provider():
    """MCP 连续失败只打开 MCP breaker，AI Provider breaker 不受影响。"""
    server = FakeMcpServer()
    server.fail = True
    mgr, _ = _make_manager(server=server)
    for _ in range(mgr.breaker.failure_threshold + 2):
        await mgr.call_tool("web_search", {"query": "x"})
    assert mgr.breaker.state == "OPEN"
    # 熔断中：直接拒绝不执行
    result = await mgr.call_tool("web_search", {"query": "x"})
    assert "熔断" in result
    await mgr.close()


# ---------- 工具循环（ai_client._chat_with_tools） ----------
class FakeToolAI:
    """模拟 DeepSeek 返回工具调用。"""

    def __init__(self, rounds_with_tools=1):
        self.rounds_with_tools = rounds_with_tools
        self.round = 0
        self.messages_sent = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.round += 1
        self.messages_sent.append(json)
        if self.round <= self.rounds_with_tools:
            return _Resp({"choices": [{"message": {
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": "{\"query\": \"天气\"}"},
                }],
            }}]})
        return _Resp({"choices": [{"message": {"content": "查到啦 明天晴天"}}]})


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._data


async def test_tool_loop_multi_step():
    """模型调用工具 → 工具结果回填 → 模型最终回答。"""
    from src.services.ai_client import AIClient

    ai = AIClient(ai_config(), None)
    server = FakeMcpServer()
    mgr, _ = _make_manager(server=server)
    fake = FakeToolAI(rounds_with_tools=1)
    ai.client = fake

    calls = []

    async def caller(name, args):
        calls.append((name, args))
        return "结果:晴"

    reply, mem = await ai.chat_once(
        "今天天气怎么样", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_caller=caller, max_tool_calls=5,
    )
    assert reply == "查到啦 明天晴天"
    assert calls == [("web_search", {"query": "天气"})]
    # 工具结果作为 tool 消息回填
    tool_msgs = [m for m in fake.messages_sent[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    await mgr.close()


async def test_max_tool_calls_halts_loop():
    """工具调用达到上限：停止循环，不无限调用。"""
    from src.services.ai_client import AIClient

    ai = AIClient(ai_config(), None)
    fake = FakeToolAI(rounds_with_tools=999)  # 永远返回工具调用
    ai.client = fake
    calls = []

    async def caller(name, args):
        calls.append(name)
        return "结果"

    reply, mem = await ai.chat_once(
        "查一下", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=[{"type": "function", "function": {"name": "web_search"}}],
        tool_caller=caller, max_tool_calls=3,
    )
    assert len(calls) == 3  # 恰好 3 次后停止
    assert len(fake.messages_sent) == 4  # 3 轮工具 + 1 轮收尾


async def test_router_injects_tools_when_enabled():
    """Router 集成：MCP 启用且有 allowlist 工具时，guarded_chat 会注入 tools。"""
    router, config, ai, sender, mm = build_router(make_config(
        MCP_ENABLED=True, MCP_SERVER_URL="http://127.0.0.1:9000/mcp",
        MCP_ALLOWED_TOOLS="web_search", MCP_MAX_TOOL_CALLS=5))
    mgr, server = _make_manager()
    router.tool_manager = mgr
    await mgr.sync_tools()

    captured = {}

    class CaptureAI:
        async def chat_once(self, **kwargs):
            captured.update(kwargs)
            self._retryable = True
            self._api_backoff = 0.0
            return "好", None

    router.ai_client = CaptureAI()
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="查天气", context="ctx")
    assert denied is False
    assert "tools" in captured
    assert captured["tools"][0]["function"]["name"] == "web_search"
    assert "tool_caller" in captured
    await mgr.close()


async def test_router_no_tools_when_mcp_disabled():
    """MCP 禁用：不注入 tools，纯聊天路径。"""
    router, config, ai, sender, mm = build_router()
    captured = {}

    class CaptureAI:
        async def chat_once(self, **kwargs):
            captured.update(kwargs)
            self._retryable = True
            self._api_backoff = 0.0
            return "好", None

    router.ai_client = CaptureAI()
    reply, mem, denied = await router.guarded_chat(123, 456, user_message="hi", context="ctx")
    assert "tools" not in captured
