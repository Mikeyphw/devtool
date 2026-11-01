from __future__ import annotations

"""R38-R4 bounded, synthetic-worker benchmark of the *native* workflow scheduler.

No external build is performed; this measures scheduler admission, synthetic
workload overlap and optional R38 scratch routing.  It is not a cache-hit,
Android, real-consumer or release qualification benchmark.
"""

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Iterator
from unittest.mock import patch

from .models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from .workflow_scheduler import run_workflow_specs


class WorkflowBenchmarkError(ValueError):
    """Invalid workload or an observed native scheduling invariant violation."""


def _positive_int(raw: int, *, name: str, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= maximum:
        raise WorkflowBenchmarkError(f"R38_BENCH_INVALID_{name.upper()}")
    return raw


def _nonnegative_ms(raw: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (float, int)) or not 0 <= raw <= 100:
        raise WorkflowBenchmarkError("R38_BENCH_INVALID_WORK_MS")
    return float(raw)


def _git_identity(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for field, argument in (("commit", "HEAD"), ("tree", "HEAD^{tree}")):
        try:
            value = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", argument],
                capture_output=True, text=True, timeout=4, check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            value = ""
        values[field] = value
    return values


def _specs(count: int, *, shape: str) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    for i in range(count):
        dependencies = [f"n{i - 1:03d}"] if shape == "chain" and i else []
        if shape == "fanout_join" and i == count - 1:
            dependencies = [f"n{j:03d}" for j in range(i)]
        key = f"n{i:03d}"
        specs.append(CommandSpec(key, ["synthetic-worker", key], metadata={
            "workflow_node_key": key, "workflow_target": "benchmark",
            "workflow_name": "r38-r4", "workflow_depends_on": dependencies,
            "workflow_read_only": True, "workflow_write_scope_known": True,
            "workflow_write_scopes": [], "workflow_read_scopes": [],
        }))
    return specs


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise WorkflowBenchmarkError("R38_BENCH_EMPTY_SAMPLES")
    index = (len(ordered) - 1) * percentile
    lo = int(index)
    fraction = index - lo
    if lo == len(ordered) - 1:
        return ordered[-1]
    return ordered[lo] * (1.0 - fraction) + ordered[lo + 1] * fraction


@contextmanager
def _benchmark_state(root: Path) -> Iterator[None]:
    # Keep execution stores / scratch leases out of the user's real Devtool
    # state. Never delete an existing state home or mutate its contents.
    with patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "state")}), patch(
        "devtool.workflow_retry.capture_start", return_value=None,
    ):
        yield


def _run_case(
    root: Path, *, nodes: int, shape: str, workers: int,
    scratch: str, work_ms: float,
) -> dict[str, Any]:
    config = ProjectConfig(
        repo_root=root, kind="command", runner="command", project_root=str(root),
        workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(
            parallel=True, max_parallel_nodes=workers,
        )),
    )
    specs = _specs(nodes, shape=shape)
    lock = threading.Lock()
    active = 0
    peak = 0
    seen: set[str] = set()
    cache_probes = 0

    def synthetic_runner(_config: ProjectConfig, current: list[CommandSpec], **kwargs: Any):
        nonlocal active, peak, cache_probes
        if kwargs.get("workflow_initial_cancelled") or kwargs.get("workflow_initial_fail_fast"):
            raise WorkflowBenchmarkError("R38_BENCH_UNEXPECTED_CANCELLATION")
        key = str(current[0].metadata["workflow_node_key"])
        deps = current[0].metadata["workflow_depends_on"]
        if any(kwargs.get("workflow_initial_statuses", {}).get(dep) != "passed" for dep in deps):
            raise WorkflowBenchmarkError("R38_BENCH_UNSATISFIED_DEPENDENCY")
        with lock:
            if key in seen:
                raise WorkflowBenchmarkError("R38_BENCH_DUPLICATE_NODE")
            seen.add(key)
            active += 1
            peak = max(peak, active)
        try:
            if scratch == "enforce":
                private_root = Path(current[0].env["DEVTOOL_R38_SCRATCH_ROOT"])
                cache_root = Path(current[0].env["XDG_CACHE_HOME"])
                if not private_root.is_dir() or not cache_root.is_dir() or private_root not in cache_root.parents:
                    raise WorkflowBenchmarkError("R38_BENCH_SCRATCH_ROOT_INVALID")
                # One bounded cache-environment filesystem probe per node. It
                # is deliberately NOT a real cache hit or warm build.
                (cache_root / "bench-marker").write_bytes(key.encode("ascii"))
                with lock:
                    cache_probes += 1
            if work_ms:
                time.sleep(work_ms / 1000)
        finally:
            with lock:
                active -= 1
        return [OperationResult(key, ["synthetic-worker"], 0, work_ms / 1000, "", [],
                                metadata={"workflow_node_key": key, "workflow_node_state": "passed"})], ""

    from . import runners
    start = time.perf_counter()
    with patch.object(runners, "run_specs", synthetic_runner):
        results, _, plan = run_workflow_specs(
            config, specs, verbose=False, dry_run=False, continue_on_error=False,
            log_path=None, parallel_requested=True, max_parallel_nodes=workers,
            scratch_isolation_mode=scratch, write_audit_mode="off",
        )
    elapsed = time.perf_counter() - start
    if plan is None or len(results) != nodes or len(seen) != nodes:
        raise WorkflowBenchmarkError("R38_BENCH_MISSING_NODE_RESULT")
    if any(int(result.returncode) or result.metadata.get("workflow_node_state") != "passed" for result in results):
        raise WorkflowBenchmarkError("R38_BENCH_FAILED_NODE")
    if len(plan.actual_start_order) != nodes or len(plan.actual_finish_order) != nodes:
        raise WorkflowBenchmarkError("R38_BENCH_SCHEDULER_TRACE_INCOMPLETE")
    if not (1 <= peak <= workers) or (shape == "chain" and peak != 1):
        raise WorkflowBenchmarkError("R38_BENCH_PARALLEL_BOUND_VIOLATED")
    if scratch == "enforce" and (cache_probes != nodes or any(
        r.metadata.get("workflow_temporary_cleanup", {}).get("status") != "cleaned" for r in results
    )):
        raise WorkflowBenchmarkError("R38_BENCH_SCRATCH_CLEANUP_FAILED")
    return {
        "elapsed_seconds": round(elapsed, 6), "observed_peak_workers": peak,
        "completed_nodes": len(results), "cache_environment_probes": cache_probes,
        "node_elapsed_seconds": round(sum(float(r.metadata.get("workflow_scheduler_node_elapsed_seconds", 0)) for r in results), 6),
    }


