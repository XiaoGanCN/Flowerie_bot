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
    SCHEMA: Dict[str, Tuple[str, str, bool, bool, str]] = {
        # ---------- AI / Provider ----------
        "DEEPSEEK_API_KEY": ("AI", "secret", True, True, "DeepSeek API 密钥（必填）"),
        "DEEPSEEK_API_URL": ("AI", "str", False, True, "DeepSeek API 地址"),
        "DEEPSEEK_MODEL": ("AI", "str", False, True, "群聊对话模型"),
        "TOXIC_API_KEY": ("AI", "secret", True, True, "引战检测 AI 密钥（留空回退 DeepSeek）"),
        "TOXIC_API_URL": ("AI", "str", False, True, "引战检测 AI 地址（留空回退 DeepSeek）"),
        "TOXIC_MODEL": ("AI", "str", False, True, "引战检测模型（留空回退 DeepSeek）"),
        "VISION_API_KEY": ("AI", "secret", True, True, "视觉识图密钥（留空回退 DeepSeek）"),
        "VISION_API_URL": ("AI", "str", False, True, "视觉识图 API 地址（留空回退 DeepSeek）"),
        "VISION_MODEL": ("AI", "str", False, True, "视觉识图模型"),
        "VISION_TIMEOUT": ("AI", "int", False, True, "识图超时（秒）"),
        "VISION_FORWARD_IMAGES": ("AI", "bool", False, True, "识别合并转发里的图片（省视觉 token，默认关）"),
        # ---------- 基础配置 ----------
        "BOT_QQ": ("Bot", "int", False, False, "机器人 QQ 号（需重启）"),
        "BOT_NICKNAME": ("Bot", "str", False, True, "机器人昵称"),
        "ONLY_REPLY_WHEN_AT": ("Bot", "bool", False, True, "仅回复 @ 消息（哑巴模式）"),
        "MAX_REPLY_LENGTH": ("Bot", "int", False, True, "最大回复长度"),
        "MAX_CONSECUTIVE_REPLIES": ("Bot", "int", False, True, "连续回复上限"),
        "MAX_CUSTOM_PROMPT_LENGTH": ("Bot", "int", False, False, "自定义 Prompt 最大长度（需重启）"),
        # ---------- QQ / OneBot 连接 ----------
        "WS_HOST": ("Connection", "str", False, False, "反向 WS 监听地址（需重启）"),
        "WS_PORT": ("Connection", "int", False, False, "反向 WS 端口（需重启）"),
        "HTTP_API_BASE": ("Connection", "str", False, False, "NapCat HTTP API 地址（需重启）"),
        "WS_TOKEN": ("Connection", "secret", True, False, "反向 WS 鉴权 token（留空=不鉴权，需重启）"),
        # ---------- 行为与回复 ----------
        "CONTEXT_SIZE": ("Behavior", "int", False, True, "上下文条数"),
        "USER_COOLDOWN": ("Behavior", "int", False, True, "用户冷却（秒）"),
        "BOT_COOLDOWN": ("Behavior", "int", False, True, "机器人冷却（秒）"),
        # ---------- 稳定性与熔断 ----------
        "EVENT_PROCESS_TIMEOUT": ("Stability", "int", False, True, "单条消息处理超时（秒）"),
        "MAX_CONCURRENT_AI": ("Stability", "int", False, False, "并发 AI 上限（需重启）"),
        "AI_MAX_RETRIES": ("Stability", "int", False, True, "AI 最大重试次数"),
        "AI_CIRCUIT_BREAKER_FAILURES": ("Stability", "int", False, True, "AI 熔断失败阈值"),
        "AI_CIRCUIT_BREAKER_PAUSE_SECONDS": ("Stability", "int", False, True, "AI 熔断冷却（秒）"),
        "GROUP_CIRCUIT_BREAKER_FAILURES": ("Stability", "int", False, True, "群级熔断失败阈值"),
        "GROUP_CIRCUIT_BREAKER_PAUSE_SECONDS": ("Stability", "int", False, True, "群级熔断冷却（秒）"),
        "GROUP_CIRCUIT_BREAKER_MAX_GROUPS": ("Stability", "int", False, False, "群级熔断器容量上限（需重启）"),
        "GROUP_CIRCUIT_BREAKER_TTL_SECONDS": ("Stability", "int", False, True, "群级熔断器空闲 TTL（秒）"),
        "CONTEXT_BACKUP_PATH": ("Stability", "str", False, False, "上下文备份库路径（需重启）"),
        "CONTEXT_BACKUP_INTERVAL": ("Stability", "int", False, True, "上下文备份间隔（秒）"),
        # ---------- 记忆库 ----------
        "MEMORY_PATH": ("Memory", "str", False, False, "记忆库路径（需重启）"),
        "MEMORY_TTL_DAYS": ("Memory", "int", False, True, "用户原话记忆保留天数（0=永久）"),
        "MODEL_MEMORY_TTL_DAYS": ("Memory", "int", False, True, "AI 推断记忆保留天数（0=跟随）"),
        "AUDIT_LOG_PATH": ("Memory", "str", False, False, "审计日志路径（需重启）"),
        "MEMORY_DISABLED_GROUPS": ("Memory", "list-int", False, True, "禁用记忆的群号（逗号分隔）"),
        # ---------- 表情包 ----------
        "STICKER_ENABLED": ("Sticker", "bool", False, True, "表情包功能开关"),
        "STICKER_DIR": ("Sticker", "str", False, False, "表情包目录（需重启；空=禁用）"),
        "STICKER_DB_PATH": ("Sticker", "str", False, False, "表情包索引库路径（需重启）"),
        "STICKER_COOLDOWN": ("Sticker", "int", False, True, "表情包冷却（秒）"),
        "STICKER_MAX_LIST": ("Sticker", "int", False, True, "提供给模型的表情包描述上限"),
        # ---------- MCP 工具 ----------
        "MCP_ENABLED": ("MCP", "bool", False, True, "MCP 工具开关"),
        "MCP_SERVER_URL": ("MCP", "str", False, True, "MCP server 地址（单 server）"),
        "MCP_SERVER_NAME": ("MCP", "str", False, False, "MCP server 名称（需重启）"),
        "MCP_SERVERS": ("MCP", "json", False, False, "MCP 多 server JSON 数组（需重启）"),
        "MCP_TIMEOUT": ("MCP", "int", False, True, "工具调用超时（秒）"),
        "MCP_MAX_TOOL_CALLS": ("MCP", "int", False, True, "单轮工具调用上限"),
        "MCP_ALLOWED_TOOLS": ("MCP", "str", False, True, "工具 allowlist（逗号分隔）"),
        "MCP_ALLOWED_HOSTS": ("MCP", "list-str", False, False, "MCP 本地/内网主机白名单（需重启）"),
        "MCP_CIRCUIT_FAILURES": ("MCP", "int", False, True, "MCP 熔断失败阈值"),
        "MCP_CIRCUIT_PAUSE_SECONDS": ("MCP", "int", False, True, "MCP 熔断冷却（秒）"),
        # ---------- Web UI ----------
        "WEB_UI_ENABLED": ("WebUI", "bool", False, False, "Web UI 开关（需重启）"),
        "WEB_UI_HOST": ("WebUI", "str", False, False, "Web UI 监听地址（需重启）"),
        "WEB_UI_ALLOW_LAN": ("WebUI", "bool", False, False, "绑定 0.0.0.0 局域网可访问（需重启）"),
        "WEB_UI_PORT": ("WebUI", "int", False, False, "Web UI 端口（需重启）"),
        "WEB_UI_TOKEN_TTL_SECONDS": ("WebUI", "int", False, True, "登录 token 有效期（秒）"),
        # ---------- 日志 ----------
        "LOG_LEVEL": ("Logging", "str", False, False, "日志级别（需重启）"),
        "LOG_FORMAT": ("Logging", "str", False, False, "日志格式 text/json（需重启）"),
        # ---------- 预算与限额 ----------
        "DAILY_AI_CALL_BUDGET": ("Budget", "int", False, True, "全局每日 AI 调用上限（0=不限）"),
        "GROUP_DAILY_AI_CALL_BUDGET": ("Budget", "int", False, True, "每群每日 AI 调用上限（0=不限）"),
        "USER_AI_CALL_MIN_INTERVAL": ("Budget", "int", False, True, "同一用户 AI 调用最小间隔（秒）"),
        "BUDGET_EXHAUSTED_NOTICE": ("Budget", "bool", False, True, "额度用尽时在群里说一句提示"),
        # ---------- 主动聊天 ----------
        "NIGHT_SILENCE_START": ("ActiveChat", "int", False, True, "夜间静默开始（小时 0-23）"),
        "NIGHT_SILENCE_END": ("ActiveChat", "int", False, True, "夜间静默结束（小时 1-24）"),
        "ACTIVE_CHAT_COOLDOWN": ("ActiveChat", "int", False, True, "主动聊天冷却（秒）"),
        "BOT_CONSECUTIVE_REPLY_COOLDOWN": ("ActiveChat", "int", False, True, "连续回复后冷却（秒）"),
        # ---------- 复读与防刷 ----------
        "REPEAT_WINDOW": ("Repeat", "int", False, True, "复读检测窗口（秒）"),
        "REPEAT_THRESHOLD": ("Repeat", "int", False, True, "复读触发次数"),
        "TOXIC_WARNING_COOLDOWN": ("Repeat", "int", False, True, "引战警告冷却（秒）"),
        # ---------- 戳戳 ----------
        "POKE_REPLY_ENABLED": ("Poke", "bool", False, True, "戳戳回复开关"),
        "POKE_REPLIES": ("Poke", "textarea", False, True, "戳戳回复语（每行一条）"),
        # ---------- 文件解析 ----------
        "MAX_FILE_TEXT_CHARS": ("FileParse", "int", False, True, "文件解析提取文本上限（字符）"),
        "MAX_FILE_DOWNLOAD_BYTES": ("FileParse", "int", False, True, "文件下载解码字节上限"),
        "MAX_PDF_PAGES": ("FileParse", "int", False, True, "PDF 最多解析页数"),
        "MAX_EXCEL_CELLS": ("FileParse", "int", False, True, "Excel 最多解析单元格数"),
        "MAX_CSV_ROWS": ("FileParse", "int", False, True, "CSV 最多解析行数"),
        "MAX_IMAGES_PER_MESSAGE": ("FileParse", "int", False, True, "单条消息最多识图张数"),
        "MAX_FORWARD_DEPTH": ("FileParse", "int", False, True, "转发最大展开深度"),
        "MAX_FORWARD_MESSAGES": ("FileParse", "int", False, True, "转发展开消息总数上限"),
        "MAX_FORWARD_NODES": ("FileParse", "int", False, True, "转发遍历节点总数上限"),
        "MAX_FORWARD_FETCHES": ("FileParse", "int", False, True, "单条消息转发拉取次数上限"),
        # ---------- 安全与资源限制 ----------
        "MAX_AI_INPUT_CHARS": ("Security", "int", False, True, "单次 AI 输入最大字符数"),
        "MAX_IMAGE_DOWNLOAD_BYTES": ("Security", "int", False, True, "单张图片下载上限（字节）"),
        "IMAGE_DOWNLOAD_MAX_REDIRECTS": ("Security", "int", False, True, "图片下载最大重定向次数"),
        "IMAGE_ALLOWED_HOSTS": ("Security", "list-str", False, True, "图片主机白名单（逗号分隔，空=放行所有）"),
        # ---------- 白名单与隐私 ----------
        "ALLOWED_GROUP_IDS": ("Whitelist", "list-int", False, True, "允许群号白名单（逗号分隔，空=所有群）"),
        "TOXIC_GROUP_IDS": ("Whitelist", "list-int", False, True, "引战检测群号（逗号分隔，空=不检测）"),
        "ADMIN_QQ_IDS": ("Whitelist", "list-int", False, True, "管理员 QQ（逗号分隔）"),
        # ---------- 消息存档 ----------
        "ARCHIVE_ENABLED": ("Archive", "bool", False, True, "消息存档开关（默认关）"),
        "ARCHIVE_BASE_DIR": ("Archive", "str", False, False, "存档目录（需重启）"),
        "ARCHIVE_RETENTION_DAYS": ("Archive", "int", False, True, "存档保留天数（0=永久）"),
        "ARCHIVE_MAX_SIZE_MB": ("Archive", "int", False, True, "每群存档大小上限 MB（0=不限）"),
        # ---------- 数据路径 ----------
        "SETTINGS_DB_PATH": ("Paths", "str", False, False, "设置库路径（需重启，谨慎修改）"),
        # ---------- Persona（人格系统） ----------
        "PERSONA_DEFAULT": ("Persona", "str", False, False, "默认人格 id（兜底，需重启）"),
        "MAX_PERSONA_PROMPT_LENGTH": ("Persona", "int", False, False, "人格 system_prompt 最大长度（需重启）"),
        "PERSONA_MAX_COUNT": ("Persona", "int", False, True, "自定义人格总数上限（内置不计）"),
        # ---------- 群聊知识（Meme Knowledge） ----------
        "MEME_LEARNING_ENABLED": ("Knowledge", "bool", False, True, "每日梗总结任务开关"),
        "MEME_KNOWLEDGE_DB_PATH": ("Knowledge", "str", False, False, "梗知识库路径（需重启）"),
        "MEME_SUMMARY_INTERVAL_HOURS": ("Knowledge", "int", False, True, "梗总结周期（小时）"),
        "MAX_GROUP_MEMES": ("Knowledge", "int", False, True, "每群梗知识条数上限"),
        "MEME_BUFFER_PER_GROUP": ("Knowledge", "int", False, False, "每群消息缓冲上限（需重启）"),
        "MEME_MAX_GROUPS_PER_RUN": ("Knowledge", "int", False, True, "单轮总结最多处理群数"),
        "MEME_MIN_MESSAGES_PER_SUMMARY": ("Knowledge", "int", False, True, "总结最少消息数"),
        "MEME_MAX_SUMMARY_CANDIDATES": ("Knowledge", "int", False, True, "单群单轮候选梗上限"),
    }

    # 分类显示名与展示顺序（表单分组用）
    CATEGORY_LABELS: Dict[str, str] = {
        "AI": "AI / Provider 配置",
        "Bot": "基础配置",
        "Connection": "QQ / OneBot 连接",
        "Behavior": "行为与回复",
        "Stability": "稳定性与熔断",
        "Memory": "记忆库",
        "Sticker": "表情包",
        "MCP": "MCP 工具",
        "WebUI": "Web UI",
        "Logging": "日志",
        "Budget": "预算与限额",
        "ActiveChat": "主动聊天",
        "Repeat": "复读与防刷",
        "Poke": "戳戳",
        "FileParse": "文件解析",
        "Security": "安全与资源限制",
        "Whitelist": "白名单与隐私",
        "Archive": "消息存档",
        "Paths": "数据路径",
        "Persona": "人格（Persona）",
        "Knowledge": "群聊知识（Meme）",
    }
    CATEGORY_ORDER: List[str] = [
        "AI", "Bot", "Connection", "Behavior", "Stability", "Memory", "Context",
        "Sticker", "MCP", "WebUI", "Logging", "Budget", "ActiveChat", "Repeat",
        "Poke", "FileParse", "Security", "Whitelist", "Archive", "Paths",
        "Persona", "Knowledge",
    ]

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
    _ENUM_VALUES = {
        "LOG_LEVEL": {"debug", "info", "warning", "error", "critical"},
        "LOG_FORMAT": {"text", "json"},
    }
    # 枚举选项的显示/提交大小写（LOG_LEVEL 保持大写，loguru 对大小写敏感时也兼容）
    _ENUM_OPTIONS = {
        "LOG_LEVEL": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        "LOG_FORMAT": ["text", "json"],
    }
    _RANGES = {
        "BOT_QQ": (1, 999999999999),
        "MAX_REPLY_LENGTH": (1, 1000),
        "MAX_CONSECUTIVE_REPLIES": (0, 100),
        "MAX_CUSTOM_PROMPT_LENGTH": (100, 100000),
        "CONTEXT_SIZE": (1, 2000),
        "USER_COOLDOWN": (0, 86400),
        "BOT_COOLDOWN": (0, 86400),
        "EVENT_PROCESS_TIMEOUT": (1, 3600),
        "MAX_CONCURRENT_AI": (1, 100),
        "AI_MAX_RETRIES": (0, 20),
        "AI_CIRCUIT_BREAKER_FAILURES": (1, 1000),
        "AI_CIRCUIT_BREAKER_PAUSE_SECONDS": (1, 86400),
        "GROUP_CIRCUIT_BREAKER_FAILURES": (1, 1000),
        "GROUP_CIRCUIT_BREAKER_PAUSE_SECONDS": (1, 86400),
        "GROUP_CIRCUIT_BREAKER_MAX_GROUPS": (1, 1000000),
        "GROUP_CIRCUIT_BREAKER_TTL_SECONDS": (1, 31536000),
        "CONTEXT_BACKUP_INTERVAL": (5, 86400),
        "MEMORY_TTL_DAYS": (0, 36500),
        "MODEL_MEMORY_TTL_DAYS": (0, 36500),
        "STICKER_COOLDOWN": (0, 86400),
        "STICKER_MAX_LIST": (1, 200),
        "MCP_TIMEOUT": (1, 3600),
        "MCP_MAX_TOOL_CALLS": (0, 1000),
        "MCP_CIRCUIT_FAILURES": (1, 1000),
        "MCP_CIRCUIT_PAUSE_SECONDS": (1, 86400),
        "WEB_UI_PORT": (1, 65535),
        "WEB_UI_TOKEN_TTL_SECONDS": (60, 604800),
        "VISION_TIMEOUT": (1, 600),
        "DAILY_AI_CALL_BUDGET": (0, 1000000000),
        "GROUP_DAILY_AI_CALL_BUDGET": (0, 1000000000),
        "USER_AI_CALL_MIN_INTERVAL": (0, 86400),
        "NIGHT_SILENCE_START": (0, 23),
        "NIGHT_SILENCE_END": (1, 24),
        "ACTIVE_CHAT_COOLDOWN": (0, 86400),
        "BOT_CONSECUTIVE_REPLY_COOLDOWN": (0, 86400),
        "REPEAT_WINDOW": (1, 86400),
        "REPEAT_THRESHOLD": (2, 100),
        "TOXIC_WARNING_COOLDOWN": (0, 86400),
        "MAX_FILE_TEXT_CHARS": (100, 1000000),
        "MAX_FILE_DOWNLOAD_BYTES": (1024, 104857600),
        "MAX_PDF_PAGES": (1, 10000),
        "MAX_EXCEL_CELLS": (1, 10000000),
        "MAX_CSV_ROWS": (1, 1000000),
        "MAX_IMAGES_PER_MESSAGE": (1, 100),
        "MAX_FORWARD_DEPTH": (1, 100),
        "MAX_FORWARD_MESSAGES": (1, 10000),
        "MAX_FORWARD_NODES": (1, 100000),
        "MAX_FORWARD_FETCHES": (1, 1000),
        "MAX_AI_INPUT_CHARS": (100, 1000000),
        "MAX_IMAGE_DOWNLOAD_BYTES": (1024, 104857600),
        "IMAGE_DOWNLOAD_MAX_REDIRECTS": (0, 20),
        "ARCHIVE_RETENTION_DAYS": (0, 36500),
        "ARCHIVE_MAX_SIZE_MB": (0, 1000000),
        "WS_PORT": (1, 65535),
        "MAX_PERSONA_PROMPT_LENGTH": (500, 100000),
        "PERSONA_MAX_COUNT": (1, 100000),
        "MEME_SUMMARY_INTERVAL_HOURS": (1, 8760),
        "MAX_GROUP_MEMES": (10, 100000),
        "MEME_BUFFER_PER_GROUP": (50, 100000),
        "MEME_MAX_GROUPS_PER_RUN": (1, 10000),
        "MEME_MIN_MESSAGES_PER_SUMMARY": (1, 10000),
        "MEME_MAX_SUMMARY_CANDIDATES": (1, 200),
    }

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
