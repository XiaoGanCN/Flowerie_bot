"""Web UI 人格 / 群聊知识管理页测试（v1.0.1 新增，零 JS）。

任务覆盖：28~36 项（Global Persona UI / Persona CRUD UI / Group Persona UI /
Meme UI / Group isolation / Auth / 无 JS / HTML-only interaction）。
"""
import os
import tempfile

from src.repositories.meme_knowledge_repository import MemeKnowledgeRepository
from src.repositories.settings_repository import SettingsRepository
from src.services.config_service import ConfigService
from src.services.meme_knowledge_manager import MemeKnowledgeManager
from src.services.persona_manager import PersonaManager
from src.services.web_ui import WebUIServer
from tests.test_config_service import FakeSettings
from tests.test_web_ui_panel import FakeRequest, _resp_text


def _make_stack(tmp):
    repo = SettingsRepository(os.path.join(tmp, "settings.db"))
    config = FakeSettings(DEEPSEEK_API_KEY="sk-secret-key-1234567890")
    config.WEB_UI_USERNAME = "admin"
    config.WEB_UI_PASSWORD = "secret123"
    config.WEB_UI_TOKEN_TTL_SECONDS = 3600
    svc = ConfigService(config, repo, env_path=os.path.join(tmp, ".env"))
    pmgr = PersonaManager(repo)
    mrepo = MemeKnowledgeRepository(os.path.join(tmp, "knowledge.db"))
    mmgr = MemeKnowledgeManager(mrepo)
    from src.services.prompt_manager import PromptManager
    prompt_mgr = PromptManager(repo)
    server = WebUIServer(config, svc, data_dir=os.path.join(tmp, "webui"),
                         persona_manager=pmgr, meme_manager=mmgr, prompt_manager=prompt_mgr)
    return config, repo, svc, server, pmgr, mmgr


async def _login(server):
    resp = await server._handle_panel_login(
        FakeRequest(form={"username": "admin", "password": "secret123"}))
    assert resp.status == 302
    return resp.cookies.get("fb_token").value


# ---------- 28. Global Persona UI ----------
async def test_persona_tab_renders_global_and_builtins():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                    cookies={"fb_token": cookie}))
        text = _resp_text(resp)
        assert "全局人格" in text
        assert "花璃" in text and "亚托莉" in text      # 内置预设已播种
        assert 'action="/panel/persona/global"' in text
        assert "新建人格" in text


