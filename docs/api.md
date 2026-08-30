# Flowerie API 概览（v1.3.0）

> 完整示例见 [sdk.md](sdk.md)；插件开发见 [plugins.md](plugins.md)。

## 消息

| API | 权限 | 说明 |
| --- | --- | --- |
| `bot.send(target, msg)` | send_message | 群/私聊发送（str / BotMessage） |
| `bot.reply(event, msg)` | send_message | 回复（自动引用） |
| `bot.recall(message_id)` | delete_message | 撤回（仅本 bot 已发送记录） |
| `bot.get_message(message_id)` | read_message_history | 消息详情 |
| `bot.get_context(gid, n)` | read_message_history | 近期上下文（ContextManager） |

## 群

| API | 权限 | 说明 |
| --- | --- | --- |
| `bot.get_group_member(gid, uid)` | read_group_info | 成员信息（role: owner/admin/member） |
| `bot.get_group_members(gid)` | read_group_info | 成员列表 |
| `bot.is_group_admin(gid, uid)` | read_group_info | 群管理员判定 |
| `bot.is_group_owner(gid, uid)` | read_group_info | 群主判定 |
| `bot.mute(gid, uid, seconds)` | group_manage | 禁言（0=解除） |
| `bot.kick(gid, uid)` | group_manage | 移出群成员 |

## 用户 / 权限

| API | 说明 |
| --- | --- |
| `bot.is_admin(event)` / `is_owner(event)` | bot 管理员（ADMIN_QQ_IDS） |
| `bot.check_permission(event, kind)` | user / group_member / group_admin / group_owner / bot_admin / bot_owner |
| `@require_permission("group_admin")` | 处理器装饰器（未通过抛 BotPermissionError） |

## 事件（插件投递 payload，领域语义）

```
kind=message|notice|request|lifecycle
scope=group|private
group_id/user_id/message_id/time
text（CQ 已阉割）/ at_list / images / reply_id
notice_kind / request_kind
(匹配命中时) matched=[{name, kind, args, block}]
```

## 经典插件动作（协议层，未用 SDK 也可用）

send_message · send_private_message · send_reply · delete_message · get_message ·
get_group_history · get_context · get_group · get_user · get_group_member ·
get_group_members · group_ban · group_kick · group_admin · is_group_admin ·
is_group_owner · matcher_register · get_memory · write_memory · http_request ·
file_read · file_write · log · test

## 权限全集（管理员按插件批准，未批准即拒绝）

send_message · read_message · read_group_info · read_user_info · read_memory ·
write_memory · http_request · filesystem_read · filesystem_write · delete_message ·
read_message_history · group_manage · execute_process(保留，v1 拒绝) ·
webhook(保留，v1 拒绝)
