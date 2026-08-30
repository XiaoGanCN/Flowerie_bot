# 工程质量审计报告（阶段一）

> ## 📌 文档状态（2026-08-29 更新）
>
> 本报告为 2026-08-27/28 的**历史审计快照**，所列问题已在后续轮次全部处理完毕：
>
> - **阶段一「改造范围」10 项**：标准 logging + trace_id（`utils/logging_setup.py` + `utils/trace.py`）、
>   Metrics（`utils/metrics.py`）、Repository 抽象（`repositories/`）、TaskManager（`utils/task_manager.py`）、
>   AIClient 重试/退避配置化、配置启动校验（`validate_config`）、pyproject.toml、Ruff、GitHub Actions CI —— ✅ 全部完成
> - **阶段二 P1/P2**：AI 熔断（`utils/circuit_breaker.py`）、优雅关闭 draining、Metrics label 修复、
>   SQLite WAL/busy_timeout、图片 URL 日志脱敏 —— ✅ 全部完成
> - **阶段三**：ExpiringMap 状态自治、inactive 群清理、双层熔断（provider + 群级）、Metrics 低基数 —— ✅ 全部完成
> - **第四轮收尾**：MCP 工具额度按次硬上限、持久化配置启动合并、MCP SSRF 加固 + 工具结果不可信处理、
>   MCP 插件式多 server（`MCP_SERVERS`）、Web UI 改为无 JS 服务端渲染面板（`/panel`，支持注册，账号持久化 `settings.db`）—— ✅ 完成
>
> **当前基线**：测试 **535** 个（pytest + ruff 全过，CI Python 3.9 / 3.12 全绿）。
>
> **v1.1.0 现行架构说明**（本报告为 v1.0.1 前的历史审计快照，正文保留当时的架构描述）：
> 此后已做上帝类拆分——WebUIServer 1129→336 行（功能域 mixin `webui_panels/` + 渲染层 `webui_render/`）、
> AIClient 800→353 行（拆出 `prompt_builder`/`vision`/`toxic_detector`）、ConfigService 689→455 行（拆出 `config_schema`）、
> MessageRouter 732→564 行（拆出 `ai_gateway`）；当前目录结构见 [development.md](development.md)。
>
> **v1.2.0 说明**：版本 1.2.0 新增插件系统（Plugin System v1，`src/plugins/`）、第三官方人格「艾拉（Isla）」、
> 发言规则/主动发言概率配置化、NapCat WebSocket 正向/反向二选一，以及 Web UI 注册 Bootstrap Lock 安全修复；
> 本报告正文仍为审计当时的架构快照，未随新版更新。

> 审计对象：Flowerie_bot（NapCat 版，`/storage/emulated/0/Flowerie_bot/`）
> 审计时间：2026-08-27
> 审计方式：全量代码阅读（src/ 全部模块 + main.py + tests/ + 配置），未做任何修改。

---

## 1. 当前架构图

模块化单体（单进程、单事件循环），分层如下：

```
main.py
 ├─ AIClient (httpx.AsyncClient)      ← DeepSeek API / 引战检测 / 视觉识图
 ├─ MemoryManager (SQLite, 线程安全)  ← 记忆库
 ├─ FileParser (httpx, 懒连接)        ← 合并转发 / JSON 卡片 / @提取
 ├─ Sender (aiohttp.ClientSession)    ← OneBot HTTP API 发送
 ├─ PolicyEngine（门面）
 │    ├─ ContextManager   上下文读写/接话概率/重复回复/崩溃备份(SQLite)
 │    ├─ CooldownManager  用户/机器人冷却、连续回复惩罚
 │    ├─ RepeatDetector   复读检测（带内存上限）
 │    ├─ MemoryParser     记忆指令解析/强制记忆触发
 │    ├─ PokeManager      戳戳回复去重
 │    └─ ActiveChatManager 主动聊天决策
 ├─ MessageRouter（流程编排）
 │    ├─ MessageAssembler  消息组装（文本/识图/转发/卡片/存档）
 │    ├─ CommandHandler    用户/管理员指令
 │    └─ BudgetManager     三层 AI 预算（全局/群/用户）
 └─ WebSocketServer（websockets 反向 WS，单连接守卫）
```

