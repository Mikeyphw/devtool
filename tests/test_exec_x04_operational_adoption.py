from __future__ import annotations

from pathlib import Path
import json

from devtool.exec_operational_adoption import (
    OperationalContext,
    SingleflightQualificationIndex,
    SourceProofRetentionIndex,
    build_operational_adoption_plan,
    context_digest,
    diff_operational_plans,
    explain_decision,
    prepare_operational_specs,
    rebuild_reuse_index_from_history,
)
from devtool.core.reuse import ReuseHistoryIndex, result_identity, stable_digest
from devtool.models import CommandSpec


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "tracked.txt").write_text("v1\n", encoding="utf-8")
    (root / "other.txt").write_text("v1\n", encoding="utf-8")
    return root


def _spec(name: str, root: Path, *, input_name: str = "tracked.txt", provider: str = "") -> CommandSpec:
    metadata = {"workflow_inputs": [input_name]}
    if provider:
        metadata["provider_identity"] = provider
        metadata["reuse_portability"] = "same-provider"
    return CommandSpec(name, ["python", "-c", f"print('{name}')"], cwd=str(root), metadata=metadata)


def _candidate_for(spec: CommandSpec, *, run_id: str = "run-1") -> dict[str, object]:
    fingerprint = dict(spec.metadata["reuse_fingerprint"])
    output_manifest = {"digest": stable_digest({"outputs": [], "evidence": []}), "outputs": [], "evidence": []}
    return {
        "schema_version": 2,
        "node_definition_id": spec.metadata["node_definition_id"],
        "qualification_complete": True,
        "reusable": True,
        "execution_status": "succeeded",
        "fingerprint": fingerprint,
        "source_run_id": run_id,
        "source_node_id": spec.metadata["node_definition_id"],
        "result_identity": result_identity(
            fingerprint_digest=fingerprint["digest"],
            output_manifest_digest=output_manifest["digest"],
            execution_status="succeeded",
            evidence_refs=(f"run:{run_id}",),
        ),
        "output_manifest": output_manifest,
        "evidence_refs": [f"run:{run_id}"],
    }


