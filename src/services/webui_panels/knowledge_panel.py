"""Web UI 群聊知识域处理器（按群查看/搜索/增删改，严格隔离）。

从 WebUIServer 拆分（防上帝类）：数据源为注入的 meme_manager；
所有操作按 id + group_id 双作用域，防跨群访问。
"""
import time
from typing import Optional
from urllib.parse import quote

from aiohttp import web

from src.services.web_ui_assets import render_knowledge_tab


class KnowledgePanelMixin:

    def _render_knowledge_page(self, gid: Optional[int], search: str = "") -> str:
        if self._meme_manager is None:
            return render_knowledge_tab(None, [], enabled=False)
        if gid is None:
            return render_knowledge_tab(None, [], enabled=True)
        rows = self._meme_manager.list_for_group(gid, search=search or "")
        count = self._meme_manager.repository.count_by_group(gid)
        # 时间戳转可读串（渲染层不暴露原始 float）
        for idx, r in enumerate(rows):
            r["updated_at"] = self._fmt_ts(r.get("updated_at"))
            rows[idx] = r
        meme_configs = [c for c in self.config_service.list_configs()
                        if c["key"].startswith("MEME_") or c["key"] == "MAX_GROUP_MEMES"]
        return render_knowledge_tab(gid, rows, search=search or "", count=count,
                                    max_memes=self._meme_manager.max_memes_per_group, enabled=True,
                                    meme_configs=meme_configs)

    @staticmethod
    def _fmt_ts(ts) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        except (TypeError, ValueError):
            return ""

    async def _handle_panel_knowledge_config(self, request: web.Request) -> web.Response:
        """保存群聊知识配置（MEME_*，复用 ConfigService 双写 + 热更新）。"""
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        updates = {name: str(form.get(name, "")) for name in
                   ("MEME_LEARNING_ENABLED", "MEME_KNOWLEDGE_DB_PATH",
                    "MEME_SUMMARY_INTERVAL_HOURS", "MAX_GROUP_MEMES",
                    "MEME_BUFFER_PER_GROUP", "MEME_MAX_GROUPS_PER_RUN",
                    "MEME_MIN_MESSAGES_PER_SUMMARY", "MEME_MAX_SUMMARY_CANDIDATES")
                   if name in form}
        ok, message = self.config_service.update_many(updates)
        gid_q = f"&gid={request.query.get('gid', '')}" if request.query.get("gid", "").isdigit() else ""
        return web.HTTPFound(f"/panel?tab=knowledge{gid_q}&msg={quote(message)}&err={'1' if not ok else ''}")

    async def _handle_panel_knowledge_view(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        gid_raw = str(form.get("group_id", "") or "").strip()
        if not gid_raw.isdigit():
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群号必须是数字") + "&err=1")
        return web.HTTPFound(f"/panel?tab=knowledge&gid={gid_raw}")

    async def _handle_panel_knowledge_add(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._meme_manager is None:
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群聊知识系统未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        if not gid_raw.isdigit():
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群号必须是数字") + "&err=1")
        group_id = int(gid_raw)
        term = str(form.get("term", "") or "")
        meaning = str(form.get("meaning", "") or "")
        examples = str(form.get("examples", "") or "")
        confidence = str(form.get("confidence", "") or "medium").strip()
        ok, msg = self._meme_manager.add_knowledge(
            group_id, term, meaning, examples=examples, source="manual", confidence=confidence)
        return web.HTTPFound(f"/panel?tab=knowledge&gid={gid_raw}&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_knowledge_save(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._meme_manager is None:
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群聊知识系统未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        id_raw = str(form.get("id", "") or "").strip()
        if not gid_raw.isdigit() or not id_raw.isdigit():
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("参数非法") + "&err=1")
        group_id = int(gid_raw)
        knowledge_id = int(id_raw)
        ok, msg = self._meme_manager.update_knowledge(
            knowledge_id, group_id,
            meaning=str(form.get("meaning", "") or ""),
            examples=str(form.get("examples", "") or ""),
            confidence=str(form.get("confidence", "") or "").strip(),
            status=str(form.get("status", "") or "").strip(),
        )
        return web.HTTPFound(f"/panel?tab=knowledge&gid={gid_raw}&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_knowledge_delete(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._meme_manager is None:
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群聊知识系统未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        id_raw = str(form.get("id", "") or "").strip()
        if not gid_raw.isdigit() or not id_raw.isdigit():
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("参数非法") + "&err=1")
        ok, msg = self._meme_manager.delete_knowledge(int(id_raw), int(gid_raw))
        return web.HTTPFound(f"/panel?tab=knowledge&gid={gid_raw}&msg={quote(msg)}&err={'1' if not ok else ''}")

    async def _handle_panel_knowledge_clear(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.HTTPFound("/panel")
        form = await request.post()
        if self._meme_manager is None:
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群聊知识系统未启用") + "&err=1")
        gid_raw = str(form.get("group_id", "") or "").strip()
        if not gid_raw.isdigit():
            return web.HTTPFound("/panel?tab=knowledge&msg=" + quote("群号必须是数字") + "&err=1")
        ok, msg = self._meme_manager.clear_group(int(gid_raw))
        return web.HTTPFound(f"/panel?tab=knowledge&gid={gid_raw}&msg={quote(msg)}&err={'1' if not ok else ''}")
