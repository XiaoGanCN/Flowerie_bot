"""Web UI：管理后台（aiohttp）。

安全设计：
- 默认 WEB_UI_ENABLED=false；启用必须设置 WEB_UI_PASSWORD（启动校验）
- 认证：POST /api/login 换取 token（secrets.token_hex，内存存储 + TTL）；
  请求带 Authorization: Bearer <token>（无 cookie → 天然防 CSRF）
- 登录失败限速：同一 IP 连续 5 次失败锁 1 分钟
- Secret 脱敏：GET /api/config 只返回掩码；PUT 时留空=不修改
- 端口：与反向 WS 端口（WS_PORT）错开由启动校验保证
- 所有管理接口必须管理员 token
"""
import html as _html
import json
import secrets
import time
from typing import Dict, Optional, Tuple

from aiohttp import web

from src.config import Settings
from src.services.config_service import ConfigService
from src.utils.logging_setup import get_logger, get_recent_logs
from src.utils.metrics import registry

logger = get_logger(__name__)

_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 60


class WebUIServer:
    def __init__(self, config: Settings, config_service: ConfigService, status_provider=None):
        self.config = config
        self.config_service = config_service
        # status_provider: 可调用，返回状态 dict（ws_connected/uptime 等），由 main 注入
        self._status_provider = status_provider
        self._tokens: Dict[str, float] = {}  # token -> expire_at
        self._login_fails: Dict[str, list] = {}  # ip -> [timestamps]
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._started_at: float = __import__("time").time()

    # ---------- 认证 ----------
    def _issue_token(self) -> str:
        token = secrets.token_hex(24)
        self._tokens[token] = time.time() + max(60, getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600))
        return token

    def _check_token(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token:
            # 无 JS 面板走 Cookie 会话
            token = request.cookies.get("fb_token", "")
        expire = self._tokens.get(token, 0)
        if expire > time.time():
            return True
        self._tokens.pop(token, None)
        return False

    def _login_blocked(self, ip: str) -> bool:
        fails = [t for t in self._login_fails.get(ip, []) if time.time() - t < _LOGIN_FAIL_WINDOW]
        self._login_fails[ip] = fails
        return len(fails) >= _LOGIN_FAIL_LIMIT

    def _record_login_fail(self, ip: str) -> None:
        self._login_fails.setdefault(ip, []).append(time.time())

    # ---------- 处理器 ----------
    def _effective_credentials(self) -> Tuple[str, str]:
        """实际生效的管理账号：优先使用注册/修改后存于 settings.db 的账号，
        未注册时回退 .env 的 WEB_UI_USERNAME / WEB_UI_PASSWORD。"""
        repo_user = self.config_service.repository.get_config("WEB_UI_USERNAME")
        repo_pass = self.config_service.repository.get_config("WEB_UI_PASSWORD")
        user = repo_user if repo_user is not None else str(getattr(self.config, "WEB_UI_USERNAME", "admin"))
        pwd = repo_pass if repo_pass is not None else str(getattr(self.config, "WEB_UI_PASSWORD", "") or "")
        return user, pwd

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
        eff_user, eff_pass = self._effective_credentials()
        if (username == eff_user and secrets.compare_digest(password, eff_pass)):
            token = self._issue_token()
            logger.info("web_ui login success", extra={"event": "config_reload"})
            return web.json_response({"token": token, "expires_in": getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600)})
        self._record_login_fail(ip)
        return web.json_response({"error": "用户名或密码错误"}, status=401)

    async def _handle_register(self, request: web.Request) -> web.Response:
        """注册/修改管理账号：账号密码持久化到 settings.db，之后登录不再依赖 .env。

        安全护栏：
        - 若当前已有生效密码（.env 或已注册），必须提供当前管理员密码验证；
          仅当完全未配置密码（首次搭建）时才允许免验证注册
        - 与登录共享限流：当前密码猜错也计入失败，连续 5 次锁 IP 1 分钟（防暴力破解）
        """
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.json_response({"error": "尝试过多，请稍后再试"}, status=429)
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "请求格式错误"}, status=400)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        admin_password = str(body.get("admin_password", ""))
        eff_user, eff_pass = self._effective_credentials()
        if eff_pass and not secrets.compare_digest(admin_password, eff_pass):
            self._record_login_fail(ip)
            return web.json_response({"error": "当前管理员密码不正确，无法注册"}, status=403)
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
        import time
        status = {
            "version": "1.0.0",
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

    # ---------- 生命周期 ----------
    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._handle_root_redirect)
        app.router.add_get("/webui", self._handle_root_redirect)  # 旧 JS 版入口 → /panel
        # 无 JS 兼容面板（服务端渲染，任何浏览器可用，含禁用 JS 的手机浏览器）
        app.router.add_get("/panel", self._handle_panel)
        app.router.add_post("/panel/login", self._handle_panel_login)
        app.router.add_get("/panel/register", self._handle_panel_register_page)
        app.router.add_post("/panel/register", self._handle_panel_register)
        app.router.add_post("/panel/save", self._handle_panel_save)
        app.router.add_get("/panel/logout", self._handle_panel_logout)
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/register", self._handle_register)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_put("/api/config", self._handle_update_config)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/logs", self._handle_logs)
        return app

    # ---------- 无 JS 兼容面板（纯服务端渲染，表单提交即可用） ----------
    async def _handle_panel(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(text=self._panel_login_page(), content_type="text/html", charset="utf-8")
        msg = request.query.get("msg", "")
        err = request.query.get("err", "") == "1"
        tab = request.query.get("tab", "")
        return web.Response(text=self._panel_page(msg, err, tab),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_login(self, request: web.Request) -> web.Response:
        form = await request.post()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        eff_user, eff_pass = self._effective_credentials()
        if username == eff_user and secrets.compare_digest(password, eff_pass):
            token = self._issue_token()
            resp = web.HTTPFound("/panel")
            resp.set_cookie("fb_token", token, httponly=True, samesite="Strict",
                            max_age=max(60, getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600)))
            logger.info("web_ui panel login success", extra={"event": "config_reload"})
            return resp
        self._record_login_fail(request.remote or "unknown")
        return web.Response(text=self._panel_login_page("用户名或密码错误"),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_register_page(self, request: web.Request) -> web.Response:
        return web.Response(text=self._panel_register_page(), content_type="text/html", charset="utf-8")

    async def _handle_panel_register(self, request: web.Request) -> web.Response:
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.Response(text=self._panel_register_page("尝试过多，请稍后再试"),
                                content_type="text/html", charset="utf-8")
        form = await request.post()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        admin_password = str(form.get("admin_password", ""))
        eff_user, eff_pass = self._effective_credentials()
        if eff_pass and not secrets.compare_digest(admin_password, eff_pass):
            self._record_login_fail(ip)
            return web.Response(text=self._panel_register_page("当前管理员密码不正确"),
                                content_type="text/html", charset="utf-8")
        ok, message = self.config_service.register_user(username, password)
        return web.Response(text=self._panel_register_page(message, ok=ok),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_save(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        key = str(form.get("key", ""))
        value = str(form.get("value", ""))
        ok, message = self.config_service.update(key, value)
        from urllib.parse import quote
        return web.HTTPFound(f"/panel?msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get("fb_token", "")
        self._tokens.pop(token, None)
        resp = web.HTTPFound("/panel")
        resp.del_cookie("fb_token")
        return resp

    def _panel_login_page(self, msg: str = "") -> str:
        return (_PANEL_LOGIN_HTML.replace("@CSS@", _PANEL_CSS)
                .replace("@MSG@", _html.escape(msg)))

    def _panel_register_page(self, msg: str = "", ok: bool = True) -> str:
        return (_PANEL_REGISTER_HTML.replace("@CSS@", _PANEL_CSS)
                .replace("@MSG@", _html.escape(msg))
                .replace("@MSGCLASS@", "ok" if ok else "err"))

    def _panel_page(self, msg: str = "", err: bool = False, tab: str = "") -> str:
        by_cat: Dict[str, list] = {}
        for c in self.config_service.list_configs():
            by_cat.setdefault(c["category"], []).append(c)
        sections = []
        for cat in ("AI", "Bot", "Memory", "Sticker", "MCP", "Policy", "Logging", "Advanced"):
            items = by_cat.get(cat)
            if not items:
                continue
            rows = []
            for c in items:
                rows.append(
                    f'<form method="post" action="/panel/save" class="row">'
                    f'<input type="hidden" name="key" value="{_html.escape(c["key"])}">'
                    f'<span class="info"><b>{_html.escape(c["description"])}</b>'
                    f'<small>{_html.escape(c["key"])}{" · 密钥" if c.get("secret") else ""}'
                    f'{" · 需重启" if not c.get("hot_reload") else ""}</small></span>'
                    f'<input name="value" value="{_html.escape(str(c.get("current") or ""))}">'
                    f'<button type="submit">保存</button></form>'
                )
            sections.append(f'<h3>{_html.escape(cat)}</h3>' + "".join(rows))
        msg_html = ""
        if msg:
            msg_html = f'<div class="{ "ok" if not err else "err" }">{_html.escape(msg)}</div>'
        logs_html = ""
        if tab == "logs":
            logs = "<br>".join(_html.escape(x) for x in get_recent_logs(200))
            logs_html = f'<h3>日志</h3><pre class="log">{logs}</pre>'
        return (_PANEL_HTML.replace("@CSS@", _PANEL_CSS)
                .replace("@MSG@", msg_html)
                .replace("@SECTIONS@", "".join(sections))
                .replace("@LOGS@", logs_html))

    @staticmethod
    def effective_host(config) -> str:
        """实际监听地址：WEB_UI_ALLOW_LAN=true 时强制 0.0.0.0（局域网/公网可访问），否则用 WEB_UI_HOST。

        显式开关设计：默认只监听本机回环；想从其他设备访问必须显式开 WEB_UI_ALLOW_LAN，
        避免误配 WEB_UI_HOST 导致后台意外暴露。
        """
        if getattr(config, "WEB_UI_ALLOW_LAN", False):
            return "0.0.0.0"
        return str(getattr(config, "WEB_UI_HOST", "127.0.0.1") or "127.0.0.1")

    async def start(self) -> None:
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        host = self.effective_host(self.config)
        self._site = web.TCPSite(self._runner, host, self.config.WEB_UI_PORT)
        await self._site.start()
        if host in ("0.0.0.0", "::"):
            logger.warning(
                "web_ui bound to %s（WEB_UI_ALLOW_LAN=true）：管理后台对网络内所有设备可见。"
                "请确认 WEB_UI_PASSWORD 已设置强密码，且仅通过可信渠道（内网穿透/防火墙白名单）暴露公网",
                host, extra={"event": "config_reload"})
        logger.info("Web UI started on %s:%s", host, self.config.WEB_UI_PORT,
                    extra={"event": "config_reload"})

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._tokens.clear()
        logger.info("Web UI stopped", extra={"event": "config_reload"})


_PANEL_CSS = """body{font-family:sans-serif;background:#1e2229;color:#d7dde6;margin:0;padding:20px}
.box{max-width:520px;margin:8vh auto;background:#262b33;border:1px solid #363d48;border-radius:12px;padding:28px}
h2{margin-top:0;font-size:17px}h3{font-size:14px;color:#8b96a5;margin:20px 0 8px}
input{background:#2d333d;border:1px solid #363d48;color:#d7dde6;border-radius:7px;padding:9px 12px;margin-bottom:10px;box-sizing:border-box}
button{background:#5b8def;border:none;color:#fff;padding:9px 16px;border-radius:7px;cursor:pointer}
a{color:#5b8def;font-size:13px;text-decoration:none}
.err{color:#f85149;font-size:13px;margin-bottom:10px}.ok{color:#3fb950;font-size:13px;margin-bottom:10px}
.row{display:flex;gap:8px;align-items:center;margin-bottom:8px;flex-wrap:wrap;background:#262b33;border:1px solid #363d48;border-radius:8px;padding:10px 14px}
.row .info{width:240px;flex-shrink:0}.row .info small{display:block;color:#8b96a5;font-size:11px}
.row input{width:180px;margin:0}.row button{width:auto}
.log{background:#171a1f;border:1px solid #363d48;border-radius:8px;padding:12px;font-size:12px;overflow-x:auto;line-height:1.7}
"""
_PANEL_LOGIN_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>花璃 · 管理后台</title>
<style>@CSS@</style></head><body>
<div class="box"><h2>花璃 · 管理后台</h2>
<div class="err">@MSG@</div>
<form method="post" action="/panel/login">
<input name="username" placeholder="用户名" required style="width:100%">
<input name="password" type="password" placeholder="密码" required style="width:100%">
<button type="submit" style="width:100%">登录</button></form>
<p><a href="/panel/register">没有账号？注册管理员账号</a></p>
<p style="color:#8b96a5;font-size:12px">无 JS 兼容面板（服务端渲染）· 任意浏览器可用</p>
</div></body></html>"""
_PANEL_REGISTER_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>花璃 · 注册</title>
<style>@CSS@</style></head><body>
<div class="box"><h2>注册管理员账号</h2>
<div class="@MSGCLASS@">@MSG@</div>
<form method="post" action="/panel/register">
<input name="username" placeholder="新用户名（3~32 字符）" required style="width:100%">
<input name="password" type="password" placeholder="新密码（至少 6 位）" required style="width:100%">
<input name="admin_password" type="password" placeholder="当前管理员密码" style="width:100%">
<button type="submit" style="width:100%">注册</button></form>
<p><a href="/panel">← 返回登录</a></p>
</div></body></html>"""
_PANEL_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>花璃 · 管理后台</title>
<style>@CSS@</style></head><body>
<h2 style="display:flex;justify-content:space-between;align-items:center;max-width:960px;margin:0 auto 12px">
<span>花璃 · 配置管理 <small style="color:#8b96a5">无 JS 版面板</small></span>
<span style="font-size:13px"><a href="/panel?tab=logs">日志</a> · <a href="/panel/logout">退出</a></span>
</h2>
<div style="max-width:960px;margin:0 auto">
@MSG@
@SECTIONS@
@LOGS@
</div></body></html>"""
