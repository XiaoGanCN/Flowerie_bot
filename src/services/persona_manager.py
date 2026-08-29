"""PersonaManager：人格资源业务层（全局 / 群聊 / 自定义，SQLite 持久化）。

层级与回退（由调用方在组装时决定，本类只负责解析）：
    Group Persona > Global Persona > 内置默认（flowerie）

设计要点：
- 人格是**独立资源**：每个 Persona 有完整独立的 system_prompt /
  vocabulary / behavior_rules / response_style，不是对花璃 Prompt 的微调。
- 人格**动态解析**：每次请求时按 (group_id) 解析生效人格，绝不写入长期
  记忆 / 上下文 —— 切换人格不会污染 Context / Memory。
- 内置预设（builtin=1）只读保护：不可删除、不可改 id；可编辑内容（reset
  语义由调用方决定）。首次启动幂等写入（persona_presets.py 为唯一来源）。
- 权限：修改/创建/删除/绑定由调用方（Web UI / 指令层）做管理员校验。
- 悬挂引用安全：群/全局引用了不存在的人格时自动回退下一级（不崩溃）。
"""
import re
import time
from typing import Dict, List, Optional, Tuple

from src.repositories.settings_repository import SettingsRepository
from src.services.persona_presets import BUILTIN_PERSONAS, DEFAULT_PERSONA_ID
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# 人格 id 白名单（小写字母/数字/下划线/短横线，长度 ≤ 32）
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class PersonaManager:
    """人格管理：内置预设播种 / CRUD / 全局与群聊绑定 / 生效解析 / Prompt 组装。"""

    def __init__(self, repository: SettingsRepository, default_persona_id: str = DEFAULT_PERSONA_ID,
                 max_system_prompt_length: int = 8000):
        self.repository = repository
        self.default_persona_id = default_persona_id or DEFAULT_PERSONA_ID
        self.max_system_prompt_length = max(500, int(max_system_prompt_length))
        self._reserved_ids = {p["id"] for p in BUILTIN_PERSONAS}
        self._seed_builtins()

    # ---------- 内置预设播种（幂等；persona_presets.py 为唯一来源） ----------
    def _seed_builtins(self) -> int:
        """把官方预设写入 personas 表（已存在则跳过）。返回播种条数。"""
        seeded = 0
        for preset in BUILTIN_PERSONAS:
            if self.repository.get_persona(preset["id"]) is None:
                self.repository.upsert_persona({
                    **preset,
                    "builtin": True,
                    "created_at": time.time(),
                })
                seeded += 1
        if seeded:
            logger.info("persona_builtins_seeded count=%d", seeded, extra={"event": "persona_seeded"})
        return seeded

    # ---------- 生效解析（动态决定，不写入任何长期存储） ----------
    def resolve_persona(self, group_id: Optional[int] = None) -> Optional[dict]:
        """解析某群当前生效人格（Group > Global > 内置默认）。"""
        if group_id is not None:
            pid = self.repository.get_group_persona_id(group_id)
            if pid:
                persona = self.repository.get_persona(pid)
                if persona:
                    return persona
        gid = self.repository.get_global_persona_id()
        if gid:
            persona = self.repository.get_persona(gid)
            if persona:
                return persona
        persona = self.repository.get_persona(self.default_persona_id)
        if persona:
            return persona
        # 兜底：任何内置预设
        for p in self.repository.list_personas():
            if p.get("builtin"):
                return p
        return None

    def resolve_persona_id(self, group_id: Optional[int] = None) -> Optional[str]:
        persona = self.resolve_persona(group_id)
        return persona["id"] if persona else None

    def resolve_persona_name(self, group_id: Optional[int] = None) -> str:
        persona = self.resolve_persona(group_id)
        return (persona or {}).get("name") or self.default_persona_id

    # ---------- 组合（人格块 → system prompt 的一部分） ----------
    @staticmethod
    def compose_system_prompt(persona: dict) -> str:
        """把人格资源组合成完整人格块。

        system_prompt 为基底；behavior_rules / response_style / vocabulary
        非空且未被基底包含时作为补充段追加。内置预设的补充段自带标题
        （以【开头），自定义人格的补充段自动加标题。
        """
        base = (persona.get("system_prompt") or "").strip()
        parts = [base] if base else []
        for label, field in (
            ("行为规则", persona.get("behavior_rules")),
            ("回复风格", persona.get("response_style")),
            ("词库参考", persona.get("vocabulary")),
        ):
            field = (field or "").strip()
            if not field or field in base:
                continue
            if field.startswith("【"):
                parts.append(field)
            else:
                parts.append(f"【{label}】\n{field}")
        return "\n\n".join(parts)

    # ---------- 读取 ----------
    def list_personas(self) -> List[dict]:
        return self.repository.list_personas()

    def get_persona(self, persona_id: str) -> Optional[dict]:
        return self.repository.get_persona(persona_id)

    # ---------- 创建 / 更新 / 删除 ----------
    @staticmethod
    def _validate_id(persona_id: str) -> Optional[str]:
        pid = (persona_id or "").strip().lower()
        if not _ID_RE.fullmatch(pid):
            return "人格 ID 只能含小写字母/数字/下划线/短横线（1~32 字符）"
        return None

    def create_persona(self, persona_id: str, name: str, description: str = "",
                       system_prompt: str = "", vocabulary: str = "",
                       behavior_rules: str = "", response_style: str = "") -> Tuple[bool, str]:
        """创建自定义人格。返回 (是否成功, 提示)。"""
        pid = (persona_id or "").strip().lower()
        err = self._validate_id(pid)
        if err:
            return False, err
        if pid in self._reserved_ids:
            return False, f"ID {pid!r} 是官方内置人格，不能重复创建"
        if self.repository.get_persona(pid) is not None:
            return False, f"人格 ID {pid!r} 已存在"
        name = (name or "").strip()
        if not name:
            return False, "人格名称不能为空"
        if len(name) > 50:
            return False, "人格名称最长 50 字"
        system_prompt = (system_prompt or "").strip()
        if not system_prompt:
            return False, "system_prompt 不能为空（人格核心文本必填）"
        if len(system_prompt) > self.max_system_prompt_length:
            return False, f"system_prompt 过长（{len(system_prompt)} 字，上限 {self.max_system_prompt_length}）"
        self.repository.upsert_persona({
            "id": pid,
            "name": name,
            "description": (description or "").strip()[:200],
            "system_prompt": system_prompt,
            "vocabulary": (vocabulary or "").strip(),
            "behavior_rules": (behavior_rules or "").strip(),
            "response_style": (response_style or "").strip(),
            "builtin": False,
            "created_at": time.time(),
        })
        logger.info("persona_created id=%s", pid, extra={"event": "persona_created"})
        return True, f"人格「{name}」已创建"

    def update_persona(self, persona_id: str, name: Optional[str] = None,
                       description: Optional[str] = None, system_prompt: Optional[str] = None,
                       vocabulary: Optional[str] = None, behavior_rules: Optional[str] = None,
                       response_style: Optional[str] = None) -> Tuple[bool, str]:
        """更新人格（None 字段保持不变）。内置人格允许改内容但不改 id/不删除。"""
        existing = self.repository.get_persona(persona_id)
        if existing is None:
            return False, "人格不存在"
        name = (name or "").strip()
        if name and len(name) > 50:
            return False, "人格名称最长 50 字"
        system_prompt = (system_prompt or "").strip() if system_prompt is not None else (existing.get("system_prompt") or "")
        if not system_prompt:
            return False, "system_prompt 不能为空"
        if len(system_prompt) > self.max_system_prompt_length:
            return False, f"system_prompt 过长（{len(system_prompt)} 字，上限 {self.max_system_prompt_length}）"
        self.repository.upsert_persona({
            "id": persona_id,
            "name": name or existing.get("name", persona_id),
            "description": (description or existing.get("description") or "").strip()[:200],
            "system_prompt": system_prompt,
            "vocabulary": (vocabulary if vocabulary is not None else existing.get("vocabulary") or "").strip(),
            "behavior_rules": (behavior_rules if behavior_rules is not None else existing.get("behavior_rules") or "").strip(),
            "response_style": (response_style if response_style is not None else existing.get("response_style") or "").strip(),
            "builtin": bool(existing.get("builtin")),
            "created_at": existing.get("created_at") or time.time(),
        })
        logger.info("persona_updated id=%s", persona_id, extra={"event": "persona_updated"})
        return True, "人格已更新"

    def delete_persona(self, persona_id: str) -> Tuple[bool, str]:
        """删除自定义人格（内置人格禁止删除）。"""
        existing = self.repository.get_persona(persona_id)
        if existing is None:
            return False, "人格不存在"
        if existing.get("builtin"):
            return False, "内置人格不能删除"
        self.repository.delete_persona(persona_id)
        logger.info("persona_deleted id=%s", persona_id, extra={"event": "persona_deleted"})
        return True, "人格已删除"

    # ---------- 全局人格 ----------
    def get_global(self) -> Optional[dict]:
        gid = self.repository.get_global_persona_id()
        return self.repository.get_persona(gid) if gid else None

    def set_global(self, persona_id: str) -> Tuple[bool, str]:
        persona = self.repository.get_persona(persona_id)
        if persona is None:
            return False, "人格不存在"
        self.repository.set_global_persona_id(persona_id)
        logger.info("persona_global_set id=%s", persona_id, extra={"event": "persona_global_set"})
        return True, f"全局人格已设为「{persona.get('name')}」"

    # ---------- 群聊人格 ----------
    def get_group(self, group_id: int) -> Optional[dict]:
        pid = self.repository.get_group_persona_id(group_id)
        return self.repository.get_persona(pid) if pid else None

    def set_group(self, group_id: int, persona_id: str) -> Tuple[bool, str]:
        persona = self.repository.get_persona(persona_id)
        if persona is None:
            return False, "人格不存在"
        self.repository.set_group_persona_id(group_id, persona_id)
        logger.info("persona_group_set group=%s id=%s", group_id, persona_id,
                    extra={"event": "persona_group_set", "group_id": group_id})
        return True, f"本群人格已设为「{persona.get('name')}」（回退链：本群 > 全局 > 内置）"

    def clear_group(self, group_id: int) -> Tuple[bool, str]:
        removed = self.repository.delete_group_persona_id(group_id)
        return True, "已清除本群人格，自动回退全局/内置" if removed else "本群未设置专属人格（无需清除）"
