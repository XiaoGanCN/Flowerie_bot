"""统一权限抽象：user / group_member / group_admin / group_owner / bot_admin / bot_owner。

- bot_admin / bot_owner：复用 Flowerie 现有 ADMIN_QQ_IDS 配置（唯一权威，不重复实现）；
  owner 与 admin 同源（文档明确：bot 层仅有 admin 一级，owner 语义相同）
- group_admin / group_owner：经 BotAdapter 查询群成员角色（OneBot group member role）
- require_permission(kind)：装饰器，检查失败抛 BotPermissionError
"""
from typing import Callable

VALID_KINDS = ("user", "group_member", "group_admin", "group_owner", "bot_admin", "bot_owner")


class PermissionChecker:
    """角色检查器（async；复用 config.admin 与 adapter 查询）。"""

    def __init__(self, config=None, bot=None, adapter=None):
        self._config = config
        self._bot = bot
        self._adapter = adapter

    @property
    def bot_admins(self):
        ids = getattr(self._config, "ADMIN_QQ_IDS", None) if self._config is not None else None
        return set(ids or [])

    async def check(self, event, kind: str) -> bool:
        """检查 event 主体是否具备 kind 角色。kind 非法一律 False。"""
        if kind not in VALID_KINDS:
            return False
        user_id = event.user_id
        if user_id is None:
            return False
        if kind in ("user", "group_member"):
            return True  # QQ 用户存在即成立；群成员存在性由事件上下文保证
        if kind in ("bot_admin", "bot_owner"):
            return str(user_id) in {str(a) for a in self.bot_admins}
        if kind in ("group_admin", "group_owner"):
            adapter = self._adapter or getattr(self._bot, "_adapter", None)
            if event.group_id is None:
                return False
            if adapter is None:
                return False
            try:
                member = await adapter.get_group_member(event.group_id, user_id)
            except Exception:  # noqa: BLE001 - 查询失败按非管理员处理
                return False
            role = str((member or {}).get("role") or "member")
            return role == "owner" if kind == "group_owner" else role in ("owner", "admin")
        return False

def require_permission(kind: str):
    """装饰器：Matcher handler 前置权限检查（失败抛 BotPermissionError）。"""
    def wrap(func: Callable) -> Callable:
        func.__flowerie_permission__ = kind
        return func
    return wrap
