from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from devtool.workflow_scheduler import run_workflow_specs
from devtool.workflow_write_audit import (
    WriteAuditError, attest_node_transition, snapshot,
)


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "allowed.txt").write_text("old\n")
    (root / "protected.txt").write_text("protected\n")
    for args in (("init", "-q"), ("config", "user.email", "test@example.invalid"),
                 ("config", "user.name", "Test"), ("add", "."),
                 ("commit", "-qm", "fixture")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


def _config(root):
    return ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root),
                         workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=False)))


def _spec():
    return CommandSpec("node", ["true"], metadata={
        "workflow_node_key": "node", "workflow_target": "r39", "workflow_name": "audit",
        "workflow_graph": True, "workflow_read_only": False, "workflow_depends_on": [],
        "workflow_write_scope_known": True, "workflow_write_scopes": ["allowed.txt"],
    })


def test_single_attestation_accepts_allowed_tracked_changes(tmp_path):
    root = _repo(tmp_path)
    before = snapshot(root)
    (root / "allowed.txt").write_text("new\n")
    after, proof = attest_node_transition(root, before, node_key="node", writes=("allowed.txt",),
                                           writes_known=True, read_only=False)
    assert proof["status"] == "pass" and proof["observed"] == ["allowed.txt"]
    assert after != before


def test_single_attestation_refuses_out_of_scope_changes(tmp_path):
    root = _repo(tmp_path)
    before = snapshot(root)
    (root / "protected.txt").write_text("mutated\n")
    _, proof = attest_node_transition(root, before, node_key="node", writes=("allowed.txt",),
                                       writes_known=True, read_only=False)
    assert proof["status"] == "fail"
    assert proof["code"] == "R38_OUT_OF_SCOPE_TRACKED_WRITE"
    assert proof["outside_scope"] == ["protected.txt"]


def test_single_attestation_does_not_advance_on_snapshot_failure(tmp_path, monkeypatch):
    import devtool.workflow_write_audit as mod
    root = _repo(tmp_path)
    before = snapshot(root)
    def fail(_root):
        raise WriteAuditError("R38_GIT_PROOF_UNAVAILABLE")
    monkeypatch.setattr(mod, "snapshot", fail)
    after, proof = attest_node_transition(root, before, node_key="node", writes=("allowed.txt",),
                                           writes_known=True, read_only=False)
    assert after is before
    assert proof == {"status": "fail", "code": "R38_GIT_PROOF_UNAVAILABLE",
                     "node": "node", "observed": [], "outside_scope": []}


@pytest.mark.parametrize("hook, mutation, expected", [
    (True, "allowed.txt", "pass"),
    (True, "protected.txt", "fail"),
    (False, "allowed.txt", "pass"),
    (False, "protected.txt", "fail"),
])
def test_scheduler_hook_and_fallback_use_same_attestation(tmp_path, monkeypatch, hook, mutation, expected):
    import devtool.runners as runners
    import devtool.workflow_write_audit as mod
    root = _repo(tmp_path)
    calls = []
    native = mod.attest_node_transition
    def count(*args, **kwargs):
        calls.append(kwargs["node_key"])
        return native(*args, **kwargs)
    monkeypatch.setattr(mod, "attest_node_transition", count)

    def fake_run_specs(config, specs, **kwargs):
        (root / mutation).write_text("mutated\n")
        metadata = {**specs[0].metadata, "workflow_node_state": "passed"}
        rc, kind = 0, ""
        if hook:
            rc, kind, metadata = kwargs["r38_terminal_hook"](rc, kind, metadata)
        return [OperationResult("node", ["true"], rc, 0.01, "output", [],
                                failure_kind=kind, metadata=metadata)], "output"
    monkeypatch.setattr(runners, "run_specs", fake_run_specs)
    results, _, plan = run_workflow_specs(_config(root), [_spec()], verbose=False,
        dry_run=False, continue_on_error=False, log_path=None, parallel_requested=False,
        write_audit_mode="enforce")
    assert calls == ["node"]
    proof = results[0].metadata["workflow_r38_tracked_write_audit"]
    assert proof["status"] == expected
    assert (results[0].returncode != 0) == (expected == "fail")
    assert (plan is not None)


def test_legacy_runner_remains_supported(tmp_path):
    from devtool.runners import run_specs
    root = tmp_path / "plain"
    root.mkdir()
    result, raw = run_specs(_config(root), [CommandSpec("noop", ["true"], cwd=str(root))],
                            verbose=False, dry_run=False, continue_on_error=False, log_path=None)
    assert len(result) == 1 and result[0].returncode == 0
