"""MCP SSRF / Redirect / DNS 安全测试（P0 修复）。

覆盖清单：
- public → public   PASS
- public → localhost（DNS rebinding） BLOCK
- public → private IP  BLOCK
- allowed private（MCP_ALLOWED_HOSTS） PASS
- IPv6 private  BLOCK
- IPv4-mapped IPv6  BLOCK
- redirect chain：客户端显式不跟随重定向，3xx 直接拒绝
- userinfo / 编码绕过：构造即拒绝
"""
import asyncio

import httpx
import pytest

from src.core.sanitizer import validate_mcp_resolved_ips, validate_mcp_server_url
from src.services.mcp_client import McpClient, McpError


def run(coro):
    return asyncio.run(coro)


# ---------- DNS 解析结果策略（纯函数） ----------
def test_dns_policy_public_ok():
    ok, reason = validate_mcp_resolved_ips("mcp.example.com", ["93.184.216.34", "2606:2800:220:1::1"], [])
    assert ok is True, reason


def test_dns_policy_loopback_blocked():
    ok, reason = validate_mcp_resolved_ips("mcp.example.com", ["93.184.216.34", "127.0.0.1"], [])
    assert ok is False and "127.0.0.1" in reason


def test_dns_policy_private_ipv4_blocked():
    for ip in ("192.168.1.5", "10.0.0.1", "172.16.0.1", "172.31.255.255", "169.254.1.1"):
        ok, _ = validate_mcp_resolved_ips("mcp.example.com", [ip], [])
        assert ok is False, ip


def test_dns_policy_ipv6_private_blocked():
    for ip in ("::1", "fc00::1", "fd12:3456::1", "fe80::1"):
        ok, _ = validate_mcp_resolved_ips("mcp.example.com", [ip], [])
        assert ok is False, ip


def test_dns_policy_ipv4_mapped_ipv6_blocked():
    # ::ffff:127.0.0.1 → 按 127.0.0.1 判定（回环）
    ok, reason = validate_mcp_resolved_ips("mcp.example.com", ["::ffff:127.0.0.1"], [])
    assert ok is False
    # ::ffff:192.168.1.1 → 按 192.168.1.1 判定（私网）
    ok, _ = validate_mcp_resolved_ips("mcp.example.com", ["::ffff:192.168.1.1"], [])
    assert ok is False


def test_dns_policy_allowed_host_explicit_pass():
    # MCP_ALLOWED_HOSTS 显式放行：私网/回环主机可连接
    ok, _ = validate_mcp_resolved_ips("localhost", ["127.0.0.1"], ["localhost"])
    assert ok is True
    ok, _ = validate_mcp_resolved_ips("192.168.1.10", ["192.168.1.10"], ["192.168.1.10"])
    assert ok is True
    # 未显式放行仍拒绝
    ok, _ = validate_mcp_resolved_ips("192.168.1.10", ["192.168.1.10"], [])
    assert ok is False
    # 白名单只对列出的主机生效，不横向放宽
    ok, _ = validate_mcp_resolved_ips("mcp.example.com", ["192.168.1.10"], ["localhost"])
    assert ok is False


def test_dns_policy_empty_resolution_blocked():
    ok, _ = validate_mcp_resolved_ips("mcp.example.com", [], [])
    assert ok is False


# ---------- McpClient 集成：DNS 校验 ----------
class _FakeDNSClient(McpClient):
    def __init__(self, url, ips, allowed=None):
        super().__init__(url, allowed_hosts=allowed)
        self._ips = list(ips)

    async def _resolve_ips(self, host, port):
        return list(self._ips)


def test_client_dns_rebinding_blocked():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["127.0.0.1"])
    with pytest.raises(McpError, match="DNS"):
        run(c._check_dns())


def test_client_dns_public_ok():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["93.184.216.34"])
    run(c._check_dns())  # 不抛


def test_client_dns_private_ipv4_blocked():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["192.168.1.5"])
    with pytest.raises(McpError):
        run(c._check_dns())


def test_client_dns_private_ipv6_blocked():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["fc00::1"])
    with pytest.raises(McpError):
        run(c._check_dns())


def test_client_dns_mapped_ipv6_blocked():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["::ffff:127.0.0.1"])
    with pytest.raises(McpError):
        run(c._check_dns())


def test_client_dns_allowed_host_pass():
    c = _FakeDNSClient("http://127.0.0.1:9000/mcp", ["127.0.0.1"], allowed=["127.0.0.1"])
    run(c._check_dns())  # 显式白名单放行


def test_client_dns_resolve_failure_blocked():
    class _FailDNS(McpClient):
        async def _resolve_ips(self, host, port):
            raise OSError("NXDOMAIN")
    c = _FailDNS("https://mcp.example.com/mcp")
    with pytest.raises(McpError, match="resolve"):
        run(c._check_dns())


# ---------- Redirect：显式不跟随，3xx 直接拒绝 ----------
def test_client_explicitly_does_not_follow_redirects():
    c = _FakeDNSClient("https://mcp.example.com/mcp", ["93.184.216.34"])
    assert c._get_client().follow_redirects is False


def test_client_redirect_chain_rejected():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9000/mcp"})

    c = _FakeDNSClient("https://mcp.example.com/mcp", ["93.184.216.34"])
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(McpError, match="302"):
        run(c._rpc("tools/list"))
    run(c.close())


def test_client_redirect_to_private_never_reached():
    """即使服务端返回 302 → 127.0.0.1，客户端也不跟随（跳转目标不会被执行）。"""
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9000/mcp"})

    c = _FakeDNSClient("https://mcp.example.com/mcp", ["93.184.216.34"])
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    with pytest.raises(McpError):
        run(c._rpc("tools/list"))
    run(c.close())


# ---------- 构造即拒绝：userinfo / 编码 / 非法 scheme ----------
@pytest.mark.parametrize("url", [
    "http://user:pass@mcp.example.com/mcp",
    "https://admin@mcp.example.com/mcp",
    "ftp://mcp.example.com/mcp",
    "file:///etc/passwd",
    "http://",
    "javascript:alert(1)",
])
def test_url_rejected_at_construction(url):
    ok, _ = validate_mcp_server_url(url)
    assert ok is False
