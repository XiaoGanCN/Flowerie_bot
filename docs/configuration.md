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
| `NAPCAT_WS_MODE` | NapCat WebSocket 模式：`reverse`（原有，NapCat 连 Flowerie 的 WS server）/ `forward`（Flowerie 连 NapCat 正向 WS） | `reverse` |
| `NAPCAT_WS_URL` | `forward` 模式必填，NapCat 正向 WS 地址（`ws://` 或 `wss://`） | 空 |
| `NAPCAT_WS_AUTH_MODE` | forward 鉴权通道：`header`（默认，Authorization: Bearer，URL 不带 token）/ `query`（URL `?access_token=`，OneBot11 约定）——**两种约定互斥，绝不同时发送**（避免令牌出现在代理/访问日志）；需重启 | `header` |
| `NAPCAT_ACCESS_TOKEN` | `forward` 模式鉴权 token（NapCat 侧需配相同 access token；**绝不清写入日志**） | 空 |
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
| `MCP_SERVER_URL` | MCP server 地址（HTTP/SSE，单 server） | 空 |
| `MCP_SERVER_NAME` | server 名称 | `mcp` |
| `MCP_SERVERS` | 多 server 列表（JSON 数组，插件式；为空时用 `MCP_SERVER_URL`） | 空 |
| `MCP_ALLOWED_HOSTS` | 显式放行的本地/内网主机白名单（逗号分隔；仅这些地址可绕过回环/私网拒绝） | 空 |
| `MCP_TIMEOUT` | 单次工具调用超时（秒） | `15` |
| `MCP_MAX_TOOL_CALLS` | 单轮对话工具调用上限 | `5` |
| `MCP_ALLOWED_TOOLS` | 工具白名单（逗号分隔，留空=放行所有工具） | 空 |
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

## 主动发言概率

> 全部可配置（v1.2.0 把原来的硬编码概率参数化，默认值=原硬编码值，行为零变化）。

### 上下文随机回复概率（`PROACTIVE_MESSAGE_*`）

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `PROACTIVE_MESSAGE_MIN_PROBABILITY` | 最终概率钳制下限（0~1） | `0.01` |
| `PROACTIVE_MESSAGE_MAX_PROBABILITY` | 最终概率钳制上限（0~1，须 ≥ 下限） | `0.05` |
| `PROACTIVE_MESSAGE_BASE_PROBABILITY` | 基础概率 | `0.03` |
| `PROACTIVE_MESSAGE_USER_BOOST` | 最近 5 条中用户消息 ≥2 条时的增量 | `0.01` |
| `PROACTIVE_MESSAGE_SINGLE_USER_PROBABILITY` | 最近消息全部来自同一用户时的低概率（防刷屏） | `0.02` |
| `PROACTIVE_MESSAGE_SHORT_MESSAGE_PROBABILITY` | 最近一条消息 <2 字时的低概率 | `0.02` |
| `PROACTIVE_MESSAGE_EMPTY_CONTEXT_PROBABILITY` | 群尚无上下文时的回复概率 | `0.02` |
| `PROACTIVE_MESSAGE_BOT_MULTIPLIER` | 机器人连续发言 ≥2 条时的衰减系数（1.0=不衰减） | `0.3` |

### 主动聊天循环（`ACTIVE_CHAT_*`）

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `ACTIVE_CHAT_PROBABILITY` | 主动聊天循环触发概率（0~1，如 `0.10`=10%） | `0.10` |
| `ACTIVE_CHAT_INTERVAL_MIN_SECONDS` | 轮询间隔下限（秒，须 ≤ 上限） | `5` |
| `ACTIVE_CHAT_INTERVAL_MAX_SECONDS` | 轮询间隔上限（秒） | `10` |
| `ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS` | 连续主动发言 ≥2 次后的冷却（秒，0~86400） | `1800` |

> 校验：概率 `0~1` 且为有限数、`PROACTIVE_MESSAGE_MIN ≤ MAX`、`ACTIVE_CHAT_INTERVAL_MIN ≤ MAX`、
> 冷却在 `0~86400` 之间；Web UI「配置」页「主动聊天」分组展示并热更新。

