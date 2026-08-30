"""Plugin Permission System：权限枚举 + 运行时强制检查。

设计原则（requirement 1.4）：
- 插件默认权限 = 0；manifest 声明权限；管理员启用时批准（可批准子集）
- 权限不是提示文字：**所有 Action 在执行前必须过 PermissionManager.check()**，
  拒绝一律记日志（plugin_permission_denied），由管理员决定是否放开
- 插件永远无法自己决定权限（本模块是唯一检查点，副作用在 manager 执行）

权限集（v1）：
- send_message       发送群/私聊消息
- read_message       接收消息事件（未批准则事件不投递给插件）
- read_group_info    读取群信息（get_group）
- read_user_info     读取用户信息（get_user）
- read_memory        读取记忆（get_memory）
- write_memory       写入记忆（write_memory）
- http_request       发起受限 HTTP 请求（SSRF 防护）
- filesystem_read    插件目录只读访问（file_read）
- filesystem_write   插件目录写访问（file_write）
- execute_process    执行进程 —— **保留定义，v1 无对应 Action**（一律拒绝）
- webhook            外部 Webhook —— **保留定义，v1 无对应 Action**（一律拒绝）

保留权限说明：为 API 兼容而定义并参与校验，但 v1 未实现任何执行路径；
即使管理员批准，运行时 Action 检查也会返回 "not supported in v1"。
"""
from typing import Dict, Optional

# 完整权限集（manifest 校验 + 运行时检查共用）
ALL_PERMISSIONS = frozenset({
    "send_message",
    "read_message",
    "read_group_info",
    "read_user_info",
    "read_memory",
    "write_memory",
    "http_request",
    "filesystem_read",
    "filesystem_write",
    "execute_process",
    "webhook",
    "delete_message",        # 撤回（仅限本 bot 发送过的消息）
    "read_message_history",  # 消息详情/群历史/上下文读取
    "group_manage",          # 群管理写操作（禁言/踢人/管理员）
})

# Action 类型 → 所需权限（None = 无需权限：log / test 等无害动作）
ACTION_PERMISSIONS: Dict[str, Optional[str]] = {
    "send_message": "send_message",
    "send_reply": "send_message",
    "delete_message": "delete_message",
    "get_message": "read_message_history",
    "get_group_history": "read_message_history",
    "get_context": "read_message_history",
    "get_group_member": "read_group_info",
    "get_group_members": "read_group_info",
    "group_ban": "group_manage",
    "group_kick": "group_manage",
    "group_admin": "group_manage",
    "send_private_message": "send_message",
    "get_group": "read_group_info",
    "get_user": "read_user_info",
    "get_memory": "read_memory",
    "write_memory": "write_memory",
    "http_request": "http_request",
    "file_read": "filesystem_read",
    "file_write": "filesystem_write",
    "execute_process": "execute_process",
    "webhook": "webhook",
    "log": None,
    "test": None,
}

# v1 未实现的保留 Action（即使权限已批准也拒绝执行）
_UNIMPLEMENTED = frozenset({"execute_process", "webhook"})
# 内建无副作用动作：任何权限组合下都允许（仅日志/测试探针，无外部副作用）
_BUILTIN_ACTIONS = frozenset({"log", "test"})


class PermissionDeniedError(Exception):
    """权限被拒绝（含拒绝原因，供日志/测试断言）。"""

    def __init__(self, action: str, permission: str, plugin_id: str = ""):
        self.action = action
        self.permission = permission
        self.plugin_id = plugin_id
        super().__init__(
            f"plugin {plugin_id or '?'} action {action!r} requires permission {permission!r} (denied)"
        )


class PermissionManager:
    """单个插件的运行时权限门（管理员批准集 + 保护级别）。

    保护级别只影响**资源限制与预留动作**，不影响权限强制：
    - normal：默认（完整限制）
    - relaxed：放宽非必要限制（超时/输出/动作数）
    - unsafe：管理员明确确认，保留全部安全不变式（安装完整性/manifest 校验/
      管理员权限/进程隔离/日志/崩溃保护/资源限制/权限检查），仅更宽限制
    """

    PROTECTION_LEVELS = ("normal", "relaxed", "unsafe")

    def __init__(self, approved_permissions, protection: str = "normal"):
        self.approved = frozenset(str(p).strip().lower() for p in (approved_permissions or []) if p)
        self.protection = protection if protection in self.PROTECTION_LEVELS else "normal"

    def has(self, permission: str) -> bool:
        return permission in self.approved

    def check(self, action: str) -> bool:
        """Action 能否执行（运行时唯一检查点）。"""
        if action in _UNIMPLEMENTED:
            return False  # v1 不支持：即使批准也拒绝（诚实接口）
        permission = ACTION_PERMISSIONS.get(action)
        if permission is None:
            # 白名单原则：仅内建无副作用动作放行；未知 action 一律拒绝
            return action in _BUILTIN_ACTIONS
        return permission in self.approved

    def denied_reason(self, action: str) -> str:
        if action in _UNIMPLEMENTED:
            return f"action {action!r} 在 Plugin API v1 未实现（保留权限）"
        permission = ACTION_PERMISSIONS.get(action)
        if permission is None:
            return "未知 action，不允许执行（白名单外）" if action not in _BUILTIN_ACTIONS else ""
        return f"需要权限 {permission!r}（管理员未批准）"

    # ---------- 保护级别对应的资源限制 ----------
    @staticmethod
    def limits(protection: str) -> Dict[str, float]:
        """保护级别 → 资源限制表（不同级别放宽限制，不变式保持）。"""
        if protection == "unsafe":
            # 仅可信插件：超时/输出/动作数放宽；仍过权限检查与日志
            return {"event_timeout": 120.0, "startup_timeout": 30.0,
                    "max_actions": 32, "max_output_bytes": 4 * 1024 * 1024}
        if protection == "relaxed":
            return {"event_timeout": 60.0, "startup_timeout": 20.0,
                    "max_actions": 16, "max_output_bytes": 1024 * 1024}
        return {"event_timeout": 15.0, "startup_timeout": 10.0,
                "max_actions": 8, "max_output_bytes": 256 * 1024}
