"""插件 http_request action 的受限 HTTP 客户端（继承 MCP 同款 SSRF 防线）。

- 仅 http/https + 无 userinfo + 回环/私网/链路本地/组播/保留/0.0.0.0/.local 拒绝
- DNS 解析结果再校验（防 DNS rebinding）
- 不跟随重定向（3xx 判失败，防 redirect 到内网）
- 仅 GET / POST；请求体与响应体都有大小上限；响应体截断返回
- 日志不记录任何 URL 查询串（防 access_token 泄漏）
"""
import asyncio
from typing import Any, Dict
from urllib.parse import urlsplit, urlunsplit

from src.core.sanitizer import validate_mcp_resolved_ips, validate_mcp_server_url
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

ALLOWED_METHODS = ("GET", "POST")
DEFAULT_MAX_BODY_BYTES = 256 * 1024      # 请求体上限 256KB
DEFAULT_MAX_RESPONSE_BYTES = 256 * 1024  # 响应体上限 256KB
DEFAULT_TIMEOUT = 10.0


def redact_url(url: str) -> str:
    """去除 URL 查询串（access_token 等敏感参数绝不进日志/UI）。"""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
    except ValueError:
        return "<invalid-url>"


class PluginHttpError(Exception):
    pass


async def _check_dns(parts) -> None:
    host = (parts.hostname or "").lower()
    if not host:
        raise PluginHttpError("host missing")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port)
    except Exception as e:  # noqa: BLE001
        raise PluginHttpError(f"dns resolve failed: {type(e).__name__}") from e
    ips = [info[4][0] for info in infos]
    ok, reason = validate_mcp_resolved_ips(host, ips, [])
    if not ok:
        raise PluginHttpError(f"dns rejected: {reason}")


async def plugin_http_request(payload: Dict[str, Any],
                              max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
                              timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """执行受限 HTTP 请求。payload: {url, method?, headers?, body?, json?}"""
    import httpx

    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload 必须是对象"}
    url = str(payload.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "url 为空"}
    method = str(payload.get("method") or "GET").upper()
    if method not in ALLOWED_METHODS:
        return {"ok": False, "error": f"method 仅支持 {', '.join(ALLOWED_METHODS)}"}
    ok, reason = validate_mcp_server_url(url)
    if not ok:
        return {"ok": False, "error": f"SSRF 防护拒绝: {reason}"}
    parts = urlsplit(url)
    try:
        await _check_dns(parts)
    except PluginHttpError as e:
        return {"ok": False, "error": f"SSRF 防护拒绝: {e}"}
    headers = payload.get("headers") if isinstance(payload.get("headers"), dict) else {}
    clean_headers = {}
    for k, v in headers.items():
        k = str(k)[:64]
        if k.lower() in ("host", "authorization", "proxy-authorization", "cookie") \
                or k.lower().startswith("x-"):
            continue  # 禁止伪造 Host / 传递敏感头
        clean_headers[k] = str(v)[:256]
    body = payload.get("body")
    json_data = payload.get("json")
    if json_data is not None and not isinstance(json_data, (dict, list)):
        return {"ok": False, "error": "json 必须是对象或数组"}
    if body is not None and len(str(body)) > DEFAULT_MAX_BODY_BYTES:
        return {"ok": False, "error": f"请求体超过 {DEFAULT_MAX_BODY_BYTES} 字节"}
    try:
        async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout, connect=min(timeout, 5.0)),
                follow_redirects=False) as client:
            if method == "POST":
                req = client.post(url, headers=clean_headers,
                                  content=body if body is not None else None,
                                  json=json_data)
            else:
                req = client.get(url, headers=clean_headers)
            async with req as resp:
                if resp.status_code >= 300:
                    return {"ok": False, "error": f"HTTP {resp.status_code}（重定向一律拒绝）",
                            "status": resp.status_code}
                buf = bytearray()
                async for chunk in resp.aiter_bytes(16 * 1024):
                    buf.extend(chunk)
                    if len(buf) > max_response_bytes:
                        return {"ok": False, "error": f"响应超过 {max_response_bytes} 字节（已截断）",
                                "status": resp.status_code, "truncated": True}
                text = buf.decode("utf-8", errors="replace")
                result: Dict[str, Any] = {
                    "ok": True,
                    "status": resp.status_code,
                    "content_type": (resp.headers.get("content-type") or "")[:64],
                    "body": text[:max_response_bytes],
                    "truncated": len(text) > max_response_bytes,
                }
                if resp.status_code == 200 and method == "GET":
                    result["redirected"] = False
                return result
    except httpx.TimeoutException:
        return {"ok": False, "error": f"请求超时（>{timeout}s）"}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"请求失败: {type(e).__name__}"}
