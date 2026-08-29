"""Web UI 管理后台（aiohttp，无 JS 纯服务端渲染）。

安全设计：
- 默认 WEB_UI_ENABLED=false；启用必须设置 WEB_UI_PASSWORD（启动校验）
- 认证：POST /api/login 换取 token（secrets.token_hex，内存存储 + TTL）；
  请求带 Authorization: Bearer <token>（无 cookie → 天然防 CSRF）；
  无 JS 面板走 Cookie 会话（fb_token，httponly + SameSite=Strict）
- 登录失败限速：同一 IP 连续 5 次失败锁 1 分钟
- Secret 脱敏：页面只返回掩码；提交时留空=不修改
- 端口：与反向 WS 端口（WS_PORT）错开由启动校验保证
- 所有管理接口必须管理员 token

架构（防上帝类）：本模块是薄门面，仅保留认证基座 / 面板壳 / 生命周期；
各功能域处理器拆到 src/services/webui_panels/ 的独立 mixin：
- AuthPanelMixin        认证与会话（登录/注册/token/JSON API）
- ConfigPanelMixin      配置分组表单保存
- AppearancePanelMixin  主题/背景/图片/透明度
- McpPanelMixin         MCP 多 server 编辑/测试
- PromptPanelMixin      群聊自定义 Prompt 读写（按群隔离）
- PersonaPanelMixin     默认/全局/自定义/群聊人格
- KnowledgePanelMixin   群聊梗知识（按群隔离）
渲染层（HTML/CSS/主题）见 src/services/webui_render/（web_ui_assets.py 为聚合导出）。
配置持久化：.env（原子）+ settings.db 双写（见 ConfigService）。
"""
import html as _html
import secrets
import time
from typing import Dict, Optional, Tuple

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
    theme_body_class,
    theme_default_alpha,
    theme_default_bg,
)
from src.services.webui_panels import (
    AccountPanelMixin,
    AppearancePanelMixin,
    AuthPanelMixin,
    ConfigPanelMixin,
    KnowledgePanelMixin,
    McpPanelMixin,
    PersonaPanelMixin,
    PromptPanelMixin,
)

# 兼容导出：拆分后常量/辅助函数移入 webui_panels.appearance_panel
from src.services.webui_panels.appearance_panel import MAX_UPLOAD_BYTES  # noqa: F401
from src.utils.logging_setup import get_logger, get_recent_logs

logger = get_logger(__name__)

_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 60


