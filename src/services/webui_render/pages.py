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
        '<label>用户名</label><input name="username" type="text" required autocomplete="username">'
        '<label>密码</label><input name="password" type="password" required autocomplete="current-password">'
        '<button type="submit" class="btn">登录</button></form>'
        '<p class="foot"><a href="/panel/register">没有账号？注册管理员账号</a></p>'
        '<p style="text-align:center;color:var(--text-muted);font-size:11.5px;margin-top:14px">'
        '无 JS 兼容面板（服务端渲染）· 任意浏览器可用</p>'
        '</div></body></html>'
    )

def render_register_page(msg: str = "", ok: bool = True, closed: bool = False) -> str:
    cls = "ok" if ok else "err"
    style = "color:var(--ok)" if ok else "color:var(--err)"
    msg_html = f'<div class="{cls}" style="{style};font-size:13px;margin:10px 0">{_esc(msg)}</div>' if msg else ""
    if closed:
        # Bootstrap Lock：系统已初始化，公开注册入口永久关闭（无表单，防绕过）
        return (
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>花璃 · 注册</title><style>' + PANEL_CSS + '</style></head>'
            '<body class="theme-default" style="background-color:#F4F6FB">'
            '<div class="auth-card"><h2>注册已关闭</h2>'
            '<p class="sub">系统已完成管理员账号初始化，<strong>公开注册已永久关闭</strong>。</p>'
            + msg_html +
            '<p class="sub">修改登录账号：请使用现有账号登录后，到「用户状态」页操作。</p>'
            '<p class="foot"><a href="/panel">← 返回登录</a></p>'
            '</div></body></html>'
        )
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>花璃 · 注册</title><style>' + PANEL_CSS + '</style></head>'
        '<body class="theme-default" style="background-color:#F4F6FB">'
        '<div class="auth-card"><h2>首次注册管理员账号</h2>'
        '<p class="sub">仅限首次搭建（系统未初始化时）。注册后公开注册永久关闭；'
        '账号密码以 scrypt 哈希安全保存在服务器，登录不再依赖 .env</p>'
        + msg_html +
        '<form method="post" action="/panel/register">'
        '<label>新用户名（3~32 字符）</label><input name="username" type="text" required>'
        '<label>新密码（至少 6 位）</label><input name="password" type="password" required>'
        '<button type="submit" class="btn">注册首个管理员</button></form>'
        '<p class="foot"><a href="/panel">← 返回登录</a></p>'
        '</div></body></html>'
    )

def render_panel_page(*, theme_class: str, bg_rules: str, msg_html: str,
                      body_html: str, active_tab: str, panel_bg_css: str = "", glass: bool = False) -> str:
    tabs = [
        ("config", "/panel", "配置"),
        ("persona", "/panel?tab=persona", "人格"),
        ("knowledge", "/panel?tab=knowledge", "群聊知识"),
        ("plugins", "/panel?tab=plugins", "插件"),
        ("appearance", "/panel?tab=appearance", "外观"),
        ("logs", "/panel?tab=logs", "日志"),
        ("account", "/panel?tab=account", "用户状态"),
    ]
    tab_html = "".join(
        f'<a class="tab{" active" if tab == active_tab else ""}" href="{url}">{label}</a>'
        for tab, url, label in tabs
    )
    titles = {"config": "配置管理", "appearance": "外观美化", "logs": "日志",
              "persona": "人格管理", "knowledge": "群聊知识管理", "account": "用户状态",
              "plugins": "插件管理"}
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
        '</div></body></html>'
    )

