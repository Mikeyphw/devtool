"""Immutable Android release evidence sessions (AOPS04)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping
import uuid

from .. import __version__

RELEASE_EVIDENCE_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(payload: Mapping[str, Any]) -> bytes:
    clean = dict(payload)
    clean.pop("evidence_sha256", None)
    return json.dumps(clean, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _git_head(repo_root: Path) -> str:
    import subprocess
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_dirty(repo_root: Path) -> bool | None:
    import subprocess
    try:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, text=True, capture_output=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(proc.stdout.strip()) if proc.returncode == 0 else None


@dataclass(slots=True)
class ReleaseGateEvidence:
    name: str
    status: str
    required: bool = True
    duration_seconds: float = 0.0
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AndroidReleaseEvidenceSession:
    repo_root: Path
    target: str
    variant: str
    run_id: str = ""
    session_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    source: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)
    signing: dict[str, Any] = field(default_factory=dict)
    device: dict[str, Any] = field(default_factory=dict)
    gates: list[ReleaseGateEvidence] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)
    verdict: str = "running"

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).expanduser().resolve()
        self.session_id = self.session_id or f"android-release-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        self.run_id = self.run_id or os.environ.get("DEVTOOL_RUN_ID", "") or self.session_id
        self.started_at = self.started_at or _now()
        if not self.source:
            self.source = {
                "git_head": _git_head(self.repo_root),
                "git_dirty": _git_dirty(self.repo_root),
                "devtool_version": __version__,
            }

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.status == "passed" or (g.status == "skipped" and not g.required) for g in self.gates)

    def add_gate(self, name: str, status: str, *, required: bool = True, duration_seconds: float = 0.0, detail: str = "", metadata: Mapping[str, Any] | None = None) -> None:
        self.gates.append(ReleaseGateEvidence(name, status, required, duration_seconds, detail, dict(metadata or {})))

    def add_artifacts(self, payload: Mapping[str, Any] | None) -> None:
        if not payload:
            return
        for raw in payload.get("items", []) or []:
            if isinstance(raw, dict):
                self.artifacts.append(dict(raw))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": RELEASE_EVIDENCE_SCHEMA_VERSION,
            "report_kind": "devtool-android-release-evidence",
            "session_id": self.session_id,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "repository": str(self.repo_root),
            "target": self.target,
            "variant": self.variant,
            "source": dict(self.source),
            "execution": dict(self.execution),
            "signing": dict(self.signing),
            "device": dict(self.device),
            "gates": [g.as_dict() for g in self.gates],
            "artifacts": list(self.artifacts),
            "hooks": list(self.hooks),
            "storage": dict(self.storage),
            "verdict": self.verdict,
        }
        payload["evidence_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        return payload

    def seal(self, output: Path | None = None, *, register: bool = True) -> Path:
        self.finished_at = self.finished_at or _now()
        self.verdict = "passed" if self.passed else "failed"
        from ..core_runtime import private_run_root
        canonical = (private_run_root(self.repo_root) / self.run_id / "android-release" / f"{self.session_id}.final.json").resolve(strict=False)
        export = Path(output).expanduser().resolve(strict=False) if output is not None else None
        explicit_export = export is not None and export != canonical

        # Canonical RUN evidence is always private. An explicit output is a
        # projection/export of those exact bytes, never a replacement authority.
        destinations = [canonical] + ([export] if explicit_export and export is not None else [])
        for destination in destinations:
            if destination.exists():
                raise FileExistsError(f"Android release evidence output already exists: {destination}")
        self.storage = {
            "authority": "run-v3",
            "privacy": "private",
            "canonical_path": str(canonical),
            "explicit_export": explicit_export,
            "export_path": str(export) if explicit_export and export is not None else "",
        }
        payload = self.to_dict()
        encoded = (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

        def write_once(destination: Path) -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent))
            temp = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)

        write_once(canonical)
        if explicit_export and export is not None:
            try:
                write_once(export)
            except Exception:
                canonical.unlink(missing_ok=True)
                raise

        if register:
            from ..run_store import AttachmentDescriptor, RunCatalog
            catalog = RunCatalog.open(private_run_root(self.repo_root))
            existing_run = dict(catalog.runs.get(self.run_id, {}))
            if existing_run:
                existing_run.update({
                    "release_session_id": self.session_id,
                    "release_verdict": self.verdict,
                    "android_release_variant": self.variant,
                })
                catalog.add_run(existing_run)
            else:
                catalog.add_run({
                    "run_id": self.run_id,
                    "command_id": "android.release",
                    "target": self.target,
                    "variant": self.variant,
                    "status": self.verdict,
                    "release_session_id": self.session_id,
                    "created_at": time.time(),
                })
            attachment = AttachmentDescriptor.from_file(
                run_id=self.run_id, path=canonical, kind="android-release-evidence",
                privacy="private", media_type="application/json",
            )
            catalog.add_attachment(attachment)
            catalog.save()
            try:
                from ..artifact_registry import register_paths
                registered = [canonical] + ([export] if explicit_export and export is not None else [])
                register_paths(self.repo_root, registered, metadata={
                    "producer": "android-release",
                    "command": "release",
                    "target": self.target,
                    "variant": self.variant,
                    "state": self.verdict,
                    "type": "release-evidence",
                    "run_id": self.run_id,
                    "run_attachment_id": attachment.attachment_id,
                    "release_session_id": self.session_id,
                    "release_verdict": self.verdict,
                    "explicit_export": explicit_export,
                    "canonical_path": str(canonical),
                })
            except Exception:
                # RUN V3 is canonical. The artifact registry is a compatibility
                # projection and cannot invalidate successfully persisted evidence.
                pass
        return export if explicit_export and export is not None else canonical


__all__ = ["RELEASE_EVIDENCE_SCHEMA_VERSION", "ReleaseGateEvidence", "AndroidReleaseEvidenceSession"]