def benchmark(
    *, nodes: int = 12, repeats: int = 3, workers: int = 4,
    work_ms: float = 2.0, shape: str = "fanout_join",
    source_root: Path | None = None,
) -> dict[str, Any]:
    nodes = _positive_int(nodes, name="nodes", maximum=128)
    repeats = _positive_int(repeats, name="repeats", maximum=30)
    workers = _positive_int(workers, name="workers", maximum=16)
    if workers < 2:
        raise WorkflowBenchmarkError("R38_BENCH_REQUIRES_PARALLEL_COMPARISON")
    work_ms = _nonnegative_ms(work_ms)
    if shape not in {"fanout", "chain", "fanout_join"}:
        raise WorkflowBenchmarkError("R38_BENCH_INVALID_SHAPE")
    if shape == "fanout_join" and nodes < 2:
        raise WorkflowBenchmarkError("R38_BENCH_JOIN_REQUIRES_TWO_NODES")
    if nodes * repeats * 4 > 12_000:
        raise WorkflowBenchmarkError("R38_BENCH_WORKLOAD_TOO_LARGE")
    repo = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[1]
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="devtool-r38-r4-") as td:
        root = Path(td)
        with _benchmark_state(root):
            for scratch in ("off", "enforce"):
                for max_workers in (1, workers):
                    label = f"{scratch}-workers-{max_workers}"
                    samples = [_run_case(root, nodes=nodes, shape=shape, workers=max_workers,
                                         scratch=scratch, work_ms=work_ms) for _ in range(repeats)]
                    timings = [x["elapsed_seconds"] for x in samples]
                    cases.append({
                        "label": label, "scratch_isolation": scratch, "max_workers": max_workers,
                        "median_seconds": round(statistics.median(timings), 6),
                        "p95_seconds": round(_percentile(timings, .95), 6),
                        "min_seconds": round(min(timings), 6),
                        "max_seconds": round(max(timings), 6),
                        "samples": samples,
                    })
    if not cases or any(x["completed_nodes"] != nodes for case in cases for x in case["samples"]):
        raise WorkflowBenchmarkError("R38_BENCH_INVALID_RECEIPT")
    return {
        "schema_version": 1, "benchmark": "r38-r4-native-scheduler-synthetic",
        "qualification": {"real_build": False, "real_cache_hit": False,
                          "device": False, "release": False},
        "workload": {"nodes": nodes, "repeats": repeats, "workers": workers,
                     "work_ms": work_ms, "shape": shape,
                     "worker_kind": "in_process_synthetic_no_subprocess"},
        "host": {"platform": platform.platform(), "python": platform.python_version(),
                 "cpu_count": os.cpu_count()},
        "source": _git_identity(repo), "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R38-R4 bounded synthetic native DAG scheduler benchmark")
    parser.add_argument("--nodes", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--work-ms", type=float, default=2.)
    parser.add_argument("--shape", choices=("fanout", "chain", "fanout_join"), default="fanout_join")
    parser.add_argument("--json", action="store_true", help="Machine-readable report on stdout (default)")
    args = parser.parse_args(argv)
    try:
        report = benchmark(nodes=args.nodes, repeats=args.repeats, workers=args.workers,
                           work_ms=args.work_ms, shape=args.shape)
    except WorkflowBenchmarkError as exc:
        print(f"{exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
