from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import stat
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from devtool.cli import _verify_applied_artifact_effects
from devtool.artifacts import (
    list_archive_members,
    preview_artifact,
    read_delete_manifest,
    validate_artifact,
    validate_overlay_bundle,
    write_archive_entries,
)
from devtool.config import ProjectConfig
from devtool.runners import _extract_archive, _sync_snapshot
from devtool.transactions import (
    checkpoint_transaction_applied,
    create_apply_transaction,
    find_active_transactions,
    rollback_transaction,
    transaction_root,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _zip_with_mode(path: Path, name: str, data: bytes, mode: int) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFREG | mode) << 16
        zf.writestr(info, data)
    return path


def _tar(path: Path, entries: list[tuple[str, bytes | None, int, str]]) -> Path:
    with tarfile.open(path, "w") as tf:
        for name, data, mode, kind in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            else:
                payload = data or b""
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))
    return path


def _manifest_overlay(path: Path, files: dict[str, bytes], manifest: dict) -> Path:
    return write_archive_entries(path, {".devtool-artifact.json": json.dumps(manifest).encode(), **files})


def test_symlink_payload_replaces_link_path_without_mutating_link_target_and_rolls_back(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    real = repo / "real.txt"
    real.write_text("real\n", encoding="utf-8")
    link = repo / "link.txt"
    link.symlink_to("real.txt")
    archive = _zip(tmp_path / "overlay.zip", {"link.txt": b"overlay\n"})

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            repo,
            archive,
            artifact_type="overlay",
            preview={"changes": [{"kind": "modify", "path": "link.txt"}]},
        )
        _extract_archive(archive, repo)

        assert link.is_file()
        assert not link.is_symlink()
        assert link.read_text(encoding="utf-8") == "overlay\n"
        assert real.read_text(encoding="utf-8") == "real\n"

        result = rollback_transaction(tx["path"], reason="symlink rollback regression")

    assert result.returncode == 0, result.output
    assert link.is_symlink()
    assert os.readlink(link) == "real.txt"
    assert real.read_text(encoding="utf-8") == "real\n"


def test_preflight_write_overlapping_unchanged_payload_is_transactional(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    existing = repo / "same.txt"
    existing.write_text("same\n", encoding="utf-8")
    payload = existing.read_bytes()
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": "same.txt", "action": "modify", "source_sha256": _sha(payload), "sha256": _sha(payload), "size": len(payload)},
            ],
        },
        "preflight": {"write_paths": ["same.txt"]},
    }
    archive = _manifest_overlay(tmp_path / "preflight.zip", {"same.txt": payload}, manifest)

    preview = preview_artifact(repo, archive)
    by_path = {change["path"]: change["kind"] for change in preview["changes"]}
    assert by_path["same.txt"] == "preflight-write"

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(repo, archive, artifact_type="overlay", preview=preview)
    assert [entry["path"] for entry in tx["affected_paths"]] == ["same.txt"]
    assert tx["affected_paths"][0]["backup_sha256"]


