"""web_ui_assets：无 JS 面板的纯静态资源与渲染助手（HTML/CSS，零 JavaScript）。

- THEMES：内置主题（CSS variables 驱动，body class="theme-xxx" 切换）
- PANEL_CSS：现代响应式样式（flex/grid/transitions/backdrop-filter，
  适配 PC / 平板 / 手机，无任何脚本）
- 渲染函数：登录页 / 注册页 / 面板页（配置分组表单 + 外观 + 日志）
- background_rules：背景颜色 + 图片透明度合成同一视觉层（纯 CSS 渐变遮罩）
"""
import html as _html

# ---------------------------------------------------------------- 主题
THEMES = {
    "default": {
        "label": "默认",
        "desc": "明亮清爽的默认配色",
        "bg": "#F4F6FB",
        "vars": {
            "--panel-rgb": "255,255,255",
            "--panel-alpha": "0.9",
            "--panel-border": "#E3E7EF",
            "--text": "#3A4456",
            "--text-muted": "#7C8798",
            "--heading": "#1F2637",
            "--accent": "#5B8DEF",
            "--accent-hover": "#4A7AE0",
            "--accent-soft": "rgba(91, 141, 239, 0.12)",
            "--input-bg": "#FFFFFF",
            "--input-border": "#D9DEEA",
            "--ok": "#1A7F37",
            "--err": "#CF222E",
            "--shadow": "0 10px 30px rgba(50, 60, 90, .12)",
        },
    },
    "dark": {
        "label": "深色",
        "desc": "更深的近黑风格，护眼",
        "bg": "#121417",
        "vars": {
            "--panel-rgb": "24,27,31",
            "--panel-alpha": "0.9",
            "--panel-border": "#2a2f37",
            "--text": "#c9d1d9",
            "--text-muted": "#768390",
            "--heading": "#e1e6ec",
            "--accent": "#6cb6ff",
            "--accent-hover": "#539bf5",
            "--accent-soft": "rgba(108, 182, 255, 0.13)",
            "--input-bg": "#1c2026",
            "--input-border": "#333a45",
            "--ok": "#57ab5a",
            "--err": "#e5534b",
            "--shadow": "0 10px 30px rgba(0,0,0,.5)",
        },
    },
    "light": {
        "label": "浅色",
        "desc": "明亮清爽的日间风格",
        "bg": "#eef1f6",
        "vars": {
            "--panel-rgb": "255,255,255",
            "--panel-alpha": "0.9",
            "--panel-border": "#d9dee8",
            "--text": "#333a45",
            "--text-muted": "#7a8290",
            "--heading": "#1f2733",
            "--accent": "#3b82f6",
            "--accent-hover": "#2563eb",
            "--accent-soft": "rgba(59, 130, 246, 0.1)",
            "--input-bg": "#f7f9fc",
            "--input-border": "#ccd3de",
            "--ok": "#1a7f37",
            "--err": "#cf222e",
            "--shadow": "0 10px 30px rgba(30, 41, 59, .12)",
        },
    },
    "sakura": {
        "label": "Sakura",
        "desc": "樱花粉，明亮的浅粉少女系",
        "bg": "#FDEEF3",
        "vars": {
            "--panel-rgb": "255,255,255",
            "--panel-alpha": "0.82",
            "--panel-border": "#f4d8e2",
            "--text": "#7a4b5e",
            "--text-muted": "#b08a98",
            "--heading": "#5a2e42",
            "--accent": "#e75480",
            "--accent-hover": "#d64072",
            "--accent-soft": "rgba(231, 84, 128, 0.14)",
            "--input-bg": "#ffffff",
            "--input-border": "#f4d8e2",
            "--ok": "#1a7f37",
            "--err": "#cf222e",
            "--shadow": "0 10px 30px rgba(231, 84, 128, 0.2)",
        },
    },
    "ocean": {
        "label": "Ocean",
        "desc": "明亮天空蓝，清澈惬意",
        "bg": "#E7F3FC",
        "vars": {
            "--panel-rgb": "255,255,255",
            "--panel-alpha": "0.88",
            "--panel-border": "#C9E3F6",
            "--text": "#24475F",
            "--text-muted": "#6B8FA8",
            "--heading": "#15354D",
            "--accent": "#0B93E7",
            "--accent-hover": "#0284C7",
            "--accent-soft": "rgba(11, 147, 231, 0.12)",
            "--input-bg": "#FFFFFF",
            "--input-border": "#C6E1F4",
            "--ok": "#1A7F37",
            "--err": "#CF222E",
            "--shadow": "0 10px 30px rgba(40, 100, 150, .14)",
        },
    },
    "forest": {
        "label": "Forest",
        "desc": "明亮草绿，清新自然",
        "bg": "#EAF6EC",
        "vars": {
            "--panel-rgb": "255,255,255",
            "--panel-alpha": "0.9",
            "--panel-border": "#CFE9D6",
            "--text": "#2F5A39",
            "--text-muted": "#7A9C82",
            "--heading": "#1F3D27",
            "--accent": "#2FA85A",
            "--accent-hover": "#228B46",
            "--accent-soft": "rgba(47, 168, 90, 0.12)",
            "--input-bg": "#FFFFFF",
            "--input-border": "#CBE7D2",
            "--ok": "#1A7F37",
            "--err": "#CF222E",
            "--shadow": "0 10px 30px rgba(60, 120, 80, .14)",
        },
    },
    "amoled": {
        "label": "AMOLED",
        "desc": "纯黑，OLED 屏最省电",
        "bg": "#000000",
        "vars": {
            "--panel-rgb": "16,16,16",
            "--panel-alpha": "0.92",
            "--panel-border": "#262626",
            "--text": "#d4d4d4",
            "--text-muted": "#7a7a7a",
            "--heading": "#f0f0f0",
            "--accent": "#8b5cf6",
            "--accent-hover": "#7c3aed",
            "--accent-soft": "rgba(139, 92, 246, 0.16)",
            "--input-bg": "#111111",
            "--input-border": "#2e2e2e",
            "--ok": "#34d399",
            "--err": "#f87171",
            "--shadow": "0 10px 30px rgba(0,0,0,.8)",
        },
    },
}

