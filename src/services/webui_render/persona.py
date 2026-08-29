"""webui_render 人格页：默认/全局人格 / 人格 CRUD / 群绑定 / 群聊 Prompt。"""

from src.services.webui_render.util import _esc


def render_persona_tab(personas, global_id, bindings, edit_persona=None, new=False,
                       enabled=True, default_persona_id="flowerie", default_persona_name="",
                       global_prompt="", group_prompt="", prompt_gid=None) -> str:
    """人格管理页（零 JS）：默认人格 / 全局人格 / 人格列表 / 编辑表单 / 群聊人格绑定 /
    群聊自定义 Prompt（<details> 原生折叠，无 JS）。"""
    if not enabled:
        return '<div class="msg err">人格系统未接入（persona_manager 未注入）</div>'
    if not personas:
        return '<div class="msg err">人格库为空（内置预设播种失败）</div>'
    name_of = {p["id"]: p.get("name", p["id"]) for p in personas}

    # ---- 默认人格（写清楚默认人格 id + 可热更新修改） ----
    def_opts = "".join(
        f'<option value="{_esc(p["id"])}"{" selected" if p["id"] == default_persona_id else ""}>{_esc(p.get("name"))}</option>'
        for p in personas
    )
    default_block = (
        '<fieldset class="group"><legend>默认人格（Default Persona）</legend>'
        '<form method="post" action="/panel/persona/default">'
        '<div class="row"><label class="row-info"><span class="row-title">兜底人格</span>'
        '<span class="row-key">PERSONA_DEFAULT</span></label>'
        f'<div class="row-control"><select name="persona_id">{def_opts}</select>'
        '<span class="hint">没有设置全局人格、且该群没有群聊人格时，使用此兜底人格；'
        '保存后<strong>立即生效</strong>（热更新，无需重启），并写入 .env 的 <code>PERSONA_DEFAULT</code></span>'
        '</div></div>'
        '<div class="group-actions"><button type="submit" class="btn">保存默认人格</button></div>'
        '</form></fieldset>'
    )

    # ---- 群聊自定义 Prompt 管理（按群读写；<details> 原生折叠，零 JS） ----
    gid_esc = _esc(prompt_gid) if prompt_gid else ""
    gid_placeholder = f' value="{gid_esc}"' if prompt_gid else ""
    global_prompt_esc = _esc(global_prompt)
    group_prompt_esc = _esc(group_prompt)
    prompt_block = (
        '<fieldset class="group"><legend>群聊自定义 Prompt（按群读写）</legend>'
        '<p class="hint">自定义 Prompt 作为人格补充注入（优先级低于安全规则）；'
        '群 Prompt &gt; 全局 Prompt &gt; 人格自带设定。与 /prompt 命令同一存储，管理员可读写</p>'
        '<details>'
        '<summary>全局自定义 Prompt'
        + (f'（{len(global_prompt)} 字）' if global_prompt else '（未设置）')
        + '</summary>'
        '<form method="post" action="/panel/prompt/global">'
        '<div class="row"><label class="row-info"><span class="row-title">内容</span>'
        '<span class="row-key">global_prompt</span></label>'
        f'<div class="row-control"><textarea name="content" rows="6">{global_prompt_esc}</textarea></div></div>'
        '<div class="group-actions">'
        '<button type="submit" name="action" value="set" class="btn">保存全局 Prompt</button>'
        '<button type="submit" name="action" value="reset" class="btn danger">重置（恢复默认）</button>'
        '</div></form></details>'
        '<details open>'
        '<summary>按群 Prompt'
        + (f'（群 {gid_esc}：{len(group_prompt)} 字）' if prompt_gid else '（输入群号后读写）')
        + '</summary>'
        '<form method="get" action="/panel">'
        '<input type="hidden" name="tab" value="persona">'
        '<div class="row"><label class="row-info"><span class="row-title">查看某群 Prompt</span>'
        '<span class="row-key">group_id</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:10px">'
        f'<input type="text" name="prompt_gid" placeholder="群号" required style="max-width:200px"{gid_placeholder}>'
        '<button type="submit" class="btn small">查看</button></div></div></form>'
        '<form method="post" action="/panel/prompt/group">'
        '<div class="row"><label class="row-info"><span class="row-title">群号</span>'
        '<span class="row-key">group_id</span></label>'
        f'<div class="row-control"><input type="text" name="group_id" placeholder="群号" required style="max-width:200px"{gid_placeholder}></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">内容</span>'
        '<span class="row-key">group_prompt</span></label>'
        f'<div class="row-control"><textarea name="content" rows="6">{group_prompt_esc}</textarea>'
        '<span class="hint">先在上方输入群号点「查看」载入该群当前 Prompt 再编辑；'
        '保存/重置只作用于填写的群，与其他群完全隔离</span></div></div>'
        '<div class="group-actions">'
        '<button type="submit" name="action" value="set" class="btn">保存本群 Prompt</button>'
        '<button type="submit" name="action" value="reset" class="btn danger">重置本群</button>'
        '</div></form></details>'
        '</fieldset>'
    )

    # ---- 全局人格 ----
    opts = "".join(
        f'<option value="{_esc(p["id"])}"{" selected" if p["id"] == global_id else ""}>{_esc(p.get("name"))}</option>'
        for p in personas
    )
    global_block = (
        '<fieldset class="group"><legend>全局人格（Global Persona）</legend>'
        '<form method="post" action="/panel/persona/global">'
        '<div class="row"><label class="row-info"><span class="row-title">当前全局人格</span>'
        '<span class="row-key">global_persona</span></label>'
        f'<div class="row-control"><select name="persona_id">{opts}</select>'
        '<span class="hint">没有群聊特殊设置时，所有群使用全局人格；删除群绑定时自动回退到这里</span>'
        '</div></div>'
        '<div class="group-actions"><button type="submit" class="btn">保存全局人格</button></div>'
        '</form></fieldset>'
    )

    # ---- 编辑 / 新建表单 ----
    edit_block = ""
    if edit_persona is not None or new:
        p = edit_persona or {}
        pid = _esc(p.get("id", ""))
        is_builtin = bool(p.get("builtin"))
        id_field = (f'<input type="text" name="persona_id" value="{pid}" required '
                    'placeholder="小写字母/数字/下划线/短横线">'
                    '<span class="hint">创建后不可修改</span>'
                    if new else
                    f'<input type="hidden" name="persona_id" value="{pid}"><code>{pid}</code>'
                    + ('<span class="badge">内置</span>' if is_builtin else ''))
        legend = "新建人格" if new else f"编辑人格：{_esc(p.get('name'))}"
        edit_block = (
            f'<fieldset class="group"><legend>{legend}</legend>'
            '<form method="post" action="/panel/persona/save">'
            f'<input type="hidden" name="action" value="{"create" if new else "update"}">'
            '<div class="row"><label class="row-info"><span class="row-title">人格 ID</span>'
            '<span class="row-key">persona_id</span></label>'
            f'<div class="row-control">{id_field}</div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">名称</span>'
            '<span class="row-key">name</span></label>'
            f'<div class="row-control"><input type="text" name="name" value="{_esc(p.get("name", ""))}" required></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">简介</span>'
            '<span class="row-key">description</span></label>'
            f'<div class="row-control"><input type="text" name="description" value="{_esc(p.get("description", ""))}"></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">system_prompt（人格核心文本）</span>'
            '<span class="row-key">system_prompt</span></label>'
            f'<div class="row-control"><textarea name="system_prompt" rows="12" required>{_esc(p.get("system_prompt", ""))}</textarea></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">词库参考</span>'
            '<span class="row-key">vocabulary</span></label>'
            f'<div class="row-control"><textarea name="vocabulary" rows="5">{_esc(p.get("vocabulary", ""))}</textarea>'
            '<span class="hint">选填；非空且未被 system_prompt 包含时作为「【词库参考】」段注入</span></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">行为规则</span>'
            '<span class="row-key">behavior_rules</span></label>'
            f'<div class="row-control"><textarea name="behavior_rules" rows="4">{_esc(p.get("behavior_rules", ""))}</textarea></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">回复风格</span>'
            '<span class="row-key">response_style</span></label>'
            f'<div class="row-control"><textarea name="response_style" rows="4">{_esc(p.get("response_style", ""))}</textarea></div></div>'
            '<div class="group-actions"><button type="submit" class="btn">保存人格</button>'
            '<a class="btn small" href="/panel?tab=persona">取消</a></div>'
            '</form></fieldset>'
        )

    # ---- 人格列表 ----
    cards = []
    for p in personas:
        pid = _esc(p.get("id", ""))
        name = _esc(p.get("name", ""))
        desc = _esc(p.get("description", ""))
        builtin_badge = '<span class="badge">内置</span>' if p.get("builtin") else ''
        marks = []
        if p.get("id") == global_id:
            marks.append('<span class="badge">全局</span>')
        bound_groups = [b["group_id"] for b in bindings if b.get("persona_id") == p.get("id")]
        if bound_groups:
            marks.append(f'<span class="badge">{len(bound_groups)} 个群</span>')
        marks_html = f'<span class="badges">{"".join(marks)}</span>' if marks else ''
        delete_btn = ""
        if not p.get("builtin"):
            delete_btn = (
                '<form method="post" action="/panel/persona/delete" class="inline-form">'
                f'<input type="hidden" name="persona_id" value="{pid}">'
                '<button type="submit" class="btn small danger">删除</button></form>'
            )
        cards.append(
            '<div class="mcp-card">'
            f'<div class="mcp-card-head"><b>{name}</b>{builtin_badge}{marks_html}</div>'
            f'<div class="mcp-card-meta">{pid}</div>'
            f'<div class="mcp-card-url">{desc}</div>'
            '<div class="actions-row">'
            f'<a class="btn small" href="/panel?tab=persona&edit={_esc(p.get("id"))}">编辑</a>'
            '<form method="post" action="/panel/persona/global" class="inline-form">'
            f'<input type="hidden" name="persona_id" value="{pid}">'
            '<button type="submit" class="btn small">设为全局</button></form>'
            + delete_btn +
            '</div></div>'
        )
    list_block = (
        '<fieldset class="group"><legend>人格列表（Persona 资源库）</legend>'
        + "".join(cards)
        + '<div class="actions-row"><a class="btn" href="/panel?tab=persona&new=1">新建人格</a></div>'
        + '</fieldset>'
    )

    # ---- 群聊人格绑定 ----
    gopts = "".join(
        f'<option value="{_esc(p["id"])}"{" selected" if p["id"] == global_id else ""}>{_esc(p.get("name"))}</option>'
        for p in personas
    )
    bind_rows = ""
    for b in bindings:
        gid = b.get("group_id")
        bind_rows += (
            '<div class="row"><label class="row-info"><span class="row-title">群 '
            f'{_esc(gid)}</span><span class="row-key">group_persona</span></label>'
            f'<div class="row-control"><code>{_esc(name_of.get(b.get("persona_id"), b.get("persona_id")))}</code>'
            '<div class="actions-row">'
            '<form method="post" action="/panel/persona/group" class="inline-form">'
            f'<input type="hidden" name="group_id" value="{_esc(gid)}">'
            '<input type="hidden" name="action" value="clear">'
            '<button type="submit" class="btn small danger">解除绑定（回退全局）</button></form>'
            '</div></div></div>'
        )
    if not bind_rows:
        bind_rows = '<div class="row"><div class="row-control"><span class="hint">暂无群聊人格绑定</span></div></div>'
    group_block = (
        '<fieldset class="group"><legend>群聊人格（Group Persona）</legend>'
        '<form method="post" action="/panel/persona/group">'
        '<div class="row"><label class="row-info"><span class="row-title">绑定人格到群</span>'
        '<span class="row-key">group_id + persona_id</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        '<input type="text" name="group_id" placeholder="群号" required style="max-width:200px">'
        f'<select name="persona_id" style="max-width:220px">{gopts}</select>'
        '<input type="hidden" name="action" value="set">'
        '<button type="submit" class="btn small">绑定</button></div>'
        '<span class="hint">优先级：本群人格 &gt; 全局人格 &gt; 内置默认；解除绑定自动回退</span>'
        '</div></form>'
        '<div class="row"><label class="row-info"><span class="row-title">当前绑定</span>'
        '<span class="row-key">bindings</span></label>'
        '<div class="row-control">' + bind_rows + '</div></div>'
        '</fieldset>'
    )

    return default_block + global_block + prompt_block + edit_block + list_block + group_block

