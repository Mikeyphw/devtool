from __future__ import annotations

"""R07 consolidated SSH reliability projections and bounded qualification receipts.

This file never runs SSH, recovers, sends notifications, kills tmux or deletes evidence.
A checksum over local return bytes is not host attestation.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from typing import Any, Mapping

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")


def worker_substrate(platform: str, home: str, prefix: str, *, proot_available: bool, proot_ready: bool) -> dict[str, Any]:
    """Classify the observed login shell, not an unverified guest environment."""
    termux_prefix = prefix.startswith(("/data/data/com.termux/files/usr", "/data/user/0/com.termux/files/usr"))
    termux_home = home.startswith(("/data/data/com.termux/files/home", "/data/user/0/com.termux/files/home"))
    if platform == "termux" and termux_prefix and termux_home:
        kind, confidence = "termux-native", "path-and-platform"
    elif platform == "termux":
        kind, confidence = "termux-reported-unverified", "platform-only"
    elif platform == "linux":
        kind, confidence = "linux-login-shell", "platform-only"
    else:
        kind, confidence = "unknown", "insufficient"
    return {
        "login_shell": kind, "confidence": confidence,
        "proot_available": bool(proot_available), "proot_ready": bool(proot_ready),
        "guest_execution_attested": False,
        "supervisor_policy": "inspect-only; stopping a guest requires independent session inventory and explicit confirmation",
        "warning": "PRoot availability does not prove that this SSH session runs inside a guest",
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def return_recovery_inventory(repo_root: str | Path, *, profile: str = "", destination: str = "", limit: int = 200) -> dict[str, Any]:
    """Inspect only owned manifest/status bytes, with no symlink following or repair."""
    from .ssh_returns import _read_manifest
    repo = Path(repo_root).expanduser().resolve(strict=True)
    boundary = repo / ".devtool" / "ssh-return"
    records: list[dict[str, Any]] = []
    if not (1 <= limit <= 1000):
        raise ValueError("return inventory limit must be 1..1000")
    if (repo / ".devtool").is_symlink():
        return {"kind": "ssh-return-recovery-inventory", "ok": False, "reason": "symlinked-state-root", "records": [], "truncated": False}
    def add(record: dict[str, Any]) -> bool:
        records.append(record)
        return len(records) >= limit
    def output(truncated: bool) -> dict[str, Any]:
        return {"kind": "ssh-return-recovery-inventory", "ok": True, "count": len(records), "truncated": truncated, "records": records}
    if boundary.is_symlink():
        return {"kind": "ssh-return-recovery-inventory", "ok": False, "reason": "symlinked-return-root", "records": [], "truncated": False}
    if not boundary.is_dir():
        return {"kind": "ssh-return-recovery-inventory", "ok": True, "records": [], "truncated": False, "count": 0}
    for run_dir in sorted(boundary.iterdir(), key=lambda v: v.name):
        if run_dir.is_symlink() or not run_dir.is_dir() or not _SAFE.fullmatch(run_dir.name):
            if add({"run_id": run_dir.name, "state": "unsafe", "recoverable": False, "reason": "unsafe-run-directory"}):
                return output(True)
            continue
        for session_dir in sorted(run_dir.iterdir(), key=lambda v: v.name):
            if session_dir.is_symlink() or not session_dir.is_dir() or not _SAFE.fullmatch(session_dir.name):
                if add({"run_id": run_dir.name, "session": session_dir.name, "state": "unsafe", "recoverable": False, "reason": "unsafe-session-directory"}):
                    return output(True)
                continue
            path, status = session_dir / "manifest.json", session_dir / "transfer.status"
            if not path.exists() and not path.is_symlink():
                if status.exists() or status.is_symlink() or (session_dir / "repo").exists():
                    if add({"run_id": run_dir.name, "session": session_dir.name, "state": "orphaned", "recoverable": False, "reason": "manifest-missing"}):
                        return output(True)
                continue
            record: dict[str, Any] = {"run_id": run_dir.name, "session": session_dir.name, "state": "unknown", "recoverable": False}
            try:
                if path.is_symlink() or not path.is_file() or status.is_symlink():
                    raise ValueError("unsafe-symlink-or-manifest")
                manifest = _read_manifest(path)
                if str(manifest.get("run_id") or "") != run_dir.name or str(manifest.get("session") or "") != session_dir.name:
                    raise ValueError("manifest-path-identity-mismatch")
                producer = manifest.get("producer")
                if not isinstance(producer, Mapping) or any(
                    str(producer.get(key) or "") != str(manifest.get(key) or "")
                    for key in ("profile", "destination", "run_id", "session", "source_sync_id")
                ):
                    raise ValueError("manifest-producer-identity-mismatch")
                if profile and manifest.get("profile") != profile:
                    continue
                if destination and manifest.get("destination") != destination:
                    continue
                record["source_sync_id"] = str(manifest.get("source_sync_id") or "")
                record["manifest_sha256"] = _digest(path)
                if not status.is_file():
                    record.update(state="pending", recoverable=True, reason="missing-transfer-status")
                else:
                    parts: dict[str, str] = {}
                    for line in status.read_text(encoding="utf-8").splitlines():
                        if "=" not in line:
                            raise ValueError("invalid-transfer-status")
                        k, v = line.split("=", 1)
                        if k in parts or k not in {"state", "returncode", "mode", "source_bytes", "source_files", "transferred_bytes"}:
                            raise ValueError("ambiguous-transfer-status")
                        parts[k] = v
                    state, code = parts.get("state", ""), parts.get("returncode", "")
                    if state not in {"pending", "partial", "complete", "failed", "deferred"}:
                        raise ValueError("invalid-transfer-state")
                    if state == "complete" and code != "0":
                        raise ValueError("complete-without-zero-returncode")
                    record.update(state=state, recoverable=state != "complete", returncode=code, status_sha256=_digest(status))
            except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
                record.update(state="invalid", recoverable=False, reason=type(exc).__name__)
            if add(record):
                return output(True)
    return output(False)


def _source_identity(repo: Path) -> dict[str, str]:
    """Use git's local object IDs only; never leak argv, remote URLs or environment.

    A non-Git fixture/checkout is explicitly unbound. A local Git object ID is
    useful provenance, not cryptographic authentication of a remote worker.
    """
    if not (repo / ".git").exists():
        return {"state": "unavailable"}
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD", "HEAD^{tree}"],
            cwd=repo, text=True, capture_output=True, timeout=8, check=False,
        )
        ids = result.stdout.strip().splitlines()
        if result.returncode != 0 or len(ids) != 2 or any(not re.fullmatch(r"[a-f0-9]{40,64}", v) for v in ids):
            return {"state": "unavailable"}
        return {"state": "local-git", "commit": ids[0], "tree": ids[1], "worktree_verified": False}
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unavailable"}


@dataclass
class QualificationReceipt:
    """Atomic, append-preserving stage snapshot; never stores argv/secrets or credentials."""
    path: Path
    payload: dict[str, Any]

    @classmethod
    def start(cls, repo: Path, profile: str, destination: str) -> "QualificationReceipt":
        if not _SAFE.fullmatch(profile):
            raise ValueError("qualification receipt requires a safe profile name")
        base = repo / ".devtool" / "ssh-qualification" / profile
        if any(x.is_symlink() for x in (repo / ".devtool", repo / ".devtool" / "ssh-qualification", base)):
            raise ValueError("qualification receipt directory cannot be symlinked")
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        # An existing user-owned directory may have older, permissive modes.
        # Refuse to place private evidence there rather than chmod it implicitly.
        if base.stat().st_mode & 0o077:
            raise ValueError("qualification receipt directory is not private")
        nonce = uuid.uuid4().hex
        source = _source_identity(repo)
        receipt = cls(base / (nonce + ".json"), {
            "schema_version": 1, "kind": "ssh-r07-qualification-stages", "profile": profile,
            "destination_sha256": hashlib.sha256(destination.encode()).hexdigest(),
            "state": "running", "stage": "worker-probe", "stages": [], "qualification": "pending",
            "started_unix": int(time.time()),
            "source_identity": source,
        })
        receipt._persist()
        return receipt

    def checkpoint(self, stage: str, state: str) -> None:
        if stage not in {"worker-probe", "bootstrap", "source-sync", "project-validation", "tmux-probe", "return-transfer", "incremental-sync", "completed"}:
            raise ValueError("unexpected SSH qualification stage")
        if state not in {"started", "passed", "failed", "skipped"}:
            raise ValueError("unexpected SSH qualification stage state")
        self.payload["stage"] = stage
        self.payload["stages"].append({"stage": stage, "state": state, "observed_unix": int(time.time())})
        self._persist()

    def finish(self, *, passed: bool, failure_type: str = "") -> None:
        if not passed:
            self.payload["failure_stage"] = self.payload["stage"]
            self.payload["stages"].append({"stage": self.payload["stage"], "state": "failed", "observed_unix": int(time.time())})
        self.payload["state"] = "complete" if passed else "failed"
        self.payload["qualification"] = "local-worker-passed" if passed else "not-qualified"
        self.payload["failed_exception_type"] = failure_type[:64] if not passed else ""
        self.payload["finished_unix"] = int(time.time())
        self._persist()

    def _persist(self) -> None:
        target = self.path
        if target.is_symlink():
            raise ValueError("qualification receipt target cannot be symlinked")
        tmp = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with tmp.open("x", encoding="utf-8") as f:
                os.chmod(tmp, 0o600)
                json.dump(self.payload, f, ensure_ascii=False, sort_keys=True, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        finally:
            if tmp.exists():
                tmp.unlink()


def performance_projection(performance: Mapping[str, Any], *, baseline: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Observed local stage timings, not synthetic FS03 or remote quality scores."""
    keys = ("sync_seconds", "tmux_seconds", "return_seconds", "second_plan_seconds", "project_validation_first_seconds", "project_validation_second_seconds")
    values = {k: float(performance.get(k, 0) or 0) for k in keys}
    if any(not (0 <= v < float("inf")) for v in values.values()):
        raise ValueError("invalid performance observation")
    first, second = values["project_validation_first_seconds"], values["project_validation_second_seconds"]
    comparative = first > 0 and second > 0
    return {
        "kind": "ssh-r07-performance", "measurements": values,
        "cold_warm_observed": comparative,
        "warm_to_cold_ratio": round(second / first, 3) if comparative else None,
        "baseline_available": baseline is not None,
        "measured_positive_stages": [k for k, v in values.items() if k in performance and v > 0],
        "unmeasured_or_skipped_stages": [k for k, v in values.items() if k not in performance or v <= 0],
        "fs03_qualified": False,
        "notification_qualified": False,
        "reason": "Measured stages are local execution evidence; external FS03, real-worker and notification gates require separate receipts",
    }
