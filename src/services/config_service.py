"""ConfigService：Web UI 可编辑配置的业务层。

- 单一 schema 来源：可管理配置项在此定义（name/类型/默认值/分类/是否敏感/
  是否热更新/校验/控件元数据）
- 持久化双写：**`.env` 文件**（Web UI 修改后真正写入项目 .env，重启后由
  pydantic-settings 读取）+ SQLite `app_config` 表（SettingsRepository，
  保留既有优先级链与向后兼容）；未覆盖项回退到 .env/代码默认
- 优先级：Persistent Config > Environment > Code Default（既有设计不变）
- Secret 保护：敏感项只返回脱敏视图（sk-****abcd），修改时输入新值才覆盖
- 热更新：修改后立即写入 Settings 实例（运行中的 manager 每次读 config 属性）
- 需要重启项：明确标记，UI 提示"已保存，需要重启生效"
- 管理密码：**禁止明文落库**——register_user 只保存 scrypt 哈希；登录/注册
  校验统一走 verify_password（兼容旧明文并自动迁移为哈希）
"""
import hashlib
import json
import os
import secrets as _secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import Settings
from src.repositories.env_store import EnvFileStore
from src.repositories.settings_repository import SettingsRepository
from src.services.config_schema import (
    _ENUM_OPTIONS,
    _ENUM_VALUES,
    _RANGES,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    SCHEMA,
)
from src.utils.logging_setup import get_logger

logger = get_logger(__name__)

# scrypt 参数（stdlib hashlib，零新依赖；N 需为 2 的幂）
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_PASSWORD_PREFIX = "scrypt$"


