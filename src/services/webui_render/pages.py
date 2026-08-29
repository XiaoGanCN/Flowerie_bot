"""webui_render 页面壳：登录 / 注册 / 面板页。"""

from src.services.webui_render.theme import PANEL_CSS
from src.services.webui_render.util import _esc


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
                      body_html: str, active_tab: str, panel_bg_css: str = "", glass: bool = False) -> str:
    tabs = [
        ("config", "/panel", "配置"),
        ("persona", "/panel?tab=persona", "人格"),
        ("knowledge", "/panel?tab=knowledge", "群聊知识"),
        ("appearance", "/panel?tab=appearance", "外观"),
        ("logs", "/panel?tab=logs", "日志"),
    ]
    tab_html = "".join(
        f'<a class="tab{" active" if tab == active_tab else ""}" href="{url}">{label}</a>'
        for tab, url, label in tabs
    )
    titles = {"config": "配置管理", "appearance": "外观美化", "logs": "日志",
              "persona": "人格管理", "knowledge": "群聊知识管理"}
    title = titles.get(active_tab, "配置管理")
    # panel_bg_css：服务端算好的具体 rgba(r,g,b,a) 卡片背景（保证兼容）；为空则由 CSS 主题接管
    inline_style = f' style="--panel-bg:{panel_bg_css}"' if panel_bg_css else ""
    body_class = theme_class + (" pglass" if glass else "")
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>花璃 · 管理后台</title>'
        f'<style>{PANEL_CSS}</style><style>{bg_rules}</style></head>'
        f'<body class="{body_class}"{inline_style}><div class="bg-layer" aria-hidden="true"></div><div class="wrap">'
        '<header class="topbar">'
        '<div class="brand">花璃<small>· 管理后台</small></div>'
        f'<nav class="tabs">{tab_html}'
        '<a class="tab danger" href="/panel/logout">退出</a></nav></header>'
        f'<h1 class="page-title">{title}</h1>'
        + msg_html + body_html +
        '<fieldset class="group"><legend>注销管理员账号</legend>'
        '<div class="row"><label class="row-info"><span class="row-title">注销</span>'
        '<span class="row-key">unregister</span></label>'
        '<div class="row-control">'
        '<form method="post" action="/panel/account/unregister">'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        '<input type="password" name="password" placeholder="输入当前密码确认注销" '
        'autocomplete="current-password" required style="max-width:260px">'
        '<button type="submit" class="btn danger">注销账号</button></div>'
        '<span class="hint">注销将<strong>只清除管理账号与密码</strong>（settings.db 与 .env 中的 '
        '<code>WEB_UI_USERNAME</code>/<code>WEB_UI_PASSWORD</code>），'
        '其他环境配置（API Key 等）一律不动；注销后需重新注册或配置密码才能登录。</span>'
        '</form></div></div></fieldset>'
        '</div></body></html>'
    )

