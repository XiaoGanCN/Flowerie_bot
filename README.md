# 花璃 · QQ 群聊机器人

<p align="center">
  <b>银发灰瞳的小恶魔系青梅竹马 · DeepSeek 驱动 · NapCat OneBot11</b>
</p>

<p align="center">
  <b>「戳我干嘛，再戳就不理你了哦」</b>
</p>

---

## 这是什么

**花璃** 是一个基于 **DeepSeek API** 的 **QQ 群聊机器人**：像真实群友一样聊天、识图、看转发、记记忆、被戳会回应，还能自定义人格、发表情包、用 MCP 工具上网查信息，并通过 Web UI 管理配置。

## 功能

| 功能 | 说明 |
| :--- | :--- |
| 💬 AI 对话 | DeepSeek 驱动，小恶魔系人设，@ 或群聊接话 |
| 👁️ 识图 | 图片/表情包/转发内图片，视觉模型描述后自然回复 |
| 📦 转发/卡片解析 | 合并转发递归展开（含图片）、JSON 卡片 |
| 🧠 记忆库 | 按用户×群隔离，SQLite 持久化，自动去重，用户可查/删 |
| 🎭 自定义 Prompt | 全局 + 群聊两级人格（`/prompt` 命令，管理员可改） |
| 🖼️ 表情包 | 目录扫描 + Vision 索引缓存，模型按语境选择发送 |
| 🔧 MCP 工具 | 外部工具调用（如搜索），工具白名单 + 熔断隔离 |
| ⚔️ 引战检测 | 关键词 + AI 双重确认 |
| 🎯 冷却/预算 | 用户/机器人冷却、全局+群+用户三层 AI 预算、复读检测、主动聊天 |
| 🖥️ Web UI | 管理后台：AI/Bot/记忆/表情包/MCP 配置，热更新 |
| 🛡️ 安全 | SSRF 防护、Prompt 注入多层防线、日志脱敏、双层熔断 |

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
| `MCP_ENABLED` / `MCP_SERVER_URL` / `MCP_ALLOWED_TOOLS` | MCP 开关 / 地址 / 工具白名单 | `false` |
| `WEB_UI_ENABLED` / `WEB_UI_PORT` / `WEB_UI_USERNAME` / `WEB_UI_PASSWORD` | Web UI 开关 / 端口 / 登录账号 / 密码 | `false` / `8080` / `admin` / 空 |
| `LOG_FORMAT` | 日志格式 `text`/`json` | `text` |

> ⚠️ WEB_UI_PORT 不能与 WS_PORT 相同（端口冲突时启动会报错）。

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

默认关闭。启用后访问 http://127.0.0.1:8080
用 WEB_UI_USERNAME / WEB_UI_PASSWORD 登录
可管理 AI / Bot / 记忆 / 表情包 / MCP 配置（API Key 只显示掩码）。详见 [docs/configuration.md](docs/configuration.md)。

### 如何开启

1. 编辑项目根目录的 `.env`，追加：
   ```ini
   WEB_UI_ENABLED=true
   WEB_UI_PORT=8080            # 不能与 WS_PORT(3001) 相同
   WEB_UI_USERNAME=admin
   WEB_UI_PASSWORD=你的密码      # 必填，留空会拒绝启动
   ```
2. 重启机器人：`python main.py`（或守护脚本 `bash run.sh`）
3. 浏览器打开 http://127.0.0.1:8080，用上面的账号密码登录

> 同一局域网内的电脑访问：在 `.env` 加 `WEB_UI_HOST=0.0.0.0`，然后浏览器打开 `http://局域网IP:8080`（请设置强密码，勿暴露公网）。

## MCP

默认关闭。配置 MCP_ENABLED=true + MCP_SERVER_URL 后，模型可调用白名单内的工具获取实时信息。详见 [docs/mcp.md](docs/mcp.md)。

## 开发

```bash
pip install -r requirements-dev.txt
pytest              # 297 个测试
ruff check .        # 代码检查
```

CI：GitHub Actions 自动跑 Python 3.9 / 3.12 的 ruff + pytest。

更多工程细节：架构审计见 [docs/architecture-audit.md](docs/architecture-audit.md)，表情包见 [docs/stickers.md](docs/stickers.md)。

## License

[MIT](LICENSE) © 2026 铃樱（lingcat521）
