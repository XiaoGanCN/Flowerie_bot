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

当前 297 个测试：并发安全、故障隔离、熔断、状态治理、Prompt/Sticker/MCP/Web UI、SSRF/注入回归、MCP 额度/安全、配置持久化/校验。

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
├── core/           # 消息路由/组装/策略/预算/WS 服务
├── services/       # AI 客户端/记忆/文件解析/发送/表情包/MCP/配置服务/Web UI
├── repositories/   # SQLite 存储层（记忆/设置/表情包索引）
└── utils/          # 日志/trace/指标/熔断/过期容器/任务管理
tests/              # 297 个测试
docs/               # 文档
```

## 架构

架构审计报告见 [architecture-audit.md](architecture-audit.md)（含三轮工程审计结论）。