## Web UI

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `WEB_UI_ENABLED` | 管理后台开关 | `false` |
| `WEB_UI_HOST` / `WEB_UI_PORT` | 监听地址/端口 | `127.0.0.1` / `8080` |
| `WEB_UI_ALLOW_LAN` | 显式开关：true 时绑定 `0.0.0.0`（局域网/公网可访问；默认仅本机，开启后启动日志输出安全警告） | `false` |
| `WEB_UI_USERNAME` / `WEB_UI_PASSWORD` | 登录账号/密码（启用时必须设置） | `admin` / 空 |
| `WEB_UI_TOKEN_TTL_SECONDS` | 登录 token 有效期 | `3600` |

> ⚠️ `WEB_UI_PORT` 与 `WS_PORT` 冲突时启动直接报错——Web UI 的本地回环端口不能与 NapCat 反向 WS 端口一致。

## 人格（Persona）

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `PERSONA_DEFAULT` | 默认（兜底）人格 id（内置：`flowerie` / `atri` / `isla`） | `flowerie` |
| `MAX_PERSONA_PROMPT_LENGTH` | 单个人格 system_prompt 最大长度（字） | `8000` |
| `PERSONA_MAX_COUNT` | 自定义人格总数上限（内置不计） | `200` |
| `ADMIN_RESPONSE_RULES` | 管理员补充发言规则（每行一条；优先级：安全策略 > 人格 > 人格内置规则 > 本条，**不能覆盖安全策略**；Web UI「人格」页编辑） | 空 |

人格数据存 `data/settings.db`（`personas` / `group_persona` / `persona_global` 表），
Web UI「人格」页管理（全局 / 群聊 / 自定义）。详见 [persona.md](persona.md)。

## 群聊知识（Meme Knowledge）

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `MEME_LEARNING_ENABLED` | 每日梗总结任务总开关 | `false` |
| `MEME_KNOWLEDGE_DB_PATH` | 梗知识库（SQLite，按群隔离） | `./data/knowledge.db` |
| `MEME_SUMMARY_INTERVAL_HOURS` | 总结周期（小时） | `24` |
| `MAX_GROUP_MEMES` | 每群知识条数上限（防无限增长） | `500` |
| `MEME_BUFFER_PER_GROUP` | 每群消息缓冲上限（条） | `1000` |
| `MEME_MAX_GROUPS_PER_RUN` | 单轮总结最多处理群数（防 AI 风暴） | `20` |
| `MEME_MIN_MESSAGES_PER_SUMMARY` | 总结最少消息数（低于则跳过） | `10` |
| `MEME_MAX_SUMMARY_CANDIDATES` | 单群单轮最多写入候选梗数 | `20` |

详见 [knowledge.md](knowledge.md)。

### 如何开启

1. 编辑项目根目录的 `.env`，追加：
   ```ini
   WEB_UI_ENABLED=true
   WEB_UI_PORT=8080            # 不能与 WS_PORT(3001) 相同
   WEB_UI_USERNAME=admin
   WEB_UI_PASSWORD=你的密码      # 必填，留空会拒绝启动
   ```
2. 重启机器人：`python main.py`（或守护脚本 `bash run.sh`）
3. 浏览器打开 `http://127.0.0.1:8080/panel`（无 JS 兼容面板，手机浏览器也能用），用上面的账号密码登录

> 也可以在 `/panel` 登录页点「注册管理员账号」注册新账号（**仅系统未初始化时可用**，存 `data/settings.db`，优先于 .env）。
> **Bootstrap Lock**：系统一旦初始化（`.env` 或 `settings.db` 存在管理凭据），公开注册永久关闭；之后改账号走登录态
> 「用户状态」页（需当前密码），注销账号（需当前密码）= 显式重置回到 UNINITIALIZED 才可重新注册。

> 同一局域网内的电脑访问：在 `.env` 加 `WEB_UI_ALLOW_LAN=true`（显式开关），然后浏览器打开 `http://局域网IP:8080/panel`（请设置强密码，勿直接暴露公网）。

### 配置中心（无 JS 面板）

`/panel` 面板的**七个**页签全部**纯 HTML + CSS + 服务端渲染，零 JavaScript**：

- **配置页**：全部配置变量按多个功能分组展示（**「人格（Persona）」「群聊知识（Meme）」「插件（Plugin）」
  三组已移入对应页签的专属配置区块**），**顶部提供分类导航**
  （点击只看某一类，如 MCP / 发言设置 / 稳定性……各自一屏，避免拥挤），每组一个表单保存。
  控件按类型自动选择：bool→checkbox、int/float→number（含 min/max/step）、
  secret→password（只显示掩码，留空不覆盖）、枚举→select（日志级别/格式）、
  多行/JSON→textarea、列表→逗号分隔文本框。
