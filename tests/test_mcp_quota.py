"""MCP 工具调用额度测试（P2-1 修复）：MAX_TOOL_CALLS 按**实际调用次数**硬上限。

覆盖 repair.txt 要求的场景：
1. 单轮单 tool
2. 单轮多个 tool_calls
3. 多轮 tool_calls
4. 单轮超过剩余额度
5. 多轮累计达到上限
6. retry 不重置 tool-call quota
7. 并发 tool call 不突破上限
8. MAX_TOOL_CALLS=0 或非法值的配置行为
"""
import asyncio

from src.services.ai_client import AIClient
from tests.test_ai_client import make_config as ai_config


class _Resp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.text = ""

    def json(self):
        return self._data


def _tc_msg(names, content=None):
    """构造带 tool_calls 的模型返回消息。"""
    return {
        "content": content,
        "tool_calls": [
            {"id": f"call_{i}", "type": "function",
             "function": {"name": n, "arguments": "{}"}}
            for i, n in enumerate(names)
        ],
    }


class FakeModel:
    """可编程假 DeepSeek，模拟真实 API 语义：

    - 请求带 tools：按 tool_batches 依次返回 tool_calls（超出后返回 content）
    - 请求不带 tools（收尾请求）：直接返回 content
    """

    def __init__(self, tool_batches, final_content="答"):
        self.tool_batches = tool_batches  # list[list[str]]：每批工具名
        self.final_content = final_content
        self.round = 0
        self.messages_sent = []

    async def post(self, url, headers=None, json=None, timeout=None):
        self.round += 1
        self.messages_sent.append(json)
        if "tools" not in (json or {}):
            return _Resp({"choices": [{"message": {"content": self.final_content}}]})
        idx = self.round - 1
        if idx < len(self.tool_batches):
            return _Resp({"choices": [{"message": _tc_msg(self.tool_batches[idx])}]})
        return _Resp({"choices": [{"message": {"content": self.final_content}}]})


def _make_ai(model):
    ai = AIClient(ai_config(), None)
    ai.client = model
    return ai


def _caller(calls):
    async def caller(name, args):
        calls.append(name)
        return f"结果:{name}"
    return caller


TOOLS = [{"type": "function", "function": {"name": "web_search"}}]


# ---------- 1. 单轮单 tool ----------
async def test_quota_single_round_single_tool():
    calls = []
    ai = _make_ai(FakeModel([["a"]], final_content="答1"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=5,
    )
    assert reply == "答1"
    assert calls == ["a"]


# ---------- 2. 单轮多个 tool_calls ----------
async def test_quota_single_round_multiple_tools():
    calls = []
    ai = _make_ai(FakeModel([["a", "b", "c"]], final_content="答2"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=5,
    )
    assert calls == ["a", "b", "c"]
    assert reply == "答2"


# ---------- 3. 多轮 tool_calls ----------
async def test_quota_multi_round():
    calls = []
    ai = _make_ai(FakeModel([["a"], ["b"]], final_content="答3"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=5,
    )
    assert calls == ["a", "b"]
    assert reply == "答3"


# ---------- 4. 单轮超过剩余额度：只执行到剩余额度，其余跳过 ----------
async def test_quota_single_round_exceeds_remaining():
    calls = []
    ai = _make_ai(FakeModel([["a", "b", "c", "d"]], final_content="答4"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=2,
    )
    assert calls == ["a", "b"]  # c/d 必须拒绝，不突破上限
    # 跳过的调用以占位 tool 消息回填（保持对话格式合法），且发了不带 tools 的收尾请求
    last = ai.client.messages_sent[-1]
    assert "tools" not in last  # 收尾请求不带 tools
    tool_msgs = [m for m in last["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 4  # 2 真实 + 2 跳过占位
    assert all("跳过" in m["content"] for m in tool_msgs[2:])
    assert reply == "答4"


# ---------- 5. 多轮累计达到上限 ----------
async def test_quota_cumulative_multi_round():
    calls = []
    ai = _make_ai(FakeModel([["a", "b"], ["c", "d"]], final_content="答5"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=3,
    )
    assert calls == ["a", "b", "c"]  # d 被跳过
    assert reply == "答5"


# ---------- 6. retry 不重置 tool-call quota ----------
async def test_quota_retry_does_not_reset():
    """同一 logical request 的 retry 共享 quota：重试不会重新获得新额度。"""
    calls = []
    quota = {"max": 3, "used": 0}
    ai = _make_ai(FakeModel([["a", "b"], ["c", "d"]], final_content="首答"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=3, tool_quota=quota,
    )
    assert calls == ["a", "b", "c"]  # 首次尝试已用满 3 次，d 跳过
    assert quota["used"] == 3

    # 模拟 retry：同一 quota 再次调用 → 不再执行任何工具，仅收尾直接回答
    ai2 = _make_ai(FakeModel([], final_content="重试答"))
    reply2, _ = await ai2.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=3, tool_quota=quota,
    )
    assert calls == ["a", "b", "c"]  # 未新增执行
    assert quota["used"] == 3
    assert reply2 == "重试答"
    assert "tools" not in ai2.client.messages_sent[0]  # 收尾请求不带 tools


# ---------- 7. 并发 tool call 不突破上限（共享 quota 硬上限） ----------
async def test_quota_concurrent_requests_do_not_exceed():
    calls = []
    quota = {"max": 3, "used": 0}

    async def run_once():
        ai = _make_ai(FakeModel([["x", "y", "z", "w"]], final_content="并发答"))
        return await ai.chat_once(
            "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
            tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=3, tool_quota=quota,
        )

    results = await asyncio.gather(run_once(), run_once())
    assert len(calls) <= 3  # 两路并发合计不突破共享上限
    assert quota["used"] <= 3
    assert all(r[0] for r in results)  # 两路都拿到了收尾回答


# ---------- 8. MAX_TOOL_CALLS=0 / 负值：禁用工具，走纯聊天 ----------
async def test_quota_zero_disables_tools():
    calls = []
    ai = _make_ai(FakeModel([], final_content="纯聊天答"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=0,
    )
    assert calls == []
    assert reply == "纯聊天答"
    # 请求里不应带 tools
    assert "tools" not in ai.client.messages_sent[0]


async def test_quota_negative_treated_as_disabled():
    calls = []
    ai = _make_ai(FakeModel([], final_content="纯聊天答"))
    reply, _ = await ai.chat_once(
        "q", "（暂无历史聊天记录）", user_id=1, group_id=2,
        tools=TOOLS, tool_caller=_caller(calls), max_tool_calls=-1,
    )
    assert calls == []
    assert reply == "纯聊天答"
