"""Web UI：管理后台（aiohttp，无 JS 纯服务端渲染）。

安全设计：
- 默认 WEB_UI_ENABLED=false；启用必须设置 WEB_UI_PASSWORD（启动校验）
- 认证：POST /api/login 换取 token（secrets.token_hex，内存存储 + TTL）；
  请求带 Authorization: Bearer <token>（无 cookie → 天然防 CSRF）；
  无 JS 面板走 Cookie 会话（fb_token，httponly + SameSite=Strict）
- 登录失败限速：同一 IP 连续 5 次失败锁 1 分钟
- Secret 脱敏：页面只返回掩码；提交时留空=不修改
- 端口：与反向 WS 端口（WS_PORT）错开由启动校验保证
- 所有管理接口必须管理员 token

功能（全部纯 HTML/CSS/服务端，零 JavaScript）：
- /panel 配置页：全部配置变量按分类分组（fieldset），每组一个表单保存
- /panel?tab=appearance 外观页：内置主题 / 背景颜色 / 背景图片上传 /
  图片透明度 / 显示方式 / 恢复默认 / 删除图片，持久化到 settings.db + data/webui
- /panel?tab=logs 日志页
- 配置保存双写：.env（原子）+ settings.db；热更新 Settings 实例
"""
import html as _html
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from aiohttp import web

from src.config import Settings
from src.services.config_service import ConfigService, verify_password
from src.services.web_ui_assets import (
    THEMES,
    background_rules,
    render_appearance,
    render_config_sections,
    render_login_page,
    render_panel_page,
    render_register_page,
    theme_body_class,
    theme_default_alpha,
    theme_default_bg,
)
from src.utils.logging_setup import get_logger, get_recent_logs
from src.utils.metrics import registry

logger = get_logger(__name__)

