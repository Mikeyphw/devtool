from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from devtool.artifacts import operation_history_entries_dir
from devtool.config import load_project_config
from devtool.core.events import EventBus
from devtool.federation_execution import run_federated_workflow
from devtool.federation_git_lineage import (
    FederationGitLineageError,
    direct_federated_git_lineage,
    finalize_federated_git_lineage,
    planned_federated_git_lineage,
)
from devtool.federation_plan import build_federated_plan
from devtool.invocation_origin import invocation_metadata_for_args
from devtool.transactions import transaction_root


def _git(root: Path) -> str:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Devtool Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(
            root,
            root / ".devtool.toml",
            target_name="",
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _fixture(tmp_path: Path, *, command: str = "ok", git: bool = True):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(parents=True); child.mkdir()
    if command == "commit":
        argv = '["git","commit","--allow-empty","-m","child-result"]'
    elif command == "cancel":
        argv = '["python","-c","import sys; sys.exit(130)"]'
    else:
        argv = '["python","-c","print(\'ok\')"]'
    (child / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'
        f'[targets.app.jobs.run]\nrunner="command"\ncommand={argv}\n',
        encoding="utf-8",
    )
    (child / "tracked.txt").write_text("base\n", encoding="utf-8")
    start = _git(child) if git else ""
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'
        '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n'
        '[federation.workflows.release]\nsteps=[{id="child",ref="child::app::job:run"}]\n',
        encoding="utf-8",
    )
    return _load(parent), child, start


def _ref(root: Path, name: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "--quiet", name], text=True, capture_output=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def test_planned_lineage_is_derived_from_frozen_source_lock(tmp_path: Path) -> None:
    config, _child, start = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    record = planned_federated_git_lineage(
        source_lock=plan.source_lock,
        repository="child",
        parent_run_id="parent-run",
        parent_node_id="federation:release:child",
        child_run_id="child-run",
        target="app",
        contract_type="job",
        contract_name="job:run",
        contract_ref="child::app::job:run",
        federation_workflow="release",
        federation_step="child",
    )
    assert record["state"] == "planned"
    assert record["start_commit"] == start
    assert record["repository_id"]
    assert record["frozen_plan_identity"] == plan.source_lock["lock_digest"]
    assert record["frozen_repository_source_identity"]
    assert "{federation_run_id}" in record["expected_result_ref"]


def test_child_invocation_metadata_persists_planned_git_lineage() -> None:
    env = {
        "DEVTOOL_FEDERATION_PARENT_RUN_ID": "parent",
        "DEVTOOL_FEDERATION_PARENT_NODE_ID": "node",
        "DEVTOOL_FEDERATION_REPOSITORY": "repo",
        "DEVTOOL_FEDERATION_TARGET": "app",
        "DEVTOOL_FEDERATION_CONTRACT_REF": "repo::app::job:lint",
        "DEVTOOL_FEDERATION_GIT_REPOSITORY_ID": "repo-id",
        "DEVTOOL_FEDERATION_GIT_START_COMMIT": "abc123",
        "DEVTOOL_FEDERATION_GIT_EXPECTED_RESULT_REF": "refs/devtool/federations/{federation_run_id}/repos/repo/result",
        "DEVTOOL_FEDERATION_FROZEN_PLAN_IDENTITY": "lock-id",
        "DEVTOOL_FEDERATION_FROZEN_REPOSITORY_SOURCE_IDENTITY": "source-id",
        "DEVTOOL_RUN_ID": "child",
    }
    from devtool.invocation_origin import trusted_invocation_context
    with mock.patch.dict(os.environ, env, clear=False):
        # Parent-shell variables alone are untrusted, and must not create lineage.
        assert "run_lineage" not in invocation_metadata_for_args(SimpleNamespace())
        with trusted_invocation_context(source="federation", command="build"):
            metadata = invocation_metadata_for_args(SimpleNamespace())
    lineage = metadata["run_lineage"]
    assert lineage["parent_run_id"] == "parent"
    assert lineage["git_lineage"]["child_run_id"] == "child"
    assert lineage["git_lineage"]["repository_id"] == "repo-id"
    assert lineage["git_lineage"]["start_commit"] == "abc123"
    assert lineage["git_lineage"]["frozen_plan_identity"] == "lock-id"


def test_successful_child_records_final_git_lineage_in_child_and_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, start = _fixture(tmp_path / "fixture", command="commit")
    bus = EventBus()
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: (_ for _ in ()).throw(AssertionError("job contract should use bridge")),
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="git-lineage-success",
    )
    assert report.ok
    outcome = report.children[0]
    lineage = outcome.git_lineage
    assert lineage["state"] == "finalized"
    assert lineage["parent_run_id"] == report.parent_run_id
    assert lineage["parent_node_id"] == outcome.parent_node_id
    assert lineage["child_run_id"] == outcome.run_id
    assert lineage["start_commit"] == start
    assert lineage["result_commit"] and lineage["result_commit"] != start
    assert lineage["terminal_status"] == "succeeded"
    assert _ref(child, lineage["result_ref"]) == lineage["result_commit"]
    assert _ref(child, lineage["rollback_ref"]) == start
    child_snapshot = bus.execution_store.snapshot(outcome.run_id)
    assert child_snapshot is not None
    assert child_snapshot.run_lineage["git_lineage"]["start_commit"] == start
    evidence = [a for a in child_snapshot.artifacts if a.artifact_type == "federation-git-lineage"]
    assert len(evidence) == 1
    assert Path(evidence[0].path).is_file()
    payload = json.loads(Path(evidence[0].path).read_text())
    assert payload["result_commit"] == lineage["result_commit"]
    parent_snapshot = bus.execution_store.snapshot(report.parent_run_id)
    node = parent_snapshot.node(outcome.parent_node_id)
    assert node is not None
    assert node.metadata["git_lineage"]["result_commit"] == lineage["result_commit"]


