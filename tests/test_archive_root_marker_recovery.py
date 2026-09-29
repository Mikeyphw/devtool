
from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

from devtool.artifacts import preview_artifact, validate_artifact
from devtool.runners import _extract_archive
from devtool.transactions import rollback_transaction


def _manifest_for_file(path: str, data: bytes) -> dict:
    import hashlib

    return {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {
            "format": 1,
            "entries": [
                {
                    "action": "add",
                    "path": path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
            ],
        },
    }


def test_tar_root_directory_markers_are_skipped_during_extraction(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"ok\n"
    manifest = json.dumps(_manifest_for_file("payload.txt", payload)).encode("utf-8")
    archive = tmp_path / "root-marker-overlay.tar.gz"

    with tarfile.open(archive, "w:gz") as tf:
        for raw_name in ("", ".", "./"):
            info = tarfile.TarInfo(raw_name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tf.addfile(info)
        meta = tarfile.TarInfo(".devtool-artifact.json")
        meta.size = len(manifest)
        tf.addfile(meta, io.BytesIO(manifest))
        item = tarfile.TarInfo("payload.txt")
        item.size = len(payload)
        tf.addfile(item, io.BytesIO(payload))

    validation = validate_artifact(repo, archive)
    assert validation.ok, validation.messages
    preview = preview_artifact(repo, archive)
    assert [change["path"] for change in preview["changes"]] == ["payload.txt"]

    skipped = _extract_archive(archive, repo)

    assert skipped == 1  # only .devtool-artifact.json is metadata-only
    assert (repo / "payload.txt").read_bytes() == payload


def test_zip_root_directory_marker_is_skipped_during_extraction(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    payload = b"ok\n"
    archive = tmp_path / "root-marker-overlay.zip"
    manifest = json.dumps(_manifest_for_file("payload.txt", payload)).encode("utf-8")
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("./", b"")
        zf.writestr(".devtool-artifact.json", manifest)
        zf.writestr("payload.txt", payload)

    validation = validate_artifact(repo, archive)
    assert validation.ok, validation.messages

    skipped = _extract_archive(archive, repo)

    assert skipped == 1
    assert (repo / "payload.txt").read_bytes() == payload


def test_empty_non_directory_archive_member_still_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    archive = tmp_path / "empty-file-member.tar.gz"
    data = b"bad"
    with tarfile.open(archive, "w:gz") as tf:
        info = tarfile.TarInfo("")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    try:
        _extract_archive(archive, repo)
    except RuntimeError as exc:
        assert "Unsafe archive path: empty path" in str(exc)
    else:  # pragma: no cover - defensive assertion clarity
        raise AssertionError("empty non-directory member must be rejected")


def test_missing_transaction_json_rollback_is_unavailable_evidence(tmp_path: Path) -> None:
    missing = tmp_path / "transactions" / "repo-key" / "tx-id" / "transaction.json"

    result = rollback_transaction(missing, reason="repeat recovery after cleanup")

    assert result.returncode == 2
    assert result.failure_kind == "rollback_unavailable"
    assert result.metadata["rollback_receipt"]["state"] == "unavailable"
    assert result.metadata["rollback_receipt"]["verified"] is False
    assert "No rollback metadata found" in result.output
    assert "restored: 0" in result.output
