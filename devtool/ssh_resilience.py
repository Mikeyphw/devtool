from __future__ import annotations

"""Resilience and safety primitives for named SSH build workers.

SSHW-05 keeps the phone authoritative while making remote tmux execution
recoverable.  Run records live on the worker, managed workspaces use an atomic
lease, and management actions operate only on Devtool-owned session records.
"""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
from typing import Any, Iterable, Mapping

from .ssh_tmux_runner import SSHRunnerConfig, build_ssh_base_argv, build_ssh_remote_sh_argv
from .ssh_workspace import SSHWorkspaceSpec


_SENSITIVE_ENV_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS_PHRASE|PASSPHRASE|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL|AUTH|SIGNING|KEYSTORE|STORE_PASSWORD|KEY_PASSWORD)",
    re.IGNORECASE,
)


def _safe(value: str, fallback: str = "run", limit: int = 96) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return (text[:limit] or fallback)


def _remote_expr(value: str) -> str:
    text = str(value or "").strip()
    if text in {"~", "$HOME"}:
        return '"$HOME"'
    if text.startswith("~/"):
        return '"$HOME"/' + shlex.quote(text[2:])
    if text.startswith("$HOME/"):
        return '"$HOME"/' + shlex.quote(text[6:])
    return shlex.quote(text)


def _run_remote(
    config: SSHRunnerConfig,
    script: str,
    *,
    capture_output: bool = True,
    timeout: float = 30.0,
    input_text: str | None = None,
    tty: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        build_ssh_remote_sh_argv(config, script, tty=tty),
        check=False,
        capture_output=capture_output,
        text=True,
        input=input_text,
        timeout=timeout,
    )


def sensitive_env_keys(values: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in values if _SENSITIVE_ENV_RE.search(str(key))))


