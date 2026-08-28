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
            return web.json_response({"token": token, "expires_in": getattr(self.config, "WEB_UI_TOKEN_TTL_SECONDS", 3600)})
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

    async def _handle_status(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response({"error": "未认证"}, status=401)
        import time
        status = {
            "version": "0.0.1",
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

    async def _handle_index(self, request: web.Request) -> web.Response:
        html = _INDEX_HTML
        resp = web.Response(text=html, content_type="text/html", charset="utf-8")
        # 禁止缓存：保证页面更新后浏览器立即拿到新版（避免旧版 JS 造成"点不动/不跳转"）
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    # ---------- 生命周期 ----------
    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self._handle_index)
        app.router.add_post("/api/login", self._handle_login)
        app.router.add_post("/api/logout", self._handle_logout)
        app.router.add_get("/api/config", self._handle_get_config)
        app.router.add_put("/api/config", self._handle_update_config)
        app.router.add_get("/api/status", self._handle_status)
        app.router.add_get("/api/logs", self._handle_logs)
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>花璃 · 管理后台</title>
<style>
:root{--bg:#1e2229;--panel:#262b33;--panel2:#2d333d;--border:#363d48;--text:#d7dde6;--dim:#8b96a5;--accent:#5b8def;--ok:#3fb950;--err:#f85149}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);display:flex;min-height:100vh;font-size:14px}
/* 侧边栏 */
.sidebar{width:200px;background:var(--panel);border-right:1px solid var(--border);padding:16px 0;flex-shrink:0}
.logo{display:flex;align-items:center;gap:10px;padding:4px 20px 16px;border-bottom:1px solid var(--border);margin-bottom:8px}
.logo .dot{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#5b8def,#b05bef);display:flex;align-items:center;justify-content:center;font-size:18px}
.logo b{font-size:15px}.logo span{display:block;font-size:11px;color:var(--dim);font-weight:normal}
.nav{padding:0 8px}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;cursor:pointer;color:var(--dim);margin-bottom:2px;user-select:none}
.nav-item:hover{background:var(--panel2);color:var(--text)}
.nav-item.active{background:var(--accent);color:#fff}
/* 主区 */
.main{flex:1;padding:20px 24px;overflow-y:auto}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}
.topbar h1{font-size:18px}
.badge{font-size:12px;padding:3px 10px;border-radius:20px;background:var(--panel2);color:var(--dim)}
.badge.online{background:rgba(63,185,80,.15);color:var(--ok)}
.badge.offline{background:rgba(248,81,73,.15);color:var(--err)}
.page{display:none}.page.active{display:block}
/* 总览卡片 */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.card .num{font-size:22px;font-weight:600;margin-top:6px}
.card .lbl{font-size:12px;color:var(--dim)}
/* 设置表格 */
.set-item{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px;margin-bottom:8px}
.set-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.set-info{width:300px;flex-shrink:0}
.set-info .name{font-size:13px}.set-info .desc{font-size:12px;color:var(--dim);margin-top:2px}
.set-input{flex:1;min-width:160px;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:7px 10px;font-size:13px}
button{background:var(--accent);border:none;color:#fff;padding:7px 16px;border-radius:7px;cursor:pointer;font-size:13px}
button:hover{opacity:.88}
button.ghost{background:transparent;border:1px solid var(--border);color:var(--dim)}
.msg{font-size:12px;color:var(--accent);min-width:120px}
.msg.err{color:var(--err)}
.tag{font-size:11px;background:var(--panel2);border-radius:4px;padding:2px 6px;color:var(--dim)}
/* 日志 */
.logbox{background:#171a1f;border:1px solid var(--border);border-radius:10px;padding:12px;height:calc(100vh - 220px);overflow-y:auto;font-family:ui-monospace,Consolas,monospace;font-size:12px;line-height:1.7;white-space:pre-wrap;word-break:break-all}
/* 登录 */
#login{max-width:340px;margin:20vh auto;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:28px}
#login h2{margin-bottom:20px;font-size:17px}
#login input{width:100%;background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:7px;padding:9px 12px;margin-bottom:12px}
.hidden{display:none!important}
h2.sec{font-size:15px;margin:18px 0 10px;color:var(--dim)}
</style>
</head>
<body>
<div class="sidebar" id="sidebar">
  <div class="logo"><div class="dot">花</div><b>花璃<span>Flowerie · 管理后台</span></b></div>
  <div class="nav">
    <div class="nav-item active" data-page="overview">总览</div>
    <div class="nav-item" data-page="ai">AI 设置</div>
    <div class="nav-item" data-page="bot">Bot 设置</div>
    <div class="nav-item" data-page="memory">记忆</div>
    <div class="nav-item" data-page="sticker">表情包</div>
    <div class="nav-item" data-page="mcp">MCP</div>
    <div class="nav-item" data-page="policy">预算与策略</div>
    <div class="nav-item" data-page="advanced">高级</div>
    <div class="nav-item" data-page="logging">日志</div>
    <div class="nav-item" data-page="about">关于</div>
  </div>
</div>
<div class="main">
  <div class="topbar">
    <h1 id="pageTitle">总览</h1>
    <div style="display:flex;gap:8px;align-items:center">
      <span class="badge offline" id="wsBadge">未连接</span>
      <button class="ghost" onclick="logout()">退出</button>
    </div>
  </div>
  <div id="loginWrap"></div>
  <div id="app" class="hidden">
    <div class="page active" id="page-overview"></div>
    <div class="page" id="page-ai"></div>
    <div class="page" id="page-bot"></div>
    <div class="page" id="page-memory"></div>
    <div class="page" id="page-sticker"></div>
    <div class="page" id="page-mcp"></div>
    <div class="page" id="page-policy"></div>
    <div class="page" id="page-advanced"></div>
    <div class="page" id="page-logging">
      <div id="logSettings"></div>
      <div style="margin-bottom:8px"><button class="ghost" onclick="loadLogs()">刷新日志</button></div>
      <div class="logbox" id="logbox"></div>
    </div>
    <div class="page" id="page-about">
      <div class="card">
        <p><b>花璃 Flowerie</b> v0.0.1</p>
        <p class="lbl" style="margin-top:8px">DeepSeek 驱动 · NapCat OneBot11 · SQLite 存储</p>
        <p class="lbl">架构审计与完整文档见仓库 docs/</p>
      </div>
    </div>
  </div>
</div>
<script>
let token = localStorage.getItem("fb_token") || null;
const CATS = {ai:"AI 设置",bot:"Bot 设置",memory:"记忆",sticker:"表情包",mcp:"MCP",logging:"日志设置",policy:"预算与策略",advanced:"高级（需重启）"};
const PAGES = {ai:"AI",bot:"Bot",memory:"Memory",sticker:"Sticker",mcp:"MCP",policy:"Policy",logging:"Logging",advanced:"Advanced"};
async function api(url, method, body){
  const opt = {method, headers:{}};
  if (token) opt.headers["Authorization"] = "Bearer " + token;
  if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(url, opt);
  // token 过期/失效：清掉并回到登录页（否则所有设置页会是空的"点不动"）
  if (r.status === 401 && token) { token = null; localStorage.removeItem("fb_token"); showLogin(); }
  try { return {status:r.status, data:await r.json()}; } catch(e) { return {status:r.status, data:{}}; }
}
async function login(){
  const r = await api("/api/login", "POST", {username:u.value, password:p.value});
  if (r.data.token){ token = r.data.token; localStorage.setItem("fb_token", token); boot(); }
  else loginMsg.textContent = r.data.error || "登录失败";
}
function logout(){ token = null; localStorage.removeItem("fb_token"); location.reload(); }
async function boot(){
  if (!token) return;
  document.getElementById("loginWrap").innerHTML = "";
  document.getElementById("app").classList.remove("hidden");
  loadStatus(); loadConfigs(); loadLogs();
  setInterval(loadStatus, 5000);
}
async function loadStatus(){
  const r = await api("/api/status", "GET");
  if (r.status !== 200) return;
  const s = r.data;
  const badge = document.getElementById("wsBadge");
  badge.textContent = s.ws_connected ? "已连接" : "未连接";
  badge.className = "badge " + (s.ws_connected ? "online" : "offline");
  const m = s.metrics || {};
  document.getElementById("page-overview").innerHTML =
    `<div class="cards">
      ${card("运行时长", fmtUptime(s.uptime_seconds || 0))}
      ${card("已收消息", m.received_messages_total || 0)}
      ${card("AI 逻辑请求", m.ai_requests_total || 0)}
      ${card("AI 成功", m.ai_success_total || 0)}
      ${card("AI 失败", m.ai_failure_total || 0)}
      ${card("记忆写入", m.memory_write_total || 0)}
      ${card("发送失败", m.message_send_failure_total || 0)}
      ${card("MCP 调用", m.mcp_calls_total || 0)}
    </div>
    <div class="card"><span class="lbl">版本</span><div class="num">v${s.version || "0.0.1"}</div></div>`;
}
function card(lbl, num){ return `<div class="card"><div class="lbl">${lbl}</div><div class="num">${num}</div></div>`; }
function fmtUptime(s){ const h=Math.floor(s/3600), m=Math.floor(s%3600/60); return h+"h "+m+"m"; }
async function loadConfigs(){
  const r = await api("/api/config", "GET");
  if (r.status !== 200) return;
  for (const p of Object.keys(PAGES)) {
    const el = document.getElementById("page-" + p.toLowerCase());
    if (el) el.innerHTML = "";   // 缺页面元素时跳过，不再抛错导致整个渲染中断
  }
  const logSettings = document.getElementById("logSettings");
  if (logSettings) logSettings.innerHTML = "";
  const byCat = {};
  r.data.configs.forEach(c => { (byCat[c.category] = byCat[c.category] || []).push(c); });
  for (const [cat, items] of Object.entries(byCat)) {
    // 日志设置渲染在日志页上方，不覆盖日志查看器
    const page = cat === "Logging" ? logSettings
                                   : document.getElementById("page-" + PAGES[cat].toLowerCase());
    if (!page) continue;
    page.innerHTML = `<h2 class="sec">${CATS[cat] || cat} · ${items.length} 项</h2>` + items.map(c => `
      <div class="set-item"><div class="set-row">
        <div class="set-info"><div class="name">${c.description} ${c.hot_reload ? "" : '<span class="tag">需重启</span>'}</div><div class="desc">${c.key}${c.secret ? " · 密钥" : ""}</div></div>
        <input class="set-input" id="in_${c.key}" value="${esc(c.current)}">
        <button onclick="save('${c.key}')">保存</button>
        <span class="msg" id="msg_${c.key}"></span>
      </div></div>`).join("");
  }
}
async function save(key){
  const input = document.getElementById("in_" + key);
  const r = await api("/api/config", "PUT", {key, value: input.value});
  const msg = document.getElementById("msg_" + key);
  msg.textContent = r.data.message || "";
  msg.className = "msg" + (r.status === 200 ? "" : " err");
  if (r.status === 200) loadConfigs();
}
async function loadLogs(){
  const r = await api("/api/logs?limit=200", "GET");
  if (r.status === 200) {
    const box = document.getElementById("logbox");
    if (box) box.textContent = (r.data.logs || []).join("\n");
  }
}
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;"); }
function showLogin(){
  document.getElementById("app").classList.add("hidden");
  document.getElementById("loginWrap").innerHTML = `
    <div id="login">
      <h2>花璃 · 管理后台</h2>
      <input id="u" placeholder="用户名">
      <input id="p" type="password" placeholder="密码">
      <button style="width:100%" onclick="login()">登录</button>
      <div class="msg err" id="loginMsg"></div>
    </div>`;
}
// 导航
document.querySelectorAll(".nav-item").forEach(el => el.onclick = () => {
  document.querySelectorAll(".nav-item").forEach(x => x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
  document.getElementById("page-" + el.dataset.page).classList.add("active");
  document.getElementById("pageTitle").textContent = el.textContent.trim();
  if (el.dataset.page === "logging") loadLogs();
  if (el.dataset.page === "overview") loadStatus();
});
// 登录页
showLogin();
boot();
</script>
</body>
</html>
"""
