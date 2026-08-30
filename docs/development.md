# 开发

## 环境

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

## 测试

```bash
pytest              # 全部测试
pytest tests/xxx    # 单文件
```

当前 535 个测试：并发安全、故障隔离、熔断、状态治理、Prompt/Sticker/MCP/Web UI、SSRF/注入回归、MCP 额度/安全、配置持久化/校验、Web UI 注册/无 JS 面板、**Persona 系统、群聊 Meme Knowledge、每日总结、Web UI 人格/知识页**（新增 `test_persona_manager.py` / `test_meme_knowledge.py` / `test_meme_summary.py` / `test_web_ui_persona_knowledge.py`）。

## 代码检查

```bash
ruff check .        # lint（E/F/W/I/B 规则集）
```

## CI

GitHub Actions（`.github/workflows/ci.yml`）在 push/PR 时自动运行：

- Python 3.9 / 3.12 双版本
- `ruff check .` + `pytest`

## 目录结构

```
src/
├── core/           # 消息路由(委托AiGateway)/组装/策略/预算/WS 服务
│   └── ai_gateway.py   # AI 准入层（熔断/预算/人格/知识/重试，从 Router 拆分）
├── services/       # AI 客户端/记忆/文件解析/发送/表情包/MCP/配置服务/Web UI
│   ├── ai_client.py       # 薄封装：chat/工具循环/回复解析（~350 行）
│   ├── prompt_builder.py  # system prompt 组装（从 AIClient 拆分）
│   ├── vision.py          # 视觉识图 VisionService（从 AIClient 拆分）
│   ├── toxic_detector.py  # 引战检测 ToxicDetector（从 AIClient 拆分）
│   ├── config_schema.py   # 配置声明 SCHEMA（从 ConfigService 拆分）
│   ├── web_ui.py          # Web UI 薄门面（~340 行；面板 mixin 聚合）
│   ├── webui_panels/      # 各功能域处理器 mixin（account/auth/config/appearance/mcp/persona/knowledge/prompt）
│   ├── webui_render/      # 渲染层（theme/pages/config_panel/appearance/persona/knowledge）
│   ├── persona_manager/persona_presets（人格）
│   ├── meme_knowledge_manager/meme_summary（群聊梗知识/每日总结）
│   └── system_status.py     # 服务器状态采集（用户状态页用，零依赖 /proc）
├── repositories/   # SQLite 存储层（记忆/设置/表情包索引/梗知识）
└── utils/          # 日志/trace/指标/熔断/过期容器/任务管理
tests/              # 535 个测试
docs/               # 文档
```

## 架构

架构审计报告见 [architecture-audit.md](architecture-audit.md)（含三轮工程审计结论）。

## Bot SDK 开发（v1.3.0+）

- 分层：上层 `plugin_sdk/` → 中层 `src/sdk/`（零 OneBot）→ 下层 `src/sdk/onebot/`
- 新增平台能力：只改下层 `onebot/`（dto/transformer/adapter），中层上层不动
- 测试：`tests/test_sdk_*.py`（matcher/listener/adapter/permission/message）
- 文档：[sdk.md](sdk.md) / [api.md](api.md) / [plugins.md](plugins.md)
