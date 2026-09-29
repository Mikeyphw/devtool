from __future__ import annotations

import json
from pathlib import Path

from devtool.artifact_tests import run_artifact_tests
from devtool.artifacts import artifact_validation_refs
from devtool.config import load_project_config
from devtool.core.execution_semantics import (
    ApplicabilityPolicy,
    CheckpointPolicy,
    ResolvedValidationContract,
    ValidationCheck,
    ValidationOutcome,
    ValidationOutcomeState,
)
from devtool.core.reuse import ReuseHistoryIndex, result_identity, stable_digest
from devtool.core.reuse_planner import ReusePlanner
from devtool.models import CommandSpec, OperationResult
from devtool.validation_authority import (
    bind_validation_specs_to_authority,
    build_private_state,
    exo_projection_from_directory,
    semantic_control_intent,
    validation_reuse_plan,
)
from devtool.validation_sessions import (
    create_session,
    record_phase_result,
    required_checkpoint_satisfied,
    select_phase_keys_for_control,
    stored_specs,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".devtool.toml").write_text(
        """
[project]
runner = "python"

[workspace]
default_target = "devtool"

[targets.devtool]
runner = "python"
root = "."

[[test]]
id = "unit"
runner = "command"
command = ["python", "-c", "print('ok')"]

[targets.devtool.workflows]
gate = [
  { id = "unit", ref = "test:unit" },
]
""".strip()+"\n",
        encoding="utf-8",
    )
    (root / "alpha.txt").write_text("alpha-v1\n", encoding="utf-8")
    (root / "beta.txt").write_text("beta-v1\n", encoding="utf-8")
    return root


def _spec(name: str, root: Path, *, input_name: str, phase: str = "", deps=(), required: bool = True) -> CommandSpec:
    return CommandSpec(
        name,
        ["python", "-c", f"print('{name}')"],
        cwd=str(root),
        metadata={
            "validation_phase_name": phase or name,
            "validation_phase_key": f"repository:{name}:1",
            "validation_check_id": f"check:{name}",
            "validation_depends_on": list(deps),
            "validation_required": required,
            "workflow_inputs": [input_name],
        },
    )


def _candidate_for(spec: CommandSpec, *, run_id: str, node_id: str) -> dict[str, object]:
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
        "source_node_id": node_id,
        "result_identity": result_identity(
            fingerprint_digest=fingerprint["digest"],
            output_manifest_digest=output_manifest["digest"],
            execution_status="succeeded",
            evidence_refs=(f"run:{run_id}",),
        ),
        "output_manifest": output_manifest,
        "evidence_refs": [f"run:{run_id}"],
    }


