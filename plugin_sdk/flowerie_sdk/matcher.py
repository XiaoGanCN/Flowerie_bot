"""插件侧 Matcher 装饰器：收集元数据（JSON 可序列化），上报主进程注册。

优先级规则（与主进程一致，见 docs/sdk.md）：数字大者先匹配；
block=True 命中后阻断同插件后续 Matcher。
"""
from typing import Any, Callable, Dict, List

MATCHER_ATTR = "__flowerie_matchers__"


def command(name: str, /, **kw) -> Callable:
    return _mk("command", name, **kw)


def keyword(text: str, /, **kw) -> Callable:
    return _mk("keyword", text, **kw)


def regex(pattern: str, /, **kw) -> Callable:
    return _mk("regex", pattern, **kw)


def prefix(text: str, /, **kw) -> Callable:
    return _mk("prefix", text, **kw)


def exact(text: str, /, **kw) -> Callable:
    return _mk("exact", text, **kw)


def rule(**conditions) -> Dict[str, Any]:
    """Rule 条件：is_group/is_private/is_bot_admin/is_bot_owner/
    is_group_admin/is_group_owner/user_id/group_id/自定义谓词（仅服务端支持 key 形式）。"""
    return {"conditions": dict(conditions)}


def _mk(kind: str, pattern: Any, **kw) -> Callable:
    def wrap(func: Callable) -> Callable:
        matcher = {
            "kind": kind, "pattern": str(pattern), "priority": int(kw.get("priority", 0)),
            "block": bool(kw.get("block", False)),
            "name": str(kw.get("name") or func.__name__),
        }
        r = kw.get("rule")
        if isinstance(r, dict):
            matcher["rule"] = r.get("conditions", {}) if r.get("conditions") else r
        existing = list(getattr(func, MATCHER_ATTR, []))
        existing.append(matcher)
        setattr(func, MATCHER_ATTR, existing)
        return func
    return wrap


def collect(func) -> List[Dict[str, Any]]:
    return list(getattr(func, MATCHER_ATTR, []))
