from __future__ import annotations

"""Adaptive SSH workspace transport and typed transfer telemetry.

SSH-01 keeps rsync as the reconciliation authority but avoids paying rsync's
per-file round trips for a cold managed workspace.  A cold/mostly-empty owned
workspace can be seeded by a streamed tar+zstd archive and is *always* followed
by the ordinary rsync reconciliation pass.  The remote archive is never stored.
"""

from dataclasses import asdict, dataclass
import fnmatch
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Iterable, TYPE_CHECKING

from .core.events import TransferProgressChanged, publish_event

if TYPE_CHECKING:
    from .ssh_tmux_runner import SSHRunnerConfig
    from .ssh_worker import SSHWorkerProbe
    from .ssh_workspace import SSHWorkspaceSpec, SSHWorkspaceStatus


@dataclass(slots=True)
class SSHTransferTelemetry:
    mode: str
    state: str = "running"
    source_bytes: int = 0
    transferred_bytes: int = 0
    source_files: int = 0
    transferred_files: int = 0
    remaining_files: int = 0
    current_path: str = ""
    throughput_bps: float = 0.0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None
    compression_ratio: float | None = None
    reconciliation_count: int = 0
    workspace_sync_id: str = ""
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def publish(self, *, run_id: str = "", node_id: str = "ssh-transport") -> None:
        try:
            complete = self.state == "complete"
            indeterminate = self.state == "running" and self.mode in {"archive-seed", "archive-zstd"}
            publish_event(TransferProgressChanged(
                run_id=run_id,
                node_id=node_id,
                source="ssh-transport",
                current=float(self.source_bytes if complete else self.transferred_bytes),
                total=float(self.source_bytes) if self.source_bytes else None,
                percent=100.0 if complete and self.source_bytes else None,
                indeterminate=indeterminate,
                mode=self.mode,
                state=self.state,
                source_bytes=self.source_bytes,
                transferred_bytes=self.transferred_bytes,
                source_files=self.source_files,
                transferred_files=self.transferred_files,
                remaining_files=self.remaining_files,
                current_path=self.current_path,
                throughput_bps=self.throughput_bps,
                elapsed_seconds=self.elapsed_seconds,
                eta_seconds=self.eta_seconds,
                compression_ratio=self.compression_ratio,
                reconciliation_count=self.reconciliation_count,
                workspace_sync_id=self.workspace_sync_id,
                detail=self.detail,
                metadata={"ssh_transport": self.as_dict()},
            ))
        except Exception:
            # Transport must never fail because an optional UI/event subscriber is
            # unavailable. The returned payload remains authoritative evidence.
            pass


@dataclass(frozen=True, slots=True)
class SSHSourceStats:
    files: int
    bytes: int
    nested_git_excludes: tuple[str, ...]
    unsafe_symlinks: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "bytes": self.bytes,
            "nested_git_excludes": list(self.nested_git_excludes),
            "unsafe_symlinks": list(self.unsafe_symlinks),
        }


@dataclass(frozen=True, slots=True)
class SSHSeedPlan:
    mode: str
    use_archive: bool
    reason: str
    source_files: int
    source_bytes: int
    remote_bytes: int
    destination_coverage: float
    local_tar: str
    local_zstd: str
    remote_tar: str
    remote_zstd: str
    nested_git_excludes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["nested_git_excludes"] = list(self.nested_git_excludes)
        return data


def _pattern_root(pattern: str) -> str:
    text = str(pattern or "").strip().lstrip("/")
    for suffix in ("/***", "/**", "/*"):
        if text.endswith(suffix):
            return text[: -len(suffix)].rstrip("/")
    return text.rstrip("/") if text.endswith("/") else text


def _excluded(rel: str, excludes: Iterable[str]) -> bool:
    rel = rel.strip("/")
    if not rel:
        return False
    for raw in excludes:
        pattern = str(raw or "").strip().lstrip("/")
        if not pattern:
            continue
        root = _pattern_root(pattern)
        if root and (rel == root or rel.startswith(root.rstrip("/") + "/")):
            return True
        normalized = pattern.replace("***", "**")
        if fnmatch.fnmatch(rel, normalized):
            return True
    return False


