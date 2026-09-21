from __future__ import annotations

"""End-to-end qualification for named managed SSH workers.

The qualification intentionally exercises the same trust boundaries as normal
managed execution without requiring a project-specific Gradle task: worker
bootstrap/readiness, authoritative source synchronization, detached tmux
survival across SSH connections, tablet -> phone evidence return, and a second
incremental sync plan that must be clean when the phone source did not change.
"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
import uuid
from typing import Any

from .ssh_returns import build_return_rsync_argv
from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_remote_sh_argv, worker_session_name
from .ssh_worker import bootstrap_worker, probe_worker, worker_doctor_payload
from .ssh_workspace import resolve_workspace_spec, sync_workspace, plan_workspace_sync


def _remote_expr(value: str) -> str:
    text = str(value or "").strip()
    if text in {"~", "$HOME"}:
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME"/' + shlex.quote(text[2:])
    if text.startswith("$HOME/"):
        return '"$HOME"/' + shlex.quote(text[6:])
    return shlex.quote(text)


def _run_remote(config: SSHRunnerConfig, script: str, *, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        build_ssh_remote_sh_argv(config, script, tty=False),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _safe(value: str, fallback: str = "qualify") -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return cleaned[:80] or fallback


def _local_qualification_root(repo_root: Path, profile: str, nonce: str) -> Path:
    return repo_root / ".devtool" / "ssh-qualification" / _safe(profile, "worker") / nonce


def _start_tmux_probe(config: SSHRunnerConfig, *, nonce: str) -> tuple[str, str]:
    session = worker_session_name(config, operation="qualify", repository="worker", run_id=nonce)
    remote_root = str(config.run_state_root or "~/.cache/devtool/ssh-runs").rstrip("/") + "/qualification/" + nonce
    artifact = remote_root + "/artifact.txt"
    status = remote_root + "/status"
    command = (
        "sleep 1; umask 077; "
        f"printf '%s\\n' {shlex.quote(nonce)} > {_remote_expr(artifact)}; "
        f"printf '0\\n' > {_remote_expr(status)}"
    )
    script = f'''set -eu
root={_remote_expr(remote_root)}
mkdir -p "$root"
chmod 700 "$root" 2>/dev/null || true
rm -f -- "$root/artifact.txt" "$root/status"
tmux kill-session -t {shlex.quote(session)} >/dev/null 2>&1 || true
tmux new-session -d -s {shlex.quote(session)} {shlex.quote(command)}
printf '%s\n' {shlex.quote(session)}
'''
    completed = _run_remote(config, script)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH qualification tmux start failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return session, remote_root


def _wait_tmux_probe(config: SSHRunnerConfig, *, session: str, remote_root: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        script = f'''set +e
root={_remote_expr(remote_root)}
if [ -f "$root/status" ]; then
  printf 'state=complete\nstatus='; cat "$root/status"; printf 'active=0\n'; exit 0
fi
if tmux has-session -t {shlex.quote(session)} 2>/dev/null; then
  printf 'state=running\nstatus=\nactive=1\n'; exit 0
fi
printf 'state=missing\nstatus=\nactive=0\n'
'''
        completed = _run_remote(config, script)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"SSH qualification tmux status failed: {detail or completed.returncode}")
        values: dict[str, str] = {}
        for line in completed.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
        last = values.get("state", "")
        if last == "complete":
            status = values.get("status", "")
            if status != "0":
                raise ValueError(f"SSH qualification tmux phase completed with status {status or 'unknown'}")
            return {"state": "complete", "status": 0, "active": False}
        if last == "missing":
            raise ValueError("SSH qualification tmux phase disappeared without publishing status")
        time.sleep(0.2)
    raise ValueError(f"SSH qualification tmux phase timed out while state={last or 'unknown'}")


def _retrieve_probe_artifact(
    config: SSHRunnerConfig,
    *,
    remote_root: str,
    local_root: Path,
    remote_rsync: str,
    nonce: str,
) -> dict[str, Any]:
    local_root.mkdir(parents=True, exist_ok=True)
    argv = build_return_rsync_argv(
        config,
        remote_root=remote_root,
        local_repo_root=local_root,
        patterns=("artifact.txt", "status"),
        remote_rsync=remote_rsync or "rsync",
    )
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH qualification evidence return failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    artifact = local_root / "artifact.txt"
    if not artifact.is_file():
        raise ValueError("SSH qualification evidence return did not produce artifact.txt")
    content = artifact.read_text(encoding="utf-8").strip()
    if content != nonce:
        raise ValueError("SSH qualification returned artifact identity does not match the remote tmux phase")
    return {
        "ok": True,
        "path": str(artifact),
        "bytes": artifact.stat().st_size,
        "rsync_stdout": completed.stdout.strip(),
    }


def _cleanup_probe(config: SSHRunnerConfig, *, session: str, remote_root: str) -> None:
    script = (
        "set +e; "
        f"tmux kill-session -t {shlex.quote(session)} >/dev/null 2>&1 || true; "
        f"rm -rf -- {_remote_expr(remote_root)}"
    )
    _run_remote(config, script)


def qualify_worker(
    config: SSHRunnerConfig,
    repo_root: str | Path,
    *,
    dry_run: bool = False,
    bootstrap: bool = True,
    sync: bool = True,
) -> dict[str, Any]:
    if not config.profile:
        raise ValueError("SSH qualification requires a named worker profile")
    if config.mode != "commands":
        raise ValueError("SSH qualification requires commands mode")
    if sync and config.workspace_mode != "managed":
        raise ValueError("Full SSH qualification requires workspace.mode='managed'; use --no-sync for transport-only qualification")

    repo = Path(repo_root).expanduser().resolve()
    if sync and not repo.is_dir():
        raise ValueError(f"SSH qualification repository does not exist: {repo}")

    started = time.monotonic()
    doctor_before = worker_doctor_payload(config)
    bootstrap_payload: dict[str, Any] = {}
    worker = probe_worker(config)
    if worker.missing_required:
        if not bootstrap:
            raise ValueError("SSH worker is missing required tools: " + ", ".join(worker.missing_required))
        bootstrap_payload = bootstrap_worker(config, dry_run=dry_run)
        if not bool(bootstrap_payload.get("ok")):
            raise ValueError(str(bootstrap_payload.get("detail") or "SSH worker bootstrap failed"))
        if not dry_run:
            worker = probe_worker(config)

    if dry_run:
        planned_sync: dict[str, Any] = {}
        if sync:
            spec = resolve_workspace_spec(config, repo, remote_home=worker.home)
            if worker.tools.get("rsync"):
                planned_sync = plan_workspace_sync(config, spec)
            else:
                planned_sync = {
                    "ok": True,
                    "dry_run": True,
                    "deferred": True,
                    "detail": "rsync planning deferred until bootstrap installs rsync",
                }
        return {
            "schema_version": 1,
            "kind": "ssh-worker-qualification",
            "ok": True,
            "dry_run": True,
            "profile": config.profile,
            "destination": config.destination,
            "doctor": doctor_before,
            "bootstrap": bootstrap_payload,
            "sync": planned_sync,
            "checks": {
                "transport": "planned",
                "bootstrap": "planned" if worker.missing_required else "ready",
                "source_sync": "planned" if sync else "skipped",
                "detached_tmux": "planned",
                "return_transport": "planned",
                "second_sync_clean": "planned" if sync else "skipped",
            },
        }

    if worker.missing_required:
        raise ValueError("SSH worker remains incomplete after bootstrap: " + ", ".join(worker.missing_required))

    sync_payload: dict[str, Any] = {}
    second_plan: dict[str, Any] = {}
    sync_seconds = 0.0
    second_plan_seconds = 0.0
    if sync:
        spec = resolve_workspace_spec(config, repo, remote_home=worker.home)
        sync_started = time.monotonic()
        sync_payload = sync_workspace(config, spec, dry_run=False, retain_lease=False)
        sync_seconds = time.monotonic() - sync_started
        if not bool(sync_payload.get("ok")):
            raise ValueError(str(sync_payload.get("detail") or "SSH qualification source synchronization failed"))

    nonce = "q-" + uuid.uuid4().hex[:12]
    local_root = _local_qualification_root(repo, config.profile, nonce)
    tmux_started = time.monotonic()
    session, remote_root = _start_tmux_probe(config, nonce=nonce)
    try:
        tmux_state = _wait_tmux_probe(config, session=session, remote_root=remote_root)
        tmux_seconds = time.monotonic() - tmux_started
        return_started = time.monotonic()
        returned = _retrieve_probe_artifact(
            config,
            remote_root=remote_root,
            local_root=local_root,
            remote_rsync=worker.tools.get("rsync", "") or "rsync",
            nonce=nonce,
        )
        return_seconds = time.monotonic() - return_started
        if sync:
            plan_started = time.monotonic()
            second_plan = plan_workspace_sync(config, spec)
            second_plan_seconds = time.monotonic() - plan_started
            if int(second_plan.get("change_count", 0) or 0) != 0:
                raise ValueError(
                    "SSH qualification second incremental sync is not clean; the phone source changed during qualification "
                    f"or sync exclusions are incomplete ({second_plan.get('change_count')} pending change(s))"
                )
    finally:
        _cleanup_probe(config, session=session, remote_root=remote_root)

    total_seconds = time.monotonic() - started
    doctor_after = worker_doctor_payload(config)
    return {
        "schema_version": 1,
        "kind": "ssh-worker-qualification",
        "ok": True,
        "dry_run": False,
        "profile": config.profile,
        "destination": config.destination,
        "doctor": doctor_after,
        "bootstrap": bootstrap_payload,
        "sync": sync_payload,
        "second_sync_plan": second_plan,
        "tmux": {"session": session, **tmux_state},
        "return": returned,
        "performance": {
            "sync_seconds": round(sync_seconds, 3),
            "tmux_seconds": round(tmux_seconds, 3),
            "return_seconds": round(return_seconds, 3),
            "second_plan_seconds": round(second_plan_seconds, 3),
            "total_seconds": round(total_seconds, 3),
            "first_sync_changes": int(sync_payload.get("change_count", 0) or 0),
            "second_sync_changes": int(second_plan.get("change_count", 0) or 0),
            "returned_bytes": int(returned.get("bytes", 0) or 0),
        },
        "checks": {
            "transport": "pass",
            "bootstrap": "pass",
            "source_sync": "pass" if sync else "skipped",
            "detached_tmux": "pass",
            "return_transport": "pass",
            "second_sync_clean": "pass" if sync else "skipped",
        },
        "detail": "SSH managed worker qualification passed",
    }
