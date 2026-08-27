"""轻量级内部 Metrics（Prometheus 文本格式兼容导出）。

设计原则：
- 不引入 Prometheus / 监控平台等外部基础设施
- 线程安全（可能被 to_thread 内的代码调用）
- 对主业务零侵入：所有方法不抛异常、开销为 O(1)
- 提供 snapshot()（dict，内部自省）与 export_text()（Prometheus 文本格式）两种导出
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

    def series(self) -> List[Tuple[Tuple[str, ...], float]]:
        """返回 [(label_values, total)]，供导出（按 label 名序列展开）。"""
        return list(self._values.items())

    def snapshot(self) -> Dict[str, float]:
        return dict(self._values)


class Histogram:
    """直方图（延迟等），固定分桶；暂不支持标签。"""

    def __init__(self, name: str, help_text: str, buckets: Sequence[float] = DEFAULT_BUCKETS):
        self.name = name
        self.help = help_text
        self.buckets = tuple(sorted(buckets))
        self._counts: Dict[Tuple[str, ...], List[float]] = {}
        self._sums: Dict[Tuple[str, ...], float] = {}

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        # 桶计数采用"非累积"存储：value 只落入第一个满足的桶；
        # 导出时再按 Prometheus 规范做累积（否则会双重累积导致数值错误）。
        key = ()
        counts = self._counts.setdefault(key, [0.0] * (len(self.buckets) + 1))
        for i, b in enumerate(self.buckets):
            if value <= b:
                counts[i] += 1.0
                break
        counts[-1] += 1.0  # +Inf
        self._sums[key] = self._sums.get(key, 0.0) + value

    def series(self) -> List[Tuple[Tuple[str, ...], List[float], float]]:
        """返回 [(label_values, 各桶计数(含+Inf), sum)]，供导出。"""
        return [(k, list(v), self._sums.get(k, 0.0)) for k, v in self._counts.items()]

    def snapshot(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for _key, counts in self._counts.items():
            out[f"{self.name}_count"] = out.get(f"{self.name}_count", 0.0) + counts[-1]
            out[f"{self.name}_sum"] = out.get(f"{self.name}_sum", 0.0) + self._sums.get(_key, 0.0)
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

    def snapshot(self) -> Dict[str, float]:
        """返回全部指标的普通 dict（内部自省/调试用，非 Prometheus 格式）。"""
        with self._lock:
            out: Dict[str, float] = {}
            for c in self._counters.values():
                out[c.name] = sum(c.snapshot().values())
            for h in self._histograms.values():
                for key, val in h.snapshot().items():
                    out[key] = out.get(key, 0.0) + val
            return out

    def export_text(self) -> str:
        """导出 Prometheus 文本格式（Prometheus exposition format 0.0.4）。

        - counter：`name{label="value"} value`，label 名取自注册时的 label_names
        - histogram：`name_bucket{le="<上限>"}`（含 +Inf）+ `name_sum` + `name_count`
        """
        lines: List[str] = []
        with self._lock:
            for c in self._counters.values():
                lines.append(f"# HELP {c.name} {c.help}")
                lines.append(f"# TYPE {c.name} counter")
                for key, val in c.series():
                    label = self._format_labels(c.label_names, key)
                    lines.append(f"{c.name}{label} {val:g}")
            for h in self._histograms.values():
                lines.append(f"# HELP {h.name} {h.help}")
                lines.append(f"# TYPE {h.name} histogram")
                for key, counts, total_sum in h.series():
                    cumulative = 0.0
                    for i, b in enumerate(h.buckets):
                        cumulative += counts[i]
                        lines.append(f'{h.name}_bucket{{le="{b:g}"}} {cumulative:g}')
                    lines.append(f'{h.name}_bucket{{le="+Inf"}} {counts[-1]:g}')
                    lines.append(f"{h.name}_sum {total_sum:g}")
                    lines.append(f"{h.name}_count {counts[-1]:g}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_labels(label_names: Tuple[str, ...], values: Tuple[str, ...]) -> str:
        if not label_names:
            return ""
        pairs = ",".join(f'{n}="{v}"' for n, v in zip(label_names, values))
        return "{" + pairs + "}"


# 进程内单例
registry = MetricsRegistry()
