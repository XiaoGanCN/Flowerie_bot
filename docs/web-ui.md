# 花璃 Web UI 管理后台

> 纯 **HTML + CSS + 服务端渲染**、**零 JavaScript** 的 Web 配置中心。
> 任意浏览器（含禁用 JS 的手机浏览器）可用。

---

## 如何开启

1. 编辑项目根目录 `.env`，追加：
   ```ini
   WEB_UI_ENABLED=true
   WEB_UI_PORT=8080            # 不能与 WS_PORT(3001) 相同
   WEB_UI_USERNAME=admin
   WEB_UI_PASSWORD=你的密码      # 必填，留空会拒绝启动
   ```
2. 重启：`bash run.sh` 或 `python main.py`
3. 浏览器打开 `http://127.0.0.1:8080/panel`

> 首次可在登录页点 **注册管理员账号**（密码以 scrypt 哈希存 `data/settings.db`，优先于 `.env`，永不写明文）。
> **注册 Bootstrap Lock**：公开注册仅在系统**尚未初始化**（`.env` 或 `settings.db` 无管理凭据）时可用，用于创建第一个管理员；
> 一旦系统完成初始化，注册入口永久关闭（GET/POST `/panel/register` 与 `/api/register` 一律返回「注册已关闭」），
> 之后改账号走登录态的「用户状态」页（需当前密码）。注销账号 = 显式重置，系统回到 UNINITIALIZED 才可重新首次注册。
> 局域网访问：`.env` 加 `WEB_UI_ALLOW_LAN=true`（绑定 `0.0.0.0`，启动日志输出安全警告，请设强密码、勿直接暴露公网）。

---

## 七个页签

| 页签 | 功能 |
| :--- | :--- |
| **配置** | 全部配置变量按多个功能分组（**人格/群聊知识/插件配置已移入对应页签**），顶部分类导航，每组一个表单保存 |
| **人格** | 全局人格设置、人格库 CRUD（创建/编辑/删除/设为全局）、群聊人格绑定与解除、**管理员补充发言规则** |
| **插件** | 插件系统（Plugin System v1）：保护措施开关、插件列表、上传/URL 安装、刷新扫描、启用（含权限批准）、禁用、卸载、插件系统配置 |
| **群聊知识** | 输入群号查看该群梗知识：搜索/新增/编辑/删除/清空，严格按群隔离 |
| **外观** | 7 套内置主题 + 背景颜色/图片 + 面板透明度 + 卡片效果 + 恢复默认/删除图片 |
| **日志** | 最近 200 条运行日志 |
| **用户状态** | 当前管理员与凭据来源、**修改登录账号**、注销账号（仅清账号密码）、服务器状态（平台/内存/CPU负载）、MCP 工具状态、API 厂商连接状态 |

全部通过 **HTML `<form>` + GET/POST + 服务端模板渲染**实现，**不使用任何 JavaScript**。

---

## 人格页

- **全局人格（Global Persona）**：下拉选择任意人格并保存；没有群聊特殊设置时所有群使用全局人格
- **人格列表**：内置（花璃 / 亚托莉 ATRI / 艾拉 Isla，带「内置」徽标）与自定义人格卡片；支持**编辑**（system_prompt / 词库参考 / 行为规则 / 回复风格）、**设为全局**、**删除**（内置不可删除）
- **新建人格**：填写 id / 名称 / 简介 / system_prompt 等；独立人格完全替换，不是微调
- **群聊人格（Group Persona）**：输入群号 + 选择人格绑定；当前绑定列表可一键**解除**（自动回退全局/内置）
- **默认人格**：顶部明确显示兜底人格 id（`PERSONA_DEFAULT`，默认 `flowerie` 花璃）——无全局/群设置时使用；
  下拉框**直接修改**，保存后**立即生效**（热更新，无需重启，写入 .env）
- **管理员补充发言规则（Admin Response Rules）**：`<legend>管理员补充发言规则</legend>` 区块，`<textarea>` 每行一条；
  追加在所有生效人格的发言规则之后（如「回复尽量简短」「高兴时可用感叹号」）。
  优先级：**安全策略 > 人格 > 人格内置规则 > 本条**，且**不能覆盖安全策略**
  （运行时记忆校验 / 注入清洗 / 预算限制等不受此文本影响）；保存到 `ADMIN_RESPONSE_RULES`。
- **群聊自定义 Prompt（按群读写）**：`<details>` 原生折叠（零 JS）；可读写全局 Prompt；
  输入群号查看/编辑/重置该群 Prompt（保存/重置只作用于填写的群，与其他群完全隔离，
  与 `/prompt` 命令同一存储）
- **人格配置**：`PERSONA_*` 配置（默认人格/长度上限/数量上限）在本页直接管理（保存即热更新），
  不再出现在「配置」页
