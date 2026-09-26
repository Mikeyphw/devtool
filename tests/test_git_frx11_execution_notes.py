from __future__ import annotations

import json
import subprocess
from pathlib import Path

from devtool.config import load_project_config
from devtool.federation_execution import run_federated_group, run_federated_workflow
from devtool.git_execution_notes import DEVTOOL_NOTES_REF, append_execution_note, read_execution_note


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=10)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _repo(root: Path, *, configure_identity: bool = True) -> str:
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    if configure_identity:
        _git(root, "config", "user.email", "fixture@example.invalid")
        _git(root, "config", "user.name", "Fixture")
    (root / "source.txt").write_text("base\n", encoding="utf-8")
    _git(root, "add", "source.txt")
    if not configure_identity:
        env = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
        proc = subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], env={**__import__('os').environ, **env}, text=True, capture_output=True, check=False)
        assert proc.returncode == 0, proc.stderr
    else:
        _git(root, "commit", "-qm", "base")
    return _git(root, "rev-parse", "HEAD")


def test_execution_notes_append_without_requiring_user_git_identity(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    commit = _repo(root, configure_identity=False)
    first = append_execution_note(root, commit, {"parent_run_id": "p1", "child_run_id": "c1", "repository": "child", "status": "succeeded"})
    second = append_execution_note(root, commit, {"parent_run_id": "p2", "child_run_id": "c2", "repository": "child", "status": "succeeded"})
    payload = read_execution_note(root, commit)
    assert first["notes_ref"] == DEVTOOL_NOTES_REF
    assert second["records"] == 2
    assert [row["parent_run_id"] for row in payload["records"]] == ["p1", "p2"]
    assert all(len(row["evidence_digest"]) == 64 for row in payload["records"])


def test_structured_notes_preserve_preexisting_legacy_devtool_note(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    commit = _repo(root)
    subprocess.run(
        ["git", "-C", str(root), "notes", f"--ref={DEVTOOL_NOTES_REF}", "add", "-m", "legacy provenance", commit],
        check=True, timeout=10,
    )
    append_execution_note(root, commit, {"parent_run_id": "p", "child_run_id": "c", "repository": "child"})
    payload = read_execution_note(root, commit)
    assert payload["legacy_notes"] == ["legacy provenance"]
    assert len(payload["records"]) == 1


def test_federation_completion_writes_lock_capability_scheduler_and_reuse_note(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir(); _repo(child)
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\nmetadata={read_only=true}\n[targets.app.workflows]\nvalidate=["job:check"]\n''',
        encoding="utf-8",
    )
    _git(child, "add", ".devtool.toml"); _git(child, "commit", "-qm", "config")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.repositories.child.exports]\ncapabilities=["api/v1"]\n[federation.repositories.child.requires]\ncapabilities=[{name="runtime/v1",optional=true}]\n[federation.workflows.verify]\nsteps=[{id="child",ref="child::app::validate"}]\n''',
        encoding="utf-8",
    )
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    report = run_federated_workflow(config, "verify", reenter=lambda _argv: 0, parent_run_id="parent-note")
    assert report.ok
    assert report.lifecycle["git_execution_notes"]
    commit = report.children[0].git_lineage["result_commit"]
    note = read_execution_note(child, commit)
    record = note["records"][-1]
    assert record["frozen_lock_identity"] == report.plan.source_lock["lock_digest"]
    assert record["capabilities"]["exports"] == ["api/v1"]
    assert record["capabilities"]["requires"][0]["name"] == "runtime/v1"
    assert record["scheduler"]["placement"]["step_id"] == "child"
    assert record["resume_reuse"]["child_lifecycle"] in {"executed", ""}
    assert record["git_lineage"]["result_commit"] == commit


def test_note_payload_is_valid_json_in_dedicated_git_ref(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    commit = _repo(root)
    append_execution_note(root, commit, {"parent_run_id": "p", "child_run_id": "c", "repository": "r"})
    raw = _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "show", commit)
    payload = json.loads(raw)
    assert payload["kind"] == "devtool.execution-note"
    assert payload["commit"] == commit


def test_execution_notes_are_append_only_and_duplicate_write_is_idempotent(tmp_path: Path) -> None:
    from devtool.git_execution_notes import GitExecutionNoteError
    import pytest
    root = tmp_path / "repo"
    commit = _repo(root)
    evidence = {"record_id": "stable", "parent_run_id": "p", "child_run_id": "c", "repository": "r", "status": "succeeded"}
    first = append_execution_note(root, commit, evidence)
    before_ref = _git(root, "rev-parse", DEVTOOL_NOTES_REF)
    second = append_execution_note(root, commit, evidence)
    assert second["idempotent"] is True
    assert second["notes_ref_after"] == before_ref
    assert len(read_execution_note(root, commit)["records"]) == 1
    changed = dict(evidence); changed["status"] = "failed"
    with pytest.raises(GitExecutionNoteError, match="Append-only"):
        append_execution_note(root, commit, changed)
    assert _git(root, "rev-parse", DEVTOOL_NOTES_REF) == before_ref


def test_federation_note_exposes_capability_and_interface_identity_digests(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); _repo(child)
    (child / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["python","-c","print('ok')"]\nmetadata={read_only=true}\n[targets.app.workflows]\nvalidate=["job:check"]\n''', encoding="utf-8")
    _git(child, "add", ".devtool.toml"); _git(child, "commit", "-qm", "config")
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.repositories.child.exports]\ncapabilities=["api/v1"]\n[federation.interfaces.release]\nrequired_workflows=["validate"]\n[federation.groups.ship]\nmembers=["workflow:verify"]\ninterface="release"\n[federation.workflows.verify]\nsteps=[{id="child",ref="child::app::validate"}]\n''', encoding="utf-8")
    config = load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)
    report = run_federated_group(config, "ship", reenter=lambda _argv: 0, parent_run_id="parent-identities")
    note = read_execution_note(child, report.children[0].git_lineage["result_commit"])
    record = note["records"][-1]
    assert len(record["capabilities"]["digest"]) == 64
    assert len(record["interface"]["digest"]) == 64
    assert record["interface"]["name"] == "release"
