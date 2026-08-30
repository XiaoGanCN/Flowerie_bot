"""Flowerie Bot SDK：插件面向的统一开发接口（上/中/下三层，依赖严格单向）。

    上层 plugin_sdk/（插件进程内：FlowerieBot + Matcher 装饰器 + Builder）
        ↓ 依赖
    中层 src/sdk/（BotEvent / BotMessage / Matcher / Rule / Listener / Permission —— 零 OneBot 命名）
        ↑ 被实现
    下层 src/sdk/onebot/（DTO 瘦身 + Transformer + OneBotAdapter；唯一 import OneBot 语义处）

依赖倒置校验：src/sdk/ 顶层模块不得出现 onebot/post_type/sub_type 等字样（见 docs/sdk.md）。
"""
from src.sdk.adapter import BotAdapter
from src.sdk.bot import Bot
from src.sdk.errors import (
    BotAPIError,
    BotError,
    BotPermissionError,
    BotTimeoutError,
    MessageNotFoundError,
    UnsupportedOperationError,
)
from src.sdk.event import BotEvent
from src.sdk.message import BotMessage

__all__ = [
    "Bot", "BotEvent", "BotMessage", "BotAdapter",
    "BotError", "BotAPIError", "BotTimeoutError", "BotPermissionError",
    "MessageNotFoundError", "UnsupportedOperationError",
]
