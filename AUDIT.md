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
> - **v1.0.1 新功能轮**：Persona 人格系统（全局/群聊/自定义三级 + 花璃/ATRI 双预设）、
>   群聊 Meme Knowledge（按群隔离、命中注入、每日 24h 批量总结 + MCP 按需检索）、
>   Web UI 人格/群聊知识管理页（零 JS）—— ✅ 完成（CI 全绿后验收通过）
>
> - **第五轮：上帝类拆分 + .env 防旧值覆盖**：WebUIServer（1129→336 行，功能域
>   mixin 拆分）、渲染层（webui_render/ 包）、AIClient（800→353 行，拆出
>   prompt_builder/vision/toxic_detector）、ConfigService（689→455 行，拆出
>   config_schema）、MessageRouter（732→564 行，拆出 ai_gateway）；
>   本地手工修改的 .env 不再被 settings.db 旧值覆盖（较新优先 + 同步）—— ✅ 完成
>
> **当前基线**：测试 **535** 个（pytest + ruff 全过，CI Python 3.9 / 3.12 全绿）。

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

# 第四轮：v1.0.1 新增功能专项审计（Persona / Meme / Web UI）

> 审计对象：v1.0.1 新增代码（persona_manager / persona_presets / meme_knowledge_manager /
> meme_knowledge_repository / meme_summary / ai_client 人格注入 / web_ui 人格与知识页）
> 审计方式：代码审查 + 本地纯模块复现 + CI 全量测试；结论：**507 测试全绿，验收通过**。

## 本轮发现并修复的问题

