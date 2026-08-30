"""webui_render 用户状态页：当前账户 / 注销 / 服务器与集成状态（零 JS）。"""

from src.services.webui_render.util import _esc


def render_account_tab(username, credential_source, system_info, mcp_status, api_status,
                       enabled=True) -> str:
    """用户状态页：账户信息 + 注销表单 + 服务器/MCP/API 状态。"""
    if not enabled:
        return '<div class="msg err">用户状态页未接入</div>'

    # ---- 账户信息 + 修改账号 + 注销 ----
    account_block = (
        '<fieldset class="group"><legend>当前管理员</legend>'
        '<div class="row"><label class="row-info"><span class="row-title">登录账号</span>'
        '<span class="row-key">username</span></label>'
        f'<div class="row-control"><code>{_esc(username)}</code>'
        f'<span class="hint">凭据来源：{_esc(credential_source)}</span></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">修改登录账号</span>'
        '<span class="row-key">credentials</span></label>'
        '<div class="row-control">'
        '<form method="post" action="/panel/account/credentials">'
        '<div class="row-control" style="flex-direction:column;gap:8px;align-items:stretch">'
        '<input type="text" name="username" placeholder="新用户名（3~32 字符）" '
        'autocomplete="username" required>'
        '<input type="password" name="password" placeholder="新密码（至少 6 位）" '
        'autocomplete="new-password" required>'
        '<input type="password" name="current_password" placeholder="当前密码（用于确认）" '
        'autocomplete="current-password" required>'
        '<button type="submit" class="btn">修改账号</button></div>'
        '<span class="hint">已初始化的系统不允许公开注册；此处是唯一的账号修改入口'
        '（需当前密码，改密后需重新登录）。</span>'
        '</form></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">注销账号</span>'
        '<span class="row-key">unregister</span></label>'
        '<div class="row-control">'
        '<form method="post" action="/panel/account/unregister">'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        '<input type="password" name="password" placeholder="输入当前密码确认注销" '
        'autocomplete="current-password" required style="max-width:260px">'
        '<button type="submit" class="btn danger">注销账号</button></div>'
        '<span class="hint">注销将<strong>只清除管理账号与密码</strong>（settings.db 与 .env 中的 '
        '<code>WEB_UI_USERNAME</code>/<code>WEB_UI_PASSWORD</code>），其他环境配置（API Key 等）一律不动；'
        '注销=显式重置，系统回到 UNINITIALIZED（可重新首次注册）。'
        '若 <code>WEB_UI_ENABLED=true</code>，注销后需重新配置密码或注册才能登录。</span>'
        '</form></div></div></fieldset>'
    )

    # ---- 服务器状态 ----
    si = system_info or {}
    sys_block = (
        '<fieldset class="group"><legend>服务器状态</legend>'
        '<div class="row"><label class="row-info"><span class="row-title">平台</span>'
        f'<span class="row-key">platform</span></label><div class="row-control"><code>{_esc(si.get("platform", "N/A"))} {_esc(si.get("release", ""))}</code></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">架构 / 主机</span>'
        f'<span class="row-key">machine / hostname</span></label><div class="row-control"><code>{_esc(si.get("machine", "N/A"))} @ {_esc(si.get("hostname", "N/A"))}</code></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">Python</span>'
        f'<span class="row-key">python</span></label><div class="row-control"><code>{_esc(si.get("python", "N/A"))}</code></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">内存占用</span>'
        f'<span class="row-key">memory</span></label><div class="row-control"><code>{_esc(si.get("memory", "N/A"))}</code> '
        '<span class="hint">MemTotal / MemAvailable（/proc/meminfo）</span></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">CPU 负载</span>'
        f'<span class="row-key">loadavg</span></label><div class="row-control"><code>{_esc(si.get("loadavg", "N/A"))}</code> '
        '<span class="hint">/proc/loadavg 1 分钟</span></div></div>'
        '</fieldset>'
    )

    # ---- MCP 工具状态 ----
    if mcp_status:
        rows = "".join(
            '<div class="row"><label class="row-info"><span class="row-title">'
            f'{_esc(s.get("name"))}</span><span class="row-key">mcp_server</span></label>'
            '<div class="row-control">'
            f'<span class="badge">{s.get("tools", 0)} 个工具</span> '
            f'<code>熔断：{_esc(s.get("breaker", "?"))}</code></div></div>'
            for s in mcp_status
        )
        mcp_block = (
            '<fieldset class="group"><legend>MCP 工具状态</legend>'
            + rows +
            '<div class="hint">MCP 已启用；以上为各 server 已同步工具数与熔断状态</div></fieldset>'
        )
    else:
        mcp_block = (
            '<fieldset class="group"><legend>MCP 工具状态</legend>'
            '<div class="row"><div class="row-control"><span class="hint">MCP 未启用'
            '（在「配置」页设置 <code>MCP_ENABLED=true</code> 并配置 server）</span></div></div></fieldset>'
        )

    # ---- API 厂商连接状态（配置层面） ----
    def _api_card(label, st):
        st = st or {}
        key_state = '<span class="badge ok">已配置</span>' if st.get("key_set") else '<span class="badge warn">未配置 Key</span>'
        return (
            '<div class="mcp-card">'
            f'<div class="mcp-card-head"><b>{_esc(label)}</b>{key_state}</div>'
            f'<div class="mcp-card-url">地址：{_esc(st.get("url", "N/A"))} · 模型：{_esc(st.get("model", "N/A"))}'
            + (' · Key：' + _esc(st.get("key")) if st.get("key") else "")
            + '</div>'
            '</div>'
        )
    api_status = api_status or {}
    api_block = (
        '<fieldset class="group"><legend>API 厂商连接状态（配置层面）</legend>'
        + _api_card("DeepSeek（聊天主厂商）", api_status.get("deepseek"))
        + _api_card("视觉识图", api_status.get("vision"))
        + _api_card("引战检测", api_status.get("toxic"))
        + '<div class="hint">此处展示的是配置状态（地址/模型/Key 是否设置），'
        '实际连通性取决于网络与凭据有效性；视觉/引战未独立配置时回退使用 DeepSeek。</div>'
        '</fieldset>'
    )

    return account_block + sys_block + mcp_block + api_block
