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
    # Keep management/history traffic on the same SSH-01 operation-scoped
    # multiplex contract as execution. Mux-specific failures transparently
    # retry without ControlMaster while ordinary transport failures remain
    # visible to the caller for reconnect/recovery policy.
    from .ssh_multiplex import run_with_multiplex_fallback
    return run_with_multiplex_fallback(
        config, build_ssh_remote_sh_argv(config, script, tty=tty),
        check=False, capture_output=capture_output, text=True,
        input=input_text, timeout=timeout,
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
        "stdout": root + "/stdout.log",
        "stderr": root + "/stderr.log",
        "events": root + "/events.jsonl",
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
    # Keep one stable tabular transport from the remote worker. Values persisted
    # by phase_metadata_lines are newline-free/redacted, while numeric/output
    # state is computed from the durable run directory at inspection time.
    metadata_keys = (
        "workspace_id", "workspace_path", "started_unix", "finished_unix", "lease_dir",
        "execution_mode", "execution_provider", "oci_phase_node_id", "ssh_source_sync_id",
        "oci_container", "oci_image_reference", "oci_image_digest", "oci_definition_digest",
        "oci_build_context_sha256", "remote_log_path", "stdout_path", "stderr_path",
        "ssh_repository_fingerprint", "ssh_repository_root", "ssh_target", "ssh_phase",
        "ssh_contract_type", "ssh_contract_name", "ssh_contract_ref", "ssh_frozen_source_lock_digest",
        "ssh_confirmation_mode", "tmux_window", "tmux_pane", "federation_parent_run_id", "federation_parent_node_id",
        "federation_child_run_id", "federation_child_repository", "federation_child_target",
        "federation_child_workflow", "federation_child_contract", "federation_contract_ref",
    )
    extracts = "\n".join(
        "  {key}=\"$(sed -n 's/^{key}=//p' \"$meta\" 2>/dev/null | head -n1)\"".format(key=key)
        for key in metadata_keys
    )
    values = [
        '"$run_id"', '"$session"', '"$state"', '"$status"', '"$active"',
        *(f'"${key}"' for key in metadata_keys),
        '"$stdout_exists"', '"$stdout_size"', '"$stderr_exists"', '"$stderr_size"',
    ]
    fmt = "%s" + "\\t%s" * (len(values) - 1) + "\\n"
    joined = " ".join(values)
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
{extracts}
  stdout_exists=0; stdout_size=0; stderr_exists=0; stderr_size=0
  [ -f "$dir/stdout.log" ] && stdout_exists=1 && stdout_size="$(wc -c < "$dir/stdout.log" 2>/dev/null || printf 0)"
  [ -f "$dir/stderr.log" ] && stderr_exists=1 && stderr_size="$(wc -c < "$dir/stderr.log" 2>/dev/null || printf 0)"
  active=0
  tmux has-session -t "$session" >/dev/null 2>&1 && active=1
  [ -n "$state" ] || state="$([ "$active" -eq 1 ] && printf running || printf unknown)"
  if [ "$active" -eq 0 ] && [ "$state" = running ] && [ -z "$status" ]; then state=stale; fi
  printf '{fmt}' {joined}
done
'''


def _output_availability(*, state: str, active: bool, stdout_exists: bool, stderr_exists: bool) -> str:
    exists = stdout_exists or stderr_exists
    if active or state == "running":
        return "still-writing" if exists else "pending"
    if state in {"stale", "interrupted", "cancelled"}:
        return "interrupted" if exists else "unavailable"
    if state == "completed":
        return "complete" if exists else "unavailable"
    return "partial" if exists else "unavailable"


def runs_payload(config: SSHRunnerConfig) -> dict[str, Any]:
    completed = _run_remote(config, _records_script(config))
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH run discovery failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    metadata_keys = (
        "workspace_id", "workspace_path", "started_unix", "finished_unix", "lease_dir",
        "execution_mode", "execution_provider", "phase_node_id", "source_sync_id",
        "oci_container", "oci_image_reference", "oci_image_digest", "oci_definition_digest",
        "oci_build_context_sha256", "remote_log_path", "stdout_path", "stderr_path",
        "repository_fingerprint", "repository_root", "target", "phase",
        "contract_type", "contract_name", "contract_ref", "frozen_source_lock_digest",
        "confirmation_mode", "tmux_window", "tmux_pane", "federation_parent_run_id", "federation_parent_node_id",
        "federation_child_run_id", "federation_child_repository", "federation_child_target",
        "federation_child_workflow", "federation_child_contract", "federation_contract_ref",
    )
    records: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        run_id, session, state, status, active = fields[:5]
        # SSHW-05 emitted a compact 9-column record. Preserve compatibility
        # while SSH-02 extends the transport with canonical identity/output
        # fields. Old history must remain readable after the upgrade.
        if len(fields) <= 9:
            legacy = fields + [""] * (9 - len(fields))
            record = {key: "" for key in metadata_keys}
            record.update({
                "workspace_id": legacy[5],
                "workspace_path": legacy[6],
                "started_unix": legacy[7],
                "lease_dir": legacy[8],
            })
            stdout_exists = stdout_size = stderr_exists = stderr_size = "0"
        else:
            minimum = 5 + len(metadata_keys) + 4
            fields.extend([""] * (minimum - len(fields)))
            meta_values = fields[5:5 + len(metadata_keys)]
            tail = fields[5 + len(metadata_keys):5 + len(metadata_keys) + 4]
            record = dict(zip(metadata_keys, meta_values))
            stdout_exists, stdout_size, stderr_exists, stderr_size = tail
        record.update({
            "run_id": run_id,
            "session": session,
            "state": state,
            "status": int(status) if status.lstrip("-").isdigit() else None,
            "active": active == "1",
            "started_unix": int(record.get("started_unix") or 0) if str(record.get("started_unix") or "").isdigit() else 0,
            "finished_unix": int(record.get("finished_unix") or 0) if str(record.get("finished_unix") or "").isdigit() else 0,
            "stdout_exists": stdout_exists == "1",
            "stdout_size": int(stdout_size) if stdout_size.isdigit() else 0,
            "stderr_exists": stderr_exists == "1",
            "stderr_size": int(stderr_size) if stderr_size.isdigit() else 0,
        })
        record["output_state"] = _output_availability(
            state=state, active=record["active"], stdout_exists=record["stdout_exists"], stderr_exists=record["stderr_exists"]
        )
        record["duration_seconds"] = max(
            0,
            int(record.get("finished_unix") or 0) - int(record.get("started_unix") or 0),
        ) if record.get("finished_unix") and record.get("started_unix") else 0
        record["typed_contract"] = {
            "repository": str(record.get("federation_child_repository") or ""),
            "target": str(record.get("federation_child_target") or record.get("target") or ""),
            "type": str(record.get("contract_type") or ""),
            "name": str(record.get("contract_name") or ""),
            "ref": str(record.get("federation_contract_ref") or record.get("contract_ref") or ""),
        }
        record["lineage"] = {
            "parent_run_id": str(record.get("federation_parent_run_id") or ""),
            "parent_node_id": str(record.get("federation_parent_node_id") or ""),
            "child_run_id": str(record.get("federation_child_run_id") or ""),
        }
        records.append(record)
    records.sort(key=lambda item: (int(item.get("started_unix") or 0), str(item.get("session") or "")), reverse=True)
    run_ids = {str(item.get("run_id") or "") for item in records if str(item.get("run_id") or "")}
    return {
        "schema_version": 2,
        "kind": "ssh-runs",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "records": records,
        "run_count": len(run_ids),
        "active_count": sum(1 for item in records if item["active"]),
        "stale_count": sum(1 for item in records if item["state"] == "stale"),
        "output_available_count": sum(1 for item in records if item["output_state"] not in {"unavailable", "pending"}),
    }

def _select_records(payload: Mapping[str, Any], *, run_id: str = "", session: str = "", node_id: str = "") -> list[dict[str, Any]]:
    records = [dict(item) for item in payload.get("records", []) if isinstance(item, dict)]
    if run_id:
        records = [item for item in records if str(item.get("run_id") or "") == _safe(run_id)]
    if session:
        records = [item for item in records if str(item.get("session") or "") == _safe(session, "phase")]
    if node_id:
        wanted = str(node_id).strip()
        records = [item for item in records if str(item.get("phase_node_id") or "") == wanted]
    if not run_id and not session and not node_id and records:
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
    node_id: str = "",
    attach: bool = True,
) -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id, session=session, node_id=node_id)
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
        "recovery": {
            "intent": "reattach" if record.get("active") and attach else "inspect" if record.get("active") else "reconcile-completed",
            "rerun_required": False,
            "output_state": str(record.get("output_state") or "unavailable"),
            "lease_state": "held" if record.get("active") and record.get("lease_dir") else "reconciled" if record.get("lease_dir") else "none",
            "resumable": bool(record.get("active")),
        },
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


def cancel_remote_run(config: SSHRunnerConfig, *, run_id: str = "", session: str = "", node_id: str = "") -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id, session=session, node_id=node_id)
    if not matches:
        raise ValueError("No SSH worker run/session matched the cancel request")
    # Never rewrite already-completed phase history. A run-level cancel targets
    # only currently active tmux phases; an explicit session must also be active.
    matches = [item for item in matches if item.get("active")]
    if not matches:
        return {
            "schema_version": 1, "kind": "ssh-run-cancel", "ok": True,
            "profile": config.profile, "destination": config.destination,
            "cancelled": [], "recovery": {"intent": "cancel", "rerun_required": False, "resumable": False},
            "detail": "no active Devtool SSH phase matched the cancel request",
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
        "recovery": {"intent": "cancel", "rerun_required": False, "resumable": False},
    }


def recover_stale_runs(config: SSHRunnerConfig, *, run_id: str = "", node_id: str = "") -> dict[str, Any]:
    payload = runs_payload(config)
    matches = _select_records(payload, run_id=run_id, node_id=node_id) if (run_id or node_id) else [item for item in payload["records"] if item.get("state") == "stale"]
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
    # SSH-03 extends recovery to Devtool-owned workspace leases whose owner run
    # has disappeared.  The lifecycle helper is path-constrained and only
    # removes old, inactive orphans; active/young/corrupt leases remain
    # protected and visible in the response.
    from .ssh_lifecycle import orphan_lease_payload
    try:
        orphan_repair = orphan_lease_payload(config, repair=True)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        orphan_repair = {
            "schema_version": 1, "kind": "ssh-orphan-lease-audit", "ok": False,
            "records": [], "orphan_count": 0, "repaired_count": 0, "detail": str(exc),
        }
    return {
        "schema_version": 2,
        "kind": "ssh-run-recover",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "recovered": recovered,
        "orphan_leases": orphan_repair,
        "recovery": {
            "intent": "mark-interrupted-release-owned-lease-and-repair-safe-orphans",
            "rerun_required": bool(recovered),
            "resumable": False,
            "successful_phases_rerun": False,
            "orphan_leases_repaired": int(orphan_repair.get("repaired_count") or 0),
        },
        "detail": (
            f"reconciled {len(recovered)} stale phase(s) and {int(orphan_repair.get('repaired_count') or 0)} orphan lease(s)"
            if recovered or orphan_repair.get("repaired_count")
            else "no stale remote run records or safely-repairable orphan leases found"
        ),
    }
