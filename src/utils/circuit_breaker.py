"""Circuit Breaker（熔断器）：CLOSED → OPEN → HALF_OPEN → CLOSED 状态机。

设计要点：
- 使用 time.monotonic()（不受系统时钟调整影响）
- HALF_OPEN 只允许有限并发 probe（默认 1），probe 成功 → CLOSED，失败 → OPEN
- 所有方法同步、无锁（asyncio 单线程内原子；多线程场景由调用方保证）
- 纯内存对象，无资源需要 shutdown 释放
"""
import time
from typing import Callable, Optional

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """熔断器状态机。

    :param failure_threshold: 连续失败多少次后 OPEN（CLOSED 期间计数）
    :param cooldown_seconds: OPEN 后冷却多久进入 HALF_OPEN
    :param half_open_probes: HALF_OPEN 期间允许的并发探测请求数
    :param clock: 可注入时钟（测试用），默认 time.monotonic
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 10,
        cooldown_seconds: float = 60.0,
        half_open_probes: int = 1,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.name = name
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.half_open_probes = max(1, int(half_open_probes))
        self._clock = clock or time.monotonic
        self._state = CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0

    # ---------- 状态 ----------
    @property
    def state(self) -> str:
        """当前状态（含 OPEN 冷却到期后的自动迁移为 HALF_OPEN）。"""
        if self._state == OPEN and self._clock() - self._opened_at >= self.cooldown_seconds:
            self._state = HALF_OPEN
            self._half_open_inflight = 0
        return self._state

    def allow(self) -> bool:
        """是否允许放行一个请求。

        - CLOSED：放行
        - OPEN：拒绝（冷却未到期）
        - HALF_OPEN：只放行有限并发 probe（默认 1），其余拒绝
        """
        st = self.state
        if st == CLOSED:
            return True
        if st == OPEN:
            return False
        # HALF_OPEN：probe 独占
        if self._half_open_inflight < self.half_open_probes:
            self._half_open_inflight += 1
            return True
        return False

    def record_success(self) -> None:
        """一次成功：CLOSED/HALF_OPEN 下清零失败计数并回到 CLOSED。"""
        self._failures = 0
        self._half_open_inflight = max(0, self._half_open_inflight - 1)
        if self._state == HALF_OPEN:
            self._state = CLOSED

    def record_failure(self) -> None:
        """一次失败：CLOSED 下累计，达阈值 → OPEN；HALF_OPEN 下 probe 失败 → OPEN。"""
        self._half_open_inflight = max(0, self._half_open_inflight - 1)
        if self._state == OPEN:
            return
        if self._state == HALF_OPEN:
            self._open()
            return
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open()

    def _open(self) -> None:
        self._state = OPEN
        self._failures = 0
        self._opened_at = self._clock()

    def reset(self) -> None:
        """手动复位（如配置变更）。"""
        self._state = CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_inflight = 0

    # ---------- 自省 ----------
    def snapshot(self) -> dict:
        st = self.state
        return {
            "name": self.name,
            "state": st,
            "failures": self._failures if st != OPEN else 0,
            "half_open_inflight": self._half_open_inflight,
        }