DEFAULT_THEME = "default"
THEME_ORDER = ["default", "dark", "light", "sakura", "ocean", "forest", "amoled"]


def theme_body_class(name: str) -> str:
    return f"theme-{name}" if name in THEMES else f"theme-{DEFAULT_THEME}"


def theme_default_bg(name: str) -> str:
    t = THEMES.get(name)
    return t["bg"] if t else THEMES[DEFAULT_THEME]["bg"]


def theme_default_alpha(name: str) -> float:
    """主题默认的面板不透明度（0~1）。"""
    t = THEMES.get(name)
    if not t:
        return 0.9
    try:
        return float(t["vars"].get("--panel-alpha", "0.9"))
    except (TypeError, ValueError):
        return 0.9


def theme_css_block() -> str:
    """生成全部主题的 CSS variables（body.theme-xxx 作用域）。"""
    chunks = [f".theme-{name} {{ {_join_vars(t['vars'])} }}" for name, t in THEMES.items()]
    return "\n".join(chunks)


def _join_vars(vars_dict) -> str:
    return "; ".join(f"{k}: {v}" for k, v in vars_dict.items())


# ---------------------------------------------------------------- 背景合成
def hex_to_rgb(hex_color: str):
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 30, 34, 41
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 30, 34, 41


def background_rules(bg_color: str, image_url: str, opacity: int, size: str, position: str) -> str:
    """合成独立的背景层 CSS：背景颜色 + 图片透明度 → 同一视觉层。

    背景放在 `position:fixed; inset:0` 的 `.bg-layer` 上（而非 body），这样：
    - 图片始终按**视口**大小做 `cover` 缩放并裁剪（移动端 body 的
      background-attachment: fixed 支持差，会按整页高度把图拉伸，无法比例缩放裁剪）
    - 页面滚动时背景固定不动
    图片作为第二层背景，上面叠一层"背景颜色 + (100-透明度)% 不透明度"的渐变遮罩：
    - 透明度 100% → 遮罩全透明，图片完全显示
    - 透明度 0%   → 遮罩不透明，只剩背景颜色
    图片不透明度与背景颜色因此共同组成最终背景视觉。
    """
    rules = [
        "position: fixed;",
        "inset: 0;",
        "z-index: -1;",
        "pointer-events: none;",
        f"background-color: {bg_color};",
    ]
    if image_url:
        alpha = max(0.0, min(1.0, (100 - max(0, min(100, int(opacity)))) / 100.0))
        r, g, b = hex_to_rgb(bg_color)
        overlay = f"rgba({r},{g},{b},{alpha:.3f})"
        # 第一层线性渐变是"背景颜色遮罩"，叠在第二层图片之上，透明度合成同一视觉层
        rules.append(f"background-image: linear-gradient({overlay}, {overlay}), url('{image_url}');")
        rules.append(f"background-size: cover, {size};")
        rules.append(f"background-position: center, {position};")
        rules.append("background-repeat: no-repeat, no-repeat;")
    return ".bg-layer {\n  " + "\n  ".join(rules) + "\n}"


