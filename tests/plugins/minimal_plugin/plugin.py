"""最小可执行插件（Plugin Runtime 端到端验证用）。

契约：收到 on_message 事件时返回一个 test action。
不调用任何真实 QQ / MCP / Memory / filesystem —— 越小越好。
"""
import json


def on_message(event, api=None):
    """返回单个 action（Flowerie 捕获后经 PermissionManager 验证）。"""
    return {"type": "test", "message": "plugin-ok", "event": event.get("event", "")}


if __name__ == "__main__":
    # 直接运行（不经 runner）仅用于人工验证语法/行为
    print(json.dumps(on_message({"event": "message"})))