def split_sensitive_env(values: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    public: dict[str, str] = {}
    sensitive: dict[str, str] = {}
    for key, value in values.items():
        (sensitive if _SENSITIVE_ENV_RE.search(str(key)) else public)[str(key)] = str(value)
    return public, sensitive


def remote_run_state_dir(config: SSHRunnerConfig, run_id: str, session: str) -> str:
    return str(config.run_state_root or "~/.cache/devtool/ssh-runs").rstrip("/") + "/" + _safe(run_id) + "/" + _safe(session, "phase")


def remote_phase_state_paths(config: SSHRunnerConfig, run_id: str, session: str) -> dict[str, str]:
    root = remote_run_state_dir(config, run_id, session)
    return {
        "root": root,
        "metadata": root + "/metadata",
        "state": root + "/state",
        "status": root + "/status",
    }


@dataclass(slots=True)
class SSHWorkspaceLease:
    profile: str
    destination: str
    workspace_id: str
    instance_id: str
    lease_dir: str
    run_id: str
    session_prefix: str
    acquired_unix: int
    reclaimed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lease_dir(spec: SSHWorkspaceSpec) -> str:
    return spec.workspace_root.rstrip("/") + "/.leases/" + _safe(spec.instance_id, "workspace") + ".lock"


def acquire_workspace_lease(
    config: SSHRunnerConfig,
    spec: SSHWorkspaceSpec,
    *,
    run_id: str,
    session_prefix: str,
    runner: Any | None = None,
) -> SSHWorkspaceLease:
    if spec.mode == "existing":
        raise ValueError("Devtool workspace leases are only valid for managed/ephemeral SSH workspaces")
    lease_dir = _lease_dir(spec)
    now = int(time.time())
    stale_after = max(30, int(config.lease_timeout_seconds or 900))
    script = f'''set -eu
root={shlex.quote(spec.workspace_root)}
lease={shlex.quote(lease_dir)}
new_run={shlex.quote(_safe(run_id))}
new_session={shlex.quote(_safe(session_prefix, 'devtool'))}
now=$(date +%s 2>/dev/null || printf {now})
stale_after={stale_after}
mkdir -p "$root/.leases"
root_real="$(cd "$root" && pwd -P)"
leases_real="$(cd "$root/.leases" && pwd -P)"
case "$leases_real/" in "$root_real"/*) ;; *) echo 'devtool ssh: lease root escapes workspace root' >&2; exit 73;; esac
reclaimed=0
if ! mkdir "$lease" 2>/dev/null; then
  old_run="$(sed -n 's/^run_id=//p' "$lease/owner" 2>/dev/null | head -n1)"
  old_session="$(sed -n 's/^session_prefix=//p' "$lease/owner" 2>/dev/null | head -n1)"
  old_time="$(sed -n 's/^acquired_unix=//p' "$lease/owner" 2>/dev/null | head -n1)"
  case "$old_time" in ''|*[!0-9]*) old_time=0;; esac
  active=0
  if command -v tmux >/dev/null 2>&1 && [ -n "$old_session" ]; then
    for s in $(tmux list-sessions -F '#{{session_name}}' 2>/dev/null || true); do
      case "$s" in "$old_session"|"$old_session"-*) active=1;; esac
    done
  fi
  age=$((now-old_time))
  if [ "$active" -eq 0 ] && [ "$age" -ge "$stale_after" ]; then
    rm -rf -- "$lease"
    mkdir "$lease"
    reclaimed=1
  else
    printf 'owner_run=%s\nowner_session=%s\nage_seconds=%s\nactive=%s\n' "$old_run" "$old_session" "$age" "$active"
    exit 76
  fi
fi
umask 077
{{ printf 'run_id=%s\n' "$new_run"; printf 'session_prefix=%s\n' "$new_session"; printf 'acquired_unix=%s\n' "$now"; }} > "$lease/owner.tmp"
mv -f -- "$lease/owner.tmp" "$lease/owner"
printf 'reclaimed=%s\nacquired_unix=%s\n' "$reclaimed" "$now"
'''
    completed = (runner or _run_remote)(config, script)
    values: dict[str, str] = {}
    for line in str(completed.stdout or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if completed.returncode != 0:
        if completed.returncode == 76:
            owner = values.get("owner_run") or "unknown"
            session = values.get("owner_session") or "unknown"
            active = values.get("active") == "1"
            age = values.get("age_seconds") or "?"
            raise ValueError(
                f"SSH workspace is leased by another Devtool run (run={owner}, session={session}, active={'yes' if active else 'no'}, age={age}s). "
                "Use 'devtool ssh runs'/'devtool ssh recover' before retrying if that run is no longer valid."
            )
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH workspace lease acquisition failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return SSHWorkspaceLease(
        profile=config.profile,
        destination=config.destination,
        workspace_id=spec.workspace_id,
        instance_id=spec.instance_id,
        lease_dir=lease_dir,
        run_id=_safe(run_id),
        session_prefix=_safe(session_prefix, "devtool"),
        acquired_unix=int(values.get("acquired_unix") or now),
        reclaimed=values.get("reclaimed") == "1",
    )


def _release_lease_script(lease: SSHWorkspaceLease) -> str:
    return f'''set -eu
lease={shlex.quote(lease.lease_dir)}
expected={shlex.quote(lease.run_id)}
if [ ! -d "$lease" ]; then exit 0; fi
owner="$(sed -n 's/^run_id=//p' "$lease/owner" 2>/dev/null | head -n1)"
[ "$owner" = "$expected" ] || {{ echo 'devtool ssh: refusing to release a lease owned by another run' >&2; exit 77; }}
rm -rf -- "$lease"
'''


def build_release_lease_ssh_argv(config: SSHRunnerConfig, lease: SSHWorkspaceLease) -> list[str]:
    return build_ssh_remote_sh_argv(config, _release_lease_script(lease), tty=False)


def release_workspace_lease(config: SSHRunnerConfig, lease: SSHWorkspaceLease, *, runner: Any | None = None) -> bool:
    if runner is not None:
        completed = runner(config, _release_lease_script(lease))
    else:
        completed = subprocess.run(build_release_lease_ssh_argv(config, lease), check=False, capture_output=True, text=True, timeout=30)
    return completed.returncode == 0


def ensure_remote_capacity(config: SSHRunnerConfig, *, free_storage_kib: int) -> dict[str, Any]:
    required_mb = max(0, int(config.min_free_mb or 0))
    free_mb = max(0, int(free_storage_kib or 0) // 1024)
    if required_mb and free_mb and free_mb < required_mb:
        raise ValueError(
            f"SSH worker has only {free_mb} MiB free; managed execution requires at least {required_mb} MiB. "
            "Free storage on the worker or lower [ssh.hosts.*.resilience].min_free_mb explicitly."
        )
    return {"required_free_mb": required_mb, "free_mb": free_mb, "ok": not required_mb or not free_mb or free_mb >= required_mb}


def _records_script(config: SSHRunnerConfig) -> str:
    root = _remote_expr(config.run_state_root or "~/.cache/devtool/ssh-runs")
    return f'''set +e
root={root}
[ -d "$root" ] || exit 0
for meta in "$root"/*/*/metadata; do
  [ -f "$meta" ] || continue
  dir=${{meta%/metadata}}
  session=${{dir##*/}}
  run_dir=${{dir%/*}}
  run_id=${{run_dir##*/}}
  state="$(cat "$dir/state" 2>/dev/null | head -n1)"
  status="$(cat "$dir/status" 2>/dev/null | head -n1)"
  workspace_id="$(sed -n 's/^workspace_id=//p' "$meta" 2>/dev/null | head -n1)"
  workspace_path="$(sed -n 's/^workspace_path=//p' "$meta" 2>/dev/null | head -n1)"
  started="$(sed -n 's/^started_unix=//p' "$meta" 2>/dev/null | head -n1)"
  lease_dir="$(sed -n 's/^lease_dir=//p' "$meta" 2>/dev/null | head -n1)"
  active=0
  tmux has-session -t "$session" >/dev/null 2>&1 && active=1
  [ -n "$state" ] || state="$([ "$active" -eq 1 ] && printf running || printf unknown)"
  if [ "$active" -eq 0 ] && [ "$state" = running ] && [ -z "$status" ]; then state=stale; fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$run_id" "$session" "$state" "$status" "$active" "$workspace_id" "$workspace_path" "$started" "$lease_dir"
done
'''


def runs_payload(config: SSHRunnerConfig) -> dict[str, Any]:
    completed = _run_remote(config, _records_script(config))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH run discovery failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    records: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 9:
            continue
        run_id, session, state, status, active, workspace_id, workspace_path, started, lease_dir = fields
        records.append({
            "run_id": run_id,
            "session": session,
            "state": state,
            "status": int(status) if status.lstrip("-").isdigit() else None,
            "active": active == "1",
            "workspace_id": workspace_id,
            "workspace_path": workspace_path,
            "started_unix": int(started) if started.isdigit() else 0,
            "lease_dir": lease_dir,
        })
    records.sort(key=lambda item: (int(item.get("started_unix") or 0), str(item.get("session") or "")), reverse=True)
    return {
        "schema_version": 1,
        "kind": "ssh-runs",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "records": records,
        "active_count": sum(1 for item in records if item["active"]),
        "stale_count": sum(1 for item in records if item["state"] == "stale"),
    }


def _select_records(payload: Mapping[str, Any], *, run_id: str = "", session: str = "") -> list[dict[str, Any]]:
    records = [dict(item) for item in payload.get("records", []) if isinstance(item, dict)]
    if run_id:
        records = [item for item in records if str(item.get("run_id") or "") == _safe(run_id)]
    if session:
        records = [item for item in records if str(item.get("session") or "") == _safe(session, "phase")]
    if not run_id and not session and records:
        newest = str(records[0].get("run_id") or "")
        records = [item for item in records if str(item.get("run_id") or "") == newest]
    return records


def _phase_secret_cleanup_script(config: SSHRunnerConfig, *, run_id: str, session: str) -> str:
    path = remote_phase_state_paths(config, run_id, session)["root"] + "/secret-env.sh"
    return f"rm -f -- {_remote_expr(path)}"


def resume_remote_run(
    config: SSHRunnerConfig,
    *,
    run_id: str = "",
    session: str = "",
    attach: bool = True,
) -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id, session=session)
    if not matches:
        raise ValueError("No SSH worker run/session matched the resume request")
    # Prefer a currently active phase; otherwise report persisted completion.
    record = next((item for item in matches if item.get("active")), matches[0])
    if record.get("active") and attach:
        command = build_ssh_remote_sh_argv(
            config,
            "tmux attach-session -t " + shlex.quote(str(record["session"])),
            tty=True,
        )
        completed = subprocess.run(command, check=False)
        record["reattach_returncode"] = int(completed.returncode)
        refreshed = runs_payload(config)
        again = _select_records(refreshed, run_id=str(record["run_id"]), session=str(record["session"]))
        if again:
            record.update(again[0])

    # Once a recovered phase is definitively no longer active, remove any
    # staged secret and release the matching managed-workspace lease. This is
    # what lets a build that survived an SSH loss stop blocking the next run.
    if not record.get("active"):
        rid = _safe(str(record.get("run_id") or ""))
        sess = _safe(str(record.get("session") or ""), "phase")
        cleanup = _phase_secret_cleanup_script(config, run_id=rid, session=sess)
        cleanup += "\n" + _record_lease_cleanup_script(
            config, run_id=rid, lease_dir=str(record.get("lease_dir") or "")
        )
        completed = _run_remote(config, "set -eu\n" + cleanup)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"SSH recovered-run cleanup failed: {detail or completed.returncode}")

    return {
        "schema_version": 1,
        "kind": "ssh-run-resume",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "record": record,
        "detail": (
            "reattached to active tmux phase" if record.get("active") and attach
            else "active tmux phase available for reattach" if record.get("active")
            else "remote phase is no longer active; persisted status returned and worker lease reconciled"
        ),
    }


def _record_lease_cleanup_script(config: SSHRunnerConfig, *, run_id: str, lease_dir: str) -> str:
    """Return a guarded cleanup fragment for a lease referenced by a run record.

    Remote run metadata is treated as untrusted input: the lease must resolve
    directly below the configured Devtool workspace ``.leases`` directory and
    its owner must match the run being reconciled.
    """
    if not str(lease_dir or "").strip():
        return ":"
    return f'''
root={_remote_expr(config.workspace_root or "~/.cache/devtool/workspaces")}
lease={_remote_expr(lease_dir)}
expected={shlex.quote(_safe(run_id))}
if [ -d "$lease" ]; then
  root_real="$(cd "$root" 2>/dev/null && pwd -P)" || {{ echo 'devtool ssh: cannot resolve workspace root for lease cleanup' >&2; exit 78; }}
  lease_parent="${{lease%/*}}"
  parent_real="$(cd "$lease_parent" 2>/dev/null && pwd -P)" || {{ echo 'devtool ssh: cannot resolve lease parent' >&2; exit 78; }}
  [ "$parent_real" = "$root_real/.leases" ] || {{ echo 'devtool ssh: refusing lease cleanup outside managed lease root' >&2; exit 78; }}
  case "${{lease##*/}}" in *.lock) ;; *) echo 'devtool ssh: refusing non-lease cleanup target' >&2; exit 78;; esac
  owner="$(sed -n 's/^run_id=//p' "$lease/owner" 2>/dev/null | head -n1)"
  if [ "$owner" != "$expected" ]; then
    echo 'devtool ssh: refusing to release a lease owned by another run' >&2
    exit 77
  fi
  rm -rf -- "$lease"
fi
'''


def cancel_remote_run(config: SSHRunnerConfig, *, run_id: str = "", session: str = "") -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id, session=session)
    if not matches:
        raise ValueError("No SSH worker run/session matched the cancel request")
    # Never rewrite already-completed phase history. A run-level cancel targets
    # only currently active tmux phases; an explicit session must also be active.
    matches = [item for item in matches if item.get("active")]
    if not matches:
        return {
            "schema_version": 1, "kind": "ssh-run-cancel", "ok": True,
            "profile": config.profile, "destination": config.destination,
            "cancelled": [], "detail": "no active Devtool SSH phase matched the cancel request",
        }
    cancelled: list[dict[str, Any]] = []
    for item in matches:
        sess = _safe(str(item.get("session") or ""), "phase")
        rid = _safe(str(item.get("run_id") or ""))
        state_dir = remote_run_state_dir(config, rid, sess)
        lease_cleanup = _record_lease_cleanup_script(
            config, run_id=rid, lease_dir=str(item.get("lease_dir") or "")
        )
        script = f'''set -eu
session={shlex.quote(sess)}
state_dir={_remote_expr(state_dir)}
mkdir -p "$state_dir"
tmux kill-session -t "$session" >/dev/null 2>&1 || true
printf '130\n' > "$state_dir/status.tmp"; mv -f -- "$state_dir/status.tmp" "$state_dir/status"
printf 'cancelled\n' > "$state_dir/state.tmp"; mv -f -- "$state_dir/state.tmp" "$state_dir/state"
{_phase_secret_cleanup_script(config, run_id=rid, session=sess)}
{lease_cleanup}
'''
        completed = _run_remote(config, script)
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"SSH run cancellation failed for {sess}: {detail or completed.returncode}")
        cancelled.append({"run_id": rid, "session": sess, "status": 130, "state": "cancelled"})
    return {
        "schema_version": 1,
        "kind": "ssh-run-cancel",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "cancelled": cancelled,
    }


