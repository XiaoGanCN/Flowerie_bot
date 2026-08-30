"""Plugin Permission System 白盒测试：默认为 0 权限、运行时强制检查、保护级别限制。"""
from src.plugins.permissions import (
    ACTION_PERMISSIONS,
    ALL_PERMISSIONS,
    PermissionDeniedError,
    PermissionManager,
)


def test_all_required_permissions_defined():
    # requirement 1.4 至少包含这些权限
    for name in ("send_message", "read_message", "read_group_info", "read_user_info",
                 "read_memory", "write_memory", "http_request", "filesystem_read",
                 "filesystem_write", "execute_process", "webhook"):
        assert name in ALL_PERMISSIONS


def test_action_to_permission_mapping_complete():
    for action in ("send_message", "send_private_message", "get_group", "get_user",
                   "get_memory", "write_memory", "http_request", "file_read",
                   "file_write", "execute_process", "webhook", "log", "test"):
        assert action in ACTION_PERMISSIONS, f"{action} 缺权限映射"


def test_default_zero_permissions():
    pm = PermissionManager([])
    assert pm.check("send_message") is False
    assert pm.check("get_memory") is False
    assert pm.has("send_message") is False


def test_approved_permission_allows_action():
    pm = PermissionManager(["send_message"])
    assert pm.check("send_message") is True
    assert pm.check("send_private_message") is True      # 同权限族
    assert pm.check("get_group") is False               # 未批准


def test_harmless_actions_always_allowed():
    pm = PermissionManager([])
    assert pm.check("log") is True
    assert pm.check("test") is True


def test_reserved_permissions_never_execute():
    """execute_process / webhook 为保留定义：即使批准也一律拒绝（v1 无实现）。"""
    pm = PermissionManager(["execute_process", "webhook"])
    assert pm.check("execute_process") is False
    assert pm.check("webhook") is False
    assert "未实现" in pm.denied_reason("execute_process")


def test_permission_denied_error_message():
    pm = PermissionManager([])
    assert pm.check("send_message") is False
    e = PermissionDeniedError("send_message", "send_message", "p1")
    assert "send_message" in str(e)


def test_protection_levels_limits():
    normal = PermissionManager.limits("normal")
    relaxed = PermissionManager.limits("relaxed")
    unsafe = PermissionManager.limits("unsafe")
    # unsafe 限制更宽，但仍然是有限资源（不是无限制）
    assert normal["event_timeout"] <= relaxed["event_timeout"] <= unsafe["event_timeout"]
    assert normal["max_actions"] <= relaxed["max_actions"] <= unsafe["max_actions"]
    assert unsafe["max_output_bytes"] < 100 * 1024 * 1024  # 仍是硬上限


def test_permission_check_not_bypassable_by_level():
    """关闭保护（unsafe）也不能绕过权限（权限强制是唯一检查点）。"""
    for level in ("normal", "relaxed", "unsafe"):
        pm = PermissionManager([], level)
        assert pm.check("send_message") is False
        assert pm.check("http_request") is False
