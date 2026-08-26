import unittest

from src.core.memory_parser import MemoryParser


class TestMemoryParser(unittest.TestCase):
    def setUp(self):
        self.parser = MemoryParser()

    def test_parse_empty(self):
        self.assertEqual(self.parser.parse_memory_update("", 123), (123, ""))

    def test_parse_memory_prefix(self):
        uid, text = self.parser.parse_memory_update("记忆: 喜欢玩三角洲", 456)
        self.assertEqual(uid, 456)
        self.assertEqual(text, "喜欢玩三角洲")

    def test_parse_memory_prefix_colon_cn(self):
        uid, text = self.parser.parse_memory_update("记忆：怕黑", 456)
        self.assertEqual(uid, 456)
        self.assertEqual(text, "怕黑")

    def test_parse_target_user(self):
        uid, text = self.parser.parse_memory_update("【记忆】888: 怕黑", 456)
        self.assertEqual(uid, 888)
        self.assertEqual(text, "怕黑")

    def test_parse_plain(self):
        uid, text = self.parser.parse_memory_update("喜欢穿白丝", 456)
        self.assertEqual(uid, 456)
        self.assertEqual(text, "喜欢穿白丝")

    def test_force_memory_like(self):
        self.assertTrue(self.parser.should_force_memory("我喜欢玩三角洲", "我喜欢玩三角洲", False))

    def test_force_memory_english(self):
        self.assertTrue(self.parser.should_force_memory("I like this game", "I like this game", False))

    def test_force_memory_not_personal(self):
        self.assertFalse(self.parser.should_force_memory("今天天气不错", "今天天气不错", False))

    def test_force_memory_at_others_blocks(self):
        self.assertFalse(self.parser.should_force_memory("我喜欢玩三角洲", "我喜欢玩三角洲", True))

    def test_force_memory_command_blocks(self):
        self.assertFalse(self.parser.should_force_memory("我喜欢", "/start", False))


if __name__ == "__main__":
    unittest.main()
