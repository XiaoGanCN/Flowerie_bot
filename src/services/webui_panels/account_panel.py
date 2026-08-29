"""Web UI 用户状态域处理器（账户/注销 + 服务器状态 + MCP/API 状态）。

从 WebUIServer 拆分（防上帝类）：
- 当前管理员与凭据来源
- 注销账号（需当前密码验证：只清账号密码，其他配置不动）
- 服务器运行状态（平台/内存/系统，见 src/services/system_status.py）
- MCP 工具状态（各 server 名称/工具数/熔断）与 API 厂商连接状态（配置层面）
"""

from urllib.parse import quote

from aiohttp import web

from src.services.system_status import collect as collect_system_status
from src.services.web_ui_assets import render_account_tab

# API 厂商连接状态展示所需的配置键（配置层面：URL/模型/Key 是否设置/是否独立）
_API_KEYS = ("DEEPSEEK_API_URL", "DEEPSEEK_MODEL", "DEEPSEEK_API_KEY",
             "VISION_API_URL", "VISION_MODEL", "VISION_API_KEY",
             "TOXIC_API_URL", "TOXIC_MODEL", "TOXIC_API_KEY")


class AccountPanelMixin:
    """用户状态页：账户 / 注销 / 服务器与集成状态。"""

    def _render_account_page(self) -> str:
        """组装「用户状态」页：当前用户 + 注销表单 + 服务器/MCP/API 状态。"""
        username, source = self._credential_info()
        system_info = collect_system_status()
        mcp_status = self._mcp_status()
        api_status = self._config_status()
        return render_account_tab(username, source, system_info, mcp_status, api_status)

    def _credential_info(self) -> tuple:
        """(当前管理员名, 凭据来源)。settings.db 有注册账号 → 已注册；否则回退 .env 初始。"""
        db_user = self.config_service.repository.get_config("WEB_UI_USERNAME")
        eff_user, _pass = self._effective_credentials()
        if db_user:
            return eff_user, "settings.db 注册账号"
        return eff_user, ".env 初始配置"

    def _mcp_status(self) -> list:
        """MCP 工具状态：各 server 名称 / 工具数 / 熔断状态。"""
        out = []
        mgr = self._tool_manager
        if mgr is None or not mgr.is_enabled():
            return out
        for s in getattr(mgr, "_servers", []) or []:
            name = getattr(s, "name", "?")
            tools = len(getattr(s, "schemas", {}) or {})
            breaker = getattr(s, "breaker", None)
            bstate = getattr(breaker, "state", "?") if breaker else "?"
            out.append({"name": name, "tools": tools, "breaker": bstate})
        return out

    def _config_status(self) -> dict:
        """API 厂商连接状态（配置层面）：以配置值判断"已配置/未配置/独立或回退"。"""
        values = {k: self.config_service.get_value(k) for k in _API_KEYS}
        out = {}

        def _masked(key):
            v = values.get(key) or ""
            return (v[:4] + "****" + v[-4:]) if len(v) > 8 else ("****" if v else "")

        out["deepseek"] = {
            "url": values.get("DEEPSEEK_API_URL") or "N/A",
            "model": values.get("DEEPSEEK_MODEL") or "N/A",
            "key": _masked("DEEPSEEK_API_KEY"),
            "key_set": bool(values.get("DEEPSEEK_API_KEY")),
            "label": "DeepSeek（聊天主厂商）",
        }
        out["vision"] = {
            "url": values.get("VISION_API_URL") or "回退 DeepSeek",
            "model": values.get("VISION_MODEL") or "回退 DeepSeek",
            "key": _masked("VISION_API_KEY"),
            "key_set": bool(values.get("VISION_API_KEY")),
            "label": "视觉识图",
        }
        out["toxic"] = {
            "url": values.get("TOXIC_API_URL") or "回退 DeepSeek",
            "model": values.get("TOXIC_MODEL") or "回退 DeepSeek",
            "key": _masked("TOXIC_API_KEY"),
            "key_set": bool(values.get("TOXIC_API_KEY")),
            "label": "引战检测",
        }
        return out

    async def _handle_panel_unregister(self, request: web.Request) -> web.Response:
        """注销管理员账号：必须已登录且提供当前密码验证（防误触/防劫持）。

        只清除管理凭据（settings.db + .env 的 WEB_UI_USERNAME / WEB_UI_PASSWORD），
        其他环境配置（API Key 等）一律不动；完成后登出并回到登录页。
        """
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        password = str(form.get("password", "") or "")
        eff_user, _eff_pass = self._effective_credentials()
        if not self._verify_admin(eff_user, password):
            self._record_login_fail(request.remote or "unknown")
            return web.HTTPFound(f"/panel?tab=account&msg={quote('当前密码不正确，无法注销')}&err=1")
        _ok, message = self.config_service.unregister_account()
        # 注销成功 → 强制登出（清 token 与 cookie）
        token = request.cookies.get("fb_token", "")
        self._tokens.pop(token, None)
        self._tokens.clear()
        resp = web.HTTPFound("/panel?msg=" + quote(message))
        resp.del_cookie("fb_token")
        return resp
