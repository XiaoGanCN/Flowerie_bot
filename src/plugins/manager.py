"""PluginManager：插件注册表 + 生命周期 + 事件分发 + Action 执行（受控运行时入口）。

职责边界（与 Flowerie 现有架构对齐）：
- 注册表：settings.db `plugins` 表（manifest 镜像 / enabled / 批准的权限 / 保护级别 / 状态）
- 自动发现：扫描 PLUGIN_DIR 下的 */manifest.json → 校验 → 注册（**发现 ≠ 自动执行**，
  新插件一律 enabled=0，由管理员明确启用并批准权限）
- 运行时：enabled 插件启动独立子进程（PluginRuntime）；崩溃/超时标记 unhealthy，
  Flowerie 继续运行（不影响其他插件与主流程）
- 事件分发：message/notice 等事件按 read_message 权限投递（声明式 JSON 插件在进程内
  匹配规则，不落地执行代码）
- Action 执行：**唯一副作用出口**，一切 action 先过 PermissionManager，拒绝即记日志
  （plugin_permission_denied），绝不静默放行；token/secret 不写日志

安全不变式（任何保护级别都不豁免）：管理员权限（Web UI 认证）、安装完整性检查、
manifest 校验、进程隔离、日志、崩溃保护、资源限制、权限强制。
"""
import json
import os
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.sanitizer import validate_memory_content
from src.plugins.http_action import plugin_http_request, redact_url
from src.plugins.installer import PluginInstaller, PluginInstallError
from src.plugins.manifest import PluginManifest, PluginManifestError
from src.sdk.bot import Bot
from src.sdk.event import BotEvent
from src.sdk.matcher import Matcher
from src.sdk.onebot.adapter import OneBotAdapter
from src.plugins.permissions import PermissionManager
from src.plugins.runtime import PluginRuntime
from src.repositories.settings_repository import SettingsRepository
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 事件类型 → 需要权限（未批准则事件不投递）
_EVENT_PERMISSION = {"message": "read_message", "group_message": "read_message",
                     "notice": "read_message", "command": "read_message"}
# 声明式插件支持的模板字段（值只能来自事件 payload，绝不执行代码）
_TEMPLATE_FIELDS = ("group_id", "user_id", "text", "message_name", "message")