# ---------------------------------------------------------------- CSS
PANEL_CSS = """
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",Roboto,"Noto Sans SC",sans-serif;
color:var(--text,#3A4456);min-height:100vh;-webkit-font-smoothing:antialiased;line-height:1.5}
:root{--panel-rgb:255,255,255;--panel-alpha:0.9;
--panel-bg:rgba(var(--panel-rgb),var(--panel-alpha))}
""" + theme_css_block() + """
.wrap{max-width:1080px;margin:0 auto;padding:16px 20px 72px}
.topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:16px;
background-color:rgb(var(--panel-rgb));border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);
position:relative;z-index:1}
.brand{font-size:19px;font-weight:700;letter-spacing:.5px}
.brand small{color:var(--text-muted);font-size:13px;font-weight:400;margin-left:4px}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{display:inline-block;padding:7px 15px;border-radius:999px;color:var(--text-muted);text-decoration:none;
font-size:13.5px;border:1px solid transparent;transition:all .18s ease}
.tab:hover{color:var(--text);background:var(--accent-soft)}
.tab.active{color:#fff;background:var(--accent);border-color:var(--accent)}
.tab.danger:hover{color:var(--err);background:rgba(248,81,73,.1)}
.page-title{font-size:15px;color:var(--heading);margin:0 0 14px;font-weight:600;letter-spacing:.3px}
.msg{padding:11px 15px;border-radius:10px;font-size:13.5px;margin-bottom:14px;word-break:break-all}
.msg.ok{background:rgba(63,185,80,.12);color:var(--ok);border:1px solid rgba(63,185,80,.35)}
.msg.err{background:rgba(248,81,73,.12);color:var(--err);border:1px solid rgba(248,81,73,.35)}
.group{background:var(--panel-bg);border:1px solid var(--panel-border);border-radius:14px;
box-shadow:var(--shadow);padding:16px 18px 18px;margin-bottom:18px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.group legend{font-size:14px;font-weight:600;color:var(--accent);padding:0 8px;letter-spacing:.5px}
.row{display:flex;gap:14px;align-items:flex-start;padding:11px 4px;border-bottom:1px dashed var(--panel-border)}
.row:last-of-type{border-bottom:none}
.row-info{flex:0 0 300px;display:flex;flex-direction:column;gap:2px;cursor:default}
.row-title{font-size:13.5px;color:var(--text)}
.row-key{font-size:11px;color:var(--text-muted);font-family:ui-monospace,Menlo,Consolas,monospace}
.badges{display:flex;gap:6px;margin-top:3px;flex-wrap:wrap}
.badge{font-size:10.5px;padding:1px 8px;border-radius:999px;background:var(--accent-soft);color:var(--accent)}
.badge.warn{background:rgba(210,153,34,.15);color:#d2a32a}
.row-control{flex:1;min-width:0;display:flex;flex-direction:column;gap:6px}
input[type=text],input[type=password],input[type=number],input[type=color],select,textarea{
width:100%;background:var(--input-bg);border:1px solid var(--input-border);color:var(--text);
border-radius:9px;padding:9px 12px;font-size:13.5px;font-family:inherit;transition:border-color .15s,box-shadow .15s}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
textarea{resize:vertical;line-height:1.55;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent);cursor:pointer}
input[type=range]{width:100%;accent-color:var(--accent);cursor:pointer}
input[type=color]{width:64px;height:38px;padding:3px;cursor:pointer;border-radius:9px}
input[type=text].color-text{flex:0 1 180px;width:180px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px}
.color-swatch{width:34px;height:34px;border-radius:8px;border:1px solid var(--panel-border);flex-shrink:0;display:inline-block}
.row-control code{background:var(--accent-soft);padding:1px 5px;border-radius:5px;color:var(--accent);font-size:11.5px}
.masked{font-size:12px;color:var(--text-muted)}
.hint{font-size:11.5px;color:var(--text-muted);line-height:1.5}
.cats{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 18px}
.cat{display:inline-block;padding:6px 13px;border-radius:999px;border:1px solid var(--panel-border);
background:var(--panel-bg);color:var(--text-muted);font-size:12.5px;text-decoration:none;transition:all .15s ease;white-space:nowrap}
.cat:hover{border-color:var(--accent);color:var(--text)}
.cat.active{background:var(--accent);border-color:var(--accent);color:#fff}
.group-actions{display:flex;justify-content:flex-end;margin-top:12px;padding-top:13px;border-top:1px solid var(--panel-border)}
.btn{background:var(--accent);color:#fff;border:none;border-radius:9px;padding:9px 22px;font-size:13.5px;
cursor:pointer;transition:background .18s ease;font-family:inherit}
.btn:hover{background:var(--accent-hover)}
.btn.danger{background:transparent;color:var(--err);border:1px solid rgba(248,81,73,.45)}
.btn.danger:hover{background:rgba(248,81,73,.12)}
.btn.small{padding:6px 14px;font-size:12.5px}
.theme-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:10px}
.theme-card{display:flex;gap:11px;align-items:center;padding:10px 12px;border:1px solid var(--panel-border);
border-radius:11px;cursor:pointer;transition:border-color .15s,background .15s}
.theme-card:hover{border-color:var(--accent);background:var(--accent-soft)}
.theme-card input{accent-color:var(--accent);margin:0}
.theme-swatch{width:36px;height:36px;border-radius:8px;border:1px solid var(--panel-border);flex-shrink:0}
.theme-meta{display:flex;flex-direction:column;gap:1px}
.theme-meta b{font-size:13px;color:var(--text)}
.theme-meta small{font-size:11px;color:var(--text-muted);line-height:1.4}
.bg-preview{max-width:240px;max-height:150px;border-radius:10px;border:1px solid var(--panel-border);object-fit:cover;margin-top:6px}
.range-row{display:flex;align-items:center;gap:12px}
.range-row output{font-size:12.5px;color:var(--text-muted);min-width:44px;text-align:right}
.inline-form{display:inline-block}
.actions-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
.log{background:var(--input-bg);border:1px solid var(--panel-border);border-radius:10px;padding:14px;
font-size:12px;overflow-x:auto;line-height:1.7;white-space:pre-wrap;word-break:break-all;font-family:ui-monospace,Menlo,Consolas,monospace}
.auth-card{max-width:430px;margin:9vh auto;background:var(--panel-bg);border:1px solid var(--panel-border);
border-radius:16px;box-shadow:var(--shadow);padding:30px 32px;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px)}
.auth-card h2{margin:0 0 6px;font-size:18px;color:var(--heading)}
.auth-card .sub{color:var(--text-muted);font-size:12.5px;margin:0 0 18px}
.auth-card label{display:block;font-size:12.5px;color:var(--text-muted);margin:12px 0 5px}
.auth-card .btn{width:100%;margin-top:18px;padding:11px}
.auth-card .foot{margin-top:16px;text-align:center;font-size:12.5px}
.auth-card .foot a{color:var(--accent);text-decoration:none}
.auth-card .foot a:hover{text-decoration:underline}
@media (max-width:720px){
  .wrap{padding:12px 12px 56px}
  .row{flex-direction:column;gap:8px}
  .row-info{flex-basis:auto}
  .group-actions{justify-content:stretch}
  .btn{width:100%}
  .theme-grid{grid-template-columns:1fr 1fr}
  .auth-card{margin:4vh 12px;padding:24px 20px}
}
@media (max-width:420px){.theme-grid{grid-template-columns:1fr}}
"""


