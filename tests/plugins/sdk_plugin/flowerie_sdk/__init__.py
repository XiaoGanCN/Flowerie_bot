"""Flowerie 插件 SDK（插件自带副本，零依赖）。

插件只需：
    from flowerie_sdk import FlowerieBot, command, keyword, regex, prefix, exact, rule

（见 docs/sdk.md 最小示例）
"""
from flowerie_sdk.bot import BotAPIError, FlowerieBot
from flowerie_sdk.event import BotEvent
from flowerie_sdk.matcher import command, exact, keyword, prefix, regex, rule
from flowerie_sdk.message import BotMessage

__all__ = ["FlowerieBot", "BotEvent", "BotMessage",
           "command", "keyword", "regex", "prefix", "exact", "rule", "BotAPIError"]