def test_rollback_refuses_to_clobber_user_edits_after_successful_apply_checkpoint(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "file.txt"
    target.write_text("base\n", encoding="utf-8")
    archive = _zip(tmp_path / "overlay.zip", {"file.txt": b"overlay\n"})

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(
            repo,
            archive,
            artifact_type="overlay",
            preview={"changes": [{"kind": "modify", "path": "file.txt"}]},
        )
        target.write_text("overlay\n", encoding="utf-8")
        checkpoint_transaction_applied(tx["path"])
        target.write_text("user edit\n", encoding="utf-8")

        result = rollback_transaction(tx["path"], reason="must not clobber user edit")

    assert result.returncode == 1
    assert "changed after Devtool applied" in result.output
    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_snapshot_sync_preserves_devtool_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    snapshot = tmp_path / "snapshot"
    repo.mkdir()
    snapshot.mkdir()
    (repo / ".devtool").mkdir()
    (repo / ".devtool" / "artifacts.json").write_text("{}\n", encoding="utf-8")
    (repo / "stale.txt").write_text("remove\n", encoding="utf-8")

    _sync_snapshot(snapshot, repo)

    assert (repo / ".devtool" / "artifacts.json").exists()
    assert not (repo / "stale.txt").exists()


def test_rollback_removes_added_empty_directories_and_created_parents(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = _tar(tmp_path / "dirs.tar", [("nested/child.txt", b"child\n", 0o644, "file"), ("empty/dir", None, 0o755, "dir")])
    preview = preview_artifact(repo, archive)
    assert {change["path"]: change["kind"] for change in preview["changes"]} == {
        "empty/dir": "add-dir",
        "nested/child.txt": "add",
    }

    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        tx = create_apply_transaction(repo, archive, artifact_type="overlay", preview=preview)
        _extract_archive(archive, repo)
        checkpoint_transaction_applied(tx["path"])
        result = rollback_transaction(tx["path"], reason="cleanup created dirs")

    assert result.returncode == 0, result.output
    assert not (repo / "nested").exists()
    assert not (repo / "empty").exists()
    assert "removed_created_parent_dirs" in result.output


def test_apply_verification_accepts_added_directory_and_rejects_non_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    added = repo / "empty" / "dir"
    added.mkdir(parents=True)
    config = ProjectConfig(repo_root=repo, kind="unknown")
    args = argparse.Namespace(
        dry_run=False,
        preview=False,
        diff=False,
        interactive=False,
        validate_only=False,
        command="apply-overlay",
        _devtool_artifact_preview={"changes": [{"kind": "add-dir", "path": "empty/dir"}]},
        _devtool_artifact_type="overlay",
        artifact="",
    )

    result = _verify_applied_artifact_effects(config, args)
    assert result is not None
    assert result.returncode == 0, result.output

    added.rmdir()
    added.write_text("not a directory\n", encoding="utf-8")
    result = _verify_applied_artifact_effects(config, args)
    assert result is not None
    assert result.returncode == 1
    assert "expected applied directory" in result.output


def test_delete_manifest_traversal_is_rejected_during_validation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = _zip(tmp_path / "bad-delete.zip", {".devtool-delete": b"../outside.txt\n"})

    validation = validate_artifact(repo, archive)

    assert not validation.ok
    assert any("Unsafe archive path" in message for message in validation.messages)
    with pytest.raises(RuntimeError):
        read_delete_manifest(archive)


def test_extraction_preserves_zip_and_tar_executable_modes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    zip_archive = _zip_with_mode(tmp_path / "mode.zip", "script.sh", b"#!/bin/sh\n", 0o755)
    tar_archive = _tar(tmp_path / "mode.tar", [("tool.sh", b"#!/bin/sh\n", 0o755, "file")])

    _extract_archive(zip_archive, repo)
    _extract_archive(tar_archive, repo)

    assert stat.S_IMODE((repo / "script.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((repo / "tool.sh").stat().st_mode) == 0o755


def test_payload_replacing_existing_directory_is_rejected_before_apply(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "conflict").mkdir()
    archive = _zip(tmp_path / "dir-conflict.zip", {"conflict": b"file\n"})

    validation = validate_artifact(repo, archive)

    assert not validation.ok
    assert any("replace existing directory" in message for message in validation.messages)


@pytest.mark.parametrize("entries", [
    {"a.txt": b"one", "./a.txt": b"two"},
    {"a": b"one", "a/b.txt": b"two"},
])
def test_duplicate_normalized_or_file_child_archive_paths_are_rejected(tmp_path: Path, entries: dict[str, bytes]) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)

    with pytest.raises(RuntimeError):
        list_archive_members(archive)
    assert not validate_artifact(tmp_path, archive).ok


def test_malformed_legacy_active_transaction_marker_does_not_surface_as_recoverable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch.dict(os.environ, {"DEVTOOL_XDG_DIR": str(tmp_path / "xdg")}, clear=False):
        root = transaction_root(repo)
        root.mkdir(parents=True)
        (root / "active.json").write_text("not-json", encoding="utf-8")

        assert find_active_transactions(repo, include_live=True) == []
        assert not (root / "active.json").exists()


def test_payload_and_delete_manifest_same_path_conflict_is_rejected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = _zip(tmp_path / "conflict.zip", {"target.txt": b"payload\n", ".devtool-delete": b"target.txt\n"})

    validation = validate_artifact(repo, archive)

    assert not validation.ok
    assert any("payload conflicts" in message for message in validation.messages)


def test_nested_devtool_delete_is_payload_not_destructive_manifest(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = _zip(tmp_path / "nested-delete.zip", {"docs/.devtool-delete": b"keep me\n"})

    assert read_delete_manifest(archive) == []
    preview = preview_artifact(repo, archive)
    assert preview["changes"] == [{"kind": "add", "path": "docs/.devtool-delete"}]
    _extract_archive(archive, repo)
    assert (repo / "docs" / ".devtool-delete").read_text(encoding="utf-8") == "keep me\n"


def test_json_delete_manifest_requires_string_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = _zip(tmp_path / "bad-json-delete.zip", {".devtool-delete": json.dumps(["ok.txt", 42]).encode()})

    validation = validate_artifact(repo, archive)

    assert not validation.ok
    assert any("list of strings" in message for message in validation.messages)


def test_bundle_schema_v2_implicit_string_false_does_not_bypass_integrity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    nested = _zip(tmp_path / "nested.zip", {"file.txt": b"ok\n"})
    bundle_manifest = {
        "schema_version": 2,
        "kind": "devtool.overlay_bundle",
        "implicit": "false",
        "apply_order": [{"id": "one", "path": "overlays/nested.zip"}],
    }
    bundle = write_archive_entries(
        tmp_path / "bundle.zip",
        {"devtool-bundle.json": json.dumps(bundle_manifest).encode(), "overlays/nested.zip": nested.read_bytes()},
    )

    validation = validate_overlay_bundle(repo, bundle)

    assert not validation.ok
    assert any("implicit must be a boolean" in message for message in validation.messages)
    assert any("requires sha256" in message for message in validation.messages)
    assert any("requires size" in message for message in validation.messages)


@pytest.mark.parametrize("item", [
    {"id": "second", "path": "overlays/second.zip", "after": "first"},
    {"id": "second", "path": "overlays/second.zip", "requires": [123]},
])
def test_bundle_dependencies_must_be_string_lists(tmp_path: Path, item: dict) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    first = _zip(tmp_path / "first.zip", {"a.txt": b"a\n"})
    second = _zip(tmp_path / "second.zip", {"b.txt": b"b\n"})
    manifest = {
        "schema_version": 1,
        "kind": "devtool.overlay_bundle",
        "apply_order": [
            {"id": "first", "path": "overlays/first.zip"},
            item,
        ],
    }
    bundle = write_archive_entries(
        tmp_path / "deps.zip",
        {
            "devtool-bundle.json": json.dumps(manifest).encode(),
            "overlays/first.zip": first.read_bytes(),
            "overlays/second.zip": second.read_bytes(),
        },
    )

    validation = validate_overlay_bundle(repo, bundle)

    assert not validation.ok
    assert any("must" in message and "list" in message for message in validation.messages)


def test_archive_root_directory_marker_is_ignored_but_not_payload(tmp_path: Path) -> None:
    archive = tmp_path / "root-marker.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        root = tarfile.TarInfo(".")
        root.type = tarfile.DIRTYPE
        tf.addfile(root)
        payload = b"ok\n"
        info = tarfile.TarInfo("file.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))

    members = list_archive_members(archive, include_data=True)

    assert [member.name for member in members] == ["file.txt"]
    assert members[0].data == b"ok\n"


def test_archive_empty_non_directory_member_still_fails_closed(tmp_path: Path) -> None:
    archive = tmp_path / "empty-name.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("")
        info.size = 0
        tf.addfile(info, io.BytesIO(b""))

    with pytest.raises(RuntimeError, match="Unsafe archive path: empty path"):
        list_archive_members(archive)
