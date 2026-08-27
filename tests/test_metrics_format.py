"""Metrics Prometheus 格式合规测试（场景 17）。"""
from src.utils.metrics import Counter, Histogram, MetricsRegistry


def test_counter_label_format_prometheus():
    """counter 带标签时输出 `name{label="value"}` 而非裸值列表。"""
    c = Counter("test_labelled", "help", label_names=["reason"])
    c.inc({"reason": "whitelist"})
    c.inc({"reason": "budget"})
    c.inc({"reason": "budget"})

    reg = MetricsRegistry()
    reg._counters["test_labelled"] = c
    text = reg.export_text()
    assert 'test_labelled{reason="whitelist"} 1' in text
    assert 'test_labelled{reason="budget"} 2' in text
    assert '{"whitelist"}' not in text  # 旧错误格式


def test_counter_without_labels():
    c = Counter("test_plain", "help")
    c.inc()
    reg = MetricsRegistry()
    reg._counters["test_plain"] = c
    text = reg.export_text()
    assert "test_plain 1" in text
    assert "# TYPE test_plain counter" in text
    assert "# HELP test_plain help" in text


def test_histogram_prometheus_buckets():
    """直方图输出 _bucket{le=...}（含 +Inf）+ _sum + _count。"""
    h = Histogram("test_hist", "help", buckets=[0.5, 1.0, 2.0])
    h.observe(0.3)
    h.observe(0.7)
    h.observe(5.0)

    reg = MetricsRegistry()
    reg._histograms["test_hist"] = h
    text = reg.export_text()
    assert '# TYPE test_hist histogram' in text
    assert 'test_hist_bucket{le="0.5"} 1' in text
    assert 'test_hist_bucket{le="1"} 2' in text
    assert 'test_hist_bucket{le="2"} 2' in text
    assert 'test_hist_bucket{le="+Inf"} 3' in text
    assert 'test_hist_sum 6' in text
    assert 'test_hist_count 3' in text


def test_snapshot_aggregates_labels():
    """snapshot() 是内部自省用途：按指标名聚合，不带标签细节。"""
    c = Counter("test_agg", "help", label_names=["a"])
    c.inc({"a": "x"})
    c.inc({"a": "y"})
    reg = MetricsRegistry()
    reg._counters["test_agg"] = c
    assert reg.snapshot()["test_agg"] == 2.0
