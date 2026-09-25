from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from devtool.artifact_base_contract import build_base_contract, path_source_sha256
from devtool.artifacts import read_artifact_manifest, validate_artifact
from devtool.cli import build_parser, main
from devtool.overlay import OverlayRebaseConflict, inspect_patch_equivalence, rebase_overlay


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Devtool Test")
    (repo / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (repo / "b.txt").write_text("base-b\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def _overlay(repo: Path, path: Path, changes: dict[str, bytes | None]) -> Path:
    entries: list[dict[str, object]] = []
    payload: dict[str, bytes] = {}
    for rel, data in changes.items():
        target = repo / rel
        if data is None:
            entries.append({"path": rel, "action": "delete", "source_sha256": path_source_sha256(target)})
        elif target.exists():
            entries.append({
                "path": rel,
                "action": "modify",
                "source_sha256": path_source_sha256(target),
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            })
            payload[rel] = data
        else:
            entries.append({"path": rel, "action": "add", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            payload[rel] = data
    manifest: dict[str, object] = {
        "schema_version": 2,
        "type": "overlay",
        "name": "frx06-fixture",
        "inventory": {"format": 1, "entries": entries},
        "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative", "requires_clean_worktree": False, "commit": {"enabled": False}},
        "validation": {"required": False, "mode": "disabled"},
    }
    manifest.update(build_base_contract(repo, entries, mode="touched-files"))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(".devtool-artifact.json", json.dumps(manifest, indent=2) + "\n")
        for rel, data in payload.items():
            zf.writestr(rel, data)
    return path


def test_status_absent_and_patch_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"one\ntwo changed\nthree\n"})
    status = inspect_patch_equivalence(repo, overlay)
    assert status.status == "absent"
    assert status.overlay_patch_id
    assert status.current_patch_id == ""


def test_status_fully_present_uses_patch_equivalence(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    incoming = b"one\ntwo changed\nthree\n"
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": incoming})
    (repo / "a.txt").write_bytes(incoming)
    status = inspect_patch_equivalence(repo, overlay)
    assert status.status == "fully-present"
    assert status.overlay_patch_id == status.current_patch_id


def test_status_partially_present(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"A\n", "b.txt": b"B\n"})
    (repo / "a.txt").write_bytes(b"A\n")
    status = inspect_patch_equivalence(repo, overlay)
    assert status.status == "partially-present"
    assert {p.state for p in status.paths} == {"present", "absent"}


def test_status_ambiguous_for_divergent_touched_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"one\ntwo incoming\nthree\n"})
    (repo / "a.txt").write_text("one\ntwo current\nthree\n", encoding="utf-8")
    assert inspect_patch_equivalence(repo, overlay).status == "ambiguous"


def test_three_way_rebase_merges_non_overlapping_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"one\ntwo\nthree incoming\n"})
    (repo / "a.txt").write_text("one current\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "current drift")
    result = rebase_overlay(repo, overlay, output=tmp_path / "rebased.zip")
    assert not result.noop
    assert result.equivalence_before == "ambiguous"
    assert result.source_patch_id
    manifest = read_artifact_manifest(result.output)
    assert manifest["rebase"]["source_artifact_sha256"]
    assert manifest["rebase"]["source_patch_id"] == result.source_patch_id
    with zipfile.ZipFile(result.output) as zf:
        merged = zf.read("a.txt")
    assert merged == b"one current\ntwo\nthree incoming\n"
    assert validate_artifact(repo, result.output, artifact_type="overlay", check_source_state=True).ok


def test_partial_rebase_drops_already_present_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"A\n", "b.txt": b"B\n"})
    (repo / "a.txt").write_bytes(b"A\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "partial")
    result = rebase_overlay(repo, overlay, output=tmp_path / "rebased.zip")
    manifest = read_artifact_manifest(result.output)
    assert [e["path"] for e in manifest["inventory"]["entries"]] == ["b.txt"]
    assert manifest["rebase"]["equivalence_before"] == "partially-present"


def test_fully_present_rebase_is_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"A\n"})
    (repo / "a.txt").write_bytes(b"A\n")
    result = rebase_overlay(repo, overlay)
    assert result.noop is True
    assert result.output == ""
    assert result.equivalence_before == "fully-present"


def test_conflict_emits_evidence_artifact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"one\nincoming\nthree\n"})
    (repo / "a.txt").write_text("one\ncurrent\nthree\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "conflict drift")
    with pytest.raises(OverlayRebaseConflict) as exc:
        rebase_overlay(repo, overlay, output=tmp_path / "rebased.zip")
    conflict = Path(exc.value.conflict_artifact)
    assert conflict.is_file()
    with zipfile.ZipFile(conflict) as zf:
        evidence = json.loads(zf.read(".devtool-conflicts.json"))
        assert evidence["type"] == "devtool-overlay-rebase-conflicts"
        assert evidence["conflicts"][0]["kind"] == "content-conflict"
        assert any(name.endswith(".merge") for name in zf.namelist())


def test_apply_rebase_fully_present_skips_without_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"A\n"})
    (repo / "a.txt").write_bytes(b"A\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "already present")
    before = _git(repo, "rev-parse", "HEAD")
    rc = main(["-y", "-r", str(repo), "apply-overlay", str(overlay), "--rebase", "--no-validate", "--commit-mode", "new", "--commit", "should not happen"])
    assert rc == 0
    assert _git(repo, "rev-parse", "HEAD") == before


def test_cli_exposes_overlay_status() -> None:
    args = build_parser().parse_args(["overlay", "status", "x.zip"])
    assert args.command == "overlay"
    assert args.overlay_command == "status"


def test_cli_status_json_reports_equivalence(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"A\n"})
    rc = main(["--json", "-r", str(repo), "overlay", "status", str(overlay)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    data = payload.get("data", payload)
    assert data["status"] == "absent"
    assert data["overlay_patch_id"]


def test_apply_rebase_performs_clean_three_way_merge(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    overlay = _overlay(repo, tmp_path / "o.zip", {"a.txt": b"one\ntwo\nthree incoming\n"})
    (repo / "a.txt").write_text("one current\ntwo\nthree\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "target drift")
    rc = main(["-y", "-r", str(repo), "apply-overlay", str(overlay), "--rebase", "--no-validate", "--no-commit"])
    assert rc == 0
    assert (repo / "a.txt").read_bytes() == b"one current\ntwo\nthree incoming\n"
