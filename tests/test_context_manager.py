import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from src.models import GlobalState
from src.core.context_manager import ContextManager


def run(coro):
    return asyncio.run(coro)


def make_config(path):
    return SimpleNamespace(CONTEXT_BACKUP_PATH=path)


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
        self.add_msgs(1, 3)
        run(self.cm.save_context_backup())
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        legacy = {gid: v["messages"] for gid, v in data.items()}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(legacy, f, ensure_ascii=False, indent=2)
        cm2 = ContextManager(self.config, {}, GlobalState())
        cm2.load_context_backup()
        self.assertEqual(len(cm2.get_group_state(1).context), 3)


if __name__ == "__main__":
    unittest.main()
