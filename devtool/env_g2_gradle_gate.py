from __future__ import annotations

"""ENV-G2 authoritative real-Gradle milestone qualification."""

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from .config import load_project_config
from .project_runners import JvmGradleRunner
from .runners import run_specs


class EnvG2GateError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EnvG2GateError(message)


def _gradle_executable() -> str:
    override = os.environ.get("DEVTOOL_ENV_G2_GRADLE", "").strip()
    if override:
        if os.environ.get("DEVTOOL_ENV_G2_ALLOW_TEST_GRADLE", "").strip() != "1":
            raise EnvG2GateError("DEVTOOL_ENV_G2_GRADLE is forbidden for authoritative ENV-G2 validation")
        candidate = Path(override).expanduser().resolve(strict=False)
        _require(candidate.is_file(), f"ENV-G2 test Gradle override does not exist: {candidate}")
        return str(candidate)
    resolved = shutil.which("gradle")
    _require(bool(resolved), "ENV-G2 requires a real Gradle executable on PATH")
    return str(Path(str(resolved)).resolve(strict=False))


def _write_fixture(root: Path, gradle: str, user_home: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name = 'devtool-env-g2'\n", encoding="utf-8")
    bulk = ", ".join(f"'bulk{i}'" for i in range(1, 49))
    (root / "build.gradle").write_text(
        """
for (int i = 1; i <= 48; i++) {
    tasks.register("bulk${i}")
}

tasks.register('clean') { doLast { println('ENV_G2_CLEAN') } }
tasks.register('gateAlpha') { doLast { println('ENV_G2_ALPHA') } }
tasks.register('gateBeta') { doLast { println('ENV_G2_BETA') } }
tasks.register('gateBulk') { dependsOn(%s) }
""" % bulk,
        encoding="utf-8",
    )
    gradle_q = gradle.replace("\\", "\\\\").replace('"', '\\"')
    home_q = str(user_home).replace("\\", "\\\\").replace('"', '\\"')
    (root / ".devtool.toml").write_text(
        f'''[project]\nrunner = "gradle"\n\n[execution]\nenvironment = "native-termux"\n\n[gradle]\ncommand = "{gradle_q}"\nuser_home = "{home_q}"\ntelemetry_level = "standard"\nconfiguration_cache = "required"\nbuild_cache = true\ndaemon = true\nvfs = true\nparallel = true\nmax_workers = 2\nheap_mb = 768\nhost_safe_heap_ceiling_mb = 1024\nresource_profile = "balanced"\nfailure_policy = "collect"\n''',
        encoding="utf-8",
    )


def _receipt(result: Any) -> dict[str, Any]:
    payload = dict(result.metadata.get("gradle_execution_receipt") or {})
    _require(bool(payload), "Gradle execution did not emit GradleExecutionReceipt")
    return payload


def _validate_receipt(receipt: dict[str, Any], *, second: bool) -> None:
    requested = list(receipt.get("requested_roots") or [])
    observed = list(receipt.get("observed_tasks") or [])
    _require(requested == ["gateAlpha", "gateBeta", "gateBulk"], f"requested roots drifted: {requested}")
    _require(receipt.get("success") is True, "Gradle receipt does not record success")
    _require(receipt.get("process_executed") is True, "ENV-G2 must prove a real Gradle process executed")
    _require(bool(receipt.get("launcher_identity")), "launcher identity missing from receipt")
    _require(bool(receipt.get("jdk_identity")), "JDK identity missing from receipt")
    _require(receipt.get("resource_profile") == "balanced", f"expected balanced profile, got {receipt.get('resource_profile')!r}")

    cc = dict(receipt.get("configuration_cache") or {})
    ccq = dict(receipt.get("configuration_cache_qualification") or {})
    _require(cc.get("requested") == "required", f"CC requested truth drifted: {cc}")
    _require(cc.get("effective") == "on", f"CC was not effective: {cc}")
    _require(ccq.get("compatible") is True, f"CC qualification is not compatible: {ccq}")
    expected_states = {"reused"} if second else {"miss", "stored"}
    _require(cc.get("observed_state") in expected_states, f"unexpected Configuration Cache state: {cc}")

    for field in ("build_cache", "daemon", "vfs"):
        policy = dict(receipt.get(field) or {})
        _require(policy.get("effective") is True, f"balanced gate unexpectedly disabled {field}: {policy}")
    workers = dict(receipt.get("workers") or {})
    _require(int(workers.get("effective") or 0) == 2, f"balanced gate did not retain 2 workers: {workers}")
    parallel = dict(receipt.get("parallel") or {})
    _require(parallel.get("effective") is True, f"balanced gate disabled parallel execution: {parallel}")
    telemetry = dict(receipt.get("telemetry") or {})
    _require(telemetry.get("effective") == "standard", f"standard telemetry not effective: {telemetry}")
    requested_normalized = {str(value).lstrip(":") for value in requested}
    observed_normalized = {str(value).lstrip(":") for value in observed}
    _require(requested_normalized.issubset(observed_normalized), f"requested roots absent from observed task truth: {observed}")
    _require(len(observed) > len(requested), "requested roots and observed dependency outcomes were collapsed")
    _require(bool(receipt.get("run_ref")), "RUN reference missing from receipt")


def _validate_bounded_telemetry(output: str, observed_count: int) -> dict[str, int]:
    event_lines = [line for line in output.splitlines() if line.startswith("DEVTOOL_EVENT ")]
    _require(event_lines, "standard Gradle telemetry emitted no structured task events")
    _require(all(len(line) <= 4096 for line in event_lines), "structured telemetry emitted an unbounded event line")
    _require(len(event_lines) <= observed_count + 4, "structured telemetry exceeded the bounded task-completion envelope")
    _require("artifactOutputs" not in output, "standard telemetry unexpectedly performed legacy output scanning")
    return {"event_lines": len(event_lines), "max_line_bytes": max(len(line.encode()) for line in event_lines)}


def _validate_barrier_processes(config: Any, runner: JvmGradleRunner) -> dict[str, Any]:
    specs = runner.build_task_specs(
        config, ["clean", "gateAlpha", "gateBeta"],
        name="env-g2-real-barrier", action="gate",
    )
    _require(len(specs) == 2, f"clean + normal roots should require exactly two Gradle processes, got {len(specs)}")
    roots = [list(spec.metadata.get("gradle_tasks") or []) for spec in specs]
    _require(roots == [["clean"], ["gateAlpha", "gateBeta"]], f"semantic barrier order/coalescing drifted: {roots}")
    results, _ = run_specs(config, specs, verbose=False, continue_on_error=False)
    _require(len(results) == 2, f"expected two physical Gradle results for clean barrier proof, got {len(results)}")
    _require(all(result.returncode == 0 for result in results), "semantic barrier proof contained a failed Gradle process")
    receipts = [_receipt(result) for result in results]
    _require(all(receipt.get("process_executed") is True for receipt in receipts), "semantic barrier proof did not execute every physical process")
    observed = {str(task).lstrip(":") for receipt in receipts for task in (receipt.get("observed_tasks") or [])}
    _require({"clean", "gateAlpha", "gateBeta"}.issubset(observed), f"semantic barrier proof lost required outcomes: {sorted(observed)}")
    return {
        "physical_processes": len(results),
        "invocation_roots": roots,
        "observed_required_roots": sorted({"clean", "gateAlpha", "gateBeta"} & observed),
    }


def run_gate() -> dict[str, Any]:
    gradle = _gradle_executable()
    with tempfile.TemporaryDirectory(prefix="devtool-env-g2-") as tmp:
        root = Path(tmp).resolve()
        user_home = root / ".gradle-home"
        user_home.mkdir(parents=True, exist_ok=True)
        _write_fixture(root, gradle, user_home)
        config = load_project_config(str(root), None, allow_ambiguous_target=True)
        runner = JvmGradleRunner()
        runs: list[dict[str, Any]] = []
        for index in (1, 2):
            specs = runner.build_task_specs(
                config, ["gateAlpha", "gateBeta", "gateBulk"],
                name=f"env-g2-real-gradle-{index}", action="gate",
            )
            _require(len(specs) == 1, f"barrier-free roots should coalesce into one Gradle process, got {len(specs)}")
            plan = dict(specs[0].metadata.get("gradle_execution_plan") or {})
            _require(len(plan.get("invocations") or []) == 1, "barrier-free gate roots did not coalesce")
            results, raw = run_specs(
                config, specs, verbose=False, continue_on_error=False,
            )
            _require(len(results) == 1, f"expected one Gradle result, got {len(results)}")
            result = results[0]
            _require(result.returncode == 0, f"Gradle gate process {index} failed with exit {result.returncode}\n{result.output[-4000:]}")
            receipt = _receipt(result)
            _validate_receipt(receipt, second=index == 2)
            telemetry = _validate_bounded_telemetry(result.output or raw, len(receipt.get("observed_tasks") or []))
            runs.append({
                "index": index,
                "receipt_id": receipt.get("receipt_id"),
                "plan_digest": receipt.get("plan_digest"),
                "context_digest": receipt.get("context_digest"),
                "cc_qualification_id": dict(receipt.get("configuration_cache_qualification") or {}).get("qualification_id"),
                "configuration_cache_state": dict(receipt.get("configuration_cache") or {}).get("observed_state"),
                "observed_task_count": len(receipt.get("observed_tasks") or []),
                **telemetry,
            })
        _require(runs[0]["context_digest"] == runs[1]["context_digest"], "unchanged real Gradle runs changed execution context identity")
        _require(runs[0]["cc_qualification_id"] == runs[1]["cc_qualification_id"], "unchanged real Gradle runs changed Configuration Cache qualification identity")
        barrier = _validate_barrier_processes(config, runner)
        return {
            "status": "passed", "gradle": gradle,
            "first": runs[0], "second": runs[1], "barrier": barrier,
            "configuration_cache_transition": [runs[0]["configuration_cache_state"], runs[1]["configuration_cache_state"]],
        }


def main() -> int:
    try:
        payload = run_gate()
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": f"{type(exc).__name__}: {exc}"}, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
