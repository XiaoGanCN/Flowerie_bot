"""PluginManifest：插件统一契约（manifest.json 严格校验）。

字段（全部必须经过 schema 校验，未知字段一律拒绝 —— 防 manifest 注入）：
- id           小写字母开头，[a-z0-9_-]，≤32 字符
- name         1~64 字符
- version      x.y.z
- runtime      python | node | json
- entry        相对路径文件名（禁止绝对路径 / .. / \\）
- api_version  仅支持 "1"
- permissions  允许的权限键列表（见 permissions.ALL_PERMISSIONS）
- author / description / config 可选
- declarations 仅 runtime=json 时允许（声明式插件规则）

安全：本模块只做**校验与数据表达**，不包含任何执行逻辑；
执行路径（Python/Node 子进程）与权限检查见 runtime.py / manager.py。
"""
import json
import re
from typing import Any, Dict, List, Optional

from src.plugins.permissions import ALL_PERMISSIONS

# 允许的 manifest 顶层字段（严格白名单）
_ALLOWED_KEYS = frozenset({
    "id", "name", "version", "author", "description", "runtime",
    "entry", "api_version", "permissions", "config", "declarations",
})

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
# 条目路径：无前导 / 、无 .. 段、无反斜杠、每段 [A-Za-z0-9_.-]
_ENTRY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/]{0,127}$")
_RUNTIMES = ("python", "node", "json")
_API_VERSIONS = ("1",)
MAX_MANIFEST_BYTES = 64 * 1024          # manifest.json 大小上限（64KB）
MAX_PERMISSIONS = 24                    # 权限声明上限
MAX_NAME_LEN = 64
MAX_AUTHOR_LEN = 64
MAX_DESC_LEN = 500
MAX_CONFIG_CHARS = 16 * 1024            # config 对象序列化上限（16KB）
MAX_DECLARATIONS = 64                   # 声明式规则条数上限


class PluginManifestError(ValueError):
    """manifest 校验失败（携带可展示的拒绝原因）。"""


