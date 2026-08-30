"""Web UI 插件域处理器（Plugin Panel）。

从 WebUIServer 拆分（防上帝类）：数据源为注入的 plugin_manager。
全部处理器先过 _check_token（管理员认证）；未认证一律重定向回 /panel。
"""
from urllib.parse import quote

from aiohttp import web

from src.services.web_ui_assets import render_plugin_tab
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

_MAX_UPLOAD_READ = 6 * 1024 * 1024  # 上传读取兜底上限（安装器内另有 ZIP 大小校验）


class PluginPanelMixin:

    def _render_plugin_page(self) -> str:
        if self._plugin_manager is None:
            return render_plugin_tab([], protection="normal", plugin_configs=[])
        try:
            plugins = self._plugin_manager.list_plugins()
        except Exception as e:  # noqa: BLE001 - 页面不因插件系统异常崩溃
            logger.error("plugin_page_list_failed reason=%s", e)
            plugins = []
        protection = self._plugin_manager._protection_level() if hasattr(
            self._plugin_manager, "_protection_level") else "normal"
        plugin_configs = [c for c in self.config_service.list_configs()
                          if c["key"].startswith("PLUGIN_")]
        return render_plugin_tab(plugins, protection=protection,
                                 plugin_configs=plugin_configs, protection_warning=protection == "unsafe")

    async def _handle_panel_plugins_refresh(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg="
                                 + quote("插件系统未启用（未注入 PluginManager）"))
        discovered, changed = self._plugin_manager.refresh()
        msg = f"扫描完成：新发现 {len(discovered)} 个插件，更新 {len(changed)} 个" \
              if (discovered or changed) else "扫描完成：无变化（新插件默认禁用，需手动启用）"
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(msg)}")

    async def _handle_panel_plugins_upload(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("插件系统未启用"))
        form = await request.post()
        upload = form.get("plugin_file")
        if upload is None or not getattr(upload, "file", None):
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("未选择文件"))
        filename = str(getattr(upload, "filename", "") or "upload.zip")
        data = upload.file.read(_MAX_UPLOAD_READ + 1)
        if len(data) > _MAX_UPLOAD_READ:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg="
                                 + quote(f"文件超过大小上限（{_MAX_UPLOAD_READ} 字节）"))
        ok, message = self._plugin_manager.install_upload(data, filename=filename)
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_install_url(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("插件系统未启用"))
        form = await request.post()
        url = str(form.get("url", ""))
        if not url:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("URL 为空"))
        ok, message = await self._plugin_manager.install_url_async(url)
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_enable(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("插件系统未启用"))
        form = await request.post()
        plugin_id = str(form.get("id", ""))
        # MultiDict（真 aiohttp）用 getall；简易 dict（测试桩）兼容取单值/列表
        if hasattr(form, "getall"):
            perms = [str(p) for p in form.getall("perm")]
        else:
            raw = form.get("perm")
            perms = [str(p) for p in (raw if isinstance(raw, (list, tuple)) else [raw]) if p]
        ok, message = await self._plugin_manager.enable(plugin_id, approved_permissions=perms)
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_disable(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("插件系统未启用"))
        form = await request.post()
        ok, message = self._plugin_manager.disable(str(form.get("id", "")))
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_uninstall(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        if self._plugin_manager is None:
            return web.HTTPFound("/panel?tab=plugins&err=1&msg=" + quote("插件系统未启用"))
        form = await request.post()
        ok, message = self._plugin_manager.uninstall(str(form.get("id", "")))
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_protection(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        level = str(form.get("protection", "")).lower()
        # 双写：ConfigService（.env + settings.db + 热更新）→ Manager 运行态
        ok, message = self.config_service.update("PLUGIN_PROTECTION", level)
        if ok and self._plugin_manager is not None:
            _ok2, msg2 = self._plugin_manager.set_protection(level)
            message = (message + "；" + msg2) if _ok2 else (msg2 or message)
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_plugins_config(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        updates = {k: str(v) for k, v in form.items()
                   if k in ("PLUGIN_MAX_COUNT", "PLUGIN_URL_MAX_BYTES", "PLUGIN_URL_TIMEOUT",
                            "PLUGIN_ZIP_MAX_UNZIPPED_BYTES", "PLUGIN_ZIP_MAX_FILES")}
        ok, message = self.config_service.update_many(updates)
        return web.HTTPFound(f"/panel?tab=plugins&msg={quote(message)}&err={'1' if not ok else ''}")
