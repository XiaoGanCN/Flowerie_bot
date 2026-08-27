"""轻量级内部 Metrics。

设计原则：
- 不引入 Prometheus / 监控平台等外部基础设施
- 线程安全（可能被 to_thread 内的代码调用）
- 对主业务零侵入：所有方法不抛异常、开销为 O(1)
- 提供 snapshot()（dict）与 export_text()（Prometheus 文本格式）两种导出
"""
import threading
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_BUCKETS: Tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)


class Counter:
    """计数器（可带标签）。"""

    def __init__(self, name: str, help_text: str, label_names: Sequence[str] = ()):
        self.name = name
        self.help = help_text
        self.label_names = tuple(label_names)
        self._values: Dict[Tuple[str, ...], float] = {}

    def inc(self, labels: Optional[Dict[str, str]] = None, value: float = 1.0) -> None:
        key = self._label_key(labels)
        self._values[key] = self._values.get(key, 0.0) + value

    def _label_key(self, labels: Optional[Dict[str, str]]) -> Tuple[str, ...]:
        if not self.label_names:
            return ()
        if not labels:
            return tuple("" for _ in self.label_names)
        return tuple(str(labels.get(n, "")) for n in self.label_names)

    def snapshot(self) -> Dict[str, float]:
        return dict(self._values)


class Histogram:
    """直方图（延迟等），固定分桶。"""

    def __init__(self, name: str, help_text: str, buckets: Sequence[float] = DEFAULT_BUCKETS):
        self.name = name
        self.help = help_text
        self.buckets = tuple(sorted(buckets))
        self._counts: Dict[Tuple[str, ...], List[float]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        key = self._label_key(labels)
        counts = self._counts.setdefault(key, [0.0] * (len(self.buckets) + 1))
        for i, b in enumerate(self.buckets):
            if value <= b:
                counts[i] += 1.0
        counts[-1] += 1.0  # +Inf
        self._sums[key] = self._sums.get(key, 0.0) + value

    def _label_key(self, labels: Optional[Dict[str, str]]) -> Tuple[str, ...]:
        return tuple(str(labels.get(n, "")) for n in ())  # 直方图暂不支持标签

    def snapshot(self) -> Dict[str, object]:
        out: Dict[str, object] = {}
        for key, counts in self._counts.items():
            out[f"{self.name}_count"] = out.get(f"{self.name}_count", 0.0) + counts[-1]
            out[f"{self.name}_sum"] = out.get(f"{self.name}_sum", 0.0) + self._sums.get(key, 0.0)
        return out


class MetricsRegistry:
    """指标注册表（进程内单例即可）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, Counter] = {}
        self._histograms: Dict[str, Histogram] = {}

    def counter(self, name: str, help_text: str, label_names: Sequence[str] = ()) -> Counter:
        with self._lock:
            c = self._counters.get(name)
            if c is None:
                c = Counter(name, help_text, label_names)
                self._counters[name] = c
            return c

    def histogram(self, name: str, help_text: str, buckets: Sequence[float] = DEFAULT_BUCKETS) -> Histogram:
        with self._lock:
            h = self._histograms.get(name)
            if h is None:
                h = Histogram(name, help_text, buckets)
                self._histograms[name] = h
            return h

    def snapshot(self) -> Dict[str, object]:
        """返回全部指标的普通 dict（供测试/调试/自省）。"""
        with self._lock:
            out: Dict[str, object] = {}
            for c in self._counters.values():
                total = sum(c.snapshot().values())
                out[c.name] = total
            for h in self._histograms.values():
                for key, val in h.snapshot().items():
                    out[key] = out.get(key, 0.0) + float(val)
            return out

    def export_text(self) -> str:
        """导出 Prometheus 文本格式（可直接被抓取或落盘）。"""
        lines: List[str] = []
        with self._lock:
            for c in self._counters.values():
                lines.append(f"# HELP {c.name} {c.help}")
                lines.append(f"# TYPE {c.name} counter")
                for key, val in c.snapshot().items():
                    label = ""
                    if key:
                        label = "{" + ",".join(f'"{k}"' for k in key) + "}"
                    lines.append(f"{c.name}{label} {val:g}")
            for h in self._histograms.values():
                lines.append(f"# HELP {h.name} {h.help}")
                lines.append(f"# TYPE {h.name} histogram")
                for key, val in h.snapshot().items():
                    lines.append(f"{key} {val:g}")
        return "\n".join(lines) + "\n"


# 进程内单例
registry = MetricsRegistry()
