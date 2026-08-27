<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/OneBot-11-green.svg" alt="OneBot">
  <img src="https://img.shields.io/badge/DeepSeek-v4-ff69b4.svg" alt="DeepSeek">
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License">
</p>

<h1 align="center">🌸 花璃 · QQ 群聊机器人</h1>

<p align="center">
  银发灰瞳的小恶魔系青梅竹马 · DeepSeek 驱动 · NapCat OneBot11
</p>

<p align="center">
  <b>「戳我干嘛，再戳就不理你了哦」</b>
</p>

---

## 📖 简介

**花璃** 是一个基于 **DeepSeek API** 的 **QQ** 群聊机器人，设计想法源于漫画 **《童话般的你开始了恋爱猛攻》**，搭载了完整的"银发灰瞳小恶魔系青梅竹马"人设。她不仅能像普通群友一样自然聊天，还能：

- 📎 解析 **txt / pdf / docx / xlsx / csv** 文件
- 🔁 递归提取 **合并转发消息** 与 **JSON 卡片** 内容
- 🧠 自动记忆群友的 **特点与喜好**（持久化存储）
- ⚔️ 基于 **关键词 + AI 二次确认** 的引战检测
- 🎯 按群隔离的 **冷却系统**、**复读检测**、**主动聊天**

> 从 40+ 小时的 MVP 原型，到模块清晰的生产级工程 —— 花璃已经准备好陪你闹群了。

---

## ✨ 功能特性

| 功能 | 说明 |
| :--- | :--- |
| 💬 **AI 对话** | DeepSeek v4 驱动，20 字内简洁回复，小恶魔系语气（模型/API 网址环境变量可配）|
| 👁️ **识图回复** | 群里的图片与表情包不再是空消息：NapCat 图片段 → 视觉模型识图 → 自然回复；**合并转发里的每一张图也会逐张识图**（视觉模型/网址/key 环境变量可配）|
| 📎 **文件解析** | 自动识别并读取 txt / pdf / docx / xlsx / csv 内容 |
| 📦 **转发解析** | 递归提取合并转发消息中的所有文本，**并识别聊天记录里嵌套的每一张图片**（NapCat 转发内 image 段的 url → 视觉模型识图）|
| 🃏 **卡片解析** | 提取 JSON 卡片（分享链接、小程序等）的文本内容 |
| 🧠 **记忆库** | 按 `(用户, 群)` 隔离，自动记录群友偏好，**SQLite 持久化**（旧版 JSON 自动迁移）；**高相似度自动去重**，记忆内容只写客观事实不写内心戏 |
| 🔁 **复读检测** | 3 次相同消息触发复读，带冷却 |
| ⚔️ **引战检测** | 关键词 + AI 双重校验，15 分钟冷却，避免误伤（**检测模型/网址/key 可独立配置**，留空回退 DeepSeek）|
| 🎯 **冷却系统** | 用户级（5s）+ 机器人级（2s）+ 连续回复惩罚 |
| ☀️ **主动聊天** | 4~6 小时间隔，10% 概率触发，夜间静默 |
| 👆 **戳戳回复** | 被戳头像随机回复，带最近 5 条去重 |
| 📝 **消息存档** | 自动按群/日期保存所有文字消息 |
| 🛡️ **群白名单** | 可选只响应指定群聊 |

---

## 🚀 快速开始

### 📱 安卓 (Termux) 专用

由于安卓环境与 PC 不同（需绕过 `pydantic` 编译且依赖预编译库），请严格按以下步骤执行：

#### 步骤一：更换软件源（避免下载超时）
首次安装务必切换国内镜像，否则 `pkg` 可能无法连接：
```bash
termux-change-repo
```
（在界面中选择 `Tsinghua` 或 `USTC` 镜像）

#### 步骤二：安装基础环境
```bash
pkg update && pkg upgrade -y
pkg install python python-pip git -y
# 如需读取手机存储（/sdcard），执行下方命令并授权：
termux-setup-storage
```

