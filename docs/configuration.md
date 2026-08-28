# 配置说明

优先级：**Web UI 持久化配置 > 环境变量（.env）> 代码默认值**。
Web UI 修改的配置存于 `data/settings.db`，重启后优先使用。

## 必填

| 变量 | 说明 |
| :--- | :--- |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `BOT_QQ` | 机器人 QQ 号 |

## AI

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `DEEPSEEK_API_URL` | API 地址 | `https://api.deepseek.com/chat/completions` |
| `DEEPSEEK_MODEL` | 群聊模型 | `deepseek-v4-flash` |
| `VISION_MODEL` / `VISION_API_URL` / `VISION_API_KEY` | 识图视觉模型（留空回退 DeepSeek） | `deepseek-v4-flash-vision-exp` |
| `VISION_TIMEOUT` | 识图超时（秒） | `30` |
| `MAX_REPLY_LENGTH` | 最大回复长度 | `40` |
| `MAX_AI_INPUT_CHARS` | 单次 AI 输入上限 | `8000` |

## Bot

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `BOT_NICKNAME` | 昵称 | `花璃` |
| `WS_HOST` / `WS_PORT` | 反向 WS 监听（NapCat 连这里） | `127.0.0.1` / `3001` |
| `HTTP_API_BASE` | NapCat HTTP API | `http://127.0.0.1:3000` |
| `WS_TOKEN` | 反向 WS 鉴权 token（可选） | 空 |
| `ONLY_REPLY_WHEN_AT` | 哑巴模式（只回 @） | `false` |
| `USER_COOLDOWN` / `BOT_COOLDOWN` | 用户/机器人冷却（秒） | `5` / `2` |
| `MAX_CONSECUTIVE_REPLIES` | 连续回复上限 | `3` |
| `ALLOWED_GROUP_IDS` | 群白名单（逗号分隔，空=所有群） | 空 |
| `ADMIN_QQ_IDS` | 管理员 QQ（可改 Prompt/清记忆） | 空 |

## 记忆

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `MEMORY_PATH` | 记忆库（SQLite） | `./data/memory.db` |
| `MEMORY_TTL_DAYS` | 用户原话记忆保留天数 | `0`（永久） |
| `MODEL_MEMORY_TTL_DAYS` | AI 推断记忆保留天数 | `30` |
| `MEMORY_DISABLED_GROUPS` | 禁用记忆的群 | 空 |
| `AUDIT_LOG_PATH` | 记忆审计日志 | `./data/audit.log` |

## 表情包（Sticker）

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `STICKER_DIR` | 表情包目录（图片文件） | 空（禁用） |
| `STICKER_ENABLED` | 功能开关 | `false` |
| `STICKER_COOLDOWN` | 每群表情包冷却（秒） | `60` |
| `STICKER_DB_PATH` | Vision 索引缓存（SQLite） | `./data/stickers.db` |

索引机制：首次扫描用视觉模型生成描述缓存（按文件 SHA-256）；重启复用缓存不重复调 API；文件被替换（同名不同内容）重新分析；Vision 失败记录状态，24 小时后自动重试。

## MCP

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `MCP_ENABLED` | 总开关 | `false` |
| `MCP_SERVER_URL` | MCP server 地址（HTTP/SSE） | 空 |
| `MCP_SERVER_NAME` | server 名称 | `mcp` |
| `MCP_TIMEOUT` | 单次工具调用超时（秒） | `15` |
| `MCP_MAX_TOOL_CALLS` | 单轮对话工具调用上限 | `5` |
| `MCP_ALLOWED_TOOLS` | 工具白名单（逗号分隔，空=不允许任何工具） | 空 |
| `MCP_CIRCUIT_FAILURES` / `MCP_CIRCUIT_PAUSE_SECONDS` | MCP 独立熔断阈值/冷却 | `5` / `60` |

## 预算与稳定性

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `DAILY_AI_CALL_BUDGET` | 全局每日 AI 调用上限（0=不限） | `1000` |
| `GROUP_DAILY_AI_CALL_BUDGET` | 每群每日上限（0=不限） | `300` |
| `USER_AI_CALL_MIN_INTERVAL` | 同一用户 AI 调用最小间隔（秒） | `10` |
| `AI_MAX_RETRIES` | AI 重试次数（每次尝试单独过预算） | `3` |
| `AI_CIRCUIT_BREAKER_FAILURES` / `AI_CIRCUIT_BREAKER_PAUSE_SECONDS` | AI 熔断阈值/冷却 | `10` / `60` |
| `GROUP_CIRCUIT_BREAKER_*` | 群级熔断（阈值/冷却/容量/TTL） | `5` / `30` / `1000` / `7d` |
| `EVENT_PROCESS_TIMEOUT` | 单条消息处理超时（秒） | `90` |
| `MAX_CONCURRENT_AI` | AI/识图并发上限 | `3` |
| `TOXIC_GROUP_IDS` | 引战检测群 | 空 |

## Web UI

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `WEB_UI_ENABLED` | 管理后台开关 | `false` |
| `WEB_UI_HOST` / `WEB_UI_PORT` | 监听地址/端口 | `127.0.0.1` / `8080` |
| `WEB_UI_USERNAME` / `WEB_UI_PASSWORD` | 登录账号/密码（启用时必须设置） | `admin` / 空 |
| `WEB_UI_TOKEN_TTL_SECONDS` | 登录 token 有效期 | `3600` |

> ⚠️ `WEB_UI_PORT` 与 `WS_PORT` 冲突时启动直接报错——Web UI 的本地回环端口不能与 NapCat 反向 WS 端口一致。

## 日志

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FORMAT` | `text`（人类可读）/ `json`（JSON lines，含 trace_id/event） | `text` |
| `CONTEXT_BACKUP_PATH` | 上下文崩溃备份（SQLite） | `./data/context_backup.db` |
| `CONTEXT_BACKUP_INTERVAL` | 备份间隔（秒） | `60` |

## 热更新说明

- **Web UI 可热更新**：模型/密钥/冷却/预算/表情包开关/MCP 开关/日志级别等（修改后立即生效）
- **需要重启**：WS 端口、HTTP API 地址、数据库路径、监听地址等 Advanced 项（UI 会提示）
