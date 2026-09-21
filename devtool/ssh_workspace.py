from __future__ import annotations

"""Managed SSH workspace planning and incremental repository synchronization.

SSHW-02 keeps the local repository authoritative.  A named SSH worker may use
an existing checkout, a persistent Devtool-managed mirror, or an ephemeral
mirror.  Managed paths are guarded by a workspace marker and a canonical-root
check before any rsync operation that can delete remote files.
"""

from dataclasses import asdict, dataclass
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
from .ssh_worker import probe_worker

_WORKSPACE_MARKER = ".devtool-workspace-id"
_WORKSPACE_METADATA = ".devtool-remote-workspace.json"
_REGISTRY_DIR = ".registry"
_DEFAULT_EXCLUDES = (
    ".gradle/",
    "build/",
    ".devtool/build-artifacts/",
    ".devtool/ssh-stage/",
)
_RESERVED_EXCLUDES = (
    f"/{_WORKSPACE_MARKER}",
    f"/{_WORKSPACE_METADATA}",
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
    return subprocess.run(
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
) -> list[str]:
    probe = probe_worker(config)
    remote_rsync = probe.tools.get("rsync", "")
    if not remote_rsync:
        raise ValueError("SSH workspace sync requires rsync on the remote worker; run 'devtool ssh bootstrap' first")
    argv = [
        _rsync_executable(),
        "-a",
        "--no-owner",
        "--no-group",
        "--no-devices",
        "--no-specials",
        "--protect-args",
        "--human-readable",
        "--itemize-changes",
        "--out-format=%i|%n%L",
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
    for pattern in spec.excludes:
        argv.extend(["--exclude", pattern])
    source = str(Path(spec.repo_root)) + "/"
    destination = destination_override or f"{config.destination}:{spec.remote_path.rstrip('/')}/"
    argv.extend([source, destination])
    return argv


def _parse_rsync_changes(stdout: str) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    for raw in str(stdout or "").splitlines():
        line = raw.rstrip("\r\n")
        if not line:
            continue
        if "|" in line:
            code, path = line.split("|", 1)
        else:
            code, path = line[:12].strip(), line[12:].strip()
        changes.append({"code": code.strip(), "path": path})
    return changes


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


def plan_workspace_sync(config: SSHRunnerConfig, spec: SSHWorkspaceSpec) -> dict[str, Any]:
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
        )
        completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=300)
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


def _metadata_payload(spec: SSHWorkspaceSpec, *, changes: int) -> dict[str, Any]:
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
        "last_change_count": int(changes),
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


def sync_workspace(config: SSHRunnerConfig, spec: SSHWorkspaceSpec, *, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return plan_workspace_sync(config, spec)

    # Fail before creating/claiming a managed directory if either side is not
    # ready for synchronization. This keeps bootstrap/tool failures non-mutating.
    _rsync_executable()
    probe = probe_worker(config)
    if not probe.tools.get("rsync"):
        raise ValueError("SSH workspace sync requires rsync on the remote worker; run 'devtool ssh bootstrap' first")

    status_before = workspace_status(config, spec)
    prepared = prepare_workspace(config, spec)
    canonical = prepared.canonical_path or spec.remote_path
    argv = _rsync_argv(
        config,
        spec,
        dry_run=False,
        destination_override=f"{config.destination}:{canonical.rstrip('/')}/",
    )
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=None)
    changes = _parse_rsync_changes(completed.stdout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "schema_version": 1,
            "kind": "ssh-workspace-sync",
            "ok": False,
            "workspace": spec.as_dict(),
            "status_before": status_before.as_dict(),
            "changes": changes,
            "change_count": len(changes),
            "detail": detail or f"rsync failed with exit {completed.returncode}",
        }
    payload = _metadata_payload(spec, changes=len(changes))
    _write_remote_metadata(config, spec, payload)
    status_after = workspace_status(config, spec)
    return {
        "schema_version": 1,
        "kind": "ssh-workspace-sync",
        "ok": status_after.exists and status_after.safe,
        "workspace": spec.as_dict(),
        "status_before": status_before.as_dict(),
        "status_after": status_after.as_dict(),
        "changes": changes,
        "change_count": len(changes),
        "delete_enabled": spec.delete,
        "detail": "workspace synchronized" if status_after.safe else status_after.detail,
    }


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
    return sync_workspace(config, spec, dry_run=dry_run)
