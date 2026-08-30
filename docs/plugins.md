# 插件开发（v1.3.0）

> 完整入口见 [plugin-developer-guide.md](plugin-developer-guide.md)；SDK API 见 [sdk.md](sdk.md)；
> 动作/权限总表见 [api.md](api.md)。

## 两种模式

| | SDK 模式（推荐新插件） | 经典模式 |
| --- | --- | --- |
| 写法 | 装饰器 + `await event.reply(...)` | `on_message` 返回 actions 数组 |
| 依赖 | `flowerie_sdk/`（自带副本） | 无 |
| 匹配 | 主进程按 Matcher 只投递命中事件 | 声明式 JSON 规则 / 全量事件 |
| 示例 | `@command("hello") async def hello(event): await event.reply("你好")` | 见 guide |

## 快速开始（复制模板）

```
plugins/myplugin/
├── manifest.json        # runtime=python, permissions 至少 read_message(+send_message 若回复)
├── plugin.py            # 见下
└── flowerie_sdk/        # 从仓库 plugin_sdk/ 复制
```

```python
from flowerie_sdk import FlowerieBot, command, rule

bot = FlowerieBot()

@command("hello")
async def hello(event):
    await event.reply("你好")

def on_startup(context, api=None):
    bot.attach(api)
    bot.register()

def on_message(event, api=None):
    return bot.route(event)
```

## 安装 / 启用

管理员在 Web UI「插件」页 上传 ZIP / 填 URL 安装（默认未启用），
批准权限后启用；风险权限（filesystem_write / group_manage / delete_message /
read_message_history）按需最小授权。

## 高频能力速查（v1.4）

多轮/Session（wait_for/ask/confirm/select）· 定时（@bot.schedule）· 冷却（cool_down）·
命令参数（event.args·子命令）· KV（kv_*）· 请求处理（handle_friend_request）·
AI（ai_chat 受限）· HTTP 扩展（http_put/delete/head/download）· 记忆（mem_update/clear）·
工具（random_choice/random_int/now/format_time）——完整示例与章节见 [sdk.md](sdk.md)。

## 检查清单（上线前）

- [ ] `manifest.permissions` 只声明必要项（批准 ⊆ 声明，声明 ⊆ 需求）
- [ ] 消息回复用 `event.reply` 或 `bot.send`（不要拼 OneBot 段/CQ 码）
- [ ] 群管理类命令加 `rule(is_group_admin=True)` 或 `require_permission`
- [ ] 插件目录内文件用 `file_read` / `file_write`（禁止读 `../`）
- [ ] 网络请求走 `http_request`（主进程 SSRF 防护生效）
