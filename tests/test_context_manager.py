import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from src.core.context_manager import ContextManager
from src.models import GlobalState, GroupState


def run(coro):
    return asyncio.run(coro)


def make_config(path):
    return SimpleNamespace(CONTEXT_BACKUP_PATH=path, CONTEXT_SIZE=300)


class TestGroupStateContextSize(unittest.TestCase):
    def test_context_size_wired(self):
        """CONTEXT_SIZE 配置应作用于 GroupState 的上下文容量。"""
        config = SimpleNamespace(CONTEXT_BACKUP_PATH=None, CONTEXT_SIZE=10)
        cm = ContextManager(config, {}, GlobalState())
        state = cm.get_group_state(1)
        for i in range(20):
            state.context.append({"user_id": 1, "message": f"m{i}", "is_bot": False, "time": i})
        self.assertEqual(len(state.context), 10)
        self.assertEqual(state.context[-1]["message"], "m19")

    def test_group_state_default(self):
        self.assertEqual(GroupState().context.maxlen, 300)
        self.assertEqual(GroupState(context_size=50).context.maxlen, 50)


class TestContextManagerBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "context_backup.json")
        self.config = make_config(self.path)
        self.groups = {}
        self.global_state = GlobalState()
        self.cm = ContextManager(self.config, self.groups, self.global_state)

    def tearDown(self):
        self.tmp.cleanup()

    def add_msgs(self, group_id, count):
        state = self.cm.get_group_state(group_id)
        for i in range(count):
            state.context.append({"user_id": 1, "message": f"msg{i}", "is_bot": False, "time": i})

    def add_processed(self, group_id, count):
        state = self.cm.get_group_state(group_id)
        for i in range(count):
            state.processed_msg_ids.append(1000 + i)

    def test_save_and_reload(self):
        self.add_msgs(1, 10)
        self.add_processed(1, 5)
        run(self.cm.save_context_backup())

        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        state = cm2.get_group_state(1)
        self.assertEqual(len(state.context), 10)
        self.assertEqual(len(state.processed_msg_ids), 5)

    def test_only_last_50_saved(self):
        self.add_msgs(1, 120)
        run(self.cm.save_context_backup())
        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        state = cm2.get_group_state(1)
        self.assertEqual(len(state.context), 50)

    def test_only_last_200_ids_saved(self):
        self.add_processed(1, 300)
        run(self.cm.save_context_backup())
        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        state = cm2.get_group_state(1)
        self.assertEqual(len(state.processed_msg_ids), 200)

    def test_legacy_list_format_compatible(self):
        """旧版纯数组格式的 context_backup.json 应能自动迁移到 SQLite。"""
        legacy = {str(1): [{"user_id": 1, "message": f"msg{i}", "is_bot": False, "time": i} for i in range(3)]}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        self.assertEqual(len(cm2.get_group_state(1).context), 3)
        # 迁移后：原 JSON 备份为 .migrated，SQLite 库就位
        self.assertTrue(os.path.exists(self.path + ".migrated"))
        self.assertTrue(os.path.exists(self.path[:-5] + ".db"))

    def test_migrate_new_format_json(self):
        """新版 {"messages": [...], "processed_ids": [...]} 格式的 JSON 同样自动迁移。"""
        legacy = {str(1): {
            "messages": [{"user_id": 1, "message": f"m{i}", "is_bot": False, "time": i} for i in range(5)],
            "processed_ids": [1001, 1002, 1003],
        }}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False)
        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        state = cm2.get_group_state(1)
        self.assertEqual(len(state.context), 5)
        self.assertEqual(set(state.processed_msg_ids), {1001, 1002, 1003})

    def test_save_writes_sqlite(self):
        self.add_msgs(1, 3)
        run(self.cm.save_context_backup())
        # 备份落在 SQLite 库（json 路径自动映射 .db），且不产生 json 文件
        self.assertTrue(os.path.exists(self.path[:-5] + ".db"))
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
