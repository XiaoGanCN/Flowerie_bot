# 花璃 · QQ 群聊机器人

<p align="center">
  <b>银发灰瞳的小恶魔系青梅竹马 · DeepSeek 驱动 · NapCat OneBot11</b>
</p>

<p align="center">
  <b>「戳我干嘛，再戳就不理你了哦」</b>
</p>

<div align="center">
  
[![GitHub Tag](https://img.shields.io/github/v/tag/lingcat521/Flowerie_bot)](https://github.com) [![Build Flowerie_bot](https://github.com/lingcat521/Flowerie_bot/actions/workflows/compiler.yml/badge.svg)](https://github.com/lingcat521/Flowerie_bot/actions/workflows/compiler.yml)
[![Acceptance](https://github.com/XiaoGanCN/Flowerie_bot/actions/workflows/acceptance.yml/badge.svg)](https://github.com/XiaoGanCN/Flowerie_bot/actions/workflows/acceptance.yml)

</div>

---
## 这是什么

**花璃** 是一个基于 **DeepSeek API** 的 **QQ 群聊机器人**：像真实群友一样聊天、识图、看转发、记记忆、被戳会回应，还能自定义人格、发表情包、用 MCP 工具上网查信息，并且可以通过 Web UI 管理配置（当前版本 **v1.4.0**）。

## 功能

| 功能 | 说明 |
| :--- | :--- |
| 💬 AI 对话 | DeepSeek 驱动，小恶魔系人设，@ 或群聊接话 |
| 👁️ 识图 | 图片/表情包/转发内图片，视觉模型描述后自然回复 |
| 📦 转发/卡片解析 | 合并转发递归展开（含图片）、JSON 卡片 |
| 🧠 记忆库 | 按用户×群隔离，SQLite 持久化，自动去重，用户可查/删 |
| 🎭 人格系统（Persona） | 全局 / 群聊 / 自定义三级人格，内置花璃 + 亚托莉（ATRI）+ 艾拉（Isla）三套官方预设，Web UI 管理 |
| 💬 群聊梗知识（Meme） | 每群独立梗/黑话知识库，按消息命中注入，24h 批量总结 + MCP 辅助检索 |
| 🎭 自定义 Prompt | 全局 + 群聊两级人格补充（`/prompt` 命令，管理员可改） |
| 🗣️ 发言规则配置化 | 说话风格规则归属各 Persona；管理员可另加 `ADMIN_RESPONSE_RULES` 补充（不覆盖安全策略） |
| 🎲 主动发言概率配置化 | `PROACTIVE_MESSAGE_*` 上下文随机回复概率 + `ACTIVE_CHAT_*` 主动聊天循环，全部可配置 |
| 🧩 插件系统（Plugin System v1） | 受控插件运行时：Python / Node / JSON 声明式插件，独立子进程 + 权限批准 + 保护级别 |
| 🔌 NapCat WebSocket | 正向 / 反向二选一（`NAPCAT_WS_MODE`），forward 支持鉴权 token + 断线重连 |
| 🖼️ 表情包 | 目录扫描 + Vision 索引缓存，模型按语境选择发送 |
| 🔧 MCP 工具 | 外部工具调用（如搜索），插件式多 server + 工具白名单 + 独立熔断 |
| ⚔️ 引战检测 | 关键词 + AI 双重确认 |
| 🎯 冷却/预算 | 用户/机器人冷却、全局+群+用户三层 AI 预算、复读检测、主动聊天 |
| 🖥️ Web UI | 管理后台：配置/人格/群聊知识/外观/日志/用户状态/插件，全零 JS，热更新 |
| 🛡️ 安全 | SSRF 防护、Prompt 注入多层防线、知识防污染、日志脱敏、双层熔断、Web UI 注册 Bootstrap Lock、插件权限强制 |

## 快速开始

### 环境要求

- Python 3.9+（Linux / macOS / Termux）
- NapCat（或任意 OneBot11 实现），开启反向 WebSocket
- DeepSeek API Key

### 安装

```bash
git clone https://github.com/lingcat521/Flowerie_bot.git
cd Flowerie_bot
pip install -r requirements.txt
cp .env_example .env        # 然后编辑 .env
```

> 📱 **安卓 / Termux 用户**  
> 若在手机上（Termux）安装，请勿使用上述步骤直接装依赖——安卓环境需**绕过 `pydantic` 编译**并依赖预编译库，直接安装会长时间源码编译甚至失败。请务必查看专用安装文档：**[📱 安卓 (Termux) 专用安装](docs/install-termux.md)**。

### 配置（必填两项）

```ini
DEEPSEEK_API_KEY=sk-你的密钥
BOT_QQ=你的机器人QQ号
```

### 启动

```bash
# 推荐：守护脚本（崩溃自动重启）
bash run.sh

# 或前台运行
python main.py
```

启动成功看到：

```
OneBot WebSocket connected
```

## 配置

完整配置见 [docs/configuration.md](docs/configuration.md)，常用项：

| 变量 | 说明 | 默认 |
| :--- | :--- | :--- |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | DeepSeek 密钥 / 模型 | 必填 / `deepseek-v4-flash` |
| `BOT_QQ` / `BOT_NICKNAME` | 机器人 QQ / 昵称 | 必填 / 花璃 |
| `WS_PORT` / `HTTP_API_BASE` | 反向 WS 端口 / NapCat HTTP 地址 | `3001` / `http://127.0.0.1:3000` |
| `STICKER_DIR` / `STICKER_ENABLED` | 表情包目录 / 开关 | 空 / `false` |
| `MCP_ENABLED` / `MCP_SERVER_URL` / `MCP_SERVERS` / `MCP_ALLOWED_TOOLS` / `MCP_ALLOWED_HOSTS` | MCP 开关 / 单 server 地址 / 多 server 列表(JSON，插件式) / 工具白名单（留空=放行所有）/ 本地·内网主机白名单 | `false` / 空 / 空 / 空 / 空 |
| `PERSONA_DEFAULT` / `MAX_PERSONA_PROMPT_LENGTH` | 默认人格 id（兜底）/ 人格 system_prompt 长度上限 | `flowerie` / `8000` |
| `ADMIN_RESPONSE_RULES` | 管理员补充发言规则（每行一条；优先级：安全策略 > 人格 > 人格内置规则 > 本条，不覆盖安全策略） | 空 |
| `PROACTIVE_MESSAGE_MIN/MAX/_BASE/_USER_BOOST/_SINGLE_USER/_SHORT_MESSAGE/_EMPTY_CONTEXT/_BOT_MULTIPLIER` | 上下文随机回复概率（详见 configuration.md） | `0.01`/`0.05`/`0.03`/`0.01`/`0.02`/`0.02`/`0.02`/`0.3` |
| `ACTIVE_CHAT_PROBABILITY` / `ACTIVE_CHAT_INTERVAL_MIN/MAX_SECONDS` / `ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS` | 主动聊天循环概率与间隔/冷却 | `0.10` / `5`/`10` / `1800` |
| `NAPCAT_WS_MODE` / `NAPCAT_WS_URL` / `NAPCAT_ACCESS_TOKEN` | NapCat WS 模式（`reverse`/`forward`）/ forward 地址 / forward 鉴权 token | `reverse` / 空 / 空 | · `NAPCAT_WS_AUTH_MODE`（header/query 互斥鉴权）
| `PLUGIN_DIR` / `PLUGIN_PROTECTION` / `PLUGIN_MAX_COUNT` / `PLUGIN_URL_MAX_BYTES` / `PLUGIN_URL_TIMEOUT` / `PLUGIN_ZIP_MAX_UNZIPPED_BYTES` / `PLUGIN_ZIP_MAX_FILES` | 插件系统：目录 / 保护级别 / 总数上限 / URL 下载大小上限 / 超时 / 解压后总大小上限 / 文件数上限 | `./plugins` / `normal` / `100` / `5242880` / `15` / `52428800` / `200` |
| `MEME_LEARNING_ENABLED` / `MEME_SUMMARY_INTERVAL_HOURS` / `MAX_GROUP_MEMES` | 群聊梗知识学习开关 / 总结周期（小时）/ 每群知识条数上限 | `false` / `24` / `500` |
| `WEB_UI_ENABLED` / `WEB_UI_PORT` / `WEB_UI_USERNAME` / `WEB_UI_PASSWORD` | Web UI 开关 / 端口 / 登录账号 / 密码 | `false` / `8080` / `admin` / 空 |
| `LOG_FORMAT` | 日志格式 `text`/`json` | `text` |

> ⚠️ `WEB_UI_PORT` 不能与 `WS_PORT` 相同（端口冲突时启动会报错）。

## 指令

| 命令 | 权限 | 作用 |
| :--- | :--- | :--- |
| `/help` | 所有人 | 指令菜单 |
| `/memory` / `/forget 关键词` / `/forget_me` | 所有人 | 查看/删除自己的记忆 |
| `/prompt show` | 所有人 | 查看当前生效 Prompt |
| `/prompt set <内容>` / `/prompt reset` | 管理员 | 设置/重置全局 Prompt |
| `/prompt group set <内容>` / `/prompt group reset` | 管理员 | 设置/重置本群 Prompt |
| `/memory_clear` / `/memory_dump` | 管理员 | 清空/导出本群记忆 |

## Web UI

默认关闭。启用后访问 `http://127.0.0.1:8080/panel`（无 JS 兼容面板，手机浏览器也能用）
用 `WEB_UI_USERNAME` / `WEB_UI_PASSWORD` 登录。

面板七个页签，全部纯 HTML + CSS + 服务端渲染，**零 JavaScript**：

- **配置**：全部配置变量按功能分组（fieldset 表单），
  bool/int/secret/文本/列表/JSON 各有对应控件（checkbox/number/password/textarea/select）；
  每组独立保存，修改**真正写入项目根 `.env`**（原子更新、保留注释与原有变量），
  重启后由 pydantic-settings 自动读取；Secret 只显示掩码、留空不覆盖
- **人格**：全局人格设置、人格库 CRUD（创建/编辑/删除/设为全局）、群聊人格绑定与解除、
  人格配置（`PERSONA_*`）+ 自定义人格 vs 自定义 Prompt 区别说明
- **群聊知识**：输入群号查看该群梗知识，搜索/新增/编辑/删除/清空 + 知识配置（`MEME_*`），严格按群隔离
- **外观**：7 套内置主题（默认/深色/浅色/Sakura/Ocean/Forest/AMOLED）、
  自定义背景颜色、背景图片上传（PNG/JPEG/WEBP/GIF，≤5MB，魔数校验，持久化到
  `data/webui/background/`）、图片透明度、显示方式（cover/contain + 位置）、
  恢复默认主题 / 删除背景图片
- **日志**：最近 200 条运行日志
- **用户状态**：当前管理员与凭据来源、修改登录账号、注销账号（仅清账号密码，其他配置不动）、
  服务器状态（平台/内存/CPU负载）、MCP 工具状态、API 厂商连接状态（DeepSeek/视觉/引战）
- **插件**：插件系统（Plugin System v1）管理：保护措施开关（normal/relaxed/unsafe）、插件列表、上传 ZIP / URL 安装、刷新扫描、启用（含权限批准）、禁用、卸载、插件系统配置

完整功能指南（配置中心 / 人格管理 / 群聊知识 / 主题美化 / MCP 卡片管理 / 插件管理 / 安全）见 **[docs/web-ui.md](docs/web-ui.md)**；
人格系统设计见 [docs/persona.md](docs/persona.md)，梗知识层见 [docs/knowledge.md](docs/knowledge.md)；
插件开发（Plugin API）见 **[docs/plugin-developer-guide.md](docs/plugin-developer-guide.md)**，安全模型见 **[docs/security.md](docs/security.md)**；
变量说明见 [docs/configuration.md](docs/configuration.md)。

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

> 同一局域网内的电脑访问：在 `.env` 加 `WEB_UI_ALLOW_LAN=true`（显式开关，绑定 0.0.0.0 并输出安全警告），然后浏览器打开 `http://局域网IP:8080/panel`（请设置强密码，勿直接暴露公网）。

## MCP

默认关闭。配置 `MCP_ENABLED=true` + `MCP_SERVER_URL`（或插件式多 server：`MCP_SERVERS` JSON，可自行添加任意数量的 MCP 服务，支持本地/内网地址）后，模型可调用白名单内的工具获取实时信息。群聊梗知识的每日总结也会在需要时通过 MCP 检索验证新梗。详见 [docs/mcp.md](docs/mcp.md)。

## Persona（人格系统）

内置三套官方人格：**花璃**（默认）、**亚托莉（ATRI）** 与 **艾拉（Isla）**；管理员可创建完全独立的自定义人格。
人格优先级：**群聊人格 > 全局人格 > 内置默认**，切换人格不影响记忆与上下文。
Web UI「人格」页管理；详细设计见 [docs/persona.md](docs/persona.md)。

## 插件系统（Plugin System v1）
插件开发支持 **SDK 模式**（推荐）与经典声明式模式：SDK 提供统一 Event /
Message / Matcher / Permission 与 Bot Adapter 分层，插件**不接触 OneBot payload**。
最小示例：`@command("hello") async def hello(event): await event.reply("你好")`

- [SDK 三层架构与 API](docs/sdk.md)
- [API 概览](docs/api.md)
- [插件开发](docs/plugins.md)
- [插件开发指南（完整）](docs/plugin-developer-guide.md)


受控插件运行时：插件以**独立子进程**运行（Python / Node），或为**进程内声明式规则**（JSON，无代码执行），
通过 stdin/stdout JSON-Lines 协议与 Flowerie 通信，由 PermissionManager 强制检查动作权限。
安装途径：Web UI 上传 ZIP / URL 下载（SSRF 防护 + 大小限制），或放入 `plugins/` 目录自动发现
（**发现 ≠ 自动执行**，默认 disabled，须管理员启用并批准权限）。
保护级别 `PLUGIN_PROTECTION`（`normal`/`relaxed`/`unsafe`）只影响运行时限制，**任何级别都不豁免**
权限检查 / 进程隔离 / 日志 / 崩溃保护 / 资源限制 / manifest 校验 / 管理员权限。
详细开发文档见 **[docs/plugin-developer-guide.md](docs/plugin-developer-guide.md)**。

## 群聊梗知识（Meme Knowledge）

每个群拥有**完全隔离**的梗/黑话知识库：消息命中时只注入相关词条（不可信上下文知识），
`MEME_LEARNING_ENABLED=true` 时每 24 小时批量总结一次群聊并写入新梗（必要时经 MCP 检索验证）。
Web UI「群聊知识」页管理；详细设计见 [docs/knowledge.md](docs/knowledge.md)。

## 开发

```bash
pip install -r requirements-dev.txt
pytest              # 535 个测试（含 Persona / Meme / Web UI 新套件）
ruff check .        # 代码检查
```

CI：GitHub Actions 自动跑 Python 3.9 / 3.12 的 ruff + pytest。

更多工程细节：架构审计见 [docs/architecture-audit.md](docs/architecture-audit.md)，表情包见 [docs/stickers.md](docs/stickers.md)，
安全模型见 [docs/security.md](docs/security.md)。

## License

[MIT](LICENSE) © 2026 铃樱（lingcat521）