依赖：aiohttp / httpx / websockets / pydantic(+settings) / python-dotenv / loguru（+ 已移除的文件解析依赖）。

## 2. 核心数据流

```
NapCat 反向 WS → WebSocketServer._handler
  → process_event → _handle_message
      → 白名单 → 消息去重(processed_msg_ids) → 指令?
      → assembler.assemble（文本/图片识图/转发/卡片/存档）
      → 复读检测 → 引战检测(TOXIC_GROUP_IDS, 走预算)
      → 静默记忆(强制记忆) → 回复决策(should_reply)
      → 冷却检查 → guarded_chat（预算闸门 → chat_once，失败重试）
      → 记忆写入(MEMORY_JSON) → 重复回复过滤 → Sender 发送
```

## 3. 后台 asyncio task 的创建与生命周期

| Task | 创建点 | 管理 |
|---|---|---|
| `_active_chat_loop` | `MessageRouter.start()` `create_task` | `stop()` 中 cancel+await，**有** |
| `_context_backup_loop` | `MessageRouter.start()` `create_task` | `stop()` 中 cancel+await，**有** |
| WS server 主协程 | `main()` await | 随主流程 |

**问题**：
- 两个后台任务各自 try/except，但**没有统一 TaskManager**；若 `_active_chat_loop` 抛出未捕获异常（如 sender 偶发 bug），任务**静默死亡**（"Task exception was never retrieved"），无重拉、无告警。
- `_active_chat_loop` 的 while 循环内 `_do_active_chat` 未整体包 try/except（仅内部部分有）。
- 新增后台任务无统一注册/取消/优雅关闭入口。

## 4. AI 请求的完整生命周期

```
guarded_chat(group_id, user_id, ...)
 ├─ 循环 attempt in 0..2（最多 3 次）
 │   ├─ _ai_allowed → BudgetManager.check（全局计数/群计数/用户限速）
 │   │     └─ 拒绝 → notify_exhausted（每天每群一次）+ denied=True
 │   ├─ AIClient.chat_once（单次尝试，内部不重试）
 │   │     ├─ httpx POST（connect 20s / read 60s / write 20s / pool 20s）
 │   │     ├─ 非 200 → 记录；429 → _api_backoff=8s
 │   │     └─ 解析 choices → 剥离 MEMORY_JSON 记忆指令 → 截断 MAX_REPLY_LENGTH
 │   └─ 空回复 → sleep(backoff) 后重试
 └─ 返回 (reply, memory_update, denied)
```

**计费语义**：每次尝试（含重试）都单独过预算闸门——`一次预算 = 一次真实 API 尝试`。这保证 retry **不会绕过** BudgetManager，但代价是 3 次尝试会消耗 3 次额度计数（语义为"尝试次数"）。用户限速只在首次尝试检查（`user_interval=(attempt==0)`），避免重试被自己的限速拦截——设计正确。

## 5. Memory 的读写路径

```
写入：
 ① AI 回复中的 MEMORY_JSON / 【记忆】 → validate_memory_content 校验
    → MemoryManager.append_memory_text（矛盾替换→去重→插入→超50条截25条→审计→commit）
 ② 静默强制记忆（个人偏好句式，未@）→ 同上（confidence=self_claim）
 ③ /forget /forget_me /memory_clear 用户指令 → 删除
读取：
 chat_once → get_memory_context(user_id, group_id) → 最近 20 条 + kv
 /memory 指令 → get_user_notes
```

存储：SQLite（memory 表 + memory_kv 表），`check_same_thread=False` + `threading.RLock`，`save()` 走 `asyncio.to_thread`。**业务逻辑（去重/矛盾替换/TTL/审计）与 SQL 语句耦合在 MemoryManager 一个类里**。

## 6. 当前异常处理策略