def recover_stale_runs(config: SSHRunnerConfig, *, run_id: str = "") -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id) if run_id else [item for item in payload["records"] if item.get("state") == "stale"]
    stale = [item for item in matches if item.get("state") == "stale"]
    recovered: list[dict[str, Any]] = []
    for item in stale:
        rid = _safe(str(item.get("run_id") or ""))
        sess = _safe(str(item.get("session") or ""), "phase")
        state_dir = remote_run_state_dir(config, rid, sess)
        lease_cleanup = _record_lease_cleanup_script(
            config, run_id=rid, lease_dir=str(item.get("lease_dir") or "")
        )
        script = f'''set -eu
state_dir={_remote_expr(state_dir)}
mkdir -p "$state_dir"
[ -f "$state_dir/status" ] || printf '255\n' > "$state_dir/status"
printf 'interrupted\n' > "$state_dir/state.tmp"; mv -f -- "$state_dir/state.tmp" "$state_dir/state"
{_phase_secret_cleanup_script(config, run_id=rid, session=sess)}
{lease_cleanup}
'''
        completed = _run_remote(config, script)
        if completed.returncode == 0:
            recovered.append({"run_id": rid, "session": sess, "state": "interrupted", "status": 255})
    return {
        "schema_version": 1,
        "kind": "ssh-run-recover",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "recovered": recovered,
        "detail": "stale remote run records reconciled" if recovered else "no stale remote run records found",
    }
