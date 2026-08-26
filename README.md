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
| 🧠 **记忆库** | 按 `(用户, 群)` 隔离，自动记录群友偏好，持久化 JSON；**高相似度自动去重**，记忆内容只写客观事实不写内心戏 |
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
| `CONTEXT_BACKUP_PATH` | 上下文崩溃备份路径（意外去世后重启自动恢复最近 50 条） | `./data/context_backup.json` |
| `CONTEXT_BACKUP_INTERVAL` | 上下文备份间隔（秒） | `60` |
| `MEMORY_PATH` | 记忆库路径 | `./data/memory.json` |
| `ARCHIVE_BASE_DIR` | 消息存档路径 | `./data/archive` |
| `MEMORY_TTL_DAYS` | 记忆保留天数（0=永久） | `0` |
| `AUDIT_LOG_PATH` | 记忆写入/删除审计日志 | `./data/audit.log` |
| `MAX_FILE_TEXT_CHARS` | 文件解析文本最大字符数 | `8000` |
| `MAX_PDF_PAGES` / `MAX_EXCEL_CELLS` / `MAX_CSV_ROWS` | 文件解析规模上限 | `100` / `50000` / `10000` |
| `MAX_AI_INPUT_CHARS` | 单次 AI 输入最大字符数 | `8000` |
| `MAX_IMAGES_PER_MESSAGE` | 单条消息最多识图张数 | `10` |
| `MAX_FORWARD_DEPTH` | 嵌套转发最大展开深度 | `5` |
| `DAILY_AI_CALL_BUDGET` | 每日 AI 调用上限（0=不限） | `0` |
| `MEMORY_DISABLED_GROUPS` | 禁用记忆的群（逗号分隔） | — |
| `ADMIN_QQ_IDS` | 管理员 QQ（可执行 /memory_clear /memory_dump） | — |

完整配置请参考 .env.example。

### 🛡️ 记忆管理命令（用户数据控制权）

| 命令 | 权限 | 作用 |
|---|---|---|
| `/memory` | 所有人 | 查看花璃记住了自己什么 |
| `/forget 关键词` | 所有人 | 删除包含关键词的自己的记忆 |
| `/forget_me` | 所有人 | 清空花璃对自己的全部记忆 |
| `/memory_clear` | 管理员（ADMIN_QQ_IDS） | 清空本群所有记忆 |
| `/memory_dump` | 管理员（ADMIN_QQ_IDS） | 导出本群全部记忆 |

> 💡 **识图说明**：花璃现在能"看"群里的图片和表情包（NapCat OneBot11 的 `image` 段带 `url`）。识图走独立的视觉模型（默认 `deepseek-v4-flash-vision-exp`），把图片转成一句描述再自然回复；视觉 key 留空时复用 DeepSeek 的 key。
>
> 💡 **记忆去重**：记忆库现在会对高相似度的记录去重（完全相同/互为子串/相似度 ≥0.85 都不再重复写入），并约束 AI 记忆内容只写客观事实、不写内心戏，避免记忆卡像"喜欢打三角州 已退游 / 喜欢打三角洲 已退游 好家伙…"这样越积越乱。

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
├── data/                 # 运行时数据（记忆库、存档、上下文备份）
├── logs/                 # 日志文件
└── src/
    ├── __init__.py
    ├── config.py         # Pydantic 配置管理
    ├── models.py         # 数据模型（GroupState, GlobalState 等）
    ├── utils/
    │   └── logger.py     # loguru 日志配置
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
        ├── message_router.py   # 事件分发与消息处理（流程决策）
        └── websocket_server.py # WebSocket 连接管理（单连接守卫 + 优雅停机 + 超时 + 并发）
tests/
    ├── test_memory_parser.py    # 记忆指令/强制记忆单元测试
    ├── test_memory_manager.py   # 记忆去重（含错别字容忍）单元测试
    ├── test_cooldown_manager.py # 冷却逻辑单元测试
    └── test_context_manager.py  # 上下文备份/恢复单元测试
```

---

## 🧠 人设设定

花璃是一位银发灰瞳、留着不对称长鬓角的 17 岁高中女生，寄住在青梅竹马「七君」家里，早已芳心暗许。（设计想法源于漫画《童话般的你开始了恋爱猛攻》）

· 高攻低防：对熟人话题发起猛攻，被反撩就红温
· 说话风格：20 字以内，用空格代替标点，极少用 emoji
· 偶尔接梗：能听懂群友黑话，但从不主动玩梗
· 粘人但嘴硬：喜欢七君，但绝不承认自己是恋爱脑

详细提示词见 src/services/ai_client.py 中的 system_prompt。

---

## 🛠️ 常见问题

Q: 提示 ModuleNotFoundError: No module named 'xxx'

```bash
# 1. 确认依赖已安装
pip install -r requirements.txt

# 2. 如果仍然报错，尝试重新安装
pip install --upgrade --force-reinstall -r requirements.txt
```

Q: 提示 FileNotFoundError: ./data/memory.json

```bash
mkdir -p data logs
```

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
