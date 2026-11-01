from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from devtool.models import CommandSpec, OperationResult, ProjectConfig, WorkspaceConfig, WorkflowSchedulerConfig
from devtool.workflow_scheduler import WorkflowScheduleError, run_workflow_specs
from devtool.workflow_write_audit import WriteAuditError, snapshot, verify_node


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "allowed.txt").write_text("first\n")
    (root / "src" / "protected.txt").write_text("secret-free fixture\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "audit@example.invalid")
    _git(root, "config", "user.name", "Audit Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    return root


def _config(root: Path) -> ProjectConfig:
    return ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root),
                         workspace=WorkspaceConfig(scheduler=WorkflowSchedulerConfig(parallel=False, max_parallel_nodes=2)))


def _spec(key: str, *, writes: tuple[str, ...] = ("src/allowed.txt",), read_only: bool = False,
          dependencies: tuple[str, ...] = ()) -> CommandSpec:
    return CommandSpec(key, ["true"], metadata={
        "workflow_node_key": key, "workflow_target": "app", "workflow_name": "build",
        "workflow_depends_on": list(dependencies), "workflow_read_only": read_only,
        "workflow_write_scope_known": True,
        "workflow_write_scopes": list(writes),
    })


def _run(monkeypatch: pytest.MonkeyPatch, root: Path, specs: list[CommandSpec], writes: dict[str, tuple[str, str]],
         *, parallel: bool = False, mode: str = "enforce"):
    import devtool.runners as runners
    launched: list[str] = []
    def fake_run_specs(config, current, **kwargs):
        key = current[0].metadata["workflow_node_key"]
        if kwargs.get("workflow_initial_fail_fast") or kwargs.get("workflow_initial_cancelled") or any(
            kwargs.get("workflow_initial_statuses", {}).get(dep) != "passed"
            for dep in current[0].metadata.get("workflow_depends_on", [])
        ):
            return [OperationResult(key, [], 0, .0, "blocked", [], metadata={"workflow_node_key": key,
                "workflow_node_state": "blocked"})], "blocked"
        launched.append(key)
        if key in writes:
            filename, contents = writes[key]
            (root / filename).write_text(contents)
        return [OperationResult(key, ["true"], 0, .02, "ok", [], metadata={"workflow_node_key": key})], "ok"
    monkeypatch.setattr(runners, "run_specs", fake_run_specs)
    results, _, plan = run_workflow_specs(_config(root), specs, verbose=False, dry_run=False,
        continue_on_error=False, log_path=None, parallel_requested=parallel, write_audit_mode=mode)
    return results, plan, launched


def test_allowed_tracked_output_passes_with_visible_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    results, plan, launched = _run(monkeypatch, root, [_spec("first")], {"first": ("src/allowed.txt", "modified\n")})
    assert launched == ["first"]
    assert results[0].returncode == 0
    proof = results[0].metadata["workflow_r38_tracked_write_audit"]
    assert proof["status"] == "pass"
    assert proof["observed"] == ["src/allowed.txt"]
    assert plan is not None


