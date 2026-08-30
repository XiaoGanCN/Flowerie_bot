# Changelog

本文件记录 Flowerie_bot 的版本变更。版本号遵循 [Semantic Versioning](https://semver.org/)。

## [1.2.0] - 2026-08-31

### Added

- **插件系统（Plugin System v1）**：受控插件运行时。导航栏新增「插件」页（Web UI）。
  支持 Python（`plugin.py`）、Node.js（`index.js`/`package.json`）与 JSON 声明式插件
  （`runtime=json`，`declarations` 规则，无代码执行）。
  安装途径：Web UI 上传 ZIP / URL 下载安装（SSRF 防护 + 大小限制）、本地目录 `plugins/` 自动发现
  （发现 ≠ 自动执行，默认 disabled）。插件运行在独立子进程（`python -I` 隔离 / `node` 子进程），
  stdin/stdout JSON-Lines 协议，崩溃/超时被隔离标记 `crashed`。
  相关配置：`PLUGIN_DIR` / `PLUGIN_PROTECTION` / `PLUGIN_URL_MAX_BYTES` / `PLUGIN_URL_TIMEOUT` /
  `PLUGIN_ZIP_MAX_UNZIPPED_BYTES` / `PLUGIN_ZIP_MAX_FILES` / `PLUGIN_MAX_COUNT`。
  详见 [docs/plugin-developer-guide.md](docs/plugin-developer-guide.md)。
- **第三官方人格「艾拉（Isla）」**：内置 persona `id=isla`（《可塑性记忆》风格原创改编，
  不复制原作台词，温柔克制/自贬/关键时刻决断路线），与 flowerie / atri 并列。`PERSONA_DEFAULT=flowerie` 保持默认。
- **管理员补充发言规则配置**：`ADMIN_RESPONSE_RULES`（每行一条；Web UI「人格」页编辑；
  优先级：安全策略 > 人格 > 人格内置规则 > 本条；不能覆盖安全策略）。
- **主动发言概率配置化**：`PROACTIVE_MESSAGE_MIN/MAX/BASE/USER_BOOST/SINGLE_USER/SHORT_MESSAGE/EMPTY_CONTEXT/BOT_MULTIPLIER`
  （上下文随机回复概率全部可配置）与 `ACTIVE_CHAT_PROBABILITY` / `ACTIVE_CHAT_INTERVAL_MIN/MAX_SECONDS` /
  `ACTIVE_CHAT_CONSECUTIVE_COOLDOWN_SECONDS`（主动聊天循环只配置化数字不改逻辑）。默认值 = 原硬编码值，行为零变化。
- **NapCat WebSocket 正向模式**：`NAPCAT_WS_MODE=forward`（Flowerie 客户端连接 NapCat 正向 WS，
  需 `NAPCAT_WS_URL=ws://` 或 `wss://` + 可选 `NAPCAT_ACCESS_TOKEN` 鉴权），含超时/重连退避/心跳/连接失败处理。

### Changed

- **发言规则配置化**：说话风格规则归属各 Persona（`system_prompt` 内嵌），新增管理员补充规则 `ADMIN_RESPONSE_RULES`；
  全局规则仍以「全局说话风格 & 标点规则」最高优先级注入。
- **Web UI**：面板页签由六个增至七个（新增「插件」）；「人格」页新增管理员补充发言规则编辑；
  「用户状态」页新增修改登录账号表单；「配置」页「主动聊天」分组新增主动发言概率项、Connection 分组新增 NapCat WS 配置。
- **版本号**：1.1.0 → 1.2.0。

### Security

- **Web UI 注册 Bootstrap Lock**：系统一旦初始化（`.env` 或 `settings.db` 存在管理凭据），公开注册永久关闭
  （GET/POST `/panel/register` 与 `/api/register` 一律 403 / 展示「注册已关闭」）；只有 `UNINITIALIZED` 状态才能注册
  第一个管理员；并发注册用 `admin_bootstrap` 表原子 CAS 保证仅一个成功；改账号走登录态 `/panel/account/credentials`
  （需当前密码）；注销（`/panel/account/unregister`，需当前密码）= 显式重置回到 `UNINITIALIZED`；
  历史已有凭据自动视为已初始化。
- **插件安全**：安装 ZIP 防护（ZIP Slip / Zip Bomb / 符号链接 / 路径穿越 / manifest 注入）、URL 下载 SSRF 防护、
  权限强制（PermissionManager）、进程隔离、日志脱敏；保护级别（`PLUGIN_PROTECTION`）任何级别都不豁免
  manifest 校验 / 管理员权限 / 进程隔离 / 日志 / 崩溃保护 / 资源限制 / 权限检查。
- **NapCat WS token 脱敏**：`NAPCAT_ACCESS_TOKEN` 绝不写入日志（URL 查询串剥离后记录）。