class PluginManager:
    """受控插件运行时管理器。"""

    def __init__(self, config, repository: SettingsRepository,
                 sender: Optional[Any] = None, memory_manager: Optional[Any] = None,
                 state_provider: Optional[Callable[[str, Any], Optional[dict]]] = None,
                 installer: Optional[PluginInstaller] = None, context_manager: Optional[Any] = None):
        self.config = config
        self.repository = repository
        self.sender = sender
        self.memory_manager = memory_manager
        # state_provider: (kind, id) -> dict | None（get_group/get_user 的数据源，由 main 注入）
        self.state_provider = state_provider
        self.installer = installer or PluginInstaller(self._plugin_dir())
        self._runtimes: Dict[str, PluginRuntime] = {}
        self._manifest_cache: Dict[str, Optional[PluginManifest]] = {}
        self._started = False
        # 本实例（bot）已发送的 message_id 记录：插件只能撤回这些消息（防删他人消息）
        self._sent_message_ids: list = []
        self._context_manager = context_manager   # SDK get_context 复用 ContextManager
        self._matchers: Dict[str, list] = {}      # plugin_id -> [Matcher]（SDK 注册）
        self._bot = None                          # SDK 匹配用 Bot（惰性构建）

    @property
    def plugin_dir(self) -> str:
        return os.path.abspath(self._plugin_dir())

    def _plugin_dir(self) -> str:
        return str(getattr(self.config, "PLUGIN_DIR", "./plugins") or "./plugins")

    # ================= 注册表 =================
    def _manifest_of(self, record: dict) -> Optional[PluginManifest]:
        """从注册行解析 manifest（带进程内缓存，manifest 变更时失效）。"""
        m = self._manifest_cache.get(record["id"], "missing")
        if m == "missing" or (isinstance(m, PluginManifest) and m.to_json() != record.get("manifest_json")):
            try:
                m = PluginManifest.from_dict(json.loads(record["manifest_json"]))
            except (PluginManifestError, ValueError):
                m = None
            self._manifest_cache[record["id"]] = m
        return m

    def list_plugins(self) -> List[dict]:
        """注册表视图（含解析后的 manifest 摘要与运行时状态）。"""
        rows = self.repository.list_plugins()
        result = []
        for row in rows:
            manifest = self._manifest_of(row)
            rt = self._runtimes.get(row["id"])
            status = row["status"]
            if rt is not None:
                status = rt.status
            result.append({
                "id": row["id"],
                "enabled": bool(row["enabled"]),
                "status": status,
                "protection": row.get("protection", "normal") or "normal",
                "approved_permissions": [p for p in (row.get("approved_permissions") or "").split(",") if p],
                "install_source": row.get("install_source", ""),
                "version": manifest.version if manifest else "?",
                "name": manifest.name if manifest else row["id"],
                "runtime": manifest.runtime if manifest else "?",
                "declared_permissions": manifest.permissions if manifest else [],
                "description": manifest.description if manifest else "",
                "manifest_valid": manifest is not None,
            })
        return result

    def get_plugin(self, plugin_id: str) -> Optional[dict]:
        for item in self.list_plugins():
            if item["id"] == plugin_id:
                return item
        return None

    # ================= 自动发现（发现 ≠ 自动执行） =================
    def discover(self) -> List[str]:
        """扫描插件目录：新插件注册为 disabled（启用需管理员明确操作）。返回新发现 id 列表。"""
        os.makedirs(self.plugin_dir, exist_ok=True)
        known = {row["id"] for row in self.repository.list_plugins()}
        discovered: List[str] = []
        for entry in sorted(os.listdir(self.plugin_dir)):
            dir_path = os.path.join(self.plugin_dir, entry)
            if not os.path.isdir(dir_path):
                continue
            manifest_path = os.path.join(dir_path, "manifest.json")
            if not os.path.isfile(manifest_path):
                continue
            try:
                manifest = PluginManifest.load(manifest_path)
            except (PluginManifestError, OSError, ValueError) as e:
                logger.warning("plugin_discover_invalid dir=%s reason=%s", entry, e,
                               extra={"event": "plugin_invalid"})
                if entry in known:
                    self._mark_status(entry, "invalid")
                continue
            if manifest.id in known or manifest.id in discovered:
                continue
            self.repository.upsert_plugin({
                "id": manifest.id,
                "manifest_json": manifest.to_json(),
                "enabled": False,           # 发现 ≠ 自动执行
                "approved_permissions": [],
                "protection": self._protection_level(),
                "status": "discovered",
                "install_source": "local",
            })
            discovered.append(manifest.id)
            logger.info("plugin_discovered id=%s version=%s", manifest.id, manifest.version,
                        extra={"event": "plugin_discovered"})
        return discovered

    def refresh(self) -> Tuple[List[str], List[str]]:
        """重新扫描（新发现的插件 disabled；manifest 变更的注册行同步 + 停掉旧运行时）。"""
        discovered = self.discover()
        changed: List[str] = []
        for row in self.repository.list_plugins():
            dir_path = os.path.join(self.plugin_dir, row["id"])
            manifest_path = os.path.join(dir_path, "manifest.json")
            if not os.path.isfile(manifest_path):
                continue
            try:
                manifest = PluginManifest.load(manifest_path)
            except (PluginManifestError, OSError, ValueError):
                continue
            if manifest.to_json() != row.get("manifest_json"):
                self._stop_runtime(row["id"])
                self.repository.upsert_plugin({
                    "id": row["id"], "manifest_json": manifest.to_json(),
                    "enabled": bool(row["enabled"]),
                    "approved_permissions": (row.get("approved_permissions") or "").split(","),
                    "protection": row.get("protection", "normal") or "normal",
                    "status": "discovered", "install_source": row.get("install_source", ""),
                })
                changed.append(row["id"])
                logger.info("plugin_manifest_updated id=%s", row["id"], extra={"event": "plugin_updated"})
        return discovered, changed

    # ================= 安装 / 卸载 =================
    def install_upload(self, data: bytes, filename: str = "") -> Tuple[bool, str]:
        """文件上传安装（校验通过才落盘 + 注册；安装后一律 disabled）。"""
        try:
            manifest = self.installer.install_from_bytes(data, source="upload", filename=filename)
        except PluginInstallError as e:
            logger.warning("plugin_install_rejected source=upload reason=%s", e,
                           extra={"event": "plugin_install_rejected"})
            return False, str(e)
        return self._register_installed(manifest, "upload")

    def install_url(self, url: str) -> Tuple[bool, str]:
        """URL 下载安装（同步占位：必须走 install_url_async，见 Web UI 处理器）。"""
        raise RuntimeError("URL 安装必须使用 install_url_async（async 环境）")

    async def install_url_async(self, url: str) -> Tuple[bool, str]:
        try:
            manifest = await self.installer.install_from_url(url)
        except PluginInstallError as e:
            logger.warning("plugin_install_rejected source=url url=%s reason=%s",
                           redact_url(url), e, extra={"event": "plugin_install_rejected"})
            return False, str(e)
        return self._register_installed(manifest, "url")

    def _register_installed(self, manifest: PluginManifest, source: str) -> Tuple[bool, str]:
        existing = self.repository.get_plugin(manifest.id)
        if existing:
            return False, f"插件 {manifest.id} 已存在于注册表（请先卸载）"
        self.repository.upsert_plugin({
            "id": manifest.id, "manifest_json": manifest.to_json(),
            "enabled": False, "approved_permissions": [],
            "protection": self._protection_level(), "status": "discovered",
            "install_source": source,
        })
        logger.info("plugin_installed id=%s version=%s source=%s", manifest.id, manifest.version, source,
                    extra={"event": "plugin_installed"})
        return True, f"插件「{manifest.name}」已安装（默认禁用，请手动启用并批准权限）"

    def uninstall(self, plugin_id: str) -> Tuple[bool, str]:
        row = self.repository.get_plugin(plugin_id)
        if row is None:
            return False, "插件不存在"
        self._stop_runtime(plugin_id)
        self._manifest_cache.pop(plugin_id, None)
        self.repository.delete_plugin(plugin_id)
        dir_path = os.path.join(self.plugin_dir, plugin_id)
        if os.path.isdir(dir_path):
            shutil.rmtree(dir_path, ignore_errors=True)
        logger.info("plugin_uninstalled id=%s", plugin_id, extra={"event": "plugin_uninstalled"})
        return True, f"插件 {plugin_id} 已卸载"

    # ================= 启用 / 禁用（管理员操作） =================
    async def enable(self, plugin_id: str, approved_permissions: Optional[List[str]] = None,
                     protection: Optional[str] = None) -> Tuple[bool, str]:
        """启用插件并批准权限（权限子集 = manifest 声明 ∩ 管理员选择）。"""
        row = self.repository.get_plugin(plugin_id)
        if row is None:
            return False, "插件不存在（请先扫描/安装）"
        manifest = self._manifest_of(row)
        if manifest is None:
            return False, "manifest 校验失败，无法启用"
        if manifest.runtime == "node":
            import shutil as _shutil
            if _shutil.which("node") is None:
                return False, "Node.js 插件需要 node 可执行文件（环境未安装）"
        protection = (protection or row.get("protection") or "normal")
        if protection not in PermissionManager.PROTECTION_LEVELS:
            return False, f"保护级别非法: {protection}"
        declared = set(manifest.permissions)
        chosen = [p for p in (approved_permissions or []) if p and p in declared]
        if manifest.permissions and approved_permissions is not None and set(approved_permissions) - declared:
            return False, "批准了 manifest 未声明的权限（拒绝）"
        if manifest.permissions and not chosen:
            return False, "该插件声明了权限，请至少批准一项后再启用（0 权限不建议启用）"
        # 已运行则先停（更新权限/保护级别后重启）
        self._stop_runtime(plugin_id)
        self.repository.upsert_plugin({
            "id": plugin_id, "manifest_json": manifest.to_json(),
            "enabled": True, "approved_permissions": chosen,
            "protection": protection, "status": "starting",
            "install_source": row.get("install_source", ""),
        })
        try:
            rt = self._start_runtime(plugin_id, manifest, chosen, protection)
            if manifest.runtime != "json":
                await rt.start()  # json 声明式插件无需子进程
            else:
                rt.status = "running"
                self._mark_status(plugin_id, "running")
        except Exception as e:  # noqa: BLE001 - 启用失败：回滚 enabled
            self.repository.upsert_plugin({
                "id": plugin_id, "manifest_json": manifest.to_json(),
                "enabled": False, "approved_permissions": chosen,
                "protection": protection, "status": "error", "install_source": row.get("install_source", ""),
            })
            logger.error("plugin_enable_failed id=%s reason=%s", plugin_id, e,
                         extra={"event": "plugin_enable_failed"})
            return False, f"启用失败: {type(e).__name__}: {e}"
        logger.info("plugin_enabled id=%s perms=%s protection=%s", plugin_id, chosen, protection,
                    extra={"event": "plugin_enabled"})
        return True, f"插件「{manifest.name}」已启用（权限: {', '.join(chosen) or '无'}；保护: {protection}）"

    def disable(self, plugin_id: str) -> Tuple[bool, str]:
        row = self.repository.get_plugin(plugin_id)
        if row is None:
            return False, "插件不存在"
        self._stop_runtime(plugin_id)
        self.repository.upsert_plugin({
            "id": plugin_id, "manifest_json": row["manifest_json"],
            "enabled": False, "approved_permissions": (row.get("approved_permissions") or "").split(","),
            "protection": row.get("protection", "normal") or "normal",
            "status": "disabled", "install_source": row.get("install_source", ""),
        })
        self._manifest_cache.pop(plugin_id, None)
        logger.info("plugin_disabled id=%s", plugin_id, extra={"event": "plugin_disabled"})
        return True, f"插件 {plugin_id} 已禁用"

    def set_protection(self, level: str) -> Tuple[bool, str]:
        """全局插件保护级别（Web UI 开关；影响后续启动的运行时限制）。"""
        if level not in PermissionManager.PROTECTION_LEVELS:
            return False, "保护级别非法（normal/relaxed/unsafe）"
        setattr(self.config, "PLUGIN_PROTECTION", level)
        return True, f"插件保护级别已设为 {level}（运行中插件需重启生效）"

    def _protection_level(self) -> str:
        level = str(getattr(self.config, "PLUGIN_PROTECTION", "normal") or "normal").lower()
        return level if level in PermissionManager.PROTECTION_LEVELS else "normal"

    # ================= 运行时生命周期 =================
    def _start_runtime(self, plugin_id: str, manifest: Optional[PluginManifest],
                       approved: List[str], protection: str) -> PluginRuntime:
        if manifest is None:
            manifest = self._manifest_of(self.repository.get_plugin(plugin_id))
            if manifest is None:
                raise RuntimeError("manifest 非法")
        rt = PluginRuntime(
            plugin_id, manifest, os.path.join(self.plugin_dir, plugin_id),
            protection=protection, on_exit=self._on_runtime_exit,
        )
        rt.permissions = PermissionManager(approved, protection)
        rt.set_action_handler(self._handle_action)
        self._runtimes[plugin_id] = rt
        return rt

    async def start_all(self) -> None:
        """启动所有 enabled 插件（发现新插件；失败记状态不影响启动）。"""
        self._started = True
        try:
            self.discover()
        except Exception as e:  # noqa: BLE001
            logger.error("plugin_discover_failed reason=%s", e)
        for row in self.repository.list_plugins():
            if not row.get("enabled"):
                continue
            manifest = self._manifest_of(row)
            if manifest is None:
                self._mark_status(row["id"], "invalid")
                continue
            if manifest.runtime == "json":
                # 声明式：进程内（无代码执行），无需子进程
                rt = PluginRuntime(row["id"], manifest, os.path.join(self.plugin_dir, row["id"]),
                                   protection=row.get("protection") or "normal",
                                   on_exit=self._on_runtime_exit)
                rt.permissions = PermissionManager(
                    (row.get("approved_permissions") or "").split(","),
                    row.get("protection") or "normal")
                self._runtimes[row["id"]] = rt
                self._mark_status(row["id"], "running")
                continue
            try:
                rt = self._start_runtime(row["id"], manifest,
                                         (row.get("approved_permissions") or "").split(","),
                                         row.get("protection") or "normal")
                await rt.start()
            except Exception as e:  # noqa: BLE001 - 单插件失败不阻塞
                self._mark_status(row["id"], "error")
                logger.error("plugin_start_failed id=%s reason=%s", row["id"], e,
                             extra={"event": "plugin_failed"})

    async def shutdown(self) -> None:
        self._started = False
        for plugin_id in list(self._runtimes.keys()):
            rt = self._runtimes.pop(plugin_id, None)
            if rt is not None:
                try:
                    await rt.shutdown()
                except Exception:  # noqa: BLE001
                    pass
        self._runtimes.clear()

    def _stop_runtime(self, plugin_id: str) -> None:
        rt = self._runtimes.pop(plugin_id, None)
        if rt is not None:
            asyncio_create_task(rt.shutdown())

    def _mark_status(self, plugin_id: str, status: str) -> None:
        row = self.repository.get_plugin(plugin_id)
        if row is None:
            return
        self.repository.upsert_plugin({
            "id": plugin_id, "manifest_json": row["manifest_json"],
            "enabled": bool(row["enabled"]), "approved_permissions": (row.get("approved_permissions") or "").split(","),
            "protection": row.get("protection", "normal") or "normal",
            "status": status, "install_source": row.get("install_source", ""),
        })

    def _on_runtime_exit(self, plugin_id: str, reason: str, code: int) -> None:
        """插件进程异常退出：标记 unhealthy（Flowerie 继续运行；管理员可重新启用）。"""
        self._mark_status(plugin_id, "crashed")
        logger.error("plugin_crashed id=%s reason=%s code=%s", plugin_id, reason, code,
                     extra={"event": "plugin_crash"})

    # ================= 事件分发 =================
    async def dispatch_event(self, event_type: str, payload: Dict[str, Any]) -> List[dict]:
        """向所有 enabled 且有 read_message 权限的插件投递事件；返回执行摘要（测试可断言）。"""
        if not self._started:
            self._started = True  # 幂等：未显式 start_all 时自动发现一次（测试友好）
            try:
                self.discover()
            except Exception:  # noqa: BLE001
                pass
        summary: List[dict] = []
        required = _EVENT_PERMISSION.get(event_type, "read_message")
        for row in self.repository.list_plugins():
            if not row.get("enabled"):
                continue
            record_id = row["id"]
            approved = set((row.get("approved_permissions") or "").split(","))
            if required not in approved:
                continue  # 事件权限未批准：不投递（权限不是提示文字）
            manifest = self._manifest_of(row)
            # SDK 模式：插件注册过 matcher → 只投递命中事件（payload 附带 matched 列表）
            matchers = self._matchers.get(record_id)
            if matchers:
                hits = await self._match_plugin_payload(record_id, matchers, event_type, payload)
                if not hits:
                    continue
                payload = {**payload, "matched": hits}
            if manifest is None:
                continue
            try:
                if manifest.runtime == "json":
                    actions = self._declarative_match(manifest, event_type, payload)
                    for action in actions:
                        summary.extend(await self._execute_action(record_id, action))
                else:
                    rt = self._runtimes.get(record_id)
                    if rt is None or not rt.healthy:
                        continue
                    actions = await rt.dispatch_event(event_type, payload)
                    for action in actions:
                        summary.extend(await self._execute_action(record_id, action))
            except Exception as e:  # noqa: BLE001 - 插件异常被隔离
                logger.error("plugin_event_error id=%s event=%s reason=%s", record_id, event_type, e,
                             extra={"event": "plugin_error"})
        return summary

    async def _match_plugin_payload(self, plugin_id, matchers, event_type, payload):
        """SDK Matcher 匹配：返回命中的 [{name, kind, args, block}]（priority 降序）。"""
        try:
            if self._bot is None and self.sender is not None:
                self._bot = Bot(OneBotAdapter(self.sender, self._context_manager))
            if self._bot is None:
                return []  # 无 sender：匹配不可用 → 不投递（保守）
            event = BotEvent.from_dict(payload)
            hits = []
            for m in sorted(matchers, key=lambda x: x.priority, reverse=True):
                try:
                    if await m.amatches(event, self._bot):
                        hits.append({"name": m.name or "", "kind": m.kind,
                                     "args": getattr(event, "matcher_args", ""),
                                     "block": m.block})
                except Exception:  # noqa: BLE001 - 匹配失败按不命中处理
                    continue
            return hits
        except Exception:  # noqa: BLE001 - 匹配框架异常 → 不投递（保守）
            return []

    # ================= 声明式 JSON 插件（进程内规则匹配，无代码执行） =================
    def _declarative_match(self, manifest: PluginManifest, event_type: str, payload: Dict[str, Any]) -> List[dict]:
        if event_type not in ("message", "group_message", "notice"):
            return []
        wants = "message" if event_type in ("message", "group_message") else "notice"
        actions: List[dict] = []
        # 优先级大者先执行；stop=true 的规则命中后，本插件剩余规则不再匹配（Matcher 阻断）
        rules = sorted(manifest.declarations, key=lambda r: int(r.get("priority") or 0), reverse=True)
        for rule in rules:
            if rule["event"] != wants:
                continue
            match = rule["match"]
            if not self._rule_matches(match, payload):
                continue
            for a in rule["actions"]:
                actions.append({
                    "type": str(a.get("type") or ""),
                    "payload": self._substitute(a.get("payload") or {}, payload),
                })
            if rule.get("stop"):  # Matcher 阻断：插件内后续规则不再匹配（跨插件不阻断，保隔离）
                break
        return actions

    @staticmethod
    def _rule_matches(match: dict, payload: Dict[str, Any]) -> bool:
        for key, value in match.items():
            if key == "text_contains":
                if str(value) not in str(payload.get("text", "")):
                    return False
            elif key == "text_prefix":
                if not str(payload.get("text", "")).startswith(str(value)):
                    return False
            elif key == "text_exact":
                if str(payload.get("text", "")) != str(value):
                    return False
            elif key == "text_suffix":
                if not str(payload.get("text", "")).endswith(str(value)):
                    return False
            elif key == "text_regex":
                try:
                    if re.search(str(value)[:200], str(payload.get("text", ""))) is None:
                        return False
                except re.error:
                    return False
            elif key == "command":
                # 命令匹配：与消息开头（可选前缀）解析的命令名及参数
                import shlex
                text = str(payload.get("text", "")).strip()
                parts = shlex.split(text) if text else []
                if not parts:
                    return False
                cmd_name, cmd_args = parts[0].lstrip("/!."), parts[1:]
                wanted = str(value)
                okay = cmd_name in {wanted, wanted.lstrip("/!.")}
                if not okay:
                    return False
                if isinstance(match.get("args"), (list, tuple)) and match.get("args"):
                    if len(cmd_args) < len(match["args"]):
                        return False
                return True
            elif key == "user_id":
                try:
                    if int(payload.get("user_id", -1)) != int(value):
                        return False
                except (TypeError, ValueError):
                    return False
            elif key == "group_id":
                try:
                    if int(payload.get("group_id", -1)) != int(value):
                        return False
                except (TypeError, ValueError):
                    return False
        return True

    @classmethod
    def _substitute(cls, value: Any, payload: Dict[str, Any]) -> Any:
        """模板替换：${group_id} 等只从事件 payload 取值（纯文本替换，绝无代码路径）。"""
        if isinstance(value, str):
            out = value
            for field in _TEMPLATE_FIELDS:
                out = out.replace("${" + field + "}", str(payload.get(field, "")))
            return out
        if isinstance(value, dict):
            return {k: cls._substitute(v, payload) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._substitute(v, payload) for v in value]
        return value

    # ================= Action 执行（唯一副作用出口） =================
    async def _handle_action(self, plugin_id: str, action: str, payload: Dict[str, Any]) -> dict:
        """PluginRuntime 的回调：执行单个 action（含权限检查与结果回传插件）。"""
        results = await self._execute_action(plugin_id, {"type": action, "payload": payload})
        if not results:
            return {"ok": False, "error": "permission denied or unknown action"}
        return results[0].get("result", {"ok": True})

    async def _execute_action(self, plugin_id: str, action: dict) -> List[dict]:
        action_type = str(action.get("type") or "")
        payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
        row = self.repository.get_plugin(plugin_id)
        if row is None:
            return [{"plugin": plugin_id, "action": action_type, "ok": False, "denied": True,
                     "error": "plugin not in registry"}]
        rt = self._runtimes.get(plugin_id)
        pm = rt.permissions if rt is not None else PermissionManager(
            (row.get("approved_permissions") or "").split(","), row.get("protection") or "normal")
        if not pm.check(action_type):
            reason = pm.denied_reason(action_type)
            logger.warning("plugin_permission_denied id=%s action=%s reason=%s",
                           plugin_id, action_type, reason, extra={"event": "plugin_permission_denied"})
            return [{"plugin": plugin_id, "action": action_type, "ok": False, "denied": True,
                     "error": reason}]
        try:
            result = await self._run_action(plugin_id, action_type, payload)
            return [{"plugin": plugin_id, "action": action_type, "ok": bool(result.get("ok", False)),
                     "denied": False, "result": result}]
        except Exception as e:  # noqa: BLE001 - action 异常不扩散
            logger.error("plugin_action_error id=%s action=%s reason=%s", plugin_id, action_type, e,
                         extra={"event": "plugin_error"})
            return [{"plugin": plugin_id, "action": action_type, "ok": False, "denied": False,
                     "error": f"{type(e).__name__}: {e}"}]

    async def _run_action(self, plugin_id: str, action_type: str, payload: Dict[str, Any]) -> dict:
        """action 具体实现（按类型；全部结果回传插件，不执行任何未实现动作）。"""
        if action_type == "test":
            return {"ok": True, "plugin": plugin_id}
        if action_type == "log":
            level = str(payload.get("level") or "info")[:16]
            message = str(payload.get("message") or "")[:500]
            logger.info("plugin_log id=%s level=%s message=%s", plugin_id, level, message,
                        extra={"event": "plugin_log"})
            return {"ok": True}
        if action_type in ("send_message", "send_private_message", "send_reply"):
            # 消息发送三者共用：message 支持 str（含 [CQ:...]）或 OneBot 段数组 list
            group_id = payload.get("group_id")
            user_id = payload.get("user_id")
            message = payload.get("message")
            reply_id = payload.get("reply_id")
            if action_type == "send_private_message":
                target, target_id = "private", user_id
            else:
                target, target_id = "group", group_id
            if not target_id or not message:
                return {"ok": False, "error": f"{action_type} 需要目标与 message"}
            if self.sender is None:
                return {"ok": False, "error": "sender 未注入（不可用）"}
            result = await self.sender.send_msg_raw(target, int(target_id), message, reply_id=reply_id)
            if isinstance(message, str):
                result.setdefault("raw_text", str(message)[:2000])
            if result.get("ok") and result.get("message_id"):
                self._sent_message_ids.append(int(result["message_id"]))
                if len(self._sent_message_ids) > 200:  # 只保留最近 200 条（撤回窗口）
                    self._sent_message_ids = self._sent_message_ids[-200:]
            return {"ok": bool(result.get("ok")), "target": target, "target_id": int(target_id),
                    "message_id": result.get("message_id"),
                    "raw_text": result.get("raw_text", str(message)[:2000] if isinstance(message, str) else "")}
        if action_type == "delete_message":
            # 撤回：只允许撤回本 bot 发送过（且由本实例记录）的消息
            message_id = payload.get("message_id")
            if message_id is None:
                return {"ok": False, "error": "delete_message 需要 message_id"}
            if int(message_id) not in self._sent_message_ids:
                return {"ok": False, "denied": True,
                        "error": "只能撤回本 bot 发送的消息（message_id 未被记录）"}
            if self.sender is None:
                return {"ok": False, "error": "sender 未注入（不可用）"}
            ok = await self.sender.delete_msg(int(message_id))
            if ok:
                try:
                    self._sent_message_ids.remove(int(message_id))
                except ValueError:
                    pass
            return {"ok": ok, "message_id": int(message_id)}
        if action_type == "get_message":
            message_id = payload.get("message_id")
            if message_id is None or self.sender is None:
                return {"ok": False, "error": "get_message 需要 message_id（或 sender 不可用）"}
            return await self.sender.get_msg(int(message_id))
        if action_type == "get_group_history":
            group_id = payload.get("group_id")
            count = int(payload.get("count") or 15)
            if not group_id or self.sender is None:
                return {"ok": False, "error": "get_group_history 需要 group_id（或 sender 不可用）"}
            return await self.sender.get_group_msg_history(int(group_id), count)
        if action_type == "get_context":
            # 群上下文：最近 10 条（与主进程上下文同理的轻量版）
            group_id = payload.get("group_id")
            if not group_id or self.sender is None:
                return {"ok": False, "error": "get_context 需要 group_id（或 sender 不可用）"}
            return await self.sender.get_group_msg_history(int(group_id), 10)
        if action_type == "get_group":
            group_id = payload.get("group_id")
            if not group_id:
                return {"ok": False, "error": "get_group 需要 group_id"}
            info = self._state_lookup("group", int(group_id)) or {}
            return {"ok": True, "group_id": int(group_id), "info": info}
        if action_type == "get_user":
            user_id = payload.get("user_id")
            if not user_id:
                return {"ok": False, "error": "get_user 需要 user_id"}
            info = self._state_lookup("user", int(user_id)) or {}
            return {"ok": True, "user_id": int(user_id), "info": info}
        if action_type == "get_group_member":
            group_id, user_id = payload.get("group_id"), payload.get("user_id")
            if not group_id or not user_id or self.sender is None:
                return {"ok": False, "error": "get_group_member 需要 group_id 与 user_id（或 sender 不可用）"}
            return await self.sender.get_group_member_info(int(group_id), int(user_id))
        if action_type == "get_group_members":
            group_id = payload.get("group_id")
            if not group_id or self.sender is None:
                return {"ok": False, "error": "get_group_members 需要 group_id（或 sender 不可用）"}
            return await self.sender.get_group_member_list(int(group_id))
        if action_type == "group_ban":
            group_id, user_id = payload.get("group_id"), payload.get("user_id")
            duration = int(payload.get("duration") or 0)
            if not group_id or not user_id or self.sender is None:
                return {"ok": False, "error": "group_ban 需要 group_id 与 user_id（或 sender 不可用）"}
            if duration < 0 or duration > 30 * 24 * 3600:
                return {"ok": False, "error": "group_ban duration 0~2592000 秒"}
            ok = await self.sender.set_group_ban(int(group_id), int(user_id), duration)
            return {"ok": ok, "group_id": int(group_id), "user_id": int(user_id), "duration": duration}
        if action_type == "group_kick":
            group_id, user_id = payload.get("group_id"), payload.get("user_id")
            reject_add = bool(payload.get("reject_add", False))
            if not group_id or not user_id or self.sender is None:
                return {"ok": False, "error": "group_kick 需要 group_id 与 user_id（或 sender 不可用）"}
            ok = await self.sender.set_group_kick(int(group_id), int(user_id), reject_add)
            return {"ok": ok, "group_id": int(group_id), "user_id": int(user_id)}
        if action_type == "group_admin":
            group_id, user_id = payload.get("group_id"), payload.get("user_id")
            enable = bool(payload.get("enable", False))
            if not group_id or not user_id or self.sender is None:
                return {"ok": False, "error": "group_admin 需要 group_id 与 user_id（或 sender 不可用）"}
            ok = await self.sender.set_group_admin(int(group_id), int(user_id), enable)
            return {"ok": ok, "group_id": int(group_id), "user_id": int(user_id), "enable": enable}
        if action_type == "matcher_register":
            # SDK matcher 注册（幂等）：插件上报匹配规则，主进程匹配后只投递命中事件
            raw = payload.get("matchers") or []
            if not isinstance(raw, list):
                return {"ok": False, "error": "matcher_register 需要 matchers 数组"}
            compiled, errors = [], []
            for i, m in enumerate(raw):
                try:
                    compiled.append(Matcher(
                        str(m.get("kind") or "keyword"), m.get("pattern"),
                        priority=int(m.get("priority") or 0),
                        block=bool(m.get("block", False)),
                        name=str(m.get("name") or f"m{i}"),
                        rule=_rule_from(m.get("rule") or {}),
                    ))
                except Exception as e:  # noqa: BLE001
                    errors.append(f"matcher[{i}]: {e}")
            if compiled:
                self._matchers[plugin_id] = compiled
            return {"ok": True, "count": len(compiled), "errors": errors}
        if action_type in ("is_group_admin", "is_group_owner"):
            group_id, user_id = payload.get("group_id"), payload.get("user_id")
            if not group_id or not user_id or self.sender is None:
                return {"ok": False, "error": f"{action_type} 需要 group_id 与 user_id（或 sender 不可用）"}
            info = await self.sender.get_group_member_info(int(group_id), int(user_id))
            role = str((info or {}).get("role") or "member")
            wanted = "owner" if action_type == "is_group_owner" else ("owner", "admin")
            return {"ok": True, "result": role in wanted}
        if action_type == "get_memory":
            user_id = payload.get("user_id")
            group_id = payload.get("group_id")
            if not user_id or not group_id or self.memory_manager is None:
                return {"ok": False, "error": "get_memory 需要 user_id 与 group_id（或 memory 不可用）"}
            mem = self.memory_manager.get_user_memory(int(user_id), int(group_id))
            if not isinstance(mem, dict):
                return {"ok": False, "error": "memory 不可用"}
            return {"ok": True, "memory": mem}
        if action_type == "write_memory":
            user_id = payload.get("user_id")
            group_id = payload.get("group_id")
            content = str(payload.get("content") or "")[:500]
            if not user_id or not group_id or not content or self.memory_manager is None:
                return {"ok": False, "error": "write_memory 需要 user_id/group_id/content"}
            safe = validate_memory_content(content)
            if safe is None:
                return {"ok": False, "error": "记忆内容被安全策略拒绝（防注入）"}
            await self.memory_manager.append_memory_text(
                int(user_id), int(group_id), safe, source_user=int(user_id),
                source_group=int(group_id), source_message_id=0, confidence="plugin")
            return {"ok": True}
        if action_type == "http_request":
            return await plugin_http_request(payload)
        if action_type == "file_read":
            rel = str(payload.get("path") or "").strip()
            return self._file_read(plugin_id, rel)
        if action_type == "file_write":
            rel = str(payload.get("path") or "").strip()
            data = str(payload.get("data") or "")[:256 * 1024]
            return self._file_write(plugin_id, rel, data)
        if action_type in ("execute_process", "webhook"):
            return {"ok": False, "error": f"action {action_type!r} 在 Plugin API v1 未实现（保留权限）"}
        return {"ok": False, "error": f"未知 action: {action_type!r}"}

    def _state_lookup(self, kind: str, ident: int) -> Optional[dict]:
        if self.state_provider is None:
            return None
        try:
            return self.state_provider(kind, ident) or None
        except Exception:  # noqa: BLE001
            return None

    def _file_read(self, plugin_id: str, rel: str) -> dict:
        """filesystem_read：仅允许读取插件自身目录内的文件（真实路径校验）。"""
        base = os.path.realpath(os.path.join(self.plugin_dir, plugin_id))
        target = os.path.realpath(os.path.join(base, rel))
        if os.path.commonpath([base, target]) != base or not os.path.isfile(target):
            return {"ok": False, "error": "路径越界（仅允许插件目录内文件）"}
        if os.path.getsize(target) > 256 * 1024:
            return {"ok": False, "error": "文件过大（>256KB）"}
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                return {"ok": True, "content": f.read()[:256 * 1024]}
        except OSError as e:
            return {"ok": False, "error": f"读取失败: {e}"}

    def _file_write(self, plugin_id: str, rel: str, data: str) -> dict:
        """filesystem_write：仅允许写入插件自身目录（真实路径校验 + 大小上限）。"""
        if not rel or ".." in rel.split("/") or rel.startswith("/") or "\\" in rel:
            return {"ok": False, "error": "路径越界（仅允许插件目录内相对路径）"}
        base = os.path.realpath(os.path.join(self.plugin_dir, plugin_id))
        target = os.path.realpath(os.path.join(base, rel))
        if os.path.commonpath([base, target]) != base:
            return {"ok": False, "error": "路径越界"}
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(data)
        return {"ok": True, "bytes": len(data.encode("utf-8"))}


def _rule_from(conditions: dict):
    """SDK rule 条件 dict → Rule 对象（用户/群/机器人角色条件）。"""
    from src.sdk.matcher import Rule
    return Rule(**dict(conditions or {}))


def asyncio_create_task(coro) -> None:
    """兼容无事件循环上下文（_stop_runtime 可能被同步调用）。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无运行中的循环：直接丢弃（进程即将退出场景）
    loop.create_task(coro)
