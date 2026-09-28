from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping, Sequence
import zipfile

from devtool.models import CommandSpec, OperationResult, ProjectConfig
from devtool.validation_sessions import create_session


@dataclass
class RepoFactory:
    root: Path
    _counter: int = 0

    def create(self, name: str = "repo", *, files: Mapping[str, str | bytes] | None = None) -> Path:
        self._counter += 1
        path = self.root / f"{name}-{self._counter:03d}"
        path.mkdir(parents=True, exist_ok=False)
        for relative, payload in dict(files or {}).items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, bytes):
                target.write_bytes(payload)
            else:
                target.write_text(str(payload), encoding="utf-8")
        return path


@dataclass
class GitRepoFactory(RepoFactory):
    def create(self, name: str = "git-repo", *, files: Mapping[str, str | bytes] | None = None, initial_commit: bool = True) -> Path:
        path = super().create(name, files=files)
        self._git(path, "init", "-q")
        self._git(path, "config", "user.name", "Devtool Test")
        self._git(path, "config", "user.email", "devtool-tests@example.invalid")
        if initial_commit:
            if not any(path.iterdir()):
                (path / ".keep").write_text("\n", encoding="utf-8")
            self.commit(path, "initial")
        return path

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    def commit(self, repo: Path, message: str = "test commit") -> str:
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "--allow-empty", "-m", message)
        return self._git(repo, "rev-parse", "HEAD")

    def branch(self, repo: Path, name: str, *, checkout: bool = False) -> None:
        self._git(repo, "checkout" if checkout else "branch", *( ["-b", name] if checkout else [name] ))

    def worktree(self, repo: Path, name: str, *, branch: str = "") -> Path:
        target = self.root / f"worktree-{name}-{self._counter:03d}"
        args = ["worktree", "add", "-q"]
        if branch:
            args.extend(["-b", branch])
        args.append(str(target))
        self._git(repo, *args)
        return target


@dataclass
class ValidationSessionFactory:
    root: Path
    _counter: int = 0

    def create(self, specs: Sequence[CommandSpec] | None = None, *, persist: bool = False):
        self._counter += 1
        repo = self.root / f"validation-repo-{self._counter:03d}"
        (repo / "tests").mkdir(parents=True)
        (repo / "src").mkdir(parents=True)
        (repo / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "tests" / "test_app.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
        (repo / ".devtool.toml").write_text("[project]\nkind='python'\n", encoding="utf-8")
        config = ProjectConfig(repo_root=repo, project_root=str(repo), kind="python", runner_options={})
        selected = list(specs or [CommandSpec("lint", ["true"], metadata={"validation_phase_name": "lint"})])
        return create_session(config, selected, persist=persist)


@dataclass
class ArtifactFixtureBuilder:
    root: Path
    _counter: int = 0

    @staticmethod
    def _sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _single_artifact(
        self,
        files: Mapping[str, str | bytes],
        *,
        name: str,
        target: str,
        artifact_type: str,
    ) -> Path:
        self._counter += 1
        normalized: dict[str, bytes] = {}
        for relative, payload in sorted(files.items()):
            normalized[str(relative)] = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
        entries = [
            {"path": path, "action": "add", "sha256": self._sha(data), "size": len(data)}
            for path, data in normalized.items()
        ]
        manifest = {
            "schema_version": 2,
            "type": artifact_type,
            "name": name,
            "target": target,
            "base_contract_mode": "touched-files",
            "touched_paths": list(normalized),
            "expected_post_sha256": {path: self._sha(data) for path, data in normalized.items()},
            "inventory": {"format": 1, "entries": entries},
            "apply": {"atomic": True, "conflict_policy": "fail", "path_policy": "repository-relative", "requires_clean_worktree": False},
            "validation": {"mode": "disabled", "required": False},
        }
        output = self.root / f"{name}-{self._counter:03d}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            self._write(archive, ".devtool-artifact.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
            for path, data in normalized.items():
                self._write(archive, path, data)
        return output

    def overlay(self, files: Mapping[str, str | bytes], *, name: str = "fixture-overlay", target: str = "devtool") -> Path:
        return self._single_artifact(files, name=name, target=target, artifact_type="overlay")

    def snapshot(self, files: Mapping[str, str | bytes], *, name: str = "fixture-snapshot", target: str = "devtool") -> Path:
        return self._single_artifact(files, name=name, target=target, artifact_type="snapshot")

    def bundle(self, overlays: Sequence[Path], *, name: str = "fixture-bundle") -> Path:
        self._counter += 1
        rows: list[dict[str, Any]] = []
        payloads: list[tuple[str, bytes]] = []
        for index, path in enumerate(overlays, 1):
            data = Path(path).read_bytes()
            arcname = f"overlays/{index:03d}_{Path(path).name}"
            payloads.append((arcname, data))
            rows.append({
                "id": Path(path).stem,
                "path": arcname,
                "label": Path(path).stem,
                "required": True,
                "sha256": self._sha(data),
                "size": len(data),
            })
        manifest = {
            "schema_version": 2,
            "type": "overlay_bundle",
            "kind": "devtool.overlay_bundle",
            "name": name,
            "target_mode": "per_overlay",
            "overlays": rows,
            "apply_order": rows,
            "apply": {
                "atomic": True,
                "conflict_policy": "fail",
                "requires_clean_worktree": False,
                "commit": {"enabled": False, "strategy": "single", "message": ""},
            },
            "validation": {"required": False, "strategy": "per-overlay", "rollback_on_failure": True, "failure_action": "pause"},
        }
        output = self.root / f"{name}-{self._counter:03d}.zip"
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            self._write(archive, "devtool-bundle.json", (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode())
            for arcname, data in payloads:
                self._write(archive, arcname, data)
        return output

    @staticmethod
    def _write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
        info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, data, compresslevel=9)


@dataclass
class StableRepositoryBoundary:
    """Pure repository-state fake for unit/contract tests that do not need Git."""

    head: str = "deadbeef"
    branch: str = "main"
    dirty: bool = False
    tracked_paths: tuple[str, ...] = ()
    calls: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        self.calls.append("snapshot")
        return {
            "head": self.head,
            "branch": self.branch,
            "dirty": self.dirty,
            "tracked_paths": list(self.tracked_paths),
        }


@dataclass
class StableCommandBoundary:
    """Behavior-level fake: records only stable command boundary inputs."""

    returncode: int = 0
    output: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)

    def run(self, argv: Sequence[str], *, cwd: str | Path | None = None, env: Mapping[str, str] | None = None) -> OperationResult:
        self.calls.append({
            "argv": tuple(str(value) for value in argv),
            "cwd": str(cwd or ""),
            "env": dict(env or {}),
        })
        return OperationResult("stable-boundary", list(argv), self.returncode, 0.0, self.output, [])
