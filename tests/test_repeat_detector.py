"""复读检测内存上限 / 长内容跳过 单元测试。"""
import unittest
from types import SimpleNamespace

from src.core.repeat_detector import RepeatDetector


def make_config(**overrides):
    base = dict(
        REPEAT_WINDOW=120,
        REPEAT_THRESHOLD=3,
        CONTEXT_SIZE=300,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestRepeatDetectorCap(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.groups = {}
        self.rd = RepeatDetector(self.config, self.groups)

    def test_cache_bounded(self):
        """大量不同内容时 msg_timestamps 与 repeat_cache 有上限。"""
        for i in range(400):
            self.rd.check_and_record_repeat(f"内容{i}", 1)
        state = self.rd.get_group_state(1)
        self.assertLessEqual(len(state.msg_timestamps), RepeatDetector._MAX_CACHE_ENTRIES)
        self.assertLessEqual(len(state.repeat_cache), RepeatDetector._MAX_CACHE_ENTRIES)

    def test_long_content_not_tracked(self):
        """超长内容（如文件全文）不参与复读，也不占缓存。"""
        long_text = "长" * (RepeatDetector._MAX_TRACKED_CONTENT + 10)
        for _ in range(5):
            self.assertFalse(self.rd.check_and_record_repeat(long_text, 1))
        state = self.rd.get_group_state(1)
        self.assertEqual(len(state.msg_timestamps), 0)

    def test_repeat_triggers(self):
        """同一短内容 3 次触发复读。"""
        results = [self.rd.check_and_record_repeat("复读", 1) for _ in range(3)]
        self.assertEqual(results, [False, False, True])
        # 冷却窗口内不再触发
        self.assertFalse(self.rd.check_and_record_repeat("复读", 1))

    def test_command_not_tracked(self):
        self.assertFalse(self.rd.check_and_record_repeat("/help", 1))


if __name__ == "__main__":
    unittest.main()
