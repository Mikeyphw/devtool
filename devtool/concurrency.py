from __future__ import annotations

import atexit
import contextlib
import fcntl
import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

from .models import CommandSpec, ProjectConfig


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_home() -> Path:
    override = os.environ.get("DEVTOOL_XDG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    if runtime:
        return Path(runtime).expanduser() / "devtool"
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    if state:
        return Path(state).expanduser() / "devtool"
    return Path.home() / ".local" / "state" / "devtool"


def _repo_key(repo_root: Path) -> str:
    resolved = str(repo_root.resolve())
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", repo_root.resolve().name or "repo").strip(".-") or "repo"
    digest = hashlib.sha256(resolved.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{slug}-{digest}"


def lease_root(repo_root: Path) -> Path:
    return _state_home() / "locks" / _repo_key(repo_root)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _process_start_token(pid: int) -> str:
    if pid <= 0:
        return ""
    stat = Path(f"/proc/{pid}/stat")
    try:
        fields = stat.read_text(encoding="utf-8", errors="replace").split()
        return fields[21] if len(fields) > 21 else ""
    except OSError:
        return ""


def process_is_alive(pid: int, start_token: str = "") -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    current = _process_start_token(pid)
    return not start_token or not current or current == start_token


def _normalize_path(value: str | Path) -> str:
    text = str(value).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text or text == ".":
        return "@repo"
    parts = PurePosixPath(text).parts
    if ".." in parts:
        raise ValueError(f"Unsafe concurrency path: {value}")
    return str(PurePosixPath(*parts))


def _path_overlap(left: str, right: str) -> bool:
    if left.startswith("@") or right.startswith("@"):
        return left == right or left == "@repo" or right == "@repo"
    a = PurePosixPath(left).parts
    b = PurePosixPath(right).parts
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def _scopes_conflict(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, list[str]]:
    conflicts: list[str] = []
    left_reads = [_normalize_path(item) for item in left.get("reads", [])]
    left_writes = [_normalize_path(item) for item in left.get("writes", [])]
    right_reads = [_normalize_path(item) for item in right.get("reads", [])]
    right_writes = [_normalize_path(item) for item in right.get("writes", [])]
    for a in left_writes:
        for b in right_reads + right_writes:
            if _path_overlap(a, b):
                conflicts.append(f"{a} ↔ {b}")
    for a in left_reads:
        for b in right_writes:
            if _path_overlap(a, b):
                conflicts.append(f"{a} ↔ {b}")
    return bool(conflicts), list(dict.fromkeys(conflicts))


@dataclass(slots=True)
class LeaseConflict:
    run_id: str
    pid: int
    command: str
    phase: str
    paths: list[str]
    started_at: str


@dataclass(slots=True)
class ConcurrencyLease:
    repo_root: Path
    command: str
    reads: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    phase: str = "run"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lease_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timeout: float = 300.0
    heartbeat_interval: float = 5.0
    wait_callback: Callable[[list[LeaseConflict]], None] | None = None
    _path: Path | None = None
    _started_at: str = ""
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    last_conflicts: list[LeaseConflict] = field(default_factory=list)

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "lease_id": self.lease_id,
            "pid": os.getpid(),
            "process_start": _process_start_token(os.getpid()),
            "repo_root": str(self.repo_root.resolve()),
            "command": self.command,
            "phase": self.phase,
            "reads": list(dict.fromkeys(_normalize_path(item) for item in self.reads)),
            "writes": list(dict.fromkeys(_normalize_path(item) for item in self.writes)),
            "started_at": self._started_at or _utc_now(),
            "heartbeat_at": _utc_now(),
        }

    def acquire(self) -> "ConcurrencyLease":
        root = lease_root(self.repo_root)
        leases_dir = root / "leases"
        leases_dir.mkdir(parents=True, exist_ok=True)
        registry = root / "registry.lock"
        registry.touch(exist_ok=True)
        deadline = time.monotonic() + max(0.0, float(self.timeout))
        self._started_at = _utc_now()
        notified = False
        while True:
            conflicts: list[LeaseConflict] = []
            with registry.open("a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                for path in list(leases_dir.glob("*.json")):
                    try:
                        data = json.loads(path.read_text(encoding="utf-8"))
                    except Exception:
                        path.unlink(missing_ok=True)
                        continue
                    pid = int(data.get("pid", 0) or 0)
                    if not process_is_alive(pid, str(data.get("process_start") or "")):
                        path.unlink(missing_ok=True)
                        continue
                    if str(data.get("run_id") or "") == self.run_id:
                        continue
                    conflict, paths = _scopes_conflict(self._payload(), data)
                    if conflict:
                        conflicts.append(
                            LeaseConflict(
                                run_id=str(data.get("run_id") or path.stem),
                                pid=pid,
                                command=str(data.get("command") or "devtool"),
                                phase=str(data.get("phase") or "run"),
                                paths=paths[:12],
                                started_at=str(data.get("started_at") or ""),
                            )
                        )
                if not conflicts:
                    self._path = leases_dir / f"{self.lease_id}.json"
                    _atomic_write_json(self._path, self._payload())
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    break
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            self.last_conflicts = conflicts
            if not notified and self.wait_callback is not None:
                with contextlib.suppress(Exception):
                    self.wait_callback(conflicts)
                notified = True
            if self.timeout <= 0 or time.monotonic() >= deadline:
                detail = "; ".join(
                    f"{item.command} pid={item.pid}: {', '.join(item.paths[:3])}" for item in conflicts[:3]
                )
                raise TimeoutError(f"Conflicting devtool run is using the requested files. {detail}")
            time.sleep(0.2)
        self._thread = threading.Thread(target=self._heartbeat_loop, name=f"devtool-lease-{self.run_id[:8]}", daemon=True)
        self._thread.start()
        atexit.register(self.release)
        return self

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(max(1.0, self.heartbeat_interval)):
            path = self._path
            if path is None or not path.exists():
                return
            with contextlib.suppress(Exception):
                payload = self._payload()
                _atomic_write_json(path, payload)

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        if self._path is not None:
            with contextlib.suppress(Exception):
                _atomic_write_json(self._path, self._payload())

    def release(self) -> None:
        self._stop.set()
        path, self._path = self._path, None
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    def __enter__(self) -> "ConcurrencyLease":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.release()


def _repo_relative(repo_root: Path, value: str | Path) -> str | None:
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    except Exception:
        return None


def _solution_projects(repo_root: Path, solution: Path) -> list[Path]:
    projects: list[Path] = []
    try:
        text = solution.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return projects
    for match in re.finditer(r'^Project\([^\n]+?\)\s*=\s*"[^"]+",\s*"([^"]+\.(?:csproj|fsproj|vbproj))"', text, re.MULTILINE | re.IGNORECASE):
        candidate = (solution.parent / match.group(1).replace("\\", "/")).resolve()
        try:
            candidate.relative_to(repo_root.resolve())
        except ValueError:
            continue
        projects.append(candidate)
    return projects


def _project_references(project: Path) -> list[Path]:
    try:
        text = project.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    refs: list[Path] = []
    for match in re.finditer(r'<ProjectReference\b[^>]*\bInclude=["\']([^"\']+)["\']', text, re.IGNORECASE):
        refs.append((project.parent / match.group(1).replace("\\", "/")).resolve())
    return refs


def _dotnet_projects_for_specs(repo_root: Path, specs: Sequence[CommandSpec]) -> list[Path]:
    queue: list[Path] = []
    for spec in specs:
        argv = list(spec.metadata.get("progress_argv") or spec.argv)
        for index, item in enumerate(argv):
            if Path(item).name == "dotnet" and index + 2 < len(argv):
                target = Path(argv[index + 2])
                if not target.is_absolute():
                    target = repo_root / target
                target = target.resolve()
                if target.suffix.lower() == ".sln":
                    queue.extend(_solution_projects(repo_root, target))
                elif target.suffix.lower() in {".csproj", ".fsproj", ".vbproj"}:
                    queue.append(target)
    seen: set[Path] = set()
    projects: list[Path] = []
    while queue:
        project = queue.pop(0)
        if project in seen:
            continue
        seen.add(project)
        try:
            project.relative_to(repo_root.resolve())
        except ValueError:
            continue
        projects.append(project)
        queue.extend(_project_references(project))
    return projects


def scope_for_specs(config: ProjectConfig, specs: Sequence[CommandSpec]) -> tuple[list[str], list[str]]:
    repo_root = config.repo_root.resolve()
    projects = _dotnet_projects_for_specs(repo_root, specs)
    if not projects:
        # Explicit/generic commands cannot be safely inspected. Serialize them.
        return [], ["@repo"]
    reads: list[str] = []
    writes: list[str] = []
    for project in projects:
        rel_dir = _repo_relative(repo_root, project.parent)
        rel_project = _repo_relative(repo_root, project)
        if rel_dir:
            reads.append(rel_dir)
            writes.extend([f"{rel_dir}/bin", f"{rel_dir}/obj", f"{rel_dir}/TestResults"])
        if rel_project:
            reads.append(rel_project)
        current = project.parent
        while True:
            for name in ("Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props", "NuGet.config", "global.json"):
                candidate = current / name
                if candidate.exists():
                    rel = _repo_relative(repo_root, candidate)
                    if rel:
                        reads.append(rel)
            if current == repo_root or repo_root not in current.parents:
                break
            current = current.parent
    return list(dict.fromkeys(reads)), list(dict.fromkeys(writes))


def scope_for_artifact_preview(preview: dict[str, Any], *, snapshot: bool = False) -> tuple[list[str], list[str]]:
    if snapshot:
        return [], ["@repo"]
    writes: list[str] = []
    for change in preview.get("changes", []) or []:
        if not isinstance(change, dict) or str(change.get("kind") or "") == "unchanged":
            continue
        raw = str(change.get("path") or "").strip()
        if raw:
            writes.append(_normalize_path(raw))
    return [], list(dict.fromkeys(writes))


def merge_scopes(*scopes: tuple[Iterable[str], Iterable[str]]) -> tuple[list[str], list[str]]:
    reads: list[str] = []
    writes: list[str] = []
    for scope_reads, scope_writes in scopes:
        reads.extend(scope_reads)
        writes.extend(scope_writes)
    return list(dict.fromkeys(_normalize_path(item) for item in reads)), list(dict.fromkeys(_normalize_path(item) for item in writes))