def _nested_git_roots(repo: Path) -> tuple[str, ...]:
    result: list[str] = []
    for root, dirs, _files in os.walk(repo, topdown=True, followlinks=False):
        current = Path(root)
        if current != repo and ((current / ".git").is_dir() or (current / ".git").is_file()):
            rel = current.relative_to(repo).as_posix()
            result.append(rel + "/***")
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if name != ".devtool-ssh-seed-tmp"]
    return tuple(sorted(set(result)))


def source_stats(repo_root: str | Path, excludes: Iterable[str]) -> SSHSourceStats:
    repo = Path(repo_root).expanduser().resolve()
    nested = _nested_git_roots(repo)
    all_excludes = tuple(excludes) + nested
    files = 0
    total = 0
    unsafe_symlinks: list[str] = []
    for root, dirs, names in os.walk(repo, topdown=True, followlinks=False):
        current = Path(root)
        rel_root = current.relative_to(repo).as_posix()
        if rel_root == ".":
            rel_root = ""
        kept: list[str] = []
        for name in dirs:
            rel = f"{rel_root}/{name}".strip("/")
            if not _excluded(rel, all_excludes):
                kept.append(name)
        dirs[:] = kept
        for name in names:
            path = current / name
            rel = f"{rel_root}/{name}".strip("/")
            if _excluded(rel, all_excludes):
                continue
            try:
                if path.is_symlink():
                    resolved = path.resolve(strict=False)
                    try:
                        resolved.relative_to(repo)
                    except ValueError:
                        unsafe_symlinks.append(rel)
                    files += 1
                    continue
                stat = path.stat()
            except OSError:
                continue
            if path.is_file():
                files += 1
                total += max(0, int(stat.st_size))
    return SSHSourceStats(files=files, bytes=total, nested_git_excludes=nested, unsafe_symlinks=tuple(sorted(unsafe_symlinks)))



def federation_transport_identity(repo_root: str | Path) -> dict[str, Any]:
    """Return/verify federation ownership + frozen Git lineage for transport.

    SSH does not invent a snapshot format.  When invoked as a federated child it
    consumes the already-exported GIT-FRX-07/GIT-FRX-08/Y03/GIT-FRX-09 identity
    and verifies the local checkout is still the planned Git source before bytes
    leave the controller.
    """
    from .invocation_origin import federation_lineage_from_environment
    from .federation_ownership import federation_operational_owner_from_environment

    repo = Path(repo_root).expanduser().resolve()
    lineage = federation_lineage_from_environment()
    if not lineage:
        return {"federated": False, "verified": True}
    owner = federation_operational_owner_from_environment(repo)
    git_lineage = dict(lineage.get("git_lineage") or {}) if isinstance(lineage.get("git_lineage"), dict) else {}
    expected_commit = str(git_lineage.get("start_commit") or "")
    expected_repo_id = str(git_lineage.get("repository_id") or "")
    actual_commit = ""
    actual_repo_id = ""
    try:
        from .git_state import collect_git_state
        state = collect_git_state(repo)
        actual_commit = str((state.head or {}).get("sha") or "")
        actual_repo_id = str(state.repository_id or "")
    except Exception as exc:
        if expected_commit or expected_repo_id:
            raise ValueError(f"Unable to verify frozen federated Git source before SSH transport: {exc}") from exc
    if expected_commit and actual_commit != expected_commit:
        raise ValueError(
            "Frozen federation source changed before SSH workspace transport: "
            f"expected commit {expected_commit}, got {actual_commit or '<unborn>'}"
        )
    if expected_repo_id and actual_repo_id != expected_repo_id:
        raise ValueError(
            "Federated Git repository identity changed before SSH workspace transport: "
            f"expected {expected_repo_id}, got {actual_repo_id or '<missing>'}"
        )
    return {
        "federated": True,
        "verified": True,
        "ownership": owner,
        "lineage": lineage,
        "frozen_plan_identity": str(git_lineage.get("frozen_plan_identity") or lineage.get("frozen_source_lock_digest") or ""),
        "frozen_plan_equivalence_identity": str(git_lineage.get("frozen_plan_equivalence_identity") or lineage.get("frozen_source_equivalence_digest") or ""),
        "frozen_repository_source_identity": str(git_lineage.get("frozen_repository_source_identity") or ""),
        "repository_id": actual_repo_id,
        "start_commit": actual_commit,
        "contract_ref": str(git_lineage.get("contract_ref") or lineage.get("contract_ref") or ""),
        "parent_run_id": str(lineage.get("parent_run_id") or ""),
        "parent_node_id": str(lineage.get("parent_node_id") or ""),
        "child_run_id": str(lineage.get("child_run_id") or ""),
    }

