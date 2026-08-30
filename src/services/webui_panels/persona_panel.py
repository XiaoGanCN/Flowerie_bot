"""Web UI 人格域处理器（默认/全局/CRUD/群绑定）。

从 WebUIServer 拆分（防上帝类）：数据源为注入的 persona_manager。
"""
from typing import Optional
from urllib.parse import quote

from aiohttp import web

from src.services.web_ui_assets import render_persona_tab


class PersonaPanelMixin:

    def _render_persona_page(self, edit_id: str = "", new_persona: bool = False,
                            prompt_gid: Optional[int] = None) -> str:
        if self._persona_manager is None:
            return render_persona_tab([], "", [], enabled=False)
        personas = self._persona_manager.list_personas()
        global_p = self._persona_manager.get_global()
        global_id = global_p["id"] if global_p else ""
        bindings = self._persona_manager.repository.list_group_bindings()
        edit_persona = None
        if edit_id and not new_persona:
            edit_persona = self._persona_manager.get_persona(edit_id)
        # 默认人格 id（写清楚）：来自 PERSONA_DEFAULT 配置当前值（热更新后立即反映）
        default_id = str(getattr(self.config, "PERSONA_DEFAULT", "flowerie") or "flowerie")
        default_p = self._persona_manager.get_persona(default_id)
        default_name = (default_p or {}).get("name", "") if default_p else ""
        # 自定义 Prompt（全局 / 按群读写；prompt_manager 未注入时留空并提示）
        global_prompt = ""
        group_prompt = ""
        if self._prompt_manager is not None:
            global_prompt = self._prompt_manager.get_global_prompt()
            if prompt_gid is not None:
                group_prompt = self._prompt_manager.get_group_prompt(prompt_gid)
        # 人格配置（PERSONA_*，从配置页移入本页管理）
        persona_configs = [c for c in self.config_service.list_configs()
                           if c["key"] in ("PERSONA_DEFAULT", "MAX_PERSONA_PROMPT_LENGTH",
                                           "PERSONA_MAX_COUNT")]
        # 管理员补充发言规则（全局文本；每行一条，热更新立即生效）
        admin_rules = list(getattr(self.config, "ADMIN_RESPONSE_RULES", None) or [])
        rules_text = "\n".join(str(r) for r in admin_rules)
        return render_persona_tab(
            personas, global_id, bindings,
            edit_persona=edit_persona, new=new_persona, enabled=True,
            default_persona_id=default_id, default_persona_name=default_name,
            global_prompt=global_prompt, group_prompt=group_prompt, prompt_gid=prompt_gid,
            persona_configs=persona_configs,
            admin_rules=admin_rules, rules_text=rules_text,
        )

    async def _handle_panel_persona_admin_rules(self, request: web.Request) -> web.Response:
        """保存管理员补充发言规则（ConfigService 双写 + 热更新，立即生效）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        raw = str(form.get("rules", ""))
        ok, message = self.config_service.update("ADMIN_RESPONSE_RULES", raw)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_persona_config(self, request: web.Request) -> web.Response:
        """保存人格配置（PERSONA_*，复用 ConfigService 双写 + 热更新）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        updates = {name: str(form.get(name, "")) for name in
                   ("PERSONA_DEFAULT", "MAX_PERSONA_PROMPT_LENGTH", "PERSONA_MAX_COUNT")
                   if name in form}
        ok, message = self.config_service.update_many(updates)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_persona_default(self, request: web.Request) -> web.Response:
        """设置默认（兜底）人格：校验存在 → 走 ConfigService 热更新
        （写 .env + settings.db + 运行时 Settings，立即生效，无需重启）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._persona_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格系统未启用") + "&err=1")
        persona_id = str(form.get("persona_id", "") or "").strip()
        if self._persona_manager.get_persona(persona_id) is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格不存在：" + persona_id) + "&err=1")
        ok, message = self.config_service.update("PERSONA_DEFAULT", persona_id)
        if not ok:
            return web.HTTPFound(f"/panel?tab=persona&msg={quote(message)}&err=1")
        name = (self._persona_manager.get_persona(persona_id) or {}).get("name", persona_id)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote('默认人格已设为「' + name + '」，立即生效（无需重启）')}")

    async def _handle_panel_persona_global(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        persona_id = str(form.get("persona_id", "") or "").strip()
        if self._persona_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格系统未启用") + "&err=1")
        ok, msg = self._persona_manager.set_global(persona_id)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_persona_save(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._persona_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格系统未启用") + "&err=1")
        action = str(form.get("action", "") or "update").strip()
        persona_id = str(form.get("persona_id", "") or "").strip()
        name = str(form.get("name", "") or "")
        description = str(form.get("description", "") or "")
        system_prompt = str(form.get("system_prompt", "") or "")
        vocabulary = str(form.get("vocabulary", "") or "")
        behavior_rules = str(form.get("behavior_rules", "") or "")
        response_style = str(form.get("response_style", "") or "")
        if action == "create":
            ok, msg = self._persona_manager.create_persona(
                persona_id, name, description, system_prompt,
                vocabulary=vocabulary, behavior_rules=behavior_rules, response_style=response_style)
        else:
            ok, msg = self._persona_manager.update_persona(
                persona_id, name=name, description=description, system_prompt=system_prompt,
                vocabulary=vocabulary, behavior_rules=behavior_rules, response_style=response_style)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_persona_delete(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._persona_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格系统未启用") + "&err=1")
        persona_id = str(form.get("persona_id", "") or "").strip()
        ok, msg = self._persona_manager.delete_persona(persona_id)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_persona_group(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._persona_manager is None:
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("人格系统未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        if not gid_raw.isdigit():
            return web.HTTPFound("/panel?tab=persona&msg=" + quote("群号必须是数字") + "&err=1")
        group_id = int(gid_raw)
        action = str(form.get("action", "") or "set").strip()
        if action == "clear":
            ok, msg = self._persona_manager.clear_group(group_id)
        else:
            persona_id = str(form.get("persona_id", "") or "").strip()
            ok, msg = self._persona_manager.set_group(group_id, persona_id)
        return web.HTTPFound(f"/panel?tab=persona&msg={quote(msg)}&err={'1' if not ok else ''}")
