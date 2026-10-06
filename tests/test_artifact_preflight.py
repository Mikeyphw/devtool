from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.artifact_validation import run_artifact_preflight_script
from devtool.artifacts import (
    artifact_preflight_phase,
    artifact_preflight_write_paths,
    preview_artifact,
    validate_artifact,
    write_archive_entries,
)
from devtool.runners import apply_overlay, apply_overlay_bundle
from devtool.transactions import affected_paths_from_preview, create_apply_transaction, rollback_transaction


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay(
    path: Path,
    *,
    payload: dict[str, bytes] | None = None,
    script: bytes | None = None,
    phase: str = "before_extract",
    write_paths: list[str] | None = None,
    runner: str = "bash",
    sha256: str | None = None,
    timeout_seconds: int = 30,
    extra_manifest: dict | None = None,
) -> Path:
    manifest: dict = {
        "schema_version": 2,
        "type": "overlay",
    }
    if script is not None:
        manifest["preflight"] = {
            "phase": phase,
            "write_paths": list(write_paths or []),
            "script": {
                "path": ".devtool-artifact/preflight.sh",
                "runner": runner,
                "sha256": sha256 if sha256 is not None else _sha(script),
                "timeout_seconds": timeout_seconds,
            },
        }
    if extra_manifest:
        manifest.update(extra_manifest)
    entries = {".devtool-artifact.json": json.dumps(manifest).encode()}
    entries.update(payload or {})
    if script is not None:
        entries[".devtool-artifact/preflight.sh"] = script
    write_archive_entries(path, entries)
    return path


def _bundle(path: Path, overlays: list[Path], *, script: bytes | None = None, phase: str = "before_extract", write_paths: list[str] | None = None) -> Path:
    apply_order = []
    entries: dict[str, bytes] = {}
    for index, overlay in enumerate(overlays, 1):
        raw = overlay.read_bytes()
        member = f"overlays/{index:03d}-{overlay.name}"
        entries[member] = raw
        apply_order.append(
            {
                "id": f"overlay-{index}",
                "path": member,
                "sha256": _sha(raw),
                "size": len(raw),
            }
        )
    manifest: dict = {
        "schema_version": 2,
        "type": "overlay_bundle",
        "kind": "devtool.overlay_bundle",
        "apply_order": apply_order,
        "apply": {"atomic": True, "path_policy": "repository-relative"},
        "validation": {"strategy": "per-overlay"},
    }
    if script is not None:
        manifest["preflight"] = {
            "phase": phase,
            "write_paths": list(write_paths or []),
            "script": {
                "path": ".devtool-artifact/preflight.sh",
                "runner": "bash",
                "sha256": _sha(script),
                "timeout_seconds": 30,
            },
        }
        entries[".devtool-artifact/preflight.sh"] = script
    entries["devtool-bundle.json"] = json.dumps(manifest).encode()
    write_archive_entries(path, entries)
    return path


def test_preflight_phase_aliases_and_write_paths_normalize() -> None:
    manifest = {
        "preflight": {
            "phase": "after-extract",
            "write_paths": ["generated/cache", "./state.txt", "generated/cache"],
            "script": {},
        }
    }
    assert artifact_preflight_phase(manifest) == "after_extract"
    assert artifact_preflight_write_paths(manifest) == ["generated/cache", "state.txt"]


