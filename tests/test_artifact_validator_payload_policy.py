from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from devtool.artifacts import preview_artifact, validate_artifact, write_archive_entries
from devtool.runners import _extract_archive


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay(path: Path, files: dict[str, bytes], manifest: dict) -> Path:
    payload = {".devtool-artifact.json": json.dumps(manifest).encode("utf-8"), **files}
    return write_archive_entries(path, payload)


def test_inventory_may_target_repo_validation_directory_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"#!/bin/sh\necho repo validator\n"
    rel = ".devtool-artifact/validation/validate.sh"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": rel, "action": "add", "sha256": _sha(payload), "size": len(payload)},
            ],
        },
    }
    archive = _overlay(tmp_path / "validator.zip", {rel: payload}, manifest)

    validation = validate_artifact(repo, archive)
    assert validation.ok, validation.messages

    preview = preview_artifact(repo, archive)
    assert {change["path"] for change in preview["changes"]} == {rel}

    skipped = _extract_archive(archive, repo)
    assert skipped == 1  # only the manifest is metadata-only
    assert (repo / rel).read_bytes() == payload


def test_inventory_may_target_legacy_repo_validator_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"#!/bin/sh\necho legacy validator\n"
    rel = ".devtool-artifact/validate.sh"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": rel, "action": "add", "sha256": _sha(payload), "size": len(payload)},
            ],
        },
    }
    archive = _overlay(tmp_path / "validator.zip", {rel: payload}, manifest)

    validation = validate_artifact(repo, archive)
    assert validation.ok, validation.messages
    skipped = _extract_archive(archive, repo)
    assert skipped == 1
    assert (repo / rel).read_bytes() == payload


def test_validation_script_metadata_stays_metadata_unless_inventory_declares_it(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    normal_payload = b"ok\n"
    script = b"#!/bin/sh\nexit 0\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": "payload.txt", "action": "add", "sha256": _sha(normal_payload), "size": len(normal_payload)},
            ],
        },
        "validation": {
            "script": {
                "path": ".devtool-artifact/validate.sh",
                "sha256": _sha(script),
                "runner": "bash",
            }
        },
    }
    archive = _overlay(
        tmp_path / "metadata-validator.zip",
        {"payload.txt": normal_payload, ".devtool-artifact/validate.sh": script},
        manifest,
    )

    validation = validate_artifact(repo, archive)
    assert validation.ok, validation.messages
    skipped = _extract_archive(archive, repo)
    assert skipped == 2  # manifest + metadata-only validation script
    assert (repo / "payload.txt").read_bytes() == normal_payload
    assert not (repo / ".devtool-artifact/validate.sh").exists()


def test_inventory_still_rejects_unapproved_artifact_metadata_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"nope\n"
    rel = ".devtool-artifact/private/state.json"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": rel, "action": "add", "sha256": _sha(payload), "size": len(payload)},
            ],
        },
    }
    archive = _overlay(tmp_path / "bad.zip", {rel: payload}, manifest)

    validation = validate_artifact(repo, archive)
    assert not validation.ok
    assert any("reserved artifact metadata" in message for message in validation.messages)


def test_inventory_still_rejects_devtool_repository_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"{}\n"
    rel = ".devtool/artifacts.json"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {"path": rel, "action": "add", "sha256": _sha(payload), "size": len(payload)},
            ],
        },
    }
    archive = _overlay(tmp_path / "bad-devtool.zip", {rel: payload}, manifest)

    validation = validate_artifact(repo, archive)
    assert not validation.ok
    assert any("reserved artifact metadata" in message for message in validation.messages)