#### 步骤三：安装项目依赖（关键步骤）
**请直接复制整条命令**，它强制从安卓专用源获取预编译包，**避免耗时 10 分钟以上的源码编译**：
```bash
pip install -r requirements.txt \
  -i https://termux-user-repository.github.io/pypi/ \
  --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --only-binary pydantic-core,pydantic
```

> **⚠️ 如果上述命令因网络问题下载失败**（提示 `github.com` 超时），请尝试：
> 1. 先安装系统自带的 yaml：`pkg install python-yaml -y`
> 2. 安装编译工具：`pkg install clang binutils rust -y`
> 3. 升级 pip 后直接编译安装（耗时约 10~20 分钟，请耐心等待）：

```bash
pip install --upgrade pip 
pip install -r requirements.txt
```


#### 步骤四：运行项目
```bash
cd ~/Flowerie_bot #请替换为你的实际项目路径
# 推荐用守护脚本启动：崩溃/被杀后 5 秒自动重启，配合上下文备份无缝恢复
bash run.sh
# 想直接前台跑（崩了不会自动重启）也可以用：
# python main.py
```

### 💻 Linux / macOS

### 1. 安装Python

**Ubuntu/Debian：**
```bash
sudo apt update
sudo apt install python3 python3-pip git -y
```

**macOS（Homebrew）：**

```bash
brew install python3 git
# 验证
python3 --version
```

### 2. 克隆项目

```bash
git clone https://github.com/lingcat521/Flowerie_bot.git 
cd Flowerie_bot
```

### 3. 配置环境变量

```bash
cp .env.example .env
vim .env
```

必填项：

```ini
DEEPSEEK_API_KEY=sk-你的DeepSeek密钥
BOT_QQ=你的机器人QQ号
```

### 4. 安装依赖

```bash
pip install -r requirements.txt
# 开发/测试额外依赖（可选）：
pip install -r requirements-dev.txt
```

### 5. 创建数据目录

```bash
mkdir -p data logs
```

### 6. 启动 NapCat

确保 NapCat 已开启 反向 WebSocket，地址为 127.0.0.1:3001。

### 7. 启动花璃

```bash
# 推荐用守护脚本启动（崩溃/被杀自动重启，配合上下文备份无缝恢复）
bash run.sh
# 或者直接前台跑：
# python main.py
```

看到以下日志即为启动成功：

```
[INFO] 记忆库已加载，共 0 个用户-群组合
[INFO] Starting WebSocket server on 127.0.0.1:3001
[INFO] WebSocket server started, waiting for connections...
```

---

## ⚙️ 配置说明

