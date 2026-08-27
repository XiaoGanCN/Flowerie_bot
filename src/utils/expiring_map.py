"""ExpiringMap：轻量 TTL 容器（状态自治的生命周期管理）。

语义：
- get/contains 时惰性检查过期（不依赖外部 cleanup 循环）
- cleanup() 批量清除过期条目（可周期性调用，但业务正确性不依赖它）
- max_size 超限时按插入序淘汰最旧条目（近似 FIFO）
- 使用 time.monotonic()（不受系统时钟调整影响）
- 线程安全（RLock；asyncio 单线程内同样安全）

适用于：user_last_time / user_ai_last_call / poke_last_time /
last_toxic_warning / group breakers 等 "dict + timestamp + cleanup" 模式。
"""
import threading
import time
from typing import Callable, Dict, Iterator, Optional, TypeVar

T = TypeVar("T")


class ExpiringMap:
    """带 TTL 与容量上限的键值容器。"""

    def __init__(
        self,
        ttl_seconds: float,
        max_size: int = 1000,
        clock: Optional[Callable[[], float]] = None,
    ):
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_size = max(1, int(max_size))
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._data: Dict[object, tuple] = {}  # key -> (expire_at, value)

    def set(self, key, value) -> None:
        """写入；ttl<=0 表示永不过期。"""
        expire_at = (self._clock() + self.ttl_seconds) if self.ttl_seconds > 0 else float("inf")
        with self._lock:
            self._data[key] = (expire_at, value)
            self._evict_if_needed()

    def get(self, key, default=None) -> Optional[T]:
        """读取；已过期返回 default（并惰性删除）。"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            expire_at, value = entry
            if expire_at != float("inf") and self._clock() >= expire_at:
                self._data.pop(key, None)
                return default
            return value

    def contains(self, key) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def delete(self, key) -> None:
        with self._lock:
            self._data.pop(key, None)

    def get_timestamp(self, key, default: float = 0.0) -> float:
        """读取条目的写入时间戳（用于间隔判断），已过期返回 default。"""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return default
            expire_at, _value = entry
            now = self._clock()
            if expire_at != float("inf") and now >= expire_at:
                self._data.pop(key, None)
                return default
            # value 为写入时间戳的用法：直接返回 value
            return float(_value) if isinstance(_value, (int, float)) else default

    def cleanup(self) -> int:
        """清除全部过期条目，返回清理数量。"""
        now = self._clock()
        with self._lock:
            stale = [k for k, (expire_at, _v) in self._data.items()
                     if expire_at != float("inf") and now >= expire_at]
            for k in stale:
                self._data.pop(k, None)
            return len(stale)

    def _evict_if_needed(self) -> None:
        # 超限：按插入序淘汰最旧（dict 保持插入序，近似 FIFO/LRU）
        if len(self._data) > self.max_size:
            for k in list(self._data)[: len(self._data) - self.max_size]:
                self._data.pop(k, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def keys(self) -> Iterator[object]:
        with self._lock:
            return iter(list(self._data.keys()))


_MISSING = object()
