"""R38-R4: deterministic shape, bounded workload and honest evidence contracts."""
from __future__ import annotations

import json
from pathlib import Path
import os

import pytest
from devtool.workflow_benchmark import (
    WorkflowBenchmarkError, _percentile, _specs, benchmark, main,
)


def test_scheduler_benchmark_fanout_join_and_per_node_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "original-state"))
    result = benchmark(nodes=4, repeats=2, workers=3, work_ms=0.05,
                       shape="fanout_join", source_root=tmp_path)
    assert result["schema_version"] == 1
    assert result["benchmark"] == "r38-r4-native-scheduler-synthetic"
    assert result["source"]["commit"] == ""
    assert result["qualification"] == {
        "real_build": False, "real_cache_hit": False, "device": False, "release": False,
    }
    assert [x["label"] for x in result["cases"]] == [
        "off-workers-1", "off-workers-3", "enforce-workers-1", "enforce-workers-3",
    ]
    for case in result["cases"]:
        assert case["p95_seconds"] >= case["min_seconds"] >= 0
        assert case["median_seconds"] >= 0
        assert len(case["samples"]) == 2
        assert all(x["completed_nodes"] == 4 for x in case["samples"])
        assert all(1 <= x["observed_peak_workers"] <= case["max_workers"] for x in case["samples"])
        assert all(x["cache_environment_probes"] == (4 if case["scratch_isolation"] == "enforce" else 0)
                   for x in case["samples"])
    # The benchmark may not redirect the parent environment permanently.
    assert Path(os.environ["XDG_STATE_HOME"]) == tmp_path / "original-state"
    assert not (tmp_path / "original-state").exists()


def test_chain_never_exceeds_one_active_worker(tmp_path):
    result = benchmark(nodes=4, repeats=1, workers=4, work_ms=0.1,
                       shape="chain", source_root=tmp_path)
    assert all(sample["observed_peak_workers"] == 1
               for case in result["cases"] for sample in case["samples"])


@pytest.mark.parametrize("options,expected", [
    ({"nodes": 0}, "INVALID_NODES"),
    ({"nodes": 129}, "INVALID_NODES"),
    ({"repeats": 0}, "INVALID_REPEATS"),
    ({"repeats": 31}, "INVALID_REPEATS"),
    ({"workers": 1}, "REQUIRES_PARALLEL_COMPARISON"),
    ({"workers": 17}, "INVALID_WORKERS"),
    ({"work_ms": -1}, "INVALID_WORK_MS"),
    ({"work_ms": float("nan")}, "INVALID_WORK_MS"),
    ({"shape": "unknown"}, "INVALID_SHAPE"),
    ({"shape": "fanout_join", "nodes": 1}, "JOIN_REQUIRES_TWO_NODES"),
    ({"nodes": True}, "INVALID_NODES"),
])
def test_invalid_workload_fails_closed(options, expected):
    with pytest.raises(WorkflowBenchmarkError, match=expected):
        benchmark(**options)


def test_benchmark_receipt_cli_valid_json(tmp_path, capsys):
    assert main(["--nodes", "3", "--repeats", "1", "--workers", "2", "--work-ms", "0", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["workload"]["nodes"] == 3
    assert result["workload"]["worker_kind"] == "in_process_synthetic_no_subprocess"
    assert len(result["cases"]) == 4


def test_cli_invalid_returns_error_without_report(capsys):
    assert main(["--nodes", "0"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "R38_BENCH_INVALID_NODES" in captured.err


def test_percentile_interpolation_is_stable():
    assert _percentile([4., 1., 2., 3.], .5) == 2.5
    assert _percentile([4.], .95) == 4.
    with pytest.raises(WorkflowBenchmarkError, match="EMPTY_SAMPLES"):
        _percentile([], .95)


def test_manual_ci_contract_rejects_false_qualification():
    repo = Path(__file__).resolve().parents[1]
    workflow = (repo / ".github/workflows/devtool-r38-dag-benchmark.yml").read_text()
    assert "workflow_dispatch:" in workflow
    assert "actions/checkout@v6" in workflow
    assert "actions/setup-python@v6" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "python -m devtool.workflow_benchmark" in workflow
    assert "real_cache_hit" in workflow
    assert "release" in workflow
    assert "pull_request:" not in workflow
