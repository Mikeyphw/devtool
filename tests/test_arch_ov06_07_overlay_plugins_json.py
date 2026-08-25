from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

from devtool.artifacts import read_artifact_manifest, validate_artifact
from devtool.cli import build_parser, main
from devtool.overlay import check_overlay, rebase_overlay
from devtool.plugins import discover_plugins, doctor_plugin


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_overlay(path: Path, *, source: bytes, incoming: bytes, commit: bool = False) -> None:
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "test-overlay",
        "inventory": {
            "format": 1,
            "entries": [{
                "path": "file.txt",
                "action": "modify",
                "source_sha256": _sha(source),
                "sha256": _sha(incoming),
                "size": len(incoming),
            }],
        },
        "apply": {
            "atomic": True,
            "conflict_policy": "fail",
            "path_policy": "repository-relative",
            "requires_clean_worktree": False,
            "commit": {
                "enabled": commit,
                "strategy": "single",
                "message": "test rebase commit" if commit else "",
            },
        },
        "validation": {"required": False, "mode": "disabled"},
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(".devtool-artifact.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("file.txt", incoming)


def _git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Devtool Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=path, check=True)


def test_overlay_check_reports_rebaseable_source_conflict(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.txt").write_bytes(b"current\n")
    overlay = tmp_path / "overlay.zip"
    _write_overlay(overlay, source=b"old\n", incoming=b"new\n")

    result = check_overlay(repo, overlay)
    assert result.ok is False
    assert result.structural_ok is True
    assert result.rebaseable is True
    assert [item.kind for item in result.conflicts] == ["source-sha-mismatch"]


def test_rebase_overlay_updates_source_hash_and_validates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    current = b"current\n"
    (repo / "file.txt").write_bytes(current)
    overlay = tmp_path / "overlay.zip"
    _write_overlay(overlay, source=b"old\n", incoming=b"new\n")

    result = rebase_overlay(repo, overlay)
    rebased = Path(result.output)
    assert rebased.is_file()
    manifest = read_artifact_manifest(rebased)
    entry = manifest["inventory"]["entries"][0]
    assert entry["source_sha256"] == _sha(current)
    assert validate_artifact(repo, rebased).ok is True


def test_apply_overlay_rebase_applies_stale_inventory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "file.txt").write_bytes(b"current\n")
    _git_repo(repo)
    overlay = tmp_path / "overlay.zip"
    _write_overlay(overlay, source=b"old\n", incoming=b"new\n")

    rc = main(["-y", "-r", str(repo), "apply", "overlay", str(overlay), "--rebase", "--no-validate", "--no-commit"])
    assert rc == 0
    assert (repo / "file.txt").read_bytes() == b"new\n"


def test_plugin_registry_exposes_builtins(tmp_path: Path) -> None:
    plugins, errors = discover_plugins()
    assert not errors
    names = {item.name for item in plugins}
    assert {"android", "python", "rust", "magisk"}.issubset(names)
    payload = doctor_plugin("python", repo_root=tmp_path)
    assert payload["plugin"]["name"] == "python"
    assert isinstance(payload["checks"], list)


def test_parser_has_overlay_and_plugin_commands() -> None:
    parser = build_parser()
    args = parser.parse_args(["overlay", "check", "x.zip"])
    assert args.command == "overlay"
    assert args.overlay_command == "check"
    args = parser.parse_args(["plugin", "doctor", "android"])
    assert args.command == "plugin"
    assert args.plugin_command == "doctor"


def test_universal_json_wraps_commands(tmp_path: Path, capsys) -> None:
    rc = main(["--json", "-r", str(tmp_path), "plugin", "list"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["command"] == "plugin"
    assert payload["data"]["plugins"]
    assert payload["stderr"] == ""


def test_completion_exposes_new_surfaces() -> None:
    from devtool.completion import ZSH_COMPLETION

    assert "overlay:preflight and rebase overlay artifacts" in ZSH_COMPLETION
    assert "plugin:discover and diagnose devtool plugins" in ZSH_COMPLETION
    assert "--rebase[rebase a single overlay inventory" in ZSH_COMPLETION
    assert "--json[emit a universal machine-readable JSON invocation envelope]" in ZSH_COMPLETION
