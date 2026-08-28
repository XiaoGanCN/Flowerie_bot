"""McpToolManager：MCP 工具的准入/执行/隔离。

安全边界：
- 默认 MCP_ENABLED=false；只有管理员配置后启用
- 工具 allowlist（MCP_ALLOWED_TOOLS）：不在列表中的工具一律拒绝
- 单次调用超时（MCP_TIMEOUT）、单轮对话调用上限（MCP_MAX_TOOL_CALLS）
- 独立熔断（MCP 故障不打开 AI Provider 熔断）
- 指标：mcp_calls_total / mcp_call_failures_total / mcp_call_latency_seconds /
  mcp_tool_rejections_total（低 cardinality label：tool/result/reason）
"""
import asyncio
import time
from typing import Any, Dict, List, Optional

from src.config import Settings
from src.services.mcp_client import McpClient, McpError
from src.utils.circuit_breaker import CircuitBreaker
from src.utils.logging_setup import get_logger
from src.utils.metrics import registry

logger = get_logger(__name__)

_M_CALLS = registry.counter("mcp_calls_total", "MCP 工具调用总数", ["tool"])
_M_FAILS = registry.counter("mcp_call_failures_total", "MCP 工具调用失败数", ["tool"])
_M_LATENCY = registry.histogram("mcp_call_latency_seconds", "MCP 工具调用耗时（秒）")
_M_REJECT = registry.counter("mcp_tool_rejections_total", "MCP 工具拒绝数", ["reason"])


class McpToolManager:
    """MCP 工具管理器：allowlist 准入 + 执行 + 独立熔断。"""

    def __init__(self, config: Settings, client: Optional[McpClient] = None):
        self.config = config
        self.client = client or (
            McpClient(getattr(config, "MCP_SERVER_URL", ""), getattr(config, "MCP_SERVER_NAME", "mcp"),
                     timeout=max(1, getattr(config, "MCP_TIMEOUT", 15)))
            if getattr(config, "MCP_ENABLED", False) and getattr(config, "MCP_SERVER_URL", "") else None
        )
        self.allowlist: List[str] = [
            t.strip() for t in (getattr(config, "MCP_ALLOWED_TOOLS", "") or "").split(",") if t.strip()
        ]
        self.breaker = CircuitBreaker(
            name="mcp",
            failure_threshold=max(1, int(getattr(config, "MCP_CIRCUIT_FAILURES", 5))),
            cooldown_seconds=max(5, int(getattr(config, "MCP_CIRCUIT_PAUSE_SECONDS", 60))),
        )
        self._tool_schemas: Dict[str, Dict[str, Any]] = {}

    # ---------- 状态 ----------
    def is_enabled(self) -> bool:
        return self.client is not None

    def allow_tool(self, tool_name: str) -> bool:
        return tool_name in self.allowlist

    # ---------- 工具元数据 ----------
    async def sync_tools(self) -> List[Dict[str, Any]]:
        """拉取 MCP 工具列表（失败不阻塞聊天）。"""
        if not self.is_enabled():
            return []
        try:
            tools = await self.client.list_tools()
            self._tool_schemas = {t.get("name", ""): t for t in tools if t.get("name")}
            return tools
        except (McpError, Exception) as e:  # noqa: BLE001 - 工具同步失败不影响聊天
            logger.warning("mcp tools sync failed: %s", e)
            return []

    def build_tools_payload(self) -> List[Dict[str, Any]]:
        """构造发送给模型的 OpenAI 风格 tools 参数（仅 allowlist 内工具）。"""
        tools = []
        for name, schema in self._tool_schemas.items():
            if name not in self.allowlist:
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": schema.get("description", ""),
                    "parameters": schema.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })
        return tools

    # ---------- 执行 ----------
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """执行工具（allowlist 准入 → 熔断 → 超时执行），返回文本结果。"""
        if not self.is_enabled():
            _M_REJECT.inc({"reason": "disabled"})
            return "工具不可用（MCP 未启用）"
        if not self.allow_tool(tool_name):
            _M_REJECT.inc({"reason": "not_allowed"})
            logger.warning("mcp_tool_rejected tool=%s", tool_name, extra={"event": "mcp_tool_rejected"})
            return f"工具 {tool_name} 不在允许列表"
        if not self.breaker.allow():
            _M_REJECT.inc({"reason": "circuit_open"})
            return "工具服务暂时不可用（熔断中）"
        started = time.monotonic()
        _M_CALLS.inc({"tool": tool_name})
        try:
            result = await asyncio.wait_for(
                self.client.call_tool(tool_name, arguments), timeout=max(1, getattr(self.config, "MCP_TIMEOUT", 15))
            )
            self.breaker.record_success()
            _M_LATENCY.observe(time.monotonic() - started)
            content = result.get("content", [])
            if isinstance(content, list):
                return " ".join(str(c.get("text", "")) for c in content if isinstance(c, dict)) or str(result)[:500]
            return str(result)[:500]
        except asyncio.TimeoutError:
            self.breaker.record_failure()
            _M_FAILS.inc({"tool": tool_name})
            logger.warning("mcp_call_failed tool=%s err=timeout", tool_name, extra={"event": "mcp_call_failed"})
            return "工具调用超时"
        except (McpError, Exception) as e:  # noqa: BLE001 - 单次工具失败隔离
            self.breaker.record_failure()
            _M_FAILS.inc({"tool": tool_name})
            logger.warning("mcp_call_failed tool=%s err=%s", tool_name, e, extra={"event": "mcp_call_failed"})
            return f"工具调用失败：{e}"

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
