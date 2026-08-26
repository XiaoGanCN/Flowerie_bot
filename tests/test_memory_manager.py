import asyncio
import os
import tempfile
import unittest

from src.services.memory_manager import MemoryManager


def run(coro):
    """在独立的 asyncio 事件循环里跑协程（兼容普通 Python 与无完整 asyncio 的环境）。"""
    return asyncio.run(coro)


class TestMemoryManagerDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "memory.json")
        self.mm = MemoryManager(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def notes(self, uid=1, gid=10):
        return self.mm.get_user_memory(uid, gid).get("notes", [])

    def test_exact_duplicate_skipped(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲 已退游"))
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲 已退游"))
        self.assertEqual(len(self.notes()), 1)

    def test_substring_duplicate_skipped(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲 已退游"))
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲 已退游 好家伙 退游了还提这个 是怀念了吗"))
        self.assertEqual(len(self.notes()), 1)

    def test_typo_variant_skipped(self):
        # 错别字版本（三角州 vs 三角洲）也应被识别为重复
        run(self.mm.append_memory_text(1, 10, "喜欢打三角州 已退游"))
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲 已退游 好家伙 退游了还提这个 是怀念了吗"))
        self.assertEqual(len(self.notes()), 1)

    def test_similar_prefix_skipped(self):
        run(self.mm.append_memory_text(1, 10, "喜欢穿白丝"))
        run(self.mm.append_memory_text(1, 10, "喜欢穿白丝袜"))
        self.assertEqual(len(self.notes()), 1)

    def test_different_notes_kept(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 10, "讨厌蚯蚓"))
        run(self.mm.append_memory_text(1, 10, "怕黑"))
        self.assertEqual(len(self.notes()), 3)

    def test_different_colors_kept(self):
        # 喜欢红色 vs 喜欢蓝色：不应被误判为重复
        run(self.mm.append_memory_text(1, 10, "喜欢红色"))
        run(self.mm.append_memory_text(1, 10, "喜欢蓝色"))
        self.assertEqual(len(self.notes()), 2)

    def test_per_user_group_isolation(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(2, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 20, "喜欢打三角洲"))
        self.assertEqual(len(self.notes(1, 10)), 1)
        self.assertEqual(len(self.notes(2, 10)), 1)
        self.assertEqual(len(self.notes(1, 20)), 1)

    def test_persist_and_reload(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        reloaded = MemoryManager(self.path)
        notes = reloaded.get_user_memory(1, 10).get("notes", [])
        self.assertEqual([n.get("text") if isinstance(n, dict) else n for n in notes], ["喜欢打三角洲"])

    def test_string_timestamp_does_not_crash_prune(self):
        """回归：旧数据里 created_at 是字符串时，TTL 清理不得抛异常/清空整个记忆库。"""
        import json
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({
                "1_10": {"notes": [
                    {"text": "喜欢打三角洲", "created_at": "2024-01-01", "confidence": "model"},
                    {"text": "怕黑", "created_at": 1700000000, "confidence": "model"},
                ]}
            }, f)
        mm2 = MemoryManager(self.path, ttl_days=0, model_memory_ttl_days=30)
        notes = mm2.get_user_memory(1, 10).get("notes", [])
        # 字符串时间戳那条无法判断年龄 → 保留；数值时间戳超期 → 清理
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].get("text"), "喜欢打三角洲")


if __name__ == "__main__":
    unittest.main()