- **自定义人格 vs 自定义 Prompt 区别说明**：页内卡片详解（人格=换身份，Prompt=加补充，可同时生效）
- 权限：页面复用管理后台登录认证，普通用户不可见/不可操作

## 群聊知识页

- 输入**群号**查看该群全部梗知识（知识按群完全隔离，群 A 页面绝不出现群 B 的知识）
- 支持按词条/含义**搜索**、**新增**（词条/含义/例句/可信度）、**编辑**（含义/例句/可信度/状态）、**删除**、**清空本群**
- 可信度：低/中/高（知识是群聊知识而非绝对事实）；状态：活跃/停用（停用不注入）
- **知识配置**：`MEME_*` 配置（学习开关/总结周期/每群上限等）在本页底部直接管理，不再出现在「配置」页
- 服务端按 `group_id` 强制作用域，所有写入过清洗闸门（注入句式/疑似 QQ 号/长度）

---

## 配置页

- **分类导航**：顶部一排分类（全部 / AI / 基础 / 连接 / 行为与回复 / 稳定性与熔断 / 记忆库 / 表情包 / MCP 工具 / Web UI / 日志 / 预算与限额 / 主动聊天 / 复读与防刷 / 戳戳 / 文件解析 / 安全与资源限制 / 白名单与隐私 / 消息存档 / 数据路径），点某个分类只看那一类，避免全部变量堆一屏。
- **控件按类型自动匹配**：
  - `bool` → checkbox（未勾选服务端自动 `false`）
  - `int/float` → number（含 `min/max/step`）
  - `secret` → password（只显示掩码，留空=不修改）
  - 枚举 → select（如日志级别/格式）
  - 列表 → 逗号分隔文本框
  - 多行/JSON → textarea
- **保存**：服务端校验（类型/范围/枚举/JSON/列表），通过后**真正写入项目根 `.env`**，并热更新运行配置；需重启的项在页面标注"已保存，部分配置将在服务器重启后生效"。

### 分组新增项（v1.2.0）

- **连接（Connection）**：本组新增 NapCat WebSocket 配置——`NAPCAT_WS_MODE`（`reverse`/`forward` 二选一，枚举下拉）、
  forward 时必填的 `NAPCAT_WS_URL`（`ws://` 或 `wss://`）、以及 `NAPCAT_ACCESS_TOKEN`
  （secret，forward 鉴权 token；**绝不写入日志 / 只在 UI 显示掩码，留空不修改**）。均为需重启项。
- **主动聊天（ActiveChat）**：本组除原有 `ACTIVE_CHAT_COOLDOWN` 外，新增**主动发言概率**全套——上下文随机回复概率
  `PROACTIVE_MESSAGE_MIN/MAX/_BASE/_USER_BOOST/_SINGLE_USER/_SHORT_MESSAGE/_EMPTY_CONTEXT/_BOT_MULTIPLIER`，
  与主动聊天循环 `ACTIVE_CHAT_PROBABILITY` / `ACTIVE_CHAT_INTERVAL_MIN/MAX_SECONDS` /
  `ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS`。校验：取值 `0~1`、有限数、**MIN ≤ MAX**、间隔 `MIN ≤ MAX`、冷却 `0~86400`；均热更新。
- **插件系统（Plugin）**：`PLUGIN_*` 配置组不在此展示，由**「插件」页**的专属配置区块管理（见下文插件页）。

> 管理账号（`WEB_UI_USERNAME` / `WEB_UI_PASSWORD`）不在配置表单：统一走注册页 / 「用户状态」页修改。

### `.env` 持久化（可靠写入）

- **保留原有变量与注释**：只改目标行，注释/空行/其他变量逐字节保留
- **原子写入**：临时文件 → flush + fsync → `os.replace`，绝不留半个 `.env`
- **并发安全**：线程锁串行化，多个请求同时保存不互相覆盖
- **特殊字符**：空格/`#`/`=`/引号/中文/换行/空串都能正确编解码（dotenv 兼容）

---

## 外观页

### 主题与背景一体预设

- **7 套内置主题**：默认（明亮浅色）/ 深色 / 浅色 / Sakura（浅粉）/ Ocean（天空蓝）/ Forest（草绿）/ AMOLED（纯黑，省电）
- **选主题即整套换肤**：背景色按主题**隔离存储**（`bg_color__<主题>`）——切到别的主题用各自配色，互不污染；选黑色主题自动变黑
- **背景颜色**：色块预览 + 手动输入 `#RRGGBB` / `253,238,243` / `rgb(r,g,b)`，留空=用主题默认，只改当前主题

### 透明度与卡片效果

- **主题面板透明度**（0~100%）：卡片/面板越透明越能透出背景图与主题底色
- **卡片效果**：**纯透明（淡入淡出）** 或 **液态玻璃（磨砂）** 二选一

