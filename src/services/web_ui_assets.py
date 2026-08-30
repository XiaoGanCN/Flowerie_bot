"""web_ui_assets：渲染层聚合导出（兼容旧导入路径；实现见 src/services/webui_render/）。

按功能域拆分（防上帝类）：
- webui_render/theme.py        内置主题 / 面板 CSS / 背景合成
- webui_render/pages.py        登录 / 注册 / 面板壳
- webui_render/config_panel.py 配置分组表单 + MCP 卡片编辑器
- webui_render/appearance.py   外观页
- webui_render/persona.py      人格页
- webui_render/knowledge.py    群聊知识页
零 JavaScript 保证：所有渲染函数只输出 HTML/CSS，无任何脚本。
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
from src.services.webui_render.plugins import render_plugin_tab
from src.services.webui_render.theme import (
    DEFAULT_THEME,
    PANEL_CSS,
    THEME_ORDER,
    THEMES,
    background_rules,
    hex_to_rgb,
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
    "render_panel_page", "render_persona_tab", "render_plugin_tab", "render_register_page",
    "render_account_tab",
]