class WebUIServer(AccountPanelMixin, AuthPanelMixin, ConfigPanelMixin, AppearancePanelMixin,
                  McpPanelMixin, PromptPanelMixin, PersonaPanelMixin,
                  KnowledgePanelMixin):

    def __init__(self, config: Settings, config_service: ConfigService, status_provider=None,
                 data_dir: str = "./data/webui", tool_manager=None,
                 persona_manager=None, meme_manager=None, prompt_manager=None):
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
        # 运行中 MCP 工具管理器（读取各 server 已同步工具数，用于卡片显示真实数量）
        self._tool_manager = tool_manager
        # 人格系统 / 群聊知识 / 自定义 Prompt（Web UI 管理页数据源；未注入时对应区块提示不可用）
        self._persona_manager = persona_manager
        self._meme_manager = meme_manager
        self._prompt_manager = prompt_manager

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
        # 注销管理员账号（需当前密码验证；只清账号密码，其他配置不动）
        app.router.add_post("/panel/account/unregister", self._handle_panel_unregister)
        # 外观美化（主题 / 背景颜色 / 背景图片 / 透明度）
        app.router.add_post("/panel/appearance", self._handle_panel_appearance_save)
        app.router.add_post("/panel/appearance/restore", self._handle_panel_appearance_restore)
        app.router.add_post("/panel/appearance/delete-image", self._handle_panel_appearance_delete_image)
        app.router.add_get("/panel/background", self._handle_panel_background)
        # MCP server 结构化编辑（添加/编辑/删除，零 JS 表单）
        app.router.add_post("/panel/mcp/edit", self._handle_panel_mcp_edit)
        # 人格管理（零 JS 表单：默认 / 全局 / 列表 CRUD / 群绑定）
        app.router.add_post("/panel/persona/default", self._handle_panel_persona_default)
        app.router.add_post("/panel/persona/global", self._handle_panel_persona_global)
        app.router.add_post("/panel/persona/save", self._handle_panel_persona_save)
        app.router.add_post("/panel/persona/delete", self._handle_panel_persona_delete)
        app.router.add_post("/panel/persona/group", self._handle_panel_persona_group)
        # 群聊自定义 Prompt 管理（零 JS 表单：全局 / 按群读写，按群隔离）
        app.router.add_post("/panel/prompt/global", self._handle_panel_prompt_global)
        app.router.add_post("/panel/prompt/group", self._handle_panel_prompt_group)
        # 群聊知识管理（零 JS 表单：查看 / 新增 / 编辑 / 删除，严格按群隔离）
        app.router.add_post("/panel/knowledge/view", self._handle_panel_knowledge_view)
        app.router.add_post("/panel/knowledge/add", self._handle_panel_knowledge_add)
        app.router.add_post("/panel/knowledge/save", self._handle_panel_knowledge_save)
        app.router.add_post("/panel/knowledge/delete", self._handle_panel_knowledge_delete)
        app.router.add_post("/panel/knowledge/clear", self._handle_panel_knowledge_clear)
        # JSON API（保留，供脚本/自动化使用）
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/register", self._handle_register)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_put("/api/config", self._handle_update_config)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/logs", self._handle_logs)
        return app

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

    async def _handle_panel(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.Response(text=render_login_page(), content_type="text/html", charset="utf-8")
        msg = request.query.get("msg", "")
        err = request.query.get("err", "") == "1"
        tab = request.query.get("tab", "")
        if tab not in ("appearance", "logs", "persona", "knowledge", "account"):
            tab = "config"
        cat = request.query.get("cat", "")
        if cat not in ("all", "") and cat not in ConfigService.CATEGORY_ORDER:
            cat = ""
        try:
            mcp_edit = int(request.query.get("edit", ""))
        except (TypeError, ValueError):
            mcp_edit = None
        # 人格编辑目标 / 新建标记 / 知识页群号与搜索
        edit_id = request.query.get("edit", "") if tab == "persona" else ""
        new_persona = request.query.get("new", "") == "1"
        gid_raw = request.query.get("gid", "")
        gid: Optional[int] = None
        if gid_raw.isdigit():
            gid = int(gid_raw)
        q = request.query.get("q", "")
        # 群聊 Prompt 管理：prompt_gid 参数指定要读写哪个群的 Prompt（仅数字）
        prompt_gid_raw = request.query.get("prompt_gid", "")
        prompt_gid: Optional[int] = None
        if prompt_gid_raw.isdigit():
            prompt_gid = int(prompt_gid_raw)
        return web.Response(
            text=self._panel_page(msg, err, tab, cat, mcp_edit, edit_id=edit_id,
                                  new_persona=new_persona, gid=gid, search=q,
                                  prompt_gid=prompt_gid),
            content_type="text/html", charset="utf-8",
        )

    def _panel_page(self, msg: str = "", err: bool = False, tab: str = "config", cat: str = "",
                    mcp_edit=None, edit_id: str = "", new_persona: bool = False,
                    gid: Optional[int] = None, search: str = "",
                    prompt_gid: Optional[int] = None) -> str:
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
        elif tab == "account":
            body_html = self._render_account_page()
        elif tab == "persona":
            body_html = self._render_persona_page(edit_id, new_persona, prompt_gid)
        elif tab == "knowledge":
            body_html = self._render_knowledge_page(gid, search)
        else:
            body_html = render_config_sections(self.config_service.list_configs(), active_cat=cat,
                                               mcp_edit=mcp_edit, mcp_test_status=self._get_mcp_test_status(),
                                               mcp_tool_counts=self._mcp_tool_counts())
        return render_panel_page(
            theme_class=theme_body_class(theme),
            bg_rules=bg_rules,
            msg_html=msg_html,
            body_html=body_html,
            active_tab=tab,
            panel_bg_css=panel_bg_css,
            glass=(panel_style == "glass"),
        )

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