| 环境变量 | 说明 | 默认值 |
| :--- | :--- | :--- |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥（**必填**） | — |
| `DEEPSEEK_API_URL` | DeepSeek API 地址（群聊模型走这里） | `https://api.deepseek.com/chat/completions` |
| `DEEPSEEK_MODEL` | DeepSeek 群聊模型 | `deepseek-v4-flash` |
| `BOT_QQ` | 机器人 QQ 号（**必填**） | — |
| `WS_HOST` | WebSocket 监听地址 | `127.0.0.1` |
| `WS_PORT` | WebSocket 监听端口 | `3001` |
| `HTTP_API_BASE` | NapCat HTTP API 地址 | `http://127.0.0.1:3000` |
| `WS_TOKEN` | 反向 WS 鉴权 token（可选；空=不鉴权，设置后 NapCat 需带 `Authorization: Bearer <token>` 或 `?access_token=<token>`） | — |
| `ONLY_REPLY_WHEN_AT` | 仅回复 @ 消息 | `false` |
| `MAX_REPLY_LENGTH` | 最大回复长度 | `40` |
| `USER_COOLDOWN` | 用户冷却（秒） | `5` |
| `BOT_COOLDOWN` | 机器人全局冷却（秒） | `2` |
| `ALLOWED_GROUP_IDS` | 白名单群号（逗号分隔，留空=所有群） | — |
| `TOXIC_GROUP_IDS` | 引战检测群号（逗号分隔） | — |
| `TOXIC_API_KEY` / `TOXIC_API_URL` / `TOXIC_MODEL` | 引战检测 AI 独立配置（留空回退用 DeepSeek） | — |
| `VISION_MODEL` | 识图视觉模型 | `deepseek-v4-flash-vision-exp` |
| `VISION_API_URL` / `VISION_API_KEY` | 视觉模型网址/密钥（留空回退用 DeepSeek） | — |
| `VISION_TIMEOUT` | 识图超时（秒） | `30` |
| `VISION_FORWARD_IMAGES` | 是否识别合并转发（聊天记录）里的图片（false 省视觉 token） | `false` |
| `EVENT_PROCESS_TIMEOUT` | 单条消息处理超时（秒），防一条慢消息堵住全群 | `90` |
| `MAX_CONCURRENT_AI` | 同时处理消息的并发上限（AI/识图额度） | `3` |
| `CONTEXT_BACKUP_PATH` | 上下文崩溃备份库路径（SQLite，意外去世后重启自动恢复最近 50 条） | `./data/context_backup.db` |
| `CONTEXT_BACKUP_INTERVAL` | 上下文备份间隔（秒） | `60` |
| `LOG_FORMAT` | 日志格式：`text`（开发人类可读）/ `json`（生产 JSON lines，含 trace_id/event） | `text` |
| `AI_MAX_RETRIES` | 单次逻辑 AI 操作最大重试次数（每次尝试都单独过预算闸门） | `3` |
| `MEMORY_PATH` | 记忆库路径（SQLite；旧 `memory.json` 首次启动自动迁移到同目录 `.db`） | `./data/memory.db` |
| `ARCHIVE_ENABLED` | 是否启用消息存档（默认关，隐私优先） | `false` |
| `ARCHIVE_RETENTION_DAYS` / `ARCHIVE_MAX_SIZE_MB` | 存档保留天数 / 每群大小上限 | `0` / `0` |
| `ARCHIVE_BASE_DIR` | 消息存档路径 | `./data/archive` |
| `MEMORY_TTL_DAYS` | 用户原话记忆保留天数（0=永久） | `0` |
| `MODEL_MEMORY_TTL_DAYS` | AI 推断记忆保留天数（低信任，默认 30 天过期） | `30` |
| `AUDIT_LOG_PATH` | 记忆写入/删除审计日志 | `./data/audit.log` |
| `MAX_FILE_TEXT_CHARS` | 文件解析文本最大字符数 | `8000` |
| `MAX_FILE_DOWNLOAD_BYTES` | 文件解码字节兜底上限 | `2MB` |
| `MAX_PDF_PAGES` / `MAX_EXCEL_CELLS` / `MAX_CSV_ROWS` | 文件解析规模上限 | `100` / `50000` / `10000` |
| `MAX_AI_INPUT_CHARS` | 单次 AI 输入最大字符数 | `8000` |
| `MAX_IMAGES_PER_MESSAGE` | 单条消息最多识图张数 | `10` |
| `MAX_FORWARD_DEPTH` | 嵌套转发最大展开深度 | `5` |
| `MAX_FORWARD_MESSAGES` / `MAX_FORWARD_NODES` / `MAX_FORWARD_FETCHES` | 转发解析消息/节点/拉取预算（防套娃 DoS） | `100` / `500` / `20` |
| `DAILY_AI_CALL_BUDGET` | 全局每日 AI 调用上限（0=不限） | `1000` |
| `GROUP_DAILY_AI_CALL_BUDGET` | 每群每日 AI 调用上限（0=不限） | `300` |
| `USER_AI_CALL_MIN_INTERVAL` | 同一用户两次 AI 回复最小间隔（秒，0=不限） | `10` |
| `BUDGET_EXHAUSTED_NOTICE` | 额度用尽时群里提示（每天每群一次） | `true` |
| `IMAGE_ALLOWED_HOSTS` | 图片主机白名单（空=放行所有 http/https） | — |
| `MEMORY_DISABLED_GROUPS` | 禁用记忆的群（逗号分隔） | — |
| `ADMIN_QQ_IDS` | 管理员 QQ（可执行 /memory_clear /memory_dump） | — |

完整配置请参考 .env.example。

