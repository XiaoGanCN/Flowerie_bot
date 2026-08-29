"""webui_render 群聊知识页：按群查看 / 搜索 / 增删改（严格隔离）。"""

from src.services.webui_render.util import _esc


def render_knowledge_tab(group_id, rows, search="", count=0, max_memes=500,
                         enabled=True) -> str:
    """群聊知识管理页（零 JS）：输入群号查看，搜索/新增/编辑/删除（严格按群隔离）。"""
    if not enabled:
        return '<div class="msg err">群聊知识系统未接入（meme_manager 未注入）</div>'
    q = _esc(search)
    view_block = (
        '<fieldset class="group"><legend>群聊梗/黑话知识（Group Meme Knowledge）</legend>'
        '<form method="post" action="/panel/knowledge/view">'
        '<div class="row"><label class="row-info"><span class="row-title">查看指定群的知识</span>'
        '<span class="row-key">group_id</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
        '<input type="text" name="group_id" placeholder="群号" required style="max-width:200px"'
        + (f' value="{_esc(group_id)}"' if group_id else '') + '>'
        '<button type="submit" class="btn small">查看</button></div>'
        '<span class="hint">知识按群完全隔离：查看/编辑/删除都只作用于输入的群，其他群的数据不可见</span>'
        '</div></form>'
        + (f'<div class="mcp-card-meta">当前群 {_esc(group_id)}：共 {count} 条（上限 {max_memes}）</div>' if group_id else '')
        + '</fieldset>'
    )
    if not group_id:
        return view_block

    # 搜索
    search_block = (
        '<fieldset class="group"><legend>搜索</legend>'
        f'<form method="get" action="/panel">'
        f'<input type="hidden" name="tab" value="knowledge">'
        f'<input type="hidden" name="gid" value="{_esc(group_id)}">'
        '<div class="row"><div class="row-control" style="flex-direction:row;gap:10px">'
        f'<input type="text" name="q" placeholder="按词条/含义搜索" value="{q}" style="max-width:300px">'
        '<button type="submit" class="btn small">搜索</button>'
        '<a class="btn small" href="/panel?tab=knowledge&gid=' + _esc(group_id) + '">全部</a>'
        '</div></div></form></fieldset>'
    )

    # 新增
    add_block = (
        '<fieldset class="group"><legend>新增知识（管理员手动）</legend>'
        '<form method="post" action="/panel/knowledge/add">'
        f'<input type="hidden" name="group_id" value="{_esc(group_id)}">'
        '<div class="row"><label class="row-info"><span class="row-title">词条</span>'
        '<span class="row-key">term</span></label>'
        '<div class="row-control"><input type="text" name="term" required placeholder="如：电子宠物"></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">含义</span>'
        '<span class="row-key">meaning</span></label>'
        '<div class="row-control"><textarea name="meaning" rows="2" required></textarea></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">例句</span>'
        '<span class="row-key">examples</span></label>'
        '<div class="row-control"><textarea name="examples" rows="2"></textarea></div></div>'
        '<div class="row"><label class="row-info"><span class="row-title">可信度</span>'
        '<span class="row-key">confidence</span></label>'
        '<div class="row-control" style="flex-direction:row;gap:18px;flex-wrap:wrap">'
        '<label class="opt"><input type="radio" name="confidence" value="low"> 低</label>'
        '<label class="opt"><input type="radio" name="confidence" value="medium" checked> 中</label>'
        '<label class="opt"><input type="radio" name="confidence" value="high"> 高</label></div>'
        '<span class="hint">知识只是「群聊知识」不是绝对事实；高可信需群内长期使用/多来源验证</span></div>'
        '<div class="group-actions"><button type="submit" class="btn">添加知识</button></div>'
        '</form></fieldset>'
    )

    # 列表（含编辑/删除）
    rows_html = ""
    for r in rows:
        rid = _esc(r.get("id"))
        term = _esc(r.get("term"))
        meaning = _esc(r.get("meaning"))
        examples = _esc(r.get("examples"))
        confidence = r.get("confidence") or "low"
        status = r.get("status") or "active"
        conf_label = {"low": "低", "medium": "中", "high": "高"}.get(confidence, confidence)
        conf_opts = "".join(
            f'<option value="{c}"{" selected" if c == confidence else ""}>{label}</option>'
            for c, label in (("low", "低"), ("medium", "中"), ("high", "高"))
        )
        status_opts = "".join(
            f'<option value="{s}"{" selected" if s == status else ""}>{label}</option>'
            for s, label in (("active", "活跃"), ("inactive", "停用"))
        )
        rows_html += (
            '<div class="mcp-card">'
            f'<div class="mcp-card-head"><b>{term}</b>'
            f'<span class="badge">可信度：{conf_label}</span>'
            + ('<span class="badge warn">停用</span>' if status != "active" else '')
            + '</div>'
            f'<div class="mcp-card-meta">来源：{_esc(r.get("source") or "manual")} · '
            f'更新：{_esc(r.get("updated_at") or "")}</div>'
            f'<div class="mcp-card-url">{meaning}</div>'
            + (f'<div class="mcp-card-test ok">例句：{examples}</div>' if examples else '')
            + '<form method="post" action="/panel/knowledge/save">'
            f'<input type="hidden" name="id" value="{rid}">'
            f'<input type="hidden" name="group_id" value="{_esc(group_id)}">'
            '<div class="row"><label class="row-info"><span class="row-title">含义</span>'
            '<span class="row-key">meaning</span></label>'
            f'<div class="row-control"><textarea name="meaning" rows="2">{meaning}</textarea></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">例句</span>'
            '<span class="row-key">examples</span></label>'
            f'<div class="row-control"><textarea name="examples" rows="2">{examples}</textarea></div></div>'
            '<div class="row"><label class="row-info"><span class="row-title">可信度/状态</span>'
            '<span class="row-key">confidence/status</span></label>'
            f'<div class="row-control" style="flex-direction:row;gap:10px;flex-wrap:wrap">'
            f'<select name="confidence" style="max-width:120px">{conf_opts}</select>'
            f'<select name="status" style="max-width:120px">{status_opts}</select>'
            '<button type="submit" class="btn small">保存</button></div></div></form>'
            '<div class="actions-row">'
            '<form method="post" action="/panel/knowledge/delete" class="inline-form">'
            f'<input type="hidden" name="id" value="{rid}">'
            f'<input type="hidden" name="group_id" value="{_esc(group_id)}">'
            '<button type="submit" class="btn small danger">删除</button></form>'
            '</div></div>'
        )
    if not rows_html:
        rows_html = '<div class="row"><div class="row-control"><span class="hint">该群暂无知识'
        if search:
            rows_html += f'（搜索「{q}」无结果）'
        rows_html += '</span></div></div>'
    list_block = (
        '<fieldset class="group"><legend>知识列表</legend>' + rows_html +
        '<div class="actions-row">'
        '<form method="post" action="/panel/knowledge/clear" class="inline-form">'
        f'<input type="hidden" name="group_id" value="{_esc(group_id)}">'
        '<button type="submit" class="btn danger">清空本群全部知识</button></form>'
        '</div></fieldset>'
    )
    return view_block + search_block + add_block + list_block

