"""McpClient：最小 MCP（Model Context Protocol）客户端。

协议：MCP over HTTP（JSON-RPC 2.0）。
支持方法：
- initialize：握手
- tools/list：获取工具列表
- tools/call：调用工具

安全边界：
- 工具 allowlist 由 McpToolManager 在调用层强制（本客户端只负责协议）
- 超时 / 取消 / 日志 / 指标 由调用方（ToolManager）负责
"""
import json
from typing import Any, Dict, List, Optional

import httpx

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class McpError(Exception):
    """MCP 协议/调用错误。"""


class McpClient:
    def __init__(self, url: str, name: str = "mcp", timeout: float = 15.0):
        self.url = url
        self.name = name
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._initialized = False

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发送 JSON-RPC 请求，返回 result（非 error）。"""
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        try:
            resp = await self._get_client().post(self.url, headers=headers, json=payload)
        except httpx.TimeoutException as e:
            raise McpError(f"mcp timeout: {e}") from e
        except httpx.HTTPError as e:
            raise McpError(f"mcp http error: {e}") from e
        if resp.status_code != 200:
            raise McpError(f"mcp http {resp.status_code}")
        # MCP 可能返回 SSE 流（单事件）或 JSON
        content_type = resp.headers.get("content-type", "")
        text = resp.text
        data = None
        if "text/event-stream" in content_type:
            # 解析 SSE：取第一个 data: 行
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    break
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise McpError("mcp invalid json response") from e
        if data is None:
            raise McpError("mcp empty response")
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self._session_id = session_id
        if "error" in data and data["error"] is not None:
            err = data["error"]
            raise McpError(f"mcp rpc error: {err.get('message', err)}")
        result = data.get("result", {})
        if not isinstance(result, dict):
            return {"_raw": result}
        return result

    async def initialize(self) -> None:
        """MCP 握手（幂等）。"""
        if self._initialized:
            return
        await self._rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "flowerie-bot", "version": "0.0.1"},
        })
        self._initialized = True

    async def list_tools(self) -> List[Dict[str, Any]]:
        """列出可用工具：[{name, description, inputSchema}]。"""
        await self.initialize()
        result = await self._rpc("tools/list")
        tools = result.get("tools", [])
        return tools if isinstance(tools, list) else []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用工具，返回 result。"""
        await self.initialize()
        return await self._rpc("tools/call", {"name": tool_name, "arguments": arguments})