### 背景图片

- 上传 PNG/JPEG/WEBP/GIF ≤ 5MB，服务端**魔数校验** + 固定文件名（`background.<ext>`）**防路径穿越**，持久化到 `data/webui/background/`，刷新/重启均不丢失
- **图片透明度**（0~100%）与背景颜色通过 CSS 渐变遮罩合成**同一视觉层**
- **显示方式**：cover（铺满裁切）/ contain（完整留白）+ 位置（居中/顶部/底部/左右）
- **顶栏不透明**，不被背景图遮挡
- **恢复默认主题** / **删除背景图片** 独立按钮

---

## MCP 工具页（卡片式管理）

MCP 服务器以**卡片列表**展示，无需手写 JSON：

- 每张卡片：服务器名、传输方式（streamable-http / sse）、**工具数量**（运行时已同步数/配置数）、启用状态、地址、**测试结果**（✔ 连接成功 / ✖ 连接失败）
- 操作按钮：**启用/停用**、**测试**（发起 MCP `initialize` 握手验证连通）、**编辑**（进入编辑表单）、**删除**
- 添加服务器表单：名称 / 地址 / 工具白名单 / 超时 / 启用
- **工具白名单留空 = 放行所有工具**；填了则只放行列出的（服务端仍做 SSRF/白名单校验）

> MCP 变更保存到 `MCP_SERVERS`，**重启后生效**（页面会提示）。

---

## 插件页（Plugin System v1）

插件的启用/权限批准/安装/卸载全部在此页完成，**只有管理员（Web UI 登录账号）能操作**；普通 QQ 用户永远无法
安装/启用插件或修改权限。插件支持 Python（`plugin.py`）、Node（`index.js`/`package.json`）与 JSON 声明式
（`runtime=json`，`declarations` 规则，无代码执行）；API 详见 [plugin-developer-guide.md](plugin-developer-guide.md)。

- **插件保护措施（Plugin Protection）**：三档单选——`Normal`（推荐，完整限制）/ `Relaxed`（放宽非必要限制）/
  `Unsafe`（仅可信插件，作者概不负责）。只影响运行时资源限制；**任何级别都不豁免** manifest 校验、管理员权限、
  进程隔离、日志、崩溃保护、资源限制与权限强制（PermissionManager）。
- **插件列表（Plugin Registry）**：以卡片展示每个插件的 runtime / 版本、`manifest 无效`（徽标）、启用状态与状态、
  声明权限 / 已批准权限、安装来源。已禁用且 manifest 有效时显示**批准权限后启用**（勾选声明权限 → 启用）；
  已启用的显示**禁用**；每个插件都有**卸载（删除文件与注册）**。
- **发现 ≠ 自动执行**：本页「刷新扫描插件目录」扫描 `PLUGIN_DIR`（默认 `./plugins`），新发现插件注册为**禁用**，
  由管理员明确启用并批准权限。
- **导入插件**：**本地 ZIP 包（含 manifest.json）或单个 manifest.json**上传装（仅 `runtime=json` 支持单个 manifest）；
  或填 **URL** 下载装（受 SSRF 防护——拒绝内网/回环/私网/重定向 + 大小上限 + 超时 + Content-Type/扩展名检查）。
  安装后一律 `disabled`。
- **插件系统配置**：`PLUGIN_MAX_COUNT` / `PLUGIN_URL_MAX_BYTES` / `PLUGIN_URL_TIMEOUT` /
  `PLUGIN_ZIP_MAX_UNZIPPED_BYTES` / `PLUGIN_ZIP_MAX_FILES`；`PLUGIN_PROTECTION` 由上方保护措施开关管理，
  插件目录为 `PLUGIN_DIR`（需重启生效）。

---

## 安全

- **Bootstrap Lock（注册锁定）**：系统一旦初始化（`.env` 或 `settings.db` 存在管理凭据），公开注册**永久关闭**
  （GET/POST `/panel/register` 与 `/api/register` 一律 403 / 展示「注册已关闭」，无表单）；只有 `UNINITIALIZED`
  状态才能创建第一个管理员；并发注册用 `admin_bootstrap` 表原子 CAS 保证仅一个成功；历史已有凭据自动视为已初始化（绝不因升级重开注册）
- **改账号 / 注销**：初始化后改账号走登录态 `/panel/account/credentials`（需当前密码，改密后强制重新登录）；
  注销账号（`/panel/account/unregister`，需当前密码）= 显式重置，系统回到 `UNINITIALIZED`（可重新首次注册）。
  注销只清除管理账号与密码，**其他环境配置（API Key 等）一律不动**，注销后强制登出
