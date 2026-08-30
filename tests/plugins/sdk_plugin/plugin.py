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


def on_schedule(event, api=None):
    return bot.route_schedule(event)


@command("add")
async def add_handler(event):
    args = event.args
    if len(args) == 2 and all(a.isdigit() for a in args):
        await event.reply(str(int(args[0]) + int(args[1])))
    else:
        await event.reply("用法：!add 1 2")


@command("cool")
async def cool_handler(event):
    if not await bot.cool_down("cmd:coolsample", 60):
        await event.reply("冷却中")
        return
    await event.reply("OK")


@bot.schedule(interval=1)  # 端到端测试用短间隔
async def tick(event):
    bot.log("info", "tick fired")
