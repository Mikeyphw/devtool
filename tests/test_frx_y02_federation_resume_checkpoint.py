from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from devtool.config import load_project_config
from devtool.federation_execution import FederatedChildRun, FederatedRunReport, run_federated_workflow
from devtool.federation_plan import build_federated_plan
from devtool.federation_resume import (
    FederationResumeError,
    build_resume_plan,
    checkpoint_report,
    child_from_checkpoint,
    load_checkpoint,
)


def _git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True, timeout=10)


def _fixture(tmp_path: Path):
    parent = tmp_path / "parent"
    a = tmp_path / "a"
    b = tmp_path / "b"
    parent.mkdir(); a.mkdir(); b.mkdir()
    child_config = '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\nmetadata={read_only=true}\n[targets.app.workflows]\nvalidate=["job:check"]\n'''
    for root in (a, b):
        (root / ".devtool.toml").write_text(child_config, encoding="utf-8")
        (root / "source.txt").write_text("base\n", encoding="utf-8")
        _git(root)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.repositories.a.exports]\ncapabilities=["contracts/v1"]\n[federation.repositories.b]\npath="../b"\n[federation.repositories.b.requires]\ncapabilities=["contracts/v1"]\n[federation.workflows.release]\nsteps=[{id="a",ref="a::app::workflow:validate"},{id="b",ref="b::app::workflow:validate",depends_on=["a"]}]\n''',
        encoding="utf-8",
    )
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    return config, a, b


def _children(config, plan):
    rows = []
    for step in config.federation.workflows["release"].steps:
        node = next(n for n in plan.nodes if not n.synthetic and n.metadata.get("federation_step") == step.step_id)
        identity = dict(node.metadata.get("repository_identity") or {})
        rows.append(FederatedChildRun(
            step_id=step.step_id, repository=step.repository, workflow="validate", target="app",
            run_id=f"child-{step.step_id}", parent_node_id=f"federation:release:{step.step_id}",
            status="succeeded", exit_code=0, repository_identity=identity,
            run_lineage={"parent_run_id": "parent-1"}, operational_ownership={"references": []},
            federation_ref=f"{step.repository}::app::validate", requested_ref=step.ref,
            child_contract="workflow:validate", contract_ref=f"{step.repository}::app::workflow:validate",
            git_lineage={}, lifecycle="executed",
        ))
    return tuple(rows)


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: root)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: root)


def test_checkpoint_reuses_successful_children_when_all_predicates_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    report = FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan)
    checkpoint_report(config, report)
    loaded = load_checkpoint(config, "parent-1")
    resume = build_resume_plan(config, loaded, mode="resume")
    assert [row.action for row in resume.decisions] == ["reused", "reused"]


def test_checkpoint_references_remote_history_without_copying_ssh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    payload = checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    forbidden = {
        "stdout", "stderr", "stdout_path", "stderr_path", "tmux", "tmux_session",
        "ssh_phase", "ssh_history", "lease", "lease_path", "return_state", "return_transfer",
    }
    for child in payload["children"]:
        assert forbidden.isdisjoint(child)
        assert child["run_id"].startswith("child-")
        assert child["evidence_digest"]


def test_source_drift_invalidates_changed_child_and_downstream_result_dependency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    (a / "source.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(a), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(a), "commit", "-qm", "drift"], check=True, timeout=10)
    config2 = load_project_config(config.repo_root, config.repo_root / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    resume = build_resume_plan(config2, load_checkpoint(config2, "parent-1"), mode="retry")
    assert resume.decisions[0].action == "rerun_due_to_drift"
    assert "frozen Git/tree/config state changed" in resume.decisions[0].reasons
    assert resume.decisions[1].action == "rerun_due_to_drift"
    assert any("upstream child result changed: a" == reason for reason in resume.decisions[1].reasons)


def test_failed_child_is_retried_and_invalidates_dependent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    checkpoint = checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    checkpoint["children"][0]["status"] = "failed"
    resume = build_resume_plan(config, checkpoint, mode="retry")
    assert resume.decisions[0].action == "retried"
    assert "prior child status is failed" in resume.decisions[0].reasons
    assert resume.decisions[1].action == "rerun_due_to_drift"


def test_missing_success_evidence_fails_reuse_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    checkpoint = checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    checkpoint["children"][0]["evidence_digest"] = ""
    resume = build_resume_plan(config, checkpoint, mode="resume")
    assert resume.decisions[0].action == "rerun_due_to_drift"
    assert "prior child evidence is unavailable" in resume.decisions[0].reasons


def test_retry_failed_fails_closed_when_successful_child_has_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    (a / "source.txt").write_text("drift\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(a), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(a), "commit", "-qm", "drift"], check=True, timeout=10)
    config2 = load_project_config(config.repo_root, config.repo_root / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    with pytest.raises(FederationResumeError, match="cannot be reused under --failed"):
        build_resume_plan(config2, load_checkpoint(config2, "parent-1"), mode="retry", failed_only=True)


def test_parent_dag_shape_change_requires_new_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    checkpoint_report(config, FederatedRunReport("release", "parent-1", "succeeded", 0, _children(config, plan), plan))
    path = config.repo_root / ".devtool.toml"
    text = path.read_text(encoding="utf-8").replace('depends_on=["a"]', 'depends_on=[]')
    path.write_text(text, encoding="utf-8")
    changed = load_project_config(config.repo_root, path, target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    with pytest.raises(FederationResumeError, match="parent plan identity changed"):
        build_resume_plan(changed, load_checkpoint(changed, "parent-1"), mode="resume")


def test_execution_can_reuse_prior_child_without_reentering_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _state(monkeypatch, tmp_path)
    config, _a, _b = _fixture(tmp_path)
    calls: list[list[str]] = []
    first = run_federated_workflow(config, "release", reenter=lambda argv: calls.append(list(argv)) or 0)
    assert first.ok and len(calls) == 2
    checkpoint = load_checkpoint(config, first.parent_run_id)
    resume_plan = build_resume_plan(config, checkpoint, mode="resume")
    assert all(row.action == "reused" for row in resume_plan.decisions)
    reuse = {row.step_id: child_from_checkpoint(row.prior_child) for row in resume_plan.decisions}
    second = run_federated_workflow(
        config, "release", reenter=lambda _argv: (_ for _ in ()).throw(AssertionError("reentered reused child")),
        reuse_children=reuse, lifecycle_actions={}, resumed_from=first.parent_run_id, resume_mode="resume",
    )
    assert second.ok
    assert [child.lifecycle for child in second.children] == ["reused", "reused"]
    assert second.lifecycle["counts"]["reused"] == 2


def test_cli_parser_exposes_resume_retry_and_failed_flag() -> None:
    from devtool.cli import build_parser
    parser = build_parser()
    resume = parser.parse_args(["federation", "resume", "run-1", "--json"])
    assert resume.federation_command == "resume" and resume.run_id == "run-1" and resume.json is True
    retry = parser.parse_args(["federation", "retry", "run-1", "--failed"])
    assert retry.federation_command == "retry" and retry.failed is True

    from devtool.completion import ZSH_COMPLETION
    assert "status summary plan run exec resume retry lock" in ZSH_COMPLETION
    assert "--failed[retry only failed children" in ZSH_COMPLETION
