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
import math
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

        优先级：Persistent Config > Environment > Code Default（P2-2 修复），
        **但本地手工修改的 .env 不会被 settings.db 的旧值覆盖**（P4-1 修复）：
        - 若 .env 中存在同 key 且 .env 文件修改时间晚于 settings.db 的
          updated_at → 视为管理员在本地直接改了 .env，以 .env 新值为准，
          并把新值同步回 settings.db（避免重启后旧 UI 值再次压掉 .env）。
        - 其余情况维持原优先级（settings.db > .env > 代码默认）。
        - 只应用 SCHEMA 内且类型/范围校验通过的键；非法值跳过并记日志，
          不阻止 Bot 启动（无效持久化配置不把 Bot 带入危险状态）。
        - 敏感项（secret）按用户保存时的原值应用；显示层仍走 _mask 脱敏。
        返回成功应用的键数。
        """
        applied = 0
        env_values: Dict[str, str] = {}
        env_mtime = 0.0
        if self.env_store is not None:
            try:
                env_values = self.env_store.read_values()
                env_mtime = os.path.getmtime(self.env_store._path)
            except OSError:
                env_mtime = 0.0
        for key, value in self.repository.list_configs():
            if key not in self.SCHEMA:
                logger.warning("config_persisted_unknown key=%s（跳过）", key)
                continue
            # .env 较新优先：本地手工修改的 .env 值覆盖 db 旧值（并同步 db）
            if key in env_values and env_mtime > 0:
                meta = self.repository.get_config_meta(key)
                db_ts = meta[1] if meta else 0.0
                if env_mtime > db_ts:
                    value = env_values[key]
                    try:
                        self.repository.set_config(key, str(value))
                    except Exception:  # noqa: BLE001 - 同步失败不影响启动
                        pass
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
    def register_user(self, username: str, password: str) -> Tuple[bool, str]:
        """注册 Web UI **第一个**管理员账号（持久化到 settings.db，优先级高于 .env）。

        **Bootstrap Lock（安全设计）**：仅允许在 UNINITIALIZED 状态（系统从未初始化
        管理员账号）时通过公开注册入口创建第一个管理员；一旦初始化完成，公开注册
        一律 403（登录后通过 /panel/account/credentials 修改账号）。
        原子 compare-and-set（admin_bootstrap 表）保证并发注册只有一个成功（防 race）。
        之后登录优先使用这里保存的账号；.env 的 WEB_UI_USERNAME / WEB_UI_PASSWORD
        仅作未注册时的兜底。安全：密码只存 scrypt 哈希，**绝不写明文**，也不写入任何日志。
        """
        if self.admin_initialized():
            return False, "系统已完成初始化，公开注册已关闭（请用现有账号登录后在「用户状态」页修改）"
        # **先校验输入再 CAS**：非法输入绝不能把 bootstrap 状态锁死（否则系统将无法注册任何账号）
        username = (username or "").strip()
        if not (3 <= len(username) <= 32):
            return False, "用户名长度需 3~32 字符"
        if len(password or "") < 6:
            return False, "密码至少 6 位"
        # 原子 CAS：只有一个请求能把 uninitialized→initialized（并发注册仅一个成功）
        if not self.repository.try_mark_bootstrap_initialized():
            return False, "系统正在初始化中（并发注册冲突），请稍后再试"
        password_hash = hash_password(password)
        try:
            self.repository.set_config("WEB_UI_USERNAME", username)
            self.repository.set_config("WEB_UI_PASSWORD", password_hash)
        except Exception:  # noqa: BLE001 - 写库失败：回滚 bootstrap 状态，避免系统锁死
            try:
                self.repository.mark_bootstrap_uninitialized()
            except Exception:  # noqa: BLE001
                pass
            raise
        try:
            setattr(self.config, "WEB_UI_USERNAME", username)
            setattr(self.config, "WEB_UI_PASSWORD", password_hash)
        except Exception:  # noqa: BLE001
            pass
        logger.info("web_ui account registered user=%s", username, extra={"event": "config_reload"})
        return True, "注册成功，请用新账号登录"

    def admin_initialized(self) -> bool:
        """系统是否已完成管理员初始化（Bootstrap Lock 状态判定）。

        判定来源（任一命中即视为已初始化，兼容历史数据迁移——旧版本已存在
        注册/明文密码时自动视为 INITIALIZED，绝不因升级而开放注册）：
        1. settings.db 已保存管理凭据（WEB_UI_PASSWORD / WEB_UI_USERNAME）
        2. .env / 运行配置中的 WEB_UI_PASSWORD 非空
        """
        if self.repository.get_config("WEB_UI_PASSWORD") is not None:
            return True
        if self.repository.get_config("WEB_UI_USERNAME") is not None:
            return True
        if self.env_store is not None:
            try:
                env_values = self.env_store.read_values()
                if str(env_values.get("WEB_UI_PASSWORD", "") or "").strip():
                    return True
                if str(env_values.get("WEB_UI_USERNAME", "") or "").strip():
                    return True
            except OSError:
                pass
        if str(getattr(self.config, "WEB_UI_PASSWORD", "") or "").strip():
            return True
        return False

    def change_credentials(self, username: str, password: str,
                           current_password: str) -> Tuple[bool, str]:
        """登录态下修改管理员账号（调用方必须已通过认证）。

        独立于公开注册：只替换凭据，不改变 Bootstrap Lock 状态（系统保持 INITIALIZED）。
        需要当前密码二次验证（防会话劫持后直接改密）。
        """
        eff_user, eff_pass = self._effective_credentials_safe()
        if not verify_password(current_password, eff_pass):
            return False, "当前密码不正确"
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
        logger.info("web_ui account changed user=%s", username, extra={"event": "config_reload"})
        return True, "账号已更新，下次登录请使用新账号"

    def _effective_credentials_safe(self) -> Tuple[str, str]:
        repo_user = self.repository.get_config("WEB_UI_USERNAME")
        repo_pass = self.repository.get_config("WEB_UI_PASSWORD")
        user = repo_user if repo_user is not None else str(getattr(self.config, "WEB_UI_USERNAME", "admin"))
        pwd = repo_pass if repo_pass is not None else str(getattr(self.config, "WEB_UI_PASSWORD", "") or "")
        return user, pwd

    def unregister_account(self) -> Tuple[bool, str]:
        """注销管理员账号：只清除管理凭据（settings.db + .env 的
        WEB_UI_USERNAME / WEB_UI_PASSWORD），**其他环境配置一律不动**。

        调用方必须已经校验当前密码（防误触/防劫持）。注销后回到未注册状态：
        登录回退到 .env 的 WEB_UI_USERNAME / WEB_UI_PASSWORD（若 .env 原配置
        已被清除则需重新注册或配置）。
        """
        removed_db = 0
        for key in ("WEB_UI_USERNAME", "WEB_UI_PASSWORD"):
            if self.repository.delete_config(key):
                removed_db += 1
        if self.env_store is not None:
            try:
                self.env_store.delete(["WEB_UI_USERNAME", "WEB_UI_PASSWORD"])
            except Exception:  # noqa: BLE001 - .env 删除失败不阻断（db 已清）
                logger.warning("env unregister 删除失败（db 凭据已清除）")
        try:
            setattr(self.config, "WEB_UI_USERNAME", "admin")
            setattr(self.config, "WEB_UI_PASSWORD", "")
        except Exception:  # noqa: BLE001
            pass
        # Bootstrap Lock：显式回退到 UNINITIALIZED（这是管理员在认证上下文内的
        # 明确操作——注销=重置；此后系统允许重新执行首次注册）
        self.repository.mark_bootstrap_uninitialized()
        logger.info("web_ui account unregistered", extra={"event": "config_reload"})
        note = "；注意：若 WEB_UI_ENABLED=true，需在 .env 重新配置 WEB_UI_PASSWORD 或重新注册后才能登录"
        if removed_db:
            return True, "管理员账号已注销（仅清除账号与密码，其他配置未动）" + note
        return True, "管理员账号已注销（settings.db 中无持久化凭据，已清除 .env 相关项）" + note

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
        pair_err = self._pair_error({key: value}, {key: ctype})
        if pair_err:
            return False, pair_err
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
        pair_err = self._pair_error(validated, ctypes)
        if pair_err:
            return False, "未保存：" + pair_err
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

    # 成对配置的交叉校验（min <= max 语义）：键对 (下限键, 上限键)
    _ORDERED_PAIRS = (
        ("PROACTIVE_MESSAGE_MIN_PROBABILITY", "PROACTIVE_MESSAGE_MAX_PROBABILITY"),
        ("ACTIVE_CHAT_INTERVAL_MIN_SECONDS", "ACTIVE_CHAT_INTERVAL_MAX_SECONDS"),
    )

    def _pair_current_value(self, key: str, validated: Dict[str, str]) -> float:
        """成对校验时取某一侧的当前生效值（本次提交值 > 持久化值 > Settings 默认）。"""
        if key in validated:
            return float(validated[key])
        override = self.repository.get_config(key)
        if override is not None:
            try:
                return float(override)
            except (TypeError, ValueError):
                pass
        return float(getattr(self.config, key, 0.0) or 0.0)

    def _pair_error(self, validated: Dict[str, str], ctypes: Dict[str, str]) -> Optional[str]:
        """成对配置交叉校验（如 min <= max）；返回错误文案或 None。"""
        for lo_key, hi_key in self._ORDERED_PAIRS:
            if lo_key not in validated and hi_key not in validated:
                continue
            try:
                lo = self._pair_current_value(lo_key, validated)
                hi = self._pair_current_value(hi_key, validated)
            except (TypeError, ValueError):
                return f"{lo_key} / {hi_key} 值非法"
            if lo > hi:
                return f"{lo_key} 不能大于 {hi_key}（当前 {lo} > {hi}）"
        return None

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
                try:
                    v = float(raw)
                except ValueError:
                    return None
                if not math.isfinite(v):  # NaN / Infinity 一律拒绝
                    return None
                lo, hi = self._RANGES.get(key, (None, None))
                if lo is not None and v < lo:
                    return None
                if hi is not None and v > hi:
                    return None
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
