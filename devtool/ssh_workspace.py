from __future__ import annotations

"""Managed SSH workspace planning and incremental repository synchronization.

SSHW-02 keeps the local repository authoritative.  A named SSH worker may use
an existing checkout, a persistent Devtool-managed mirror, or an ephemeral
mirror.  Managed paths are guarded by a workspace marker and a canonical-root
check before any rsync operation that can delete remote files.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any, Iterable

from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_base_argv, build_ssh_remote_sh_argv
from .ssh_worker import SSHWorkerProbe, probe_worker
from .ssh_multiplex import disable_for_operation, multiplex_failure, strip_multiplex_from_shell, run_with_multiplex_fallback
from .ssh_transport import SSHTransferTelemetry, federation_transport_identity, run_archive_seed, seed_plan, source_stats

_WORKSPACE_MARKER = ".devtool-workspace-id"
_WORKSPACE_METADATA = ".devtool-remote-workspace.json"
_SYNC_STATE = ".devtool-sync-state.json"
_REGISTRY_DIR = ".registry"
_DEFAULT_EXCLUDES = (
    ".gradle/",
    "build/",
    # Source-only synchronization: never mirror mutable/private provider state.
    "/.devtool/private/",
    "/.devtool/tmp/",
    # Generated Python caches are not source and change under normal testing.
    "__pycache__/",
    "**/__pycache__/***",
    "*.py[co]",
    ".pytest_cache/",
    "**/.pytest_cache/***",
    ".mypy_cache/",
    "**/.mypy_cache/***",
    ".ruff_cache/",
    "**/.ruff_cache/***",
    ".devtool/build-artifacts/",
    ".devtool/ssh-stage/",
    ".devtool/ssh-return/",
    ".devtool/ssh-qualification/",
    ".devtool/ssh-runtime-logs/",
)
_RESERVED_EXCLUDES = (
    f"/{_WORKSPACE_MARKER}",
    f"/{_WORKSPACE_METADATA}",
    f"/{_SYNC_STATE}",
)
_VALID_MODES = {"existing", "managed", "ephemeral"}


@dataclass(slots=True)
class SSHWorkspaceSpec:
    profile: str
    destination: str
    mode: str
    repo_root: str
    repo_name: str
    workspace_id: str
    instance_id: str
    workspace_root: str
    remote_path: str
    delete: bool
    sync_mode: str
    excludes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["excludes"] = list(self.excludes)
        return data


@dataclass(slots=True)
class SSHWorkspaceStatus:
    exists: bool
    is_directory: bool
    is_symlink: bool
    safe: bool
    canonical_root: str
    canonical_path: str
    marker: str
    metadata: dict[str, Any]
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _transport_timeout() -> float:
    raw = str(os.environ.get("DEVTOOL_SSH_TRANSPORT_TIMEOUT", "30") or "30").strip()
    try:
        return max(1.0, min(float(raw), 3600.0))
    except ValueError:
        return 30.0


def _sanitize_component(value: str, *, fallback: str = "repo", limit: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return (text[:limit] or fallback)


def _git_origin(repo_root: Path) -> str:
    git = shutil.which("git")
    if not git or not (repo_root / ".git").exists():
        return ""
    completed = subprocess.run(
        [git, "-C", str(repo_root), "config", "--get", "remote.origin.url"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def repository_identity(repo_root: Path) -> tuple[str, str]:
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"SSH workspace repository does not exist or is not a directory: {root}")
    name = _sanitize_component(root.name, fallback="repo")
    # Include the canonical local checkout path even when an origin exists. Two
    # independent phone checkouts must never silently share a mutable workspace.
    origin = _git_origin(root)
    identity = f"path={root}\norigin={origin}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return name, f"{name}-{digest}"


def _expand_remote_home(value: str, home: str) -> str:
    text = str(value or "").strip()
    home = str(home or "").rstrip("/")
    if not text:
        return ""
    if text == "~" or text == "$HOME":
        return home
    if text.startswith("~/"):
        return home + "/" + text[2:]
    if text.startswith("$HOME/"):
        return home + "/" + text[6:]
    return text


def _normalize_remote_absolute(value: str) -> str:
    text = re.sub(r"/+", "/", str(value or "").strip())
    if not text.startswith("/"):
        raise ValueError(f"Managed SSH workspace path must resolve to an absolute remote path, got {value!r}")
    parts: list[str] = []
    for part in text.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"Managed SSH workspace path escapes the remote root: {value!r}")
            parts.pop()
        else:
            parts.append(part)
    return "/" + "/".join(parts)


def _merged_excludes(config: SSHRunnerConfig) -> tuple[str, ...]:
    result: list[str] = []
    if config.sync_default_excludes:
        result.extend(_DEFAULT_EXCLUDES)
    result.extend(str(item).strip() for item in config.sync_excludes if str(item).strip())
    result.extend(_RESERVED_EXCLUDES)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in result:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return tuple(deduped)


def resolve_workspace_spec(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    operation_id: str = "",
    remote_home: str = "",
) -> SSHWorkspaceSpec:
    repo = Path(repo_root).expanduser().resolve()
    repo_name, workspace_id = repository_identity(repo)
    mode = str(config.workspace_mode or "existing").strip().lower().replace("_", "-")
    if mode not in _VALID_MODES:
        raise ValueError(f"SSH workspace mode must be existing, managed, or ephemeral, got {mode!r}")

    if mode == "existing":
        if not config.cwd:
            raise ValueError("SSH workspace mode 'existing' requires ssh.cwd/--ssh-cwd")
        remote_path = _expand_remote_home(config.cwd, remote_home) if remote_home else config.cwd
        instance_id = workspace_id
        workspace_root = ""
    else:
        if not remote_home:
            probe = probe_worker(config)
            remote_home = probe.home
        if not remote_home:
            raise ValueError("Unable to resolve the remote worker HOME for a managed SSH workspace")
        raw_workspace_root = str(config.workspace_root or "~/.cache/devtool/workspaces").strip()
        if ".." in Path(raw_workspace_root.replace("$HOME", "~")).parts:
            raise ValueError(f"Managed SSH workspace root contains '..' and could escape its intended boundary: {raw_workspace_root!r}")
        workspace_root = _expand_remote_home(raw_workspace_root, remote_home)
        workspace_root = _normalize_remote_absolute(workspace_root)
        if mode == "managed":
            instance_id = workspace_id
            remote_path = workspace_root.rstrip("/") + "/" + workspace_id
        else:
            token = _sanitize_component(operation_id or uuid.uuid4().hex[:12], fallback="run", limit=32)
            instance_id = f"{workspace_id}-{token}"
            remote_path = workspace_root.rstrip("/") + "/.ephemeral/" + workspace_id + "/" + token
        remote_path = _normalize_remote_absolute(remote_path)

    sync_mode = str(config.sync_mode or "incremental").strip().lower().replace("_", "-")
    if sync_mode not in {"incremental"}:
        raise ValueError(f"SSH sync mode must be incremental, got {sync_mode!r}")
    return SSHWorkspaceSpec(
        profile=config.profile,
        destination=config.destination,
        mode=mode,
        repo_root=str(repo),
        repo_name=repo_name,
        workspace_id=workspace_id,
        instance_id=instance_id,
        workspace_root=workspace_root,
        remote_path=remote_path,
        delete=bool(config.sync_delete),
        sync_mode=sync_mode,
        excludes=_merged_excludes(config),
    )


def _run_remote(
    config: SSHRunnerConfig,
    script: str,
    *,
    input_text: str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_with_multiplex_fallback(
        config,
        build_ssh_remote_sh_argv(config, script, tty=False),
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=_transport_timeout() if timeout is None else timeout,
    )


def _status_script(spec: SSHWorkspaceSpec) -> str:
    root = shlex.quote(spec.workspace_root) if spec.workspace_root else "''"
    path = shlex.quote(spec.remote_path)
    marker = shlex.quote(_WORKSPACE_MARKER)
    metadata = shlex.quote(_WORKSPACE_METADATA)
    mode = shlex.quote(spec.mode)
    return f'''set +e
root={root}
workspace={path}
mode={mode}
printf 'exists='
[ -e "$workspace" ] && printf '1\n' || printf '0\n'
printf 'is_directory='
[ -d "$workspace" ] && printf '1\n' || printf '0\n'
printf 'is_symlink='
[ -L "$workspace" ] && printf '1\n' || printf '0\n'
root_real=""
path_real=""
safe=0
if [ "$mode" = existing ]; then
  if [ -d "$workspace" ]; then
    path_real="$(cd "$workspace" 2>/dev/null && pwd -P)"
    [ -n "$path_real" ] && safe=1
  fi
else
  if [ -d "$root" ]; then root_real="$(cd "$root" 2>/dev/null && pwd -P)"; fi
  if [ -d "$workspace" ]; then path_real="$(cd "$workspace" 2>/dev/null && pwd -P)"; fi
  if [ -n "$root_real" ] && [ -n "$path_real" ]; then
    case "$path_real/" in "$root_real"/*) safe=1;; esac
  elif [ ! -e "$workspace" ] && [ -n "$root_real" ]; then
    case "$workspace/" in "$root"/*) safe=1;; esac
  elif [ ! -e "$workspace" ] && [ ! -e "$root" ]; then
    safe=1
  fi
fi
printf 'safe=%s\n' "$safe"
printf 'canonical_root=%s\n' "$root_real"
printf 'canonical_path=%s\n' "$path_real"
printf 'marker='
if [ -f "$workspace/{_WORKSPACE_MARKER}" ]; then cat "$workspace/{_WORKSPACE_MARKER}"; fi
printf '\nmetadata_b64='
if [ -f "$workspace/{_WORKSPACE_METADATA}" ]; then
  if command -v base64 >/dev/null 2>&1; then base64 "$workspace/{_WORKSPACE_METADATA}" | tr -d '\n'; fi
fi
printf '\n'
'''


def _parse_kv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if "=" in raw:
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def workspace_status(config: SSHRunnerConfig, spec: SSHWorkspaceSpec) -> SSHWorkspaceStatus:
    completed = _run_remote(config, _status_script(spec))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH workspace status failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    values = _parse_kv(completed.stdout)
    metadata: dict[str, Any] = {}
    encoded = values.get("metadata_b64", "")
    if encoded:
        try:
            import base64
            metadata = json.loads(base64.b64decode(encoded).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            metadata = {"invalid": True}
    exists = values.get("exists") == "1"
    safe = values.get("safe") == "1"
    marker = values.get("marker", "")
    detail = "ready" if exists and safe else ("missing" if not exists and safe else "unsafe")
    if spec.mode != "existing" and exists and marker and marker != spec.instance_id:
        safe = False
        detail = f"marker mismatch: expected {spec.instance_id}, found {marker}"
    if spec.mode != "existing" and exists and not marker:
        safe = False
        detail = "managed workspace exists without a Devtool ownership marker"
    return SSHWorkspaceStatus(
        exists=exists,
        is_directory=values.get("is_directory") == "1",
        is_symlink=values.get("is_symlink") == "1",
        safe=safe,
        canonical_root=values.get("canonical_root", ""),
        canonical_path=values.get("canonical_path", ""),
        marker=marker,
        metadata=metadata,
        detail=detail,
    )


def _prepare_script(spec: SSHWorkspaceSpec) -> str:
    if spec.mode == "existing":
        return "exit 0"
    root = shlex.quote(spec.workspace_root)
    path = shlex.quote(spec.remote_path)
    marker_value = shlex.quote(spec.instance_id)
    return f'''set -eu
root={root}
workspace={path}
mkdir -p "$root"
root_real="$(cd "$root" && pwd -P)"
mkdir -p "$workspace"
path_real="$(cd "$workspace" && pwd -P)"
case "$path_real/" in "$root_real"/*) ;; *) echo 'devtool ssh: workspace escapes managed root' >&2; exit 73;; esac
if [ -f "$workspace/{_WORKSPACE_MARKER}" ]; then
  old="$(cat "$workspace/{_WORKSPACE_MARKER}")"
  [ "$old" = {marker_value} ] || {{ echo 'devtool ssh: workspace ownership marker mismatch' >&2; exit 74; }}
elif [ -n "$(find "$workspace" -mindepth 1 -maxdepth 1 ! -name {_WORKSPACE_MARKER} ! -name {_WORKSPACE_METADATA} -print -quit 2>/dev/null)" ]; then
  echo 'devtool ssh: refusing to claim a non-empty unowned managed workspace' >&2
  exit 75
fi
printf '%s' {marker_value} > "$workspace/{_WORKSPACE_MARKER}"
'''


def prepare_workspace(config: SSHRunnerConfig, spec: SSHWorkspaceSpec) -> SSHWorkspaceStatus:
    if spec.mode == "existing":
        status = workspace_status(config, spec)
        if not status.exists or not status.is_directory:
            raise ValueError(f"Existing SSH workspace is unavailable: {spec.remote_path}")
        if not status.safe:
            raise ValueError(f"Existing SSH workspace could not be resolved safely: {spec.remote_path}")
        return status
    completed = _run_remote(config, _prepare_script(spec))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH workspace preparation failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    status = workspace_status(config, spec)
    if not status.exists or not status.is_directory or not status.safe or status.marker != spec.instance_id:
        raise ValueError(f"SSH workspace preparation did not produce a safe owned workspace: {status.detail}")
    return status


def _rsync_executable() -> str:
    candidate = str(os.environ.get("DEVTOOL_RSYNC_COMMAND", "rsync") or "rsync").strip()
    resolved = shutil.which(candidate) if os.sep not in candidate else candidate
    if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"Local rsync executable is unavailable or not executable: {candidate}")
    return str(resolved)


def _rsync_remote_shell(config: SSHRunnerConfig) -> str:
    argv = build_ssh_base_argv(config, tty=False)
    return shlex.join(argv[:-1])


def _rsync_argv(
    config: SSHRunnerConfig,
    spec: SSHWorkspaceSpec,
    *,
    dry_run: bool,
    destination_override: str = "",
    worker: SSHWorkerProbe | None = None,
    nested_git_excludes: tuple[str, ...] | None = None,
) -> list[str]:
    worker = worker or probe_worker(config)
    remote_rsync = worker.tools.get("rsync", "")
    if not remote_rsync:
        raise ValueError("SSH workspace sync requires rsync on the remote worker; run 'devtool ssh bootstrap' first")
    argv = [
        _rsync_executable(),
        "-a",
        # Managed metadata changes directory mtimes; only content/real changes
        # should prevent a clean second synchronization fixed point.
        "--omit-dir-times",
        "--no-owner",
        "--no-group",
        "--no-devices",
        "--no-specials",
        "--protect-args",
        "--human-readable",
        "--itemize-changes",
        "--out-format=%i|%b|%l|%n%L",
        "--partial",
        "--delay-updates",
        "-e",
        _rsync_remote_shell(config),
        f"--rsync-path={remote_rsync}",
    ]
    if spec.delete:
        argv.append("--delete-delay")
    if dry_run:
        argv.append("--dry-run")
    # Reuse the current operation's source inventory when available. rsync still
    # checks every included source file and performs its ordinary safety checks.
    if nested_git_excludes is None:
        nested_git_excludes = source_stats(spec.repo_root, spec.excludes).nested_git_excludes
    for pattern in tuple(spec.excludes) + tuple(nested_git_excludes):
        argv.extend(["--exclude", pattern])
    source = str(Path(spec.repo_root)) + "/"
    destination = destination_override or f"{config.destination}:{spec.remote_path.rstrip('/')}/"
    argv.extend([source, destination])
    return argv


def _parse_rsync_changes(stdout: str) -> list[dict[str, str]]:
    """Parse stable itemized rsync records while tolerating legacy output.

    SSH-01 emits ``%i|%b|%l|%n%L`` so byte counters are machine-readable.
    This helper intentionally preserves the long-standing ``code/path`` public
    shape used by sync plans and diagnostics.
    """
    changes: list[dict[str, str]] = []
    for raw in str(stdout or "").splitlines():
        line = raw.rstrip("\r\n")
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4 and parts[1].strip().isdigit() and parts[2].strip().isdigit():
            code, _bytes_sent, _length, path = parts
        elif "|" in line:
            code, path = line.split("|", 1)
        else:
            code, path = line[:12].strip(), line[12:].strip()
        changes.append({"code": code.strip(), "path": path})
    return changes


def _parse_rsync_metrics(stdout: str) -> dict[str, int]:
    """Aggregate rsync's machine-readable ``%b``/``%l`` records.

    ``transferred_bytes`` is the payload rsync reports as actually transferred,
    not the total repository size. This keeps warm-delta telemetry truthful.
    Legacy two-field output is accepted and simply contributes no byte metrics.
    """
    transferred_bytes = 0
    changed_source_bytes = 0
    metric_records = 0
    for raw in str(stdout or "").splitlines():
        parts = raw.rstrip("\r\n").split("|", 3)
        if len(parts) != 4:
            continue
        sent_text, length_text = parts[1].strip(), parts[2].strip()
        if not sent_text.isdigit() or not length_text.isdigit():
            continue
        transferred_bytes += int(sent_text)
        changed_source_bytes += int(length_text)
        metric_records += 1
    return {
        "transferred_bytes": transferred_bytes,
        "changed_source_bytes": changed_source_bytes,
        "metric_records": metric_records,
    }


def _run_rsync(config: SSHRunnerConfig, argv: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=timeout)
    if completed.returncode == 0:
        return completed
    detail = str(completed.stderr or completed.stdout or "")
    if not multiplex_failure(detail):
        return completed
    disable_for_operation(config, detail.strip() or "rsync remote-shell multiplexing failed")
    retry = list(argv)
    try:
        index = retry.index("-e")
        if index + 1 < len(retry):
            retry[index + 1] = strip_multiplex_from_shell(retry[index + 1])
    except ValueError:
        pass
    return subprocess.run(retry, check=False, capture_output=True, text=True, timeout=timeout)


def _remote_workspace_bytes(config: SSHRunnerConfig, spec: SSHWorkspaceSpec, status: SSHWorkspaceStatus) -> int:
    if not status.exists or not status.safe:
        return 0
    target = status.canonical_path or spec.remote_path
    completed = _run_remote(config, f"du -sk {shlex.quote(target)} 2>/dev/null | awk '{{print $1}}'", timeout=15)
    if completed.returncode != 0:
        return 0
    try:
        return max(0, int((completed.stdout or "0").strip().splitlines()[-1])) * 1024
    except (ValueError, IndexError):
        return 0


def _local_missing_workspace_plan(config: SSHRunnerConfig, spec: SSHWorkspaceSpec) -> tuple[int, str, str]:
    # An absent managed destination cannot be queried with remote rsync without
    # creating parent directories. Compare against an empty local directory so
    # dry-run remains strictly non-mutating while still honoring rsync filters.
    with tempfile.TemporaryDirectory(prefix="devtool-ssh-sync-plan-") as temp_dir:
        argv = _rsync_argv(config, spec, dry_run=True, destination_override=temp_dir.rstrip("/") + "/")
        # Local destination means the remote-shell/remote-rsync options are not
        # applicable. Remove those pairs while preserving all archive/filter semantics.
        cleaned: list[str] = []
        skip = 0
        for token in argv:
            if skip:
                skip -= 1
                continue
            if token == "-e":
                skip = 1
                continue
            if token.startswith("--rsync-path="):
                continue
            cleaned.append(token)
        completed = subprocess.run(cleaned, check=False, capture_output=True, text=True, timeout=120)
        return completed.returncode, completed.stdout, completed.stderr


def plan_workspace_sync(
    config: SSHRunnerConfig,
    spec: SSHWorkspaceSpec,
    *,
    worker: SSHWorkerProbe | None = None,
    nested_git_excludes: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    status = workspace_status(config, spec)
    if status.exists and (not status.is_directory or not status.safe):
        raise ValueError(f"Refusing to plan sync for unsafe SSH workspace {spec.remote_path}: {status.detail}")
    if spec.mode != "existing" and status.exists and status.marker != spec.instance_id:
        raise ValueError(f"Refusing to sync unowned SSH workspace {spec.remote_path}: {status.detail}")
    if spec.mode == "existing" and not status.exists:
        raise ValueError(f"Existing SSH workspace is unavailable: {spec.remote_path}")

    if status.exists:
        canonical = status.canonical_path or spec.remote_path
        argv = _rsync_argv(
            config,
            spec,
            dry_run=True,
            destination_override=f"{config.destination}:{canonical.rstrip('/')}/",
            worker=worker,
            nested_git_excludes=nested_git_excludes,
        )
        completed = _run_rsync(config, argv, timeout=300)
        rc, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    else:
        rc, stdout, stderr = _local_missing_workspace_plan(config, spec)
    if rc != 0:
        detail = (stderr or stdout or "").strip()
        raise ValueError(f"SSH workspace rsync plan failed with exit {rc}: {detail or 'no diagnostic output'}")
    changes = _parse_rsync_changes(stdout)
    return {
        "schema_version": 1,
        "kind": "ssh-workspace-sync-plan",
        "ok": True,
        "dry_run": True,
        "workspace": spec.as_dict(),
        "status": status.as_dict(),
        "changes": changes,
        "change_count": len(changes),
        "would_create_workspace": not status.exists and spec.mode != "existing",
        "delete_enabled": spec.delete,
        "detail": "sync plan only; remote workspace was not modified",
    }


def _source_revision(repo_root: str) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    git = shutil.which("git")
    if not git or not (repo / ".git").exists():
        return {"git_head": "", "working_tree_dirty": False, "working_tree_fingerprint": ""}
    head = subprocess.run([git, "-C", str(repo), "rev-parse", "HEAD"], check=False, capture_output=True, text=True, timeout=10)
    status = subprocess.run(
        [git, "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=False, capture_output=True, timeout=30,
    )
    raw = status.stdout if status.returncode == 0 else b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="surrogateescape")
    return {
        "git_head": head.stdout.strip() if head.returncode == 0 else "",
        "working_tree_dirty": bool(raw),
        "working_tree_fingerprint": hashlib.sha256(raw).hexdigest() if raw else hashlib.sha256(b"").hexdigest(),
    }


def _new_sync_id(spec: SSHWorkspaceSpec) -> str:
    stamp = int(time.time() * 1000)
    return f"sync-{spec.workspace_id}-{stamp}-{uuid.uuid4().hex[:8]}"


def _metadata_payload(spec: SSHWorkspaceSpec, *, changes: int, sync_id: str, source_revision: dict[str, Any], transport: dict[str, Any] | None = None, federation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workspace_id": spec.workspace_id,
        "instance_id": spec.instance_id,
        "profile": spec.profile,
        "destination": spec.destination,
        "mode": spec.mode,
        "repo_name": spec.repo_name,
        "repo_root": spec.repo_root,
        "remote_path": spec.remote_path,
        "sync_mode": spec.sync_mode,
        "delete": spec.delete,
        "excludes": list(spec.excludes),
        "last_sync_unix": int(time.time()),
        "last_sync_id": sync_id,
        "last_change_count": int(changes),
        "source_revision": dict(source_revision),
        "transport": dict(transport or {}),
        "federation_transport_identity": dict(federation or {}),
    }


def _write_remote_metadata(config: SSHRunnerConfig, spec: SSHWorkspaceSpec, payload: dict[str, Any]) -> None:
    # Existing/unmanaged checkouts remain untouched except for files explicitly
    # synchronized from the local repository. Devtool ownership metadata is
    # reserved for managed/ephemeral workspaces.
    if spec.mode == "existing":
        return
    body = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    workspace_file = spec.remote_path.rstrip("/") + "/" + _WORKSPACE_METADATA
    registry_dir = spec.workspace_root.rstrip("/") + "/" + _REGISTRY_DIR
    registry_file = registry_dir + "/" + spec.instance_id + ".json"
    script = (
        f"set -eu; umask 077; mkdir -p {shlex.quote(registry_dir)}; "
        f"cat > {shlex.quote(workspace_file + '.tmp')}; "
        f"cp {shlex.quote(workspace_file + '.tmp')} {shlex.quote(registry_file + '.tmp')}; "
        f"mv -f -- {shlex.quote(workspace_file + '.tmp')} {shlex.quote(workspace_file)}; "
        f"mv -f -- {shlex.quote(registry_file + '.tmp')} {shlex.quote(registry_file)}"
    )
    completed = _run_remote(config, script, input_text=body)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH workspace metadata update failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")




def _write_sync_state(config: SSHRunnerConfig, spec: SSHWorkspaceSpec, payload: dict[str, Any]) -> None:
    if spec.mode == "existing":
        return
    body = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    target = spec.remote_path.rstrip("/") + "/" + _SYNC_STATE
    script = (
        f"set -eu; umask 077; cat > {shlex.quote(target + '.tmp')}; "
        f"mv -f -- {shlex.quote(target + '.tmp')} {shlex.quote(target)}"
    )
    completed = _run_remote(config, script, input_text=body)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH workspace sync-state update failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")

def _git_bundle_sync(
    config: SSHRunnerConfig,
    spec: SSHWorkspaceSpec,
    *,
    worker: SSHWorkerProbe,
    source_plan: Any,
    retain_lease: bool,
) -> dict[str, Any]:
    """Materialize a frozen federation child from a verified Git bundle.

    This is source transport only. FRX-Y11 remains the authority that produced
    the frozen lineage; SSH merely transfers and materializes that exact object
    closure into a Devtool-owned managed/ephemeral worker workspace.
    """
    from .federation_git_transport import (
        build_remote_bundle_materialization_shell,
        bundle_transport_evidence,
        create_federated_git_bundle,
    )
    from .ssh_tmux_runner import _copy_local_file_to_remote

    if spec.mode == "existing":
        raise ValueError("git-bundle source transport requires a Devtool-managed or ephemeral SSH workspace")
    if not worker.tools.get("git"):
        raise ValueError("git-bundle source transport requires git on the remote worker")

    from .ssh_resilience import acquire_workspace_lease, release_workspace_lease
    from .ssh_tmux_runner import worker_session_name

    lease = None
    bundle = None
    remote_bundle = ""
    run_id = str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or spec.instance_id).strip()
    session_prefix = worker_session_name(config, operation="commands", repository=spec.remote_path, run_id=run_id)
    lease = acquire_workspace_lease(config, spec, run_id=run_id, session_prefix=session_prefix, runner=_run_remote)
    try:
        status_before = workspace_status(config, spec)
        if status_before.exists and (not status_before.is_directory or not status_before.safe):
            raise ValueError(f"Refusing to materialize unsafe SSH workspace {spec.remote_path}: {status_before.detail}")
        prepared = prepare_workspace(config, spec)
        canonical = prepared.canonical_path or spec.remote_path
        sync_id = _new_sync_id(spec)
        source_revision = _source_revision(spec.repo_root)
        bundle = create_federated_git_bundle(spec.repo_root, source_plan)
        remote_bundle_dir = spec.workspace_root.rstrip("/") + "/.bundles"
        remote_bundle = remote_bundle_dir + "/" + sync_id + ".bundle"
        mkdir = _run_remote(config, f"set -eu; umask 077; mkdir -p -- {shlex.quote(remote_bundle_dir)}")
        if mkdir.returncode != 0:
            detail = (mkdir.stderr or mkdir.stdout or "").strip()
            raise ValueError(f"Unable to prepare remote Git-bundle staging directory: {detail or mkdir.returncode}")
        _copy_local_file_to_remote(config, bundle.path, remote_bundle)

        transport: dict[str, Any] = {
            "planned_mode": "git-bundle",
            "effective_mode": "git-bundle",
            "source_transport": source_plan.as_dict(),
            "reconciliation": {"required": False, "complete": False},
        }
        _write_sync_state(config, spec, {
            "schema_version": 1, "state": "in-progress", "sync_id": sync_id,
            "started_unix": int(time.time()), "source_revision": source_revision,
            "transport": transport, "federation_transport_identity": federation_transport_identity(spec.repo_root),
        })
        script = build_remote_bundle_materialization_shell(
            source_plan, bundle, remote_root=canonical, remote_bundle=remote_bundle,
        )
        completed = _run_remote(config, script, timeout=None)
        if completed.returncode != 0:
            evidence = bundle_transport_evidence(
                source_plan, bundle, remote_root=canonical, remote_bundle=remote_bundle, verified=False,
            )
            transport["source_transport_evidence"] = evidence
            transport["reconciliation"] = {"required": False, "complete": False, "returncode": int(completed.returncode)}
            _write_sync_state(config, spec, {
                "schema_version": 1, "state": "partial", "recoverable": True, "sync_id": sync_id,
                "updated_unix": int(time.time()), "returncode": int(completed.returncode),
                "source_revision": source_revision, "transport": transport,
                "federation_transport_identity": federation_transport_identity(spec.repo_root),
            })
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(
                "Federated git-bundle materialization failed; source transport did not fall back to rsync: "
                + (detail or f"exit {completed.returncode}")
            )

        evidence = bundle_transport_evidence(
            source_plan, bundle, remote_root=canonical, remote_bundle=remote_bundle, verified=True,
        )
        transport["source_transport_evidence"] = evidence
        transport["reconciliation"] = {"required": False, "complete": True, "returncode": 0}
        federation = federation_transport_identity(spec.repo_root)
        payload = _metadata_payload(
            spec, changes=0, sync_id=sync_id, source_revision=source_revision,
            transport=transport, federation=federation,
        )
        _write_sync_state(config, spec, {
            "schema_version": 1, "state": "complete", "recoverable": False, "sync_id": sync_id,
            "completed_unix": int(time.time()), "source_revision": source_revision, "change_count": 0,
            "transport": transport, "federation_transport_identity": federation,
        })
        _write_remote_metadata(config, spec, payload)
        status_after = workspace_status(config, spec)
        lease_payload = lease.as_dict() if lease is not None else {}
        if lease is not None and not retain_lease:
            release_workspace_lease(config, lease, runner=_run_remote)
            lease_payload = {}
            lease = None
        return {
            "schema_version": 1,
            "kind": "ssh-workspace-sync",
            "ok": status_after.exists and status_after.safe,
            "workspace": spec.as_dict(),
            "status_before": status_before.as_dict(),
            "status_after": status_after.as_dict(),
            "changes": [],
            "change_count": 0,
            "sync_id": sync_id,
            "source_revision": source_revision,
            "delete_enabled": spec.delete,
            "transport_mode": "git-bundle",
            "source_transport_requested": source_plan.requested_transport,
            "source_transport_effective": source_plan.effective_transport,
            "source_transport_evidence_digest": str(evidence.get("evidence_digest") or ""),
            "transport": transport,
            "federation_transport_identity": federation,
            "lease": lease_payload,
            "detail": "frozen federation Git object closure materialized and remotely attested",
        }
    except Exception:
        if remote_bundle:
            try:
                _run_remote(config, f"rm -f -- {shlex.quote(remote_bundle)}")
            except Exception:
                pass
        if lease is not None:
            release_workspace_lease(config, lease, runner=_run_remote)
        raise
    finally:
        if bundle is not None:
            bundle.path.unlink(missing_ok=True)


def sync_workspace(
    config: SSHRunnerConfig,
    spec: SSHWorkspaceSpec,
    *,
    dry_run: bool = False,
    retain_lease: bool = False,
    worker: SSHWorkerProbe | None = None,
) -> dict[str, Any]:
    worker = worker or probe_worker(config)
    federation = federation_transport_identity(spec.repo_root)
    from .federation_git_transport import plan_federated_git_source
    from .invocation_origin import federation_lineage_from_environment

    lineage = federation_lineage_from_environment() or {}
    source_plan = plan_federated_git_source(
        spec.repo_root, lineage=lineage, requested_transport=config.source_transport,
    )
    if source_plan.effective_transport == "git-bundle" and not worker.tools.get("git"):
        if source_plan.requested_transport == "git-bundle":
            raise ValueError("Strict git-bundle source transport requires git on the remote worker")
        source_plan = replace(
            source_plan, effective_transport="rsync", eligible=False,
            reason="remote worker does not provide git; auto source transport selected rsync",
        )
    if spec.mode == "existing" and source_plan.effective_transport == "git-bundle":
        if source_plan.requested_transport == "git-bundle":
            raise ValueError("Strict git-bundle source transport requires a managed or ephemeral SSH workspace")
        source_plan = replace(
            source_plan, effective_transport="rsync", eligible=False,
            reason="existing SSH checkouts retain rsync compatibility transport",
        )

    if dry_run and source_plan.effective_transport == "git-bundle":
        status = workspace_status(config, spec)
        return {
            "schema_version": 1, "kind": "ssh-workspace-sync-plan", "ok": True, "dry_run": True,
            "workspace": spec.as_dict(), "status": status.as_dict(), "changes": [], "change_count": 0,
            "would_create_workspace": not status.exists and spec.mode != "existing",
            "delete_enabled": spec.delete,
            "transport_mode": "git-bundle",
            "source_transport_requested": source_plan.requested_transport,
            "source_transport_effective": source_plan.effective_transport,
            "transport": {"source_transport": source_plan.as_dict(), "federation_transport_identity": federation},
            "detail": "plan only; frozen federation source will use verified git-bundle transport",
        }
    if dry_run:
        # One inventory for rsync's nested-repo boundaries and archive sizing.
        # Every rsync dry-run still compares live files against the remote.
        stats = source_stats(spec.repo_root, spec.excludes)
        payload = plan_workspace_sync(config, spec, worker=worker, nested_git_excludes=stats.nested_git_excludes)
        status = SSHWorkspaceStatus(**payload["status"])
        remote_bytes = _remote_workspace_bytes(config, spec, status)
        transport_plan = seed_plan(spec, status, worker, remote_bytes=remote_bytes, stats=stats)
        payload["transport"] = {
            "plan": transport_plan.as_dict(), "source_transport": source_plan.as_dict(),
            "federation_transport_identity": federation,
        }
        payload["source_transport_requested"] = source_plan.requested_transport
        payload["source_transport_effective"] = source_plan.effective_transport
        return payload

    if source_plan.effective_transport == "git-bundle":
        return _git_bundle_sync(
            config, spec, worker=worker, source_plan=source_plan, retain_lease=retain_lease,
        )

    # Fail before creating/claiming a managed directory if either side is not
    # ready for synchronization. This keeps rsync bootstrap/tool failures non-mutating.
    _rsync_executable()
    if not worker.tools.get("rsync"):
        raise ValueError("SSH workspace sync requires rsync on the remote worker; run 'devtool ssh bootstrap' first")

    lease = None
    if spec.mode != "existing":
        from .ssh_resilience import acquire_workspace_lease, release_workspace_lease
        from .ssh_tmux_runner import worker_session_name
        run_id = str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or spec.instance_id).strip()
        session_prefix = worker_session_name(config, operation="commands", repository=spec.remote_path, run_id=run_id)
        lease = acquire_workspace_lease(config, spec, run_id=run_id, session_prefix=session_prefix, runner=_run_remote)

    try:
        status_before = workspace_status(config, spec)
        if status_before.exists and (not status_before.is_directory or not status_before.safe):
            raise ValueError(f"Refusing to sync unsafe SSH workspace {spec.remote_path}: {status_before.detail}")
        remote_bytes = _remote_workspace_bytes(config, spec, status_before)
        transport_plan = seed_plan(spec, status_before, worker, remote_bytes=remote_bytes)
        prepared = prepare_workspace(config, spec)
        canonical = prepared.canonical_path or spec.remote_path
        sync_id = _new_sync_id(spec)
        source_revision = _source_revision(spec.repo_root)
        transport: dict[str, Any] = {
            "planned_mode": transport_plan.mode,
            "effective_mode": "rsync-delta",
            "plan": transport_plan.as_dict(),
            "source_transport": source_plan.as_dict(),
            "archive": {"used": False},
            "reconciliation": {"required": True, "complete": False},
        }
        _write_sync_state(config, spec, {
            "schema_version": 1, "state": "in-progress", "sync_id": sync_id,
            "started_unix": int(time.time()), "source_revision": source_revision,
            "transport": transport, "federation_transport_identity": federation,
        })

        if transport_plan.use_archive:
            archive = run_archive_seed(config, spec, transport_plan, sync_id=sync_id)
            transport["archive"] = archive
            if archive.get("ok"):
                transport["effective_mode"] = "archive-seed+reconcile"
            else:
                transport["archive_fallback"] = "rsync-delta"

        argv = _rsync_argv(
            config,
            spec,
            dry_run=False,
            destination_override=f"{config.destination}:{canonical.rstrip('/')}/",
            worker=worker,
            nested_git_excludes=transport_plan.nested_git_excludes,
        )
        started = time.monotonic()
        telemetry = SSHTransferTelemetry(
            mode=str(transport["effective_mode"]),
            state="running",
            source_bytes=transport_plan.source_bytes,
            source_files=transport_plan.source_files,
            remaining_files=transport_plan.source_files,
            workspace_sync_id=sync_id,
            detail="rsync authoritative reconciliation",
        )
        telemetry.publish(run_id=str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or ""))
        completed = _run_rsync(config, argv, timeout=None)
        elapsed = max(0.000001, time.monotonic() - started)
        changes = _parse_rsync_changes(completed.stdout)
        rsync_metrics = _parse_rsync_metrics(completed.stdout)
        transport["reconciliation"] = {
            "required": True,
            "complete": completed.returncode == 0,
            "change_count": len(changes),
            "returncode": int(completed.returncode),
            "transferred_bytes": int(rsync_metrics["transferred_bytes"]),
            "changed_source_bytes": int(rsync_metrics["changed_source_bytes"]),
            "metric_records": int(rsync_metrics["metric_records"]),
        }
        telemetry.state = "complete" if completed.returncode == 0 else "failed"
        telemetry.transferred_files = len(changes) if completed.returncode == 0 else 0
        telemetry.transferred_bytes = int(rsync_metrics["transferred_bytes"])
        telemetry.remaining_files = 0 if completed.returncode == 0 else max(0, transport_plan.source_files - len(changes))
        telemetry.current_path = str(changes[-1].get("path") or "") if changes else ""
        telemetry.elapsed_seconds = elapsed
        telemetry.throughput_bps = (float(telemetry.transferred_bytes) / elapsed) if telemetry.transferred_bytes else 0.0
        telemetry.eta_seconds = 0.0 if completed.returncode == 0 else None
        telemetry.reconciliation_count = len(changes)
        telemetry.detail = "rsync reconciliation complete" if completed.returncode == 0 else "rsync reconciliation failed; workspace remains recoverable"
        telemetry.publish(run_id=str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or ""))
        transport["telemetry"] = telemetry.as_dict()

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            _write_sync_state(config, spec, {
                "schema_version": 1, "state": "partial", "recoverable": True, "sync_id": sync_id,
                "updated_unix": int(time.time()), "returncode": int(completed.returncode),
                "source_revision": source_revision, "change_count": len(changes),
                "transport": transport, "federation_transport_identity": federation,
            })
            if lease is not None:
                release_workspace_lease(config, lease, runner=_run_remote)
                lease = None
            return {
                "schema_version": 1,
                "kind": "ssh-workspace-sync",
                "ok": False,
                "recoverable": True,
                "state": "partial",
                "sync_id": sync_id,
                "workspace": spec.as_dict(),
                "status_before": status_before.as_dict(),
                "changes": changes,
                "change_count": len(changes),
                "transport_mode": transport["effective_mode"],
                "source_transport_requested": source_plan.requested_transport,
                "source_transport_effective": source_plan.effective_transport,
                "transport": transport,
                "federation_transport_identity": federation,
                "detail": detail or f"rsync failed with exit {completed.returncode}; the next sync will resume from rsync partial state",
            }
        payload = _metadata_payload(
            spec, changes=len(changes), sync_id=sync_id, source_revision=source_revision,
            transport=transport, federation=federation,
        )
        _write_sync_state(config, spec, {
            "schema_version": 1, "state": "complete", "recoverable": False, "sync_id": sync_id,
            "completed_unix": int(time.time()), "source_revision": source_revision, "change_count": len(changes),
            "transport": transport, "federation_transport_identity": federation,
        })
        _write_remote_metadata(config, spec, payload)
        status_after = workspace_status(config, spec)
        lease_payload = lease.as_dict() if lease is not None else {}
        if lease is not None and not retain_lease:
            release_workspace_lease(config, lease, runner=_run_remote)
            lease_payload = {}
            lease = None
        return {
            "schema_version": 1,
            "kind": "ssh-workspace-sync",
            "ok": status_after.exists and status_after.safe,
            "workspace": spec.as_dict(),
            "status_before": status_before.as_dict(),
            "status_after": status_after.as_dict(),
            "changes": changes,
            "change_count": len(changes),
            "sync_id": sync_id,
            "source_revision": source_revision,
            "delete_enabled": spec.delete,
            "transport_mode": transport["effective_mode"],
            "source_transport_requested": source_plan.requested_transport,
            "source_transport_effective": source_plan.effective_transport,
            "transport": transport,
            "federation_transport_identity": federation,
            "lease": lease_payload,
            "detail": "workspace synchronized" if status_after.safe else status_after.detail,
        }
    except Exception:
        if lease is not None:
            from .ssh_resilience import release_workspace_lease
            release_workspace_lease(config, lease, runner=_run_remote)
        raise

def workspace_payload(config: SSHRunnerConfig, repo_root: str | Path) -> dict[str, Any]:
    probe = probe_worker(config)
    spec = resolve_workspace_spec(
        config,
        repo_root,
        operation_id="preview" if config.workspace_mode == "ephemeral" else "",
        remote_home=probe.home,
    )
    status = workspace_status(config, spec)
    return {
        "schema_version": 1,
        "kind": "ssh-workspace-status",
        "ok": status.safe and (status.exists if spec.mode == "existing" else True),
        "workspace": spec.as_dict(),
        "status": status.as_dict(),
        "probe": {
            "home": probe.home,
            "platform": probe.platform,
            "rsync": probe.tools.get("rsync", ""),
        },
    }


def sync_payload(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    dry_run: bool = False,
    operation_id: str = "",
) -> dict[str, Any]:
    probe = probe_worker(config)
    spec = resolve_workspace_spec(
        config,
        repo_root,
        operation_id=operation_id or ("preview" if dry_run and config.workspace_mode == "ephemeral" else ""),
        remote_home=probe.home,
    )
    return sync_workspace(config, spec, dry_run=dry_run, worker=probe)
