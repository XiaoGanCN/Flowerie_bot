"""Web UI 面板处理器（按功能域拆分的 mixin，WebUIServer 聚合）。

拆分目的：WebUIServer 曾为 ~1100 行的上帝类；现在每个功能域一个 mixin，
WebUIServer 只保留核心（认证基座/面板壳/生命周期）并多重继承各 mixin。
对外 API（from src.services.web_ui import WebUIServer）保持不变。
"""
from src.services.webui_panels.appearance_panel import AppearancePanelMixin
from src.services.webui_panels.auth_panel import AuthPanelMixin
from src.services.webui_panels.config_panel import ConfigPanelMixin
from src.services.webui_panels.knowledge_panel import KnowledgePanelMixin
from src.services.webui_panels.mcp_panel import McpPanelMixin
from src.services.webui_panels.persona_panel import PersonaPanelMixin
from src.services.webui_panels.prompt_panel import PromptPanelMixin

__all__ = [
    "AppearancePanelMixin", "AuthPanelMixin", "ConfigPanelMixin",
    "KnowledgePanelMixin", "McpPanelMixin", "PersonaPanelMixin", "PromptPanelMixin",
]
