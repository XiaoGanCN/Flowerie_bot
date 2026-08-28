# MCP（外部工具）

花璃通过 MCP 调用外部工具获取实时/网络信息，由**模型自主判断**是否需要工具——不是每条消息都调工具。

## 启用

```ini
MCP_ENABLED=true
MCP_SERVER_URL=http://127.0.0.1:9000/mcp   # 你的 MCP server 地址
MCP_ALLOWED_TOOLS=web_search, fetch_url     # 工具白名单
```

## 工作流程

```
用户问题 → 模型判断 → 不需要工具 → 直接回答
                    → 需要实时信息 → 调用白名单工具 → 结果回填 → 模型最终回答
```

## 安全边界

- **默认关闭**：`MCP_ENABLED=false`，只有管理员主动配置后启用
- **工具白名单**：`MCP_ALLOWED_TOOLS` 外的工具一律拒绝（如 server 暴露了 `execute_shell` 但不在白名单 → 拒绝）
- **超时**：单次工具调用 `MCP_TIMEOUT` 秒超时
- **上限**：单轮对话最多 `MCP_MAX_TOOL_CALLS` 次工具调用，绝不无限循环
- **独立熔断**：MCP 连续失败只熔断 MCP 自身，不会打开 AI Provider 熔断
- **指标**：`mcp_calls_total` / `mcp_call_failures_total` / `mcp_call_latency_seconds` / `mcp_tool_rejections_total`（低基数 label）

## 指标

工具调用在 Metrics 中单独统计，与 AI 请求分离。