def test_cancelled_child_preserves_git_lineage_and_recovery_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, child, start = _fixture(tmp_path / "fixture", command="cancel")
    bus = EventBus()
    report = run_federated_workflow(
        config,
        "release",
        reenter=lambda _argv: 0,
        publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        parent_run_id="git-lineage-cancel",
    )
    assert report.status == "cancelled"
    outcome = report.children[0]
    assert outcome.status == "cancelled"
    assert outcome.git_lineage["terminal_status"] == "cancelled"
    assert outcome.git_lineage["start_commit"] == start
    assert outcome.git_lineage["result_commit"] == start
    assert _ref(child, outcome.git_lineage["rollback_ref"]) == start
    snapshot = bus.execution_store.snapshot(outcome.run_id)
    assert snapshot is not None
    assert any(a.artifact_type == "federation-git-lineage" for a in snapshot.artifacts)


def test_finalizer_collects_transaction_policy_overlay_and_supersession_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "file.txt").write_text("one\n")
    start = _git(repo)
    planned = direct_federated_git_lineage(
        repo, repository="repo", parent_run_id="parent", parent_node_id="node", child_run_id="child",
        target="app", contract_type="workflow", contract_name="validate", contract_ref="repo::app::validate", federation_step="step",
    )
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "result"], check=True)
    result = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    history = transaction_root(repo) / "history"; history.mkdir(parents=True)
    (history / "tx.json").write_text(json.dumps({
        "transaction_id": "tx", "run_id": "child", "status": "completed", "updated_at": "2026-01-01T00:00:00Z",
        "commit_policy": {"mode": "amend", "result_commit": result, "supersedes_commit": start},
        "git_recovery": {"commit_mode": "amend", "recovery": {"result_commit": result, "refs": []}, "finalization": {"state": "completed"}},
    }))
    opdir = operation_history_entries_dir(repo); opdir.mkdir(parents=True, exist_ok=True)
    opdir.joinpath("entry.json").write_text(json.dumps({"history_id": "h", "operation_id": "child", "sha256": "a" * 64}))
    finalized = finalize_federated_git_lineage(repo, planned, terminal_status="succeeded")
    assert finalized["commit_mode"] == "amend"
    assert finalized["overlay_digests"] == ["a" * 64]
    assert finalized["supersession_pairs"] == [{"superseded": start, "result": result}]
    assert finalized["transactions"][0]["transaction_id"] == "tx"
    assert finalized["transactions"][0]["finalization_state"] == "completed"


def test_lineage_private_ref_collision_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "x").write_text("x")
    _git(repo)
    planned = direct_federated_git_lineage(
        repo, repository="repo", parent_run_id="same-parent", parent_node_id="node", child_run_id="child",
        target="", contract_type="direct", contract_name="exec", contract_ref="", federation_step="exec",
    )
    first = finalize_federated_git_lineage(repo, planned, terminal_status="succeeded")
    assert first["result_ref"]
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-qm", "later"], check=True)
    with pytest.raises(FederationGitLineageError, match="ref collision"):
        finalize_federated_git_lineage(repo, planned, terminal_status="failed")


def test_non_git_child_keeps_logical_lineage_without_fabricating_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    repo = tmp_path / "plain"; repo.mkdir(); (repo / "x").write_text("x")
    planned = direct_federated_git_lineage(
        repo, repository="plain", parent_run_id="parent", parent_node_id="node", child_run_id="child",
        target="app", contract_type="job", contract_name="job:x", contract_ref="plain::app::job:x", federation_step="x",
    )
    finalized = finalize_federated_git_lineage(repo, planned, terminal_status="succeeded")
    assert finalized["state"] == "finalized"
    assert finalized["repository"] == "plain"
    assert finalized["repository_id"] == ""
    assert finalized["start_commit"] == finalized["result_commit"] == ""
    assert finalized["result_ref"] == finalized["rollback_ref"] == ""


def test_parent_report_serializes_final_git_lineage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config, _child, start = _fixture(tmp_path / "fixture")
    bus = EventBus()
    report = run_federated_workflow(config, "release", reenter=lambda _argv: 0, publish=bus.publish, snapshot_lookup=bus.execution_store.snapshot)
    payload = report.as_dict()
    assert payload["children"][0]["git_lineage"]["start_commit"] == start
    assert payload["children"][0]["git_lineage"]["terminal_status"] == "succeeded"


def test_lineage_environment_is_path_independent_and_contains_no_child_root(tmp_path: Path) -> None:
    config, child, _start = _fixture(tmp_path)
    plan = build_federated_plan(config, "release")
    record = planned_federated_git_lineage(
        source_lock=plan.source_lock, repository="child", parent_run_id="parent", parent_node_id="node", child_run_id="child-run",
        target="app", contract_type="job", contract_name="job:run", contract_ref="child::app::job:run", federation_workflow="release", federation_step="child",
    )
    rendered = json.dumps(record, sort_keys=True)
    assert str(child.resolve()) not in rendered
    assert record["frozen_plan_identity"]
