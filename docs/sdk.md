# Flowerie Bot SDK（v1.3.0）

> 插件面向的统一开发接口。目标：插件作者只写业务，不接触 OneBot payload /
> HTTP / WebSocket / 消息匹配 / 权限判断。

## 1. 三层架构（依赖严格单向）

```text
插件（上层 plugin_sdk/）
    ↓ 依赖
中层 src/sdk/（BotEvent / BotMessage / Matcher / Rule / Listener / Permission —— 零 OneBot 命名）
    ↑ 被实现
下层 src/sdk/onebot/（DTO 瘦身 + Transformer + OneBotAdapter —— 唯一 import OneBot 语义处）
    ↓
OneBot / NapCat
```

- **下层**：WS 连接、HTTP 调用、JSON↔内部结构机械转换；只映射用到的字段
  （多余字段忽略 / 兜底）；CQ 码在此**阉割**为结构化数据（`at_list` / `images` /
  `reply_id`）——插件永远看不到 `[CQ:at,qq=...]`。
- **中层**：领域 Event / Message / Matcher / Rule / Permission / Listener；定义自己的
  `BotEvent` / `BotMessage` 接口；**绝无** `post_type` / `sub_type` / `message_type`
  等 OneBot 命名（依赖倒置校验：`grep -r "post_type" src/sdk/ --include="*.py"` 为空）。
- **上层**：插件 SDK（FlowerieBot + Matcher 装饰器 + Builder）；依赖中层接口，
  OneBot 细节完全屏蔽。

**换平台**：重写下层 `src/sdk/onebot/` 的三个文件（dto / transformer / adapter），
中层与上层零改动。

## 2. 最小示例（SDK 模式插件）

```python
# plugin.py（插件自带 plugin_sdk/flowerie_sdk/ 副本）
from flowerie_sdk import FlowerieBot, command, keyword, regex, prefix, exact, rule

bot = FlowerieBot()

@command("hello")
async def hello(event):
    await event.reply("你好")          # 自动引用原消息（reply 段）

@keyword("花璃")
async def flowerie(event):
    await event.reply(BotMessage().add_text("怎么啦？").at(event.user_id))

@regex(r"^!天气\s", rule=rule(is_group=True))
async def weather(event):
    await event.reply(BotMessage().add_text("正在查询…").image("http://x/y.png"))

def on_startup(context, api=None):
    bot.attach(api)      # 绑定协议 api
    bot.register()       # 上报 matchers（一次性）

def on_message(event, api=None):
    return bot.route(event)   # SDK 路由（未匹配自动忽略）
```

插件零依赖：**不 import httpx / aiohttp / json / sqlite3**，不构造 OneBot payload。

## 3. Matcher（匹配与路由）

| 装饰器 | 说明 |
| --- | --- |
| `@command("hello")` | 命令匹配，自动支持 `/` `!` `.` 前缀；`event.matcher_args` 拿参数 |
| `@keyword("花璃")` | 包含匹配
| `@regex(r"...")` | 正则匹配（非法正则按不命中处理，不抛）
| `@prefix("!hi")` | 前缀匹配
| `@exact("ping")` | 精确匹配

- **priority**：数字**大者先匹配**（与声明式插件规则一致，全项目统一）。
- **block / stop**：`@command("x", block=True)`——命中后阻断本插件后续 Matcher；
  event.stop() 亦同。**跨插件不阻断**（事件隔离原则）。
- **Rule**（可扩展条件，AND 组合）：`is_group` / `is_private` / `is_bot_admin` /
  `is_bot_owner` / `is_group_admin` / `is_group_owner` / `user_id` / `group_id` /
  自定义谓词 `rule(custom=lambda ev, bot: ...)`（支持 async）。跨规则组合：`r1 + r2`。
- 性能提示：`is_group_admin` 等群角色条件会触发成员查询（网络开销），
  建议只用于低频命令。

## 4. Event（统一事件对象）

```python
event.kind            # message / notice / request / lifecycle（领域语义）
event.scope           # group / private
event.group_id / event.user_id / event.message_id / event.time
event.text            # 纯文本（CQ 已阉割）
event.at_list         # @ 的 QQ 列表
event.images          # 图片列表
event.is_group / event.is_private
await event.reply("hi")   # 自动回复（引用原消息）；群→群、私聊→私聊
await event.recall()      # 撤回本事件消息
event.stop()              # 阻断后续 Matcher / Listener
```

## 5. Bot API（bot.send / bot.reply / ...）

| API | 说明 |
| --- | --- |
| `await bot.send(("group", 123), "hi")` | 发送（target 可为 group/private 元组或群号）
| `await bot.reply(event, "hi")` | 回复事件
| `await bot.recall(message_id)` | 撤回（只允许撤回本 bot 已发送的记录）
| `await bot.get_message(message_id)` | 消息详情（BotMessage）
| `await bot.get_context(group_id, max_messages=10)` | 近期上下文（**复用 Flowerie
  ContextManager**，不调平台历史接口）
| `await bot.get_group_member(group_id, user_id)` | 成员信息（含 role）
| `await bot.get_group_members(group_id)` | 成员列表
| `await bot.is_admin(event)` / `is_owner(event)` | bot 管理员（**复用 ADMIN_QQ_IDS**）
| `await bot.is_group_admin(gid, uid)` / `is_group_owner(gid, uid)` | 群角色
| `await bot.mute(gid, uid, seconds)` / `bot.kick(gid, uid)` | 群管理
| `await bot.check_permission(event, "group_owner")` | 权限检查

## 6. Builder（消息构造链式）

```python
msg = BotMessage().add_text("恭喜").at(123).image("http://x/a.png").reply(42)
await bot.send(("group", 1), msg)
# 下层自动转成 OneBot 段数组：reply → text → at → image
```

## 7. 错误统一（BotError 体系）

插件只面对：`BotError` / `BotAPIError` / `BotTimeoutError` /
`BotPermissionError` / `MessageNotFoundError` / `UnsupportedOperationError`。
底层 HTTP / retcode / 超时细节不泄漏。

## 8. 兼容性

- 经典插件模式（`on_message` 返回 actions 数组 / 声明式 JSON 插件）**完全保留**；
  SDK 模式是新增路径（注册了 matcher 的插件只接收匹配事件）。
- 插件进程仍是 `python -I` 隔离：SDK **以插件自带副本**（`plugin_sdk/flowerie_sdk/`）
  方式交付；runner 加载时把插件目录加入 `sys.path`（不影响隔离：其余 import 仍被阻止）。
- 事件负载领域化（kind / scope / text / at_list / images / reply_id / notice_kind）；
  旧字段（post_type/message_type）不再投递——经典插件如依赖请改用领域字段。
