"""MCP 插件式多 server 测试（本轮新功能）。

覆盖：
- MCP_SERVERS JSON 解析（多 server / 非法 JSON / 缺字段 / 回退 legacy）
- 多 server 工具聚合与按工具名路由
- 每 server 独立 allowlist 与独立熔断（一个 server 故障不拖垮其他）
- disabled 条目跳过
- MCP_ALLOWED_HOSTS 显式白名单放行本地/内网（SSRF 放宽的受控边界）
- validate_config 对多 server 的 fail-fast 校验
"""
import json

import pytest

from src.config import parse_mcp_servers, validate_config
from src.core.sanitizer import validate_mcp_server_url
from src.services.mcp_client import McpClient, McpError
from src.services.mcp_tool_manager import McpToolManager
from tests.test_config_validation import _cfg
from tests.test_router_regression import make_config


class FakeMcp:
    """可编程假 MCP server：记录调用，可配置失败。"""

    def __init__(self, name, tools):
        self.name = name
        self.tools = tools
        self.calls = []
        self.fail = False

    async def list_tools(self):
        return self.tools

    async def call_tool(self, tool_name, arguments):
        if self.fail:
            raise RuntimeError("server down")
        self.calls.append((tool_name, arguments))
        return {"content": [{"type": "text", "text": f"{self.name}:{tool_name}"}]}

    async def close(self):
        return None


def _tool(name, desc="d"):
    return {"name": name, "description": desc, "inputSchema": {"type": "object"}}


def _multi_config(servers_json, **extra):
    base = dict(
        MCP_ENABLED=True,
        MCP_SERVERS=servers_json,
        MCP_SERVER_URL="",
        MCP_SERVER_NAME="mcp",
        MCP_TIMEOUT=15,
        MCP_MAX_TOOL_CALLS=5,
        MCP_ALLOWED_TOOLS="",
        MCP_ALLOWED_HOSTS=None,
        MCP_CIRCUIT_FAILURES=5,
        MCP_CIRCUIT_PAUSE_SECONDS=60,
    )
    base.update(extra)
    return make_config(**base)


SERVERS_JSON = json.dumps([
    {"name": "srv_a", "url": "https://a.example.com/mcp", "allowed_tools": "search_a, shared"},
    {"name": "srv_b", "url": "https://b.example.com/mcp", "allowed_tools": "search_b, shared"},
])


# ---------- MCP_SERVERS 解析 ----------
def test_parse_mcp_servers_multi():
    servers = parse_mcp_servers(SERVERS_JSON, default_tools="g", default_timeout=15)
    assert [s["name"] for s in servers] == ["srv_a", "srv_b"]
    assert servers[0]["allowed_tools"] == "search_a, shared"
    assert servers[0]["enabled"] is True


def test_parse_mcp_servers_defaults_applied():
    servers = parse_mcp_servers('[{"name":"x","url":"https://x.example.com"}]',
                                default_tools="g_tool", default_timeout=9)
    assert servers[0]["allowed_tools"] == "g_tool"
    assert servers[0]["timeout"] == 9
    assert servers[0]["enabled"] is True


def test_parse_mcp_servers_invalid_json():
    with pytest.raises(ValueError, match="JSON"):
        parse_mcp_servers("{not json")


def test_parse_mcp_servers_missing_fields():
    with pytest.raises(ValueError, match="name/url"):
        parse_mcp_servers('[{"name":"x"}]')


def test_parse_mcp_servers_empty_falls_back_legacy():
    servers = parse_mcp_servers("", legacy_url="https://legacy.example.com", legacy_tools="t1",
                                default_name="legacy")
    assert len(servers) == 1
    assert servers[0]["name"] == "legacy"
    assert servers[0]["url"] == "https://legacy.example.com"
    assert servers[0]["allowed_tools"] == "t1"


# ---------- 多 server 聚合与路由 ----------
def _multi_manager():
    fake_a = FakeMcp("A", [_tool("search_a"), _tool("shared")])
    fake_b = FakeMcp("B", [_tool("search_b"), _tool("shared")])
    mgr = McpToolManager(_multi_config(SERVERS_JSON), clients=[fake_a, fake_b])
    return mgr, fake_a, fake_b


async def test_multi_server_sync_and_payload():
    mgr, fake_a, fake_b = _multi_manager()
    tools = await mgr.sync_tools()
    assert len(tools) == 4  # A:2 + B:2
    payload = mgr.build_tools_payload()
    names = {t["function"]["name"] for t in payload}
    assert names == {"search_a", "search_b", "shared"}  # 每 server 只注入其 allowlist
    await mgr.close()


async def test_multi_server_routing_by_owner():
    mgr, fake_a, fake_b = _multi_manager()
    await mgr.sync_tools()
    r1 = await mgr.call_tool("search_a", {})
    r2 = await mgr.call_tool("search_b", {})
    assert "A:" in r1 and "B:" in r2
    assert fake_a.calls == [("search_a", {})]
    assert fake_b.calls == [("search_b", {})]
    await mgr.close()


