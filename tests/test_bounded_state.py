"""无界资源治理测试（场景 13）：陈旧用户状态清理。"""
import tempfile
import time

from src.core.policy_engine import PolicyEngine
from src.services.memory_manager import MemoryManager
from tests.test_router_regression import make_config


def _make_policy():
    tmp = tempfile.TemporaryDirectory()
    mm = MemoryManager(f"{tmp.name}/m.db")
    cfg = make_config()
    policy = PolicyEngine(cfg, mm)
    return policy, mm, tmp


def test_prune_stale_state_removes_old_entries():
    policy, mm, tmp = _make_policy()
    try:
        now = time.time()
        gs = policy.global_state
        # 旧条目（3 天前）与新条目（刚刚）
        gs.user_ai_last_call = {1: now - 3 * 86400, 2: now - 10}
        gs.poke_last_time = {3: now - 5 * 86400}
        gs.last_toxic_warning = {4: now - 2 * 86400}
        state = policy.get_group_state(100)
        state.user_last_time = {"5": now - 4 * 86400, "6": now - 5}

        removed = policy.prune_stale_state(max_age_seconds=86400)
        assert removed == 4  # 1、3、4、5 过期；2、6 保留
        assert 2 in gs.user_ai_last_call
        assert "6" in state.user_last_time
        assert 1 not in gs.user_ai_last_call
        assert 3 not in gs.poke_last_time
        assert 4 not in gs.last_toxic_warning
        assert "5" not in state.user_last_time
    finally:
        mm.close()
        tmp.cleanup()


def test_prune_stale_state_idempotent():
    policy, mm, tmp = _make_policy()
    try:
        policy.global_state.poke_last_time = {1: time.time() - 999999}
        policy.prune_stale_state()
        assert policy.prune_stale_state() == 0  # 第二次无清理
    finally:
        mm.close()
        tmp.cleanup()