### 🛡️ 指令菜单（用户数据控制权）

| 命令 | 权限 | 作用 |
|---|---|---|
| `/help` | 所有人 | 显示指令菜单 |
| `/memory` | 所有人 | 查看花璃记住了自己什么 |
| `/forget 关键词` | 所有人 | 删除包含关键词的自己的记忆 |
| `/forget_me` | 所有人 | 清空花璃对自己的全部记忆 |
| `/memory_clear` | 管理员（ADMIN_QQ_IDS） | 清空本群所有记忆 |
| `/memory_dump` | 管理员（ADMIN_QQ_IDS） | 导出本群全部记忆 |

> 💡 **识图说明**：花璃现在能"看"群里的图片和表情包（NapCat OneBot11 的 `image` 段带 `url`）。识图走独立的视觉模型（默认 `deepseek-v4-flash-vision-exp`），把图片转成一句描述再自然回复；视觉 key 留空时复用 DeepSeek 的 key。
>
> 💡 **记忆去重**：记忆库现在会对高相似度的记录去重（完全相同/互为子串/相似度 ≥0.85 都不再重复写入），并约束 AI 记忆内容只写客观事实、不写内心戏，避免记忆卡像"喜欢打三角州 已退游 / 喜欢打三角洲 已退游 好家伙…"这样越积越乱。

---

## 🗄️ 数据存储（SQLite）

花璃的全部持久化数据都存放在 `data/` 目录下的 **SQLite 数据库**（Python 标准库 `sqlite3`，零额外依赖）：

| 文件 | 内容 |
| :--- | :--- |
| `data/memory.db` | 记忆库：`memory` 表（每条记忆一行，含来源/时间/置信度元数据）+ `memory_kv` 表 |
| `data/context_backup.db` | 上下文崩溃备份：每群最近 50 条聊天记录 + 最近 200 条已处理消息 id |
| `data/audit.log` | 记忆写入/删除审计日志（文本） |
| `data/archive/` | 消息存档（`ARCHIVE_ENABLED=true` 时启用，默认关） |

- **备份/迁移**：直接复制 `.db` 文件即可完整备份（建议先停止机器人再复制）。数据库是事务性写入，崩溃/断电不会损坏已有数据。
- **旧版 JSON 自动迁移**：从旧版本升级无需任何操作——首次启动时若检测到同目录存在 `memory.json` / `context_backup.json`，会自动导入 SQLite（原文件改名为 `*.json.migrated` 保留备份）。`.env` 里仍写旧的 `.json` 路径也兼容，会自动映射到同目录 `.db`。

---

## 📁 项目结构

