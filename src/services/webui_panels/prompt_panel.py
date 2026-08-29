"""Web UI 群聊自定义 Prompt 域处理器（全局/按群读写，按群隔离）。

从 WebUIServer 拆分（防上帝类）：与 /prompt 命令同一存储（PromptManager）。
"""
from urllib.parse import quote

from aiohttp import web


class PromptPanelMixin:

    async def _handle_panel_prompt_global(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._prompt_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("自定义 Prompt 功能未启用") + "&err=1")
        action = str(form.get("action", "") or "set").strip()
        if action == "reset":
            self._prompt_manager.reset_global_prompt()
            msg = "全局 Prompt 已重置（回退到内置人格）"
        else:
            content = str(form.get("content", "") or "")
            try:
                self._prompt_manager.set_global_prompt(content)
                msg = "全局 Prompt 已保存，立即生效"
            except ValueError as e:
                return web.HTTPFound(f"/panel?tab=persona&msg={quote(str(e))}&err=1")
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(msg)}")

    async def _handle_panel_prompt_group(self, request: web.Request) -> web.Response:
        """按群读写 Prompt：group_id 强制数字校验，保存/重置只作用于该群（群隔离）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._prompt_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("自定义 Prompt 功能未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        if not gid_raw.isdigit():
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("群号必须是数字") + "&err=1")
        group_id = int(gid_raw)
        action = str(form.get("action", "") or "set").strip()
        if action == "reset":
            self._prompt_manager.reset_group_prompt(group_id)
            msg = f"群 {group_id} 的 Prompt 已重置（回退到全局/内置）"
        else:
            content = str(form.get("content", "") or "")
            try:
                self._prompt_manager.set_group_prompt(group_id, content)
                msg = f"群 {group_id} 的 Prompt 已保存，立即生效"
            except ValueError as e:
                return web.HTTPFound(f"/panel?tab=persona&prompt_gid={group_id}&msg={quote(str(e))}&err=1")
        return web.HTTPFound(f"/panel?tab=persona&prompt_gid={group_id}&msg={quote(msg)}")
