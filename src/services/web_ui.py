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
import json
import secrets
import time
from typing import Dict, Optional

from aiohttp import web

from src.config import Settings
from src.services.config_service import ConfigService
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_LOGIN_FAIL_LIMIT = 5
_LOGIN_FAIL_WINDOW = 60


class WebUIServer:
    def __init__(self, config: Settings, config_service: ConfigService):
        self.config = config
        self.config_service = config_service
        self._tokens: Dict[str, float] = {}  # token -> expire_at
        self._login_fails: Dict[str, list] = {}  # ip -> [timestamps]
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    # ---------- 认证 ----------
    def _issue_token(self) -> str:
        token = secrets.token_hex(24)
        self._tokens[token] = time.time() + max(60, self.config.WEB_UI_TOKEN_TTL_SECONDS)
        return token

    def _check_token(self, request: web.Request) -> bool:
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
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
        if (username == self.config.WEB_UI_USERNAME and
                secrets.compare_digest(password, self.config.WEB_UI_PASSWORD or "")):
            token = self._issue_token()
            logger.info("web_ui login success", extra={"event": "config_reload"})
            return web.json_response({"token": token, "expires_in": self.config.WEB_UI_TOKEN_TTL_SECONDS})
        self._record_login_fail(ip)
        return web.json_response({"error": "用户名或密码错误"}, status=401)

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

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = _INDEX_HTML
        return web.Response(text=html, content_type="text/html", charset="utf-8")

    # ---------- 生命周期 ----------
    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_put("/api/config", self._handle_update_config)
        return app

    async def start(self) -> None:
        app = self.build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.config.WEB_UI_HOST, self.config.WEB_UI_PORT)
        await self._site.start()
        logger.info("Web UI started on %s:%s", self.config.WEB_UI_HOST, self.config.WEB_UI_PORT,
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


_INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>花璃 · 管理后台</title>
<style>
body{font-family:sans-serif;max-width:860px;margin:24px auto;padding:0 16px;background:#f7f7fb;color:#333}
h1{font-size:22px} h2{font-size:16px;margin:20px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px}
.card{background:#fff;border-radius:8px;padding:12px 16px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
label{width:280px;font-size:13px} .desc{color:#888;font-size:12px;width:280px}
input{flex:1;min-width:180px;padding:6px 8px;border:1px solid #ccc;border-radius:6px}
button{padding:6px 14px;border:none;border-radius:6px;background:#5b8def;color:#fff;cursor:pointer}
button:hover{opacity:.9} .msg{font-size:12px;color:#5b8def} .badge{font-size:11px;background:#eee;border-radius:4px;padding:2px 6px}
.hidden{display:none} .toolbar{display:flex;gap:8px;align-items:center;margin-bottom:16px}
</style>
</head>
<body>
<h1>🌸 花璃 · 管理后台</h1>
<div id="login" class="card">
  <div class="row"><label>用户名</label><input id="u" placeholder="admin"></div>
  <div class="row"><label>密码</label><input id="p" type="password" placeholder="******"></div>
  <div class="row"><button onclick="login()">登录</button><span id="loginMsg" class="msg"></span></div>
</div>
<div id="panel" class="hidden">
  <div class="toolbar">
    <button onclick="logout()">退出</button>
    <span class="msg" id="msg"></span>
    <span class="badge">修改后立即生效（Advanced 类需重启）</span>
  </div>
  <div id="configs"></div>
</div>
<script>
let token = null;
async function api(url, method, body){
  const opt = {method, headers: {}};
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  return r.json();
}
async function login(){
  const r = await api("/api/login", "POST", {username: u.value, password: p.value});
  if (r.token) { token = r.token; show(); } else { loginMsg.textContent = r.error || "登录失败"; }
}
async function logout(){ await api("/api/logout", "POST"); token = null; location.reload(); }
async function show(){
  document.getElementById("login").classList.add("hidden");
  document.getElementById("panel").classList.remove("hidden");
  const r = await api("/api/config", "GET");
  render(r.configs || []);
}
function render(configs){
  const el = document.getElementById("configs");
  el.innerHTML = "";
  const byCat = {};
  configs.forEach(c => { (byCat[c.category] = byCat[c.category] || []).push(c); });
  for (const [cat, items] of Object.entries(byCat)) {
    el.innerHTML += `<h2>${cat}</h2>`;
    items.forEach(c => {
      el.innerHTML += `<div class="card"><div class="row">
        <div><label>${c.description}</label><div class="desc">${c.key}${c.hot_reload ? "" : "（需重启）"}</div></div>
        <input id="in_${c.key}" value="${escapeHtml(c.current)}" ${c.type === "bool" ? "" : ""}>
        <button onclick="save('${c.key}')">保存</button>
        <span class="msg" id="msg_${c.key}"></span>
      </div></div>`;
    });
  }
}
function escapeHtml(s){ return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;"); }
async function save(key){
  const input = document.getElementById("in_" + key);
  const r = await api("/api/config", "PUT", {key, value: input.value});
  document.getElementById("msg_" + key).textContent = r.message || "";
}
</script>
</body>
</html>
"""