```
Flowerie_bot/
├── .env.example          # 环境变量模板
├── .gitignore            # Git 忽略配置
├── requirements.txt      # Python 依赖
├── README.md             # 项目文档
├── main.py               # 程序入口
├── run.sh                # 守护脚本（崩溃自动重启）
├── data/                 # 运行时数据（memory.db 记忆库、context_backup.db 上下文备份、存档、审计日志）
├── logs/                 # 日志文件
└── src/
    ├── __init__.py
    ├── config.py         # Pydantic 配置管理
    ├── models.py         # 数据模型（GroupState, GlobalState 等）
    ├── utils/
    │   ├── logging_setup.py # 标准 logging 基础设施（text/JSON 格式、脱敏、trace_id 注入）
    │   ├── trace.py         # trace_id（contextvars，消息链路唯一标识）
    │   ├── metrics.py       # 内部 MetricsRegistry（snapshot / Prometheus 文本导出）
    │   ├── task_manager.py  # 后台任务统一管理（注册/跟踪/优雅关闭）
    │   └── logger.py        # 兼容入口
    ├── repositories/
    │   ├── base.py           # MemoryRepository 抽象 + MemoryNote
    │   └── sqlite_repository.py # SQLite 实现（业务层不感知 SQL）
    ├── services/
    │   ├── ai_client.py      # DeepSeek API + 视觉识图封装（429 限流退避）
    │   ├── memory_manager.py # 记忆库 CRUD（原子写入 + 相似度去重 + 错别字容忍）
    │   ├── file_parser.py    # 文件/转发/卡片解析（转发内图片 url + 嵌套转发展开）
    │   └── sender.py         # HTTP 消息发送（带重试）
    └── core/
        ├── policy_engine.py    # 策略门面（聚合各职责管理器，对外 API 不变）
        ├── context_manager.py  # 上下文读写 / 接话概率 / 重复回复 / 崩溃备份（含消息去重 id）
        ├── cooldown_manager.py # 用户与机器人冷却 / 连续回复惩罚
        ├── repeat_detector.py  # 复读检测
        ├── memory_parser.py    # 记忆指令解析 / 强制记忆触发
        ├── poke_manager.py     # 戳戳回复去重
        ├── active_chat_manager.py # 主动聊天决策（cooldown 由门面注入）
        ├── message_assembler.py # 消息组装：文本/识图/转发/卡片/文件/存档
        ├── command_handler.py   # 指令处理：/help /memory /forget /forget_me + 管理员指令
        ├── budget_manager.py    # AI 预算：全局+每群+每用户限速 + 额度用尽提示
        ├── sanitizer.py         # 代码层防注入：不可信内容清洗 + 记忆写入校验
        ├── message_router.py   # 事件分发与消息处理（流程编排，不再是大杂烩）
        └── websocket_server.py # WebSocket 连接管理（单连接守卫 + 优雅停机 + 超时 + 并发）
tests/
    ├── test_memory_parser.py    # 记忆指令/强制记忆单元测试
    ├── test_logging_trace.py    # trace_id 并发隔离 / 日志脱敏 / metrics
    ├── test_task_manager.py     # 后台任务失败捕获 / 优雅关闭
    ├── test_repository.py       # 存储仓库 CRUD / 并发访问
    ├── test_ai_reliability.py   # AI 超时 / 重试 / 预算不绕过
    ├── test_graceful_shutdown.py # Router 后台任务生命周期
    └── test_config_validation.py # 启动配置校验
    ├── test_memory_manager.py   # 记忆去重（含错别字容忍）单元测试
    ├── test_cooldown_manager.py # 冷却逻辑单元测试
    ├── test_context_manager.py  # 上下文备份/恢复单元测试
    ├── test_sanitizer.py        # 防注入清洗/记忆校验单元测试
    └── test_url_and_contradiction.py # SSRF 图片 URL 闸门 / 记忆矛盾替换单元测试
```

---

## 🔐 安全说明

- **WS 鉴权（可选）**：反向 WS 默认只监听 `127.0.0.1`（本机）。若修改 `WS_HOST` 对外暴露端口，务必设置 `WS_TOKEN`，否则任何能连上端口的人都能伪造事件驱动花璃发言。
- **图片/文件下载**：图片下载只允许 `http/https` 与 `data:image/`，带大小上限与 MIME 嗅探；`IMAGE_ALLOWED_HOSTS` 可进一步收紧到白名单主机。**已知信任边界**：NapCat 本地图片走 `127.0.0.1` loopback，因此 loopback 始终放行。
- **记忆隐私**：记忆按 `(用户, 群)` 隔离；代码层闸门会拒绝含 QQ 号（5~12 位）、超长、指令句式的记忆写入；`MEMORY_DISABLED_GROUPS` 群完全不写记忆；用户可用 `/memory /forget /forget_me` 查看/删除自己的记忆。
- **AI 预算**：全局每日 / 每群每日 / 每用户限速三层闸门，所有消耗 AI 的路径（聊天/引战检测/主动聊天）统一过闸，防 API 额度被刷爆。

---

## 🧠 人设设定

花璃是一位银发灰瞳、留着不对称长鬓角的 17 岁高中女生，寄住在青梅竹马「七君」家里，早已芳心暗许。（设计想法源于漫画《童话般的你开始了恋爱猛攻》）

· 高攻低防：对熟人话题发起猛攻，被反撩就红温
· 说话风格：20 字以内，用空格代替标点，极少用 emoji
· 偶尔接梗：能听懂群友黑话，但从不主动玩梗
· 粘人但嘴硬：喜欢七君，但绝不承认自己是恋爱脑

