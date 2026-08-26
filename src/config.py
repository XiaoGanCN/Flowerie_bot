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
    # 上下文崩溃持久化：周期备份最近 50 条上下文，意外去世后重启自动恢复
    CONTEXT_BACKUP_PATH: str = "./data/context_backup.json"
    CONTEXT_BACKUP_INTERVAL: int = 60
    
    # Bot
    BOT_QQ: int = Field(..., env="BOT_QQ")
    BOT_NICKNAME: str = "花璃"
    
    # Connection
    WS_HOST: str = "127.0.0.1"
    WS_PORT: int = 3001
    HTTP_API_BASE: str = "http://127.0.0.1:3000"
    
    # Behavior
    ONLY_REPLY_WHEN_AT: bool = False
    REPLY_PROBABILITY: float = 0.3
    ACTIVE_CHAT_INTERVAL: int = 600
    USER_COOLDOWN: int = 5
    BOT_COOLDOWN: int = 2
    MAX_REPLY_LENGTH: int = 40
    MAX_CONSECUTIVE_REPLIES: int = 3
    CONTEXT_SIZE: int = 300
    COLD_TIME: int = 600
    LOG_LEVEL: str = "INFO"
    
    # Random active chat
    RANDOM_ACTIVE_CHAT_MIN_INTERVAL: int = 14400
    RANDOM_ACTIVE_CHAT_MAX_INTERVAL: int = 21600
    NIGHT_SILENCE_START: int = 0
    NIGHT_SILENCE_END: int = 8
    ACTIVE_CHAT_COOLDOWN: int = 180
    BOT_CONSECUTIVE_REPLY_COOLDOWN: int = 60
    BOT_RECENT_REPLY_WINDOW: int = 60
    
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
    MEMORY_PATH: str = "./data/memory.json"
    ARCHIVE_BASE_DIR: str = "./data/archive"
    
    # White list
    ALLOWED_GROUP_IDS: Optional[List[int]] = Field(None, env="ALLOWED_GROUP_IDS")
    TOXIC_GROUP_IDS: Optional[List[int]] = Field(None, env="TOXIC_GROUP_IDS")
    
    @field_validator("ALLOWED_GROUP_IDS", "TOXIC_GROUP_IDS", mode="before")
    @classmethod
    def parse_list(cls, v):
        if isinstance(v, str):
            if v.strip() == "":
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        return v
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

def load_config() -> Settings:
    return Settings()