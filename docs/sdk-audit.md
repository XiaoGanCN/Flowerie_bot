# Bot SDK 审计基线（v1.3.0 前置）

> 只读审计（2026-08）：事件流入/流出、OneBot 耦合点、可复用 API、风险。
> 结论：OneBot 发送侧已集中在 Sender（可直接作为下层），接收侧分散于
> Router/Assembler/FileParser（本阶段插件通道已迁移至 Transformer，主流程保持）。

## 1. 架构图（审计时）

```
NapCat WS（反向 websocket_server / 正向 napcat_forward_client）
  → message_router.process_event
      → 先投递插件：plugin_manager.dispatch_event（权限门 read_message）
      → 再分派主流程：_handle_message / _handle_group_upload / _handle_poke
          → CommandHandler → _should_reply → AiGateway.guarded_chat → AIClient
  ← Sender → HTTP_API_BASE（OneBot11 HTTP）
```

插件流：dispatch_event → runtime（stdin/stdout JSON-Lines）→ runner
（on_message/on_notice/on_request/on_lifecycle）→ 返回 actions / SDK route →
manager._run_action（唯一副作用出口，先过 PermissionManager）→ sender。

## 2. OneBot 耦合点（审计清单）

- 发送侧：`src/services/sender.py`（全部 HTTP 调用与 payload 构造集中于此：send_* /
  send_msg_raw / delete_msg / get_msg / get_group_msg_history / get_group_member_info /
  get_group_member_list / set_group_ban / set_group_kick / set_group_admin）
- 接收侧：`message_router`（post_type/message_type/notice_type/sub_type/段字段）、
  `message_assembler`（image/reply/at/forward/json 段解析）、`file_parser`（get_file /
  get_forward_msg 端点）
- 插件通道：`message_router._plugin_payload`（本阶段已改为 Transformer 领域输出；
  主流程仍直接访问字段——Router 稳定性优先，下阶段逐步迁移）

## 3. 可复用（未重复实现）

- Sender 全部方法（OneBotAdapter 直接包装）
- ContextManager：get_group_state / add_context / get_context_text（get_context 数据源）
- ADMIN_QQ_IDS：bot_admin 唯一权威（command_handler 同源）
- CommandHandler / 冷却 / 记忆：均未改动（SDK 不重造）
- 插件权限门：PermissionManager + _run_action（唯一副作用出口）

## 4. 主要风险与本阶段处置

1. `python -I` 子进程无法 import `src.sdk` → SDK 以插件自带副本交付
   （`plugin_sdk/flowerie_sdk/`），runner 加载时插件目录入 sys.path（其余隔离不变）
2. 插件事件负载此前缺 post_type → 领域化后自带 kind/scope（已修）
3. 事件投递在白名单检查之前（既有行为）→ 保持，文档注明
4. 客户端-服务端能力不对称 → runner PluginApi 补齐 SDK 动作包装（本轮完成）
