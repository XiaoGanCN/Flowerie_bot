"""Web UI 认证域处理器（登录/注册/token/API JSON 接口）。

从 WebUIServer 拆分（防上帝类）：只包含认证与会话相关处理器，
通过 self 访问 WebUIServer 核心（_tokens/_login_fails/_verify_admin 等）。
"""
import json
import time

from aiohttp import web

from src.services.web_ui_assets import render_login_page, render_register_page
from src.utils.logging_setup import get_logger, get_recent_logs
from src.utils.metrics import registry

logger = get_logger(__name__)


class AuthPanelMixin:

    async def _handle_login(self, request: web.Request) -> web.Response:
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.json_response({"error": "登录尝试过多，请稍后再试"}, status=429)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "请求格式错误"}, status=400)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        if self._verify_admin(username, password):
            token = self._issue_token()
            logger.info("web_ui login success", extra={"event": "config_reload"})
            return web.json_response({"token": token, "expires_in": getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600)})
        self._record_login_fail(ip)
        return web.json_response({"error": "用户名或密码错误"}, status=401)

    async def _handle_register(self, request: web.Request) -> web.Response:
        """注册（仅 Bootstrap：系统从未初始化管理员账号时创建第一个管理员）。

        安全设计（Bootstrap Lock）：
        - 一旦系统已初始化（.env 或 settings.db 存在管理凭据），公开注册永久关闭 → 403
        - 未初始化时无需任何口令验证（首次搭建）；并发注册由原子 CAS 保证仅一个成功
        - 初始化后改账号一律走登录态 /panel/account/credentials（需当前密码）
        """
        if self.config_service.admin_initialized():
            return web.json_response(
                {"error": "系统已完成初始化，公开注册已关闭（攻击者无法再自建管理员账号）"},
                status=403)
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.json_response({"error": "尝试过多，请稍后再试"}, status=429)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "请求格式错误"}, status=400)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        ok, message = self.config_service.register_user(username, password)
        if not ok:
            return web.json_response({"error": message}, status=400)
        logger.info("web_ui register success user=%s", username, extra={"event": "config_reload"})
        return web.json_response({"ok": True, "message": message})

    async def _handle_logout(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        self._tokens.pop(token, None)
        return web.json_response({"ok": True})

    async def _handle_get_config(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        return web.json_response({"configs": self.config_service.list_configs()})

    async def _handle_update_config(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "请求格式错误"}, status=400)
        key = str(body.get("key", ""))
        value = body.get("value")
        ok, message = self.config_service.update(key, "" if value is None else str(value))
        status = 200 if ok else 400
        return web.json_response({"ok": ok, "message": message}, status=status)

    async def _handle_status(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        status = {
            "version": "1.4.0",
            "uptime_seconds": int(time.time() - self._started_at),
            "config_count": len(self.config_service.list_configs()),
        }
        if self._status_provider is not None:
            try:
                status.update(self._status_provider())
            except Exception:  # noqa: BLE001
                pass
        # 指标摘要（低基数，聚合值）
        status["metrics"] = {k: v for k, v in registry.snapshot().items() if k in (
            "received_messages_total", "processed_messages_total", "rejected_messages_total",
            "ai_requests_total", "ai_attempts_total", "ai_success_total", "ai_failure_total",
            "memory_read_total", "memory_write_total", "message_send_failure_total",
            "mcp_calls_total", "mcp_call_failures_total")}
        return web.json_response(status)

    async def _handle_logs(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError:
            limit = 100
        return web.json_response({"logs": get_recent_logs(limit=max(1, min(limit, 500)))})

    async def _handle_root_redirect(self, request: web.Request) -> web.Response:
        """JS 版已移除：根路径与 /webui 重定向到无 JS 面板 /panel。"""
        return web.HTTPFound("/panel")

    async def _handle_panel_login(self, request: web.Request) -> web.Response:
        form = await request.post()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        if self._verify_admin(username, password):
            token = self._issue_token()
            resp = web.HTTPFound("/panel")
            resp.set_cookie("fb_token", token, httponly=True, samesite="Strict",
                            max_age=max(60, getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600)))
            logger.info("web_ui panel login success", extra={"event": "config_reload"})
            return resp
        self._record_login_fail(request.remote or "unknown")
        return web.Response(text=render_login_page("用户名或密码错误"),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_register_page(self, request: web.Request) -> web.Response:
        if self.config_service.admin_initialized():
            return web.Response(text=render_register_page(
                "系统已完成初始化，公开注册已关闭。修改账号请用现有账号登录后，"
                "到「用户状态」页操作。", ok=False, closed=True),
                content_type="text/html", charset="utf-8")
        return web.Response(text=render_register_page(), content_type="text/html", charset="utf-8")

    async def _handle_panel_register(self, request: web.Request) -> web.Response:
        """Bootstrap 注册（同 /api/register）：仅系统未初始化时可用。"""
        if self.config_service.admin_initialized():
            return web.Response(text=render_register_page(
                "系统已完成初始化，公开注册已关闭。", ok=False, closed=True),
                content_type="text/html", charset="utf-8")
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.Response(text=render_register_page("尝试过多，请稍后再试", ok=False),
                                content_type="text/html", charset="utf-8")
        form = await request.post()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        ok, message = self.config_service.register_user(username, password)
        return web.Response(text=render_register_page(message, ok=ok, closed=False),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get("fb_token", "")
        self._tokens.pop(token, None)
        resp = web.HTTPFound("/panel")
        resp.del_cookie("fb_token")
        return resp