| 位置 | 策略 | 评估 |
|---|---|---|
| WS handler | 每事件 try/except + `wait_for(EVENT_PROCESS_TIMEOUT)` | ✅ 有兜底，超时取消 |
| `_context_backup_loop` | 循环内 try/except | ✅ |
| `_active_chat_loop` | 无整体防护 | ⚠️ 单点异常→任务死亡 |
| AIClient | 内部 catch 返回 None（记录日志） | ⚠️ 异常信息足够但非结构化；网络错误与业务错误不分 |
| Sender | catch 记录日志返回 False | ✅ |
| CommandHandler | 部分 `except (ValueError, TypeError): continue` | ⚠️ 吞异常（无害但无日志） |
| FileParser | 多处 catch 返回默认值；**1 处 bare `except:`** | ⚠️ bare except |
| main | KeyboardInterrupt / Exception 出口 | ✅ |

## 7. 当前日志策略

- loguru：stdout（彩色）+ `logs/bot.log`（500MB 轮转 / 保留 10 天）。
- **问题**：
  1. 非标准库（用户要求标准 logging）
  2. 无统一结构化格式、无 JSON 输出模式
  3. **无 trace_id**：无法关联"一条消息从入到出的完整链路"
  4. 敏感信息未过滤：debug 级别记录 API 原始响应前 500 字符（含用户消息全文）、系统提示词全量
  5. 消息正文无截断策略（部分日志记录完整 `raw_message`）
  6. 日志级别不可运行时调整

## 8. 当前测试策略

- unittest 风格，80 个用例（记忆/冷却/上下文/清洗/转发/路由回归/AI/复读）。
- 用 `asyncio.run()` 包装协程（无 pytest-asyncio）。
- **无 pyproject.toml**：pytest 需手工 `PYTHONPATH=.` 才能导入 `src.*`。
- 无 CI、无 lint、无类型检查。
- 缺：trace_id 并发隔离 / task 失败捕获 / 优雅关闭 / AI 超时与重试 / repository / 并发访问 / metrics / 敏感日志等测试。

## 9. 已知技术债务

1. `FileParser.file_cache` 声明后从未使用（死代码）。
2. `file_parser.py:203` bare `except:`。
3. loguru → 标准 logging 迁移未做。
4. `Settings` 用 `Field(..., env=...)` 的旧式写法（pydantic v2 推荐直接字段名 + 小写环境变量前缀，当前无 deprecation warning 但可优化）；`model_config` 未设 `case_sensitive` 等。
5. `models.BotDependencies` 未被使用。
6. `AIClient.chat()` 兼容入口保留（无调用方）。
7. `GroupState.user_last_time` / `GlobalState.user_ai_last_call` / `poke_last_time` / `last_toxic_warning` 等 dict 无上限（随用户数增长，当前量级可接受，但无治理）。
8. MemoryManager 业务与 SQL 耦合（阶段五处理）。
9. 硬编码重试次数 3、退避策略（429 固定 8s + 其他随机 1~2s）未配置化、非指数退避。
10. 测试与 pytest 配置缺失（pythonpath 问题）。

## 10. 可能的并发问题

| 位置 | 风险 | 现状 |
|---|---|---|
| SQLite 多线程 | 跨线程访问 | ✅ `check_same_thread=False` + RLock + `to_thread` 提交 |
| 多消息并发 | AI/识图并发打爆 API | ✅ `process_semaphore`（MAX_CONCURRENT_AI） |
| 单消息超时取消 | `wait_for` 取消时中断记忆写入 | ⚠️ 取消发生在 await 点，写入可能半途中断（SQLite 事务保证不损坏，但该条记忆可能未落库）；可接受但未记录 |
| 后台任务 vs 消息处理 | 共享 `groups`/`global_state` | ✅ asyncio 单线程内无竞态 |
| trace_id 污染 | 多消息并发处理 | ⚠️ 当前无 trace_id；引入时必须用 contextvars |
| 主动聊天并发 | 与 WS 处理共用额度 | ✅ 已并入 process_semaphore |

## 11. 可能的资源泄漏问题

| 资源 | 现状 |
|---|---|
| AIClient httpx client | ✅ `async with` 生命周期内关闭 |
| Sender aiohttp session | ✅ `async with` 关闭 |
| **FileParser httpx client** | ❌ 懒创建后**从不关闭**（优雅关闭时泄漏一个连接池） |
| **MemoryManager SQLite 连接** | ❌ 长连接，main.py 退出时未调用 `close()` |
| 上下文备份 SQLite | ✅ 每次开关连接 |
| 图片下载 | ✅ 流式 + 上限 + async with |
| 待解析文件缓存 | ✅ TTL + 上限 |
| 复读缓存 | ✅ 上限 200 + 长内容不跟踪 |

