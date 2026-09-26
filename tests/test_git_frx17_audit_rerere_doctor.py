from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.git_audit import audit_delta, audit_status, record_audit_pass
from devtool.git_doctor import federation_doctor, git_doctor
from devtool.git_rerere import (
    GitRerereError,
    apply_validated_resolution,
    capture_conflict_preimage,
    record_resolution,
    suggest_resolution,
)
from devtool.cli import main


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


def repo(path: Path, text: str = "base\n") -> str:
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "Devtool Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "file.txt").write_text(text, encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def parent_config(tmp_path: Path) -> tuple[Path, Path, object]:
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    repo(parent, "parent\n")
    repo(child, "child\n")
    (parent / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n'
        '[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\nrequired=true\n'
        '[federation.workflows.validate]\n'
        'steps=[{id="child",ref="child::app::workflow:validate"}]\n',
        encoding="utf-8",
    )
    (child / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
        'command=["python","-c","print(1)"]\nmetadata={read_only=true}\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n',
        encoding="utf-8",
    )
    git(parent, "add", ".devtool.toml"); git(parent, "commit", "-qm", "config")
    git(child, "add", ".devtool.toml"); git(child, "commit", "-qm", "config")
    return parent, child, load_project_config(str(parent), None, allow_ambiguous_target=True)


