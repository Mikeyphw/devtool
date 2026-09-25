from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

from devtool.artifacts import validate_artifact, write_archive_entries
from devtool.artifact_base_contract import build_base_contract, path_source_sha256, validate_base_contract
from devtool.overlay.manifest import manifest_summary


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "roadmap@example.invalid")
    _git(root, "config", "user.name", "Roadmap Fixture")
    (root / "touched.txt").write_text("before\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("v1\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _overlay(root: Path, tmp_path: Path, *, mode: str = "touched-files", action: str = "modify", path: str = "touched.txt", after: bytes = b"after\n") -> Path:
    target = root / path
    if action == "add":
        source = None
    else:
        source = path_source_sha256(target)
    entry: dict[str, object] = {"path": path, "action": action}
    payload: dict[str, bytes] = {}
    if source is not None:
        entry["source_sha256"] = source
    if action != "delete":
        entry["sha256"] = _sha(after)
        entry["size"] = len(after)
        payload[path] = after
    entries = [entry]
    manifest: dict[str, object] = {
        "schema_version": 2,
        "type": "overlay",
        "name": "frx05-fixture",
        "inventory": {"format": 1, "entries": entries},
        "apply": {"atomic": True, "path_policy": "repository-relative", "conflict_policy": "fail"},
    }
    manifest.update(build_base_contract(root, entries, mode=mode))
    payload[".devtool-artifact.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    archive = tmp_path / f"{mode}-{action}.zip"
    return write_archive_entries(archive, payload)


def test_touched_files_accepts_unrelated_committed_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    (root / "unrelated.txt").write_text("v2\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    _git(root, "commit", "-qm", "unrelated")

    result = validate_artifact(root, overlay, check_source_state=True)
    assert result.ok, result.messages
    assert any("Base contract verified (touched-files)" in message for message in result.messages)



def test_touched_files_portable_across_equivalent_tree_with_different_commit(tmp_path: Path) -> None:
    source_root = _repo(tmp_path)
    overlay = _overlay(source_root, tmp_path)
    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        manifest = json.loads(zf.read(".devtool-artifact.json"))

    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    _git(other, "config", "user.email", "other@example.invalid")
    _git(other, "config", "user.name", "Other Fixture")
    (other / "touched.txt").write_text("before\n", encoding="utf-8")
    (other / "unrelated.txt").write_text("v1\n", encoding="utf-8")
    _git(other, "add", ".")
    _git(other, "commit", "-qm", "different metadata")
    assert _git(other, "rev-parse", "HEAD") != manifest["base_commit"]
    assert _git(other, "rev-parse", "HEAD^{tree}") == manifest["base_tree"]
    result = validate_artifact(other, overlay, check_source_state=True)
    assert result.ok, result.messages
    assert any("base_commit is unavailable locally" in message for message in result.messages)

def test_exact_tree_rejects_unrelated_commit_drift(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path, mode="exact-tree")
    (root / "unrelated.txt").write_text("v2\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    _git(root, "commit", "-qm", "unrelated")

    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("exact-tree base_commit mismatch" in message for message in result.messages)


def test_exact_tree_rejects_dirty_worktree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path, mode="exact-tree")
    (root / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert "exact-tree base contract refuses a dirty worktree." in result.messages


def test_touched_file_content_drift_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    (root / "touched.txt").write_text("drift\n", encoding="utf-8")
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("Touched path content changed" in message or "Source SHA-256 mismatch" in message for message in result.messages)


def test_touched_file_mode_drift_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    target = root / "touched.txt"
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("Touched path mode changed" in message for message in result.messages)


def test_touched_symlink_target_drift_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "a.txt").write_text("same\n", encoding="utf-8")
    (root / "b.txt").write_text("same\n", encoding="utf-8")
    os.symlink("a.txt", root / "link.txt")
    _git(root, "add", "a.txt", "b.txt", "link.txt")
    _git(root, "commit", "-qm", "symlink base")
    overlay = _overlay(root, tmp_path, action="delete", path="link.txt")
    (root / "link.txt").unlink()
    os.symlink("b.txt", root / "link.txt")

    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("symlink target changed" in message or "Source SHA-256 mismatch" in message for message in result.messages)


def test_touched_type_drift_fails_closed(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    (root / "touched.txt").unlink()
    os.symlink("unrelated.txt", root / "touched.txt")
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("Touched path type changed" in message for message in result.messages)


def test_touched_add_requires_path_to_remain_absent(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path, action="add", path="new.txt", after=b"new\n")
    (root / "new.txt").write_text("someone else\n", encoding="utf-8")
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("Touched add path drifted from missing state" in message or "add conflicts" in message for message in result.messages)


def test_touched_delete_requires_path_to_remain_present(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path, action="delete")
    (root / "touched.txt").unlink()
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("expects existing file or symlink" in message or "type changed" in message for message in result.messages)


def test_expected_post_hash_must_match_payload(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        manifest = json.loads(zf.read(".devtool-artifact.json"))
        payload = {name: zf.read(name) for name in zf.namelist() if name != ".devtool-artifact.json"}
    manifest["expected_post_sha256"]["touched.txt"] = "0" * 64
    payload[".devtool-artifact.json"] = (json.dumps(manifest) + "\n").encode()
    write_archive_entries(overlay, payload)
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("expected_post_sha256['touched.txt'] must match" in message for message in result.messages)


def test_contract_shape_requires_exact_touched_inventory_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        manifest = json.loads(zf.read(".devtool-artifact.json"))
        payload = {name: zf.read(name) for name in zf.namelist() if name != ".devtool-artifact.json"}
    manifest["touched_paths"].append("unrelated.txt")
    manifest["source_sha256"]["unrelated.txt"] = path_source_sha256(root / "unrelated.txt")
    manifest["expected_post_sha256"]["unrelated.txt"] = None
    payload[".devtool-artifact.json"] = (json.dumps(manifest) + "\n").encode()
    write_archive_entries(overlay, payload)
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert "touched_paths must exactly match the artifact inventory paths." in result.messages


def test_base_tree_must_belong_to_base_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    import zipfile
    with zipfile.ZipFile(overlay) as zf:
        manifest = json.loads(zf.read(".devtool-artifact.json"))
        payload = {name: zf.read(name) for name in zf.namelist() if name != ".devtool-artifact.json"}
    (root / "unrelated.txt").write_text("other tree\n", encoding="utf-8")
    _git(root, "add", "unrelated.txt")
    _git(root, "commit", "-qm", "other tree")
    manifest["base_tree"] = _git(root, "rev-parse", "HEAD^{tree}")
    payload[".devtool-artifact.json"] = (json.dumps(manifest) + "\n").encode()
    write_archive_entries(overlay, payload)
    result = validate_artifact(root, overlay, check_source_state=True)
    assert not result.ok
    assert any("base_tree does not match base_commit" in message for message in result.messages)


def test_legacy_inventory_without_base_contract_remains_valid(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    before = (root / "touched.txt").read_bytes()
    after = b"after\n"
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "inventory": {"format": 1, "entries": [{
            "path": "touched.txt", "action": "modify", "source_sha256": _sha(before),
            "sha256": _sha(after), "size": len(after),
        }]},
    }
    overlay = write_archive_entries(tmp_path / "legacy.zip", {
        "touched.txt": after,
        ".devtool-artifact.json": (json.dumps(manifest) + "\n").encode(),
    })
    assert validate_artifact(root, overlay, check_source_state=True).ok


def test_manifest_summary_exposes_base_contract_identity(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    overlay = _overlay(root, tmp_path)
    summary = manifest_summary(overlay)
    contract = summary["base_contract"]
    assert contract["mode"] == "touched-files"
    assert contract["base_commit"] == _git(root, "rev-parse", "HEAD")
    assert contract["base_tree"] == _git(root, "rev-parse", "HEAD^{tree}")
    assert contract["touched_paths"] == ["touched.txt"]