def test_outside_scoped_write_fails_and_blocks_successor(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    specs = [_spec("first"), _spec("second", dependencies=("first",))]
    results, plan, launched = _run(monkeypatch, root, specs, {"first": ("src/protected.txt", "bad\n")})
    assert "second" not in launched
    assert results[0].returncode != 0
    assert results[0].failure_kind == "workflow_r38_write_scope_violation"
    assert results[0].metadata["workflow_r38_tracked_write_audit"]["outside_scope"] == ["src/protected.txt"]
    assert plan is not None and dict(plan.node_outcomes)["first"] == "failed"


def test_serial_followup_compares_changed_content_not_only_dirty_names(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    specs = [_spec("first"), _spec("second", writes=("src/protected.txt",), dependencies=("first",))]
    results, plan, launched = _run(monkeypatch, root, specs, {
        "first": ("src/allowed.txt", "modified1\n"),
        "second": ("src/allowed.txt", "modified2\n"),
    })
    assert launched == ["first", "second"]
    assert results[0].returncode == 0
    assert results[1].returncode != 0
    assert results[1].metadata["workflow_r38_tracked_write_audit"]["outside_scope"] == ["src/allowed.txt"]


def test_read_only_node_cannot_change_tracked_file(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    res, _, _ = _run(monkeypatch, root, [_spec("readonly", writes=(), read_only=True)],
                     {"readonly": ("src/protected.txt", "change\n")})
    assert res[0].returncode == 1


def test_unqualified_scope_refused(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    spec = _spec("a")
    spec.metadata["workflow_write_scope_known"] = False
    res, _, _ = _run(monkeypatch, root, [spec], {})
    assert res[0].metadata["workflow_r38_tracked_write_audit"]["code"] == "R38_UNKNOWN_WRITE_SCOPE"


def test_parallel_requested_refused_before_any_spawn(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    with pytest.raises(WorkflowScheduleError, match="R38_WRITE_AUDIT_REQUIRES_SERIAL_WORKFLOW"):
        _run(monkeypatch, root, [_spec("one"), _spec("two")], {}, parallel=True)


def test_dirty_tracked_baseline_refused(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "src/protected.txt").write_text("already dirty")
    with pytest.raises(WorkflowScheduleError, match="R38_WRITE_AUDIT_REQUIRES_CLEAN_TRACKED_BASELINE"):
        _run(monkeypatch, root, [_spec("a")], {})


def test_untracked_changes_not_misrepresented_as_proven(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    (root / "untracked.txt").write_text("not covered")
    res, _, _ = _run(monkeypatch, root, [_spec("a")], {})
    proof = res[0].metadata["workflow_r38_tracked_write_audit"]
    assert proof["status"] == "pass"
    assert "git-tracked" in proof["limits"]
    assert "untracked.txt" not in proof["observed"]


def test_git_head_mutation_is_detected(tmp_path):
    root = _repo(tmp_path)
    before = snapshot(root)
    _git(root, "commit", "--allow-empty", "-qm", "mutated head")
    after = snapshot(root)
    proof = verify_node(before, after, node_key="n", writes=("src/allowed.txt",), writes_known=True, read_only=False)
    assert proof["code"] == "R38_SOURCE_HEAD_CHANGED"


def test_non_git_or_nested_root_fails_closed(tmp_path):
    with pytest.raises(WriteAuditError, match="R38_GIT_PROOF_UNAVAILABLE"):
        snapshot(tmp_path)
    (tmp_path / "nested").mkdir()
    root = _repo(tmp_path / "nested")
    with pytest.raises(WriteAuditError, match="R38_REPOSITORY_SCOPE_MISMATCH"):
        snapshot(root / "src")


def test_symlink_and_large_tracked_changed_outputs_fail_closed(tmp_path):
    root = _repo(tmp_path)
    changed = root / "src/allowed.txt"
    changed.unlink()
    changed.symlink_to("protected.txt")
    snap = snapshot(root)
    assert snap.files()["src/allowed.txt"].startswith("symlink:")
    changed.unlink()
    changed.write_bytes(b"Z" * (32 * 1024 * 1024 + 1))
    with pytest.raises(WriteAuditError, match="R38_UNSUPPORTED_FILE_TYPE_OR_SIZE"):
        snapshot(root)


def test_invalid_env_mode_rejected_before_spawn(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.setenv("DEVTOOL_R38_WRITE_AUDIT", "probably")
    with pytest.raises(WorkflowScheduleError, match="R38_INVALID_WRITE_AUDIT_MODE"):
        _run(monkeypatch, root, [_spec("a")], {}, mode=None)


def test_disabled_audit_preserves_legacy_execution(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    res, _, launched = _run(monkeypatch, root, [_spec("a")], {"a": ("src/protected.txt", "changed")}, mode="off")
    assert res[0].returncode == 0
    assert "workflow_r38_tracked_write_audit" not in res[0].metadata
    assert launched == ["a"]


def test_native_runner_propagates_write_refusal_before_terminal_events(tmp_path):
    import sys
    root = _repo(tmp_path)
    code = 'from pathlib import Path;Path("src/protected.txt").write_text("outside\\n")'
    spec = _spec("native")
    spec.argv = [sys.executable, "-c", code]
    spec.cwd = str(root)
    results, raw, plan = run_workflow_specs(_config(root), [spec], verbose=False, dry_run=False,
        continue_on_error=False, log_path=None, parallel_requested=False, write_audit_mode="enforce")
    assert results[0].returncode != 0
    assert results[0].metadata["workflow_r38_tracked_write_audit"]["code"] == "R38_OUT_OF_SCOPE_TRACKED_WRITE"
    assert plan is not None


def test_enforce_mode_refuses_clean_checkout_without_known_write_scope(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    bad = _spec("n", writes=())
    bad.metadata["workflow_write_scope_known"] = False
    results, _, _ = _run(monkeypatch, root, [bad], {})
    assert results[0].returncode != 0
    assert results[0].metadata["workflow_r38_tracked_write_audit"]["status"] == "fail"


def test_audit_enforces_single_live_worker_even_for_independent_nodes(tmp_path, monkeypatch):
    import threading
    import time
    import devtool.runners as runners
    root = _repo(tmp_path)
    lock = threading.Lock()
    active = 0
    peak = 0
    def fake_run_specs(config, current, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.08)
        with lock:
            active -= 1
        name = current[0].metadata["workflow_node_key"]
        return [OperationResult(name, [], 0, .01, "", [], metadata={})], ""
    monkeypatch.setattr(runners, "run_specs", fake_run_specs)
    results, _, plan = run_workflow_specs(_config(root), [_spec("one"), _spec("two")],
        verbose=False, dry_run=False, continue_on_error=False, log_path=None,
        parallel_requested=False, write_audit_mode="enforce")
    assert peak == 1
    assert plan is not None and plan.max_parallel_nodes == 1
    assert all(r.returncode == 0 for r in results)