def test_one_graph_private_state_contains_references_not_execution_truth(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(
        config,
        [_spec("alpha", root, input_name="alpha.txt"), _spec("beta", root, input_name="beta.txt", deps=("alpha",))],
        apply_context={"run_refs": {"exo_run_ref": "exo:run-1"}},
        replace_active=True,
    )
    for phase in session.phases:
        record_phase_result(session, phase.key, OperationResult(phase.spec_name, ["ok"], 0, 0.01, "ok", []))
    state = build_private_state(session)
    encoded = json.dumps(state, sort_keys=True)
    assert state["authority"] == "EXEC-X03 validation-reference-state"
    assert state["exo_run_ref"] == "exo:run-1"
    assert state["contract_ref"]["digest"] == session.resolved_validation_contract["digest"]
    assert all(row["node_definition_id"] and row["rer_fingerprint_digest"] for row in state["checks"])
    for forbidden in ('"argv"', '"stdout"', '"stderr"', '"heartbeat"', '"progress"', '"attempts"'):
        assert forbidden not in encoded


def test_required_optional_empty_and_applicability_semantics_are_one_evaluator() -> None:
    empty = ResolvedValidationContract((), CheckpointPolicy("finalize", ()))
    assert empty.evaluate(()).satisfied

    optional_only = ResolvedValidationContract((ValidationCheck("optional", "optional", required=False),), CheckpointPolicy("finalize", ()))
    assert optional_only.evaluate(()).satisfied

    required = ValidationCheck("required", "required", required=True)
    contract = ResolvedValidationContract((required,), CheckpointPolicy("finalize", ("required",)))
    assert not contract.evaluate((ValidationOutcome("required", ValidationOutcomeState.SKIPPED),)).satisfied

    conditional = ValidationCheck(
        "device", "device", required=True,
        applicability_policy=ApplicabilityPolicy.CONDITIONAL,
        evidence_requirements=("device-selection",),
    )
    conditional_contract = ResolvedValidationContract((conditional,), CheckpointPolicy("finalize", ("device",)))
    assert not conditional_contract.evaluate((ValidationOutcome("device", ValidationOutcomeState.NOT_APPLICABLE),)).satisfied
    assert conditional_contract.evaluate((
        ValidationOutcome("device", ValidationOutcomeState.NOT_APPLICABLE, applicability_evidence=("no-device",)),
    )).satisfied


def test_output_presentation_options_do_not_change_validation_contract(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    specs = [_spec("alpha", root, input_name="alpha.txt"), _spec("beta", root, input_name="beta.txt")]
    a = create_session(config, specs, options={"output":"human", "verbose":False}, replace_active=True)
    b = create_session(config, specs, options={"output":"json", "verbose":True, "copy":True}, replace_active=True)
    assert a.resolved_validation_contract["digest"] == b.resolved_validation_contract["digest"]
    assert [row.metadata["validation_check_id"] for row in stored_specs(a)] == [row.metadata["validation_check_id"] for row in stored_specs(b)]


def test_rer_planner_reuses_unchanged_node_and_invalidates_changed_node_only(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    original = bind_validation_specs_to_authority([
        _spec("alpha", root, input_name="alpha.txt"),
        _spec("beta", root, input_name="beta.txt"),
    ], repo_root=root)
    history = ReuseHistoryIndex()
    history.add(_candidate_for(original[0], run_id="run-a", node_id="node-a"))
    history.add(_candidate_for(original[1], run_id="run-b", node_id="node-b"))

    (root / "beta.txt").write_text("beta-v2\n", encoding="utf-8")
    current = bind_validation_specs_to_authority([
        _spec("alpha", root, input_name="alpha.txt"),
        _spec("beta", root, input_name="beta.txt"),
    ], repo_root=root)
    plan = ReusePlanner(history_index=history, repo_root=root).plan_specs(current)
    by_name = {entry.spec_name: entry for entry in plan.entries}
    assert by_name["alpha"].disposition == "reuse"
    assert by_name["alpha"].source_run_id == "run-a"
    assert by_name["beta"].disposition == "invalidated"
    assert "input_manifest_digest" in by_name["beta"].reason


def test_semantic_controls_are_revision_guarded_and_do_not_create_lifecycle_transitions(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(config, [
        _spec("alpha", root, input_name="alpha.txt"),
        _spec("beta", root, input_name="beta.txt", deps=("alpha",)),
    ], replace_active=True)
    intent = semantic_control_intent("run", scope="through", through="finalize", expected_revision=7)
    assert intent.scope == "through"
    assert intent.through == "finalize"
    assert intent.expected_revision == 7
    keys = select_phase_keys_for_control(session, action="until-checkpoint", scope="through", through="finalize")
    assert keys == [phase.key for phase in session.phases]
    assert session.status == "ready"


def test_finalize_checkpoint_is_administrative_verdict_not_fake_executable(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(config, [_spec("alpha", root, input_name="alpha.txt")], apply_context={"minimum_phase":"finalize"}, replace_active=True)
    assert not any(phase.name == "finalize" for phase in session.phases)
    record_phase_result(session, session.phases[0].key, OperationResult(session.phases[0].spec_name, ["ok"], 0, 0.01, "ok", []))
    assert required_checkpoint_satisfied(session)
    assert session.validation_verdict["satisfied"] is True
    assert session.complete


def test_bounded_exo_projection_reads_history_without_copying_event_truth(tmp_path: Path) -> None:
    root = tmp_path / "exo"
    for idx in range(3):
        run = root / f"run-{idx}"
        run.mkdir(parents=True)
        (run / "execution.json").write_text(json.dumps({
            "execution": {"run_id": f"run-{idx}", "status": "succeeded", "nodes": [{"id":"n"}]},
            "events": [{"kind":"started"}],
        }), encoding="utf-8")
    projection = exo_projection_from_directory(root, limit=2)
    assert projection["source"] == "exo-run-history"
    assert len(projection["runs"]) == 2
    assert all("events" not in row for row in projection["runs"])
    assert projection["runs"][-1]["status"] == "succeeded"


def test_workflow_only_artifact_validation_ref_is_counted_as_executed_check(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    manifest = {"target":"", "validation":{"refs":["workflow:gate"], "expected":{"tests":"passed"}, "maximum_warnings":0}}
    assert artifact_validation_refs(manifest)[0]["ref"] == "workflow:gate"
    results, raw = run_artifact_tests(root, manifest, verbose=False, dry_run=False)
    assert len(results) == 1
    assert results[0].returncode == 0
    assert results[0].metadata["artifact_validation_workflow"] == "gate"
    assert "ok" in raw
