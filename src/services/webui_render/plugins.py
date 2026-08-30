"""webui_render/plugins.py：插件管理页（零 JS 服务端渲染）。

安全展示原则：
- 权限/保护级别/状态全部由后端数据决定，本模块只做 HTML 转义渲染
- 任何「启用/禁用/卸载/安装」按钮都必须经过认证处理器（管理员）
- 用户无 JavaScript 影响：全部为 form + input/checkbox/radio
"""
from src.services.webui_render.util import _esc

_PROTECTION_LABELS = {"normal": "Normal（推荐：完整限制）",
                      "relaxed": "Relaxed（放宽非必要限制）",
                      "unsafe": "Unsafe（仅可信插件 · 作者概不负责）"}
_PROTECTION_WARN = (
    "⚠️ 关闭插件保护（Unsafe）将允许插件以更少限制运行：请仅对可信插件使用，风险由管理员自行承担，"
    "作者概不负责。即使关闭保护，插件仍无法绕过：manifest 校验、管理员权限、进程隔离、"
    "日志、崩溃保护、资源限制与权限检查（PermissionManager）。"
)


def render_plugin_tab(plugins, protection: str = "normal", plugin_configs=None,
                      protection_warning: bool = False) -> str:
    """插件管理页：保护措施开关 / 插件列表 / 上传与 URL 安装 / 插件系统配置。"""
    parts = []
    # ---------- 插件保护措施 ----------
    opts = "".join(
        f'<label class="opt"><input type="radio" name="protection" value="{p}"'
        f'{" checked" if p == protection else ""}> {_esc(_PROTECTION_LABELS.get(p, p))}</label>'
        for p in ("normal", "relaxed", "unsafe")
    )
    parts.append(
        '<fieldset class="group"><legend>插件保护措施（Plugin Protection）</legend>'
        '<form method="post" action="/panel/plugins/protection">'
        f'{opts}'
        '<p class="hint">保护级别只影响运行时资源限制；无论哪一级，插件权限（PermissionManager）'
        '都必须在启用时由管理员批准，且不会因关闭保护而被绕过。</p>'
        f'<p class="warn">{_esc(_PROTECTION_WARN)}</p>'
        '<button type="submit" class="btn">保存保护级别</button>'
        '</form></fieldset>'
    )
    # ---------- 插件列表 ----------
    cards = []
    for p in plugins:
        pid = _esc(p["id"])
        badges = []
        badges.append(f'<span class="badge">{_esc(p["runtime"])}</span>')
        badges.append(f'<span class="badge">v{_esc(p["version"])}</span>')
        if p["manifest_valid"] is False:
            badges.append('<span class="badge err">manifest 无效</span>')
        if p["enabled"]:
            badges.append(f'<span class="badge ok">已启用 · {_esc(p["status"])}</span>')
        else:
            badges.append(f'<span class="badge">禁用 · {_esc(p["status"])}</span>')
        head = (f'<div class="mcp-card-head"><b>{_esc(p["name"])}</b>'
                f'<span class="mcp-card-meta">{pid} · {"".join(badges)}</span></div>')
        desc = (f'<div class="mcp-card-url">{_esc(p["description"])}</div>'
                if p["description"] else "")
        meta = (f'<div class="mcp-card-meta">声明权限：{_esc(", ".join(p["declared_permissions"]) or "无")}'
                f' · 已批准：{_esc(", ".join(p["approved_permissions"]) or "无")}'
                f' · 来源：{_esc(p["install_source"])}</div>')
        actions = []
        if p["manifest_valid"] and not p["enabled"]:
            perms_ui = "".join(
                f'<label class="opt"><input type="checkbox" name="perm" value="{_esc(perm)}" checked>'
                f' {_esc(perm)}</label>'
                for perm in p["declared_permissions"]
            ) or '<p class="hint">该插件未声明任何权限</p>'
            actions.append(
                '<form method="post" action="/panel/plugins/enable">'
                f'<input type="hidden" name="id" value="{pid}">'
                f'<div class="group-actions"><b>批准权限后启用：</b>{perms_ui}</div>'
                '<button type="submit" class="btn">启用插件</button></form>'
            )
        if p["enabled"]:
            actions.append(
                '<form method="post" action="/panel/plugins/disable">'
                f'<input type="hidden" name="id" value="{pid}">'
                '<button type="submit" class="btn">禁用插件</button></form>'
            )
        actions.append(
            '<form method="post" action="/panel/plugins/uninstall">'
            f'<input type="hidden" name="id" value="{pid}">'
            '<button type="submit" class="btn danger">卸载（删除文件与注册）</button></form>'
        )
        cards.append(
            f'<div class="mcp-card">{head}{desc}{meta}'
            f'<div class="range-row">{"".join(actions)}</div></div>'
        )
    parts.append(
        '<fieldset class="group"><legend>插件列表（Plugin Registry）</legend>'
        '<p class="hint">本地目录中放入插件后点「刷新扫描」即可发现；'
        '<b>发现 ≠ 自动执行</b>：新插件默认禁用，由管理员明确启用并批准权限。</p>'
        '<form method="post" action="/panel/plugins/refresh" style="margin-bottom:10px">'
        '<button type="submit" class="btn">刷新扫描插件目录</button></form>'
        + ("".join(cards) if cards else '<p class="hint">暂无插件</p>')
        + '</fieldset>'
    )
    # ---------- 安装 ----------
    parts.append(
        '<fieldset class="group"><legend>导入插件</legend>'
        '<p class="hint">支持：本地 ZIP 包（含 manifest.json）或单个 manifest.json（声明式 JSON 插件）。'
        '安装后默认禁用，需手动启用并批准权限。</p>'
        '<form method="post" action="/panel/plugins/upload" enctype="multipart/form-data">'
        '<label class="row-label">本地文件：</label>'
        '<input type="file" name="plugin_file" accept=".zip,.json" required> '
        '<button type="submit" class="btn">上传并安装</button></form>'
        '<form method="post" action="/panel/plugins/install-url" style="margin-top:8px">'
        '<label class="row-label">URL：</label>'
        '<input type="url" name="url" placeholder="https://example.com/plugin.zip" '
        'required style="width:60%"> '
        '<button type="submit" class="btn">下载并安装</button>'
        '<p class="hint">下载受到 SSRF 防护（拒绝内网/回环/私网/重定向）、大小上限、'
        '超时与 Content-Type/扩展名检查。</p></form>'
        '</fieldset>'
    )
    # ---------- 插件系统配置 ----------
    cfg_rows = ""
    allowed = ("PLUGIN_MAX_COUNT", "PLUGIN_URL_MAX_BYTES", "PLUGIN_URL_TIMEOUT",
               "PLUGIN_ZIP_MAX_UNZIPPED_BYTES", "PLUGIN_ZIP_MAX_FILES")
    for entry in (plugin_configs or []):
        key = entry.get("key", "")
        if key not in allowed:
            continue
        cfg_rows += (
            '<div class="row"><div class="row-info"><div class="row-title">'
            f'{_esc(key)}</div><div class="small">{_esc(entry.get("description", ""))}</div></div>'
            f'<div class="row-control"><input type="text" name="{_esc(key)}" value="{_esc(entry.get("current", ""))}"></div></div>'
        )
    parts.append(
        '<fieldset class="group"><legend>插件系统配置</legend>'
        '<form method="post" action="/panel/plugins/config">' + cfg_rows +
        '<button type="submit" class="btn">保存插件系统配置</button>'
        '<p class="hint">PLUGIN_PROTECTION 由上方保护措施开关管理；插件目录为配置项 PLUGIN_DIR'
        '（默认 ./plugins，需重启生效）。</p></form></fieldset>'
    )
    return "".join(parts)
