"""Immutable Android release evidence sessions (AOPS04)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
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
    verdict: str = "running"

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).expanduser().resolve()
        self.run_id = self.run_id or os.environ.get("DEVTOOL_RUN_ID", "")
        self.session_id = self.session_id or f"android-release-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
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
            "verdict": self.verdict,
        }
        payload["evidence_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
        return payload

    def seal(self, output: Path | None = None, *, register: bool = True) -> Path:
        self.finished_at = self.finished_at or _now()
        self.verdict = "passed" if self.passed else "failed"
        output = output or (self.repo_root / ".devtool" / "reports" / "android-release" / f"{self.session_id}.final.json")
        output = Path(output).expanduser().resolve(strict=False)
        if output.exists():
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict()
        fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=str(output.parent))
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, output)
        finally:
            temp.unlink(missing_ok=True)
        if register:
            try:
                from ..artifact_registry import register_paths
                register_paths(self.repo_root, [output], metadata={
                    "producer": "android-release",
                    "command": "release",
                    "target": self.target,
                    "variant": self.variant,
                    "state": self.verdict,
                    "type": "release-evidence",
                    "run_id": self.run_id,
                    "release_session_id": self.session_id,
                    "release_verdict": self.verdict,
                })
            except Exception:
                pass
        return output


__all__ = ["RELEASE_EVIDENCE_SCHEMA_VERSION", "ReleaseGateEvidence", "AndroidReleaseEvidenceSession"]