# ---------------------------------------------------------------- 渲染助手
def _esc(s) -> str:
    return _html.escape("" if s is None else str(s))


def render_login_page(msg: str = "") -> str:
    msg_html = f'<div class="err" style="color:var(--err);font-size:13px;margin-bottom:10px">{_esc(msg)}</div>' if msg else ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>花璃 · 管理后台</title><style>' + PANEL_CSS + '</style></head>'
        '<body class="theme-default" style="background-color:#F4F6FB">'
        '<div class="auth-card"><h2>花璃 · 管理后台</h2>'
        '<p class="sub">登录后管理全部配置、外观美化与日志</p>'
        + msg_html +
        '<form method="post" action="/panel/login">'
        '<label>用户名</label><input name="username" required autocomplete="username">'
        '<label>密码</label><input name="password" type="password" required autocomplete="current-password">'
        '<button type="submit" class="btn">登录</button></form>'
        '<p class="foot"><a href="/panel/register">没有账号？注册管理员账号</a></p>'
        '<p style="text-align:center;color:var(--text-muted);font-size:11.5px;margin-top:14px">'
        '无 JS 兼容面板（服务端渲染）· 任意浏览器可用</p>'
        '</div></body></html>'
    )


def render_register_page(msg: str = "", ok: bool = True) -> str:
    cls = "ok" if ok else "err"
    style = "color:var(--ok)" if ok else "color:var(--err)"
    msg_html = f'<div class="{cls}" style="{style};font-size:13px;margin:10px 0">{_esc(msg)}</div>' if msg else ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>花璃 · 注册</title><style>' + PANEL_CSS + '</style></head>'
        '<body class="theme-default" style="background-color:#F4F6FB">'
        '<div class="auth-card"><h2>注册管理员账号</h2>'
        '<p class="sub">账号密码将安全保存在服务器（scrypt 哈希），登录不再依赖 .env</p>'
        + msg_html +
        '<form method="post" action="/panel/register">'
        '<label>新用户名（3~32 字符）</label><input name="username" required>'
        '<label>新密码（至少 6 位）</label><input name="password" type="password" required>'
        '<label>当前管理员密码（首次搭建可留空）</label><input name="admin_password" type="password">'
        '<button type="submit" class="btn">注册</button></form>'
        '<p class="foot"><a href="/panel">← 返回登录</a></p>'
        '</div></body></html>'
    )