def conflict_repo(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "conflict"
    repo(root, "base\n")
    git(root, "checkout", "-qb", "side")
    (root / "file.txt").write_text("side\n", encoding="utf-8")
    git(root, "commit", "-am", "side", "-q")
    side = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "master")
    (root / "file.txt").write_text("main\n", encoding="utf-8")
    git(root, "commit", "-am", "main", "-q")
    proc = subprocess.run(["git", "-C", str(root), "merge", "side"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.returncode != 0
    preimage = capture_conflict_preimage(root, campaign_id="camp")
    return root, preimage


def test_audit_pass_delta_and_zero_gap_freshness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = parent_config(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    one = record_audit_pass(config, campaign_id="camp", roadmap_range="GIT-FRX-01..17", gap_count=0, pass_id="p1")
    status = audit_status(config, campaign_id="camp")
    assert status["zero_gap_fresh"] is True
    assert status["seal_eligible"] is True
    (child / "file.txt").write_text("changed\n", encoding="utf-8")
    status = audit_status(config, campaign_id="camp")
    assert status["zero_gap_fresh"] is False
    assert status["source_changes_since_zero_gap"][0]["repository"] == "child"
    git(child, "add", "file.txt"); git(child, "commit", "-qm", "change")
    two = record_audit_pass(config, campaign_id="camp", roadmap_range="GIT-FRX-01..17", gap_count=0, pass_id="p2")
    delta = audit_delta("p1", "p2", campaign_id="camp")
    assert delta["source_changed"] is True
    assert "commit" in delta["repositories"][0]["changed_fields"]
    assert one["audit_digest"] != two["audit_digest"]


def test_audit_evidence_digest_is_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = parent_config(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    evidence = tmp_path / "evidence.json"
    evidence.write_text('{"ok":true}\n', encoding="utf-8")
    payload = record_audit_pass(config, campaign_id="camp", evidence=[evidence], gap_count=1, pass_id="with-evidence")
    assert payload["zero_gap"] is False
    assert payload["evidence"][0]["sha256"]
    assert Path(payload["path"]).is_file()


def test_rerere_exact_memory_reuses_only_with_validation_rerun(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    root, preimage = conflict_repo(tmp_path)
    (root / "file.txt").write_text("resolved\n", encoding="utf-8")
    memory = record_resolution(root, preimage, resolved_paths=["file.txt"], campaign_id="camp", validation={"status": "passed", "run_id": "v1"})
    assert memory["resolution_digest"]
    # Restore the exact same conflict worktree content; index still contains the
    # original stages so the preimage identity is unchanged.
    (root / "file.txt").write_text("<<<<<<< HEAD\nmain\n=======\nside\n>>>>>>> side\n", encoding="utf-8")
    called = []
    result = apply_validated_resolution(root, campaign_id="camp", mode="validated-auto", validate=lambda: called.append(True) or True)
    assert result["applied"] is True
    assert result["validation_rerun"] is True
    assert called == [True]
    assert (root / "file.txt").read_text(encoding="utf-8") == "resolved\n"


def test_rerere_failed_validation_restores_conflict_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    root, preimage = conflict_repo(tmp_path)
    (root / "file.txt").write_text("resolved\n", encoding="utf-8")
    record_resolution(root, preimage, resolved_paths=["file.txt"], campaign_id="camp", validation={"status": "passed"})
    conflict_text = "<<<<<<< HEAD\nmain\n=======\nside\n>>>>>>> side\n"
    (root / "file.txt").write_text(conflict_text, encoding="utf-8")
    with pytest.raises(GitRerereError, match="failed current validation"):
        apply_validated_resolution(root, campaign_id="camp", mode="validated-auto", validate=lambda: False)
    assert (root / "file.txt").read_text(encoding="utf-8") == conflict_text


def test_rerere_campaign_scope_and_stale_memory_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    root, preimage = conflict_repo(tmp_path)
    (root / "file.txt").write_text("resolved\n", encoding="utf-8")
    record_resolution(root, preimage, resolved_paths=["file.txt"], campaign_id="camp", validation={"status": "passed"})
    assert suggest_resolution(root, campaign_id="other")["matched"] is False
    # Corrupt the exact memory to prove stale scoped data is rejected, not used.
    path = Path(str((tmp_path / "xdg" / "state")))
    matches = list((tmp_path / "xdg").rglob("resolution.json"))
    assert len(matches) == 1
    payload = json.loads(matches[0].read_text(encoding="utf-8")); payload["repository_id"] = "wrong"
    matches[0].write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GitRerereError, match="stale"):
        suggest_resolution(root, campaign_id="camp")


def test_git_doctor_finds_stale_transaction_and_does_not_delete_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"; repo(root)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    # Construct the durable active marker in the same public transaction layout.
    from devtool.transactions import transaction_root
    tr = transaction_root(root)
    tx_dir = tr / "stale-tx"; tx_dir.mkdir(parents=True)
    tx = {"transaction_id": "stale-tx", "repo_root": str(root), "status": "applied", "pid": 99999999, "process_start": "", "affected_paths": []}
    (tx_dir / "transaction.json").write_text(json.dumps(tx), encoding="utf-8")
    (tr / "active.json").write_text(json.dumps({"path": str(tx_dir / "transaction.json"), "transaction_id": "stale-tx"}), encoding="utf-8")
    payload = git_doctor(root, repair_safe=True)
    row = next(item for item in payload["checks"] if item["name"] == "transactions")
    assert row["status"] == "warning"
    assert "stale-tx" in row["stale_transactions"]
    assert (tx_dir / "transaction.json").exists(), "--repair-safe must not destroy recovery journals"


def test_git_doctor_checks_operation_worktrees_objects_and_signing(tmp_path: Path) -> None:
    root = tmp_path / "repo"; repo(root)
    payload = git_doctor(root)
    names = {row["name"] for row in payload["checks"]}
    assert {"repository", "head", "upstream", "working-tree", "git-operation", "clone-shape", "objects", "worktrees", "locks", "devtool-refs", "transactions", "signing"} <= names
    assert payload["ok"] is True


def test_federation_doctor_reports_inconsistent_child_ref(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child, config = parent_config(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    lineage = tmp_path / "xdg" / "federation-git-lineage" / "run-a" / "child-a.json"
    lineage.parent.mkdir(parents=True)
    lineage.write_text(json.dumps({
        "parent_run_id": "run-a", "child_run_id": "child-a", "repository": "child",
        "start_ref": "refs/devtool/federations/run-a/repos/child/start", "start_commit": "0" * 40,
    }), encoding="utf-8")
    payload = federation_doctor(config, "run-a")
    assert payload["ok"] is False
    assert payload["inconsistencies"][0]["kind"] == "start-ref-mismatch"


def test_cli_exposes_audit_git_doctor_rerere_and_federation_doctor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    parent, child, config = parent_config(tmp_path)
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    assert main(["-r", str(parent), "audit", "record", "--campaign", "camp", "--pass-id", "p1", "--roadmap", "GIT-FRX-01..17", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)["data"]; assert record["zero_gap"] is True
    assert main(["-r", str(parent), "audit", "status", "--campaign", "camp", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)["data"]; assert status["zero_gap_fresh"] is True
    assert main(["-r", str(parent), "git", "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["kind"] == "devtool.git-doctor"
    assert main(["-r", str(parent), "federation", "doctor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["kind"] == "devtool.federation-git-doctor"


def test_git_frx17_does_not_implement_git_frx18_signing_or_final_manifest() -> None:
    source = Path("devtool/git_audit.py").read_text(encoding="utf-8") + Path("devtool/git_rerere.py").read_text(encoding="utf-8") + Path("devtool/git_doctor.py").read_text(encoding="utf-8")
    assert "federated provenance manifest" not in source.lower()
    assert "signing = off" not in source.lower()


def test_completion_exposes_git_frx17_surfaces_without_breaking_prior_sequences() -> None:
    from devtool.completion import ZSH_COMPLETION
    assert "record delta status" in ZSH_COMPLETION
    assert "state doctor rerere notes campaign bisect" in ZSH_COMPLETION
    assert "sync doctor bisect" in ZSH_COMPLETION
    assert "final-seal seal status summary plan run exec" in ZSH_COMPLETION
    assert "status summary plan run exec resume retry lock integrate" in ZSH_COMPLETION