## 12. 可能的安全问题

| 项 | 状态 |
|---|---|
| 提示词注入 | ✅ 多层防线（不可信数据区/清洗/记忆闸门/目标用户恒为当前用户） |
| SSRF（图片下载） | ✅ scheme 白名单 + 可选主机白名单 + loopback 信任边界 |
| 文件下载资源耗尽 | ✅ 流式 + 字节上限 + MIME 嗅探 |
| 转发套娃 DoS | ✅ 四重预算 |
| WS 未授权连接 | ✅ 可选 WS_TOKEN（默认 loopback） |
| **日志泄露** | ❌ debug 日志含 API 响应原文/用户消息；无敏感字段脱敏（API Key 不落盘 ✅，但响应内容可能含隐私） |
| 记忆隐私 | ✅ 按用户隔离 + 代码层校验 + 用户可控删除 |
| 预算被刷 | ✅ 三层限速 |
| 指令越权 | ✅ 管理员指令校验 ADMIN_QQ_IDS |
| 图片 CDN url 直接入日志 | ⚠️ url 可能含签名参数，日志记录 `url[:80]` |

## 专项检查结论

- **create_task 后没人管理**：部分存在（无统一 TaskManager、无任务失败告警）→ 阶段六
- **WS/HTTP session 未正确关闭**：FileParser._client、MemoryManager 连接 → 阶段六
- **SQLite 并发访问**：已加锁，正确 → 保持
- **AI 请求超时**：httpx 分层超时 + EVENT_PROCESS_TIMEOUT 兜底 ✅ → 阶段七补充重试策略细节
- **retry 重复扣费**：每次尝试过预算（不绕过额度），语义为尝试次数 → 阶段七固化并文档化
- **exception 被吞**：大部分有日志，1 处 bare except、CommandHandler 个别静默 → 阶段十
- **无限循环/递归**：active chat 循环有 sleep+冷却 ✅；转发/卡片递归有深度预算 ✅
- **无界队列**：无 queue；缓存均有上限 ✅
- **无界缓存**：见 9.7（用户维度 dict，可接受）✅
- **文件/图片下载资源泄漏**：FileParser 连接不关闭 → 阶段六
- **shutdown 数据未持久化**：退出时 save_context_backup ✅；MemoryManager 实时 commit ✅；但连接未显式关闭 → 阶段六

## 改造范围（已完成 ✅，见顶部文档状态）

1. 标准 logging 基础设施（dev 人类可读 / prod JSON、敏感脱敏、trace_id 注入）
2. contextvars 版 trace_id 贯穿消息处理链路
3. 内部 MetricsRegistry（snapshot + Prometheus 文本导出）
4. MemoryRepository 抽象（SQLite 实现，业务层解耦）
5. BackgroundTaskManager（注册/跟踪/取消/优雅关闭）+ 关闭所有泄漏资源
6. AIClient 重试/退避配置化、usage 记录
7. 配置增强（敏感保护、启动校验）
8. pyproject.toml（pytest/ruff）、补测试
9. Ruff 修复
10. GitHub Actions CI

---

# 第二轮：生产事故模拟式审计（故障模式表）

> 审计时间：2026-08-28。结论先行：**未发现会导致 Bot 崩溃或数据损坏的 P0 缺陷**；
> 发现 3 个 P1 与 4 个 P2 问题，修复如下。

