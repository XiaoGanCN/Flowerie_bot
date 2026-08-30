# -*- coding: utf-8 -*-
"""SDK 模式插件：装饰器收集 matcher，主进程匹配后投递，SDK 路由 handler。"""
from flowerie_sdk import FlowerieBot, command, keyword, rule

bot = FlowerieBot()


@command("hi", rule=rule(is_group=True))
async def hi_handler(event):
    await event.reply("你好呀")


@keyword("test")
async def kw_handler(event):
    await event.reply("关键词命中")


def on_startup(context, api=None):
    bot.attach(api)
    bot.register()  # 上报 matchers（一次性）


def on_message(event, api=None):
    return bot.route(event)
