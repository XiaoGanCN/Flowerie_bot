# Review 报告（v1.2.0 安全与正确性）

> 独立 review（两位安全审查员 + 白盒/黑盒测试补强）+ 发现与修复记录。
> 结论先行：**结构性防御可靠，无可用认证绕过**；修复了 1 个协议级缺陷、
> 1 个注册流程设计矛盾、3 个健壮性问题，其余为文档化边界。

## 1. 已修复问题

### 1.1 [高] 插件协议 ID 命名空间碰撞（会导致动作丢失/运行器卡死）
- **发现**（插件系统 Review）：Flowerie → 插件的响应请求 id（1,2,3…）与
  插件 → Flowerie 的 action 请求 id（插件侧也从 1 计数）**共用同一命名空间**。
  id 相撞时，reader 会把插件的 action 请求误当作响应 set 给挂起的请求，
  插件等待响应永远卡死（30s 超时才恢复），管理器的请求收到错误结果。
- **修复**：插件侧 action 请求 id 改为 `1_000_000 + n`（python_runner.py /
  node_runner.js 同步）；runtime reader 把 `method == "action"` 分支判定前移。
- **测试**：现有 api 插件（on_message 中同步调 api.log）覆盖该路径；协议增韧测试通过。

### 1.2 [高] Web UI 注册流程与启动校验自相矛盾（首次注册不可达）
- **发现**（认证 Review）：`validate_config` 强制 `WEB_UI_ENABLED=true` 必须设置
  `WEB_UI_PASSWORD` → `admin_initialized()` 恒为 True → 公开注册永久 403。
  首装用户只能靠 .env 密码登录，Bootstrap「首次搭建仅一页」永远不可用。
- **修复**：
  - `config.py`：WEB_UI 启用时允许密码为空（= UNINITIALIZED，走首次注册）；
    仅要求 `WEB_UI_USERNAME` 非空（登录页展示）。
  - `web_ui.py`：`_verify_admin` 对空密码一律拒绝 —— **未初始化时任何登录
    都 401**，注册页是唯一入口（不存在"空密码裸奔"）。
- **测试**：`test_login_rejected_when_uninitialized`（空/任意密码 401）。

### 1.3 [中] register_user 异常回滚不完整（可永久锁死注册）
- **发现**：`WEB_UI_USERNAME` 写入成功、`WEB_UI_PASSWORD` 写库失败时只重置
  bootstrap 行、不删残留用户名 → `admin_initialized()`（凭据判定）恒 True →
  注册永久关闭，且该失败状态无任何凭据可登录（双输）。
- **修复**：异常路径完整删除 USERNAME + PASSWORD + 重置行；并新增
  **CAS 残留恢复**：行已置位但无凭据（上次初始化中断残留）→ 自动重置行后
  再 CAS 一次，可继续注册（不误伤真实并发：恢复路径仅在确实无凭据时触发）。
- **测试**：`test_register_recovers_from_stale_bootstrap_marker`。

### 1.4 [中] 未知插件动作被放行（白名单缺口）
- **发现**（白盒测试触发）：`PermissionManager.check("do_evil")` — 无权限映射的
  动作被当作无害动作（log/test 同级）放行，进入 `_run_action`（虽然无实现分支，
  但语义上违反白名单原则，未来新增动作时极易误放行）。
- **修复**：`_BUILTIN_ACTIONS = {log, test}` 显式白名单；未知动作一律拒绝。
- **测试**：`test_unknown_action_rejected_not_executed`（rogue_plugin）。

### 1.5 [低] NapCat 正向 WS 退避实现与文档不符
- **发现**：每次断开一次性 sleep 完整个序列（5+10+20+40+60=135s）后再重连一次，
  而非"逐档递增"；长时间不可达唤醒慢。
- **修复**：`_reconnect_idx` 逐档轮转（5→10→20→40→60，此后保持 60s）。

### 1.6 [低] NapCat access_token 未 URL 编码 / WEB UI token 表无上限
- access_token 含 `&`/`#` 等字符会破坏查询串 → 改用 `urlencode`。
- `_tokens` / `_login_fails` 增加 512 上限与过期清理（防无界内存）。

### 1.7 [低] 插件并发 action 无背压
- 插件可并发刷 action 请求堆积主进程任务 → 增加信号量（8 并发）限制。

## 2. 已验证安全（无需修改）

- ✅ scrypt 哈希 + 恒定时间比较；密码/密钥无明文日志、无响应回显
- ✅ `request.remote` 取真实客户端 IP（不信任 X-Forwarded-For），限速不可伪造
- ✅ 改密/注销后 `_tokens.clear()`，旧会话立即失效（黑盒断言）
- ✅ 概率配置 NaN/Infinity/越界在启动校验与 Web UI 保存双路拒绝
- ✅ NapCat token 不进日志（URL 脱敏查询串；连接日志只记脱敏 URL）
- ✅ WS 背压（max_queue=64）+ 消息处理 `asyncio.wait_for` + 信号量（不阻塞事件循环）
- ✅ `ADMIN_RESPONSE_RULES` 无法覆盖代码层安全闸门（sanityzer/记忆校验/策略引擎均在
  prompt 文本之上；管理员独占配置）
- ✅ ZIP Slip / Zip Bomb / 符号链接 / 路径穿越 / manifest 注入无绕过
  （含 UTF-8 名称、重复条目、special file 位检查）
- ✅ URL 下载 SSRF：字面量 + DNS 解析结果双检查；无重定向；扩展名/Content-Type/
  大小/超时全受控
- ✅ 插件事件负载最小化（不透传原始段数组/私聊内容）
- ✅ 环境变量白名单不含任何 API Key

## 3. 文档化边界（不修改，如实声明）

- **插件子进程隔离是"代码级"而非 OS 级沙箱**：插件以 bot 用户运行，权限系统只
  约束经管理器路由的动作，不约束原始 OS 调用（读同用户文件、访问云元数据等）。
  已写入 `docs/plugin-developer-guide.md` §20：只安装审查过的插件；生产建议容器化
  + 收紧 `.env`/数据目录权限。
- Web UI 会话 cookie 未加 `Secure`（默认 127.0.0.1 + HTTP 部署下不可用）；
  `WEB_UI_ALLOW_LAN=true` 时建议 WSS/防火墙白名单（`docs/security.md` 已注明）。

## 4. 能力验证（白盒 + 黑盒测试新增）

| 层 | 测试 | 新增用例 |
| --- | --- | --- |
| 白盒-权限 | 权限矩阵逐项 / 未知动作拒绝 / unsafe 不豁免 | +3 |
| 白盒-协议 | 垃圾输出韧性的 initialize+事件 / 洪水输出终止 | +2 |
| 白盒-注册 | 未初始化登录拒绝 / CAS 残留恢复 / 失败回滚 | +3 |
| 白盒-事件 | 无 read_message 不投递 / 崩溃隔离 | 已有 |
| 黑盒-面板 | URL 内网·file:// 拒绝（SSRF）/ 超限上传 err / 改密注销会话失效 | +5 |
| 黑盒-WS | 畸形/非 JSON 帧存活 / 断线重连 / 鉴权头 | +2 |
| 黑盒-概率 | 5000 次采样频率与配置一致 / 全零永不回复 | +2 |

本地运行：`test_plugin_*` + `test_napcat_ws_forward` + `test_persona_isla`
共 **93 个测试全部通过**；CI（3.9/3.12 + Acceptance）全绿。
