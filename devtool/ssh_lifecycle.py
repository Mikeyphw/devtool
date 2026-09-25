from __future__ import annotations

"""SSH-03 lifecycle, host identity, capacity, and safe GC primitives.

This module deliberately reuses the SSH-02 persisted run directory as the
single history/lifecycle authority.  It does not introduce a second database.
Host identity evidence is derived from OpenSSH known_hosts, and destructive
cleanup is constrained to Devtool-owned run/lease roots with path checks.
"""

from collections import defaultdict
import base64
import hashlib
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
from typing import Any, Mapping

from .ssh_tmux_runner import SSHRunnerConfig
from .ssh_resilience import _remote_expr, _run_remote, _safe, runs_payload

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_TERMINAL_GC_STATES = {"completed", "cancelled"}
_RECOVERY_SENSITIVE_STATES = {"running", "stale", "interrupted", "unknown"}


def _sha256_fingerprint(public_key_b64: str) -> str:
    try:
        raw = base64.b64decode(public_key_b64.encode("ascii"), validate=True)
    except Exception:
        return ""
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _known_hosts_query(config: SSHRunnerConfig) -> str:
    host = str(config.host or "").strip()
    if not host:
        return ""
    return host if int(config.port or 22) == 22 else f"[{host}]:{int(config.port)}"