def hash_password(password: str) -> str:
    """口令哈希（scrypt + 随机盐），返回自描述格式 `scrypt$N$r$p$salt_hex$hash_hex`。

    绝不返回/记录明文。参数内嵌便于未来升级 KDF 参数。
    """
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"{_PASSWORD_PREFIX}{_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """安全校验口令：stored 为 scrypt 哈希时用哈希校验；为旧版明文（历史 DB 或 .env）
    时用恒定时间比较。任何解析失败按不通过处理，不抛异常。"""
    if not stored:
        return False
    if stored.startswith(_PASSWORD_PREFIX):
        try:
            _prefix, n, r, p, salt_hex, hash_hex = stored.split("$", 5)
            dk = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=_SCRYPT_DKLEN)
            return _secrets.compare_digest(dk.hex(), hash_hex)
        except (ValueError, TypeError):
            return False
    # 旧版明文（兼容迁移路径）
    return _secrets.compare_digest(password, stored)


def is_hashed_password(stored: str) -> bool:
    return bool(stored) and stored.startswith(_PASSWORD_PREFIX)


class ConfigService:
    """Web UI 可编辑配置的业务层。

    数据声明（SCHEMA/分类/范围/枚举）已拆分到 src/services/config_schema.py；
    本类只保留校验、持久化（.env + settings.db 双写）与热更新逻辑。
    以下类属性为兼容别名：ConfigService.SCHEMA 等仍可直接访问。
    """

    SCHEMA = SCHEMA
    CATEGORY_LABELS = CATEGORY_LABELS
    CATEGORY_ORDER = CATEGORY_ORDER
    _ENUM_VALUES = _ENUM_VALUES
    _ENUM_OPTIONS = _ENUM_OPTIONS
    _RANGES = _RANGES
    """管理 Web UI 可编辑的配置（覆盖 Settings 全部可管理变量）。

    SCHEMA 条目格式：key -> (分类, 类型, 是否敏感, 是否热更新, 说明)
    类型：str / secret / int / float / bool / list-int / list-str / textarea / json
    - list-int / list-str：逗号分隔（.env 中序列化为 JSON 数组）
    - textarea：每行一条（如 POKE_REPLIES，.env 中序列化为 JSON 数组）
    - json：原样 JSON 文本（如 MCP_SERVERS）
    管理账号（WEB_UI_USERNAME / WEB_UI_PASSWORD）**不在此表**：由注册页管理，
    密码只存 scrypt 哈希，禁止通过配置表单写入明文 .env。
    """

    # key -> (分类, 类型, 是否敏感, 是否热更新, 说明)

    # 分类显示名与展示顺序（表单分组用）

    def __init__(self, config: Settings, repository: SettingsRepository,
                 env_path: Optional[str] = None):
        self.config = config
        self.repository = repository
        # .env 持久化：显式传入路径才启用（main.py 传入项目根 .env；测试可指向临时目录）
        if env_path is None:
            self.env_store: Optional[EnvFileStore] = None
        else:
            self.env_store = EnvFileStore(env_path)

    @staticmethod
    def default_env_path() -> str:
        """项目根目录的 .env（main.py 位于项目根）。"""
        return str(Path(__file__).resolve().parents[2] / ".env")

    # ---------- 读取 ----------
    def list_configs(self) -> List[Dict[str, Any]]:
        """按分类返回全部可管理配置（含当前值/默认/脱敏/表单元数据）。"""
        overrides = dict(self.repository.list_configs())
        result = []
        for key, (category, ctype, is_secret, hot, desc) in self.SCHEMA.items():
            current = overrides.get(key) if key in overrides else getattr(self.config, key, None)
            entry = {
                "key": key,
                "category": category,
                "type": ctype,
                "description": desc,
                "secret": is_secret,
                "hot_reload": hot,
                "current": self._display(key, ctype, current, is_secret),
                "set": key in overrides,
            }
            entry.update(self._field_meta(key, ctype))
            result.append(entry)
        return result

    def get_value(self, key: str) -> Optional[str]:
        """读取某配置的实际生效值（持久化优先）。"""
        if key not in self.SCHEMA:
            return None
        override = self.repository.get_config(key)
        if override is not None:
            return override
        return getattr(self.config, key, None)

    def apply_persisted(self) -> int:
        """启动阶段：把 settings.db 的持久化覆盖合并进运行中的 Settings 实例。

        优先级：Persistent Config > Environment > Code Default（P2-2 修复）。
        - 只应用 SCHEMA 内且类型/范围校验通过的键；非法值跳过并记日志，
          不阻止 Bot 启动（无效持久化配置不把 Bot 带入危险状态）。
        - 敏感项（secret）按用户保存时的原值应用；显示层仍走 _mask 脱敏。
        返回成功应用的键数。
        """
        applied = 0
        for key, value in self.repository.list_configs():
            if key not in self.SCHEMA:
                logger.warning("config_persisted_unknown key=%s（跳过）", key)
                continue
            _cat, ctype, _secret, _hot, _desc = self.SCHEMA[key]
            if self._validate(key, ctype, str(value)) is None:
                logger.warning("config_persisted_invalid key=%s（跳过，使用 .env/默认值）", key)
                continue
            try:
                setattr(self.config, key, self._coerce(ctype, str(value)))
                applied += 1
            except Exception:  # noqa: BLE001
                logger.exception("config_persisted_apply_failed key=%s", key)
        if applied:
            logger.info(
                "config_persisted_applied count=%d", applied,
                extra={"event": "config_persisted_applied", "count": applied},
            )
        return applied

    # ---------- 注册/修改管理账号 ----------
    def register_user(self, username: str, password: str) -> Tuple[bool, str]:
        """注册/修改 Web UI 管理账号（持久化到 settings.db，优先级高于 .env）。

        之后登录优先使用这里保存的账号；.env 的 WEB_UI_USERNAME / WEB_UI_PASSWORD
        仅作未注册时的兜底。账号信息存入项目数据目录（data/settings.db），不依赖 .env。
        安全：密码只存 scrypt 哈希，**绝不写明文**，也不写入任何日志。
        """
        username = (username or "").strip()
        if not (3 <= len(username) <= 32):
            return False, "用户名长度需 3~32 字符"
        if len(password or "") < 6:
            return False, "密码至少 6 位"
        password_hash = hash_password(password)
        self.repository.set_config("WEB_UI_USERNAME", username)
        self.repository.set_config("WEB_UI_PASSWORD", password_hash)
        try:
            setattr(self.config, "WEB_UI_USERNAME", username)
            setattr(self.config, "WEB_UI_PASSWORD", password_hash)
        except Exception:  # noqa: BLE001
            pass
        logger.info("web_ui account registered user=%s", username, extra={"event": "config_reload"})
        return True, "注册成功，请用新账号登录"

    def migrate_plaintext_password(self, username: str, plaintext: str) -> bool:
        """把 settings.db 中的旧版**明文**密码迁移为 scrypt 哈希（登录成功后调用）。

        迁移前提：调用方已用 verify_password 验证通过。迁移成功后 DB 不再保留明文；
        .env 的明文仍保留（文件本身无法改写，但此后登录走 DB 哈希）。
        返回是否发生了写入。
        """
        stored = self.repository.get_config("WEB_UI_PASSWORD")
        if stored is not None and not is_hashed_password(stored):
            password_hash = hash_password(plaintext)
            self.repository.set_config("WEB_UI_USERNAME", username)
            self.repository.set_config("WEB_UI_PASSWORD", password_hash)
            try:
                setattr(self.config, "WEB_UI_USERNAME", username)
                setattr(self.config, "WEB_UI_PASSWORD", password_hash)
            except Exception:  # noqa: BLE001
                pass
            logger.info("web_ui password migrated to hash user=%s", username,
                        extra={"event": "config_reload"})
            return True
        return False

    # ---------- 修改 ----------
    def update(self, key: str, raw_value: str) -> Tuple[bool, str]:
        """更新单个配置（API 与旧版单键表单路径）。返回 (是否成功, 提示信息)。"""
        if key not in self.SCHEMA:
            return False, "未知配置项"
        category, ctype, is_secret, hot, _ = self.SCHEMA[key]
        raw = "" if raw_value is None else str(raw_value)
        # 敏感项：空输入 = 不修改（保留旧值）
        if is_secret and not raw.strip():
            return False, "未输入新值，保持原密钥"
        value = self._validate(key, ctype, raw)
        if value is None:
            return False, "配置值校验失败"
        try:
            self._commit({key: value}, {key: ctype})
        except OSError as e:
            logger.exception("config_env_write_failed key=%s", key)
            return False, f"写入 .env 失败：{e}"
        logger.info("config_updated key=%s hot=%s", key, hot, extra={"event": "config_updated"})
        if hot:
            return True, "已保存，立即生效"
        return True, "已保存，需要重启生效"

    def update_many(self, updates: Dict[str, str]) -> Tuple[bool, str]:
        """批量更新（无 JS 分组表单提交）。

        - 先整体校验：任一键非法 → 全部不写（.env 与 settings.db 都不动）
        - 敏感项留空 = 不修改（跳过，不报错）
        - 全部合法 → 一次性写入 .env（原子）+ settings.db + 热更新
        """
        if not updates:
            return False, "没有提交任何配置"
        validated: Dict[str, str] = {}
        ctypes: Dict[str, str] = {}
        errors: List[str] = []
        for key, raw in updates.items():
            if key not in self.SCHEMA:
                continue  # 表单中的非配置字段（提交按钮等）忽略
            category, ctype, is_secret, hot, _ = self.SCHEMA[key]
            raw = "" if raw is None else str(raw)
            if is_secret and not raw.strip():
                continue  # 密钥留空 = 不修改
            value = self._validate(key, ctype, raw)
            if value is None:
                errors.append(f"{key} 值不合法")
                continue
            validated[key] = value
            ctypes[key] = ctype
        if errors:
            return False, "未保存：" + "；".join(errors)
        if not validated:
            return False, "没有可保存的配置（密钥留空视为不修改）"
        try:
            self._commit(validated, ctypes)
        except OSError as e:
            logger.exception("config_env_write_failed keys=%s", sorted(validated))
            return False, f"写入 .env 失败：{e}"
        hot_ok = all(self.SCHEMA[k][3] for k in validated)
        logger.info("config_updated_many keys=%s", sorted(validated), extra={"event": "config_updated"})
        if hot_ok:
            return True, f"已保存 {len(validated)} 项，全部立即生效"
        return True, f"已保存 {len(validated)} 项，部分配置需重启生效"

    def _commit(self, validated: Dict[str, str], ctypes: Dict[str, str]) -> None:
        """持久化：.env（原子）→ settings.db → 热更新 Settings 实例。"""
        if self.env_store is not None:
            env_updates = {k: self._env_value(k, ctypes[k], v) for k, v in validated.items()}
            self.env_store.update(env_updates)
        for key, value in validated.items():
            ctype = ctypes[key]
            self.repository.set_config(key, value)
            try:
                setattr(self.config, key, self._coerce(ctype, value))
            except Exception:  # noqa: BLE001
                pass

    # ---------- 校验 / 类型 ----------
    # 枚举选项的显示/提交大小写（LOG_LEVEL 保持大写，loguru 对大小写敏感时也兼容）

    def _validate(self, key: str, ctype: str, raw: str) -> Optional[str]:
        """校验并返回规范化存储值；非法返回 None。"""
        raw = raw.strip()
        try:
            if ctype == "int":
                v = int(raw)
                lo, hi = self._RANGES.get(key, (0, None))
                if v < lo:
                    return None
                if hi is not None and v > hi:
                    return None
                return str(v)
            if ctype == "float":
                float(raw)  # 仅校验
                return raw
            if ctype == "bool":
                if raw.lower() not in ("true", "false", "1", "0"):
                    return None
                return "true" if raw.lower() in ("true", "1") else "false"
            if ctype == "secret":
                if len(raw) < 6:
                    return None
                return raw
            if ctype == "str":
                if key in self._ENUM_VALUES and raw.lower() not in self._ENUM_VALUES[key]:
                    return None
                return raw
            if ctype == "json":
                # MCP_SERVERS：必须是合法 JSON 数组（元素级校验交给启动 validate_config）
                if raw:
                    data = json.loads(raw)
                    if not isinstance(data, list):
                        return None
                return raw
            if ctype == "list-int":
                items = self._parse_int_list(raw)
                if items is None:
                    return None
                return ",".join(str(x) for x in items)
            if ctype == "list-str":
                items = [x.strip() for x in raw.split(",") if x.strip()]
                return ",".join(items)
            if ctype == "textarea":
                items = [x.strip() for x in raw.splitlines() if x.strip()]
                return "\n".join(items)
        except ValueError:
            return None
        return raw

    @staticmethod
    def _parse_int_list(raw: str) -> Optional[List[int]]:
        """解析逗号分隔或 JSON 数组形式的整数列表；非法返回 None。"""
        raw = raw.strip()
        if raw == "":
            return []
        if raw.startswith("["):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return None
            if not isinstance(data, list):
                return None
            result = []
            for x in data:
                if isinstance(x, bool) or not isinstance(x, int):
                    return None
                result.append(x)
            return result
        result = []
        for token in raw.split(","):
            token = token.strip()
            if token == "":
                continue
            if not token.isdigit():
                return None
            result.append(int(token))
        return result

    @staticmethod
    def _coerce(ctype: str, value: str):
        if ctype == "int":
            return int(value)
        if ctype == "float":
            return float(value)
        if ctype == "bool":
            return value.lower() in ("true", "1")
        if ctype == "list-int":
            return [int(x) for x in value.split(",") if x.strip()]
        if ctype == "list-str":
            return [x.strip() for x in value.split(",") if x.strip()]
        if ctype == "textarea":
            return [x for x in value.split("\n") if x.strip()]
        return value

    def _env_value(self, key: str, ctype: str, value: str) -> str:
        """配置值 → .env 存储表示（列表/多行序列化为 JSON 数组，pydantic 可解析）。"""
        if ctype in ("list-int", "list-str", "textarea"):
            if ctype == "list-int":
                items = [int(x) for x in value.split(",") if x.strip()]
            else:
                sep = "\n" if ctype == "textarea" else ","
                items = [x.strip() for x in value.split(sep) if x.strip()]
            return json.dumps(items, ensure_ascii=False)
        return value

    @classmethod
    def _display(cls, key: str, ctype: str, current, is_secret: bool) -> str:
        """配置当前值 → 表单显示字符串（列表逗号分隔 / 多行换行 / 敏感脱敏）。"""
        if current is None:
            return ""
        if is_secret:
            return cls._mask(key, current, True)
        if ctype == "bool":
            return str(current).lower()
        if ctype == "list-int":
            items = current.split(",") if isinstance(current, str) else current
            return ", ".join(str(x) for x in items)
        if ctype == "list-str":
            items = current.split(",") if isinstance(current, str) else current
            return ", ".join(str(x) for x in items)
        if ctype == "textarea":
            items = current.split("\n") if isinstance(current, str) else current
            return "\n".join(str(x) for x in items)
        return str(current)

    @classmethod
    def _field_meta(cls, key: str, ctype: str) -> Dict[str, Any]:
        """表单控件元数据：数值范围 / 枚举选项 / 文本域行数。"""
        meta: Dict[str, Any] = {}
        if ctype in ("int", "float"):
            lo, hi = cls._RANGES.get(key, (None, None))
            if lo is not None:
                meta["min"] = lo
            if hi is not None:
                meta["max"] = hi
            meta["step"] = 1 if ctype == "int" else "any"
        if ctype == "str" and key in cls._ENUM_VALUES:
            meta["options"] = cls._ENUM_OPTIONS.get(key, sorted(cls._ENUM_VALUES[key]))
        if ctype in ("textarea", "json"):
            meta["rows"] = 10 if ctype == "textarea" else 6
        return meta

    @staticmethod
    def _mask(key: str, value, is_secret: bool) -> str:
        if not is_secret or value is None:
            return "" if value is None else str(value)
        v = str(value)
        if len(v) <= 8:
            return "****"
        return v[:4] + "****" + v[-4:]
