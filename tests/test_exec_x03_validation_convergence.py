from __future__ import annotations

import json
from pathlib import Path

from devtool.artifact_tests import run_artifact_tests
from devtool.artifacts import artifact_validation_tests, artifact_validation_refs
from devtool.config import load_project_config
from devtool.models import CommandSpec, OperationResult
from devtool.validation_authority import (
    bind_validation_specs_to_authority,
    build_private_state,
    private_session_state_path,
    validation_reuse_plan,
)
from devtool.validation_sessions import (
    create_session,
    record_phase_result,
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
    (root / "tracked.py").write_text("print('one')\n", encoding="utf-8")
    return root


def _spec(name: str, root: Path, *, phase: str, key: str = "", deps=(), required=True) -> CommandSpec:
    return CommandSpec(
        name,
        ["python", "-c", "print('" + name + "')"],
        cwd=str(root),
        metadata={
            "validation_phase_name": phase,
            "validation_phase_key": key or f"repository:{phase}:1",
            "validation_depends_on": list(deps),
            "validation_required": required,
            "workflow_inputs": ["tracked.py"],
        },
    )


def test_bound_validation_specs_are_pinned_with_rer_node_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    spec = _spec("compile", root, phase="compile")
    bound = bind_validation_specs_to_authority([spec], repo_root=root)
    assert bound[0].metadata["validation_authority"] == "exo-rer-run"
    assert bound[0].metadata["validation_node_definition_id"]
    assert bound[0].metadata["validation_rer_fingerprint"]["digest"]
    plan = validation_reuse_plan(bound, repo_root=root).to_dict()
    assert plan["entries"][0]["node_definition_id"] == bound[0].metadata["validation_node_definition_id"]
    assert plan["entries"][0]["disposition"] in {"run", "invalidated"}


def test_create_session_persists_bound_plan_private_reference_state(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(
        config,
        [_spec("compile", root, phase="compile"), _spec("unit", root, phase="unit-test", deps=("compile",))],
        replace_active=True,
    )
    stored = stored_specs(session)
    assert all(spec.metadata.get("validation_node_definition_id") for spec in stored)
    assert all(spec.metadata.get("validation_rer_fingerprint", {}).get("digest") for spec in stored)
    state_path = private_session_state_path(root, session.session_id)
    assert state_path.is_file()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["authority"] == "EXEC-X03 validation-reference-state"
    assert state["contract_ref"]["digest"] == session.resolved_validation_contract["digest"]
    assert state["checkpoint_ref"]["required_keys"] == session.required_checkpoint_keys
    assert state["checks"][0]["node_definition_id"]


def test_semantic_control_until_finalize_uses_required_checkpoints_not_fake_finalize(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(
        config,
        [_spec("compile", root, phase="compile"), _spec("unit", root, phase="unit-test", deps=("compile",))],
        apply_context={"minimum_phase":"finalize"},
        replace_active=True,
    )
    keys = select_phase_keys_for_control(session, action="until-checkpoint", scope="through", through="finalize")
    assert keys == [phase.key for phase in session.phases]
    first = select_phase_keys_for_control(session, action="next", scope="next")
    assert first == [session.phases[0].key]


def test_contract_satisfaction_does_not_need_synthetic_finalize_phase(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config = load_project_config(str(root), None, allow_ambiguous_target=True)
    session = create_session(
        config,
        [_spec("compile", root, phase="compile"), _spec("unit", root, phase="unit-test", deps=("compile",))],
        apply_context={"minimum_phase":"finalize"},
        replace_active=True,
    )
    for phase in session.phases:
        record_phase_result(session, phase.key, OperationResult(phase.spec_name, ["ok"], 0, 0.01, "ok", []))
    assert session.status == "validated"
    assert session.complete
    assert not any(phase.name == "finalize" for phase in session.phases)
    state = build_private_state(session)
    assert state["verdict_ref"]["satisfied"] is True


def test_workflow_only_validation_refs_count_as_artifact_tests(tmp_path: Path, monkeypatch) -> None:
    root = _repo(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    manifest = {"target":"", "validation":{"refs":["workflow:gate"], "expected":{"tests":"passed"}, "maximum_warnings":0}}
    assert artifact_validation_tests(manifest) == []
    assert artifact_validation_refs(manifest)[0]["ref"] == "workflow:gate"
    results, raw = run_artifact_tests(root, manifest, verbose=False, dry_run=False)
    assert results
    assert all(result.returncode == 0 for result in results)
    assert any((result.metadata or {}).get("artifact_validation_workflow") == "gate" for result in results)
    assert "ok" in raw
