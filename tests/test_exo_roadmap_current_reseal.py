from __future__ import annotations

import hashlib
import json
from pathlib import Path

from devtool.core.execution_state import EXECUTION_SNAPSHOT_SCHEMA_VERSION, ExecutionRunState, ExecutionSnapshot
from devtool.oci_image import load_android_oci_contract


ROOT = Path(__file__).resolve().parents[1]


def test_current_snapshot_schema_is_centralized_and_preserves_exo_v5_plus_contract() -> None:
    snapshot = ExecutionSnapshot.from_state(ExecutionRunState(run_id="reseal"))
    assert EXECUTION_SNAPSHOT_SCHEMA_VERSION == 7
    assert EXECUTION_SNAPSHOT_SCHEMA_VERSION >= 5
    assert snapshot.schema_version == EXECUTION_SNAPSHOT_SCHEMA_VERSION


def test_original_exo_acceptance_modules_and_post_seal_remediations_remain_present() -> None:
    for number in range(1, 12):
        assert list((ROOT / "tests").glob(f"test_exo{number:02d}_*.py")), f"missing EXO-{number:02d} contract"
    for name in (
        "test_exo_roadmap_promise_remediation.py",
        "test_exo_p0_cli_run_finalization.py",
        "test_exo_target_local_jobs_gate_seal.py",
        "test_wrap09_wrapper_final_seal.py",
        "test_wrapper_reseal_hotfix.py",
        "test_exo_lifecycle_hook_convergence.py",
    ):
        assert (ROOT / "tests" / name).is_file(), name


def test_gradle_telemetry_source_and_oci_embedded_copy_are_byte_identical_and_pinned() -> None:
    canonical = ROOT / "devtool/resources/gradle/devtool-events.init.gradle"
    embedded = ROOT / "devtool/resources/oci/android-arm64/devtool-events.init.gradle"
    assert canonical.read_bytes() == embedded.read_bytes()
    digest = hashlib.sha256(canonical.read_bytes()).hexdigest()
    contract = load_android_oci_contract()
    assert contract["devtool_runtime"]["embedded_android_builder"]["gradle_events_init_sha256"] == digest
    validator = (ROOT / "devtool/resources/oci/android-arm64/validate-image.sh").read_text(encoding="utf-8")
    assert digest in validator
    for consumer in (
        "devtool/ssh_tmux_runner.py",
        "devtool/oci_image.py",
        "devtool/oci_execution.py",
        "tests/test_oci05_repository_execution_contract.py",
    ):
        assert digest in (ROOT / consumer).read_text(encoding="utf-8"), consumer


def test_current_exo_seal_docs_distinguish_historical_and_current_schema_versions() -> None:
    final_seal = (ROOT / "EXO_EXECUTION_CONVERGENCE_FINAL_SEAL.md").read_text(encoding="utf-8")
    promise = (ROOT / "EXO_ROADMAP_PROMISE_AUDIT.md").read_text(encoding="utf-8")
    assert "originally qualified canonical snapshot schema version 4" in final_seal
    assert "current additive schema is version 7" in final_seal
    assert "Current compatibility note" in promise
    assert "EXECUTION_SNAPSHOT_SCHEMA_VERSION" in promise


def test_artifact_hook_runtime_is_authoritative_exo_work_not_tasker_only_progress() -> None:
    engine = (ROOT / "devtool/hooks/engine.py").read_text(encoding="utf-8")
    adapter = (ROOT / "devtool/hooks/execution.py").read_text(encoding="utf-8")
    assert "HookExecutionAdapter" in engine
    assert "execution_adapter=execution_adapter" in engine
    assert "ProcessStarted" not in engine  # process events are delegated to the shared runner adapter
    for token in ("NodeDiscovered", "NodeStarted", "NodeProgressChanged", "NodeFinished", "RunnerExecutionAdapter"):
        assert token in adapter
    assert 'lifecycle_authority": "exo"' in adapter