详细提示词见 src/services/ai_client.py 中的 system_prompt。

---

## 🔧 工程化能力

- **结构化日志**：基于标准库 `logging`（不再依赖 loguru）。`LOG_FORMAT=json` 时输出 JSON lines（ts/level/logger/trace_id/event/msg + 结构化字段）；`text` 为开发环境人类可读格式。日志自动脱敏（API Key / Bearer token / GitHub token 等），不记录完整 prompt 与完整模型回复，AI 请求只记录 model / latency / retry / token usage。
- **trace_id**：每条 WS 事件进入时生成唯一 trace_id（contextvars），贯穿 Router→Policy→Budget→Context→Memory→AI→Send 全链路，并发消息互不污染；日志中通过 `trace=` 字段（text 格式）或 `trace_id`（JSON 格式）串联。
- **Metrics**：内置 `MetricsRegistry`（零外部依赖、线程安全、不抛异常），统计 received/processed/rejected、AI 请求/成功/失败/延迟/重试、记忆读写、WS 重连、发送失败等；`export_text()` 输出 Prometheus 文本格式，可挂到任意采集器。
- **存储抽象**：`MemoryRepository` 接口 + `SQLiteMemoryRepository` 实现，MemoryManager 只负责业务规则（去重/矛盾替换/TTL/审计），未来可替换 Postgres/Redis 实现而不改业务层。
- **后台任务管理**：`BackgroundTaskManager` 统一注册/跟踪所有后台任务（主动聊天、上下文备份），任务异常记录 `task_failed` 日志不会静默死亡；`shutdown()` 取消并等待全部任务，超时强杀。
- **AI 可靠性**：`AI_MAX_RETRIES` 控制重试上限；指数退避（429 时 8/16/30s 封顶，其他 1/2/4s）；4xx 业务错误不重试（避免无效重试与重复扣费）；每次尝试单独过 BudgetManager（retry 永远不绕过额度）。
- **CI**：GitHub Actions 自动执行 ruff 检查 + pytest（Python 3.9 / 3.12 矩阵）。

本地运行测试：

```bash
pip install -r requirements-dev.txt
pytest
```

---

## 🛠️ 常见问题

Q: 提示 ModuleNotFoundError: No module named 'xxx'

```bash
# 1. 确认依赖已安装
pip install -r requirements.txt

# 2. 如果仍然报错，尝试重新安装
pip install --upgrade --force-reinstall -r requirements.txt
```

Q: 提示 FileNotFoundError: ./data/memory.db

```bash
mkdir -p data logs
```

> 其实无需手动创建：`data/` 目录会在首次启动时自动生成（记忆库 / 上下文备份库 / 审计日志）。

Q: WebSocket 连接失败

· 确认 NapCat 已启动
· 确认反向 WS 地址为 127.0.0.1:3001
· 检查防火墙/端口占用

Q: DeepSeek API 返回 401

· 检查 .env 中的 DEEPSEEK_API_KEY 是否正确
· 确认 API Key 未过期且有余额

Q: 安卓上安装依赖时 pydantic-core 编译失败 / 耗时太久

A: 请严格按照上方 "安卓 (Termux) 专用" 步骤执行，使用 `--only-binary` 强制使用预编译轮子。如果仍失败（如网络问题导致无法下载轮子），可安装编译工具后自行编译（耗时较长）：`pkg install clang binutils rust -y && pip install --upgrade pip && pip install -r requirements.txt`

---

## 📝 TODO

- [ ] 你来提 Issue

---

## 📄 License

[MIT](LICENSE) © 2026 [铃樱（lingcat521）](https://github.com/lingcat521)

---

## 🙏 致谢

- [NapCat](https://napcat.qq.com) — 强大的 QQ 机器人框架
- [DeepSeek](https://deepseek.com) — 聪明又便宜的 AI 模型
- [OneBot 11](https://onebot.dev) — 统一的机器人协议标准
---

<p align="center">
  <b>Made with ❤️ by 铃樱</b>
</p>