async def test_persona_set_global_via_form():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, pmgr, _ = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel_persona_global(
            FakeRequest(form={"persona_id": "atri"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.resolve_persona_id() == "atri"


# ---------- 29. Persona CRUD UI ----------
async def test_persona_create_update_delete_via_forms():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, pmgr, _ = _make_stack(td)
        cookie = await _login(server)
        # 创建
        resp = await server._handle_panel_persona_save(FakeRequest(
            form={"action": "create", "persona_id": "yukikaze", "name": "雪风",
                  "description": "驱逐舰", "system_prompt": "你是雪风 认真的驱逐舰",
                  "vocabulary": "", "behavior_rules": "", "response_style": ""},
            cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.get_persona("yukikaze") is not None
        # 编辑表单可渲染
        page = await server._handle_panel(FakeRequest(
            query={"tab": "persona", "edit": "yukikaze"}, cookies={"fb_token": cookie}))
        assert "认真的驱逐舰" in _resp_text(page)
        # 更新
        resp = await server._handle_panel_persona_save(FakeRequest(
            form={"action": "update", "persona_id": "yukikaze", "name": "雪风改",
                  "system_prompt": "你是雪风改 更强的驱逐舰"},
            cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.get_persona("yukikaze")["name"] == "雪风改"
        # 删除
        resp = await server._handle_panel_persona_delete(FakeRequest(
            form={"persona_id": "yukikaze"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.get_persona("yukikaze") is None
        # 内置人格删除被拒绝
        resp = await server._handle_panel_persona_delete(FakeRequest(
            form={"persona_id": "flowerie"}, cookies={"fb_token": cookie}))
        assert pmgr.get_persona("flowerie") is not None


# ---------- 30. Group Persona UI ----------
async def test_persona_group_bind_and_clear_via_forms():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, pmgr, _ = _make_stack(td)
        cookie = await _login(server)
        # 绑定
        resp = await server._handle_panel_persona_group(FakeRequest(
            form={"action": "set", "group_id": "100", "persona_id": "atri"},
            cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.resolve_persona_id(100) == "atri"
        # 绑定页显示
        page = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                     cookies={"fb_token": cookie}))
        assert "群 100" in _resp_text(page)
        # 解除
        resp = await server._handle_panel_persona_group(FakeRequest(
            form={"action": "clear", "group_id": "100"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert pmgr.resolve_persona_id(100) == "flowerie"  # 回退全局/内置
        # 非法群号
        resp = await server._handle_panel_persona_group(FakeRequest(
            form={"action": "set", "group_id": "abc", "persona_id": "atri"},
            cookies={"fb_token": cookie}))
        assert "err=1" in str(resp.headers.get("Location", ""))


# ---------- 31/32. Meme UI + Group isolation ----------
async def test_knowledge_tab_add_edit_delete():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, mmgr = _make_stack(td)
        cookie = await _login(server)
        # 查看页（无群）渲染输入框
        page = await server._handle_panel(FakeRequest(query={"tab": "knowledge"},
                                                     cookies={"fb_token": cookie}))
        assert 'action="/panel/knowledge/view"' in _resp_text(page)
        # 新增
        resp = await server._handle_panel_knowledge_add(FakeRequest(
            form={"group_id": "100", "term": "电子宠物", "meaning": "群黑话",
                  "confidence": "medium"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert mmgr.repository.count_by_group(100) == 1
        # 查看该群
        page = await server._handle_panel(FakeRequest(
            query={"tab": "knowledge", "gid": "100"}, cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert "电子宠物" in text and "群黑话" in text
        # 编辑
        row = mmgr.repository.get_by_term(100, "电子宠物")
        resp = await server._handle_panel_knowledge_save(FakeRequest(
            form={"id": str(row["id"]), "group_id": "100", "meaning": "修改后的含义",
                  "confidence": "high", "status": "active"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert mmgr.repository.get_by_term(100, "电子宠物")["meaning"] == "修改后的含义"
        # 删除
        resp = await server._handle_panel_knowledge_delete(FakeRequest(
            form={"id": str(row["id"]), "group_id": "100"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert mmgr.repository.count_by_group(100) == 0
        # 清空
        mmgr.add_knowledge(100, "梗", "含义")
        resp = await server._handle_panel_knowledge_clear(FakeRequest(
            form={"group_id": "100"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert mmgr.repository.count_by_group(100) == 0


async def test_knowledge_ui_group_isolation():
    """管理员查看群 A 时绝对不能出现群 B 的知识；跨群操作被服务端拒绝。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, mmgr = _make_stack(td)
        cookie = await _login(server)
        mmgr.add_knowledge(100, "群A专属梗", "含义A")
        mmgr.add_knowledge(200, "群B专属梗", "含义B")
        page_a = await server._handle_panel(FakeRequest(
            query={"tab": "knowledge", "gid": "100"}, cookies={"fb_token": cookie}))
        text_a = _resp_text(page_a)
        assert "群A专属梗" in text_a and "群B专属梗" not in text_a
        page_b = await server._handle_panel(FakeRequest(
            query={"tab": "knowledge", "gid": "200"}, cookies={"fb_token": cookie}))
        text_b = _resp_text(page_b)
        assert "群B专属梗" in text_b and "群A专属梗" not in text_b
        # 用群 B 的上下文删群 A 的记录 → 拒绝
        row_a = mmgr.repository.get_by_term(100, "群A专属梗")
        resp = await server._handle_panel_knowledge_delete(FakeRequest(
            form={"id": str(row_a["id"]), "group_id": "200"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert mmgr.repository.count_by_group(100) == 1


async def test_knowledge_ui_search():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, mmgr = _make_stack(td)
        cookie = await _login(server)
        mmgr.add_knowledge(100, "电子宠物", "黑话A")
        mmgr.add_knowledge(100, "电子烟", "物品B")
        page = await server._handle_panel(FakeRequest(
            query={"tab": "knowledge", "gid": "100", "q": "黑话"}, cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert "电子宠物" in text and "电子烟" not in text


# ---------- 33. Auth ----------
async def test_persona_knowledge_handlers_require_auth():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, pmgr, mmgr = _make_stack(td)
        # 未登录 → 全部重定向回 /panel，不执行任何操作
        r1 = await server._handle_panel_persona_global(FakeRequest(form={"persona_id": "atri"}))
        assert r1.status == 302 and pmgr.resolve_persona_id() != "atri"
        r2 = await server._handle_panel_persona_save(FakeRequest(
            form={"action": "create", "persona_id": "x", "name": "X", "system_prompt": "p"}))
        assert r2.status == 302 and pmgr.get_persona("x") is None
        r3 = await server._handle_panel_knowledge_add(FakeRequest(
            form={"group_id": "1", "term": "t", "meaning": "m"}))
        assert r3.status == 302 and mmgr.repository.count_by_group(1) == 0
        r4 = await server._handle_panel(FakeRequest(query={"tab": "persona"}))
        assert "登录" in _resp_text(r4)  # 未登录访问面板 → 登录页


# ---------- 35/36. 零 JS + HTML-only ----------
_JS_PATTERNS = ("<script", "onclick=", "onchange=", "oninput=", "fetch(", "XMLHttpRequest")


async def test_persona_and_knowledge_pages_are_js_free():
    """新页面（人格/知识）与全站一致：零 JavaScript（纯 HTML 表单交互）。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, mmgr = _make_stack(td)
        cookie = await _login(server)
        mmgr.add_knowledge(100, "梗", "含义")
        pages = []
        for query in ({"tab": "persona"}, {"tab": "persona", "new": "1"},
                      {"tab": "persona", "edit": "atri"},
                      {"tab": "knowledge"}, {"tab": "knowledge", "gid": "100"},
                      {"tab": "knowledge", "gid": "100", "q": "梗"}):
            resp = await server._handle_panel(FakeRequest(query=query, cookies={"fb_token": cookie}))
            pages.append(_resp_text(resp))
        for text in pages:
            lower = text.lower()
            for pat in _JS_PATTERNS:
                assert pat.lower() not in lower, f"{pat} 出现在页面中"
            # 交互全部是 <form method="post"> / <form method="get"> + 链接
            assert "<form " in text and 'method="post"' in text


async def test_html_only_interaction_forms_present():
    """操作全部为服务端表单：POST 提交 + 303/302 重定向回页面。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, pmgr, mmgr = _make_stack(td)
        cookie = await _login(server)
        page = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        for action in ("/panel/persona/global", "/panel/persona/group"):
            assert f'action="{action}"' in text
        # 自定义人格编辑页有 save/delete 表单
        pmgr.create_persona("custom1", "自定义", "", "你是自定义人格")
        page2 = await server._handle_panel(FakeRequest(query={"tab": "persona", "edit": "custom1"},
                                                      cookies={"fb_token": cookie}))
        text2 = _resp_text(page2)
        for action in ("/panel/persona/save", "/panel/persona/delete"):
            assert f'action="{action}"' in text2
        # 内置人格（atri）编辑表单有 save 但无 delete（内置保护；
        # 页面其他区域（自定义人格列表卡片）仍可能有删除按钮，只断言编辑区）
        page3 = await server._handle_panel(FakeRequest(query={"tab": "persona", "edit": "atri"},
                                                      cookies={"fb_token": cookie}))
        text3 = _resp_text(page3)
        form_start = text3.index("编辑人格：亚托莉")
        form_end = text3.index("人格列表") if "人格列表" in text3 else len(text3)
        edit_zone = text3[form_start:form_end]
        assert 'action="/panel/persona/save"' in edit_zone
        assert 'action="/panel/persona/delete"' not in edit_zone
        # 未指定群：只有「查看」表单
        page = await server._handle_panel(FakeRequest(query={"tab": "knowledge"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert 'action="/panel/knowledge/view"' in text
        # 指定群（含知识条目）：新增/编辑/删除/清空表单齐全
        mmgr.add_knowledge(100, "测试梗", "含义")
        page2 = await server._handle_panel(FakeRequest(query={"tab": "knowledge", "gid": "100"},
                                                      cookies={"fb_token": cookie}))
        text2 = _resp_text(page2)
        for action in ("/panel/knowledge/view", "/panel/knowledge/add",
                       "/panel/knowledge/save", "/panel/knowledge/delete",
                       "/panel/knowledge/clear"):
            assert f'action="{action}"' in text2


# ---------- 群聊自定义 Prompt 管理（按群读写 + 折叠 + 默认人格 id） ----------
async def test_prompt_management_global_set_reset():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        # 设置全局 Prompt
        resp = await server._handle_panel_prompt_global(FakeRequest(
            form={"action": "set", "content": "你是话痨小助手"},
            cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert server._prompt_manager.get_global_prompt() == "你是话痨小助手"
        # 重置
        resp = await server._handle_panel_prompt_global(FakeRequest(
            form={"action": "reset"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert server._prompt_manager.get_global_prompt() == ""


async def test_prompt_management_group_isolation():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        # 群 100 写 Prompt
        resp = await server._handle_panel_prompt_group(FakeRequest(
            form={"action": "set", "group_id": "100", "content": "本群专属"},
            cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert server._prompt_manager.get_group_prompt(100) == "本群专属"
        assert server._prompt_manager.get_group_prompt(200) == ""  # 群隔离
        # 重置群 200（无内容）不影响群 100
        resp = await server._handle_panel_prompt_group(FakeRequest(
            form={"action": "reset", "group_id": "200"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert server._prompt_manager.get_group_prompt(100) == "本群专属"
        # 重置群 100
        resp = await server._handle_panel_prompt_group(FakeRequest(
            form={"action": "reset", "group_id": "100"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert server._prompt_manager.get_group_prompt(100) == ""
        # 非法群号拒绝
        resp = await server._handle_panel_prompt_group(FakeRequest(
            form={"action": "set", "group_id": "abc", "content": "x"},
            cookies={"fb_token": cookie}))
        assert "err=1" in str(resp.headers.get("Location", ""))


async def test_prompt_management_renders_details_and_default_id():
    """人格页：<details> 原生折叠（零 JS）、默认人格 id 明确显示、按群 Prompt 载入。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        server._prompt_manager.set_global_prompt("全局测试")
        server._prompt_manager.set_group_prompt(100, "群100测试")
        # 页面含折叠元素与默认人格 id
        page = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert "<details" in text and "<summary>" in text       # 原生折叠，无 JS
        assert "PERSONA_DEFAULT" in text
        assert "flowerie" in text                                # 默认人格 id 写清楚
        assert "全局测试" in text
        assert 'action="/panel/prompt/global"' in text
        assert 'action="/panel/prompt/group"' in text
        # 按群载入：prompt_gid=100 时 textarea 带出群 100 的 Prompt
        page2 = await server._handle_panel(FakeRequest(query={"tab": "persona", "prompt_gid": "100"},
                                                      cookies={"fb_token": cookie}))
        text2 = _resp_text(page2)
        assert "群100测试" in text2
        assert 'name="group_id" placeholder="群号" required style="max-width:200px" value="100"' in text2
        # 群 200 不出现群 100 的内容（群隔离）
        page3 = await server._handle_panel(FakeRequest(query={"tab": "persona", "prompt_gid": "200"},
                                                      cookies={"fb_token": cookie}))
        text3 = _resp_text(page3)
        assert "群100测试" not in text3


async def test_prompt_management_requires_auth():
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        r1 = await server._handle_panel_prompt_global(FakeRequest(form={"action": "set", "content": "x"}))
        assert r1.status == 302
        assert server._prompt_manager.get_global_prompt() == ""
        r2 = await server._handle_panel_prompt_group(FakeRequest(
            form={"action": "set", "group_id": "1", "content": "x"}))
        assert r2.status == 302
        assert server._prompt_manager.get_group_prompt(1) == ""


# ---------- 默认人格热更新（Web UI 修改立即生效，无需重启） ----------
async def test_default_persona_hot_update():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, pmgr, _ = _make_stack(td)
        cookie = await _login(server)
        # 初始默认 = flowerie
        assert svc.config.PERSONA_DEFAULT == "flowerie"
        # 设为 atri → ConfigService 热更新（.env + settings.db + 运行时 Settings）
        resp = await server._handle_panel_persona_default(FakeRequest(
            form={"persona_id": "atri"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert svc.config.PERSONA_DEFAULT == "atri"
        assert svc.repository.get_config("PERSONA_DEFAULT") == "atri"
        # PersonaManager 动态读取 config → resolve 使用新默认
        pmgr.config = svc.config
        assert pmgr.resolve_persona_id() == "atri"
        assert pmgr.resolve_persona_id(999) == "atri"   # 无群/全局设置时用新默认
        # 不存在的 id 拒绝
        resp = await server._handle_panel_persona_default(FakeRequest(
            form={"persona_id": "not_exist"}, cookies={"fb_token": cookie}))
        assert "err=1" in str(resp.headers.get("Location", ""))
        assert svc.config.PERSONA_DEFAULT == "atri"
        # 页面显示当前默认
        page = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                     cookies={"fb_token": cookie}))
        assert 'value="atri" selected' in _resp_text(page)


# ---------- HTML 结构配对（防 DOM 错乱回归） ----------
async def test_html_structure_balanced():
    """所有渲染页面标签配对（div/form/fieldset/details/summary/select/textarea）。

    回归防护：曾因多余 </div> 导致页面 DOM 结构错乱、布局全乱。
    """
    from src.services.web_ui_assets import render_login_page, render_register_page

    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        pages = []
        for query in ({"tab": "persona"}, {"tab": "persona", "edit": "atri"},
                      {"tab": "persona", "new": "1"}, {"tab": "persona", "prompt_gid": "100"},
                      {"tab": "knowledge"}, {"tab": "knowledge", "gid": "100"}):
            resp = await server._handle_panel(FakeRequest(query=query, cookies={"fb_token": cookie}))
            pages.append(_resp_text(resp))
        pages.append(render_login_page())
        pages.append(render_register_page())
        for i, text in enumerate(pages):
            for tag in ("div", "form", "fieldset", "details", "summary", "select", "textarea"):
                o = text.count(f"<{tag}")
                c = text.count(f"</{tag}>")
                assert o == c, f"页面{i} 标签 <{tag}> 不配对：开{o} 闭{c}"
