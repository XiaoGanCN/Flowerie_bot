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
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.core.sanitizer import validate_memory_content
from src.plugins.http_action import plugin_http_request, redact_url
from src.plugins.installer import PluginInstallError, PluginInstaller
from src.plugins.manifest import PluginManifest, PluginManifestError
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
                 installer: Optional[PluginInstaller] = None):
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

    # ================= 声明式 JSON 插件（进程内规则匹配，无代码执行） =================
    def _declarative_match(self, manifest: PluginManifest, event_type: str, payload: Dict[str, Any]) -> List[dict]:
        if event_type not in ("message", "group_message", "notice"):
            return []
        wants = "message" if event_type in ("message", "group_message") else "notice"
        actions: List[dict] = []
        for rule in manifest.declarations:
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
        if action_type == "send_message":
            group_id = payload.get("group_id")
            message = str(payload.get("message") or "")[:2000]
            if not group_id or not message:
                return {"ok": False, "error": "send_message 需要 group_id 与 message"}
            if self.sender is None:
                return {"ok": False, "error": "sender 未注入（不可用）"}
            ok = await self.sender.send_group_message(int(group_id), message)
            return {"ok": ok, "group_id": int(group_id)}
        if action_type == "send_private_message":
            user_id = payload.get("user_id")
            message = str(payload.get("message") or "")[:2000]
            if not user_id or not message:
                return {"ok": False, "error": "send_private_message 需要 user_id 与 message"}
            if self.sender is None:
                return {"ok": False, "error": "sender 未注入（不可用）"}
            ok = await self.sender.send_private_message(int(user_id), message)
            return {"ok": ok, "user_id": int(user_id)}
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


def asyncio_create_task(coro) -> None:
    """兼容无事件循环上下文（_stop_runtime 可能被同步调用）。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 无运行中的循环：直接丢弃（进程即将退出场景）
    loop.create_task(coro)
