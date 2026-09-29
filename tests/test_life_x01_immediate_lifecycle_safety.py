from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.artifacts import artifact_validation_policy, artifact_validation_tests, validate_artifact, write_archive_entries
from devtool.python_support import clean_python
from devtool.repository_cleanup import cleanup_repository, discover_backup_candidates
from devtool.runners import apply_overlay, apply_snapshot
import devtool.cli as cli_module
from devtool.cli import _complete_apply_transaction, _required_artifact_validation_execution_guard, build_parser
from devtool.models import OperationResult
from devtool.transactions import _atomic_write_json, create_apply_transaction, rollback_transaction


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=False, timeout=10)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _overlay(path: Path, *, script: bytes | None = None, payload: dict[str, bytes] | None = None) -> Path:
    manifest: dict[str, object] = {"schema_version": 2, "type": "overlay"}
    entries: dict[str, bytes] = {}
    if script is not None:
        manifest["preflight"] = {
            "phase": "before_extract",
            "write_paths": [],
            "script": {
                "path": ".devtool-artifact/preflight.sh",
                "runner": "sh",
                "sha256": _sha(script),
                "timeout_seconds": 30,
            },
        }
        entries[".devtool-artifact/preflight.sh"] = script
    entries[".devtool-artifact.json"] = json.dumps(manifest).encode()
    entries.update(payload or {})
    write_archive_entries(path, entries)
    return path


def test_life_x01_validation_required_must_be_a_real_boolean() -> None:
    manifest = {
        "validation": {
            "tests": [
                {"id": "bad", "runner": "command", "command": ["true"], "required": "false"}
            ]
        }
    }
    with pytest.raises(ValueError, match="required must be a boolean"):
        artifact_validation_tests(manifest)


def test_life_x01_failure_action_is_single_recovery_authority() -> None:
    policy = artifact_validation_policy(
        {"validation": {"failure_action": "keep", "rollback_on_failure": True}}
    )
    assert policy["failure_action"] == "keep"
    assert policy["rollback_on_failure"] is False


def test_life_x01_snapshot_direct_apply_fails_closed_before_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "invalid-snapshot.zip"
    write_archive_entries(
        artifact,
        {
            ".devtool-artifact.json": json.dumps({"schema_version": 999, "type": "snapshot"}).encode(),
            "payload.txt": b"must-not-land",
        },
    )

    result = apply_snapshot(repo, str(artifact))

    assert result.returncode != 0
    assert not (repo / "payload.txt").exists()
    assert artifact.exists(), "rejected direct snapshot must not be consumed"


def test_life_x01_snapshot_records_post_apply_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "snapshot.zip"
    write_archive_entries(
        artifact,
        {
            ".devtool-artifact.json": json.dumps({"schema_version": 2, "type": "snapshot"}).encode(),
            "payload.txt": b"snapshot-result",
        },
    )
    assert validate_artifact(repo, artifact, artifact_type="snapshot").ok
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))

    result = apply_snapshot(repo, str(artifact))

    assert result.returncode == 0, result.output
    tx = json.loads(Path(result.metadata["transaction_path"]).read_text(encoding="utf-8"))
    entry = next(item for item in tx["affected_paths"] if item["path"] == "payload.txt")
    assert isinstance(entry.get("applied_identity"), dict)
    assert tx.get("applied_checkpoint_at")
    assert (repo / "payload.txt").read_bytes() == b"snapshot-result"
    rollback = rollback_transaction(result.metadata["transaction_path"], reason="fixture cleanup")
    assert rollback.returncode == 0, rollback.output


def test_life_x01_undeclared_hook_write_is_contained_even_outside_transaction_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'#!/bin/sh\nset -eu\nprintf surprise > "$DEVTOOL_REPO_ROOT/surprise.txt"\n'
    artifact = _overlay(tmp_path / "undeclared.zip", script=script, payload={"payload.txt": b"new"})
    assert validate_artifact(repo, artifact).ok
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))

    result = apply_overlay(repo, str(artifact))

    assert result.returncode != 0
    assert "undeclared" in result.output.lower() or "write" in result.output.lower()
    assert not (repo / "surprise.txt").exists()
    assert not (repo / "payload.txt").exists()


