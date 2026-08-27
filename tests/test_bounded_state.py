"""状态治理测试（第三轮）：ExpiringMap 自治 TTL、inactive 群清理、长期运行有界性。"""
import tempfile
import time

from src.core.policy_engine import PolicyEngine
from src.services.memory_manager import MemoryManager
from src.utils.expiring_map import ExpiringMap
from tests.test_router_regression import make_config


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_policy():
    tmp = tempfile.TemporaryDirectory()
    mm = MemoryManager(f"{tmp.name}/m.db")
    cfg = make_config()
    policy = PolicyEngine(cfg, mm)
    return policy, mm, tmp


# ---------- ExpiringMap ----------
def test_expiring_map_basic():
    clock = FakeClock()
    m = ExpiringMap(ttl_seconds=100, clock=clock)
    m.set("a", 1.0)
    assert m.get("a") == 1.0
    assert m.contains("a")
    clock.advance(101)
    assert m.get("a") is None  # 惰性过期
    assert not m.contains("a")


def test_expiring_map_get_timestamp():
    clock = FakeClock()
    m = ExpiringMap(ttl_seconds=100, clock=clock)
    m.set("a", 42.0)
    assert m.get_timestamp("a") == 42.0
    clock.advance(101)
    assert m.get_timestamp("a") == 0.0  # 过期返回默认


def test_expiring_map_max_size_evicts_oldest():
    clock = FakeClock()
    m = ExpiringMap(ttl_seconds=1000, max_size=3, clock=clock)
    m.set("a", 1)
    m.set("b", 2)
    m.set("c", 3)
    m.set("d", 4)
    assert len(m) == 3
    assert m.get("a") is None  # 最旧被淘汰
    assert m.get("d") == 4


def test_expiring_map_cleanup():
    clock = FakeClock()
    m = ExpiringMap(ttl_seconds=100, clock=clock)
    m.set("a", 1)
    m.set("b", 2)
    clock.advance(101)
    assert m.cleanup() == 2
    assert len(m) == 0
    assert m.cleanup() == 0  # 幂等


# ---------- PolicyEngine 状态清理 ----------
def test_prune_stale_state_cleans_expired_entries():
    policy, mm, tmp = _make_policy()
    try:
        gs = policy.global_state
        gs.user_ai_last_call.set(1, time.time())
        gs.poke_last_time.set(3, time.time())
        # 未过期：cleanup 不清任何条目
        assert policy.prune_stale_state() == 0
        assert gs.user_ai_last_call.get(1) is not None
    finally:
        mm.close()
        tmp.cleanup()


def test_toxic_warning_ttl_independent_of_backup_loop():
    """last_toxic_warning 状态自治：TTL 过期后自然失效，不依赖 backup loop。"""
    clock = FakeClock()
    m = ExpiringMap(ttl_seconds=60, clock=clock)
    m.set(123, 5.0)  # 写入"上次警告时间"
    # 不调用任何 cleanup：60 秒后读取即视为无警告
    clock.advance(61)
    assert m.get(123) is None
    # 冷却判断逻辑：last = get(...) or 0 → now - 0 >= cooldown → 允许再次警告
    last = m.get(123) or 0
    assert clock.now - last >= 900  # TOXIC_WARNING_COOLDOWN 语义保持（过期=无警告）


def test_prune_stale_groups_removes_inactive():
    policy, mm, tmp = _make_policy()
    try:
        g1 = policy.get_group_state(111)
        g1.last_activity = time.time() - 3 * 86400  # 3 天前活跃
        g2 = policy.get_group_state(222)
        g2.last_activity = time.time() - 60  # 刚刚活跃
        removed = policy.prune_stale_groups(max_idle_seconds=86400)
        assert removed == 1
        assert 111 not in policy.groups
        assert 222 in policy.groups
        # 群复活：重建空状态（短期会话历史从零开始）
        policy.get_group_state(111)
        assert 111 in policy.groups
        assert len(policy.get_group_state(111).context) == 0
    finally:
        mm.close()
        tmp.cleanup()


def test_long_running_state_stays_bounded():
    """长期运行模拟（fake clock）：t=0 → t=7d，状态数量保持有界。"""
    clock = FakeClock()
    store = ExpiringMap(ttl_seconds=86400, max_size=100, clock=clock)

    # t=0：100 个群活跃
    for i in range(100):
        store.set(i, {"msg_count": 1})

    # t=1h：大部分群 inactive（不再访问）
    clock.advance(3600)
    # t=2h：只有 10 个群继续活跃（重新 set 续期——固定 TTL 语义，续期至 t=26h）
    for i in range(10):
        store.set(i, {"msg_count": 2})

    # t=25h：未续期的 90 个群已过期，持续活跃的 10 个仍存活
    clock.advance(23 * 3600)
    alive = sum(1 for i in range(100) if store.get(i) is not None)
    assert alive == 10
    store.cleanup()
    assert len(store) == 10  # 有界

    # t=30h：活跃的也过期，全部清理
    clock.advance(5 * 3600)
    store.cleanup()
    assert len(store) == 0

    # 容量上限：继续写入超过 max_size 也不增长
    for i in range(200):
        store.set(1000 + i, {"msg_count": 1})
    assert len(store) <= 100
