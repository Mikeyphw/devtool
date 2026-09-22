from __future__ import annotations

"""OCI-04 managed workspace, bind, cache, and environment contract.

This module deliberately prepares *provider inputs* only.  Repository command
execution stays disabled until OCI-05.  Durable lifecycle truth is published to
EXO; returned bind bookkeeping is an ephemeral/provider-local description that
future command wrapping may consume but must never treat as an execution state
machine.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import time
import tempfile
from typing import Any, Mapping

from .core.events import GraphSealed, NodeDiscovered, RunFinished, RunStarted, get_event_bus, publish_event
from .core.execution_contract import ExecutionStatus
from .core.execution_subscribers import ExecutionProjectionSubscriber
from .execution_providers import SSH_PROOT_OCI
from .logstore import redact_sensitive_value
from .progress_events import RunnerExecutionAdapter
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv
from . import proot_oci as _runtime

# Keep controller-local Git probes independent from tests/provider shims that
# replace the shared ``subprocess.Popen`` attribute used by the SSH runtime.
# The probe itself is still fully EXO-owned through RunnerExecutionAdapter.
_LOCAL_POPEN = subprocess.Popen

WORKSPACE_GUEST = "/workspace"
CACHE_GUEST = "/var/cache/devtool"
APPROVED_CACHES = ("gradle", "xdg", "npm", "pip", "uv")
DEFAULT_ENV_ALLOWLIST = ("CI", "TERM", "COLORTERM", "LANG", "LC_ALL", "TZ")
_MANAGED_ENV = {
    "DEVTOOL_EXECUTION_CONTEXT": "ssh-proot-oci",
    "DEVTOOL_WORKSPACE": WORKSPACE_GUEST,
    "GRADLE_USER_HOME": f"{CACHE_GUEST}/gradle",
    "XDG_CACHE_HOME": f"{CACHE_GUEST}/xdg",
    "npm_config_cache": f"{CACHE_GUEST}/npm",
    "PIP_CACHE_DIR": f"{CACHE_GUEST}/pip",
    "UV_CACHE_DIR": f"{CACHE_GUEST}/uv",
}
_SENSITIVE_NAME = re.compile(r"(?:AUTH|TOKEN|SECRET|PASSWORD|PASSWD|COOKIE|CREDENTIAL|PRIVATE|API[_-]?KEY|ACCESS[_-]?KEY)", re.I)
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class OciWorkspaceBind:
    host: str
    guest: str
    kind: str

    def as_dict(self) -> dict[str, str]:
        return {"host": self.host, "guest": self.guest, "kind": self.kind}


@dataclass(frozen=True, slots=True)
class OciWorkspacePlan:
    profile: str
    workspace_id: str
    remote_workspace: str
    remote_cache_root: str
    source_sync_id: str
    working_directory: str
    binds: tuple[OciWorkspaceBind, ...]
    managed_environment: Mapping[str, str]
    forwarded_environment: Mapping[str, str]

    def as_dict(self) -> dict[str, Any]:
        # Forwarded values are intentionally not persisted or printed.  The
        # names are sufficient evidence that the allowlist was applied.
        return {
            "profile": self.profile,
            "workspace_id": self.workspace_id,
            "remote_workspace": self.remote_workspace,
            "guest_workspace": WORKSPACE_GUEST,
            "remote_cache_root": self.remote_cache_root,
            "guest_cache_root": CACHE_GUEST,
            "source_sync_id": self.source_sync_id,
            "working_directory": self.working_directory,
            "binds": [item.as_dict() for item in self.binds],
            "approved_caches": list(APPROVED_CACHES),
            "managed_environment": dict(self.managed_environment),
            "forwarded_environment_names": sorted(self.forwarded_environment),
        }


def _normalize_remote_root(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} cannot be empty")
    if _CONTROL_CHAR.search(text):
        raise ValueError(f"{label} cannot contain control characters")
    # PRoot-Distro uses ``host:guest`` for --bind.  A colon in the host side is
    # therefore ambiguous and must fail closed rather than be reinterpreted.
    if ":" in text:
        raise ValueError(f"{label} cannot contain ':' because OCI bind sources use host:guest syntax")

    if text == "$HOME" or text.startswith("$HOME/"):
        text = "~" + text[5:]
    if not (text.startswith("/") or text == "~" or text.startswith("~/")):
        raise ValueError(f"{label} must be absolute or HOME-relative: {value!r}")

    prefix = "~" if text == "~" or text.startswith("~/") else "/"
    raw_tail = "" if text in {"~", "/"} else (text[2:] if prefix == "~" else text[1:])
    parts: list[str] = []
    for part in raw_tail.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise ValueError(f"{label} cannot contain '..': {value!r}")
        parts.append(part)
    if prefix == "~":
        return "~" + (("/" + "/".join(parts)) if parts else "")
    return "/" + "/".join(parts)


def _normalize_guest(value: str, *, label: str) -> str:
    text = re.sub(r"/+", "/", str(value or "").strip())
    if not text.startswith("/"):
        raise ValueError(f"{label} must be an absolute guest path")
    if ".." in PurePosixPath(text).parts:
        raise ValueError(f"{label} cannot contain '..'")
    normalized = str(PurePosixPath(text))
    if normalized == "/":
        raise ValueError(f"{label} cannot be the guest root")
    return normalized


def _paths_overlap(left: str, right: str) -> bool:
    a = left.rstrip("/") or "/"
    b = right.rstrip("/") or "/"
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def validate_env_allowlist(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        name = str(raw or "").strip()
        if not name or not _ENV_NAME.fullmatch(name):
            raise ValueError(f"OCI environment allowlist contains invalid variable name: {raw!r}")
        if name.startswith(("DEVTOOL_", "PROOT_", "PD_")) or name in _MANAGED_ENV:
            raise ValueError(f"OCI environment allowlist cannot override a Devtool/runtime-managed variable: {name}")
        if _SENSITIVE_NAME.search(name):
            raise ValueError(f"OCI environment allowlist refuses secret-bearing variable names: {name}")
        if name not in seen:
            seen.add(name)
            result.append(name)
    return tuple(result)


def filter_oci_environment(environment: Mapping[str, str], allowlist: tuple[str, ...]) -> dict[str, str]:
    allowed = validate_env_allowlist(list(allowlist))
    result: dict[str, str] = {}
    for name in allowed:
        if name not in environment:
            continue
        value = str(environment[name])
        if not value:
            continue
        if "\x00" in value:
            raise ValueError(f"OCI environment value for {name} contains NUL and cannot be transported")
        result[name] = value
    return result


def _expand_home_shell(raw: str, output_var: str) -> str:
    quoted = shlex.quote(raw)
    return f'''raw={quoted}\ncase "$raw" in\n  '~') {output_var}="$HOME" ;;\n  '~/'*) {output_var}="$HOME/${{raw#\\~/}}" ;;\n  '$HOME') {output_var}="$HOME" ;;\n  '$HOME/'*) {output_var}="$HOME/${{raw#\\$HOME/}}" ;;\n  /*) {output_var}="$raw" ;;\n  *) echo 'OCI path must be absolute or HOME-relative' >&2; exit 64 ;;\nesac'''


def _no_symlink_function() -> str:
    return r'''assert_no_symlink_components() {
  target="$1"
  case "$target" in /*) ;; *) echo "non-absolute path: $target" >&2; exit 64;; esac
  rest="${target#/}"
  current=""
  while [ -n "$rest" ]; do
    case "$rest" in
      */*) component="${rest%%/*}"; rest="${rest#*/}" ;;
      *) component="$rest"; rest="" ;;
    esac
    [ -n "$component" ] || continue
    current="$current/$component"
    if [ -L "$current" ]; then
      echo "refusing symlink component in OCI bind source: $current" >&2
      exit 73
    fi
  done
}
'''

def _workspace_probe_script(workspace_root: str, workspace_id: str) -> str:
    root = _normalize_remote_root(workspace_root, label="OCI SSH workspace root")
    marker = shlex.quote(workspace_id)
    return f'''set -eu
if [ -n "${{PROOT_TMP_DIR:-}}" ] || [ -n "${{PROOT_LOADER:-}}" ]; then echo 'OCI workspace preparation refuses nested PRoot' >&2; exit 125; fi
{_no_symlink_function()}
{_expand_home_shell(root, 'workspace_root')}
workspace="$workspace_root/{workspace_id}"
[ -d "$workspace" ] || {{ echo 'managed SSH workspace is missing; synchronize it before OCI preparation' >&2; exit 66; }}
assert_no_symlink_components "$workspace"
for control in .devtool-workspace-id .devtool-remote-workspace.json .devtool-sync-state.json; do
  [ -f "$workspace/$control" ] || {{ echo "managed SSH workspace control file is missing: $control" >&2; exit 73; }}
  assert_no_symlink_components "$workspace/$control"
done
[ "$(cat "$workspace/.devtool-workspace-id")" = {marker} ] || {{ echo 'managed SSH workspace ownership marker mismatch' >&2; exit 73; }}
workspace_real="$(cd "$workspace" && pwd -P)"
command -v python3 >/dev/null 2>&1 || {{ echo 'python3 is required to verify managed SSH workspace metadata' >&2; exit 127; }}
python3 - "$workspace/.devtool-remote-workspace.json" "$workspace/.devtool-sync-state.json" {marker} "$workspace_real" <<'PYVERIFY'
import hashlib,json,sys
metadata_path,state_path,expected,workspace_real=sys.argv[1:]
try:
    metadata=json.load(open(metadata_path,encoding='utf-8'))
    state=json.load(open(state_path,encoding='utf-8'))
except (OSError,ValueError) as exc:
    raise SystemExit(f'managed SSH workspace metadata is missing or invalid: {{exc}}')
if metadata.get('workspace_id') != expected or metadata.get('instance_id') != expected or metadata.get('mode') != 'managed':
    raise SystemExit('managed SSH workspace metadata identity mismatch')
if metadata.get('remote_path') != workspace_real:
    raise SystemExit('managed SSH workspace metadata canonical path mismatch')
if state.get('state') != 'complete' or bool(state.get('recoverable')):
    raise SystemExit('managed SSH workspace synchronization is not complete')
sync_id=str(state.get('sync_id') or '')
if not sync_id or metadata.get('last_sync_id') != sync_id:
    raise SystemExit('managed SSH workspace metadata is stale relative to sync-state')
metadata_revision=metadata.get('source_revision')
state_revision=state.get('source_revision')
if not isinstance(metadata_revision,dict) or not isinstance(state_revision,dict) or metadata_revision != state_revision:
    raise SystemExit('managed SSH workspace source revision evidence mismatch')
print('sync_id='+sync_id)
revision_body=json.dumps(state_revision,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
print('source_revision_digest='+hashlib.sha256(revision_body).hexdigest())
PYVERIFY
printf 'workspace_id=%s\nworkspace_real=%s\nhome_real=%s\n' {marker} "$workspace_real" "$(cd "$HOME" && pwd -P)"
'''


def _cache_prepare_script(cache_root: str, workspace_id: str, workspace_real: str, *, dry_run: bool) -> str:
    root = _normalize_remote_root(cache_root, label="OCI cache root")
    mutate = "0" if dry_run else "1"
    dirs = " ".join(shlex.quote(name) for name in APPROVED_CACHES)
    return f'''set -eu
{_no_symlink_function()}
{_expand_home_shell(root, 'cache_base')}
home_real="$(cd "$HOME" && pwd -P)"
case "$cache_base/" in "$HOME"/*) ;; *) echo 'OCI cache root must stay under the remote HOME' >&2; exit 73;; esac
cache="$cache_base/{workspace_id}"
assert_no_symlink_components "$cache"
workspace={shlex.quote(workspace_real)}
# Reject lexical overlap before any mkdir so an invalid cache configuration can
# never mutate the authoritative managed workspace. Symlink components have
# already been rejected, so lexical and canonical containment cannot diverge
# through a link created outside Devtool's control.
case "$cache/" in "$workspace"/*) echo 'OCI cache root overlaps authoritative workspace' >&2; exit 73;; esac
case "$workspace/" in "$cache"/*) echo 'authoritative workspace overlaps OCI cache root' >&2; exit 73;; esac
cache_mutated=0
if [ {mutate} -eq 1 ]; then
  umask 077
  if [ ! -d "$cache" ]; then cache_mutated=1; fi
  mkdir -p "$cache"
  assert_no_symlink_components "$cache"
  for name in {dirs}; do
    if [ ! -d "$cache/$name" ]; then cache_mutated=1; fi
    mkdir -p "$cache/$name"
    assert_no_symlink_components "$cache/$name"
  done
fi
if [ -d "$cache" ]; then cache_real="$(cd "$cache" && pwd -P)"; else cache_real="$cache"; fi
case "$cache_real/" in "$home_real"/*) ;; *) echo 'OCI cache canonical path escapes remote HOME' >&2; exit 73;; esac
case "$cache_real/" in "$workspace"/*) echo 'OCI cache canonical path overlaps authoritative workspace' >&2; exit 73;; esac
case "$workspace/" in "$cache_real"/*) echo 'authoritative workspace overlaps OCI canonical cache root' >&2; exit 73;; esac
if [ -d "$cache" ]; then cache_exists=1; else cache_exists=0; fi
cache_ready=1
for name in {dirs}; do
  if [ ! -d "$cache/$name" ]; then cache_ready=0; continue; fi
  assert_no_symlink_components "$cache/$name"
done
printf 'cache_real=%s\ncache_mutated=%s\ncache_exists=%s\ncache_ready=%s\n' "$cache_real" "$cache_mutated" "$cache_exists" "$cache_ready"
'''


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _semantic_plan(config: SSHRunnerConfig, *, workspace_id: str, workspace_real: str, cache_real: str, source_sync_id: str, environment: Mapping[str, str]) -> OciWorkspacePlan:
    workspace_guest = _normalize_guest(WORKSPACE_GUEST, label="OCI workspace mount")
    cache_guest = _normalize_guest(CACHE_GUEST, label="OCI cache mount")
    if _paths_overlap(workspace_guest, cache_guest):
        raise ValueError("OCI workspace and cache guest mounts must be disjoint")
    if _paths_overlap(workspace_real, cache_real):
        raise ValueError("OCI workspace and persistent cache host paths must be disjoint")
    forwarded = filter_oci_environment(environment, tuple(config.oci_env_allowlist))
    binds = (
        OciWorkspaceBind(workspace_real, workspace_guest, "workspace"),
        OciWorkspaceBind(cache_real, cache_guest, "persistent-cache"),
    )
    return OciWorkspacePlan(
        profile=config.profile,
        workspace_id=workspace_id,
        remote_workspace=workspace_real,
        remote_cache_root=cache_real,
        source_sync_id=source_sync_id,
        working_directory=workspace_guest,
        binds=binds,
        managed_environment=dict(_MANAGED_ENV),
        forwarded_environment=forwarded,
    )


def _preflight_repository_identity(
    config: SSHRunnerConfig,
    *,
    repo: Path,
    run_id: str,
    node_id: str,
) -> tuple[str, str]:
    """Validate OCI-04 controller inputs and derive source/workspace identity.

    The managed SSH workspace identity includes ``remote.origin.url`` while its
    freshness contract records the local Git HEAD and exact porcelain status
    bytes. Git subprocesses are therefore owned by this canonical EXO node. The
    origin URL contributes only to an in-memory hash and is never persisted.
    """

    label = "Validate OCI workspace bind preflight"
    _runtime._start_node(run_id, node_id, label, "Validating provider, repository, bind paths, environment allowlist, and source identity")
    adapter = RunnerExecutionAdapter(
        run_id=run_id,
        parent_node_id=node_id,
        tool="git",
        source="oci-workspace",
        provenance={
            "transport": "local",
            "execution_provider": SSH_PROOT_OCI,
            "profile": config.profile,
            "operation": "OCI-04 workspace identity preflight",
        },
    )
    started = time.monotonic()
    active: subprocess.Popen[bytes] | None = None
    active_started = started
    active_finished = True

    def run_owned(argv: list[str], *, timeout: float, failure_kind: str) -> tuple[bytes, bytes]:
        nonlocal active, active_started, active_finished
        active_started = time.monotonic()
        try:
            active = _LOCAL_POPEN(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                env=_runtime._sanitized_subprocess_env(),
            )
        except OSError as exc:
            raise ValueError(f"unable to launch {failure_kind.replace('_', ' ')}: {exc}") from exc
        active_finished = False
        adapter.process_started(pid=active.pid, argv=argv, cwd=str(repo))
        try:
            out_b, err_b = active.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            active.kill()
            out_b, err_b = active.communicate()
            adapter.process_exited(
                exit_code=124,
                duration_seconds=time.monotonic() - active_started,
                failure_kind="timeout",
                evidence=(_runtime._safe_text((err_b or out_b or b"").decode("utf-8", errors="replace")[-2048:]),),
            )
            active_finished = True
            raise ValueError(f"{failure_kind.replace('_', ' ')} timed out") from exc
        adapter.process_exited(
            exit_code=int(active.returncode or 0),
            duration_seconds=time.monotonic() - active_started,
            failure_kind="" if active.returncode == 0 else failure_kind,
            evidence=() if active.returncode == 0 else (_runtime._safe_text((err_b or b"").decode("utf-8", errors="replace")[-2048:]),),
        )
        active_finished = True
        if active.returncode != 0:
            detail = _runtime._safe_text((err_b or b"").decode("utf-8", errors="replace").strip())
            raise ValueError(f"{failure_kind.replace('_', ' ')} failed with exit {active.returncode}: {detail or 'no diagnostic output'}")
        return out_b or b"", err_b or b""

    try:
        if config.execution_provider != SSH_PROOT_OCI:
            raise ValueError("OCI workspace preparation requires execution provider 'ssh-proot-oci'")
        if config.workspace_mode != "managed":
            raise ValueError("OCI workspace preparation requires workspace.mode='managed'; ephemeral/existing workspaces are not OCI-04 authoritative source mirrors")
        if not repo.is_dir():
            raise ValueError(f"OCI workspace repository does not exist: {repo}")
        _normalize_remote_root(config.workspace_root, label="OCI SSH workspace root")
        _normalize_remote_root(config.oci_cache_root, label="OCI cache root")
        validate_env_allowlist(list(config.oci_env_allowlist))

        origin = ""
        source_revision: dict[str, Any] = {
            "git_head": "",
            "working_tree_dirty": False,
            "working_tree_fingerprint": "",
        }
        if (repo / ".git").exists():
            git = shutil.which("git")
            if not git:
                raise ValueError("git is required to derive the managed SSH workspace identity for a Git repository")

            # Normalize the expected `git config --get` no-key exit 1 to a
            # successful wrapper process so canonical process state remains
            # truthful rather than recording an expected lookup miss as failed.
            origin_argv = [
                "/bin/sh", "-c",
                '"$1" -C "$2" config --get remote.origin.url; rc=$?; '
                '[ "$rc" -eq 0 ] && exit 0; [ "$rc" -eq 1 ] && exit 0; exit "$rc"',
                "oci04-git-origin", git, str(repo),
            ]
            origin_b, _ = run_owned(origin_argv, timeout=10, failure_kind="git_config_error")
            origin = origin_b.decode("utf-8", errors="replace").strip()

            status_argv = [git, "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"]
            status_b, _ = run_owned(status_argv, timeout=30, failure_kind="git_status_error")

            # An unborn repository legitimately has no HEAD. `git status`
            # above still proves the repository is readable; normalize the
            # absent HEAD to an empty string without creating a failed process.
            head_argv = [
                "/bin/sh", "-c",
                '"$1" -C "$2" rev-parse --verify HEAD 2>/dev/null || true',
                "oci04-git-head", git, str(repo),
            ]
            head_b, _ = run_owned(head_argv, timeout=10, failure_kind="git_head_error")
            source_revision = {
                "git_head": head_b.decode("utf-8", errors="replace").strip(),
                "working_tree_dirty": bool(status_b),
                "working_tree_fingerprint": hashlib.sha256(status_b).hexdigest(),
            }

        name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.name.strip()).strip("-._")[:48] or "repo"
        identity = f"path={repo}\norigin={origin}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        workspace_id = f"{name}-{digest}"
        revision_digest = hashlib.sha256(
            json.dumps(source_revision, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        _runtime._finish_node(
            run_id,
            node_id,
            status=ExecutionStatus.SUCCEEDED.value,
            exit_code=0,
            duration=time.monotonic() - started,
            reason="OCI-04 workspace preflight passed",
        )
        return workspace_id, revision_digest
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        if active is not None and not active_finished:
            try:
                if active.returncode is None:
                    active.kill()
                    active.communicate()
            except Exception:
                pass
            adapter.process_exited(
                exit_code=int(active.returncode if active.returncode is not None else 1),
                duration_seconds=time.monotonic() - active_started,
                failure_kind="workspace_preflight_error",
                evidence=(_runtime._safe_text(str(exc))[-2048:],),
            )
        message = _runtime._safe_text(f"OCI workspace preflight failed: {exc}")
        diagnostic_id = adapter.diagnostic(
            severity="error",
            code="OCI_WORKSPACE_PREFLIGHT",
            message=message,
            failure_kind="workspace_preflight_error",
            evidence=(message[-2048:],),
        )
        _runtime._finish_node(
            run_id,
            node_id,
            status=ExecutionStatus.FAILED.value,
            exit_code=65,
            duration=time.monotonic() - started,
            diagnostic_ids=(diagnostic_id,),
            reason="workspace bind preflight failed",
        )
        raise ValueError(message) from exc


def proot_login_options(plan: OciWorkspacePlan) -> tuple[str, ...]:
    """Return deterministic PRoot-Distro login options for OCI-05.

    This is provider-local command bookkeeping only.  It is intentionally not
    persisted as lifecycle truth.
    """
    result: list[str] = ["--work-dir", plan.working_directory]
    for bind in plan.binds:
        result.extend(["--bind", f"{bind.host}:{bind.guest}"])
    environment = dict(plan.managed_environment)
    environment.update(plan.forwarded_environment)
    for key in sorted(environment):
        result.extend(["--env", f"{key}={environment[key]}"])
    return tuple(result)


def oci_workspace_payload(
    config: SSHRunnerConfig,
    *,
    repo_root: str | Path,
    run_id: str,
    dry_run: bool = False,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve()
    repo_key = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()
    preflight = _runtime._stable_node_id(config, "oci04-workspace-preflight", repo_key)
    inspect = _runtime._stable_node_id(config, "oci04-workspace-inspect", repo_key)
    caches = _runtime._stable_node_id(config, "oci04-cache-prepare", repo_key)
    contract = _runtime._stable_node_id(config, "oci04-bind-contract", repo_key)
    nodes = (
        (preflight, "Validate OCI workspace bind preflight", ()),
        (inspect, "Inspect managed SSH workspace for OCI bind", (preflight,)),
        (caches, "Prepare persistent OCI workspace caches", (inspect,)),
        (contract, "Seal OCI workspace bind/environment contract", (caches,)),
    )
    run_dir = Path(_runtime.state_dir()) / "oci-workspace" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    bus = get_event_bus()
    projection = ExecutionProjectionSubscriber(
        store=bus.execution_store, run_id=run_id, directory=run_dir,
        history_directory=Path(_runtime.state_dir()) / "history", checkpoint_interval=1,
    )
    bus.subscribe_all(projection)
    started = time.monotonic()
    try:
        publish_event(RunStarted(run_id=run_id, command="oci workspace", project=str(repo), source="oci-workspace", metadata={"provider": SSH_PROOT_OCI}))
        for node_id, label, deps in nodes:
            publish_event(NodeDiscovered(run_id=run_id, node_id=node_id, label=label, kind="oci-workspace", depends_on=deps, source="oci-workspace"))
        publish_event(GraphSealed(run_id=run_id, reason="OCI-04 workspace preparation graph complete", source="oci-workspace"))
        workspace_id, source_revision_digest = _preflight_repository_identity(
            config, repo=repo, run_id=run_id, node_id=preflight,
        )
        inspected = _runtime._run_process(
            config, run_id=run_id, node_id=inspect, label="Inspect managed SSH workspace for OCI bind",
            argv=build_ssh_remote_sh_argv(config, _workspace_probe_script(config.workspace_root, workspace_id), tty=False),
            detail="Canonicalizing and verifying the managed SSH workspace without copying it into the image",
            timeout=_runtime._transport_timeout(), diagnostic_code="OCI_WORKSPACE_INSPECT",
            provenance_extra={"workspace_id": workspace_id, "workspace_mode": config.workspace_mode},
            required_output_keys=("workspace_id", "workspace_real", "sync_id", "source_revision_digest"),
            required_output_patterns={
                "workspace_id": re.escape(workspace_id),
                "workspace_real": r"/.+",
                "sync_id": r".+",
                "source_revision_digest": re.escape(source_revision_digest),
            },
        )
        values = _runtime._parse_key_values(inspected.stdout)
        workspace_real = str(values.get("workspace_real") or "")
        source_sync_id = str(values.get("sync_id") or "")
        try:
            prepared = _runtime._run_process(
                config, run_id=run_id, node_id=caches, label="Prepare persistent OCI workspace caches",
                argv=build_ssh_remote_sh_argv(config, _cache_prepare_script(config.oci_cache_root, workspace_id, workspace_real, dry_run=dry_run), tty=False),
                detail=("Validating persistent cache paths without mutation" if dry_run else "Creating repository-scoped persistent caches outside source synchronization"),
                timeout=_runtime._transport_timeout(), diagnostic_code="OCI_CACHE_PREPARE",
                provenance_extra={
                    "workspace_id": workspace_id,
                    "source_sync_id": source_sync_id,
                    "dry_run": dry_run,
                    "approved_caches": list(APPROVED_CACHES),
                },
                required_output_keys=("cache_real", "cache_mutated", "cache_exists", "cache_ready"),
                required_output_patterns={
                    "cache_real": r"/.+",
                    "cache_mutated": (r"0" if dry_run else r"[01]"),
                    "cache_exists": (r"[01]" if dry_run else r"1"),
                    "cache_ready": (r"[01]" if dry_run else r"1"),
                },
            )
        except Exception:
            _runtime._block_node(run_id, contract, blocked_by=(caches,), reason="Bind contract blocked because persistent cache preparation failed")
            raise
        cache_values = _runtime._parse_key_values(prepared.stdout)
        cache_real = str(cache_values.get("cache_real") or "")
        cache_exists = str(cache_values.get("cache_exists") or "") == "1"
        cache_mutated = str(cache_values.get("cache_mutated") or "") == "1"
        cache_ready = str(cache_values.get("cache_ready") or "") == "1"
        _runtime._start_node(run_id, contract, "Seal OCI workspace bind/environment contract", "Validating disjoint guest/host paths and environment allowlist")
        adapter = RunnerExecutionAdapter(
            run_id=run_id, parent_node_id=contract, tool="oci-workspace", source="oci-workspace",
            provenance=_runtime._provenance(
                config, operation="OCI-04 bind contract",
                extra={"workspace_id": workspace_id, "source_sync_id": source_sync_id},
            ),
        )
        try:
            plan = _semantic_plan(
                config,
                workspace_id=workspace_id,
                workspace_real=workspace_real,
                cache_real=cache_real,
                source_sync_id=source_sync_id,
                environment=(environment if environment is not None else os.environ),
            )
            evidence_dir = Path(_runtime.state_dir()) / "oci-workspace" / run_id
            evidence_dir.mkdir(parents=True, exist_ok=True)
            evidence_path = evidence_dir / "bind-plan.json"
            evidence = dict(redact_sensitive_value({
                "schema_version": 1,
                "kind": "devtool.oci04.workspace-bind-plan",
                "run_id": run_id,
                "dry_run": dry_run,
                "cache_exists": cache_exists,
                "cache_ready": cache_ready,
                "cache_mutated": cache_mutated,
                **plan.as_dict(),
            }))
            _atomic_json(evidence_path, evidence)
            artifact_id = adapter.artifact(path=str(evidence_path), producer_node_id=contract, producer_task="oci04-bind-contract", verification_status="verified")
        except (OSError, ValueError) as exc:
            message = _runtime._safe_text(f"OCI workspace bind contract failed: {exc}")
            diagnostic_id = adapter.diagnostic(
                severity="error", code="OCI_WORKSPACE_CONTRACT", message=message,
                failure_kind="workspace_contract_error", evidence=(message[-2048:],),
            )
            _runtime._finish_node(
                run_id, contract, status=ExecutionStatus.FAILED.value, exit_code=65,
                diagnostic_ids=(diagnostic_id,), reason="workspace bind contract validation failed",
            )
            raise ValueError(message) from exc
        _runtime._finish_node(run_id, contract, status=ExecutionStatus.SUCCEEDED.value, exit_code=0)
        publish_event(RunFinished(run_id=run_id, status=ExecutionStatus.SUCCEEDED.value, exit_code=0, duration_seconds=time.monotonic() - started, source="oci-workspace"))
        return {
            "schema_version": 1,
            "kind": "oci-workspace",
            "ok": True,
            "dry_run": dry_run,
            "cache_prepared": cache_ready,
            "cache_exists": cache_exists,
            "cache_mutated": cache_mutated,
            "run_id": run_id,
            "profile": config.profile,
            "destination": config.destination,
            "provider": SSH_PROOT_OCI,
            "node_ids": [item[0] for item in nodes],
            "plan": plan.as_dict(),
            "execution_dir": str(run_dir),
            "execution_json": str(run_dir / "execution.json"),
            "journal": str(run_dir / "execution-events.jsonl"),
            "checkpoint": str(run_dir / "execution-checkpoint.json"),
            "evidence_artifact": str(evidence_path),
            "evidence_artifact_id": artifact_id,
            "detail": ("OCI-04 managed workspace bind/cache plan validated without cache mutation" if dry_run else "OCI-04 managed workspace bind/cache contract ready"),
        }
    except Exception:
        snap = bus.execution_store.snapshot(run_id)
        if snap is not None:
            statuses = {node.node_id: node.status for node in snap.nodes}
            if statuses.get(inspect) in {ExecutionStatus.PLANNED.value, ExecutionStatus.PENDING.value, ExecutionStatus.READY.value}:
                _runtime._block_node(run_id, inspect, blocked_by=(preflight,), reason="Workspace inspection blocked because OCI-04 preflight failed")
            if statuses.get(caches) in {ExecutionStatus.PLANNED.value, ExecutionStatus.PENDING.value, ExecutionStatus.READY.value}:
                _runtime._block_node(run_id, caches, blocked_by=(inspect,), reason="Cache preparation blocked because workspace inspection failed")
            if statuses.get(contract) in {ExecutionStatus.PLANNED.value, ExecutionStatus.PENDING.value, ExecutionStatus.READY.value}:
                _runtime._block_node(run_id, contract, blocked_by=(caches,), reason="Bind contract blocked because cache preparation did not succeed")
        publish_event(RunFinished(run_id=run_id, status=ExecutionStatus.FAILED.value, exit_code=1, duration_seconds=time.monotonic() - started, source="oci-workspace"))
        raise
    finally:
        bus.unsubscribe_all(projection)
        projection.close()