@pytest.mark.parametrize(
    ("preflight", "expected"),
    [
        ("bad", "preflight must be an object"),
        ({"phase": "during_extract", "script": {}}, "preflight.phase must be one of"),
        ({"script": "x"}, "preflight.script must be an object"),
        ({"script": {"path": "../hook.sh", "sha256": "0" * 64}}, "Preflight scripts must live under"),
        ({"script": {"path": ".devtool-artifact/hook.sh", "runner": "ruby", "sha256": "0" * 64}}, "Unsupported preflight script runner"),
        ({"script": {"path": ".devtool-artifact/hook.sh", "sha256": "0" * 64, "timeout_seconds": 0}}, "timeout_seconds must be a positive integer"),
        ({"script": {"path": ".devtool-artifact/hook.sh"}}, "preflight.script.sha256 is required"),
        ({"write_paths": [".git/config"], "script": {}}, "may not target Git metadata"),
        ({"write_paths": ["../outside"], "script": {}}, "Unsafe preflight.write_paths path"),
    ],
)
def test_invalid_preflight_manifest_is_rejected(tmp_path: Path, preflight, expected: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = tmp_path / "invalid.zip"
    manifest = {"schema_version": 2, "type": "overlay", "preflight": preflight}
    write_archive_entries(archive, {".devtool-artifact.json": json.dumps(manifest).encode(), "payload.txt": b"new"})
    validation = validate_artifact(repo, archive)
    assert not validation.ok
    assert any(expected in message for message in validation.messages), validation.messages


def test_preflight_checksum_and_member_are_verified(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b"#!/bin/sh\nexit 0\n"
    bad_sha = _overlay(tmp_path / "bad-sha.zip", script=script, sha256="0" * 64)
    validation = validate_artifact(repo, bad_sha)
    assert not validation.ok
    assert "Preflight script SHA-256 mismatch." in validation.messages

    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "preflight": {
            "phase": "before_extract",
            "script": {
                "path": ".devtool-artifact/missing.sh",
                "runner": "sh",
                "sha256": _sha(script),
                "timeout_seconds": 10,
            },
        },
    }
    missing = tmp_path / "missing.zip"
    write_archive_entries(missing, {".devtool-artifact.json": json.dumps(manifest).encode()})
    validation = validate_artifact(repo, missing)
    assert not validation.ok
    assert any("Preflight script is missing from archive" in message for message in validation.messages)


def test_preflight_dry_run_does_not_execute(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'#!/bin/sh\necho ran > "$DEVTOOL_REPO_ROOT/ran.txt"\n'
    archive = _overlay(tmp_path / "dry.zip", script=script, write_paths=["ran.txt"])
    result = run_artifact_preflight_script(repo, archive, phase="before_extract", dry_run=True)
    assert result is not None
    assert result.skipped
    assert result.returncode == 0
    assert not (repo / "ran.txt").exists()


def test_before_extract_runs_before_payload_and_exports_environment(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "payload.txt").write_text("old", encoding="utf-8")
    script = b'''#!/bin/sh
set -eu
test "$(cat "$DEVTOOL_REPO_ROOT/payload.txt")" = old
test "$DEVTOOL_PREFLIGHT_PHASE" = before_extract
test -d "$DEVTOOL_PREFLIGHT_TMPDIR"
test "$DEVTOOL_PREFLIGHT_TMPDIR" = "$DEVTOOL_ARTIFACT_SCRIPT_TMPDIR"
printf '%s|%s\n' "$DEVTOOL_PREFLIGHT_PHASE" "$DEVTOOL_TARGET" > "$DEVTOOL_REPO_ROOT/preflight-env.txt"
'''
    archive = _overlay(
        tmp_path / "before.zip",
        payload={"payload.txt": b"new"},
        script=script,
        phase="before_extract",
        write_paths=["preflight-env.txt"],
        extra_manifest={"target": "devtool"},
    )
    assert validate_artifact(repo, archive).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
        assert result.returncode == 0, result.output
        assert (repo / "payload.txt").read_text() == "new"
        assert (repo / "preflight-env.txt").read_text().strip() == "before_extract|devtool"
        assert not (repo / ".devtool-artifact").exists()
        rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


def test_after_extract_runs_after_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "payload.txt").write_text("old", encoding="utf-8")
    script = b'''#!/bin/sh
set -eu
test "$(cat "$DEVTOOL_REPO_ROOT/payload.txt")" = new
test "$DEVTOOL_PREFLIGHT_PHASE" = after_extract
printf after > "$DEVTOOL_REPO_ROOT/after.txt"
'''
    archive = _overlay(
        tmp_path / "after.zip",
        payload={"payload.txt": b"new"},
        script=script,
        phase="after_extract",
        write_paths=["after.txt"],
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
        assert result.returncode == 0, result.output
        assert (repo / "after.txt").read_text() == "after"
        rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


@pytest.mark.parametrize("phase,exit_code", [("before_extract", 9), ("after_extract", 7)])
def test_failed_preflight_rolls_back_payload_and_declared_writes(tmp_path: Path, phase: str, exit_code: int) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "payload.txt").write_text("old-payload", encoding="utf-8")
    (repo / "generated.txt").write_text("old-generated", encoding="utf-8")
    script = f'''#!/bin/sh
set -eu
printf mutated > "$DEVTOOL_REPO_ROOT/generated.txt"
exit {exit_code}
'''.encode()
    archive = _overlay(
        tmp_path / f"fail-{phase}.zip",
        payload={"payload.txt": b"new-payload"},
        script=script,
        phase=phase,
        write_paths=["generated.txt"],
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == exit_code
    assert result.failure_kind == "preflight_failure"
    assert result.metadata.get("rolled_back_during_apply") is True
    assert result.metadata.get("rollback_returncode") == 0
    assert (repo / "payload.txt").read_text() == "old-payload"
    assert (repo / "generated.txt").read_text() == "old-generated"
    assert f"failed during {phase} with exit {exit_code}" in result.output



def test_failed_preflight_in_isolated_worktree_does_not_publish_deleted_transaction_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess = __import__("subprocess")

    def git(*args: str) -> None:
        proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test User")
    (repo / "payload.txt").write_text("old-payload", encoding="utf-8")
    git("add", "payload.txt")
    git("commit", "-qm", "baseline")

    archive = _overlay(
        tmp_path / "isolated-preflight-failure.zip",
        payload={"payload.txt": b"new-payload"},
        script=b"#!/bin/sh\nexit 9\n",
        phase="before_extract",
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))

    assert result.returncode == 9
    assert result.metadata.get("transaction_isolation") == "worktree"
    assert result.metadata.get("rolled_back_during_apply") is True
    assert result.metadata.get("rollback_returncode") == 0
    assert result.metadata.get("transaction_closed") is True
    assert result.metadata.get("transaction_path") == ""
    closed = Path(str(result.metadata.get("closed_transaction_path") or ""))
    assert closed.name == "transaction.json"
    assert not closed.exists()
    assert Path(str(result.metadata.get("transaction_repo_root"))).resolve() == repo.resolve()
    assert (repo / "payload.txt").read_text(encoding="utf-8") == "old-payload"

def test_preflight_write_paths_enter_preview_transaction_and_reservation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "existing.txt").write_text("original", encoding="utf-8")
    script = b"#!/bin/sh\nexit 0\n"
    archive = _overlay(
        tmp_path / "writes.zip",
        payload={"payload.txt": b"new"},
        script=script,
        write_paths=["existing.txt", "generated/tree"],
    )
    preview = preview_artifact(repo, archive)
    change_by_path = {change["path"]: change["kind"] for change in preview["changes"]}
    assert change_by_path["existing.txt"] == "preflight-write"
    assert change_by_path["generated/tree"] == "preflight-write"
    affected = affected_paths_from_preview(preview)
    assert "existing.txt" in affected
    assert "generated/tree" in affected

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(repo, archive, artifact_type="overlay", preview=preview)
        assert "existing.txt" in tx["reserved_paths"]
        assert "generated/tree" in tx["reserved_paths"]
        rollback_transaction(tx["path"], reason="test cleanup")


