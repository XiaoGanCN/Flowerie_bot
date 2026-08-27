import asyncio
import json
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
        self.path = os.path.join(self.tmp.name, "memory.db")
        self.mm = MemoryManager(self.path)

    def tearDown(self):
        self.mm.close()
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
        try:
            notes = reloaded.get_user_memory(1, 10).get("notes", [])
            self.assertEqual([n.get("text") if isinstance(n, dict) else n for n in notes], ["喜欢打三角洲"])
        finally:
            reloaded.close()

    def test_string_timestamp_does_not_crash_prune(self):
        """回归：旧数据里 created_at 是字符串时，TTL 清理不得抛异常/清空整个记忆库。"""
        legacy_json = os.path.join(self.tmp.name, "legacy_memory.json")
        with open(legacy_json, "w", encoding="utf-8") as f:
            json.dump({
                "1_10": {"notes": [
                    {"text": "喜欢打三角洲", "created_at": "2024-01-01", "confidence": "model"},
                    {"text": "怕黑", "created_at": 1700000000, "confidence": "model"},
                ]}
            }, f)
        # 构造旧 JSON 路径 → 触发自动迁移到 .db；字符串时间戳→NULL→保留；数值超期→清理
        mm2 = MemoryManager(legacy_json, ttl_days=0, model_memory_ttl_days=30)
        try:
            notes = mm2.get_user_memory(1, 10).get("notes", [])
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].get("text"), "喜欢打三角洲")
        finally:
            mm2.close()

    def test_migrate_from_json(self):
        """旧 memory.json → SQLite 自动迁移：数据完整、原文件备份为 .migrated。"""
        legacy_json = os.path.join(self.tmp.name, "old_memory.json")
        with open(legacy_json, "w", encoding="utf-8") as f:
            json.dump({
                "1_10": {"notes": [
                    "喜欢打三角洲",  # 旧版字符串格式
                    {"text": "怕黑", "created_at": 1700000000.0, "confidence": "self_claim"},
                ]},
                "脏key无下划线": {"notes": [{"text": "不应导入"}]},
                "2_20": {"notes": []},
            }, f, ensure_ascii=False)
        mm = MemoryManager(legacy_json)
        try:
            notes = mm.get_user_memory(1, 10).get("notes", [])
            self.assertEqual(len(notes), 2)
            self.assertEqual(notes[0]["text"], "喜欢打三角洲")
            self.assertEqual(notes[1]["confidence"], "self_claim")
            self.assertEqual(mm.get_user_notes(2, 20), [])
            # 原 JSON 已改名备份，不会重复迁移
            self.assertTrue(os.path.exists(legacy_json + ".migrated"))
            self.assertFalse(os.path.exists(legacy_json))
            # SQLite 文件就位
            self.assertTrue(os.path.exists(legacy_json[:-5] + ".db"))
        finally:
            mm.close()

    def test_json_path_maps_to_db(self):
        """配置里写 .json 路径也自动落库到同目录 .db。"""
        cfg_json = os.path.join(self.tmp.name, "cfg_memory.json")
        mm = MemoryManager(cfg_json)
        try:
            self.assertEqual(mm.db_path, cfg_json[:-5] + ".db")
            run(mm.append_memory_text(1, 10, "测试落库"))
            self.assertTrue(os.path.exists(mm.db_path))
            self.assertFalse(os.path.exists(cfg_json))
        finally:
            mm.close()

    def test_iter_user_groups(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(2, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 20, "喜欢打三角洲"))
        groups = set(self.mm.iter_user_groups())
        self.assertEqual(groups, {(1, 10), (2, 10), (1, 20)})

    def test_remove_notes_containing(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 10, "喜欢喝奶茶"))
        removed = run(self.mm.remove_notes_containing(1, 10, "奶茶"))
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.notes()), 1)
        self.assertEqual(self.notes()[0]["text"], "喜欢打三角洲")

    def test_clear_user_memory(self):
        run(self.mm.append_memory_text(1, 10, "喜欢打三角洲"))
        run(self.mm.append_memory_text(1, 10, "怕黑"))
        cleared = run(self.mm.clear_user_memory(1, 10))
        self.assertEqual(cleared, 2)
        self.assertEqual(self.notes(), [])


if __name__ == "__main__":
    unittest.main()
