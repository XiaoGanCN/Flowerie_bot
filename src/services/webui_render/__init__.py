"""Web UI 渲染层（按功能域拆分，防上帝类）。

- theme.py        内置主题 / 面板 CSS / 背景合成（零 JS 纯 CSS）
- pages.py        登录 / 注册 / 面板壳
- config_panel.py 配置分组表单 + MCP 卡片编辑器
- appearance.py   外观页
- persona.py      人格页
- knowledge.py    群聊知识页
"""
from src.services.webui_render.account import render_account_tab
from src.services.webui_render.appearance import render_appearance
from src.services.webui_render.config_panel import (
    render_config_sections,
    render_mcp_editor,
)
from src.services.webui_render.knowledge import render_knowledge_tab
from src.services.webui_render.pages import (
    render_login_page,
    render_panel_page,
    render_register_page,
)
from src.services.webui_render.persona import render_persona_tab
from src.services.webui_render.theme import (
    DEFAULT_THEME,
    PANEL_CSS,
    THEME_ORDER,
    THEMES,
    background_rules,
    theme_body_class,
    theme_css_block,
    theme_default_alpha,
    theme_default_bg,
)

__all__ = [
    "DEFAULT_THEME", "PANEL_CSS", "THEMES", "THEME_ORDER", "background_rules",
    "hex_to_rgb", "theme_body_class", "theme_css_block", "theme_default_alpha",
    "theme_default_bg", "render_appearance", "render_config_sections",
    "render_knowledge_tab", "render_login_page", "render_mcp_editor",
    "render_panel_page", "render_persona_tab", "render_register_page",
    "render_account_tab",
]
