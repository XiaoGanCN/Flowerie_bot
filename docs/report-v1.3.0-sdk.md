# Bot SDK 第一阶段交付报告（v1.3.0）

## 1. 审计结果

- 事件流：NapCat WS（双向）→ `message_router.process_event` → 插件投递（权限门 read_message）
  → `_handle_message` 主流程；流出统一 `Sender` → OneBot HTTP API。
- OneBot 耦合：发送侧集中在 `src/services/sender.py`（本轮直接作为下层基础）；
  接收侧分散 Router/Assembler/FileParser（插件通道已迁移至下层 Transformer，主流程保持
  ——Router 稳定优先，下阶段迁移）。
- 可复用未重写：Sender、ContextManager（get_context 数据源）、ADMIN_QQ_IDS（bot_admin
  唯一权威）、PermissionManager + `_run_action`（唯一副作用出口）、CommandHandler/
  Memory/冷却/Persona/知识层全部未动。
- 详见 [docs/sdk-audit.md](sdk-audit.md)。

## 2. 新增 SDK API（设计优先「OneBot 已有的直接用、没有的自造」）

- OneBot/OneBot11 已有（Adapter 直接包装 Sender）：send/reply/recall/get_message/
  get_group_member(s)/mute/kick
- OneBot 没有、自造（复用 Flowerie 内部）：get_context（ContextManager）、
  bot.admin（ADMIN_QQ_IDS）、匹配/路由（Matcher/Rule）、事件监听（Listener）、
  权限抽象（PermissionChecker）、统一错误（BotError 体系）

## 3. Event 架构

`BotEvent`：kind（message/notice/request/lifecycle）+ scope（group/private）+
group_id/user_id/message_id/time/text/at_list/images/reply_id/operator_id；
`is_group/is_private/is_message/is_notice/is_request/is_lifecycle`；
`reply()/recall()/stop()`；构造：顶层 `Transformer.to_bot_event(raw)`（下层）/
`PluginManager` 投递 payload（领域化）。

## 4. Message 架构

`BotMessage`：text/at_list/images/reply_id + Builder（add_text/at/image/reply）+
has()/\_\_iter\_\_；转换集中在下层：入站 `extract_text/extract_at_list/extract_images/
extract_reply_id`（CQ 码阉割）、出站 `to_bot_message_payload`（→ OneBot 段数组）。

## 5. Matcher 架构

command/keyword/regex/prefix/exact（装饰器收集 `__flowerie_matchers__`）→
`matcher_register` 协议注册（权限 read_message）→ 主进程 `_match_plugin_payload`
（priority 降序命中 → payload.matched）→ 插件 SDK `route` 分发 handler。
**priority：数字大者先（文档固定）；block=True 命中后阻断同插件后续**（跨插件不阻断）。
Rule：is_group/is_private/is_bot_admin/is_bot_owner/is_group_admin/is_group_owner/
user_id/group_id/自定义 async 谓词 + `Rule + Rule` 组合。

## 6. Permission 架构

`PermissionChecker.check(event, kind)`：user/group_member（存在即真）/
bot_admin/bot_owner（**复用 ADMIN_QQ_IDS**）/group_admin/group_owner（Adapter
get_group_member.role）；`require_permission(kind)` 装饰器（失败抛 BotPermissionError）。

## 7. OneBot Adapter 架构

`BotAdapter`（中层 abstract：send/recall/get_message/get_user_info/get_group_info/
get_group_member(s)/mute/kick/get_context）→ `OneBotAdapter`（下层：包装 Sender +
错误转换 BotAPIError/BotTimeoutError/MessageNotFoundError/UnsupportedOperationError）。
换平台：新增 Adapter 实现即可，中层/上层零改动（已用 FakeSender 测试隔离验证）。

## 8. 兼容层设计

- 经典模式（插件返回 actions / JSON 声明式插件）完全保留；SDK 模式为新增路径
  （注册 matcher 的插件只接收匹配事件）
