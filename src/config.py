from typing import List, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # DeepSeek
    DEEPSEEK_API_KEY: str = Field(..., env="DEEPSEEK_API_KEY")
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
    # 上下文崩溃持久化：周期备份最近 50 条上下文（SQLite），意外去世后重启自动恢复
    CONTEXT_BACKUP_PATH: str = "./data/context_backup.db"
    CONTEXT_BACKUP_INTERVAL: int = 60
    
    # Bot
    BOT_QQ: int = Field(..., env="BOT_QQ")
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

    # Random active chat
    NIGHT_SILENCE_START: int = 0
    NIGHT_SILENCE_END: int = 8
    ACTIVE_CHAT_COOLDOWN: int = 180
    BOT_CONSECUTIVE_REPLY_COOLDOWN: int = 60
    
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

    # White list
    ALLOWED_GROUP_IDS: Optional[List[int]] = Field(None, env="ALLOWED_GROUP_IDS")
    TOXIC_GROUP_IDS: Optional[List[int]] = Field(None, env="TOXIC_GROUP_IDS")
    # 记忆隐私：这些群里完全禁止写入记忆
    MEMORY_DISABLED_GROUPS: Optional[List[int]] = Field(None, env="MEMORY_DISABLED_GROUPS")
    # 管理员 QQ（可执行 /memory_clear /memory_dump）
    ADMIN_QQ_IDS: Optional[List[int]] = Field(None, env="ADMIN_QQ_IDS")

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
    IMAGE_ALLOWED_HOSTS: Optional[List[str]] = Field(None, env="IMAGE_ALLOWED_HOSTS")
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

    @field_validator("IMAGE_ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_str_list(cls, v):
        if isinstance(v, str):
            if v.strip() == "":
                return []
            return [x.strip().lower() for x in v.split(",") if x.strip()]
        return v
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

def load_config() -> Settings:
    return Settings()