| # | 场景 | 当前行为 | 风险 | 严重程度 | 处理 |
|---|---|---|---|---|---|
| 1 | 100 条消息并发 | trace_id 用 contextvars 隔离；全部共享状态在 asyncio 单线程内同步访问 | 无竞态 | P2 | 补并发测试 |
| 2 | 同用户 20 条并发 | cooldown 的 check+update 无 await 间隔，原子；被@消息绕过用户冷却但预算 per-user 限速兜底 | 无竞态 | P2 | 补并发测试 |
| 3 | 同群预算并发 | budget.check() check+increment 同同步块，无 TOCTOU | 无竞态 | — | 补并发测试验证 |
| 4 | AI 连续失败 | 指数退避 + 4xx 不重试 + 每次尝试过预算；**无全局熔断**：失败风暴时多消息并发重试可能打爆 API | 中 | **P1** | 新增简单熔断 |
| 5 | AI 永久卡住 | httpx 分层超时 + EVENT_PROCESS_TIMEOUT 兜底；**shutdown 不等待进行中的事件处理** | 中 | **P1** | draining + 等待/取消 in-flight |
| 6 | WS 突然断开 | 单连接守卫；serve 失败按 5→10→20→40→60s 退避重连；连接断开靠宿主重连 | 低 | P2 | 补测试 |
| 7 | shutdown 时收消息 | **无 draining 状态，WS handler 任务未追踪**，shutdown 后 in-flight 可能继续跑满超时 | 中 | **P1** | 追踪 handler task + draining |
| 8 | 后台任务崩溃 | TaskManager 记录 task_failed（含堆栈）；context_backup 循环自带 try/except 不会死；不自动无限重启 | 低（可接受） | P3 | 文档化 |
| 9 | SQLite 并发 | 单连接 + RLock + to_thread 提交；未开 WAL/busy_timeout（单连接下无 locked 风险） | 低 | P2 | 加固 WAL + busy_timeout |
| 10 | Memory 一致性 | check-duplicate + insert 在同一连接同步完成（无 await 间隔），同连接可见未提交行 → 去重原子 | 无竞态 | P2 | 补 dedup race 测试 |
| 11 | 资源泄漏 | 全部 session/client/连接有 close；CancelledError 不被 except Exception 吞 | 低 | — | 补 AI cancellation 测试 |
| 12 | 取消传播 | asyncio.CancelledError 是 BaseException，`except Exception` 不吞；无 except BaseException | 正确 | — | grep 确认 |
| 13 | 无界资源 | 缓存/队列均有上限；**用户维度 dict（user_last_time/user_ai_last_call/poke_last_time/last_toxic_warning）无治理** | 低-中 | **P2** | 定期清理陈旧条目 |
| 14 | SSRF | scheme 白名单 + 可选主机白名单 + loopback(127/8, ::1)；0.0.0.0/私有 IPv4 无白名单时放行（设计决策）；redirect ≤3；MIME 嗅探；DNS rebinding 无法完全防御（本地部署可接受） | 低 | P2 | 补边界测试 |
| 15 | Prompt Injection | 不可信数据区 + 清洗 + 记忆闸门 + 目标用户锁定，多层防护 | 低 | — | 补记忆污染测试 |
| 16 | 日志安全 | 不记录 prompt/响应全文；redact 保底；**图片 URL（含签名 query）进入错误日志** | 低 | **P2** | URL 日志只记 host+path |
| 17 | Metrics | Counter/Histogram 有锁；**export_text 的 label 输出不符合 Prometheus 规范**（`{"a","b"}` 而非 `{name="value"}`） | 中 | **P1** | 修复 label 输出 |

---

# 第三轮：故障隔离与状态治理审计

> 审计时间：2026-08-28。核心结论：
> ① Circuit Breaker 为**全局单点**——群 A 的失败会熔断所有群的 AI（故障传播，P1）；
> ② 全局熔断状态无生命周期/状态机（无 HALF_OPEN，wall clock）；
> ③ 每群 GroupState 在 groups dict 中**无限增长**（无 inactive 清理，P1）；
> ④ last_toxic_warning 等细粒度状态 TTL 依赖 backup loop（review 明确要求状态自治，P2）；
> ⑤ Metrics 无高 cardinality label（低风险，确认即可）。

## 长期状态清单（谁创建/读取/修改/清理/上限/TTL）

