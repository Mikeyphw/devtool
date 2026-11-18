"""Native RUN-V3 writer privacy regressions for G0's full Python regression.

These are positive and negative boundary tests; a permissive XDG authority
must remain a hard failure, not be repaired with chmod after source creation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from devtool.android.evidence import AndroidReleaseEvidenceSession
from devtool.core_runtime import private_run_root
from devtool.repository_pack import RepositoryPackReceipt, _persist_repository_pack_receipt
from devtool.storage_layout import StorageLayoutError, ensure_private_storage_dir
from tests._rmp_c02_durable_fixture import attach_durable_apply_evidence


@pytest.fixture
def private_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Explicitly isolate the authority outside the target repository: XDG
    # directories within the target are forbidden by the canonical layout.
    repo = tmp_path / "repo"
    repo.mkdir()
    root = tmp_path.parent / ("." + tmp_path.name + "-native-writers")
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(root))
    return repo


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _check_private_chain(root: Path, path: Path) -> None:
    assert path.is_relative_to(root)
    assert _mode(root) == 0o700
    for parent in (path, *path.parents):
        if parent == root.parent:
            break
        assert not parent.is_symlink()
        if parent.is_dir() and parent != root.parent:
            assert _mode(parent) == 0o700, (parent, oct(_mode(parent)))


def test_android_release_seals_canonical_evidence_in_private_run_authority(private_repo: Path) -> None:
    session = AndroidReleaseEvidenceSession(private_repo, target="android", variant="debug")
    session.add_gate("python-unit", "passed")
    canonical = session.seal()
    root = private_run_root(private_repo)
    assert canonical.is_file()
    assert json.loads(canonical.read_text())["verdict"] == "passed"
    assert _mode(canonical) == 0o600
    _check_private_chain(root, canonical.parent)
    with pytest.raises(FileExistsError):
        session.seal()


def test_android_export_stays_identical_to_canonical_bytes(private_repo: Path) -> None:
    export = private_repo.parent / "public-evidence" / "report.json"
    session = AndroidReleaseEvidenceSession(private_repo, target="android", variant="debug")
    session.add_gate("python-unit", "passed")
    assert session.seal(export) == export
    canonical = Path(session.storage["canonical_path"])
    assert canonical.is_file() and canonical.read_bytes() == export.read_bytes()
    assert _mode(canonical) == 0o600
    _check_private_chain(private_run_root(private_repo), canonical.parent)


def test_private_writer_does_not_chmod_unsafe_existing_repository_authority(private_repo: Path) -> None:
    base = private_run_root(private_repo)
    ensure_private_storage_dir(base.parent)
    base.parent.chmod(0o755)
    session = AndroidReleaseEvidenceSession(private_repo, target="android", variant="debug")
    session.add_gate("python-unit", "passed")
    try:
        with pytest.raises(StorageLayoutError, match="private 0700"):
            session.seal()
        assert _mode(base.parent) == 0o755, "never silently chmod an unsafe authority"
    finally:
        base.parent.chmod(0o700)


def test_repo_pack_receipt_and_catalog_have_private_permissions(private_repo: Path) -> None:
    receipt = RepositoryPackReceipt(
        receipt_id="receipt:private-test", pack_id="pack:private-test", plan_id="plan:private-test",
        output=str(private_repo.parent / "output.zip"), sha256="a" * 64,
        size=9, verified=True, published=True, pin=False, retention="default",
    )
    reference = _persist_repository_pack_receipt(private_repo, receipt)
    assert reference
    catalog_root = private_run_root(private_repo)
    evidence = list((catalog_root / "evidence").rglob("*.json"))
    receipts = [path for path in evidence if path.name == "receipt-private-test.json"]
    assert len(receipts) == 1
    assert _mode(receipts[0]) == 0o600
    _check_private_chain(catalog_root, receipts[0].parent)


def test_synthetic_cleanup_evidence_fixture_does_not_create_public_run_dirs(private_repo: Path) -> None:
    artifact = private_repo.parent / "owned-artifact.zip"
    artifact.write_bytes(b"synthetic-fixture")
    result = SimpleNamespace(metadata={"artifact_path": str(artifact)})
    attach_durable_apply_evidence(private_repo, result)
    root = private_run_root(private_repo)
    assert result.metadata.get("run_id")
    _check_private_chain(root, root / result.metadata["run_id"] / "artifacts")
