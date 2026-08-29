"""Web UI 配置域处理器（无 JS 分组表单保存）。

从 WebUIServer 拆分（防上帝类）：配置保存走 ConfigService（.env + settings.db 双写）。
"""
from typing import Dict
from urllib.parse import quote

from aiohttp import web

from src.services.config_service import ConfigService


class ConfigPanelMixin:

    async def _handle_panel_save(self, request: web.Request) -> web.Response:
        """配置保存：兼容旧版单键表单（key/value）；分组表单字段名=配置键。

        checkbox 采用 hidden false + checkbox true 同名字段模式：未勾选只提交
        false，勾选提交 false 和 true —— 服务端取同名最后一个值（未提交=false 语义）。
        """
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        cat = request.query.get("cat", "")
        if cat not in ConfigService.CATEGORY_ORDER:
            cat = ""
        _catq = f"&cat={quote(cat)}" if cat else ""
        form = await request.post()
        if "key" in form and "value" in form:
            key = str(form.get("key", ""))
            value = str(form.get("value", ""))
            ok, message = self.config_service.update(key, value)
            return web.HTTPFound(f"/panel?msg={quote(message)}&err={'1' if not ok else ''}{_catq}")
        updates: Dict[str, str] = {}
        for name in form.keys():
            if name not in self.config_service.SCHEMA:
                continue
            if hasattr(form, "getall"):
                vals = form.getall(name)
                updates[name] = str(vals[-1]) if vals else ""
            else:
                updates[name] = str(form.get(name, ""))
        ok, message = self.config_service.update_many(updates)
        return web.HTTPFound(f"/panel?msg={quote(message)}&err={'1' if not ok else ''}{_catq}")