def _local_tool(name: str) -> str:
    env_name = f"DEVTOOL_{name.upper()}_COMMAND"
    raw = str(os.environ.get(env_name, name) or name).strip()
    return (shutil.which(raw) if os.sep not in raw else raw) or ""


def seed_plan(
    spec: "SSHWorkspaceSpec",
    status: "SSHWorkspaceStatus",
    worker: "SSHWorkerProbe",
    *,
    remote_bytes: int = 0,
) -> SSHSeedPlan:
    stats = source_stats(spec.repo_root, spec.excludes)
    local_tar = _local_tool("tar")
    local_zstd = _local_tool("zstd")
    remote_tar = str(worker.tools.get("tar") or "")
    remote_zstd = str(worker.tools.get("zstd") or "")
    min_bytes = max(0, int(os.environ.get("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", str(1024 * 1024)) or 0))
    min_files = max(0, int(os.environ.get("DEVTOOL_SSH_ARCHIVE_SEED_MIN_FILES", "64") or 0))
    coverage = min(1.0, (float(remote_bytes) / float(stats.bytes))) if stats.bytes else (1.0 if status.exists else 0.0)
    supported = bool(local_tar and local_zstd and remote_tar and remote_zstd)
    large_enough = stats.bytes >= min_bytes or stats.files >= min_files
    mostly_empty = (not status.exists) or coverage < 0.50
    if spec.mode == "existing":
        reason = "existing workspaces are reconciled in place with rsync"
        use_archive = False
    elif stats.unsafe_symlinks:
        reason = "archive seed disabled because source contains symlinks that escape the repository"
        use_archive = False
    elif not supported:
        reason = "tar/zstd unavailable locally or remotely; using rsync fallback"
        use_archive = False
    elif not large_enough:
        reason = "workspace is small; archive startup cost would outweigh the seed benefit"
        use_archive = False
    elif not mostly_empty:
        reason = "destination is already populated; warm rsync delta is preferred"
        use_archive = False
    else:
        reason = "cold/mostly-empty managed workspace; streamed zstd archive seed selected"
        use_archive = True
    return SSHSeedPlan(
        mode="archive-seed+reconcile" if use_archive else "rsync-delta",
        use_archive=use_archive,
        reason=reason,
        source_files=stats.files,
        source_bytes=stats.bytes,
        remote_bytes=max(0, int(remote_bytes)),
        destination_coverage=coverage,
        local_tar=local_tar,
        local_zstd=local_zstd,
        remote_tar=remote_tar,
        remote_zstd=remote_zstd,
        nested_git_excludes=stats.nested_git_excludes,
    )


def _tar_exclude_args(excludes: Iterable[str]) -> list[str]:
    args: list[str] = []
    for raw in excludes:
        root = _pattern_root(raw)
        if not root:
            continue
        # GNU/bsdtar both understand --exclude=<pattern>; excluding the root
        # prevents accidental traversal into another repository boundary.
        args.append(f"--exclude=./{root}")
        args.append(f"--exclude={root}")
    return args