def test_bundle_preflight_and_nested_hooks_run_in_declared_order(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    order_path = "$DEVTOOL_REPO_ROOT/order.log"
    first_script = f'''#!/bin/sh
set -eu
printf 'nested-before\\n' >> "{order_path}"
'''.encode()
    second_script = f'''#!/bin/sh
set -eu
test -f "$DEVTOOL_REPO_ROOT/b.txt"
printf 'nested-after\\n' >> "{order_path}"
'''.encode()
    bundle_script = f'''#!/bin/sh
set -eu
test ! -e "$DEVTOOL_REPO_ROOT/a.txt"
printf 'bundle-before\\n' >> "{order_path}"
'''.encode()
    first = _overlay(
        tmp_path / "first.zip",
        payload={"a.txt": b"a"},
        script=first_script,
        phase="before_extract",
        write_paths=["order.log"],
    )
    second = _overlay(
        tmp_path / "second.zip",
        payload={"b.txt": b"b"},
        script=second_script,
        phase="after_extract",
        write_paths=["order.log"],
    )
    bundle = _bundle(
        tmp_path / "bundle.zip",
        [first, second],
        script=bundle_script,
        phase="before_extract",
        write_paths=["order.log"],
    )
    validation = validate_artifact(repo, bundle, artifact_type="overlay_bundle")
    assert validation.ok, validation.messages
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay_bundle(repo, bundle)
        assert result.returncode == 0, result.output
        assert (repo / "order.log").read_text().splitlines() == [
            "bundle-before",
            "nested-before",
            "nested-after",
        ]
        assert (repo / "a.txt").read_text() == "a"
        assert (repo / "b.txt").read_text() == "b"
        rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


def test_ignore_checksum_applies_to_preflight_execution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'#!/bin/sh\nprintf ok > "$DEVTOOL_REPO_ROOT/ran.txt"\n'
    archive = _overlay(
        tmp_path / "ignore.zip",
        script=script,
        write_paths=["ran.txt"],
        sha256="0" * 64,
    )
    assert not validate_artifact(repo, archive).ok
    assert validate_artifact(repo, archive, ignore_checksum=True).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive), ignore_checksum=True)
        assert result.returncode == 0, result.output
        assert (repo / "ran.txt").read_text() == "ok"
        rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