_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 60
# 背景图片上传限制（服务端强制）
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5MB
_ALLOWED_IMAGE_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
_EXT_MAP = {"png": "png", "jpeg": "jpg", "webp": "webp", "gif": "gif"}
# 固定文件名扩展名 → Content-Type（不依赖系统 mimetypes 猜测）
_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def detect_image_type(data: bytes) -> Optional[str]:
    """按魔数检测真实图片格式（不信任扩展名/MIME）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_hex_color(value: str) -> bool:
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value or ""))


_HEX6_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.I)
_RGBA_RE = re.compile(r"^rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*[\d.]+\s*\)$", re.I)
_RGB3_RE = re.compile(r"^\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*$")


def normalize_color(value: str) -> Optional[str]:
    """把用户输入的颜色解析成标准 `#RRGGBB`。支持多种写法：
    - `#FDEEF3` / `FDEEF3`
    - `253,238,243` / `253, 238, 243`
    - `rgb(253,238,243)` / `rgba(253,238,243,0.5)`
    非法返回 None。
    """
    v = (value or "").strip()
    if not v:
        return None
    m = _HEX6_RE.match(v)
    if m:
        return "#" + m.group(1).upper()
    m = _RGB_RE.match(v) or _RGBA_RE.match(v) or _RGB3_RE.match(v)
    if m:
        nums = [int(x) for x in m.groups() if x is not None]
        if len(nums) == 3 and all(0 <= x <= 255 for x in nums):
            return "#%02X%02X%02X" % (nums[0], nums[1], nums[2])
    return None


class WebUIServer:
    def __init__(self, config: Settings, config_service: ConfigService, status_provider=None,
                 data_dir: str = "./data/webui"):
        self.config = config
        self.config_service = config_service
        # status_provider: 可调用，返回状态 dict（ws_connected/uptime 等），由 main 注入
        self._status_provider = status_provider
        self._tokens: Dict[str, float] = {}  # token -> expire_at
        self._login_fails: Dict[str, list] = {}  # ip -> [timestamps]
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._started_at: float = time.time()
        # 外观资源持久化目录（背景图片），测试可注入临时目录
        self._data_dir = str(data_dir)

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

    def _verify_admin(self, username: str, password: str) -> bool:
        """安全校验管理员凭据：scrypt 哈希校验或旧明文兼容比较（恒定时间），
        登录成功且为旧明文时自动迁移为哈希（DB 不再保留明文）。"""
        eff_user, eff_pass = self._effective_credentials()
        if username != eff_user:
            return False
        if not verify_password(password, eff_pass):
            return False
        try:
            self.config_service.migrate_plaintext_password(eff_user, password)
        except Exception:  # noqa: BLE001 - 迁移失败不阻断登录
            pass
        return True

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
        if eff_pass and not self._verify_admin(eff_user, admin_password):
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
        # 外观美化（主题 / 背景颜色 / 背景图片 / 透明度）
        app.router.add_post("/panel/appearance", self._handle_panel_appearance_save)
        app.router.add_post("/panel/appearance/restore", self._handle_panel_appearance_restore)
        app.router.add_post("/panel/appearance/delete-image", self._handle_panel_appearance_delete_image)
        app.router.add_get("/panel/background", self._handle_panel_background)
        # MCP server 结构化编辑（添加/编辑/删除，零 JS 表单）
        app.router.add_post("/panel/mcp/edit", self._handle_panel_mcp_edit)
        # JSON API（保留，供脚本/自动化使用）
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/register", self._handle_register)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_put("/api/config", self._handle_update_config)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/logs", self._handle_logs)
        return app

    # ---------- 外观偏好（settings.db webui_prefs 表持久化） ----------
    def _pref(self, key: str, default: str = "") -> str:
        v = self.config_service.repository.get_pref(key)
        return v if v is not None else default

    def _set_pref(self, key: str, value: str) -> None:
        self.config_service.repository.set_pref(key, value)

    def _get_prefs(self) -> Dict[str, object]:
        try:
            opacity = int(self._pref("bg_image_opacity", "100") or 100)
        except ValueError:
            opacity = 100
        theme = self._pref("theme", "default")
        return {
            "theme": theme,
            # 背景颜色按主题隔离：bg_color__<theme>，各主题互不污染
            "bg_color": self._pref(f"bg_color__{theme}", ""),
            "bg_image": self._pref("bg_image", ""),
            "opacity": max(0, min(100, opacity)),
            "panel_opacity": self._pref("panel_opacity", ""),
            "panel_style": self._pref("panel_style", "clear"),
            "size": self._pref("bg_size", "cover"),
            "position": self._pref("bg_position", "center"),
        }

    @staticmethod
    def _bg_color_pref_key(theme: str) -> str:
        return f"bg_color__{theme}"

    def _background_dir(self) -> Path:
        return Path(self._data_dir) / "background"

    def _background_path(self) -> Optional[Path]:
        fname = self._pref("bg_image", "")
        if not fname or os.path.basename(fname) != fname or not re.fullmatch(r"background\.(png|jpg|webp|gif)", fname):
            return None
        path = self._background_dir() / fname
        return path if path.is_file() else None

    def _delete_bg_image(self) -> None:
        bg_dir = self._background_dir()
        try:
            for old in bg_dir.glob("background.*"):
                old.unlink()
        except OSError:  # noqa: BLE001
            pass
        self._set_pref("bg_image", "")

    def _save_background_image(self, data: bytes, hint_filename: str) -> Tuple[bool, str]:
        """服务端图片校验与持久化（大小/扩展名/魔数，固定安全文件名）。"""
        if len(data) > MAX_UPLOAD_BYTES:
            return False, "文件过大（最大 5MB）"
        hint_ext = os.path.splitext(hint_filename or "")[1].lstrip(".").lower()
        if hint_ext not in _ALLOWED_IMAGE_EXTS:
            return False, "文件扩展名不合法（仅允许 png/jpg/jpeg/webp/gif）"
        img_type = detect_image_type(data)
        if img_type is None:
            return False, "文件内容不是合法图片（PNG/JPEG/WEBP/GIF）"
        bg_dir = self._background_dir()
        bg_dir.mkdir(parents=True, exist_ok=True)
        # 覆盖旧图（固定文件名，绝不用用户提供的文件名 → 无路径穿越）
        for old in bg_dir.glob("background.*"):
            try:
                old.unlink()
            except OSError:  # noqa: BLE001
                pass
        target = bg_dir / f"background.{_EXT_MAP[img_type]}"
        tmp = bg_dir / ".background.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, target)
        self._set_pref("bg_image", target.name)
        return True, "背景图片已更新"

    # ---------- 无 JS 兼容面板（纯服务端渲染，表单提交即可用） ----------
    async def _handle_panel(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(text=render_login_page(), content_type="text/html", charset="utf-8")
        msg = request.query.get("msg", "")
        err = request.query.get("err", "") == "1"
        tab = request.query.get("tab", "")
        if tab not in ("appearance", "logs"):
            tab = "config"
        cat = request.query.get("cat", "")
        if cat not in ("all", "") and cat not in ConfigService.CATEGORY_ORDER:
            cat = ""
        return web.Response(text=self._panel_page(msg, err, tab, cat),
                            content_type="text/html", charset="utf-8")

    def _panel_page(self, msg: str = "", err: bool = False, tab: str = "config", cat: str = "") -> str:
        prefs = self._get_prefs()
        theme = str(prefs["theme"])
        if theme not in THEMES:
            theme = "default"
        bg_color = str(prefs["bg_color"]) or theme_default_bg(theme)
        image_url = ""
        if prefs["bg_image"]:
            image_url = "/panel/background?v=%d" % int(time.time())
        # 主题面板透明度：用户显式设置则用其值（覆盖所有主题的默认 alpha），否则用各主题默认。
        # 卡片背景由服务端算成**具体 rgba(r,g,b,a)** 注入 body（杜绝 rgba(var(),var()) 在部分
        # 浏览器失效导致卡片颜色错误），保证深色主题卡片也是深色。
        theme_vars = THEMES.get(theme, THEMES["default"])["vars"]
        theme_rgb = str(theme_vars.get("--panel-rgb", "255,255,255"))
        panel_opacity = int(round(theme_default_alpha(theme) * 100))
        if prefs["panel_opacity"]:
            try:
                panel_opacity = max(0, min(100, int(prefs["panel_opacity"])))
            except ValueError:
                panel_opacity = int(round(theme_default_alpha(theme) * 100))
        panel_bg_css = "rgba(%s,%.2f)" % (theme_rgb, panel_opacity / 100.0)
        bg_rules = background_rules(
            bg_color,
            image_url if prefs["bg_image"] else "",
            int(prefs["opacity"]),
            str(prefs["size"]),
            str(prefs["position"]),
        )
        msg_html = ""
        if msg:
            msg_html = f'<div class="msg {"ok" if not err else "err"}">{_html.escape(msg)}</div>'
        panel_style = "glass" if str(prefs.get("panel_style", "")) == "glass" else "clear"
        if tab == "appearance":
            body_html = render_appearance(
                theme, bg_color, int(prefs["opacity"]),
                str(prefs["size"]), str(prefs["position"]),
                bool(prefs["bg_image"]), image_url,
                panel_opacity=panel_opacity, panel_style=panel_style,
            )
        elif tab == "logs":
            logs = "\n".join(get_recent_logs(200))
            body_html = f'<pre class="log">{_html.escape(logs)}</pre>'
        else:
            body_html = render_config_sections(self.config_service.list_configs(), active_cat=cat)
        return render_panel_page(
            theme_class=theme_body_class(theme),
            bg_rules=bg_rules,
            msg_html=msg_html,
            body_html=body_html,
            active_tab=tab,
            panel_bg_css=panel_bg_css,
            glass=(panel_style == "glass"),
        )

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
        return web.Response(text=render_register_page(), content_type="text/html", charset="utf-8")

    async def _handle_panel_register(self, request: web.Request) -> web.Response:
        ip = request.remote or "unknown"
        if self._login_blocked(ip):
            return web.Response(text=render_register_page("尝试过多，请稍后再试", ok=False),
                                content_type="text/html", charset="utf-8")
        form = await request.post()
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))
        admin_password = str(form.get("admin_password", ""))
        eff_user, eff_pass = self._effective_credentials()
        if eff_pass and not self._verify_admin(eff_user, admin_password):
            self._record_login_fail(ip)
            return web.Response(text=render_register_page("当前管理员密码不正确", ok=False),
                                content_type="text/html", charset="utf-8")
        ok, message = self.config_service.register_user(username, password)
        return web.Response(text=render_register_page(message, ok=ok),
                            content_type="text/html", charset="utf-8")

    async def _handle_panel_save(self, request: web.Request) -> web.Response:
        """配置保存：兼容旧版单键表单（key/value）；分组表单字段名=配置键。

        checkbox 采用 hidden false + checkbox true 同名字段模式：未勾选只提交
        false，勾选提交 false 和 true —— 服务端取同名最后一个值（未提交=false 语义）。
        """
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        cat = request.query.get("cat", "")
        if cat not in ConfigService.CATEGORY_ORDER:
            cat = ""
        _catq = f"&cat={quote(cat)}" if cat else ""
        form = await request.post()
        if "key" in form and "value" in form:
            key = str(form.get("key", ""))
            value = str(form.get("value", ""))
            ok, message = self.config_service.update(key, value)
            return web.HTTPFound(f"/panel?msg={quote(message)}&err={'1' if not ok else ''}{_catq}")
        updates: Dict[str, str] = {}
        for name in form.keys():
            if name not in self.config_service.SCHEMA:
                continue
            if hasattr(form, "getall"):
                vals = form.getall(name)
                updates[name] = str(vals[-1]) if vals else ""
            else:
                updates[name] = str(form.get(name, ""))
        ok, message = self.config_service.update_many(updates)
        return web.HTTPFound(f"/panel?msg={quote(message)}&err={'1' if not ok else ''}{_catq}")

    async def _handle_panel_logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get("fb_token", "")
        self._tokens.pop(token, None)
        resp = web.HTTPFound("/panel")
        resp.del_cookie("fb_token")
        return resp

    # ---------- 外观：保存（主题/背景颜色/图片/透明度/显示方式） ----------
    async def _handle_panel_appearance_save(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        # 上传大小预检（防超大 multipart 打爆内存）
        clen = request.headers.get("Content-Length", "")
        if clen.isdigit() and int(clen) > MAX_UPLOAD_BYTES + 1_048_576:
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('文件过大（最大 5MB）')}&err=1")
        try:
            form = await request.post()
        except Exception:  # noqa: BLE001
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('表单解析失败')}&err=1")

        errors: List[str] = []
        theme = str(form.get("theme", "") or "")
        if theme and theme not in THEMES:
            errors.append("主题无效")
        # 背景颜色：严格跟主题走。**只有用户在文本输入框明确填写**才算自定义，
        # 且保存为该主题的自定义背景；文本输入框留空 = 清除该主题自定义、用主题默认色。
        # 不读取色器值（无 JS 下切主题时取色器会残留旧主题色，不能作为保存信号），
        # 这样"选黑色主题背景就该是黑"、且能清掉以前残留的浅色。
        color_text = str(form.get("bg_color_input", "") or "").strip()
        if color_text:
            bg_color = normalize_color(color_text)
            if bg_color is None:
                errors.append("背景颜色格式无效（支持 #RRGGBB 或 R,G,B 或 rgb(r,g,b)）")
                bg_color = ""
        else:
            bg_color = ""  # 用该主题默认背景
        opacity_raw = str(form.get("bg_image_opacity", "") or "100")
        try:
            opacity = int(opacity_raw)
            if not (0 <= opacity <= 100):
                raise ValueError
        except ValueError:
            errors.append("图片透明度必须是 0~100 的整数")
            opacity = 100
        panel_opacity_raw = str(form.get("panel_opacity", "") or "").strip()
        try:
            panel_opacity = int(panel_opacity_raw) if panel_opacity_raw else int(round(theme_default_alpha(theme or "default") * 100))
            if not (0 <= panel_opacity <= 100):
                raise ValueError
        except ValueError:
            errors.append("主题面板透明度必须是 0~100 的整数")
            panel_opacity = 90
        size = str(form.get("bg_size", "") or "cover")
        if size not in ("cover", "contain"):
            errors.append("图片显示方式无效")
            size = "cover"
        position = str(form.get("bg_position", "") or "center")
        if position not in ("center", "top", "bottom", "left", "right"):
            errors.append("图片位置无效")
            position = "center"

        file_field = form.get("bg_image")
        upload_data: Optional[bytes] = None
        upload_hint = ""
        if file_field is not None and getattr(file_field, "filename", ""):
            upload_hint = file_field.filename
            try:
                upload_data = file_field.file.read()
            except Exception:  # noqa: BLE001
                errors.append("读取上传文件失败")

        if errors:
            return web.HTTPFound(f"/panel?tab=appearance&msg={quote('未保存：' + '；'.join(errors))}&err=1")

        # 全部通过 → 持久化（主题/颜色/透明度/显示方式；背景色按主题隔离）
        if theme:
            self._set_pref("theme", theme)
        # bg_color 为空=清除该主题自定义色（用主题默认背景）；有值=保存为该主题背景
        self._set_pref(self._bg_color_pref_key(theme or "default"), bg_color)
        self._set_pref("bg_image_opacity", str(opacity))
        self._set_pref("bg_size", size)
        self._set_pref("bg_position", position)
        self._set_pref("panel_opacity", str(panel_opacity))
        panel_style = str(form.get("panel_style", "") or "clear").strip()
        if panel_style not in ("glass", "clear"):
            panel_style = "clear"
        self._set_pref("panel_style", panel_style)
        message = "外观设置已保存"
        if upload_data is not None:
            ok, file_msg = self._save_background_image(upload_data, upload_hint)
            if not ok:
                return web.HTTPFound(f"/panel?tab=appearance&msg={quote(file_msg)}&err=1")
            message = file_msg
        logger.info("web_ui appearance saved theme=%s", theme, extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote(message)}")

    async def _handle_panel_appearance_restore(self, request: web.Request) -> web.Response:
        """恢复默认主题/背景样式（不删除背景图片，由 delete-image 单独管理）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        self._set_pref("theme", "default")
        for k, _ in self.config_service.repository.list_prefs():
            if k.startswith("bg_color__"):
                self.config_service.repository.delete_pref(k)
        self._set_pref("panel_opacity", "")
        self._set_pref("panel_style", "clear")
        self._set_pref("bg_image_opacity", "100")
        self._set_pref("bg_size", "cover")
        self._set_pref("bg_position", "center")
        logger.info("web_ui appearance restored", extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote('已恢复默认主题与背景样式')}")

    async def _handle_panel_appearance_delete_image(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        self._delete_bg_image()
        logger.info("web_ui background image deleted", extra={"event": "config_reload"})
        return web.HTTPFound(f"/panel?tab=appearance&msg={quote('背景图片已删除')}")

    async def _handle_panel_background(self, request: web.Request) -> web.Response:
        """提供已上传的背景图片（仅管理员 token，显式 Content-Type，nosniff）。"""
        if not self._check_token(request):
            return web.Response(status=403, text="Forbidden")
        path = self._background_path()
        if path is None:
            return web.Response(status=404, text="Not Found")
        try:
            data = path.read_bytes()
        except OSError:  # noqa: BLE001
            return web.Response(status=404, text="Not Found")
        content_type = _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        resp = web.Response(body=data, content_type=content_type)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, max-age=3600"
        return resp

    # ---------- MCP server 结构化编辑（添加/编辑/删除，零 JS 表单） ----------
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
        redirect = web.HTTPFound("/panel?cat=MCP")
        if action == "delete":
            if index is not None and 0 <= index < len(servers):
                servers.pop(index)
            ok, msg = self._save_mcp_servers(servers)
            return web.HTTPFound(f"/panel?cat=MCP&msg={quote(msg)}&err={'1' if not ok else ''}")
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

    # ---------- 生命周期 ----------
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
