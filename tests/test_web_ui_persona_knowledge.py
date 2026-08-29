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
    config.PERSONA_DEFAULT = "flowerie"
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
        # 注意：区别说明卡片里也有「人格列表」字样，锚点用列表区块的完整标题
        form_end = text3.index("人格列表（Persona 资源库）") if "人格列表（Persona 资源库）" in text3 else len(text3)
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


# ---------- 防上帝类回归（核心模块行数上限） ----------
def test_core_modules_stay_slim():
    """防上帝类回归：拆分后核心模块行数有上限（Web UI/AIClient/配置/Router）。

    拆分目标：web_ui.py 1129→336、web_ui_assets.py 1082→聚合导出、
    ai_client.py 800→353、config_service.py 689→455、message_router.py 732→564。
    上限留有余量；新功能应继续走拆分方向而非堆回单个文件。
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    limits = {
        "src/services/web_ui.py": 430,           # 薄门面 + 核心（认证/面板壳/生命周期）
        "src/services/web_ui_assets.py": 120,    # 渲染层聚合导出（实现拆到 webui_render/）
        "src/services/ai_client.py": 430,        # 职责服务已拆：prompt_builder/vision/toxic
        "src/services/config_service.py": 520,   # 数据声明已拆到 config_schema.py
        "src/core/message_router.py": 650,       # AI 准入层已拆到 ai_gateway.py
    }
    for rel, limit in limits.items():
        p = os.path.join(root, rel)
        lines = sum(1 for _ in open(p, encoding="utf-8"))
        assert lines <= limit, f"{rel} 行数 {lines} 超限 {limit}（防上帝类回归）"


# ---------- 防拆分回归：提取模块的类结构完整（AST 级，含 @staticmethod） ----------
def test_split_modules_keep_class_structure():
    """拆分模块的方法必须属于类（曾因提取丢缩进变成模块级函数的隐藏 bug）。

    AST 级检查（不导入业务模块）：方法归属类、@staticmethod 保留。
    """
    import ast
    import glob
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def class_methods(path):
        tree = ast.parse(open(os.path.join(root, path), encoding="utf-8").read())
        out = {}
        for n in tree.body:
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        out[m.name] = any(
                            isinstance(d, ast.Name) and d.id == "staticmethod"
                            for d in m.decorator_list)
        return out

    checks = {
        "src/services/vision.py": ("VisionService", [
            "__init__", "client", "_url_for_log", "describe_image",
            "_describe_image_bytes", "describe_image_file"]),
        "src/services/toxic_detector.py": ("ToxicDetector", ["__init__", "client", "is_toxic"]),
        "src/core/ai_gateway.py": ("AiGateway", [
            "__init__", "guarded_chat", "_ai_allowed", "guarded_is_toxic", "_get_group_breaker"]),
        "src/services/webui_panels/account_panel.py": ("AccountPanelMixin", [
            "_render_account_page", "_credential_info", "_mcp_status",
            "_config_status", "_handle_panel_unregister"]),
    }
    for path, (cls_name, methods) in checks.items():
        methods_map = class_methods(path)
        for m in methods:
            assert m in methods_map, f"{path} 类 {cls_name} 缺失方法 {m}"
        print("✅", path, "结构完整")

    # @staticmethod 必须保留
    static_required = {"_url_for_log", "_bg_color_pref_key", "_mcp_server_error", "_fmt_ts",
                       "effective_host"}
    for path in (["src/services/vision.py", "src/services/web_ui.py"]
                 + sorted(glob.glob("src/services/webui_panels/*.py"))):
        methods_map = class_methods(path)
        for m, is_static in methods_map.items():
            if m in static_required:
                assert is_static, f"{path} 的 {m} 丢失 @staticmethod"
    print("✅ @staticmethod 全部保留")

    # prompt_builder 的 GLOBAL_STYLE_RULES/default_persona_text/build_system_prompt 在模块层
    tree = ast.parse(open(os.path.join(root, "src/services/prompt_builder.py"), encoding="utf-8").read())
    top_names = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"default_persona_text", "build_system_prompt"} <= top_names
    print("✅ prompt_builder 模块级函数就位")


# ---------- 人格/知识配置移入专属页 + 区别说明 ----------
async def test_persona_config_moved_to_persona_tab():
    """PERSONA_* 配置在人格页渲染并可保存；配置页不再显示。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        page = await server._handle_panel(FakeRequest(query={"tab": "persona"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert 'name="PERSONA_MAX_COUNT"' in text          # 配置表单在本页
        assert 'action="/panel/persona/config"' in text
        assert "自定义人格 与 自定义 Prompt 的区别" in text   # 区别说明
        assert "换身份" in text and "加补充" in text
        # 保存生效（热更新）
        resp = await server._handle_panel_persona_config(FakeRequest(
            form={"PERSONA_MAX_COUNT": "88"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert svc.config.PERSONA_MAX_COUNT == 88
        assert svc.repository.get_config("PERSONA_MAX_COUNT") == "88"
        # 配置页不含人格配置
        page_c = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}))
        text_c = _resp_text(page_c)
        assert 'name="PERSONA_MAX_COUNT"' not in text_c


async def test_knowledge_config_moved_to_knowledge_tab():
    """MEME_* 配置在群聊知识页渲染并可保存。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        page = await server._handle_panel(FakeRequest(query={"tab": "knowledge", "gid": "100"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert 'name="MAX_GROUP_MEMES"' in text
        assert 'name="MEME_LEARNING_ENABLED"' in text
        assert 'action="/panel/knowledge/config"' in text
        resp = await server._handle_panel_knowledge_config(FakeRequest(
            form={"MAX_GROUP_MEMES": "321"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert svc.config.MAX_GROUP_MEMES == 321
        assert svc.repository.get_config("MAX_GROUP_MEMES") == "321"


# ---------- 注销（只清账号密码，其他配置不动） ----------
async def test_unregister_clears_only_credentials():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        # 预置：注册账号（写入 settings.db）+ 一条与账号无关的配置（.env + db 双写）
        ok, _ = svc.register_user("admin2", "pass123")
        assert ok
        svc.update("MAX_REPLY_LENGTH", "42")
        assert svc.repository.get_config("WEB_UI_USERNAME") == "admin2"
        # 注册后凭据变为 admin2/pass123，用它登录
        resp = await server._handle_panel_login(FakeRequest(
            form={"username": "admin2", "password": "pass123"}))
        assert resp.status == 302
        cookie = resp.cookies.get("fb_token").value
        # 注销（当前密码正确）
        resp = await server._handle_panel_unregister(FakeRequest(
            form={"password": "pass123"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        # settings.db 账号凭据已清除
        assert svc.repository.get_config("WEB_UI_USERNAME") is None
        assert svc.repository.get_config("WEB_UI_PASSWORD") is None
        # .env 中 WEB_UI_USERNAME/WEB_UI_PASSWORD 已移除
        from src.repositories.env_store import EnvFileStore
        env = EnvFileStore(svc.env_store._path).read_values()
        assert "WEB_UI_USERNAME" not in env
        assert "WEB_UI_PASSWORD" not in env
        # 其他配置不受影响（.env 与 settings.db 均保留）
        assert svc.repository.get_config("MAX_REPLY_LENGTH") == "42"
        assert "MAX_REPLY_LENGTH" in env and env["MAX_REPLY_LENGTH"] == "42"
        # token 已全部失效（未登录访问面板 → 登录页）
        page = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}))
        assert "登录" in _resp_text(page)


async def test_unregister_requires_current_password():
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        svc.register_user("admin2", "pass123")
        resp = await server._handle_panel_login(FakeRequest(
            form={"username": "admin2", "password": "pass123"}))
        cookie = resp.cookies.get("fb_token").value
        resp = await server._handle_panel_unregister(FakeRequest(
            form={"password": "wrong-pass"}, cookies={"fb_token": cookie}))
        assert "err=1" in str(resp.headers.get("Location", ""))
        # 凭据未被清除，token 仍有效（面板页可见；登录页有专属标语，面板页没有）
        assert svc.repository.get_config("WEB_UI_USERNAME") == "admin2"
        page = await server._handle_panel(FakeRequest(cookies={"fb_token": cookie}))
        text = _resp_text(page)
        assert "登录后管理全部配置" not in text
        assert "注销管理员账号" in text


# ---------- 第五轮 review 回归：knowledge 配置保存保留群号 / 注销提示 ----------
async def test_knowledge_config_keeps_gid_after_save():
    """知识配置保存后重定向仍带 gid（表单隐藏域 + 服务端 form 读取）。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        resp = await server._handle_panel_knowledge_config(FakeRequest(
            form={"gid": "100", "MAX_GROUP_MEMES": "222"}, cookies={"fb_token": cookie}))
        assert resp.status == 302
        assert "gid=100" in str(resp.headers.get("Location", ""))
        assert svc.config.MAX_GROUP_MEMES == 222


async def test_unregister_message_mentions_restart_note():
    """注销提示包含 WEB_UI_ENABLED 启动说明。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        svc.register_user("admin2", "pass123")
        resp = await server._handle_panel_login(FakeRequest(
            form={"username": "admin2", "password": "pass123"}))
        cookie = resp.cookies.get("fb_token").value
        resp = await server._handle_panel_unregister(FakeRequest(
            form={"password": "pass123"}, cookies={"fb_token": cookie}))
        loc = str(resp.headers.get("Location", ""))
        import urllib.parse
        msg = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get("msg", [""])[0]
        assert "WEB_UI_ENABLED" in msg


# ---------- 用户状态页（账户/注销/服务器/MCP/API） ----------
async def test_account_tab_renders_all_status():
    """用户状态页：当前管理员 / 注销表单 / 服务器状态 / MCP 状态 / API 连接状态。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, svc, server, _, _ = _make_stack(td)
        svc.register_user("admin2", "pass123")
        resp = await server._handle_panel_login(FakeRequest(
            form={"username": "admin2", "password": "pass123"}))
        cookie = resp.cookies.get("fb_token").value
        page = await server._handle_panel(FakeRequest(query={"tab": "account"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        for probe in ("当前管理员", "admin2", "注销账号", "服务器状态",
                      "内存占用", "API 厂商连接状态", "DeepSeek（聊天主厂商）"):
            assert probe in text, f"用户状态页缺少 {probe}"
        # 注销表单 /panel/account/unregister 在 account 页
        assert 'action="/panel/account/unregister"' in text
        # 未登出前可访问（已登录）
        assert "登录后管理全部配置" not in text


async def test_unregister_form_not_in_body_bottom():
    """注销表单已从面板 body 底部移走（只在用户状态页）。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        page = await server._handle_panel(FakeRequest(query={"tab": "config"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page)
        # 配置页 body 底部不再有注销表单
        assert 'action="/panel/account/unregister"' not in text
        assert "注销管理员账号" not in text
        # 顶部导航有「用户状态」
        assert "/panel?tab=account" in text


async def test_account_tab_is_js_free():
    """用户状态页零 JS（含服务器/MCP/API 状态）。"""
    with tempfile.TemporaryDirectory() as td:
        _, _, _, server, _, _ = _make_stack(td)
        cookie = await _login(server)
        page = await server._handle_panel(FakeRequest(query={"tab": "account"},
                                                     cookies={"fb_token": cookie}))
        text = _resp_text(page).lower()
        for pat in ("<script", "onclick=", "onchange=", "oninput=", "fetch(", "XMLHttpRequest"):
            assert pat not in text, f"用户状态页出现 {pat}"


def test_login_and_register_username_input_width_consistent():
    """登录/注册页的用户名与密码输入框 type 一致（避免 [type=text] 选择器不匹配变窄）。"""
    from src.services.webui_render.pages import render_login_page, render_register_page
    for html in (render_login_page(), render_register_page()):
        assert 'name="username" type="text"' in html
        assert 'name="password" type="password"' in html