def render_panel_page(*, theme_class: str, bg_rules: str, msg_html: str,
                      body_html: str, active_tab: str, panel_bg_css: str = "") -> str:
    tabs = [
        ("config", "/panel", "配置"),
        ("appearance", "/panel?tab=appearance", "外观"),
        ("logs", "/panel?tab=logs", "日志"),
    ]
    tab_html = "".join(
        f'<a class="tab{" active" if tab == active_tab else ""}" href="{url}">{label}</a>'
        for tab, url, label in tabs
    )
    titles = {"config": "配置管理", "appearance": "外观美化", "logs": "日志"}
    title = titles.get(active_tab, "配置管理")
    # panel_bg_css：服务端算好的具体 rgba(r,g,b,a) 卡片背景（保证兼容）；为空则由 CSS 主题接管
    inline_style = f' style="--panel-bg:{panel_bg_css}"' if panel_bg_css else ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>花璃 · 管理后台</title>'
        f'<style>{PANEL_CSS}</style><style>{bg_rules}</style></head>'
        f'<body class="{theme_class}"{inline_style}><div class="bg-layer" aria-hidden="true"></div><div class="wrap">'
        '<header class="topbar">'
        '<div class="brand">花璃<small>· 管理后台</small></div>'
        f'<nav class="tabs">{tab_html}'
        '<a class="tab danger" href="/panel/logout">退出</a></nav></header>'
        f'<h1 class="page-title">{title}</h1>'
        + msg_html + body_html +
        '</div></body></html>'
    )