def host_identity_payload(config: SSHRunnerConfig, *, probe: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return non-secret OpenSSH host identity evidence.

    We intentionally use ``ssh-keygen -F`` so hashed known_hosts entries remain
    supported and no parallel host-key database is created.  Only algorithm and
    SHA-256 fingerprints are exposed; public key material is never returned.
    """

    query = _known_hosts_query(config)
    explicit = str(config.known_hosts or "").strip()
    path = str(Path(explicit).expanduser()) if explicit else ""
    executable = shutil.which("ssh-keygen") or ""
    entries: list[dict[str, str]] = []
    lookup_error = ""
    lookup_source = path or "openssh-default-known-hosts"

    if query and executable:
        argv = [executable, "-F", query]
        if path:
            argv.extend(["-f", path])
        try:
            completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            lookup_error = str(exc)
        else:
            # ssh-keygen -F returns 1 for no match; that is not an operational error.
            if completed.returncode not in {0, 1}:
                lookup_error = (completed.stderr or completed.stdout or "").strip()
            for raw in str(completed.stdout or "").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                fields = line.split()
                if len(fields) < 3:
                    continue
                algorithm = fields[-2]
                fingerprint = _sha256_fingerprint(fields[-1])
                if not fingerprint:
                    continue
                record = {"algorithm": algorithm, "fingerprint": fingerprint}
                if record not in entries:
                    entries.append(record)

    worker = dict(probe or {})
    policy = str(config.host_key_policy or "system")
    if entries:
        state = "recorded"
    elif lookup_error:
        state = "lookup-error"
    elif policy == "system" and not explicit:
        state = "system-managed-or-unrecorded"
    else:
        state = "unrecorded"

    return {
        "schema_version": 1,
        "kind": "ssh-host-identity",
        "profile": config.profile,
        "destination": config.destination,
        "host": config.host,
        "port": int(config.port or 22),
        "query": query,
        "policy": policy,
        "known_hosts": path,
        "source": lookup_source,
        "state": state,
        "entries": entries,
        "algorithms": sorted({item["algorithm"] for item in entries}),
        "fingerprints": [item["fingerprint"] for item in entries],
        "lookup_error": lookup_error,
        "openssh_enforced": policy in {"strict", "accept-new", "system"},
        "key_change_diagnostic": (
            "OpenSSH host-key verification is authoritative; changed keys are surfaced by the configured "
            "StrictHostKeyChecking/UserKnownHostsFile policy rather than a Devtool-owned key database."
        ),
        "worker_identity": {
            "platform": str(worker.get("platform") or ""),
            "os_name": str(worker.get("os_name") or ""),
            "architecture": str(worker.get("architecture") or ""),
            "home": str(worker.get("home") or ""),
            "platform_family": (
                "termux" if str(worker.get("platform") or "") == "termux"
                else "linux" if str(worker.get("os_name") or "").lower() == "linux"
                else "other"
            ),
            "qualified_platform": bool(
                str(worker.get("architecture") or "")
                and (str(worker.get("platform") or "") == "termux" or str(worker.get("os_name") or "").lower() == "linux")
            ),
        },
    }


def repository_apparent_size_bytes(repo_root: str | Path) -> int:
    """Estimate source payload size without runtime/VCS caches.

    This is intentionally an estimate, not a promise.  It is used only to avoid
    obviously unsafe ENOSPC launches and never as a performance threshold.
    """

    root = Path(repo_root).expanduser().resolve()
    total = 0
    skip_dirs = {".git", "__pycache__", ".pytest_cache"}
    for current, dirs, files in os.walk(root):
        rel = Path(current).resolve().relative_to(root)
        dirs[:] = [d for d in dirs if d not in skip_dirs and not (rel == Path(".devtool") and d in {"cache", "reports"})]
        for name in files:
            path = Path(current) / name
            try:
                if path.is_symlink():
                    continue
                total += max(0, int(path.stat().st_size))
            except OSError:
                continue
    return total


def estimate_execution_capacity(
    config: SSHRunnerConfig,
    *,
    repo_root: str | Path,
    free_storage_kib: int,
) -> dict[str, Any]:
    repo_bytes = repository_apparent_size_bytes(repo_root)
    repo_mib = int(math.ceil(repo_bytes / (1024 * 1024))) if repo_bytes else 0
    # Managed/ephemeral execution may briefly need source + delta staging +
    # outputs.  Existing workspaces need less source headroom, but retain a
    # minimum working reserve.  The constants are conservative capacity
    # reserves, not pass/fail performance timing thresholds.
    source_multiplier = 2.25 if config.workspace_mode in {"managed", "ephemeral"} else 0.75
    estimated_work_mib = int(math.ceil(repo_mib * source_multiplier)) + 256
    configured_floor_mib = max(0, int(config.min_free_mb or 0))
    required_mib = max(configured_floor_mib, estimated_work_mib)
    free_mib = max(0, int(free_storage_kib or 0) // 1024)
    verified = free_mib > 0
    ok = (not verified) or free_mib >= required_mib
    return {
        "schema_version": 1,
        "kind": "ssh-capacity-estimate",
        "workspace_mode": config.workspace_mode,
        "repository_bytes": repo_bytes,
        "repository_mib": repo_mib,
        "source_multiplier": source_multiplier,
        "estimated_work_mib": estimated_work_mib,
        "configured_floor_mib": configured_floor_mib,
        "required_free_mib": required_mib,
        "free_mib": free_mib,
        "verified": verified,
        "ok": ok,
        "detail": (
            f"estimated SSH execution requires {required_mib} MiB free; worker reports {free_mib} MiB"
            if verified else
            f"estimated SSH execution requires {required_mib} MiB free; worker free space is unavailable"
        ),
    }


def ensure_execution_capacity(
    config: SSHRunnerConfig,
    *,
    repo_root: str | Path,
    free_storage_kib: int,
) -> dict[str, Any]:
    estimate = estimate_execution_capacity(config, repo_root=repo_root, free_storage_kib=free_storage_kib)
    if estimate["verified"] and not estimate["ok"]:
        raise ValueError(
            "SSH worker capacity preflight blocked execution before source synchronization: "
            f"{estimate['free_mib']} MiB free, {estimate['required_free_mib']} MiB estimated required "
            f"({estimate['repository_mib']} MiB repository, configured floor {estimate['configured_floor_mib']} MiB). "
            "Free worker storage or adjust the explicit resilience.min_free_mb policy after reviewing the estimate."
        )
    return estimate


def _run_size_inventory(config: SSHRunnerConfig) -> dict[str, int]:
    root = _remote_expr(config.run_state_root or "~/.cache/devtool/ssh-runs")
    script = f'''set +e
root={root}
[ -d "$root" ] || exit 0
root_real="$(cd "$root" 2>/dev/null && pwd -P)" || exit 0
for d in "$root"/*; do
  [ -d "$d" ] || continue
  [ ! -L "$d" ] || continue
  id=${{d##*/}}
  case "$id" in ''|*[!A-Za-z0-9._-]*) continue;; esac
  real="$(cd "$d" 2>/dev/null && pwd -P)" || continue
  case "$real/" in "$root_real/"*) ;; *) continue;; esac
  size="$(du -sk "$real" 2>/dev/null | awk '{{print $1; exit}}')"
  case "$size" in ''|*[!0-9]*) size=0;; esac
  printf '%s\t%s\n' "$id" "$size"
done
'''
    completed = _run_remote(config, script)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH GC inventory failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    result: dict[str, int] = {}
    for line in str(completed.stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) != 2 or not _SAFE_ID_RE.fullmatch(fields[0]):
            continue
        try:
            result[fields[0]] = max(0, int(fields[1]))
        except ValueError:
            continue
    return result


def _group_run_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        rid = str(record.get("run_id") or "")
        if rid:
            grouped[rid].append(record)
    runs: list[dict[str, Any]] = []
    for rid, phases in grouped.items():
        started = [int(item.get("started_unix") or 0) for item in phases]
        finished = [int(item.get("finished_unix") or 0) for item in phases]
        states = {str(item.get("state") or "unknown") for item in phases}
        runs.append({
            "run_id": rid,
            "phases": phases,
            "states": sorted(states),
            "active": any(bool(item.get("active")) for item in phases),
            "started_unix": min((value for value in started if value > 0), default=0),
            "finished_unix": max(finished, default=0),
        })
    runs.sort(key=lambda item: (int(item["finished_unix"] or item["started_unix"] or 0), item["run_id"]), reverse=True)
    return runs


def _gc_delete(config: SSHRunnerConfig, run_ids: list[str]) -> list[str]:
    safe_ids = [rid for rid in run_ids if _SAFE_ID_RE.fullmatch(rid) and _safe(rid) == rid]
    if not safe_ids:
        return []
    root = _remote_expr(config.run_state_root or "~/.cache/devtool/ssh-runs")
    ids = " ".join(shlex.quote(item) for item in safe_ids)
    script = f'''set -eu
root={root}
[ -d "$root" ] || exit 0
root_real="$(cd "$root" && pwd -P)"
for id in {ids}; do
  case "$id" in ''|*[!A-Za-z0-9._-]*) echo "unsafe:$id" >&2; exit 73;; esac
  target="$root/$id"
  [ -d "$target" ] || continue
  [ ! -L "$target" ] || {{ echo "symlink:$id" >&2; exit 73; }}
  real="$(cd "$target" && pwd -P)"
  case "$real/" in "$root_real/"*) ;; *) echo "escape:$id" >&2; exit 73;; esac
  rm -rf -- "$real"
  printf '%s\n' "$id"
done
'''
    completed = _run_remote(config, script)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH GC deletion failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    return [line.strip() for line in str(completed.stdout or "").splitlines() if line.strip() in safe_ids]


def gc_remote_runs(
    config: SSHRunnerConfig,
    *,
    dry_run: bool = True,
    max_age_days: int = 30,
    keep_last: int = 20,
    max_mib: int = 512,
    now_unix: int | None = None,
) -> dict[str, Any]:
    if max_age_days < 0 or keep_last < 0 or max_mib < 0:
        raise ValueError("SSH GC max_age_days, keep_last, and max_mib must be non-negative")
    now = int(now_unix if now_unix is not None else time.time())
    source = runs_payload(config)
    sizes = _run_size_inventory(config)
    grouped = _group_run_records(list(source.get("records") or []))
    keep_ids = {item["run_id"] for item in grouped[:keep_last]} if keep_last else set()

    protected: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for run in grouped:
        rid = str(run["run_id"])
        states = set(run["states"])
        finished = int(run["finished_unix"] or run["started_unix"] or 0)
        age_seconds = max(0, now - finished) if finished else 0
        size_kib = int(sizes.get(rid, 0))
        reason = ""
        if not _SAFE_ID_RE.fullmatch(rid) or _safe(rid) != rid:
            reason = "unsafe-run-id"
        elif run["active"]:
            reason = "active"
        elif states & _RECOVERY_SENSITIVE_STATES:
            reason = "recovery-sensitive-state"
        elif not states or not states <= _TERMINAL_GC_STATES:
            reason = "non-terminal-state"
        elif rid in keep_ids:
            reason = "keep-last"
        elif not finished:
            reason = "missing-finish-time"
        item = {
            "run_id": rid,
            "states": sorted(states),
            "active": bool(run["active"]),
            "finished_unix": finished,
            "age_seconds": age_seconds,
            "size_kib": size_kib,
        }
        if reason:
            item["reason"] = reason
            protected.append(item)
        else:
            eligible.append(item)

    selected: dict[str, dict[str, Any]] = {}
    age_cutoff = max_age_days * 86400
    for item in eligible:
        if max_age_days == 0 or item["age_seconds"] >= age_cutoff:
            selected[item["run_id"]] = {**item, "reason": "age-policy"}

    # Size policy: once protected/newest runs are accounted for, select oldest
    # eligible terminal runs until the remote history root fits the cap.
    total_kib = sum(int(value) for value in sizes.values())
    target_kib = max_mib * 1024
    projected_kib = total_kib - sum(int(item["size_kib"]) for item in selected.values())
    if max_mib >= 0 and projected_kib > target_kib:
        for item in sorted(eligible, key=lambda row: (int(row["finished_unix"]), row["run_id"])):
            if item["run_id"] in selected:
                continue
            selected[item["run_id"]] = {**item, "reason": "size-lru-policy"}
            projected_kib -= int(item["size_kib"])
            if projected_kib <= target_kib:
                break

    candidates = sorted(selected.values(), key=lambda row: (int(row["finished_unix"]), row["run_id"]))
    deleted: list[str] = []
    if not dry_run and candidates:
        deleted = _gc_delete(config, [item["run_id"] for item in candidates])

    return {
        "schema_version": 1,
        "kind": "ssh-run-gc",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "dry_run": bool(dry_run),
        "policy": {
            "max_age_days": max_age_days, "keep_last": keep_last, "max_mib": max_mib,
            "eviction_order": "least-recently-used-terminal",
            "lru_clock": "persisted-finished-or-started-unix",
        },
        "total_kib": total_kib,
        "projected_kib": max(0, projected_kib),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "protected": protected,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "safety": {
            "active_runs_protected": True,
            "stale_interrupted_runs_protected": True,
            "unsafe_ids_protected": True,
            "symlink_escape_blocked": True,
            "root": config.run_state_root or "~/.cache/devtool/ssh-runs",
        },
        "detail": (
            "dry-run only; no remote history was deleted" if dry_run else f"deleted {len(deleted)} Devtool-owned terminal SSH run(s)"
        ),
    }


def orphan_lease_payload(config: SSHRunnerConfig, *, repair: bool = False, now_unix: int | None = None) -> dict[str, Any]:
    workspace_root = _remote_expr(config.workspace_root or "~/.cache/devtool/workspaces")
    run_root = _remote_expr(config.run_state_root or "~/.cache/devtool/ssh-runs")
    now = int(now_unix if now_unix is not None else time.time())
    stale_after = max(30, int(config.lease_timeout_seconds or 900))
    apply = 1 if repair else 0
    script = f'''set +e
workspace_root={workspace_root}
run_root={run_root}
leases="$workspace_root/.leases"
now={now}
stale_after={stale_after}
apply={apply}
[ -d "$leases" ] || exit 0
leases_real="$(cd "$leases" 2>/dev/null && pwd -P)" || exit 0
for lease in "$leases"/*.lock; do
  [ -d "$lease" ] || continue
  [ ! -L "$lease" ] || continue
  real="$(cd "$lease" 2>/dev/null && pwd -P)" || continue
  case "$real/" in "$leases_real/"*) ;; *) continue;; esac
  owner="$(sed -n 's/^run_id=//p' "$lease/owner" 2>/dev/null | head -n1)"
  session="$(sed -n 's/^session_prefix=//p' "$lease/owner" 2>/dev/null | head -n1)"
  acquired="$(sed -n 's/^acquired_unix=//p' "$lease/owner" 2>/dev/null | head -n1)"
  case "$acquired" in ''|*[!0-9]*) acquired=0;; esac
  age=$((now-acquired)); [ "$age" -ge 0 ] || age=0
  active=0
  if [ -n "$session" ] && command -v tmux >/dev/null 2>&1; then
    for s in $(tmux list-sessions -F '#{{session_name}}' 2>/dev/null || true); do
      case "$s" in "$session"|"$session"-*) active=1;; esac
    done
  fi
  run_exists=0
  case "$owner" in ''|*[!A-Za-z0-9._-]*) ;; *) [ -d "$run_root/$owner" ] && run_exists=1;; esac
  class=owned
  if [ -z "$owner" ]; then class=corrupt-owner
  elif [ "$run_exists" -eq 0 ] && [ "$active" -eq 0 ] && [ "$age" -ge "$stale_after" ]; then class=orphan
  elif [ "$run_exists" -eq 0 ] && [ "$active" -eq 0 ]; then class=young-orphan
  elif [ "$active" -eq 1 ]; then class=active
  fi
  repaired=0
  if [ "$class" = orphan ] && [ "$apply" -eq 1 ]; then
    rm -rf -- "$real" && repaired=1
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "${{lease##*/}}" "$owner" "$session" "$age" "$active" "$class" "$repaired"
done
'''
    completed = _run_remote(config, script)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH orphan lease audit failed with exit {completed.returncode}: {detail or 'no diagnostic output'}")
    records: list[dict[str, Any]] = []
    for line in str(completed.stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) != 7:
            continue
        name, owner, session, age, active, classification, repaired = fields
        try:
            age_value = max(0, int(age))
        except ValueError:
            age_value = 0
        records.append({
            "lease": name,
            "run_id": owner,
            "session_prefix": session,
            "age_seconds": age_value,
            "active": active == "1",
            "classification": classification,
            "repaired": repaired == "1",
        })
    return {
        "schema_version": 1,
        "kind": "ssh-orphan-lease-audit",
        "ok": True,
        "profile": config.profile,
        "destination": config.destination,
        "repair": repair,
        "records": records,
        "orphan_count": sum(1 for item in records if item["classification"] == "orphan"),
        "repaired_count": sum(1 for item in records if item["repaired"]),
        "protected_count": sum(1 for item in records if item["classification"] != "orphan"),
        "lease_timeout_seconds": stale_after,
    }
