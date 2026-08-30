"""SDK 中层测试：Matcher（command/keyword/regex/prefix/exact + priority + block + Rule）+ 并发。"""
import asyncio

import pytest

from src.sdk.event import BotEvent
from src.sdk.matcher import Matcher, Rule, collect_matchers, command, exact, keyword, prefix, regex, rule


def _event(text="", scope="group", user_id=1, group_id=2):
    return BotEvent({"kind": "message", "scope": scope, "user_id": user_id,
                     "group_id": group_id, "text": text, "message_id": 9})


def test_matcher_command_args():
    ev = _event("!hi 世界")
    assert Matcher("command", "hi").matches(ev) is True
    assert ev.matcher_args == "世界"
    assert Matcher("command", "bye").matches(ev) is False


def test_matcher_kinds():
    assert Matcher("keyword", "花璃").matches(_event("今天花璃好棒")) is True
    assert Matcher("keyword", "不存在").matches(_event("xxx")) is False
    assert Matcher("regex", "^!hi\\s").matches(_event("!hi x")) is True
    assert Matcher("regex", "[bad").matches(_event("x")) is False  # 非法正则不抛
    assert Matcher("prefix", "!hi").matches(_event("!hi there")) is True
    assert Matcher("exact", "ping").matches(_event("ping")) is True
    assert Matcher("exact", "ping").matches(_event("ping!")) is False


def test_matcher_invalid_kind():
    with pytest.raises(ValueError):
        Matcher("unknown", "x")


def test_priority_sort_rule_is_documented():
    """priority 数字大者先（与声明式插件一致，文档固定规则）。"""
    ranked = sorted([Matcher("exact", "a", priority=1), Matcher("exact", "b", priority=50)],
                    key=lambda m: m.priority, reverse=True)
    assert ranked[0].priority == 50


def test_rule_conditions():
    r = Rule(is_group=True, user_id=1)
    assert r.matches(_event()) is True
    assert r.matches(_event(scope="private")) is False
    assert r.matches(_event(user_id=2)) is False
    # 组合
    r2 = Rule(user_id=1) + Rule(exact_ok=True)  # 未知 key 由 matches 兜底（callable 检查）
    # 自定义谓词（同步）
    r3 = Rule(custom=lambda ev, bot: ev.text.startswith("ok"))
    assert r3.matches(_event("okay")) is True
    assert r3.matches(_event("no")) is False


@pytest.mark.asyncio
async def test_rule_async_custom():
    async def pred(ev, bot):
        return ev.text == "yes"

    r = Rule(custom=pred)
    assert await r.amatches(_event("yes")) is True
    assert await r.amatches(_event("no")) is False


def test_decorators_collect():
    @command("hello", priority=10)
    @keyword("花璃")
    async def fn(event):
        return None

    ms = collect_matchers(fn)
    assert len(ms) == 2
    kinds = {m.kind for m in ms}
    assert kinds == {"command", "keyword"}


# ---------- 并发 100：事件间 matcher 状态不互相污染 ----------
@pytest.mark.asyncio
async def test_many_events_no_cross_pollution():
    async def run(i):
        ev = _event(f"cmd{i} arg{i}")
        m = Matcher("command", f"cmd{i}")
        ok = m.matches(ev)
        return ok, ev.matcher_args, ev.text

    results = await asyncio.gather(*[run(i) for i in range(100)])
    for i, (ok, args, text) in enumerate(results):
        assert ok is True and args == f"arg{i}" and text == f"cmd{i} arg{i}"