- **登录限流与哈希**：密码 scrypt 哈希（永不写明文/日志）；登录失败同一 IP 连续 5 次锁 1 分钟
- **CSRF 防护设计**：JSON API 走 `Authorization: Bearer <token>`（无 cookie → 天然防 CSRF）；
  无 JS 面板走 Cookie 会话（`fb_token`，`httponly` + `SameSite=Strict`）
- **Secret 掩码**：API Key 只显示 `sk-a****xxxx`，永不回显明文，留空提交不覆盖
- **插件安全**：安装 ZIP 防护（ZIP Slip / Zip Bomb / 符号链接 / 路径穿越 / manifest 注入）、URL 下载 SSRF 防护、
  权限强制、进程隔离、日志脱敏（详见 [security.md](security.md)）
- **图片上传**：服务端校验大小/扩展名/MIME/真实格式魔数，固定文件名防路径穿越，拒绝 HTML/SVG/脚本
- **MCP/图片 SSRF**：地址校验、本地/内网白名单（`MCP_ALLOWED_HOSTS` / 图片主机白名单）
- **管理账号不在配置表单**：统一走注册/用户状态页，避免明文写 `.env`

---

## 相关文件

| 用途 | 路径 |
| :--- | :--- |
| 配置持久化 | 项目根 `.env`（原子写入）+ `data/settings.db`（`app_config` / `webui_prefs` / `personas` / `group_persona` / `persona_global` 表） |
| 背景图片 | `data/webui/background/` |
| 服务端实现 | `src/services/web_ui.py`（薄门面：认证/面板壳/生命周期）+ `src/services/webui_panels/`（功能域 mixin：account/auth/config/appearance/mcp/persona/knowledge/prompt/plugin） |
| 模板与主题 | `src/services/web_ui_assets.py`（聚合导出）+ `src/services/webui_render/`（theme/pages/config_panel/appearance/persona/knowledge/account/plugins，7 主题） |
| 插件运行时 | `src/plugins/`（manager/runtime/manifest/permissions/installer/http_action/runner）+ `src/services/webui_panels/plugin_panel.py` |
| 服务器状态 | `src/services/system_status.py`（用户状态页，零依赖读 /proc） |
| 配置业务层 | `src/services/config_service.py`（配置 SCHEMA + `.env`/settings.db 双写） |
| 人格业务层 | `src/services/persona_manager.py` + `src/services/persona_presets.py`（内置预设） |
| 群聊知识业务层 | `src/services/meme_knowledge_manager.py` + `src/repositories/meme_knowledge_repository.py` |
| `.env` 写入器 | `src/repositories/env_store.py`（原子/保留注释/并发锁） |

---

## 用户状态页

集中展示当前管理员的账户信息与机器人运行/集成状态（全部零 JS）：

- **当前管理员**：登录账号 + 凭据来源（`settings.db` 注册账号 / `.env` 初始配置）
- **修改登录账号（改密）**：登录态下输入新用户名（3~32 字符）/ 新密码（≥6 位）/ 当前密码（用于确认），
  提交到 `/panel/account/credentials`；改密后强制重新登录。**已初始化的系统不允许公开注册，此处是唯一的账号修改入口**
- **注销账号**：输入当前密码确认，**仅清除管理账号与密码**（settings.db 与 `.env` 的
  `WEB_UI_USERNAME`/`WEB_UI_PASSWORD`），其他环境配置（API Key 等）一律不动；注销 = 显式重置 → 系统回到
  `UNINITIALIZED`（可重新首次注册），注销后强制登出并回到登录页
- **服务器状态**：平台 / 系统 / 架构 / 主机名 / Python 版本 / **内存占用**（`/proc/meminfo`）/ **CPU 负载**（`/proc/loadavg`）
  ——零依赖采集，读取失败显示 `N/A`
- **MCP 工具状态**：各 MCP server 已同步工具数 + 熔断状态（未启用时提示去配置页开启）
- **API 厂商连接状态**：DeepSeek（聊天主厂商）/ 视觉识图 / 引战检测——地址、模型、
  Key 是否配置、是否回退 DeepSeek（配置层面展示）

数据来源：`src/services/system_status.py`（服务器状态）、`McpToolManager`（MCP）、`ConfigService`（API 配置）。

## 说明

- 全程 **无 JavaScript**（无 `<script>`/fetch/框架），仅 HTML + CSS + 服务端
- 移动端自适应（PC / 平板 / 手机），窄屏表单转单列、按钮紧凑横排
- 其它配置项说明见 [configuration.md](configuration.md)；人格详见 [persona.md](persona.md)；群聊知识详见 [knowledge.md](knowledge.md)；MCP 详见 [mcp.md](mcp.md)；插件 API 详见 [plugin-developer-guide.md](plugin-developer-guide.md)；安全模型见 [security.md](security.md)