class PluginManifest:
    """校验通过的插件 manifest（不可变数据对象）。"""

    __slots__ = ("id", "name", "version", "author", "description", "runtime",
                 "entry", "api_version", "permissions", "config", "declarations")

    def __init__(self, id: str, name: str, version: str, runtime: str, entry: str,
                 api_version: str, permissions: List[str], author: str = "",
                 description: str = "", config: Optional[Dict[str, Any]] = None,
                 declarations: Optional[List[Dict[str, Any]]] = None):
        self.id = id
        self.name = name
        self.version = version
        self.author = author
        self.description = description
        self.runtime = runtime
        self.entry = entry
        self.api_version = api_version
        self.permissions = list(permissions)
        self.config = dict(config or {})
        self.declarations = list(declarations or [])

    # ---------- 校验 ----------
    @classmethod
    def load(cls, manifest_path: str) -> "PluginManifest":
        """从磁盘读取并校验 manifest.json（大小上限）。"""
        import os
        if not os.path.isfile(manifest_path):
            raise PluginManifestError("manifest.json 不存在")
        if os.path.getsize(manifest_path) > MAX_MANIFEST_BYTES:
            raise PluginManifestError(f"manifest.json 超过大小上限（{MAX_MANIFEST_BYTES} 字节）")
        with open(manifest_path, "r", encoding="utf-8") as f:
            raw = f.read()
        return cls.from_dict(json.loads(raw), source=manifest_path)

    @classmethod
    def from_dict(cls, data: Any, source: str = "") -> "PluginManifest":
        """校验任意来源的 manifest 数据（上传 ZIP / URL / 磁盘扫描共用同一闸门）。"""
        if not isinstance(data, dict):
            raise PluginManifestError("manifest 必须是 JSON 对象")
        unknown = set(data.keys()) - _ALLOWED_KEYS
        if unknown:
            raise PluginManifestError(f"manifest 包含未知字段: {sorted(unknown)}")
        # 必填字段
        for key in ("id", "name", "version", "runtime", "entry", "api_version", "permissions"):
            if key not in data:
                raise PluginManifestError(f"manifest 缺少必填字段: {key}")
        plugin_id = str(data["id"]).strip().lower()
        if plugin_id != str(data["id"]).strip() or not _ID_RE.fullmatch(plugin_id):
            raise PluginManifestError(
                "插件 id 只能为小写字母/数字/下划线/短横线（小写字母开头，≤32 字符）")
        name = str(data["name"]).strip()
        if not name or len(name) > MAX_NAME_LEN:
            raise PluginManifestError(f"插件 name 必须为 1~{MAX_NAME_LEN} 字符")
        version = str(data["version"]).strip()
        if not _VERSION_RE.fullmatch(version):
            raise PluginManifestError("插件 version 必须为 x.y.z 格式")
        runtime = str(data["runtime"]).strip().lower()
        if runtime not in _RUNTIMES:
            raise PluginManifestError(f"runtime 非法（可选: {'/'.join(_RUNTIMES)}），当前: {runtime!r}")
        entry = str(data["entry"]).strip()
        if runtime in ("python", "node"):
            if not _ENTRY_RE.fullmatch(entry):
                raise PluginManifestError(
                    "entry 必须是相对路径文件名（仅字母/数字/下划线/点/短横线/斜杠，"
                    "禁止绝对路径与 .. 段）")
            if entry.startswith("/") or "\\" in entry or ".." in entry.split("/"):
                raise PluginManifestError("entry 包含非法路径（绝对路径/.. /反斜杠）")
        else:
            # json 声明式：entry 可空（规则在 manifest.declarations 内）
            if entry and (not _ENTRY_RE.fullmatch(entry) or "\\" in entry or ".." in entry.split("/")):
                raise PluginManifestError("json 插件的 entry 若填写必须是安全相对路径")
            entry = entry or ""
        api_version = str(data["api_version"]).strip()
        if api_version not in _API_VERSIONS:
            raise PluginManifestError(f"api_version 仅支持: {', '.join(_API_VERSIONS)}")
        raw_perms = data["permissions"]
        if not isinstance(raw_perms, list):
            raise PluginManifestError("permissions 必须是数组")
        if len(raw_perms) > MAX_PERMISSIONS:
            raise PluginManifestError(f"permissions 超过上限（{MAX_PERMISSIONS} 项）")
        perms: List[str] = []
        for p in raw_perms:
            p = str(p).strip().lower()
            if p not in ALL_PERMISSIONS:
                raise PluginManifestError(f"权限 '{p}' 不在允许列表内")
            if p not in perms:
                perms.append(p)
        author = str(data.get("author") or "").strip()[:MAX_AUTHOR_LEN]
        description = str(data.get("description") or "").strip()[:MAX_DESC_LEN]
        config_raw = data.get("config", {})
        if not isinstance(config_raw, dict):
            raise PluginManifestError("config 必须是 JSON 对象")
        try:
            config_text = json.dumps(config_raw, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            raise PluginManifestError("config 必须可 JSON 序列化") from None
        if len(config_text) > MAX_CONFIG_CHARS:
            raise PluginManifestError(f"config 过大（> {MAX_CONFIG_CHARS} 字符）")
        declarations: List[Dict[str, Any]] = []
        if "declarations" in data:
            if runtime != "json":
                raise PluginManifestError("declarations 仅 runtime=json 的声明式插件允许")
            declarations = cls._validate_declarations(data["declarations"])
        return cls(
            id=plugin_id, name=name, version=version, runtime=runtime, entry=entry,
            api_version=api_version, permissions=perms, author=author,
            description=description, config=config_raw,
            declarations=declarations,
        )

    @staticmethod
    def _validate_declarations(raw: Any) -> List[Dict[str, Any]]:
        """声明式插件规则校验（仅在进程内做模板替换与动作转发，绝不执行代码）。"""
        if not isinstance(raw, list):
            raise PluginManifestError("declarations 必须是数组")
        if len(raw) > MAX_DECLARATIONS:
            raise PluginManifestError(f"declarations 超过上限（{MAX_DECLARATIONS} 条）")
        result: List[Dict[str, Any]] = []
        for i, rule in enumerate(raw):
            if not isinstance(rule, dict):
                raise PluginManifestError(f"declarations[{i}] 必须是对象")
            event = str(rule.get("event") or "").strip()
            if event not in ("message", "group_message", "notice"):
                raise PluginManifestError(f"declarations[{i}] event 非法: {event!r}")
            match = rule.get("match", {})
            if not isinstance(match, dict):
                raise PluginManifestError(f"declarations[{i}] match 必须是对象")
            for key in match:
                if key not in ("text_contains", "text_prefix", "user_id", "group_id"):
                    raise PluginManifestError(f"declarations[{i}] match.{key} 不支持")
                if key in ("user_id", "group_id") and not isinstance(match[key], int):
                    raise PluginManifestError(f"declarations[{i}] match.{key} 必须是整数")
                if key in ("text_contains", "text_prefix") and not isinstance(match[key], str):
                    raise PluginManifestError(f"declarations[{i}] match.{key} 必须是字符串")
                if len(str(match[key])) > 200:
                    raise PluginManifestError(f"declarations[{i}] match.{key} 过长")
            actions = rule.get("actions")
            if not isinstance(actions, list) or not actions:
                raise PluginManifestError(f"declarations[{i}] 必须有 actions 数组")
            if len(actions) > 4:
                raise PluginManifestError(f"declarations[{i}] actions 超过 4 条")
            for j, act in enumerate(actions):
                if not isinstance(act, dict) or not str(act.get("type") or "").strip():
                    raise PluginManifestError(f"declarations[{i}].actions[{j}] 必须是含 type 的对象")
            result.append({"event": event, "match": match, "actions": actions})
        return result

    # ---------- 序列化 ----------
    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id, "name": self.name, "version": self.version,
            "runtime": self.runtime, "entry": self.entry,
            "api_version": self.api_version, "permissions": list(self.permissions),
        }
        if self.author:
            data["author"] = self.author
        if self.description:
            data["description"] = self.description
        if self.config:
            data["config"] = self.config
        if self.declarations:
            data["declarations"] = self.declarations
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<PluginManifest {self.id}@{self.version} {self.runtime}>"