- 插件进程 `python -I` 隔离不变；SDK 以插件自带副本（`plugin_sdk/flowerie_sdk/`）交付，
  runner 加载时插件目录入 sys.path（其余 import 仍被阻止）
- 事件负载领域化（kind/scope/at_list/images）**破坏性变更**：旧字段
  post_type/message_type 不再投递——文档已明示迁移方式（CHANGELOG v1.3.0）

## 9. 修改文件

新增：`src/sdk/`（9 文件）+ `src/sdk/onebot/`（4 文件）+ `plugin_sdk/flowerie_sdk/`
（6 文件）+ `docs/sdk.md` `docs/api.md` `docs/plugins.md` `docs/sdk-audit.md`
`docs/report-v1.3.0-sdk.md` + `tests/test_sdk_*.py`（5 文件）+ `tests/plugins/sdk_plugin/`
修改：`src/plugins/manager.py` `permissions.py` `runner/python_runner.py`、
`src/core/message_router.py`、`main.py`、`src/services/sender.py`、`src/services/
config_schema.py`、版本号 5 处、README/CHANGELOG/security/architecture-audit/audit/
plugin-developer-guide/development/web-ui/configuration 及全部功能域文档

## 10. 新增测试数量

+24（BotMessage 3 · Transformer 4 · Event kind 2 · Matcher 8 · Listener 3 ·
Adapter 5 · Permission 3 · 端到端 SDK 插件 1 · 并发 100 1）
保留既有全部测试（v1.2.0 基线未删一处）。

## 11. Pytest / 12. Ruff / 13. MyPy

- pytest：本地可跑集 **129 passed**（含 SDK 24）；CI（3.9/3.12）+ Acceptance 全绿（e101bbc）
- ruff：**0 errors**（`ruff check .` 全仓；曾发现 35 项 lint 已全部修复）
- MyPy：项目未配置 mypy（与基线一致），跳过

## 14. 并发测试

- 100 并发事件 + matcher：`test_many_events_no_cross_pollution`（matcher args/text
  不互相污染）
- 3 并发 dispatch 事件到同一 SDK 插件（泄漏检查脚本）：正常

## 15. 资源泄漏检查

- task 泄漏：`asyncio.all_tasks()` 0 残留
- 子进程泄漏：`mgr.shutdown()` 后 python_runner 子进程即回收
- HTTP session：SDK 不新建连接（复用 Sender 的 aiohttp session，main 统一 close）
- SQLite：SDK 不持有数据库连接
- 未来 workaround 说明见 sdk-audit.md §4

## 16. 文档修改

README（v1.3.0 + SDK 链接）、CHANGELOG（v1.3.0 完整条目）、docs/sdk.md、
docs/api.md、docs/plugins.md、docs/sdk-audit.md、docs/plugin-developer-guide.md、
docs/security.md、docs/architecture-audit.md、docs/development.md、docs/web-ui.md、
docs/configuration.md、AUDIT.md + 功能域文档（knowledge/mcp/persona/stickers/
install-termux 版本脚注）

## 17. 剩余问题 / 18. 下一阶段建议

1. 主流程（_handle_message/Assembler）仍直接访问 OneBot 字段——下阶段迁移至
   Transformer（Router 冻结期后）
2. `get_user_info`/`get_group_info` 平台无标准端点——SDK 抛 UnsupportedOperationError
   （或后续用 get_group_member 组合补足）
3. Session/等待消息（Ask/Confirm/等待用户）、命令系统进阶（参数类型/子命令/冷却）、
   定时任务（interval/cron）、KV 存储——第二阶段候选（SDK 骨架已就位）
4. `is_group_admin` Rule 每次匹配触发成员查询（网络开销）——可加缓存（下阶段）
5. node 插件 SDK（当前 SDK 为 Python 优先）——后续可选

## 19. Review（黑白盒独立审查）

> 独立 review 结果见对话回复；发现项如已修复同步记录。