def test_life_x01_transaction_state_is_private_and_history_redacts_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = tmp_path / "overlay.zip"
    _overlay(artifact, payload={"file.txt": b"new"})
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "file.txt"}], "counts": {}},
    )
    tx_path = Path(str(tx["path"]))
    if os.name == "posix":
        assert stat.S_IMODE(tx_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(tx_path.parent.parent.stat().st_mode) == 0o700
    tx["api_token"] = "secret-value"
    _atomic_write_json(tx_path, tx)
    history = tx_path.parent.parent / "history" / f"{tx['transaction_id']}.json"

    result = rollback_transaction(tx_path, reason="fixture cleanup")

    assert result.returncode == 0, result.output
    archived = json.loads(history.read_text(encoding="utf-8"))
    assert archived["api_token"] == "<redacted>"
    assert "secret-value" not in history.read_text(encoding="utf-8")


def test_life_x01_cleanup_preserves_tracked_and_nonignored_heuristic_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / ".gitignore").write_text("dist/\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "tracked.txt").write_text("source", encoding="utf-8")
    (repo / "tracked.bak").write_text("source", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    (repo / "dist").mkdir()
    (repo / "dist" / "generated.whl").write_bytes(b"wheel")
    (repo / "untracked.bak").write_text("backup", encoding="utf-8")

    removed = clean_python(repo, categories=("artifacts",))
    backups = {path.relative_to(repo).as_posix() for path in discover_backup_candidates(repo)}

    assert (repo / "build" / "tracked.txt").exists()
    assert not (repo / "dist").exists()
    assert any(path.endswith("dist") for path in removed)
    assert "tracked.bak" not in backups
    assert "untracked.bak" not in backups


def test_life_x01_cleanup_refuses_active_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    artifact = _overlay(tmp_path / "overlay.zip", payload={"file.txt": b"new"})
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    tx = create_apply_transaction(
        repo,
        artifact,
        artifact_type="overlay",
        preview={"changes": [{"kind": "added", "path": "file.txt"}], "counts": {}},
    )

    with pytest.raises(RuntimeError, match="transaction is active"):
        cleanup_repository(repo)

    result = rollback_transaction(tx["path"], reason="fixture cleanup")
    assert result.returncode == 0, result.output


def test_life_x01_required_minimum_phase_must_actually_be_reached() -> None:
    manifest = {"validation": {"required": True, "minimum_phase": "package"}}
    result = OperationResult(
        "static-check", ["check"], 0, 0.0, "ok", [],
        metadata={"validation_phase_name": "static"},
    )
    guard = _required_artifact_validation_execution_guard(
        manifest, validation_requested=True, validation_results=[result], artifact_validation_result=None,
    )
    assert guard is not None
    assert guard.returncode == 2
    assert guard.failure_kind == "required_validation_minimum_phase_not_reached"
    assert guard.metadata["validation_minimum_phase"] == "package"


def test_life_x01_public_transaction_isolation_defaults_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo-isolation"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    artifact = _overlay(tmp_path / "isolation.zip", payload={"new.txt": b"new\n"})
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg-isolation"))
    monkeypatch.delenv("DEVTOOL_TRANSACTION_ISOLATION", raising=False)

    result = apply_overlay(repo, str(artifact))

    assert result.returncode == 0, result.output
    tx = json.loads(Path(result.metadata["transaction_path"]).read_text(encoding="utf-8"))
    assert tx["isolation"]["requested"] == "worktree"
    assert tx["isolation"]["effective"] == "worktree"
    assert not (repo / "new.txt").exists(), "public runner must mutate its isolated execution root first"
    rollback = rollback_transaction(result.metadata["transaction_path"], reason="fixture cleanup")
    assert rollback.returncode == 0, rollback.output


def test_life_x01_clean_scopes_do_not_collide_with_project_selector() -> None:
    parser = build_parser()
    args = parser.parse_args(["clean", "--project-clean", "--caches"])
    assert args.project_clean is True
    assert args.caches is True
    assert not isinstance(getattr(args, "project", ""), bool)


def test_life_x01_transaction_finalization_failure_is_classified_and_rolls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_completion(_path: str | Path) -> None:
        raise RuntimeError("fixture integration conflict")

    rollback = OperationResult(
        "apply-rollback", ["transaction", "rollback", "fixture"], 0, 0.01, "rollback complete", []
    )
    monkeypatch.setattr(cli_module, "mark_transaction_completed", fail_completion)
    monkeypatch.setattr(
        cli_module,
        "_run_transaction_rollback",
        lambda *args, **kwargs: (rollback, "$ transaction rollback fixture\nrollback complete"),
    )

    results, raw = _complete_apply_transaction("fixture-transaction.json")

    assert [item.name for item in results] == ["apply-finalize", "apply-rollback"]
    assert results[0].returncode == 1
    assert results[0].failure_kind == "transaction_finalization"
    assert "fixture integration conflict" in results[0].output
    assert results[1].returncode == 0
    assert "rollback complete" in raw


def test_life_x01_transaction_finalization_rollback_exception_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "mark_transaction_completed",
        lambda _path: (_ for _ in ()).throw(RuntimeError("fixture finalize failure")),
    )
    monkeypatch.setattr(
        cli_module,
        "_run_transaction_rollback",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fixture rollback failure")),
    )

    results, raw = _complete_apply_transaction("fixture-transaction.json")

    assert len(results) == 2
    assert results[0].failure_kind == "transaction_finalization"
    assert results[1].name == "apply-rollback"
    assert results[1].returncode == 1
    assert results[1].failure_kind == "transaction_finalization_rollback"
    assert "fixture rollback failure" in raw
