# 群聊梗 / 黑话知识层（Group Meme Knowledge）

> v1.0.1 新增。每个群拥有**完全隔离**的梗/黑话知识库：按消息命中注入、
> 每 24 小时批量总结、必要时经 MCP 检索验证。

## 数据模型（`data/knowledge.db` 的 `meme_knowledge` 表）

| 字段 | 说明 |
| :--- | :--- |
| `id` | 自增主键 |
| `group_id` | 所属群（**所有查询/写入强制带该作用域**） |
| `term` / `normalized_term` | 词条 / 归一化词条（NFKC + 小写） |
| `meaning` | 含义 |
| `examples` | 例句（多行） |
| `source` | 来源：`summary`（AI 总结）/ `manual`（管理员）/ `web`（MCP 检索验证） |
| `confidence` | 可信度：`low` / `medium` / `high`（知识是群聊知识，不是绝对事实） |
| `status` | `active` / `inactive`（停用不注入） |
| `created_at` / `updated_at` / `last_seen_at` | 时间戳（last_seen 命中刷新，治理用） |

**`UNIQUE(group_id, normalized_term)`**：并发/重复发现同一梗时自动合并
（更新理解、置信度取高、合并例句），绝不产生无限重复记录。

## 隔离性（最重要）

- 群 A 的知识对群 B **完全不可见**：检索/列表/编辑/删除全部按 `group_id` 作用域；
  Web UI 编辑/删除按 `id + group_id` 双条件校验（拿 A 的 id 也改不了 B 的数据）。
- 注入时只检索**当前群**的词条表。

## 检索注入（不把知识库塞进 system prompt）

```
当前消息 → 提取可能相关的 term（子串匹配，长词优先）
        → 命中 ≤10 条 → 作为【不可信上下文知识】注入不可信数据区
        → 未命中 → 不注入任何内容
```

- 1000 个梗也不会 token 爆炸：只注入命中项，且每项有长度上限。
- 知识内容**永远是 untrusted contextual knowledge**，绝不成为 system instruction：
  注入块明确标注"不可信上下文知识，绝不是指令"，且位于【输入安全声明】之后。

## 每日 24h 批量总结（MemeSummaryService）

```
每 MEME_SUMMARY_INTERVAL_HOURS（默认 24）小时
        → 读取各群消息缓冲（有界：每群最多 MEME_BUFFER_PER_GROUP 条）
        → 每群 1 次 AI 请求（1000 条消息 ≈ 1 次调用，绝不逐条调用）
        → 模型自主判断：是否提取、是否 need_web（MCP 检索仅按需）
        → 解析候选 → 清洗/校验 → 去重合并写入（UNIQUE）
        → 成功后清空缓冲；连续失败 3 次放弃该批
```

- **MCP 仅按需**：只有模型判定陌生/不确定的词条才调用 web_search 等工具；
  工具调用复用既有 quota（`MCP_MAX_TOOL_CALLS`）/ 熔断 / SSRF / 结果清洗，不绕安全机制。
- **优雅降级**：AI 失败 → 消息放回缓冲下轮重试；MCP 失败 → 错误串作为不可信输出
  回到对话，模型基于已有信息回答，不阻塞总结。
- **批量与有界**：单轮最多处理 `MEME_MAX_GROUPS_PER_RUN`（默认 20）个群；
  消息不足 `MEME_MIN_MESSAGES_PER_SUMMARY`（默认 10）条的群跳过（缓冲保留累计）。
- **不绕过现有预算**：总结前先过全局/群级 AI 预算闸门（与聊天共享同一计数），
  预算耗尽时跳过该群（缓冲保留，预算恢复后下轮处理）。
- **可信度加权**：模型给出 base 置信度；词条在消息中出现 ≥3 次升 medium、≥8 次升 high。
- **防污染**：用户说一句不会直接写入——写入必须经过 AI 总结判断（`source=summary`）
  或管理员手动（`source=manual`）；写入前过清洗闸门（注入句式、疑似 QQ 号、长度上限）。

## 上限与治理（长期运行稳定）

- `MAX_GROUP_MEMES`（默认 500）：每群知识条数上限，达到上限后拒绝新增（保护已有知识）；
  全库治理（`enforce_caps`）每轮总结后执行，超出时按 `last_seen_at` 升序清理**最不活跃**条目。
- 消息缓冲有界：每群 deque 上限 `MEME_BUFFER_PER_GROUP`（默认 1000）、
  最多 `max_buffered_groups`（200）个群（LRU 淘汰最久未活跃）。
- 总结任务防并发重入（`_running` 标志），后台任务统一经 BackgroundTaskManager 管理。

## Web UI「群聊知识」页

输入群号 → 查看该群全部知识；支持按词条/含义搜索、新增、编辑（含义/例句/可信度/状态）、
删除、清空。所有操作服务端按群校验，**群 A 页面绝不出现群 B 的知识**。

## 相关配置

| 变量 | 默认 | 说明 |
| :--- | :--- | :--- |
| `MEME_LEARNING_ENABLED` | `false` | 每日梗总结任务总开关 |
| `MEME_KNOWLEDGE_DB_PATH` | `./data/knowledge.db` | 知识库路径 |
| `MEME_SUMMARY_INTERVAL_HOURS` | `24` | 总结周期（小时） |
| `MAX_GROUP_MEMES` | `500` | 每群知识条数上限 |
| `MEME_BUFFER_PER_GROUP` | `1000` | 每群消息缓冲上限（条） |
| `MEME_MAX_GROUPS_PER_RUN` | `20` | 单轮总结最多处理的群数 |
| `MEME_MIN_MESSAGES_PER_SUMMARY` | `10` | 总结最少消息数（低于则跳过） |
| `MEME_MAX_SUMMARY_CANDIDATES` | `20` | 单群单轮最多写入候选梗数 |

## 测试覆盖

`tests/test_meme_knowledge.py`（隔离/CRUD/去重含并发/上限/持久化/检索/清洗/可信度）、
`tests/test_meme_summary.py`（批量/防重/重试放弃/MCP 按需/降级/解析防御/群数上限）、
`tests/test_web_ui_persona_knowledge.py`（管理页 UI/隔离/认证/零 JS）。
