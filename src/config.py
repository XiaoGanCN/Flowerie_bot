import json
import re
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.core.sanitizer import validate_mcp_server_url


class Settings(BaseSettings):
    # DeepSeek
    DEEPSEEK_API_KEY: str = Field(...)  # 环境变量名 = 字段名（pydantic-settings）
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/chat/completions"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"

    # 引战检测 AI（可选；留空则回退用 DeepSeek 的 key/网址/模型）
    TOXIC_API_KEY: Optional[str] = None
    TOXIC_API_URL: Optional[str] = None
    TOXIC_MODEL: Optional[str] = None

    # 视觉识图（花璃看图用；可选，留空回退用 DeepSeek key/网址）
    VISION_API_KEY: Optional[str] = None
    VISION_API_URL: Optional[str] = None
    VISION_MODEL: Optional[str] = None
    VISION_TIMEOUT: int = 30

    # 是否识别合并转发（聊天记录）里的图片（默认关闭，可省视觉 token；想开在 .env 里设 true）
    VISION_FORWARD_IMAGES: bool = False

    # 稳定性
    # 单条消息处理超时（秒）：超过视为卡死跳过，防止一条慢消息堵住整个群
    EVENT_PROCESS_TIMEOUT: int = 90
    # 同时处理消息的并发上限（AI/识图都会吃这个额度，防止突发消息打爆 API）
    MAX_CONCURRENT_AI: int = 3
    # 单次逻辑 AI 操作的最大重试次数（首次尝试 + 重试；每次尝试都单独过预算闸门）
    AI_MAX_RETRIES: int = 3
    # 自定义 Prompt（全局/群聊）最大长度
    MAX_CUSTOM_PROMPT_LENGTH: int = 2000
    # AI 熔断（防失败风暴打爆 API）：
    # - Provider 级（全局）：计可重试瞬时失败（超时/网络/429/5xx），4xx 不计
    # - 群级：该群逻辑请求连续失败即熔断该群，防止单群故障拖垮其他群
    AI_CIRCUIT_BREAKER_FAILURES: int = 10
    AI_CIRCUIT_BREAKER_PAUSE_SECONDS: int = 60
    GROUP_CIRCUIT_BREAKER_FAILURES: int = 5
    GROUP_CIRCUIT_BREAKER_PAUSE_SECONDS: int = 30
    GROUP_CIRCUIT_BREAKER_MAX_GROUPS: int = 1000   # 群级熔断器容量上限（超出淘汰最旧）
    GROUP_CIRCUIT_BREAKER_TTL_SECONDS: int = 604800  # 群级熔断器空闲 TTL（7 天）
    # 上下文崩溃持久化：周期备份最近 50 条上下文（SQLite），意外去世后重启自动恢复
    CONTEXT_BACKUP_PATH: str = "./data/context_backup.db"
    CONTEXT_BACKUP_INTERVAL: int = 60

    # Bot
    BOT_QQ: int = Field(...)  # 环境变量名 = 字段名
    BOT_NICKNAME: str = "花璃"

    # Connection
    WS_HOST: str = "127.0.0.1"
    WS_PORT: int = 3001
    HTTP_API_BASE: str = "http://127.0.0.1:3000"
    # 可选：反向 WS 鉴权 token（空=不鉴权，仅建议在 WS_HOST 绑 loopback 时留空；
    # 设置后 NapCat 握手需带 Authorization: Bearer <token> 或 ?access_token=<token>）
    WS_TOKEN: str = ""

    # Behavior
    ONLY_REPLY_WHEN_AT: bool = False
    USER_COOLDOWN: int = 5
    BOT_COOLDOWN: int = 2
    MAX_REPLY_LENGTH: int = 40
    MAX_CONSECUTIVE_REPLIES: int = 3
    CONTEXT_SIZE: int = 300
    LOG_LEVEL: str = "INFO"
    # 日志格式：text=开发环境人类可读；json=生产环境 JSON lines（含 trace_id/event 字段）
    LOG_FORMAT: str = "text"

    # Random active chat
    NIGHT_SILENCE_START: int = 0
    NIGHT_SILENCE_END: int = 8
    ACTIVE_CHAT_COOLDOWN: int = 180
    BOT_CONSECUTIVE_REPLY_COOLDOWN: int = 60

    # ===== 主动发言概率（全部可配置，默认值 = 原硬编码值，行为零变化）=====
    # 上下文随机回复概率（ContextManager.should_reply_by_context，1%~5% 那套逻辑）：
    #   base        基础概率（0.03）
    #   user_boost  最近 5 条中用户消息 >= 2 时的增量（+0.01）
    #   single_user 最近消息全部来自同一用户时改用低概率（0.02，防单用户刷屏）
    #   short_msg   最近一条消息 < 2 字符时改用低概率（0.02）
    #   empty_ctx   群尚无上下文时使用（0.02）
    #   bot_mult    最近 3 条中机器人 >= 2 条时乘以的衰减系数（0.3）
    #   min/max     最终概率钳制区间（0.01 ~ 0.05）
    PROACTIVE_MESSAGE_MIN_PROBABILITY: float = 0.01
    PROACTIVE_MESSAGE_MAX_PROBABILITY: float = 0.05
    PROACTIVE_MESSAGE_BASE_PROBABILITY: float = 0.03
    PROACTIVE_MESSAGE_USER_BOOST: float = 0.01
    PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY: float = 0.02
    PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY: float = 0.02
    PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY: float = 0.02
    PROACTIVE_MESSAGE_BOT_MULTIPLIER: float = 0.3
    # 主动聊天循环（ActiveChatManager / MessageRouter._active_chat_loop）：
    #   probability                       每次 should_active_chat 的触发概率（原硬编码 0.10）
    #   interval_min/max_seconds          轮询间隔（原 hardcode random.randint(5,10)）
    #   consecutive_cooldown_seconds      连续主动发言 >= 2 次后的冷却（原 1800 秒）
    ACTIVE_CHAT_PROBABILITY: float = 0.10
    ACTIVE_CHAT_INTERVAL_MIN_SECONDS: int = 5
    ACTIVE_CHAT_INTERVAL_MAX_SECONDS: int = 10
    ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS: int = 1800

    # Repeat
    REPEAT_WINDOW: int = 120
    REPEAT_THRESHOLD: int = 3

    # Toxic warning
    TOXIC_WARNING_COOLDOWN: int = 900

    # Poke
    POKE_REPLY_ENABLED: bool = True
    POKE_REPLIES: List[str] = [
        "戳人家干嘛...", "别戳了...！", "唔...别戳啦", "（缩脑袋）",
        "好痒...别闹", "好啦好啦 让你戳", "唔 真拿你没办法", "（哈气）",
        "干什么...！", "呀...！", "再戳我就不理你了哦", "（躲开）",
        "你手不累嘛", "戳一下就够了哦", "（揉揉被戳的地方）",
        "哼 再戳就生气了", "（缩头）", "你完蛋了 我记仇了",
        "戳回去！", "反弹！", "（反手戳你）", "你的手指不要啦？",
        "再戳就收费了喔", "（假装躲闪）", "（叹气）", "杂鱼 不准戳我",
        "（摊手）", "你是不是无聊了", "找我玩嘛", "（歪头）",
        "嘤？", "（茫然）", "？", "哈！！！",
    ]

    # File paths
    MEMORY_PATH: str = "./data/memory.db"       # 记忆库（SQLite；旧 memory.json 会自动迁移到同目录 .db）
    ARCHIVE_BASE_DIR: str = "./data/archive"
    ARCHIVE_ENABLED: bool = False          # 是否启用消息存档（默认关，隐私优先；开=true 才写入 archive/）
    ARCHIVE_RETENTION_DAYS: int = 0        # 存档保留天数（0=永久；>0 自动清理过期文件）
    ARCHIVE_MAX_SIZE_MB: int = 0           # 每群存档目录总大小上限 MB（0=不限；超出删最旧）
    AUDIT_LOG_PATH: str = "./data/audit.log"
    # 应用设置库（自定义 Prompt / Web UI 可编辑配置），SQLite
    SETTINGS_DB_PATH: str = "./data/settings.db"
    # 表情包（Sticker）：目录由环境变量指定，空=禁用；Vision 索引缓存于 SQLite
    STICKER_DIR: str = ""
    STICKER_DB_PATH: str = "./data/stickers.db"
    STICKER_ENABLED: bool = False
    STICKER_COOLDOWN: int = 60          # 同一群两次表情包的最小间隔（秒）
    STICKER_MAX_LIST: int = 30          # 提供给模型的可用表情包描述上限（防 token 膨胀）
    # MCP（外部工具）：默认关闭，管理员主动配置后启用；仅 allowlist 内的工具可调用。
    # 插件式多 server：MCP_SERVERS 为 JSON 数组（每个元素含 name/url/allowed_tools/
    # timeout/enabled），为空时回退到下方单 server 字段（向后兼容）。
    MCP_ENABLED: bool = False
    MCP_SERVER_URL: str = ""            # （单 server）MCP server 地址（HTTP/SSE）
    MCP_SERVER_NAME: str = "mcp"        # （单 server）MCP server 名称
    MCP_SERVERS: str = ""               # 多 server JSON：[{"name","url","allowed_tools"?,"timeout"?,"enabled"?}, ...]
    MCP_TIMEOUT: int = 15               # 单次工具调用超时（秒，多 server 未单独指定时用此值）
    MCP_MAX_TOOL_CALLS: int = 5         # 单轮对话工具调用次数上限（所有 server 合计）
    MCP_ALLOWED_TOOLS: str = ""         # 逗号分隔的工具 allowlist（空=放行所有工具；多 server 未单独指定时用此值）
    MCP_ALLOWED_HOSTS: Optional[List[str]] = None  # 显式放行的本地/内网主机白名单（逗号分隔；仅这些地址可绕过回环/私网拒绝）
    MCP_CIRCUIT_FAILURES: int = 5       # MCP 独立熔断：连续失败阈值（每个 server 各自独立）
    MCP_CIRCUIT_PAUSE_SECONDS: int = 60 # MCP 熔断冷却
    # Web UI（管理后台）：默认关闭；必须认证；端口与反向 WS 端口（WS_PORT）错开
    WEB_UI_ENABLED: bool = False
    WEB_UI_HOST: str = "127.0.0.1"
    # 显式开关：true 时强制绑定 0.0.0.0（局域网/公网可访问）。默认 false=仅本机，
    # 请勿把管理后台直接暴露公网；开启后启动日志会输出安全警告。
    WEB_UI_ALLOW_LAN: bool = False
    WEB_UI_PORT: int = 8080
    WEB_UI_USERNAME: str = "admin"
    WEB_UI_PASSWORD: str = ""
    WEB_UI_TOKEN_TTL_SECONDS: int = 3600  # 登录 token 有效期

    # Persona（人格系统）：人格资源存 settings.db（personas / group_persona /
    # persona_global 表），内置预设见 src/services/persona_presets.py
    PERSONA_DEFAULT: str = "flowerie"      # 默认（兜底）人格 id
    MAX_PERSONA_PROMPT_LENGTH: int = 8000  # 单个人格 system_prompt 最大长度（字）
    PERSONA_MAX_COUNT: int = 200           # 自定义人格总数上限（内置不计，防无限增长）
    # 管理员补充发言规则（每行一条；优先级：安全策略 > 人格 > 人格内置规则 > 本条；
    # 不得覆盖安全策略——运行时策略/清洗/记忆校验不会被任何 prompt 文本绕过）
    ADMIN_RESPONSE_RULES: List[str] = []

    # ===== 插件系统（Plugin System v1：受控插件运行时） =====
    # 插件目录：扫描其中的 */manifest.json 自动发现（发现 ≠ 自动执行，默认禁用）
    PLUGIN_DIR: str = "./plugins"
    # 插件保护级别：normal（推荐）/ relaxed / unsafe（仅可信插件，作者概不负责）
    # 任何级别都保留：manifest 校验 / 管理员权限 / 进程隔离 / 日志 / 崩溃保护 /
    # 资源限制 / 权限强制（PermissionManager 不被豁免）
    PLUGIN_PROTECTION: str = "normal"
    PLUGIN_MAX_COUNT: int = 100            # 注册表插件总数上限（防无限增长）
    PLUGIN_URL_MAX_BYTES: int = 5242880    # URL 下载插件包大小上限（5MB）
    PLUGIN_URL_TIMEOUT: int = 15           # URL 下载超时（秒）
    PLUGIN_ZIP_MAX_UNZIPPED_BYTES: int = 52428800  # 解压后总大小上限（50MB，防 Zip Bomb）
    PLUGIN_ZIP_MAX_FILES: int = 200        # 包内文件数上限

    # ===== NapCat WebSocket（正向 / 反向 二选一） =====
    # reverse：Flowerie 作为 WS 服务端（NapCat 连接过来，即原有行为）
    # forward：Flowerie 作为客户端连接 NapCat 的正向 WS 服务（NAPCAT_WS_URL）
    NAPCAT_WS_MODE: str = "reverse"
    NAPCAT_WS_URL: str = ""                # forward 模式必填（ws:// 或 wss://）
    NAPCAT_ACCESS_TOKEN: str = ""          # forward 鉴权 token（绝不清写入日志）

    # 群聊梗/黑话知识层（Meme Knowledge）：独立 knowledge.db，按群完全隔离
    MEME_LEARNING_ENABLED: bool = False            # 每日梗总结任务总开关（默认关）
    MEME_KNOWLEDGE_DB_PATH: str = "./data/knowledge.db"
    MEME_SUMMARY_INTERVAL_HOURS: int = 24          # 每日总结周期（小时）
    MAX_GROUP_MEMES: int = 500                     # 每群知识条数上限（防无限增长）
    MEME_BUFFER_PER_GROUP: int = 1000              # 每群消息缓冲上限（条）
    MEME_MAX_GROUPS_PER_RUN: int = 20              # 单轮总结最多处理的群数（防 AI 风暴）
    MEME_MIN_MESSAGES_PER_SUMMARY: int = 10        # 群消息少于该数不总结（宁缺毋滥）
    MEME_MAX_SUMMARY_CANDIDATES: int = 20          # 单群单轮最多写入的候选梗数

    # White list
    ALLOWED_GROUP_IDS: Optional[List[int]] = None
    TOXIC_GROUP_IDS: Optional[List[int]] = None
    # 记忆隐私：这些群里完全禁止写入记忆
    MEMORY_DISABLED_GROUPS: Optional[List[int]] = None
    # 管理员 QQ（可执行 /memory_clear /memory_dump）
    ADMIN_QQ_IDS: Optional[List[int]] = None

    # ===== 安全审计加固（P1/P2/P3）=====
    # 资源限制：防止超大文件/超长输入消耗 CPU/RAM/Token
    MAX_FILE_TEXT_CHARS: int = 8000        # 文件解析后提取文本的最大字符数
    MAX_FILE_DOWNLOAD_BYTES: int = 2097152  # 文件下载解码字节兜底上限（2MB，防 NapCat 返回超预期内容）
    MAX_PDF_PAGES: int = 100               # PDF 最多解析页数
    MAX_EXCEL_CELLS: int = 50000           # Excel 最多解析单元格数
    MAX_CSV_ROWS: int = 10000              # CSV 最多解析行数
    MAX_AI_INPUT_CHARS: int = 8000         # 单次 AI 输入（上下文+消息）最大字符数
    MAX_IMAGES_PER_MESSAGE: int = 10       # 单条消息最多识图张数（防图片轰炸）
    # 转发解析预算（防套娃转发 DoS）：深度/消息数/节点数/拉取次数四重上限
    MAX_FORWARD_DEPTH: int = 5             # 嵌套转发最大展开深度
    MAX_FORWARD_MESSAGES: int = 100        # 展开后的消息总数上限
    MAX_FORWARD_NODES: int = 500           # 递归遍历节点总数上限
    MAX_FORWARD_FETCHES: int = 20          # 单条消息最多 /get_forward_msg 拉取次数（含缓存去重）
    # 预算默认收紧（安全默认而非 0=不限）：个人 Bot 用量远低于此，公开群也不会被无限刷
    DAILY_AI_CALL_BUDGET: int = 1000       # 全局每日 AI 调用次数上限（0=不限；>0 时超出即闭嘴）
    GROUP_DAILY_AI_CALL_BUDGET: int = 300  # 每群每日 AI 调用次数上限（0=不限；防止一个群刷光全局额度）
    USER_AI_CALL_MIN_INTERVAL: int = 10    # 同一用户两次 AI 回复的最小间隔秒数（0=不限；per-user 限速）
    BUDGET_EXHAUSTED_NOTICE: bool = True   # 额度用尽时在群里说一句提示（每天每群一次）
    MAX_IMAGE_DOWNLOAD_BYTES: int = 10485760  # 单张图片下载大小上限（10MB）
    IMAGE_DOWNLOAD_MAX_REDIRECTS: int = 3  # 图片下载最大重定向次数
    # 可选图片主机白名单（逗号分隔；空=放行所有 http/https，设置后只放行白名单+NapCat 本地 loopback）
    IMAGE_ALLOWED_HOSTS: Optional[List[str]] = None
    # 数据治理
    MEMORY_TTL_DAYS: int = 0               # 用户原话记忆保留天数（0=永久保留）
    MODEL_MEMORY_TTL_DAYS: int = 30        # AI 推断记忆(model)保留天数（低信任，默认 30 天自动过期）

    @field_validator("ALLOWED_GROUP_IDS", "TOXIC_GROUP_IDS", "MEMORY_DISABLED_GROUPS", "ADMIN_QQ_IDS", mode="before")
    @classmethod
    def parse_list(cls, v):
        if isinstance(v, str):
            if v.strip() == "":
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        return v

    @field_validator("IMAGE_ALLOWED_HOSTS", "MCP_ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_str_list(cls, v):
        if isinstance(v, str):
            if v.strip() == "":
                return []
            return [x.strip().lower() for x in v.split(",") if x.strip()]
        return v

    @field_validator("LOG_FORMAT", mode="before")
    @classmethod
    def parse_log_format(cls, v):
        val = str(v or "text").lower()
        if val not in ("text", "json"):
            raise ValueError(f"LOG_FORMAT 必须是 'text' 或 'json'，当前: {v!r}")
        return val

    @field_validator("ADMIN_RESPONSE_RULES", mode="before")
    @classmethod
    def parse_response_rules(cls, v):
        """管理员补充发言规则：支持 JSON 数组（Web UI 保存格式）或按行/逗号分隔的纯文本。"""
        if isinstance(v, str):
            raw = v.strip()
            if raw.startswith("["):
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    return []
                if isinstance(data, list):
                    return [str(x).strip() for x in data if str(x).strip()]
            return [x.strip() for x in raw.replace("\\n", "\n").split("\n") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

def load_config() -> Settings:
    return Settings()


def _finite_prob(value, name: str) -> float:
    """把配置值规范化为 [0,1] 内的有限小数；非法（NaN/Inf/越界/非数字）抛 ValueError。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是数字（0.0~1.0），当前: {value!r}") from None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / ±Inf
        raise ValueError(f"{name} 不能是 NaN/Infinity，当前: {value!r}")
    if not (0.0 <= v <= 1.0):
        raise ValueError(f"{name} 必须在 0.0~1.0 之间，当前: {value!r}")
    return v


def _validate_active_chat_probs(config: Settings) -> Optional[str]:
    """主动发言概率合法性集中校验（min<=max、非负增量、衰减系数合法、间隔合理）。"""
    keys = ("PROACTIVE_MESSAGE_MIN_PROBABILITY", "PROACTIVE_MESSAGE_MAX_PROBABILITY",
            "PROACTIVE_MESSAGE_BASE_PROBABILITY", "PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY",
            "PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY", "PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY",
            "PROACTIVE_MESSAGE_BOT_MULTIPLIER", "ACTIVE_CHAT_PROBABILITY")
    values = {}
    for k in keys:
        try:
            values[k] = _finite_prob(getattr(config, k, 0.1), k)
        except ValueError as e:
            return str(e)
    try:
        values["PROACTIVE_MESSAGE_USER_BOOST"] = _finite_prob(
            getattr(config, "PROACTIVE_MESSAGE_USER_BOOST", 0.01), "PROACTIVE_MESSAGE_USER_BOOST")
    except ValueError as e:
        return str(e)
    if values["PROACTIVE_MESSAGE_MIN_PROBABILITY"] > values["PROACTIVE_MESSAGE_MAX_PROBABILITY"]:
        return (
            f"主动发言概率区间非法: MIN({values['PROACTIVE_MESSAGE_MIN_PROBABILITY']}) > "
            f"MAX({values['PROACTIVE_MESSAGE_MAX_PROBABILITY']})（要求 min <= max）")
    try:
        imin = int(getattr(config, "ACTIVE_CHAT_INTERVAL_MIN_SECONDS", 5))
        imax = int(getattr(config, "ACTIVE_CHAT_INTERVAL_MAX_SECONDS", 10))
    except (TypeError, ValueError):
        return "ACTIVE_CHAT_INTERVAL_MIN/MAX_SECONDS 必须是整数"
    if not (1 <= imin <= imax <= 3600):
        return f"主动聊天轮询间隔非法: {imin}~{imax}（要求 1 <= min <= max <= 3600 秒）"
    cd = int(getattr(config, "ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS", 1800))
    if not (0 <= cd <= 86400):
        return f"ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS 必须在 0~86400 之间（当前: {cd}）"
    return None


def parse_mcp_servers(raw: str, default_timeout: int = 15, default_tools: str = "",
                      default_name: str = "mcp", legacy_url: str = "",
                      legacy_tools: str = "") -> List[dict]:
    """解析 MCP_SERVERS（插件式多 server，JSON 数组）；为空时回退 legacy 单 server。

    每个元素：{"name": 必填且唯一, "url": 必填, "allowed_tools"?: 逗号分隔,
              "timeout"?: 秒, "enabled"?: bool}。allowed_tools/timeout 缺省用全局值。
    返回 server dict 列表（含 enabled 标记；禁用项由调用方跳过）。
    """
    servers: List[dict] = []
    raw = (raw or "").strip()
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("MCP_SERVERS 必须是合法 JSON 数组") from None
        if not isinstance(data, list):
            raise ValueError("MCP_SERVERS 必须是 JSON 数组")
        for item in data:
            if not isinstance(item, dict):
                raise ValueError(f"MCP_SERVERS 元素必须是对象: {item!r}")
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if not name or not url:
                raise ValueError(f"MCP_SERVERS 元素缺少 name/url: {item!r}")
            if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
                raise ValueError(f"MCP_SERVERS 元素 name 非法: {name!r}")
            timeout_raw = item.get("timeout", default_timeout)
            try:
                timeout = int(timeout_raw) if timeout_raw is not None else int(default_timeout)
            except (TypeError, ValueError):
                raise ValueError(f"MCP_SERVERS 元素 timeout 非法: {item!r}") from None
            tools = item.get("allowed_tools")
            servers.append({
                "name": name,
                "url": url,
                "allowed_tools": str(tools).strip() if tools is not None else str(default_tools).strip(),
                "timeout": timeout,
                "enabled": bool(item.get("enabled", True)),
            })
        if servers:
            return servers
    # legacy 单 server（MCP_SERVERS 为空时）
    url = (legacy_url or "").strip()
    if url:
        servers.append({
            "name": default_name or "mcp",
            "url": url,
            "allowed_tools": (legacy_tools or "").strip(),
            "timeout": int(default_timeout),
            "enabled": True,
        })
    return servers

def validate_config(config: Settings) -> None:
    """启动阶段配置校验：必填项缺失/取值非法时直接抛错（进程不启动）。

    - API Key 只检查"已配置"，不打印任何值（敏感保护）
    - 取值范围问题在此集中报错，避免运行期才炸
    """
    if not getattr(config, "DEEPSEEK_API_KEY", "") or getattr(config, "DEEPSEEK_API_KEY", "").startswith("sk-your"):
        raise ValueError("DEEPSEEK_API_KEY 未配置或仍为占位值（.env 中设置真实 Key 后再启动）")
    if getattr(config, "BOT_QQ", 0) <= 0:
        raise ValueError(f"BOT_QQ 必须为正整数，当前: {getattr(config, 'BOT_QQ', 0)}")
    if not (0 < getattr(config, "WS_PORT", 0) < 65536):
        raise ValueError(f"WS_PORT 必须在 1~65535 之间，当前: {getattr(config, 'WS_PORT', 0)}")
    if getattr(config, "MAX_CONCURRENT_AI", 1) < 1:
        raise ValueError(f"MAX_CONCURRENT_AI 必须 >= 1，当前: {getattr(config, 'MAX_CONCURRENT_AI', 1)}")
    if getattr(config, "AI_MAX_RETRIES", 0) < 0:
        raise ValueError(f"AI_MAX_RETRIES 必须 >= 0，当前: {getattr(config, 'AI_MAX_RETRIES', 0)}")
    if getattr(config, "USER_COOLDOWN", 0) < 0 or getattr(config, "BOT_COOLDOWN", 0) < 0:
        raise ValueError("USER_COOLDOWN / BOT_COOLDOWN 不能为负")
    if (getattr(config, "NIGHT_SILENCE_START", 0) < 0 or getattr(config, "NIGHT_SILENCE_END", 24) > 24
            or getattr(config, "NIGHT_SILENCE_START", 0) >= getattr(config, "NIGHT_SILENCE_END", 24)):
        raise ValueError(
            f"夜间静默时段非法: {getattr(config, 'NIGHT_SILENCE_START', 0)}~{getattr(config, 'NIGHT_SILENCE_END', 24)}"
            "（要求 0<=start<end<=24）")
    if getattr(config, "WEB_UI_ENABLED", False):
        if not (0 < getattr(config, "WEB_UI_PORT", 0) < 65536):
            raise ValueError(f"WEB_UI_PORT 必须在 1~65535 之间，当前: {getattr(config, 'WEB_UI_PORT', 0)}")
        # ⚠️ Web UI 的本地回环端口不能与 NapCat 反向 WS 端口一致（端口冲突）
        if getattr(config, "WEB_UI_PORT", 0) == getattr(config, "WS_PORT", 0):
            raise ValueError(
                f"WEB_UI_PORT ({getattr(config, 'WEB_UI_PORT', 0)}) 与反向 WS 端口 WS_PORT 冲突："
                "Web UI 的本地回环端口不能与 NapCat 反向 WS 端口一致，请修改 WEB_UI_PORT")
        # Bootstrap Lock：WEB_UI_PASSWORD 允许为空（= UNINITIALIZED），此时公开注册页
        # 是唯一入口，用于创建第一个管理员（无凭据时 _verify_admin 拒绝一切登录）。
    # MCP：MCP_ENABLED=true 时必须完整配置，fail-fast，绝不静默降级。
    # 支持插件式多 server（MCP_SERVERS JSON）与 legacy 单 server（MCP_SERVER_URL）。
    if getattr(config, "MCP_ENABLED", False):
        mcp_raw = (getattr(config, "MCP_SERVERS", "") or "").strip()
        allowed_hosts = list(getattr(config, "MCP_ALLOWED_HOSTS", None) or [])
        if mcp_raw:
            # ---- 多 server（MCP_SERVERS JSON）----
            try:
                servers = parse_mcp_servers(
                    mcp_raw,
                    default_timeout=int(getattr(config, "MCP_TIMEOUT", 15)),
                    default_tools=(getattr(config, "MCP_ALLOWED_TOOLS", "") or ""),
                    default_name=(getattr(config, "MCP_SERVER_NAME", "mcp") or "mcp"),
                )
            except ValueError as e:
                raise ValueError(str(e)) from None
            enabled = [s for s in servers if s.get("enabled", True)]
            if not enabled:
                raise ValueError("MCP_ENABLED=true 时 MCP_SERVERS 必须至少包含一个 enabled 的 server")
            seen_names = set()
            for s in enabled:
                name = s["name"]
                if name in seen_names:
                    raise ValueError(f"MCP_SERVERS 中 server name 重复: {name!r}")
                seen_names.add(name)
                ok, reason = validate_mcp_server_url(s["url"], allowed_hosts)
                if not ok:
                    raise ValueError(f"MCP_SERVERS 中 server {name!r} 的 URL 不合法: {reason}")
                if s["timeout"] < 1:
                    raise ValueError(f"MCP_SERVERS 中 server {name!r} 的 timeout 必须 >= 1（秒）")
                for token in (t.strip() for t in s["allowed_tools"].split(",") if t.strip()):
                    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", token):
                        raise ValueError(f"MCP_SERVERS 中 server {name!r} 的 allowed_tools 含非法工具名: {token!r}")
        else:
            # ---- legacy 单 server（保持原有报错文案）----
            mcp_url = (getattr(config, "MCP_SERVER_URL", "") or "").strip()
            if not mcp_url:
                raise ValueError("MCP_ENABLED=true 时必须配置 MCP_SERVER_URL（不允许静默降级为纯聊天）")
            ok, reason = validate_mcp_server_url(mcp_url, allowed_hosts)
            if not ok:
                raise ValueError(f"MCP_SERVER_URL 不合法: {reason}")
            if int(getattr(config, "MCP_TIMEOUT", 15)) < 1:
                raise ValueError(f"MCP_TIMEOUT 必须 >= 1（秒），当前: {getattr(config, 'MCP_TIMEOUT', 15)}")
            allowed = (getattr(config, "MCP_ALLOWED_TOOLS", "") or "").strip()
            for token in (t.strip() for t in allowed.split(",") if t.strip()):
                if not re.fullmatch(r"[A-Za-z0-9_.\-]+", token):
                    raise ValueError(f"MCP_ALLOWED_TOOLS 含非法工具名: {token!r}")
        if int(getattr(config, "MCP_MAX_TOOL_CALLS", 5)) < 0:
            raise ValueError(f"MCP_MAX_TOOL_CALLS 必须 >= 0，当前: {getattr(config, 'MCP_MAX_TOOL_CALLS', 5)}")
    # Persona / 群聊知识（Meme）配置合法性
    if int(getattr(config, "MEME_SUMMARY_INTERVAL_HOURS", 24)) < 1:
        raise ValueError("MEME_SUMMARY_INTERVAL_HOURS 必须 >= 1（小时）")
    if int(getattr(config, "MAX_GROUP_MEMES", 500)) < 10:
        raise ValueError("MAX_GROUP_MEMES 必须 >= 10")
    if int(getattr(config, "MEME_BUFFER_PER_GROUP", 1000)) < 50:
        raise ValueError("MEME_BUFFER_PER_GROUP 必须 >= 50")
    if int(getattr(config, "MEME_MAX_GROUPS_PER_RUN", 20)) < 1:
        raise ValueError("MEME_MAX_GROUPS_PER_RUN 必须 >= 1")
    if int(getattr(config, "MEME_MIN_MESSAGES_PER_SUMMARY", 10)) < 1:
        raise ValueError("MEME_MIN_MESSAGES_PER_SUMMARY 必须 >= 1")
    if int(getattr(config, "MAX_PERSONA_PROMPT_LENGTH", 8000)) < 500:
        raise ValueError("MAX_PERSONA_PROMPT_LENGTH 必须 >= 500")
    if int(getattr(config, "PERSONA_MAX_COUNT", 200)) < 1:
        raise ValueError("PERSONA_MAX_COUNT 必须 >= 1")
    # ===== 主动发言概率（PROACTIVE_MESSAGE_* / ACTIVE_CHAT_*）=====
    _err = _validate_active_chat_probs(config)
    if _err:
        raise ValueError(_err)
    # ===== 插件系统 =====
    protection = str(getattr(config, "PLUGIN_PROTECTION", "normal") or "normal").lower()
    if protection not in ("normal", "relaxed", "unsafe"):
        raise ValueError(f"PLUGIN_PROTECTION 必须是 normal/relaxed/unsafe，当前: {protection!r}")
    if int(getattr(config, "PLUGIN_MAX_COUNT", 100)) < 1:
        raise ValueError("PLUGIN_MAX_COUNT 必须 >= 1")
    if int(getattr(config, "PLUGIN_URL_MAX_BYTES", 5242880)) < 1024:
        raise ValueError("PLUGIN_URL_MAX_BYTES 必须 >= 1024")
    if int(getattr(config, "PLUGIN_ZIP_MAX_FILES", 200)) < 1:
        raise ValueError("PLUGIN_ZIP_MAX_FILES 必须 >= 1")
    # ===== NapCat WebSocket 模式 =====
    ws_mode = str(getattr(config, "NAPCAT_WS_MODE", "reverse") or "reverse").lower()
    if ws_mode not in ("reverse", "forward"):
        raise ValueError(f"NAPCAT_WS_MODE 必须是 reverse/forward，当前: {ws_mode!r}")
    if ws_mode == "forward":
        ws_url = (getattr(config, "NAPCAT_WS_URL", "") or "").strip()
        if not ws_url:
            raise ValueError("NAPCAT_WS_MODE=forward 时必须配置 NAPCAT_WS_URL")
        from urllib.parse import urlsplit as _urlsplit
        parts = _urlsplit(ws_url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise ValueError("NAPCAT_WS_URL 必须是 ws:// 或 wss:// 地址")