def test_local_workflow_and_native_runner_share_exec_identity_but_keep_policy_context(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    workflow = OperationalContext(domain="local-workflow", operation="gate", target="devtool", workflow_id="wf:gate")
    native = OperationalContext(domain="native-runner", operation="gate", target="devtool", workflow_id="wf:gate")
    wf_spec = prepare_operational_specs([_spec("unit", root)], repo_root=root, context=workflow)[0]
    native_spec = prepare_operational_specs([_spec("unit", root)], repo_root=root, context=native)[0]
    assert wf_spec.metadata["node_definition_id"] == native_spec.metadata["node_definition_id"]
    assert wf_spec.metadata["exec_domain"] == "local-workflow"
    assert native_spec.metadata["exec_domain"] == "native-runner"
    assert wf_spec.metadata["exec_context_digest"] != native_spec.metadata["exec_context_digest"]


def test_no_validate_transaction_cannot_fabricate_validation_proof(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    context = OperationalContext(domain="transaction", operation="apply-overlay", validation_mode="no-validate")
    plan = build_operational_adoption_plan([_spec("artifact-check", root)], repo_root=root, context=context)
    decision = plan.decisions[0]
    assert decision.disposition == "run"
    assert decision.validation_proof_eligible is False
    assert explain_decision(decision)["kind"] == "why-rerun"


def test_reused_source_proof_is_pinned_for_retention(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    context = OperationalContext(domain="local-workflow", operation="validate")
    prepared = prepare_operational_specs([_spec("unit", root)], repo_root=root, context=context)
    history = ReuseHistoryIndex()
    history.add(_candidate_for(prepared[0], run_id="source-run"))
    retention = SourceProofRetentionIndex()
    plan = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=context, history=history, retention=retention)
    decision = plan.decisions[0]
    assert decision.disposition == "reuse"
    assert decision.source_proof is not None
    assert decision.source_proof.pinned is True
    assert retention.contains(decision.source_proof)
    explanation = explain_decision(decision)
    assert explanation["kind"] == "why-reuse"
    assert explanation["source_proof"]["run_id"] == "source-run"


def test_changed_input_invalidates_only_changed_frontier(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    context = OperationalContext(domain="roadmap", operation="gate", workflow_id="roadmap:exec")
    prepared = prepare_operational_specs([
        _spec("alpha", root, input_name="tracked.txt"),
        _spec("beta", root, input_name="other.txt"),
    ], repo_root=root, context=context)
    history = ReuseHistoryIndex()
    for spec in prepared:
        history.add(_candidate_for(spec, run_id=f"source-{spec.name}"))
    (root / "other.txt").write_text("v2\n", encoding="utf-8")
    plan = build_operational_adoption_plan([
        _spec("alpha", root, input_name="tracked.txt"),
        _spec("beta", root, input_name="other.txt"),
    ], repo_root=root, context=context, history=history)
    by_name = {d.spec_name: d for d in plan.decisions}
    assert by_name["alpha"].disposition == "reuse"
    assert by_name["beta"].disposition == "invalidated"
    assert "input_manifest_digest" in by_name["beta"].reason


def test_explicit_fresh_and_rerun_controls_override_reuse_without_corrupting_history(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    base = OperationalContext(domain="local-workflow", operation="validate")
    prepared = prepare_operational_specs([_spec("unit", root)], repo_root=root, context=base)
    history = ReuseHistoryIndex(); history.add(_candidate_for(prepared[0]))
    fresh = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=OperationalContext(domain="local-workflow", operation="validate", fresh=True), history=history)
    rerun = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=OperationalContext(domain="local-workflow", operation="validate", rerun=True), history=history)
    assert fresh.decisions[0].disposition == "run"
    assert fresh.decisions[0].changed_predicates == ("fresh",)
    assert rerun.decisions[0].disposition == "run"
    assert "rerun" in rerun.decisions[0].changed_predicates


def test_remote_and_device_bound_work_never_crosses_incompatible_boundaries(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    a = OperationalContext(domain="remote", operation="validate", provider="ssh", remote_identity="tablet")
    b = OperationalContext(domain="remote", operation="validate", provider="ssh", remote_identity="mom-phone")
    da = OperationalContext(domain="device", operation="instrumentation", provider="adb", device_identity="device-a")
    db = OperationalContext(domain="device", operation="instrumentation", provider="adb", device_identity="device-b")
    assert context_digest(a) != context_digest(b)
    assert context_digest(da) != context_digest(db)
    spec_a = prepare_operational_specs([_spec("probe", root)], repo_root=root, context=da)[0]
    spec_b = prepare_operational_specs([_spec("probe", root)], repo_root=root, context=db)[0]
    assert spec_a.metadata["reuse_fingerprint"]["environment_digest"] != spec_b.metadata["reuse_fingerprint"]["environment_digest"]


def test_selected_test_execution_is_semantic_input_not_validation_shortcut(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    one = OperationalContext(domain="selected-test", operation="pytest", selected_tests=("A::test_one",))
    two = OperationalContext(domain="selected-test", operation="pytest", selected_tests=("A::test_two",))
    spec_one = prepare_operational_specs([_spec("pytest", root)], repo_root=root, context=one)[0]
    spec_two = prepare_operational_specs([_spec("pytest", root)], repo_root=root, context=two)[0]
    assert spec_one.metadata["test_selection"] == ["A::test_one"]
    assert spec_one.metadata["reuse_fingerprint"]["environment_digest"] != spec_two.metadata["reuse_fingerprint"]["environment_digest"]


def test_singleflight_candidate_qualification_deduplicates_concurrent_lookup() -> None:
    index = SingleflightQualificationIndex()
    first, token = index.acquire("candidate:1")
    second, same = index.acquire("candidate:1")
    assert first is True
    assert second is False
    assert same == token
    assert index.release("candidate:1", token) is True
    third, token2 = index.acquire("candidate:1")
    assert third is True
    assert token2 != token


def test_rebuildable_reuse_index_contains_no_mutable_session_state(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    context = OperationalContext(domain="federation", operation="resume", provider="local")
    plan = build_operational_adoption_plan([_spec("child", root)], repo_root=root, context=context)
    rebuilt = rebuild_reuse_index_from_history([plan.as_dict()])
    encoded = json.dumps(rebuilt, sort_keys=True)
    assert "records" in rebuilt
    assert "mutable_session" not in encoded
    assert "phase_state" not in encoded


def test_diff_explains_identity_and_disposition_changes_without_presentation_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=OperationalContext(domain="validate", operation="run"))
    after = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=OperationalContext(domain="validate", operation="run", fresh=True))
    diff = diff_operational_plans(before, after)
    row = diff["rows"][0]
    assert row["before"] == "run"
    assert row["after"] == "run"
    assert row["presentation_only"] is False
    assert before.identity_digest != after.identity_digest


def test_materialized_decision_retains_source_proof_when_history_requires_materialization(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    context = OperationalContext(domain="transaction", operation="materialize")
    prepared = prepare_operational_specs([_spec("artifact", root)], repo_root=root, context=context)
    fingerprint = dict(prepared[0].metadata["reuse_fingerprint"])
    output_manifest = {
        "digest": stable_digest({"entries": [], "evidence": [], "materialization_refs": ["artifact:sha256:" + "a" * 64]}),
        "entries": [{"path": "missing.bin", "kind": "file", "digest": "missing", "exists": True}],
        "evidence": [],
        "materialization_refs": ["artifact:sha256:" + "a" * 64],
        "complete": True,
    }
    history = ReuseHistoryIndex()
    history.add({
        "schema_version": 2,
        "node_definition_id": prepared[0].metadata["node_definition_id"],
        "qualification_complete": True,
        "reusable": True,
        "execution_status": "succeeded",
        "fingerprint": fingerprint,
        "source_run_id": "source-materialize",
        "source_node_id": prepared[0].metadata["node_definition_id"],
        "result_identity": result_identity(fingerprint_digest=fingerprint["digest"], output_manifest_digest=output_manifest["digest"], execution_status="succeeded", evidence_refs=("artifact:sha256:" + "a" * 64,)),
        "output_manifest": output_manifest,
        "evidence_refs": ["artifact:sha256:" + "a" * 64],
    })
    retention = SourceProofRetentionIndex()
    plan = build_operational_adoption_plan([_spec("artifact", root)], repo_root=root, context=context, history=history, retention=retention)
    decision = plan.decisions[0]
    assert decision.disposition == "materialize"
    assert decision.materialized is True
    assert decision.source_proof is not None and decision.source_proof.pinned is True


def test_adoption_plan_is_json_projection_for_pres_without_owning_rendering(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    plan = build_operational_adoption_plan([_spec("unit", root)], repo_root=root, context=OperationalContext(domain="presentation", operation="explain"))
    payload = plan.as_dict()
    assert payload["kind"] == "exec-x04-operational-adoption-v1"
    assert payload["identity_digest"]
    assert payload["decisions"][0]["metrics"]["hashing_policy"] == "node-scoped"
    assert "ansi" not in json.dumps(payload).lower()
