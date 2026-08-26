"""回归测试：覆盖本轮审查修复的关键逻辑与致命 bug。

- guarded_chat 重复关键字参数（TypeError）→ 消息回复/主动聊天全流程
- chat_once 的 retry_count NameError（见 test_ai_client.py）
- 字符串 message_array 兼容 / 去重先于指令（重投不重复执行指令）
- 静默强制记忆：确定记录、不烧 AI 调用
- 戳戳每用户冷却
"""
import asyncio
import unittest
from types import SimpleNamespace

from src.models import GlobalState
from src.core.policy_engine import PolicyEngine
from src.core.budget_manager import BudgetManager
from src.core.message_router import MessageRouter


def run(coro):
    return asyncio.run(coro)


def make_config(**overrides):
    base = dict(
        ALLOWED_GROUP_IDS=None,
        TOXIC_GROUP_IDS=[],
        TOXIC_WARNING_COOLDOWN=900,
        MEMORY_DISABLED_GROUPS=None,
        ADMIN_QQ_IDS=None,
        BOT_QQ=10001,
        BOT_NICKNAME="花璃",
        ONLY_REPLY_WHEN_AT=False,
        USER_COOLDOWN=5,
        BOT_COOLDOWN=2,
        MAX_CONSECUTIVE_REPLIES=3,
        BOT_CONSECUTIVE_REPLY_COOLDOWN=60,
        MAX_REPLY_LENGTH=40,
        CONTEXT_SIZE=300,
        REPEAT_WINDOW=120,
        REPEAT_THRESHOLD=3,
        MAX_AI_INPUT_CHARS=8000,
        MAX_CONCURRENT_AI=3,
        EVENT_PROCESS_TIMEOUT=90,
        CONTEXT_BACKUP_PATH=None,
        CONTEXT_BACKUP_INTERVAL=60,
        DAILY_AI_CALL_BUDGET=1000,
        GROUP_DAILY_AI_CALL_BUDGET=300,
        USER_AI_CALL_MIN_INTERVAL=10,
        BUDGET_EXHAUSTED_NOTICE=True,
        ACTIVE_CHAT_COOLDOWN=180,
        NIGHT_SILENCE_START=0,
        NIGHT_SILENCE_END=8,
        POKE_REPLY_ENABLED=True,
        POKE_REPLIES=["戳人家干嘛...", "别戳了...！"],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeAIClient:
    """假 AI：记录调用次数，返回固定回复。"""

    def __init__(self, reply="回复内容"):
        self.reply = reply
        self.calls = 0
        self._api_backoff = 0.0
        self.last_kwargs = None

    async def chat_once(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return self.reply, None

    async def is_toxic(self, text):
        return False


class FakeSender:
    def __init__(self):
        self.sent = []

    async def send_group_message(self, group_id, message, retries=2):
        self.sent.append((group_id, message))
        return True

    async def send_private_message(self, user_id, message):
        return True


class FakeFileParser:
    def extract_mention_and_text(self, message_array, bot_qq):
        return "", False


class FakeAssembler:
    async def assemble(self, message_array, user_id, group_id, raw_time):
        # 从段数组拼 full_text
        parts = []
        for seg in message_array or []:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append((seg.get("data") or {}).get("text", ""))
        return "".join(parts), [], False, False, False


class FakeCommands:
    def __init__(self):
        self.handled = []

    async def handle(self, text, user_id, group_id):
        self.handled.append((text, user_id, group_id))
        return False


class FakeMemoryManager:
    def __init__(self):
        self.writes = []

    async def append_memory_text(self, user_id, group_id, text, **kwargs):
        self.writes.append((user_id, group_id, text, kwargs))


def build_router(config=None, ai=None, sender=None):
    config = config or make_config()
    ai = ai or FakeAIClient()
    sender = sender or FakeSender()
    mm = FakeMemoryManager()
    fp = FakeFileParser()
    policy = PolicyEngine(config, mm)
    router = MessageRouter(config, ai, mm, fp, sender, policy)
    # 替换内部组件为测试替身（policy/budget 保持真实实现）
    router.assembler = FakeAssembler()
    router.commands = FakeCommands()
    router.budget = BudgetManager(config, policy.global_state, sender)
    return router, config, ai, sender, mm


class TestMessageReplyFlow(unittest.TestCase):
    def test_reply_flow_no_typeerror(self):
        """回归：guarded_chat 重复关键字参数曾导致每条消息 TypeError。"""
        router, config, ai, sender, mm = build_router()
        # 未@消息接话是概率行为：这里固定为不接话，验证流程本身无异常
        router.policy_engine.should_reply_by_context = lambda group_id: False
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 789,
            "time": 1700000000,
            "message": [{"type": "text", "data": {"text": "你好呀"}}],
        }
        run(router.process_event(event))
        self.assertEqual(ai.calls, 0)  # 不接话 → 不调用 AI，且全程无异常

    def test_mention_reply_flow(self):
        """@机器人消息必须走通全流程并发出回复。"""
        router, config, ai, sender, mm = build_router()

        class FP(FakeFileParser):
            def extract_mention_and_text(self, message_array, bot_qq):
                return "在吗", True

        router.file_parser = FP()
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 790,
            "time": 1700000000,
            "message": [
                {"type": "at", "data": {"qq": "10001"}},
                {"type": "text", "data": {"text": "在吗"}},
            ],
        }
        run(router.process_event(event))
        self.assertEqual(ai.calls, 1)
        self.assertEqual(sender.sent[0][1], "回复内容")

    def test_string_message_array_compat(self):
        """OneBot11 字符串形式 message 必须正常处理（不抛 AttributeError）。"""
        router, config, ai, sender, mm = build_router()

        class FP(FakeFileParser):
            def extract_mention_and_text(self, message_array, bot_qq):
                return "在吗", True

        router.file_parser = FP()
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 791,
            "time": 1700000000,
            "message": "在吗",  # 字符串形式
        }
        run(router.process_event(event))
        self.assertEqual(ai.calls, 1)

    def test_dedup_before_command(self):
        """重投的旧消息：指令不得重复执行。"""
        router, config, ai, sender, mm = build_router()
        router.commands = FakeCommands()

        class FP(FakeFileParser):
            def extract_mention_and_text(self, message_array, bot_qq):
                return "/forget_me", False

        router.file_parser = FP()
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 792,
            "time": 1700000000,
            "message": [{"type": "text", "data": {"text": "/forget_me"}}],
        }
        run(router.process_event(event))
        self.assertEqual(len(router.commands.handled), 1)
        # 同一 message_id 重投 → 去重拦截，指令不二次执行
        run(router.process_event(event))
        self.assertEqual(len(router.commands.handled), 1)

    def test_silent_force_memory(self):
        """静默记忆：命中偏好句式（未@）→ 确定记录，不调用 AI。"""
        router, config, ai, sender, mm = build_router()

        class FP(FakeFileParser):
            def extract_mention_and_text(self, message_array, bot_qq):
                return "我喜欢喝奶茶", False

        router.file_parser = FP()
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 123,
            "user_id": 456,
            "message_id": 793,
            "time": 1700000000,
            "message": [{"type": "text", "data": {"text": "我喜欢喝奶茶"}}],
        }
        run(router.process_event(event))
        self.assertEqual(len(mm.writes), 1)
        self.assertEqual(mm.writes[0][2], "我喜欢喝奶茶")
        self.assertEqual(ai.calls, 0)  # 不烧 AI 调用
        self.assertEqual(sender.sent, [])  # 不回复

    def test_active_chat_no_typeerror(self):
        """回归：主动聊天 guarded_chat 重复关键字参数曾导致 TypeError。"""
        config = make_config(ACTIVE_CHAT_COOLDOWN=0)
        router, config, ai, sender, mm = build_router(config)
        # 先造点上下文，让主动聊天有内容
        router.policy_engine.add_context(123, 456, "大家好", is_bot=False)
        router.global_state.ws_connected = True
        router.global_state.last_active_chat_time = 0
        run(router._do_active_chat(123))
        self.assertGreaterEqual(ai.calls, 1)

    def test_poke_cooldown(self):
        """同一用户 10 秒内连戳只回一次。"""
        router, config, ai, sender, mm = build_router()
        event = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "group_id": 123,
            "user_id": 456,
            "target_id": 10001,
        }
        run(router.process_event(event))
        run(router.process_event(event))
        self.assertEqual(len(sender.sent), 1)


if __name__ == "__main__":
    unittest.main()