def render_config_sections(configs, active_cat: str = "all") -> str:
    """按分类渲染配置分组表单，顶部带分类导航（点某个分类只看那一类，避免全部堆在一屏）。

    active_cat: "all" 显示全部分类；否则只显示该分类。纯 HTML + 链接跳转，零 JS。
    """
    from src.services.config_service import ConfigService
    by_cat: dict = {}
    for c in configs:
        by_cat.setdefault(c["category"], []).append(c)
    # 有内容的分类（按固定顺序）
    cats = [cat for cat in ConfigService.CATEGORY_ORDER if by_cat.get(cat)]
    if active_cat not in ("all", "") and active_cat not in by_cat:
        active_cat = "all"
    nav = _render_cat_nav(active_cat, cats, ConfigService.CATEGORY_LABELS)
    shown_cats = [active_cat] if active_cat in by_cat else cats
    sections = []
    for cat in shown_cats:
        label = ConfigService.CATEGORY_LABELS.get(cat, cat)
        rows = "".join(_render_config_row(c) for c in by_cat[cat])
        action = f"/panel/save?cat={_esc(active_cat)}" if active_cat in by_cat else "/panel/save"
        sections.append(
            f'<fieldset class="group"><legend>{_esc(label)}</legend>'
            f'<form method="post" action="{action}">{rows}'
            '<div class="group-actions"><button type="submit" class="btn">保存本组</button></div>'
            '</form></fieldset>'
        )
    return nav + "\n" + "\n".join(sections)


def _render_cat_nav(active: str, cats, labels) -> str:
    """顶部分类导航（pill 链接，响应式换行）。"""
    links = [f'<a class="cat{" active" if active in ("all", "") else ""}" href="/panel?cat=all">全部</a>']
    for cat in cats:
        label = labels.get(cat, cat)
        active_cls = " active" if cat == active else ""
        links.append(f'<a class="cat{active_cls}" href="/panel?cat={_esc(cat)}">{_esc(label)}</a>')
    return '<nav class="cats">' + "".join(links) + '</nav>'


def _render_config_row(c: dict) -> str:
    key = c["key"]
    cur = c.get("current") or ""
    badges = []
    if c.get("secret"):
        badges.append('<span class="badge">密钥</span>')
    if not c.get("hot_reload"):
        badges.append('<span class="badge warn">需重启</span>')
    badges_html = f'<span class="badges">{"".join(badges)}</span>' if badges else ""
    ctype = c["type"]
    if ctype == "bool":
        checked = ' checked' if str(cur).lower() in ("true", "1") else ""
        control = (f'<input type="hidden" name="{key}" value="false">'
                   f'<input type="checkbox" name="{key}" value="true"{checked}>')
    elif ctype == "secret":
        control = (f'<input type="password" name="{key}" placeholder="留空 = 不修改" autocomplete="new-password">'
                   f'<span class="masked">当前：{_esc(cur) if cur else "未设置"}（不显示明文）</span>')
    elif ctype in ("int", "float"):
        attrs = ""
        if c.get("min") is not None:
            attrs += f' min="{c["min"]}"'
        if c.get("max") is not None:
            attrs += f' max="{c["max"]}"'
        attrs += f' step="{c.get("step", 1)}"'
        control = f'<input type="number" name="{key}" value="{_esc(cur)}"{attrs}>'
    elif ctype in ("textarea", "json"):
        rows = c.get("rows", 6)
        control = f'<textarea name="{key}" rows="{rows}">{_esc(cur)}</textarea>'
    elif c.get("options"):
        opts = "".join(
            f'<option value="{_esc(o)}"{" selected" if str(o).lower() == str(cur).lower() else ""}>{_esc(o)}</option>'
            for o in c["options"]
        )
        control = f'<select name="{key}">{opts}</select>'
    else:
        control = f'<input type="text" name="{key}" value="{_esc(cur)}">'
    hint = ""
    if ctype == "secret":
        hint = '<span class="hint">密钥不显示明文；留空提交不会覆盖现有值</span>'
    elif not c.get("hot_reload"):
        hint = '<span class="hint">已保存到 .env，部分配置将在服务器重启后生效</span>'
    return (
        '<div class="row">'
        f'<label class="row-info"><span class="row-title">{_esc(c["description"])}</span>'
        f'<span class="row-key">{_esc(key)}</span>{badges_html}</label>'
        f'<div class="row-control">{control}{hint}</div>'
        '</div>'
    )


