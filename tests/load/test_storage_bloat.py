from __future__ import annotations

import importlib.util
from pathlib import Path


def _benchmark_module():
    script = Path(__file__).parents[2] / "scripts" / "benchmark_machine_self.py"
    spec = importlib.util.spec_from_file_location("hal_machine_self_benchmark", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_million_repeated_events_have_constant_run_rows_and_bounded_samples() -> None:
    result = _benchmark_module().benchmark_identical_events(1_000_000)
    assert result["event_run_rows"] == 1
    assert result["represented_observations"] == 1_000_000
    assert result["sample_rows"] <= 11
    assert result["database_growth_bytes"] < 1_000_000
    assert result["blob_growth_bytes"] == 0


def test_one_million_distinct_identifiers_promote_to_bounded_hll() -> None:
    result = _benchmark_module().benchmark_distinct(1_000_000)
    assert result["mode"] == "HLL"
    assert result["exact_member_rows_retained"] == 0
    assert result["serialized_bytes"] < 10_000
    assert result["lower_bound"] <= result["estimate"] <= result["upper_bound"]
    assert result["relative_error"] < 0.05


def test_bucket_rollup_is_verified_before_child_expiry() -> None:
    result = _benchmark_module().benchmark_bucket_rollup()
    assert result["verified_child_links"] == 12
    assert result["children_expired_after_verified_commit"] == 12
    assert result["bucket_rows_after_expiry"] == 1
    assert result["parent_lower_bound"] <= result["parent_estimate"]
    assert result["parent_estimate"] <= result["parent_upper_bound"]
    assert result["parent_relative_error"] < 0.06
    assert result["parent_vs_direct_relative_difference"] < 0.05


def test_one_million_latency_and_frequency_updates_remain_bounded() -> None:
    module = _benchmark_module()
    quantiles = module.benchmark_quantiles(1_000_000)
    frequency = module.benchmark_frequency(1_000_000)
    assert quantiles["retained_items"] < 2_000
    assert quantiles["serialized_bytes"] < 20_000
    assert 45_000 <= quantiles["p50"] <= 55_000
    assert 90_000 <= quantiles["p95"] <= 100_000
    assert frequency["dominant_detected"]
    assert frequency["serialized_bytes"] < 20_000
