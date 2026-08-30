"""Web UI MCP 域处理器（多 server 卡片式编辑/测试）。

从 WebUIServer 拆分（防上帝类）：MCP_SERVERS 结构化读写 + 连通性测试。
"""
import json
import re
from typing import Dict, List, Tuple
from urllib.parse import quote

from aiohttp import web

from src.utils.logging_setup import get_logger

logger = get_logger(__name__)


class McpPanelMixin:

    def _read_mcp_servers(self) -> List[dict]:
        raw = self.config_service.get_value("MCP_SERVERS") or ""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []

    def _save_mcp_servers(self, servers: List[dict]) -> Tuple[bool, str]:
        js = json.dumps(servers, ensure_ascii=False, separators=(",", ":"))
        ok, msg = self.config_service.update("MCP_SERVERS", js)
        return ok, msg

    def _mcp_tool_counts(self) -> Dict[str, int]:
        """{server_name: 已同步工具数}，用于卡片显示真实工具数量。"""
        counts: Dict[str, int] = {}
        if self._tool_manager is None:
            return counts
        try:
            for s in getattr(self._tool_manager, "_servers", []) or []:
                counts[getattr(s, "name", "")] = len(getattr(s, "schemas", {}) or {})
        except Exception:  # noqa: BLE001
            pass
        return counts

    def _get_mcp_test_status(self) -> Dict[str, tuple]:
        """{server_name: (ok, msg)} 最近一次测试结果（用于卡片显示）。"""
        out: Dict[str, tuple] = {}
        for k, v in self.config_service.repository.list_prefs():
            if k.startswith("mcp_test_"):
                name = k[len("mcp_test_"):]
                ok_str, _, msg = v.partition("|")
                out[name] = (ok_str == "ok", msg)
        return out

    @staticmethod
    def _mcp_server_error(name: str, url: str, tools: str) -> str:
        if not name:
            return "名称必填"
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
            return "名称只能含字母/数字/点/横线/下划线"
        if not url:
            return "地址必填"
        if not re.match(r"^(https?|sse)://", url):
            return "地址需以 http:// https:// 或 sse:// 开头"
        for token in (t.strip() for t in tools.split(",") if t.strip()):
            if not re.fullmatch(r"[A-Za-z0-9_.\-]+", token):
                return f"工具名非法: {token}"
        return ""

    async def _mcp_ping(self, url: str, timeout: int) -> Tuple[bool, str]:
        """测试 MCP server 连通性：POST MCP initialize 握手。"""
        if not re.match(r"^(https?|sse)://", url or ""):
            return False, "地址需以 http:// https:// 或 sse:// 开头"
        import aiohttp as _aiohttp
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "flowerie", "version": "1.3.0"}},
        }
        try:
            async with _aiohttp.ClientSession() as sess:
                async with sess.post(url, json=payload,
                                     timeout=_aiohttp.ClientTimeout(total=max(1, min(timeout, 30)))) as resp:
                    if resp.status >= 400:
                        return False, f"HTTP {resp.status}，连接失败"
                    try:
                        data = await resp.json()
                    except Exception:  # noqa: BLE001
                        return False, f"HTTP {resp.status} 返回非 JSON"
                    info = data.get("result", {}).get("serverInfo", {}) if isinstance(data, dict) else {}
                    server = info.get("name", "MCP") if isinstance(info, dict) else "MCP"
                    version = info.get("version", "") if isinstance(info, dict) else ""
                    return True, f"连接成功：{server} {version}".strip()
        except Exception as e:  # noqa: BLE001
            return False, f"连接失败：{e}"

    async def _handle_panel_mcp_edit(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        action = str(form.get("mcp_action", "") or "save").strip()
        servers = self._read_mcp_servers()
        try:
            index = int(form.get("mcp_index", ""))
        except (TypeError, ValueError):
            index = None
        if action == "delete":
            if index is not None and 0 <= index < len(servers):
                servers.pop(index)
            ok, msg = self._save_mcp_servers(servers)
            return web.HTTPFound(f"/panel?cat=MCP&msg={quote(msg)}&err={'1' if not ok else ''}")
        if action == "toggle":
            if index is not None and 0 <= index < len(servers):
                servers[index]["enabled"] = not bool(servers[index].get("enabled", True))
            ok, msg = self._save_mcp_servers(servers)
            state = "已启用" if servers[index].get("enabled") else "已停用"
            return web.HTTPFound(f"/panel?cat=MCP&msg={quote(servers[index]['name'] + ' ' + state)}&err={'1' if not ok else ''}")
        if action == "test":
            if index is not None and 0 <= index < len(servers):
                tgt = servers[index]
                ok, msg = await self._mcp_ping(str(tgt.get("url", "")), int(tgt.get("timeout", 15) or 15))
                # 保存测试结果，卡片上直接显示（连接成功/失败）
                self._set_pref(f"mcp_test_{tgt.get('name', index)}", ("ok" if ok else "err") + "|" + msg)
                return web.HTTPFound(f"/panel?cat=MCP&msg={quote(msg)}&err={'1' if not ok else ''}")
            return web.HTTPFound("/panel?cat=MCP&msg=" + quote("未找到该服务器") + "&err=1")
        # 添加 / 保存
        name = str(form.get("mcp_name", "") or "").strip()
        url = str(form.get("mcp_url", "") or "").strip()
        tools = ",".join(t.strip() for t in str(form.get("mcp_tools", "") or "").split(",") if t.strip())
        try:
            timeout = int(form.get("mcp_timeout", "15") or 15)
            if timeout < 1:
                timeout = 15
        except (TypeError, ValueError):
            timeout = 15
        enabled = bool(form.get("mcp_enabled", "")) if not hasattr(form, "getall") else "1" in form.getall("mcp_enabled")
        err = self._mcp_server_error(name, url, tools)
        if err:
            return web.HTTPFound(f"/panel?cat=MCP&msg={quote(err)}&err=1")
        new_srv = {"name": name, "url": url, "allowed_tools": tools, "timeout": timeout, "enabled": enabled}
        if action in ("save", "edit") and index is not None and 0 <= index < len(servers):
            servers[index] = new_srv
            local_msg = f"MCP 服务器「{name}」已更新（重启后生效）"
        else:
            if any(s.get("name") == name for s in servers):
                return web.HTTPFound(f"/panel?cat=MCP&msg={quote('名称已存在：' + name)}&err=1")
            servers.append(new_srv)
            local_msg = f"已添加 MCP 服务器「{name}」（重启后生效）"
        ok, msg = self._save_mcp_servers(servers)
        if not ok:
            return web.HTTPFound(f"/panel?cat=MCP&msg={quote(msg)}&err=1")
        return web.HTTPFound(f"/panel?cat=MCP&msg={quote(local_msg)}")
