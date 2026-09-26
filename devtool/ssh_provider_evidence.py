from __future__ import annotations

"""Stable SSH worker/provider evidence consumed by federation scheduling.

SSH-01/03 remain the authority for remote environment/toolchain qualification.
This module only persists a compact, redacted scheduling projection of evidence
already produced by ``ssh env``, ``ssh prepare`` or ``ssh doctor``.  Federation
consumers read these records and never reprobe the worker independently.
"""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Mapping

from .xdg import state_dir

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(value: str, fallback: str) -> str:
    text = _SAFE_RE.sub("-", str(value or "").strip()).strip("-.")
    return text or fallback


def evidence_root() -> Path:
    return state_dir() / "ssh" / "provider-evidence"


def evidence_path(profile: str, target: str = "") -> Path:
    name = _safe(profile, "worker")
    target_name = _safe(target, "_worker") if target else "_worker"
    return evidence_root() / name / f"{target_name}.json"


def _digest(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256(raw).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _tool_names(payload: Mapping[str, Any]) -> tuple[str, ...]:
    tools: list[str] = []
    raw_tools = payload.get("tools")
    if isinstance(raw_tools, Mapping):
        for name, value in raw_tools.items():
            if isinstance(value, Mapping):
                if str(value.get("path") or ""):
                    tools.append(str(name))
            elif str(value or ""):
                tools.append(str(name))
    probe = payload.get("probe")
    if isinstance(probe, Mapping):
        ptools = probe.get("tools")
        if isinstance(ptools, Mapping):
            tools.extend(str(name) for name, value in ptools.items() if str(value or ""))
    environment = payload.get("environment")
    if isinstance(environment, Mapping):
        etools = environment.get("tools")
        if isinstance(etools, Mapping):
            for name, value in etools.items():
                if isinstance(value, Mapping) and str(value.get("path") or ""):
                    tools.append(str(name))
    return tuple(dict.fromkeys(sorted(tools)))


def _projection(payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    profile = str(payload.get("profile") or "").strip()
    target = str(payload.get("target") or "").strip()
    execution_provider = str(payload.get("execution_provider") or "").strip()
    execution = payload.get("execution")
    if isinstance(execution, Mapping):
        provider = execution.get("provider")
        if isinstance(provider, Mapping):
            execution_provider = str(provider.get("name") or provider.get("provider") or execution_provider).strip()
        elif str(provider or "").strip():
            execution_provider = str(provider).strip()
    environment = payload.get("environment")
    if isinstance(environment, Mapping):
        execution_provider = str(environment.get("execution_provider") or execution_provider).strip()
    if not execution_provider:
        execution_provider = "ssh-native"

    worker: Mapping[str, Any] = {}
    for key in ("worker", "probe"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            worker = value
            break
    if isinstance(environment, Mapping) and isinstance(environment.get("worker"), Mapping):
        worker = environment.get("worker")  # type: ignore[assignment]
    platform = str(worker.get("platform") or "").strip()
    architecture = str(worker.get("architecture") or "").strip()

    capacity = payload.get("capacity") if isinstance(payload.get("capacity"), Mapping) else {}
    free_mib = int(capacity.get("free_mib") or 0) if isinstance(capacity, Mapping) else 0
    ok = bool(payload.get("healthy", payload.get("ok", True)))
    if isinstance(payload.get("target_toolchain"), Mapping):
        ok = ok and bool(payload["target_toolchain"].get("ok", True))
    classification = str(payload.get("target_toolchain_classification") or "").strip()
    if classification in {"blocked", "incompatible"}:
        ok = False

    environments = ["ssh"]
    if "proot" in execution_provider or "oci" in execution_provider:
        environments.append("proot-distro")

    resources: dict[str, int] = {"ssh-worker": 1}
    if "proot" in execution_provider or "oci" in execution_provider:
        resources["oci-slot"] = 1
    if platform == "termux":
        resources["termux-worker"] = 1

    core = {
        "schema_version": 1,
        "kind": "ssh-worker-provider-evidence",
        "profile": profile,
        "target": target,
        "source": source,
        "ok": ok,
        "execution_provider": execution_provider,
        "execution_environments": environments,
        "worker": {"platform": platform, "architecture": architecture},
        "tools": list(_tool_names(payload)),
        "resources": resources,
        "capacity": {"free_mib": free_mib},
    }
    core["evidence_digest"] = _digest(core)
    core["recorded_unix"] = int(time.time())
    return core


def persist_provider_evidence(payload: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    record = _projection(payload, source=source)
    profile = str(record.get("profile") or "").strip()
    if not profile:
        return record
    _atomic_json(evidence_path(profile, str(record.get("target") or "")), record)
    return record


def load_provider_evidence(profile: str, *, target: str = "") -> dict[str, Any] | None:
    candidates = [evidence_path(profile, target)] if target else []
    candidates.append(evidence_path(profile, ""))
    profile_dir = evidence_root() / _safe(profile, "worker")
    if not target and profile_dir.is_dir():
        candidates.extend(sorted(profile_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == "ssh-worker-provider-evidence":
            return payload
    return None


def load_all_provider_evidence(*, target: str = "") -> tuple[dict[str, Any], ...]:
    root = evidence_root()
    if not root.is_dir():
        return ()
    records: list[dict[str, Any]] = []
    for profile_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        record = load_provider_evidence(profile_dir.name, target=target)
        if record is not None:
            records.append(record)
    return tuple(records)


__all__ = [
    "evidence_path",
    "evidence_root",
    "load_all_provider_evidence",
    "load_provider_evidence",
    "persist_provider_evidence",
]
