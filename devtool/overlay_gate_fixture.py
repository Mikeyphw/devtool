from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

from .artifact_base_contract import build_base_contract, path_source_sha256
from .overlay import inspect_patch_equivalence, rebase_overlay


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def _overlay(repo: Path, path: Path) -> None:
    incoming = b"one\ntwo\nthree incoming\n"
    entry = {
        "path": "fixture.txt",
        "action": "modify",
        "source_sha256": path_source_sha256(repo / "fixture.txt"),
        "sha256": hashlib.sha256(incoming).hexdigest(),
        "size": len(incoming),
    }
    manifest = {
        "schema_version": 2,
        "type": "overlay",
        "name": "git-frx06-gate-fixture",
        "inventory": {"format": 1, "entries": [entry]},
        "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative", "requires_clean_worktree": False, "commit": {"enabled": False}},
        "validation": {"required": False, "mode": "disabled"},
    }
    manifest.update(build_base_contract(repo, [entry], mode="touched-files"))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(".devtool-artifact.json", json.dumps(manifest, indent=2) + "\n")
        zf.writestr("fixture.txt", incoming)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="devtool-frx06-gate-") as tmp:
        root = Path(tmp)
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "devtool-gate@example.invalid")
        _git(repo, "config", "user.name", "Devtool Gate Fixture")
        (repo / "fixture.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "base")
        overlay = root / "fixture.zip"
        _overlay(repo, overlay)
        absent = inspect_patch_equivalence(repo, overlay)
        (repo / "fixture.txt").write_text("one current\ntwo\nthree\n", encoding="utf-8")
        _git(repo, "add", "fixture.txt")
        _git(repo, "commit", "-qm", "target")
        rebased = rebase_overlay(repo, overlay, output=root / "rebased.zip")
        payload = {
            "schema_version": 1,
            "roadmap_id": "GIT-FRX-06",
            "absent_status": absent.status,
            "source_patch_id": rebased.source_patch_id,
            "equivalence_before": rebased.equivalence_before,
            "rebased_artifact": bool(rebased.output),
            "validation_ok": rebased.validation_ok,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