- **保存与持久化**：提交后服务端校验（类型/范围/枚举/JSON/列表），通过则
  **真正写入项目根 `.env`**——原子更新（临时文件 + fsync + 替换）、保留注释与
  原有变量、正确处理空格/`#`/`=`/引号/中文/换行，并发提交有锁保护；
  同时热更新运行中的配置（部分需重启的项在页面上明确标注）。
- **外观页**：7 套内置主题（`default`/`dark`/`light`/`sakura`/`ocean`/
  `forest`/`amoled`，body class 切换 + CSS variables）、自定义背景颜色
  （`<input type="color">` + 手动输入框，支持 `#RRGGBB` / `R,G,B` / `rgb(r,g,b)`）、背景图片上传（PNG/JPEG/WEBP/GIF，≤5MB，
  服务端魔数校验、固定文件名 `background.<ext>` 存于 `data/webui/background/`，
  无路径穿越）、图片透明度（0~100%）、显示方式（cover/contain + 位置），
  以及「恢复默认主题」「删除背景图片」。
  背景颜色与图片透明度通过 CSS 渐变遮罩合成**同一视觉层**，刷新与重启均保留
  （主题/颜色/透明度存 `data/settings.db` 的 `webui_prefs` 表，图片落盘）。

> 管理账号（`WEB_UI_USERNAME` / `WEB_UI_PASSWORD`）不在配置表单中：统一走
> `/panel` 的注册页管理，密码只存 scrypt 哈希，避免明文写入 `.env`。

## 插件系统（Plugin System）

> v1.2.0 新增。受控插件运行时：目录扫描自动发现（**发现 ≠ 自动执行**，默认禁用），
> 安装 / 启用 / 权限批准 / 卸载均在 Web UI「插件」页（管理员操作）。

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `PLUGIN_DIR` | 插件目录（扫描其中的 `*/manifest.json` 自动发现） | `./plugins` |
| `PLUGIN_PROTECTION` | 保护级别 `normal`/`relaxed`/`unsafe`（只影响运行时资源限制；任何级别都不豁免 manifest 校验 / 管理员权限 / 进程隔离 / 日志 / 崩溃保护 / 资源限制 / 权限强制） | `normal` |
| `PLUGIN_MAX_COUNT` | 注册表插件总数上限（防无限增长） | `100` |
| `PLUGIN_URL_MAX_BYTES` | URL 下载插件包大小上限（字节，5MB） | `5242880` |
| `PLUGIN_URL_TIMEOUT` | URL 下载超时（秒） | `15` |
| `PLUGIN_ZIP_MAX_UNZIPPED_BYTES` | ZIP 解压后总大小上限（字节，50MB，防 Zip Bomb） | `52428800` |
| `PLUGIN_ZIP_MAX_FILES` | ZIP 包内文件数上限 | `200` |

插件支持 Python（`plugin.py`）、Node（`index.js`/`package.json`）与 JSON 声明式（`runtime=json`，`declarations`
规则，无代码执行）；运行在独立子进程（`python -I` 隔离 / `node` 子进程），stdin/stdout JSON-Lines 协议，
崩溃/超时被隔离标记 `crashed`。详见 [plugin-developer-guide.md](plugin-developer-guide.md)。

## 日志

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `LOG_LEVEL` | 日志级别 | `INFO` |
| `LOG_FORMAT` | `text`（人类可读）/ `json`（JSON lines，含 trace_id/event） | `text` |
| `CONTEXT_BACKUP_PATH` | 上下文崩溃备份（SQLite） | `./data/context_backup.db` |
| `CONTEXT_BACKUP_INTERVAL` | 备份间隔（秒） | `60` |

## 热更新说明

- **Web UI 可热更新**：模型/密钥/冷却/预算/表情包开关/MCP 开关/日志级别等（修改后立即生效）；
  主动发言概率（`PROACTIVE_MESSAGE_*` / `ACTIVE_CHAT_*`）与 `ADMIN_RESPONSE_RULES` 亦为热更新。
- **需要重启**：WS 端口、HTTP API 地址、数据库路径、监听地址、NapCat WS 模式（`NAPCAT_WS_MODE`/`NAPCAT_WS_URL`/`NAPCAT_ACCESS_TOKEN`）、
  `PLUGIN_DIR` 等 Advanced 项（UI 会提示）