| # | 问题 | 风险 | 修复 |
| :--- | :--- | :--- | :--- |
| 1 | 每日梗总结的 AI 调用**绕过三层预算**（DAILY/GROUP_DAILY_AI_CALL_BUDGET） | 高：聊天额度耗尽后总结仍可烧 API，违反"新增知识层不绕过现有安全机制" | MemeSummaryService 注入共享 BudgetManager；run_once 对每个候选群先过预算闸门，耗尽即跳过（缓冲保留、不计重试）；main.py 创建共享 budget 实例注入 router 与总结任务 |
| 2 | meme 知识注入前无二次清洗（DB 被手工改库时可带注入句式） | 中：纵深防御缺失 | ai_client 组装知识块前 sanitize_untrusted_text 兜底清洗 + 日志 |
| 3 | 知识搜索 LIKE 通配符未转义（`%`/`_`/`\` 变通配符放大匹配） | 低：行为异常 | repository 搜索参数转义 + `ESCAPE '\\'` |
| 4 | 自定义人格数量无上限 | 低：长期运行可无限增长（任务 23 要求有界） | `PERSONA_MAX_COUNT`（默认 200）+ 创建时拒绝 + 启动校验 + Web UI 配置项 |
| 5 | 每轮总结后全库 enforce_caps 扫描（全表遍历） | 低：无谓开销 | 改为只治理本轮处理过的群（enforce_caps 保留作全库入口） |
| 6 | acceptance 验收脚本未关闭 knowledge.db 连接 | 低：进程退出即回收 | 收尾补 `_mrepo.close()` |

## 新增测试（本轮的 review 覆盖）

- `test_summary_respects_budget_gate` / `test_summary_budget_group_gate`：全局/群预算耗尽 → 总结跳过、零 AI 调用、缓冲保留
- `test_meme_100_groups_isolation`：100 群各自隔离、计数精确、互不串线（任务 37）
- `test_meme_search_wildcard_escaped`：LIKE 通配符按字面匹配
- `test_persona_count_limit`：自定义人格数量上限（内置不计）
- `test_dirty_meme_db_content_sanitized_on_injection`：改库污染内容注入前被清洗
- `test_meme_summary_task_registered_and_shutdown`：总结任务注册与优雅关闭无泄漏（任务 41/43）

## 专项检查结论（任务 29 质量门禁逐项）

- 未关闭 task：总结/备份/主动聊天等全部经 TaskManager 注册，stop 后 running_count=0 ✅
- HTTP session 泄漏：AIClient/Sender 走 async with 生命周期 ✅（本轮未新增 HTTP 资源）
- SQLite connection 泄漏：新增 knowledge.db 由 meme_manager.close() 关闭，测试与验收均收尾关闭 ✅
- 无限增长 dict：消息缓冲有界（群数 LRU + deque 上限）、重试计数随放弃清理、人格/知识/绑定全有上限 ✅
- 高 cardinality metrics：新增指标无 label 或枚举 label（reason/tool）✅
- Prompt injection：知识写入拒绝注入词条、注入前二次清洗、知识区永远在不可信数据区内 ✅
- MCP 安全边界：总结任务复用 McpToolManager（allowlist/熔断/SSRF/结果清洗/quota）✅
- 群数据/Persona/Memory/Context 串线：全部按 group_id 作用域 + 双条件编辑 + 隔离测试 ✅
- Web UI 零 JS：新页签经黑盒与渲染级扫描（`<script`/onclick/onchange/oninput/fetch/XMLHttpRequest 全无）✅

# 第五轮：架构拆分专项审计（上帝类 / .env 防覆盖 / 注销 / 配置移页）

> 审计对象：webui_panels / webui_render / ai_gateway / prompt_builder / vision /
> toxic_detector / config_schema 拆分、.env 较新优先、注销账号、配置移页
> 审计方式：AST 结构检查 + 行数门禁 + 委托链核对 + 边界场景推演；结论：全绿。

## 发现并修复的问题

| # | 问题 | 风险 | 修复 |
| :--- | :--- | :--- | :--- |
| 1 | AiGateway 的 **budget 仍是构造时快照**（ai_client/tool_manager 已是 provider）——测试会直接替换 `router.budget`，当前因 BudgetManager 无内部状态而侥幸等价，但属隐患 | 中 | budget 同样改为 provider 动态读取 |
| 2 | 群聊知识配置保存后 **gid 丢失**（handler 读 query 而非 form，表单无隐藏域） | 低 | 表单加 `gid` 隐藏域 + handler 从 form 读取 |
| 3 | 注销成功后若 `WEB_UI_ENABLED=true` 且 .env 密码被清除，**重启会被启动校验拒绝**（无密码不允许裸奔）——提示缺失会让用户困惑 | 低 | 注销返回消息补充说明（需重新配置密码或注册） |
| 4 | 拆分时 @staticmethod 装饰器丢失 ×5、方法首行缩进丢失导致方法悬在类外（vision/gateway）——已在前轮修复并由 `test_split_modules_keep_class_structure` 防回归 | 已修复 | — |

## 专项核验（本轮）

- MRO：WebUI 全部 mixin 无重复方法定义 ✅
- 行数门禁：web_ui 339 / web_ui_assets 43 / ai_client 353 / config_service 503 / message_router 568，全在限内 ✅
- 委托链：router.guarded_chat/_ai_allowed/guarded_is_toxic/_get_group_breaker → AiGateway；AIClient → VisionService/ToxicDetector/PromptBuilder，provider 动态读取 ✅
- .env 防覆盖：env_values 读取 → mtime 比较 → db 同步 → 应用，顺序正确 ✅
- 注销：密码验证（失败计入限流）、成功清 db+.env 凭据（仅这两个 key）、强制登出、其他配置保留 ✅
- SCHEMA 引用兼容：ConfigService.SCHEMA 类属性别名保留（测试 15 处引用全部兼容）✅

## 新增测试（第五轮）

- `test_knowledge_config_keeps_gid_after_save`：配置保存后仍停留在原群
- `test_unregister_message_mentions_restart_note`：注销提示含启动说明

# 第六轮：用户状态页专项审计（账户/注销/服务器/MCP/API 状态 + 登录框修复）

> 审计对象：webui_panels/account_panel.py、webui_render/account.py、system_status.py、
> 注销迁移、登录框宽度修复、MCP/API 状态接入
> 审计方式：结构核验 + 渲染体检 + 边界推演；结论：全绿。

## 本轮改动与核验

| 项 | 说明 | 核验 |
| :--- | :--- | :--- |
| 登录框宽度 | username 输入框补 `type="text"`（原无 type → CSS `[type=text]` 选择器不匹配而变窄） | ✅ 登录/注册页一致 |
| 用户状态页 | 导航栏新增 tab：当前管理员/凭据来源 + 注销表单（仅清账号密码）+ 服务器状态 + MCP 工具 + API 连接状态 | ✅ 全部渲染 |
| 注销迁移 | 注销表单从面板 body 底部移入「用户状态」页（handler 迁至 AccountPanelMixin） | ✅ MRO 无重复 |
| 服务器状态 | system_status.py 零依赖读 /proc/meminfo + loadavg + platform；失败降级 N/A | ✅ |
| MCP/API 状态 | MCP 各 server 工具数/熔断；API 配置层面状态（URL/模型/Key/独立或回退） | ✅ |

## 关键推演（无 bug）

- 注销后 `_effective_credentials` 回退到 .env/config 默认（admin / 空密码）→ 无法登录，需重新配置/注册（符合"回到未注册状态"）
- 注销成功 → token 清空 → 未登录访问 `/panel` 回登录页（msg 不显示，但语义正确）
- MCP 未启用 / tool_manager 为 None → 状态页显示"未启用"（短路保护）
- 所有新文件零 JS（account_panel/account/system_status 扫描无脚本特征）
- AccountPanelMixin 方法归属类 + 无重复定义（MRO）✅

## 新增测试（第六轮）

- `test_account_tab_renders_all_status`：账户/注销/服务器/MCP/API 状态齐全
- `test_unregister_form_not_in_body_bottom`：注销表单只在用户状态页
- `test_account_tab_is_js_free`：用户状态页零 JS
- `test_login_and_register_username_input_width_consistent`：登录/注册框一致
- 结构防回归测试补 AccountPanelMixin

## v1.3.0 SDK 审计摘要

- 插件通道 OneBot 耦合已收入下层 `src/sdk/onebot/`（DTO/Transformer/Adapter）；
  中层零 OneBot 命名（grep 校验通过），上层插件零网络依赖。
- 消息/群/权限能力复用现有 Sender 与 ADMIN_QQ_IDS，未重写 Router/Memory/Context。
- 已知边界：主流程（_handle_message 内）仍直接访问 OneBot 字段（Router 稳定性优先，
  下阶段可逐步迁移至 transformer）；`get_user_info`/`get_group_info` 平台无标准端点，
  SDK 抛 UnsupportedOperationError（文档说明）。