async def test_multi_server_per_server_allowlist():
    # shared 只在 B 的 allowlist 里 → 调 shared 走 B
    mgr, fake_a, fake_b = _multi_manager()
    await mgr.sync_tools()
    r = await mgr.call_tool("shared", {})
    assert "B:" in r
    assert fake_b.calls == [("shared", {})]
    assert fake_a.calls == []
    # 不在任何 allowlist 的工具被拒绝
    r2 = await mgr.call_tool("evil_tool", {})
    assert "不在允许列表" in r2
    await mgr.close()


async def test_multi_server_breaker_isolation():
    mgr, fake_a, fake_b = _multi_manager()
    await mgr.sync_tools()
    fake_a.fail = True
    for _ in range(mgr._servers[0].breaker.failure_threshold + 2):
        await mgr.call_tool("search_a", {})
    # A 熔断打开
    assert mgr._servers[0].breaker.state == "OPEN"
    r = await mgr.call_tool("search_a", {})
    assert "熔断" in r
    # B 不受影响，正常工作
    r2 = await mgr.call_tool("search_b", {})
    assert "B:" in r2
    assert mgr._servers[1].breaker.state != "OPEN"
    await mgr.close()


async def test_multi_server_disabled_entry_skipped():
    cfg = _multi_config(json.dumps([
        {"name": "on", "url": "https://on.example.com/mcp", "allowed_tools": "t_on"},
        {"name": "off", "url": "https://off.example.com/mcp", "allowed_tools": "t_off", "enabled": False},
    ]))
    fake = FakeMcp("ON", [_tool("t_on")])
    mgr = McpToolManager(cfg, clients=[fake, None])
    assert len(mgr._servers) == 1  # off 被跳过
    assert mgr._servers[0].name == "on"
    await mgr.close()


# ---------- MCP_ALLOWED_HOSTS：SSRF 受控放宽 ----------
def test_mcp_allowed_hosts_relaxes_loopback():
    assert validate_mcp_server_url("http://127.0.0.1:9000/mcp", ["127.0.0.1"]) == (True, "")
    assert validate_mcp_server_url("http://192.168.1.5:9000/mcp", ["192.168.1.5"]) == (True, "")
    assert validate_mcp_server_url("http://localhost:9000/mcp", ["localhost"]) == (True, "")
    # 未列入白名单 → 仍然拒绝
    ok, reason = validate_mcp_server_url("http://127.0.0.1:9000/mcp", [])
    assert ok is False and "rejected" in reason
    # 白名单不影响 scheme/userinfo 校验
    assert validate_mcp_server_url("ftp://127.0.0.1/x", ["127.0.0.1"])[0] is False


def test_mcp_client_with_allowed_hosts_accepts_local():
    c = McpClient("http://127.0.0.1:9000/mcp", allowed_hosts=["127.0.0.1"])
    assert c.url == "http://127.0.0.1:9000/mcp"
    with pytest.raises(McpError):
        McpClient("http://127.0.0.1:9000/mcp", allowed_hosts=[])


# ---------- validate_config：多 server ----------
def test_validate_config_multi_ok():
    validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS=SERVERS_JSON))  # 不抛异常


def test_validate_config_multi_loopback_requires_whitelist():
    bad = json.dumps([{"name": "local", "url": "http://127.0.0.1:9000/mcp"}])
    with pytest.raises(ValueError, match="MCP_SERVERS"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS=bad))
    # 显式白名单放行后通过
    validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS=bad, MCP_ALLOWED_HOSTS=["127.0.0.1"]))


def test_validate_config_multi_duplicate_names():
    dup = json.dumps([
        {"name": "a", "url": "https://a.example.com"},
        {"name": "a", "url": "https://b.example.com"},
    ])
    with pytest.raises(ValueError, match="重复"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS=dup))


def test_validate_config_multi_invalid_json():
    with pytest.raises(ValueError, match="JSON"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS="{oops"))


def test_validate_config_multi_all_disabled():
    all_off = json.dumps([{"name": "a", "url": "https://a.example.com", "enabled": False}])
    with pytest.raises(ValueError, match="enabled"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS=all_off))


def test_validate_config_multi_empty_array_rejected():
    # MCP_SERVERS=[] 显式声明了多 server 配置但没有可用 server → fail-fast
    with pytest.raises(ValueError, match="enabled"):
        validate_config(_cfg(MCP_ENABLED=True, MCP_SERVERS="[]"))


def test_validate_config_legacy_loopback_with_allowed_hosts():
    validate_config(_cfg(MCP_ENABLED=True, MCP_SERVER_URL="http://127.0.0.1:9000/mcp",
                         MCP_ALLOWED_HOSTS=["127.0.0.1"]))