def test_preflight_wrong_phase_is_noop(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'#!/bin/sh\nprintf bad > "$DEVTOOL_REPO_ROOT/ran.txt"\n'
    archive = _overlay(tmp_path / "phase.zip", script=script, phase="after_extract", write_paths=["ran.txt"])
    result = run_artifact_preflight_script(repo, archive, phase="before_extract")
    assert result is None
    assert not (repo / "ran.txt").exists()


def test_preflight_timeout_is_failure_and_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "state.txt").write_text("old", encoding="utf-8")
    script = b'''#!/bin/sh
set -eu
printf changed > "$DEVTOOL_REPO_ROOT/state.txt"
sleep 2
'''
    archive = _overlay(
        tmp_path / "timeout.zip",
        script=script,
        phase="before_extract",
        write_paths=["state.txt"],
        timeout_seconds=1,
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 124
    assert result.failure_kind == "preflight_failure"
    assert "Artifact preflight script timed out after 1 seconds." in result.output
    assert (repo / "state.txt").read_text() == "old"


def test_bundle_after_extract_runs_after_all_nested_overlays(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _overlay(tmp_path / "first-plain.zip", payload={"a.txt": b"a"})
    second = _overlay(tmp_path / "second-plain.zip", payload={"b.txt": b"b"})
    script = b'''#!/bin/sh
set -eu
test -f "$DEVTOOL_REPO_ROOT/a.txt"
test -f "$DEVTOOL_REPO_ROOT/b.txt"
printf done > "$DEVTOOL_REPO_ROOT/bundle-after.txt"
'''
    bundle = _bundle(
        tmp_path / "bundle-after.zip",
        [first, second],
        script=script,
        phase="after_extract",
        write_paths=["bundle-after.txt"],
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay_bundle(repo, bundle)
        assert result.returncode == 0, result.output
        assert (repo / "bundle-after.txt").read_text() == "done"
        rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


def _inventory_modify(path: str, *, source: bytes, payload: bytes) -> dict:
    return {
        "inventory": {
            "format": 1,
            "entries": [
                {
                    "path": path,
                    "action": "modify",
                    "source_sha256": _sha(source),
                    "sha256": _sha(payload),
                    "size": len(payload),
                }
            ],
        },
        "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative"},
    }


def test_before_extract_declared_source_checksum_change_is_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    old = b"old-source"
    payload = b"new-payload"
    (repo / "payload.txt").write_bytes(old)
    script = b'''#!/bin/sh
set -eu
printf prepared > "$DEVTOOL_REPO_ROOT/payload.txt"
'''
    archive = _overlay(
        tmp_path / "declared-source-change.zip",
        payload={"payload.txt": payload},
        script=script,
        phase="before_extract",
        write_paths=["payload.txt"],
        extra_manifest=_inventory_modify("payload.txt", source=old, payload=payload),
    )
    assert validate_artifact(repo, archive).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 0, result.output
    assert (repo / "payload.txt").read_bytes() == payload
    rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")
    assert (repo / "payload.txt").read_bytes() == old


def test_before_extract_undeclared_source_checksum_change_fails_and_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    old = b"old-source"
    payload = b"new-payload"
    (repo / "payload.txt").write_bytes(old)
    script = b'''#!/bin/sh
set -eu
printf mutated > "$DEVTOOL_REPO_ROOT/payload.txt"
'''
    archive = _overlay(
        tmp_path / "undeclared-source-change.zip",
        payload={"payload.txt": payload},
        script=script,
        phase="before_extract",
        write_paths=[],
        extra_manifest=_inventory_modify("payload.txt", source=old, payload=payload),
    )
    assert validate_artifact(repo, archive).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 1
    assert result.failure_kind == "preflight_failure"
    assert "changed source SHA-256 for undeclared write path: payload.txt" in result.output
    assert result.metadata.get("rolled_back_during_apply") is True
    assert (repo / "payload.txt").read_bytes() == old


def test_after_extract_undeclared_payload_checksum_change_fails_and_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    old = b"old-source"
    payload = b"new-payload"
    (repo / "payload.txt").write_bytes(old)
    script = b'''#!/bin/sh
set -eu
printf transformed > "$DEVTOOL_REPO_ROOT/payload.txt"
'''
    archive = _overlay(
        tmp_path / "undeclared-payload-change.zip",
        payload={"payload.txt": payload},
        script=script,
        phase="after_extract",
        write_paths=[],
        extra_manifest=_inventory_modify("payload.txt", source=old, payload=payload),
    )
    assert validate_artifact(repo, archive).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 1
    assert result.failure_kind == "preflight_failure"
    assert "changed payload SHA-256 for undeclared write path: payload.txt" in result.output
    assert result.metadata.get("rolled_back_during_apply") is True
    assert (repo / "payload.txt").read_bytes() == old


def test_after_extract_declared_payload_checksum_change_is_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    old = b"old-source"
    payload = b"new-payload"
    transformed = b"transformed"
    (repo / "payload.txt").write_bytes(old)
    script = b'''#!/bin/sh
set -eu
printf transformed > "$DEVTOOL_REPO_ROOT/payload.txt"
'''
    archive = _overlay(
        tmp_path / "declared-payload-change.zip",
        payload={"payload.txt": payload},
        script=script,
        phase="after_extract",
        write_paths=["payload.txt"],
        extra_manifest=_inventory_modify("payload.txt", source=old, payload=payload),
    )
    assert validate_artifact(repo, archive).ok
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 0, result.output
    assert (repo / "payload.txt").read_bytes() == transformed
    rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")
    assert (repo / "payload.txt").read_bytes() == old


def test_preflight_artifact_path_is_private_copy(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    script = b'''#!/bin/sh
set -eu
test "$(stat -c '%a' "$DEVTOOL_ARTIFACT_PATH")" = 600
printf tamper >> "$DEVTOOL_ARTIFACT_PATH"
printf ok > "$DEVTOOL_REPO_ROOT/preflight.txt"
'''
    archive = _overlay(
        tmp_path / "artifact-copy.zip",
        payload={"payload.txt": b"payload"},
        script=script,
        phase="before_extract",
        write_paths=["preflight.txt"],
    )
    original_sha = _sha(archive.read_bytes())
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay(repo, str(archive))
    assert result.returncode == 0, result.output
    assert _sha(archive.read_bytes()) == original_sha
    assert (repo / "payload.txt").read_bytes() == b"payload"
    assert (repo / "preflight.txt").read_text() == "ok"
    rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")


def test_bundle_after_preflight_undeclared_nested_payload_checksum_change_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    child = _overlay(tmp_path / "child.zip", payload={"a.txt": b"child"})
    script = b'''#!/bin/sh
set -eu
printf changed > "$DEVTOOL_REPO_ROOT/a.txt"
'''
    bundle = _bundle(tmp_path / "bundle-undeclared-change.zip", [child], script=script, phase="after_extract")
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay_bundle(repo, str(bundle))
    assert result.returncode == 1
    assert result.failure_kind == "preflight_failure"
    assert "changed checksum/state for undeclared write path: a.txt" in result.output
    assert result.metadata.get("rolled_back_during_apply") is True
    assert not (repo / "a.txt").exists()


def test_bundle_after_preflight_declared_nested_payload_checksum_change_is_allowed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    child = _overlay(tmp_path / "child.zip", payload={"a.txt": b"child"})
    script = b'''#!/bin/sh
set -eu
printf changed > "$DEVTOOL_REPO_ROOT/a.txt"
'''
    bundle = _bundle(
        tmp_path / "bundle-declared-change.zip",
        [child],
        script=script,
        phase="after_extract",
        write_paths=["a.txt"],
    )
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        result = apply_overlay_bundle(repo, str(bundle))
    assert result.returncode == 0, result.output
    assert (repo / "a.txt").read_bytes() == b"changed"
    rollback_transaction(result.metadata["transaction_path"], reason="test cleanup")
    assert not (repo / "a.txt").exists()
