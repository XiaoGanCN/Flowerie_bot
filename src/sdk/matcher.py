"""Matcher 与 Rule：统一消息匹配体系。

规则（固定，写入文档）：
- priority：**数字大者先匹配**（与声明式插件规则一致）；默认 0
- block（=stop）：命中后阻断**后续 Matcher**（同插件内的匹配链停止）；
  跨插件不阻断（事件隔离原则）
"""

import inspect
import re
from typing import Any, Callable, Dict, List, Optional

# Matcher 元数据挂载点（装饰器写入函数属性，供 SDK/注册器收集）
MATCHER_ATTR = "__flowerie_matchers__"


class Rule:
    """可扩展匹配条件（AND 组合）。条件可以是谓词或关键字参数。

    内置：is_group / is_private / is_bot_admin / is_bot_owner /
          is_group_admin / is_group_owner / user_id / group_id /
          自定义 callable(event, bot) -> bool
    """

    def __init__(self, **conditions: Any):
        self.conditions: Dict[str, Any] = dict(conditions)

    async def amatches(self, event, bot=None) -> bool:
        """完整匹配（异步）：支持全部内建条件与自定义 async/sync 谓词。"""
        for key, value in self.conditions.items():
            if key == "is_group":
                if event.is_group != bool(value):
                    return False
            elif key == "is_private":
                if event.is_private != bool(value):
                    return False
            elif key == "is_bot_admin":
                if bot is None or await bot.is_admin(event) != bool(value):
                    return False
            elif key == "is_bot_owner":
                if bot is None or await bot.is_owner(event) != bool(value):
                    return False
            elif key == "is_group_admin":
                if bot is None or await bot.is_group_admin(event.group_id, event.user_id) != bool(value):
                    return False
            elif key == "is_group_owner":
                if bot is None or await bot.is_group_owner(event.group_id, event.user_id) != bool(value):
                    return False
            elif key == "user_id":
                if str(event.user_id) != str(value):
                    return False
            elif key == "group_id":
                if str(event.group_id) != str(value):
                    return False
            else:
                # 自定义条件：callable(event, bot)，支持 async
                if not callable(value):
                    return False
                result = value(event, bot)
                if inspect.isawaitable(result):
                    result = await result
                if not result:
                    return False
        return True

    def matches(self, event, bot=None) -> bool:
        """同步匹配（仅非网络条件，供本地/测试快速使用）。"""
        for key, value in self.conditions.items():
            if key in ("is_bot_admin", "is_bot_owner", "is_group_admin", "is_group_owner"):
                return False  # 需要 bot：请用 amatches
            if key == "is_group":
                if event.is_group != bool(value):
                    return False
            elif key == "is_private":
                if event.is_private != bool(value):
                    return False
            elif key == "user_id":
                if str(event.user_id) != str(value):
                    return False
            elif key == "group_id":
                if str(event.group_id) != str(value):
                    return False
            elif callable(value) and not value(event, bot):
                return False
        return True

    def __add__(self, other: "Rule") -> "Rule":  # 规则组合（AND）
        merged = dict(self.conditions)
        merged.update(other.conditions)
        return Rule(**merged)


class Matcher:
    """一条匹配规则：command/keyword/regex/prefix/exact + priority + block + rule。"""

    def __init__(self, kind: str, pattern: Any = None, *,
                 priority: int = 0, block: bool = False,
                 rule: Optional[Rule] = None, name: Optional[str] = None):
        if kind not in ("command", "keyword", "regex", "prefix", "exact"):
            raise ValueError(f"未知 matcher 类型: {kind}")
        self.kind = kind
        self.pattern = pattern
        self.priority = int(priority)
        self.block = bool(block)
        self.rule = rule
        self.name = name

    async def amatches(self, event, bot=None) -> bool:
        """完整匹配（含 Rule 条件）。"""
        if not self.matches(event, bot):
            return False
        if self.rule is not None:
            return await self.rule.amatches(event, bot)
        return True

    def matches(self, event, bot=None) -> bool:
        text = str(event.text or "")
        if self.kind == "command":
            parts = text.strip().split(maxsplit=1)
            if not parts:
                return False
            cmd = parts[0].lstrip("/!.")
            if cmd != str(self.pattern).lstrip("/!."):
                return False
            event._matcher_args = parts[1] if len(parts) > 1 else ""
            return True
        if self.kind == "keyword":
            return str(self.pattern) in text
        if self.kind == "regex":
            try:
                return re.search(str(self.pattern)[:200], text) is not None
            except re.error:
                return False
        if self.kind == "prefix":
            return text.startswith(str(self.pattern))
        if self.kind == "exact":
            return text == str(self.pattern)
        return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Matcher {self.kind}({self.pattern!r}) p={self.priority} block={self.block}>"


# ---------- 装饰器（收集到函数 __flowerie_matchers__） ----------
def _decorator(kind: str, pattern: Any, /, **kw) -> Callable:
    def wrap(func):
        matcher = Matcher(kind, pattern, name=kw.pop("name", func.__name__), **kw)
        existing = list(getattr(func, MATCHER_ATTR, []))
        existing.append(matcher)
        setattr(func, MATCHER_ATTR, existing)
        return func
    return wrap


def command(name: str, /, **kw) -> Callable:
    """@command("hello") —— 命令匹配（自动支持 / ! . 前缀与空白参数拆分）。"""
    return _decorator("command", name, **kw)


def keyword(text: str, /, **kw) -> Callable:
    return _decorator("keyword", text, **kw)


def regex(pattern: str, /, **kw) -> Callable:
    return _decorator("regex", pattern, **kw)


def prefix(text: str, /, **kw) -> Callable:
    return _decorator("prefix", text, **kw)


def exact(text: str, /, **kw) -> Callable:
    return _decorator("exact", text, **kw)


def rule(**conditions) -> Rule:
    return Rule(**conditions)


def collect_matchers(func) -> List[Matcher]:
    """收集函数上的全部 matcher（插件 SDK 注册用）。"""
    return list(getattr(func, MATCHER_ATTR, []))
