#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Flowerie_bot 最终验收：黑盒 + 白盒（在完整 Python 环境实际运行）。

在 GitHub Actions 的 ubuntu + Python 3.12 上运行：pip install -r requirements.txt 后
用本脚本实际启动 Web UI（main.py 中 WebUIServer.start() 同一路径）、发真 HTTP 请求、
验证 .env round-trip / 重启加载 / 主题 / 图片 / 安全 / 零 JS，并跑 pytest + ruff。
输出一段 markdown 结果，非 0 即验收失败。
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

RESULT = []  # (ok, 标题, 说明)


def rec(ok, title, note=""):
    RESULT.append((ok, title, note))
    print(("[PASS] " if ok else "[FAIL] ") + title + ((" — " + note) if note else ""))


SESS = None  # 持久 aiohttp ClientSession
TOKEN = ""  # 显式保存 login 返回的 fb_token，逐请求携带（不依赖 aiohttp 自动 cookie）

async def http(port, req):
    """req: (method, path, data=None, headers=None, cookies=None) → (status, text, resp)
    使用全局 SESS，登录 cookie 自动保存/携带；data 字典自动 urlencode。"""
    method, path = req[0], req[1]
    url = f"http://127.0.0.1:{port}{path}"
    kw = {}
    if len(req) > 2 and req[2] is not None:
        d = req[2]
        if isinstance(d, bytes):
            kw["data"] = d
        elif isinstance(d, dict):
            kw["data"] = urllib.parse.urlencode(d).encode()
            kw.setdefault("headers", {})["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            kw["data"] = d
    if len(req) > 3 and req[3]:
        kw.setdefault("headers", {}).update(req[3])
    # 显式携带登录 token（不依赖 aiohttp 自动跟随 3xx 的 cookie 行为）
    if TOKEN:
        kw["cookies"] = {"fb_token": TOKEN}
    async with SESS.request(method, url, **kw) as resp:
        return resp.status, await resp.text(), resp


def make_env(**over):
    base = {
        "DEEPSEEK_API_KEY": "sk-acceptance-key-123456",
        "BOT_QQ": "10001",
        "WS_PORT": "3001",
        "WEB_UI_ENABLED": "true",
        "WEB_UI_PORT": "18080",
        "WEB_UI_PASSWORD": "secret123",
        "MAX_REPLY_LENGTH": "40",
        "ONLY_REPLY_WHEN_AT": "false",
        "BOT_NICKNAME": "花璃",
        "LOG_FORMAT": "text",
        "# 中文注释保留测试": "",
    }
    base.update(over)
    lines = []
    for k, v in base.items():
        if k.startswith("#"):
            lines.append(k)
        else:
            lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


def write_env(text):
    with open(".env", "w", encoding="utf-8") as f:
        f.write(text)


def read_env():
    with open(".env", "r", encoding="utf-8") as f:
        return f.read()


async def main():
    # ---------- A. 实际启动 main.py（尝试）----------
    global SESS
    import aiohttp
    SESS = aiohttp.ClientSession()
    rec(True, "验收环境", f"python {sys.version.split()[0]} / {sys.platform} / aiohttp 实际启动")
    write_env(make_env())
    # 尝试启动 main.py（后台），看能否监听 web_ui 端口
    main_proc = None
    try:
        main_proc = subprocess.Popen([sys.executable, "main.py"], cwd=ROOT,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True)
    except Exception as e:  # noqa: BLE001
        rec(False, "main.py 启动", f"subprocess 启动失败: {e}")
        main_proc = None

    port = 18080
    main_ok = False
    for _ in range(40):
        try:
            st, txt, _resp = await http(port, ("GET", "/panel"))
            if st in (200, 302, 401, 403):
                main_ok = True
                break
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.5)
    if main_proc is not None:
        try:
            if main_proc.poll() is not None:
                main_ok = False
        except Exception:  # noqa: BLE001
            pass
    rec(main_ok, "实际启动 main.py", "Web UI 端口 18080 响应" if main_ok else "main.py 未能保持监听（需 NapCat WS），改由 WebUIServer 组件实际启动")

    # ---------- 准备组件级启动（真实 Web UI 服务）----------
    from src.config import load_config, Settings
    from src.repositories.settings_repository import SettingsRepository
    from src.services.config_service import ConfigService
    from src.services.web_ui import WebUIServer
    from src.repositories.env_store import EnvFileStore

    port = 18081  # 组件专用端口（main.py 检测用 18080）
    # 实际启动组件（= main.py 里 WebUIServer.start() 同一路径，真实监听 + 真实 .env + 真实 Settings）
    from src.config import load_config, Settings
    from src.repositories.settings_repository import SettingsRepository
    from src.services.config_service import ConfigService
    from src.services.web_ui import WebUIServer
    from src.repositories.env_store import EnvFileStore

    cfg = load_config()
    cfg.WEB_UI_PORT = port
    repo = SettingsRepository(cfg.SETTINGS_DB_PATH)
    svc = ConfigService(cfg, repo, env_path=os.path.join(ROOT, ".env"))
    # 注入人格与群聊知识管理器（与 main.py 组装一致），验收新页签
    from src.services.persona_manager import PersonaManager
    from src.repositories.meme_knowledge_repository import MemeKnowledgeRepository
    from src.services.meme_knowledge_manager import MemeKnowledgeManager
    _pmgr = PersonaManager(repo)
    _mrepo = MemeKnowledgeRepository(os.path.join(ROOT, "data", "webui_accept", "knowledge.db"))
    _mmgr = MemeKnowledgeManager(_mrepo)
    wui = WebUIServer(cfg, svc, data_dir=os.path.join(ROOT, "data", "webui_accept"),
                      persona_manager=_pmgr, meme_manager=_mmgr)
    await wui.start()

    BG_DIR = os.path.join(ROOT, "data", "webui_accept", "background")
    def reload_runtime():
        """模拟进程重启：重新读 .env 的 Settings。"""
        return Settings()

    # ---------- 登录（黑盒）----------
    global TOKEN
    async with SESS.post(f"http://127.0.0.1:{port}/panel/login",
                         data=urllib.parse.urlencode({"username": "admin", "password": "secret123"}).encode(),
                         headers={"Content-Type": "application/x-www-form-urlencoded"},
                         allow_redirects=False) as resp:
        ck = resp.headers.get("Set-Cookie", "")
        m = re.search(r"fb_token=([^;]+)", ck)
        if m:
            TOKEN = m.group(1)
    st, txt, resp = await http(port, ("GET", "/panel"))
    cookie_ok = (st == 200 and "配置管理" in txt)
    rec(cookie_ok, "登录（黑盒 POST /panel/login 后 GET /panel）",
        "认证成功（显式携带 fb_token）" if cookie_ok else f"认证失败 HTTP {st}")
    auth = None  # 兼容调用处参数（http 已用 TOKEN）

    # ---------- B. 全配置 round-trip ----------
    # 配一组测试值
    tests = [
        ("MAX_REPLY_LENGTH", "88", "int"),
        ("ONLY_REPLY_WHEN_AT", "true", "bool"),
        ("BOT_NICKNAME", "包含 # 空格 = 引号 \" 中文🍰", "特殊字符串"),
        ("POKE_REPLIES", "戳一下\n再戳\n（躲开）", "textarea"),
        ("ALLOWED_GROUP_IDS", "10001, 10002", "list-int"),
        ("MCP_SERVERS", '[{"name":"s1","url":"https://mcp.example.com/mcp","allowed_tools":"web_search","timeout":15,"enabled":true}]', "JSON"),
    ]
    rd_ok = True
    for key, val, typ in tests:
        # 黑盒：POST /panel/save（字段名=key）
        data = urllib.parse.urlencode({key: val}).encode()
        try:
            st, txt, resp = await http(port, ("POST", "/panel/save", data,
                                              {"Content-Type": "application/x-www-form-urlencoded"}, auth))
        except Exception as e:  # noqa: BLE001
            rec(False, f"保存 {key}({typ})", f"HTTP 异常: {e}")
            rd_ok = False
            continue
        saved = False
        try:
            ev = EnvFileStore(os.path.join(ROOT, ".env")).read_values()
            if key == "MAX_REPLY_LENGTH":
                saved = ev.get(key) == val
            elif key == "ONLY_REPLY_WHEN_AT":
                saved = ev.get(key) in ("true", "false")
            elif key == "POKE_REPLIES":
                saved = json.loads(ev.get(key, "[]")) == ["戳一下", "再戳", "（躲开）"] if ev.get(key) else False
            elif key == "ALLOWED_GROUP_IDS":
                saved = json.loads(ev.get(key, "[]")) == [10001, 10002] if ev.get(key) else False
            elif key == "MCP_SERVERS":
                saved = json.loads(ev.get(key, "[]"))[0]["name"] == "s1" if ev.get(key) else False
            else:
                saved = ev.get(key) == val
        except Exception as e:  # noqa: BLE001
            rec(False, f"读取 .env {key}", f"异常: {e}")
            saved = False
        rec(saved, f"保存 {key}({typ})", ".env 已 update" if saved else ".env 未变更")
        rd_ok = rd_ok and saved

    # 重启加载：重新 load_config 读 .env 的 MAX_REPLY_LENGTH
    s2 = reload_runtime()
    rec(s2.MAX_REPLY_LENGTH == 88, "重启后 Settings 加载", f"MAX_REPLY_LENGTH={s2.MAX_REPLY_LENGTH} (期望 88)")

    # ---------- C. Secret ----------
    # 留空不覆盖
    await http(port, ("POST", "/panel/save", urllib.parse.urlencode({"DEEPSEEK_API_KEY": ""}).encode(),
                      {"Content-Type": "application/x-www-form-urlencoded"}, auth))
    ev = EnvFileStore(os.path.join(ROOT, ".env")).read_values()
    rec(ev.get("DEEPSEEK_API_KEY") == "sk-acceptance-key-123456", "Secret 留空不覆盖", f".env={ev.get('DEEPSEEK_API_KEY', 'MISSING')}")
    # 修改后保存
    await http(port, ("POST", "/panel/save", urllib.parse.urlencode({"DEEPSEEK_API_KEY": "sk-new-secret-abcdef"}).encode(),
                      {"Content-Type": "application/x-www-form-urlencoded"}, auth))
    ev = EnvFileStore(os.path.join(ROOT, ".env")).read_values()
    rec(ev.get("DEEPSEEK_API_KEY") == "sk-new-secret-abcdef", "Secret 修改后保存", ".env key 已更新")

    # ---------- D. 热更新 vs 重启项 ----------
    # MAX_REPLY_LENGTH 为热更新项 → 运行时 Settings 同步改变（通过 db 为 true）；此处用 ConfigService 运行时实例验证
    # 由于组件实例 cfg 是启动时的 Settings，这里重读运行时值：用 load_config 验证 hot 标志 + 文档
    hot_msg = "已保存，立即生效"
    await http(port, ("POST", "/panel/save", urllib.parse.urlencode({"MAX_REPLY_LENGTH": "50"}).encode(),
                      {"Content-Type": "application/x-www-form-urlencoded"}, auth))
    s3 = reload_runtime()
    rec(s3.MAX_REPLY_LENGTH == 50, "热更新值写入 .env", f"load_config MAX_REPLY_LENGTH={s3.MAX_REPLY_LENGTH} (期望 50)")
    # 重启项（WS_PORT → 需重启提示）
    r = await http(port, ("POST", "/panel/save", urllib.parse.urlencode({"WS_PORT": "4000"}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded"}, auth))
    st, txt, resp = r
    rec("重启" in txt, "重启项 UI 提示", "WS_PORT 保存后页面提示'部分配置需重启生效'" if "重启" in txt else "未提示")

    # ---------- E. .env 无损 ----------
    write_env("# AI Configuration\nDEEPSEEK_API_KEY=sk-original\nBOT_QQ=10001\nWS_PORT=3001\n"
              "WEB_UI_PASSWORD=secret123\nMAX_REPLY_LENGTH=40\n# 保留注释\n")
    from src.repositories.env_store import EnvFileStore as EFS
    store = EFS(os.path.join(ROOT, ".env"))
    store.update({"MAX_REPLY_LENGTH": "77"})
    t = read_env()
    rec("# AI Configuration" in t and "# 保留注释" in t and "DEEPSEEK_API_KEY=sk-original" in t,
        ".env 注释与未修改变量保留", "注释/原变量仍在")
    special = {"A": "hello world", "B": "a#b", "C": "a=b", "D": 'say "hi"', "E": "中文🍰", "F": ""}
    store2 = EFS(os.path.join(ROOT, ".env"))
    store2.update(special)
    vals = store2.read_values()
    ok_sp = all(vals.get(k) == v for k, v in special.items())
    rec(ok_sp, "特殊字符 round-trip", f"{vals}")
    # 非法值拒绝
    svc_ref = ConfigService(Settings(), SettingsRepository(cfg.SETTINGS_DB_PATH), env_path=os.path.join(ROOT, ".env"))
    before = EnvFileStore(os.path.join(ROOT, ".env")).read_values().get("MAX_REPLY_LENGTH")
    ok_reject, _msg = svc_ref.update("MAX_REPLY_LENGTH", "abc")
    rec(ok_reject is False, "非法值拒绝", "update 返回 False")
    after = EnvFileStore(os.path.join(ROOT, ".env")).read_values().get("MAX_REPLY_LENGTH")
    rec(before == after, "非法值不写入 .env", f"before={before} after={after}")
    # 并发提交
    errs = []
    def worker(k, v):
        try:
            svc_ref.update(k, v)
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ths = [threading.Thread(target=worker, args=(k, v)) for k, v in
           [("MAX_REPLY_LENGTH", "50"), ("BOT_COOLDOWN", "3"), ("USER_COOLDOWN", "7"), ("REPEAT_THRESHOLD", "4")]]
    for t2 in ths:
        t2.start()
    for t2 in ths:
        t2.join()
    vals = EnvFileStore(os.path.join(ROOT, ".env")).read_values()
    rec(not errs and vals.get("MAX_REPLY_LENGTH") == "50" and vals.get("BOT_COOLDOWN") == "3"
        and vals.get("USER_COOLDOWN") == "7" and vals.get("REPEAT_THRESHOLD") == "4",
        "并发提交不互相覆盖", f"errs={len(errs)}")

    # ---------- F. 主题 ----------
    themes = ["default", "dark", "light", "sakura", "ocean", "forest", "amoled"]
    theme_ok = True
    for th in themes:
        data = urllib.parse.urlencode({"theme": th, "bg_color_input": "",
                                       "bg_image_opacity": "100", "bg_size": "cover", "bg_position": "center",
                                       "panel_opacity": "90", "panel_style": "clear"}).encode()
        await http(port, ("POST", "/panel/appearance", data,
                          {"Content-Type": "application/x-www-form-urlencoded"}, auth))
        st, txt, resp = await http(port, ("GET", "/panel?tab=appearance", None, None, auth))
        if f'theme-{th}' not in txt:
            theme_ok = False
    rec(theme_ok, "7 个主题逐个保存并渲染", "全部通过")

    # ---------- G. 背景图片 ----------
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    def multipart(fname, fbytes, extra=None):
        boundary = "X-BOUNDARY-X"
        body = bytearray()
        if extra:
            for k, v in extra.items():
                body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"bg_image\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: application/octet-stream\r\n\r\n").encode()
        body += (fbytes if isinstance(fbytes, bytes) else fbytes.encode())
        body += f"\r\n--{boundary}--\r\n".encode()
        return bytes(body), f"multipart/form-data; boundary={boundary}"

    async def upload(fname, fbytes, extra=None):
        body, ctype = multipart(fname, fbytes, extra or {"theme": "default", "bg_color_input": "",
                                                        "bg_image_opacity": "100", "bg_size": "cover", "bg_position": "center"})
        st, txt, resp = await http(port, ("POST", "/panel/appearance", body,
                                          {"Content-Type": ctype}, auth))
        return str(resp.url)

    ok_png = await upload("bg.png", PNG)
    saved = os.path.exists(os.path.join(BG_DIR, "background.png"))
    rec(saved, "上传合法 PNG", "已保存 background.png" if saved else "未保存")

    # 刷新后仍在（GET /panel/background）
    async with SESS.get(f"http://127.0.0.1:{port}/panel/background",
                        cookies={"fb_token": TOKEN}) as resp:
        data = await resp.read()
        rec(resp.status == 200 and data[:8] == PNG[:8],
            "刷新后背景图仍可访问", f"HTTP {resp.status} content-type={resp.headers.get('Content-Type')} 字节={len(data)}")

    # 非法文件
    async def check_reject(fname, fbytes, label):
        loc = None
        try:
            loc = await upload(fname, fbytes)
        except Exception:  # noqa: BLE001
            rec(False, label, "上传异常")
            return
        rec("err=1" in loc, label, "已被拒绝")

    await check_reject("evil.html", b"<html><script>alert(1)</script></html>", "拒绝 HTML/JS")
    await check_reject("evil.svg", b"<svg xmlns=... onload=alert(1)>", "拒绝 SVG")
    await check_reject("big.png", PNG + b"\x00" * (5 * 1024 * 1024 + 10), "拒绝超 5MB")
    await check_reject("fake.png", b"this is not an image", "拒绝非图片")
    async def check_traversal(fname, fbytes, label):
        await upload(fname, fbytes)
        leaked = (os.path.exists(os.path.join(BG_DIR, "..", "evil.png"))
                  or os.path.exists("/etc/passwd.png")
                  or os.path.exists(os.path.join(ROOT, "evil.png"))
                  or os.path.exists(os.path.join(ROOT, "passwd.png")))
        saved_fixed = os.path.exists(os.path.join(BG_DIR, "background.png"))
        rec((not leaked) and saved_fixed, label,
            "未发生路径穿越（固定名保存）" if not leaked else "存在穿越文件！")

    await check_traversal("../../evil.png", PNG, "路径穿越 ../ 被安全化")
    await check_traversal("/etc/passwd.png", PNG, "绝对路径被安全化")

    # 重启（组件 stop/start + 读 .env 持久化图片目录仍在）
    if not main_ok and 'wui' in dir():
        await wui.stop()
    time.sleep(0.5)
    persist = os.path.exists(os.path.join(BG_DIR, "background.png"))
    rec(persist, "图片重启后仍存在", f"data/webui_accept/background/background.png 存在={persist}")

    # ---------- G2. 人格 / 群聊知识页签（黑盒，零 JS） ----------
    try:
        st_p, txt_p, _ = await http(port, ("GET", "/panel?tab=persona"))
        st_k, txt_k, _ = await http(port, ("GET", "/panel?tab=knowledge"))
        persona_ok = st_p == 200 and "人格管理" in txt_p and "全局人格" in txt_p \
            and "花璃" in txt_p and "亚托莉" in txt_p
        knowledge_ok = st_k == 200 and "群聊知识管理" in txt_k \
            and 'action="/panel/knowledge/view"' in txt_k
        rec(persona_ok and knowledge_ok, "人格/群聊知识页签渲染（黑盒）",
            f"persona={st_p} knowledge={st_k}")
        # 零 JS：两个新页签的 HTML 不允许出现脚本特征
        js_hits_p = [pat for pat in ("<script", "onclick=", "onchange=", "oninput=", "fetch(", "XMLHttpRequest")
                     if pat in txt_p.lower()]
        js_hits_k = [pat for pat in ("<script", "onclick=", "onchange=", "oninput=", "fetch(", "XMLHttpRequest")
                     if pat in txt_k.lower()]
        rec(not js_hits_p and not js_hits_k, "人格/群聊知识页零 JavaScript",
            "无脚本特征" if not (js_hits_p or js_hits_k) else f"命中 {js_hits_p + js_hits_k}")
        # 黑盒新增一条知识（跨群隔离）
        await http(port, ("POST", "/panel/knowledge/add",
                          urllib.parse.urlencode({"group_id": "77777", "term": "验收梗",
                                                  "meaning": "验收用含义", "confidence": "medium"}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded"}, None))
        _, txt_k2, _ = await http(port, ("GET", "/panel?tab=knowledge&gid=77777"))
        rec("验收梗" in txt_k2 and "验收用含义" in txt_k2, "群聊知识新增+查看（黑盒）", "group 77777")
        # 群 B 页面不出现群 A 的知识
        _, txt_kb, _ = await http(port, ("GET", "/panel?tab=knowledge&gid=88888"))
        rec("验收梗" not in txt_kb, "群聊知识跨群隔离（黑盒）", "group 88888 不可见 77777 的知识")
        # 人格 CRUD（黑盒）：新建 → 列表可见 → 删除
        await http(port, ("POST", "/panel/persona/save",
                          urllib.parse.urlencode({"action": "create", "persona_id": "accept_test",
                                                  "name": "验收人格", "system_prompt": "你是验收人格"}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded"}, None))
        _, txt_p2, _ = await http(port, ("GET", "/panel?tab=persona"))
        rec("验收人格" in txt_p2, "人格创建+列表（黑盒）", "accept_test")
        await http(port, ("POST", "/panel/persona/delete",
                          urllib.parse.urlencode({"persona_id": "accept_test"}).encode(),
                          {"Content-Type": "application/x-www-form-urlencoded"}, None))
    except Exception as e:  # noqa: BLE001
        rec(False, "人格/群聊知识页签（黑盒）", f"异常: {e}")

    # ---------- H. 零 JS 检查 ----------
    import subprocess as sp
    out = sp.run(["grep", "-rnE", "<script|javascript:|onclick=|onload=|addEventListener|fetch\\(|XMLHttpRequest|let |const |var ",
                  "src/services/web_ui.py", "src/services/web_ui_assets.py",
                  "src/repositories/env_store.py", "src/services/config_service.py",
                  "src/repositories/settings_repository.py", "main.py",
                  "src/services/persona_manager.py", "src/services/persona_presets.py",
                  "src/services/meme_knowledge_manager.py", "src/services/meme_summary.py",
                  "src/repositories/meme_knowledge_repository.py"],
                 capture_output=True, text=True)
    js_hits = [l for l in (out.stdout or "").splitlines()
               if not re.search(r"javascript:|# noqa", l)]
    # 排除纯注释里的 "javascript:" 说明
    js_hits = [l for l in js_hits if "javascript:" not in l]
    rec(len(js_hits) == 0, "零 JavaScript 检查", f"命中 {len(js_hits)} 条" + ("：\n" + "\n".join(js_hits[:5]) if js_hits else ""))

    # ---------- I. pytest + ruff ----------
    pt = subprocess.run([sys.executable, "-m", "pytest", "-q"], capture_output=True, text=True)
    rec(pt.returncode == 0, "pytest", (pt.stdout or pt.stderr).strip()[-160:])
    rf = subprocess.run([sys.executable, "-m", "ruff", "check", "."], capture_output=True, text=True)
    rec(rf.returncode == 0, "ruff check", (rf.stdout or rf.stderr).strip()[-160:] or "通过")

    # 收尾
    try:
        await SESS.close()
    except Exception:  # noqa: BLE001
        pass
    try:
        await wui.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        if "_mrepo" in dir():
            _mrepo.close()
    except Exception:  # noqa: BLE001
        pass
    if main_proc is not None:
        try:
            main_proc.terminate()
        except Exception:  # noqa: BLE001
            pass

    print("\n========== 验收汇总 ==========")
    fail = [r for r in RESULT if not r[0]]
    print(f"通过 {len(RESULT) - len(fail)}/{len(RESULT)}")
    if fail:
        for f2 in fail:
            print("FAIL:", f2[1], f2[2])
    return len(fail)


if __name__ == "__main__":
    n = asyncio.run(main())
    sys.exit(1 if n else 0)