| State | Scope | 创建 | 读取 | 修改 | 清理 | Max | TTL |
|---|---|---|---|---|---|---|---|
| `context` (deque) | per-group | get_group_state | router/AI | router | — | CONTEXT_SIZE | 随群清理 |
| `user_last_time` | per-group/user | 首次冷却检查 | can_user_reply | update_user_time | backup loop prune | 群成员数 | 24h |
| `processed_msg_ids` | per-group | 消息处理 | 去重 | 消息处理 | — | 1000 | — |
| `recent_bot_replies` | per-group | 回复 | 重复检测 | 回复 | — | 30 | — |
| `repeat_cache`/`msg_timestamps` | per-group | 复读检测 | 复读检测 | 复读检测 | 自身淘汰 | 200 | — |
| `user_ai_last_call` | global/user | budget.check | budget.check | budget.check | backup loop prune | 用户数 | 24h |
| `poke_last_time` | global/user | poke | poke | poke | backup loop prune | 用户数 | 24h |
| `last_toxic_warning` | global/group | 引战警告 | 引战警告 | 引战警告 | backup loop prune | 群数 | 24h |
| `group_ai_budget_count`/`budget_notified_groups` | global/group | budget | budget | budget | 跨天重置 | 群数 | 1 天 |
| **`groups` (GroupState dict)** | per-group | get_group_state | 全部 | 全部 | **无** | **无上限（P1）** | **无** |
| **`ai_consecutive_failures`/`ai_circuit_open_until`** | global | guarded_chat | guarded_chat | guarded_chat | 成功清零 | 1 | 冷却后 |
| task registry | global | register | shutdown | done callback | done callback | 任务数 | 任务结束 |
| metrics registry | global | 模块导入 | export | 埋点 | — | 固定集合 | — |

## 本轮修复方案（先说明，再实施）

1. **双层 Circuit Breaker**（provider 级全局 + 群级有界）：
   - `CircuitBreaker` 类：CLOSED/OPEN/HALF_OPEN 状态机、monotonic clock、HALF_OPEN 单并发 probe
   - Provider breaker：全局一个，计可重试瞬时失败（超时/网络/429/5xx），4xx 永久错误不计
   - Group breaker：per-group、ExpiringMap 容器（TTL 7 天 + max 1000 LRU 淘汰），防单群失败拖垮其他群
   - 调用顺序：Circuit admission（逻辑请求层一次）→ Budget admission（每次尝试）→ semaphore → timeout → attempt → retry decision → record_result
   - 故障分类：provider-level（网络/5xx/429/超时）→ provider breaker；group-level（该群逻辑请求连续失败）→ group breaker；用户输入/4xx/预算不足 → 不计任何 breaker
2. **ExpiringMap**（轻量 TTL 容器，monotonic + 惰性过期 + max_size 淘汰）：统一 user_last_time / user_ai_last_call / poke_last_time / last_toxic_warning / group breakers——状态自治，不再依赖 backup loop
3. **inactive 群清理**：GroupState 增加 last_activity，超过 24h 无活动的群从 groups 移除（context 短期记忆随群清理，长期记忆在 SQLite 不受影响）
4. **Metrics**：新增 ai_attempts_total / ai_circuit_rejections_total{level}，确认无 group_id/user_id label（低 cardinality）

## v1.3.0：SDK 分层（上/中/下，依赖倒置）

```text
插件（上层 plugin_sdk/）
   ↓ 依赖
中层 src/sdk/（BotEvent / BotMessage / Matcher / Rule / Listener / Permission —— 零 OneBot 命名）
   ↑ 被实现
下层 src/sdk/onebot/（sdk_dto 瘦身 + Transformer CQ 阉割 + OneBotAdapter＝复用 Sender）
   ↓
NapCat / OneBot
```

- 事件流入：NapCat WS 两端 → `message_router.process_event` →（插件通道）下层
  `Transformer.to_bot_event` 机械转换 → 领域 payload（kind/scope/text/at_list/images/reply_id）
  → `PluginManager.dispatch_event`（权限门 read_message）→ 匹配（SDK matcher 注册后仅投递
  命中事件，payload 附带 matched）→ 插件进程 SDK `route` → handler → `event.reply` →
  动作协议 → `manager._run_action`（权限门）→ `Sender`（OneBot HTTP）。
- 副作用出口保持唯一（`manager._run_action`，先过 PermissionManager）；
  主流程（AI/Memory/Context/Persona）与插件路径解耦。