def render_appearance(theme: str, bg_color: str, opacity: int, size: str, position: str,
                      has_bg_image: bool, image_url: str = "", panel_opacity: int = 90) -> str:
    cards = []
    for name in THEME_ORDER:
        t = THEMES.get(name)
        if t is None:
            continue
        checked = ' checked' if name == theme else ""
        cards.append(
            f'<label class="theme-card"><input type="radio" name="theme" value="{name}"{checked}>'
            f'<span class="theme-swatch" style="background:{t["bg"]}"></span>'
            f'<span class="theme-meta"><b>{_esc(t["label"])}</b>'
            f'<small>{_esc(t["desc"])}</small></span></label>'
        )
    preview = ""
    if has_bg_image and image_url:
        preview = f'<img class="bg-preview" src="{_esc(image_url)}" alt="当前背景图">'
    opacity = max(0, min(100, int(opacity)))
    size_opts = {
        "cover": "cover（铺满，裁切边缘）",
        "contain": "contain（完整显示，留白）",
    }
    pos_opts = {
        "center": "居中",
        "top": "顶部",
        "bottom": "底部",
        "left": "左侧",
        "right": "右侧",
    }
    size_html = "".join(
        f'<option value="{k}"{" selected" if k == size else ""}>{_esc(v)}</option>' for k, v in size_opts.items()
    )
    pos_html = "".join(
        f'<option value="{k}"{" selected" if k == position else ""}>{_esc(v)}</option>' for k, v in pos_opts.items()
    )
    return (
        '<form method="post" action="/panel/appearance" enctype="multipart/form-data">'
        '<fieldset class="group"><legend>主题</legend>'
        f'<div class="theme-grid">{"".join(cards)}</div>'
        '<p class="hint">主题通过服务端渲染切换（body class），不依赖任何脚本</p>'
        '<div class="row"><label class="row-info"><span class="row-title">主题面板透明度</span>'
        '<span class="row-key">panel_opacity</span></label>'
        '<div class="row-control">'
        f'<div class="range-row"><input type="range" name="panel_opacity" min="0" max="100" value="{max(0,min(100,int(panel_opacity)))}">'
        f'<output>{max(0,min(100,int(panel_opacity)))}%</output></div>'
        '<span class="hint">面板/卡片越透明，越能透出背景图片与主题底色；100% 完全不透明</span>'
        '</div></div></fieldset>'

        '<fieldset class="group"><legend>背景颜色（跟主题绑定）</legend>'
        '<div class="row"><div class="row-control" style="flex-direction:row;align-items:center;gap:14px;flex-wrap:wrap">'
        f'<span class="color-swatch" style="background:{_esc(bg_color)}" title="当前主题背景色"></span>'
        f'<input type="text" name="bg_color_input" value="" '
        'placeholder="#RRGGBB 或 253,238,243，留空=用主题默认" class="color-text" '
        'title="留空=用当前主题默认背景；想自定义填入 #RRGGBB 或 R,G,B / rgb(r,g,b)">'
        '<span class="hint">背景颜色已随所选主题自动带好（切换主题即整套换肤，选黑色主题自动变黑）；'
        '留空用主题默认；<code>#RRGGBB</code> / <code>253,238,243</code> 输入只改当前主题</span>'
        '</div></div></fieldset>'

        '<fieldset class="group"><legend>背景图片</legend>'
        '<div class="row"><div class="row-control">'
        '<input type="file" name="bg_image" accept="image/png,image/jpeg,image/webp,image/gif">'
        f'<span class="hint">仅允许 PNG / JPEG / WEBP / GIF，最大 5MB；'
        '文件将保存到服务器持久化目录（data/webui/background/），刷新与重启均不会丢失</span>'
        + preview +
        '</div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">图片透明度</span>'
        '<span class="row-key">bg_image_opacity</span></label>'
        '<div class="row-control">'
        f'<div class="range-row"><input type="range" name="bg_image_opacity" min="0" max="100" value="{opacity}">'
        f'<output>{opacity}%</output></div></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">图片显示方式</span>'
        '<span class="row-key">bg_size / bg_position</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        f'<select name="bg_size" style="max-width:220px">{size_html}</select>'
        f'<select name="bg_position" style="max-width:160px">{pos_html}</select></div></div>'
        '</fieldset>'
        '<div class="actions-row"><button type="submit" class="btn">保存外观设置</button></div>'
        '</form>'
        '<div class="actions-row">'
        '<form method="post" action="/panel/appearance/delete-image">'
        '<button type="submit" class="btn danger">删除背景图片</button></form>'
        '<form method="post" action="/panel/appearance/restore">'
        '<button type="submit" class="btn danger">恢复默认主题</button></form>'
        '</div>'
    )
