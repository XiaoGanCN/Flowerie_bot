"""ConfigService：Web UI 可编辑配置的业务层。

- 单一 schema 来源：可管理配置项在此定义（name/类型/默认值/分类/是否敏感/
  是否热更新/校验）
- 持久化：SQLite app_config 表（SettingsRepository）；未覆盖项回退到
  .env/代码默认（当前 Settings 实例的值）
- 优先级：Persistent Config > Environment > Code Default
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
from typing import Any, Dict, List, Optional, Tuple

from src.config import Settings
from src.repositories.settings_repository import SettingsRepository
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
    """管理 Web UI 可编辑的配置子集（不机械搬运全部 .env 变量）。"""

    # key -> (分类, 类型, 是否敏感, 是否热更新, 说明)
    SCHEMA: Dict[str, Tuple[str, str, bool, bool, str]] = {
        # AI
        "DEEPSEEK_MODEL": ("AI", "str", False, True, "群聊模型"),
        "DEEPSEEK_API_KEY": ("AI", "secret", True, True, "DeepSeek API 密钥"),
        "DEEPSEEK_API_URL": ("AI", "str", False, True, "API 地址"),
        "VISION_MODEL": ("AI", "str", False, True, "识图视觉模型"),
        "MAX_REPLY_LENGTH": ("Bot", "int", False, True, "最大回复长度"),
        # Bot
        "BOT_NICKNAME": ("Bot", "str", False, True, "机器人昵称"),
        "USER_COOLDOWN": ("Bot", "int", False, True, "用户冷却（秒）"),
        "BOT_COOLDOWN": ("Bot", "int", False, True, "机器人冷却（秒）"),
        "MAX_CONSECUTIVE_REPLIES": ("Bot", "int", False, True, "连续回复上限"),
        "ONLY_REPLY_WHEN_AT": ("Bot", "bool", False, True, "仅回复@消息（哑巴模式）"),
        # Policy
        "DAILY_AI_CALL_BUDGET": ("Policy", "int", False, True, "全局每日 AI 调用上限"),
        "GROUP_DAILY_AI_CALL_BUDGET": ("Policy", "int", False, True, "每群每日 AI 调用上限"),
        "USER_AI_CALL_MIN_INTERVAL": ("Policy", "int", False, True, "同一用户 AI 调用最小间隔（秒）"),
        "AI_MAX_RETRIES": ("Policy", "int", False, True, "AI 重试次数"),
        "AI_CIRCUIT_BREAKER_FAILURES": ("Policy", "int", False, True, "AI 熔断失败阈值"),
        "AI_CIRCUIT_BREAKER_PAUSE_SECONDS": ("Policy", "int", False, True, "AI 熔断冷却（秒）"),
        # Memory
        "MEMORY_TTL_DAYS": ("Memory", "int", False, True, "记忆保留天数（0=永久）"),
        "MODEL_MEMORY_TTL_DAYS": ("Memory", "int", False, True, "AI 推断记忆保留天数"),
        # Sticker
        "STICKER_ENABLED": ("Sticker", "bool", False, True, "表情包功能开关"),
        "STICKER_COOLDOWN": ("Sticker", "int", False, True, "表情包冷却（秒）"),
        # MCP
        "MCP_ENABLED": ("MCP", "bool", False, True, "MCP 工具开关"),
        "MCP_SERVER_URL": ("MCP", "str", False, True, "MCP server 地址（单 server）"),
        "MCP_SERVERS": ("MCP", "str", False, False, "MCP 服务列表 JSON（多 server 插件式，需重启）"),
        "MCP_ALLOWED_HOSTS": ("MCP", "str", False, False, "MCP 本地/内网主机白名单（逗号分隔，需重启）"),
        "MCP_TIMEOUT": ("MCP", "int", False, True, "工具调用超时（秒）"),
        "MCP_MAX_TOOL_CALLS": ("MCP", "int", False, True, "单轮工具调用上限"),
        "MCP_ALLOWED_TOOLS": ("MCP", "str", False, True, "工具 allowlist（逗号分隔）"),
        # Logging
        "LOG_LEVEL": ("Logging", "str", False, True, "日志级别"),
        "LOG_FORMAT": ("Logging", "str", False, True, "日志格式（text/json）"),
        # Advanced（需要重启）
        "WS_PORT": ("Advanced", "int", False, False, "反向 WS 端口（需重启）"),
        "HTTP_API_BASE": ("Advanced", "str", False, False, "NapCat HTTP API 地址（需重启）"),
        "MEMORY_PATH": ("Advanced", "str", False, False, "记忆库路径（需重启）"),
    }

    def __init__(self, config: Settings, repository: SettingsRepository):
        self.config = config
        self.repository = repository

    # ---------- 读取 ----------
    def list_configs(self) -> List[Dict[str, Any]]:
        """按分类返回全部可管理配置（含当前值/默认/脱敏）。"""
        overrides = dict(self.repository.list_configs())
        result = []
        for key, (category, ctype, is_secret, hot, desc) in self.SCHEMA.items():
            current = overrides.get(key) if key in overrides else getattr(self.config, key, None)
            result.append({
                "key": key,
                "category": category,
                "type": ctype,
                "description": desc,
                "secret": is_secret,
                "hot_reload": hot,
                "current": self._mask(key, current, is_secret),
                "set": key in overrides,
            })
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
                setattr(self.config, key, self._coerce(ctype, value))
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
        """更新配置。返回 (是否成功, 提示信息)。校验失败返回 (False, 原因)。"""
        if key not in self.SCHEMA:
            return False, "未知配置项"
        category, ctype, is_secret, hot, _ = self.SCHEMA[key]
        # 敏感项：空输入 = 不修改（保留旧值）
        if is_secret and (raw_value is None or not raw_value.strip()):
            return False, "未输入新值，保持原密钥"
        value = self._validate(key, ctype, raw_value)
        if value is None:
            return False, "配置值校验失败"
        # 持久化 + 热更新到运行中的 Settings 实例
        self.repository.set_config(key, value)
        try:
            setattr(self.config, key, self._coerce(ctype, value))
        except Exception:  # noqa: BLE001
            pass
        logger.info("config_updated key=%s hot=%s", key, hot, extra={"event": "config_updated"})
        if hot:
            return True, "已保存，立即生效"
        return True, "已保存，需要重启生效"

    # P3-4：轻量枚举/数值范围校验（复用 SCHEMA 类型，不引入复杂 schema 框架）
    _ENUM_VALUES = {
        "LOG_LEVEL": {"debug", "info", "warning", "error", "critical"},
        "LOG_FORMAT": {"text", "json"},
    }
    _RANGES = {
        "MAX_REPLY_LENGTH": (1, 1000),
        "USER_COOLDOWN": (0, 86400),
        "BOT_COOLDOWN": (0, 86400),
        "MAX_CONSECUTIVE_REPLIES": (0, 100),
        "AI_MAX_RETRIES": (0, 20),
        "AI_CIRCUIT_BREAKER_FAILURES": (1, 1000),
        "AI_CIRCUIT_BREAKER_PAUSE_SECONDS": (1, 86400),
        "DAILY_AI_CALL_BUDGET": (0, 1000000000),
        "GROUP_DAILY_AI_CALL_BUDGET": (0, 1000000000),
        "USER_AI_CALL_MIN_INTERVAL": (0, 86400),
        "MEMORY_TTL_DAYS": (0, 36500),
        "MODEL_MEMORY_TTL_DAYS": (0, 36500),
        "STICKER_COOLDOWN": (0, 86400),
        "MCP_TIMEOUT": (1, 3600),
        "MCP_MAX_TOOL_CALLS": (0, 1000),
        "WS_PORT": (1, 65535),
    }

    def _validate(self, key: str, ctype: str, raw: str) -> Optional[str]:
        raw = raw.strip()
        try:
            if ctype == "int":
                v = int(raw)
                if v < 0:
                    return None
                lo, hi = self._RANGES.get(key, (0, None))
                if v < lo:
                    return None
                if hi is not None and v > hi:
                    return None
            elif ctype == "bool":
                if raw.lower() not in ("true", "false", "1", "0"):
                    return None
            elif ctype == "secret":
                if len(raw) < 6:
                    return None
            elif ctype == "str":
                if key in self._ENUM_VALUES and raw.lower() not in self._ENUM_VALUES[key]:
                    return None
                # MCP_SERVERS：必须是合法 JSON 数组（元素级校验交给启动 validate_config）
                if key == "MCP_SERVERS" and raw:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        return None
                    if not isinstance(data, list):
                        return None
        except ValueError:
            return None
        return raw

    @staticmethod
    def _coerce(ctype: str, value: str):
        if ctype == "int":
            return int(value)
        if ctype == "bool":
            return value.lower() in ("true", "1")
        return value

    @staticmethod
    def _mask(key: str, value, is_secret: bool) -> str:
        if not is_secret or value is None:
            return "" if value is None else str(value)
        v = str(value)
        if len(v) <= 8:
            return "****"
        return v[:4] + "****" + v[-4:]
