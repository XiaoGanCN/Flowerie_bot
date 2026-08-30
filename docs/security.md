# 安全模型（Security Model）

> v1.2.0 新增。描述 Flowerie 的整体安全边界与防护设计。
> 插件开发侧的接口性安全规范见 [plugin-developer-guide.md](https://github.com/lingcat521/Flowerie_bot/blob/main/docs/plugin-developer-guide.md)，本节侧重架构性保证。

---

## 1. 三层安全边界（互不绕过）

Flowerie 的提示词注入防护按**权限层级**隔离，低层指令不能覆盖高层策略：

```
Runtime Security Policy    （运行时安全策略 / 输入安全声明 / 记忆铁律）   ← 最高，不可覆盖
        ↓
Persona                    （人格 system_prompt + 全局说话风格规则）     ← 继承，但不可突破
        ↓
Admin Response Rules       （ADMIN_RESPONSE_RULES 补充发言规则）         ← 只做风格补充
        ↓
Memory / MCP / Plugin / Knowledge  （用户记忆 / 工具结果 / 插件输出 / 知识）← 全部视为不可信外部输入
```

- **Runtime Security Policy**：安全声明、记忆协议/记忆安全铁律、知识区边界，由系统框架（AIClient）
  组装，**任何人格、自定义 Prompt、插件输出都无法覆盖**。
- **Persona**：人格内容属于"指令区"（管理员配置），但与 `ADMIN_RESPONSE_RULES` 一样**不能覆盖安全策略**，
  且知识区内容永远标记为不可信上下文知识。
- **Memory / MCP / Plugin / Knowledge**：一律当作**不可信外部输入**处理，互不信任、互不绕过
  ——记忆写入过 `validate_memory_content` 闸门，MCP/插件工具结果与群聊知识过 `sanitize_untrusted_text`
  清洗（注入句式替换为占位符、控制字符与零宽字符清理），且注入为不可信区段，不混入指令区。

---

## 2. 插件安全

### 2.1 安装防护（ZIP / URL）

- **ZIP**：校验压缩包大小（≤5MB，`PLUGIN_URL_MAX_BYTES`）与解压后总大小（≤50MB，`PLUGIN_ZIP_MAX_UNZIPPED_BYTES`，
  防 Zip Bomb）、文件数（`PLUGIN_ZIP_MAX_FILES`）与目录深度；成员名禁止绝对路径与 `..` 段（防 **ZIP Slip / 路径穿越**）；
  拒绝**符号链接**成员；缺 `manifest.json` 或混合多个顶层目录直接拒绝；manifest 严格 schema 校验，未知字段拒绝。
- **URL 下载**：**SSRF 防护**——拒绝内网/回环/私网/链路本地/组播/保留地址、`.local` 主机、userinfo、
  DNS 解析结果命中内网（防 rebinding）；**不跟随重定向**；Content-Length 预检 + 流式大小中止 + 超时 +
  Content-Type/扩展名检查（仅 `.zip` / `.json`）。
- 安装后插件一律处于 **disabled**，由管理员启用并批准权限。

### 2.2 权限强制（PermissionManager）

任何动作（action）在真正执行前都会过 PermissionManager；未批准 → 拒绝并记录 `plugin_permission_denied` 日志。
**关闭插件保护（Unsafe）也不会绕过权限检查**。插件返回「动作」而非直接执行副作用，插件永远无法绕过权限。
（权限列表与保留权限见 [plugin-developer-guide.md](plugin-developer-guide.md) §9。）

### 2.3 进程隔离与运行时限制

- 插件运行在**独立子进程**：Python 用 `python -I` 隔离模式，Node 用子进程，环境变量白名单不含任何 API Key / Token；
  插件不能 `import Flowerie` 内部模块、不能访问 Flowerie 数据库。
- stdin/stdout **JSON-Lines 协议**；崩溃 / 超时被隔离标记 `crashed`，Flowerie 继续运行。
- 资源限制：输出累计上限、单事件动作数上限、入口文件与 manifest 大小、注册表插件总数上限。

### 2.4 保护级别不变式

`PLUGIN_PROTECTION=normal|relaxed|unsafe`。保护级别**只影响运行时资源限制**（超时/输出/动作数），
**任何级别都不豁免**：manifest 校验、管理员权限（Web UI 认证）、进程隔离、日志、崩溃保护、
资源限制、**权限强制（PermissionManager）**。普通 QQ 用户永远不能安装/启用插件或修改权限。

---

## 3. Web UI 认证安全

### 3.1 注册 Bootstrap Lock

- 系统一旦初始化（`.env` 或 `settings.db` 存在管理凭据），公开注册**永久关闭**：
  GET/POST `/panel/register` 与 `/api/register` 一律 403 / 展示「注册已关闭」（无表单）。
- 只有 `UNINITIALIZED` 状态才能创建**第一个**管理员；并发注册用 `admin_bootstrap` 表**原子 CAS** 保证仅一个成功。
- 历史已有凭据自动视为已初始化（绝不因升级而重开注册）。
- 初始化后改账号走登录态 `/panel/account/credentials`（需当前密码）；注销（`/panel/account/unregister`，需当前密码）
  = 显式重置，系统回到 `UNINITIALIZED` 才可重新注册。

### 3.2 登录限流与哈希

- 密码只存 **scrypt 哈希**（`data/settings.db`，优先于 `.env`），永不写明文 / 日志；旧明文兼容比较用恒定时间。
- 登录失败：同一 IP 连续 5 次锁 1 分钟（`_login_fails` 时间窗）。

### 3.3 CSRF 防护设计

- JSON API 走 `Authorization: Bearer <token>`（无 cookie → 天然防 CSRF）。
- 无 JS 面板走 Cookie 会话（`fb_token`，`httponly` + `SameSite=Strict`）。
- 所有管理接口必须管理员 token；未认证一律重定向回 `/panel`。

---

## 4. NapCat WebSocket token 脱敏

`NAPCAT_ACCESS_TOKEN`（forward 模式鉴权）**绝不写入日志**：含 access_token 的 URL 经 `redact_ws_url` 剥离查询串后才
进入日志 / UI（仅显示 scheme + host + path）。插件 URL 下载与 WS 日志同样只记录脱敏后的查询串。

---

## 5. 主动发言概率与管理员规则不可覆盖安全策略

- 主动发言概率（`PROACTIVE_MESSAGE_*` / `ACTIVE_CHAT_*`）只控制**是否/何时**触发回复，**不改变内容安全边界**；
  所有输出仍受安全策略 / 清洗 / 记忆校验约束。
- `ADMIN_RESPONSE_RULES` 仅为**风格补充**（优先级：安全策略 > 人格 > 人格内置规则 > 本条），
  **不能覆盖安全策略**——运行时记忆校验 / 注入清洗 / 预算限制等不会被任何提示文本绕过。

---

## 6. 其它防护

- **知识防污染**：群聊梗知识按群隔离、过清洗闸门；MCP 工具元数据按不可信外部输入处理（防 tool description 注入）。
- **Secret 脱敏**：API Key 只在 UI 显示掩码，留空提交不覆盖；日志不记录密钥明文。
- **双层熔断 / 预算**：AI 熔断 + 群级熔断、三层 AI 预算，防风暴与滥用。
- **SSRF 通用防线**：MCP / 图片 / 插件 URL 均走地址校验与本地-内网白名单。

## Bot SDK 安全边界（v1.3.0）

- **三层隔离**：插件（上层）→ 领域层（中层，零 OneBot 命名）→ OneBot 适配层（下层，
  唯一 import OneBot 语义处）。**插件不接触 OneBot payload / HTTP endpoint / 网络库**；
  所有网络能力经主进程动作出口，SSRF 防护与权限门继续生效。
- **CQ 码阉割**：`[CQ:at,qq=x]` 等段码在中层转换为结构化 `at_list` / `images`，
  插件见不到底层格式；发送侧插件用 `BotMessage`（text/at/image/reply），由下层拼段。
- **撤回边界**：`delete_message` 权限仅允许撤回**本 bot 已发送并记录**的消息
  （`_sent_message_ids` 上限 200）；未记录的 message_id 一律拒绝（防删他人消息）。
- **群管理**：`group_manage` 权限（mute/kick）单独授权；建议只给受信任插件，
  并在命令上加 `rule(is_group_admin=True)` 守卫。
- **匹配注册**：`matcher_register`（read_message）只影响事件筛选与投递，
  不授予任何副作用。

## v1.4 新增边界

- **请求处理**（request_handle）：好友/加群同意是**社交副作用**——只批准给明确处理
  申请场景的插件；approve 必须回传原 flag（伪造 flag 无效）。
- **调度器**（scheduler）：定时任务在主进程执行（asyncio Task）；**间隔下限 1 秒**
  （防刷）；插件进程异常不影响调度器；shutdown 全部清理。任务数不设上限 → 文档
  建议每插件 ≤20 个任务；同插件同名注册幂等（覆盖）。
- **KV**（storage）：plugin_kv 表**按插件命名空间隔离**（key 前缀 plugin_id），
  其他插件不可读；单值 64KB；值为 JSON 或字符串。
- **AI**（ai_chat）：**独立于主聊天预算与三层限频**——插件可无限制消耗 token！
  仅在信任插件批准，且建议命令级冷却（cool_down）自限频。
- **HTTP 扩展**：PUT/DELETE/HEAD 与下载**全部复用** http_action 防线
  （字面量 + DNS 双闸、头过滤、不重定向）；下载 ≤10MB 且只能写插件目录
  （save_to 相对路径校验 + 真实路径公共前缀校验）。
- **记忆写入**：mem_update/mem_clear 复用现有 MemoryManager（审计与 TTL 不变）。
