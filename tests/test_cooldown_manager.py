import time
import unittest
from types import SimpleNamespace

from src.models import GlobalState
from src.core.cooldown_manager import CooldownManager


def make_config(**overrides):
    base = dict(
        USER_COOLDOWN=5,
        BOT_COOLDOWN=2,
        MAX_CONSECUTIVE_REPLIES=3,
        BOT_CONSECUTIVE_REPLY_COOLDOWN=60,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestCooldownManager(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.groups = {}
        self.global_state = GlobalState()
        self.cm = CooldownManager(self.config, self.groups, self.global_state)

    def test_user_cooldown(self):
        # 未发言过 → 可以回复
        self.assertTrue(self.cm.can_user_reply(100, 1))
        self.cm.update_user_time(100, 1)
        # 刚发言 → 冷却中
        self.assertFalse(self.cm.can_user_reply(100, 1))
        # 其他用户不受影响
        self.assertTrue(self.cm.can_user_reply(200, 1))

    def test_bot_global_cooldown(self):
        self.assertTrue(self.cm.can_bot_reply(1))
        self.cm.record_bot_reply(1)
        self.assertFalse(self.cm.can_bot_reply(1))

    def test_group_isolation(self):
        self.cm.record_bot_reply(1)
        # 不同群的机器人冷却共享全局 bot_last_reply_global，但也受全局冷却影响
        # 这里验证连续回复惩罚是按群隔离的
        for _ in range(self.config.MAX_CONSECUTIVE_REPLIES):
            self.cm.record_bot_reply(2)
        self.cm.global_state.bot_last_reply_global = 0  # 清掉全局冷却再验证
        self.assertFalse(self.cm.can_bot_reply(2))  # 群 2 被 block
        self.assertTrue(self.cm.can_bot_reply(3))   # 群 3 不受影响


if __name__ == "__main__":
    unittest.main()