def run_archive_seed(
    config: "SSHRunnerConfig",
    spec: "SSHWorkspaceSpec",
    plan: SSHSeedPlan,
    *,
    sync_id: str,
) -> dict[str, Any]:
    if not plan.use_archive:
        return {"ok": True, "used": False, "plan": plan.as_dict(), "detail": plan.reason}
    from .ssh_tmux_runner import build_ssh_remote_sh_argv

    started = time.monotonic()
    telemetry = SSHTransferTelemetry(
        mode="archive-seed",
        state="running",
        source_bytes=plan.source_bytes,
        source_files=plan.source_files,
        remaining_files=plan.source_files,
        workspace_sync_id=sync_id,
        detail="streaming tar through zstd to remote workspace",
    )
    telemetry.publish(run_id=str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or ""))

    excludes = tuple(spec.excludes) + tuple(plan.nested_git_excludes)
    tar_argv = [plan.local_tar, "-C", spec.repo_root, *_tar_exclude_args(excludes), "-cf", "-", "."]
    zstd_argv = [plan.local_zstd, "-1", "-T0", "-q", "-c"]
    remote_script = (
        "set -eu; umask 077; "
        f"{shlex_quote(plan.remote_zstd)} -d -q -c | "
        f"{shlex_quote(plan.remote_tar)} -C {shlex_quote(spec.remote_path)} -xf -"
    )
    ssh_argv = build_ssh_remote_sh_argv(config, remote_script, tty=False)

    tar_proc = subprocess.Popen(tar_argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert tar_proc.stdout is not None
    zstd_proc = subprocess.Popen(zstd_argv, stdin=tar_proc.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tar_proc.stdout.close()
    assert zstd_proc.stdout is not None
    ssh_proc = subprocess.Popen(ssh_argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timeout = max(30.0, float(os.environ.get("DEVTOOL_SSH_ARCHIVE_TIMEOUT", "900") or 900))
    deadline = time.monotonic() + timeout
    transferred = 0
    last_publish = started
    try:
        assert ssh_proc.stdin is not None
        while True:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(ssh_argv, timeout)
            chunk = zstd_proc.stdout.read(256 * 1024)
            if not chunk:
                break
            ssh_proc.stdin.write(chunk)
            transferred += len(chunk)
            now = time.monotonic()
            if now - last_publish >= 0.25:
                elapsed = max(0.000001, now - started)
                telemetry.transferred_bytes = transferred
                telemetry.elapsed_seconds = elapsed
                telemetry.throughput_bps = float(transferred) / elapsed
                telemetry.compression_ratio = (
                    float(transferred) / float(plan.source_bytes) if plan.source_bytes else None
                )
                telemetry.publish(run_id=str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or ""))
                last_publish = now
        ssh_proc.stdin.close()
        ssh_proc.stdin = None
        remaining = max(0.001, deadline - time.monotonic())
        ssh_out, ssh_err = ssh_proc.communicate(timeout=remaining)
    except (subprocess.TimeoutExpired, BrokenPipeError):
        ssh_proc.kill()
        zstd_proc.kill()
        tar_proc.kill()
        ssh_out, ssh_err = ssh_proc.communicate()
        ssh_err = (ssh_err or b"") + b"\narchive seed timed out"
    zstd_err = zstd_proc.stderr.read() if zstd_proc.stderr is not None else b""
    tar_err = tar_proc.stderr.read() if tar_proc.stderr is not None else b""
    zstd_rc = zstd_proc.wait()
    tar_rc = tar_proc.wait()
    elapsed = max(0.000001, time.monotonic() - started)
    ok = tar_rc == 0 and zstd_rc == 0 and ssh_proc.returncode == 0
    telemetry.state = "complete" if ok else "failed"
    telemetry.transferred_bytes = transferred
    telemetry.transferred_files = plan.source_files if ok else 0
    telemetry.remaining_files = 0 if ok else plan.source_files
    telemetry.elapsed_seconds = elapsed
    telemetry.throughput_bps = float(transferred) / elapsed if transferred else 0.0
    telemetry.eta_seconds = 0.0 if ok else None
    telemetry.compression_ratio = float(transferred) / float(plan.source_bytes) if plan.source_bytes else None
    telemetry.detail = "archive seed complete; rsync reconciliation required" if ok else "archive seed failed; caller should fall back to rsync"
    telemetry.publish(run_id=str(config.operation_id or os.environ.get("DEVTOOL_RUN_ID") or ""))
    if not ok:
        from .ssh_multiplex import disable_for_operation, multiplex_failure
        multiplex_detail = (ssh_err or b"").decode("utf-8", errors="replace")
        if multiplex_failure(multiplex_detail):
            disable_for_operation(config, multiplex_detail.strip() or "archive seed multiplexing failed")
    diagnostics = b"\n".join(chunk for chunk in (tar_err, zstd_err, ssh_err, ssh_out) if chunk).decode("utf-8", errors="replace").strip()
    return {
        "ok": ok,
        "used": True,
        "mode": "archive-seed",
        "plan": plan.as_dict(),
        "telemetry": telemetry.as_dict(),
        "returncodes": {"tar": tar_rc, "zstd": zstd_rc, "ssh": int(ssh_proc.returncode or 0)},
        "detail": diagnostics or telemetry.detail,
    }


def shlex_quote(value: str) -> str:
    import shlex
    return shlex.quote(str(value))


__all__ = [
    "SSHSeedPlan",
    "SSHSourceStats",
    "SSHTransferTelemetry",
    "federation_transport_identity",
    "run_archive_seed",
    "seed_plan",
    "source_stats",
]
