"""ENV-X04 environment lifecycle and native-runner adoption contracts.

This module deliberately models environment/provider truth as immutable contribution
records that can be consumed by EXEC, Android, federation and presentation layers
without those layers taking ownership of provider semantics.  It is intentionally
side-effect free: callers pass observed destination facts and receive typed
snapshots, lifecycle plans, qualification decisions and explanation payloads.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Mapping, Sequence


_SECRET_RE = re.compile(r"(token|password|secret|credential|key)", re.IGNORECASE)


class ProviderKind(str, Enum):
    PYTHON = "python"
    GO = "go"
    ASDF = "asdf"
    PYENV = "pyenv"
    GRADLE = "gradle"


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    BLOCKED = "blocked"


class GradleFeature(str, Enum):
    ISOLATED_PROJECTS = "isolated-projects"
    PARALLEL_CONFIGURATION_CACHE = "parallel-configuration-cache"
    REMOTE_BUILD_CACHE = "remote-build-cache"


@dataclass(frozen=True)
class DestinationSnapshot:
    provider: ProviderKind
    destination: str
    executable: str | None
    version: str | None
    root: str | None = None
    data_dir: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    source: str = "observed"
    legacy_version_file: str | None = None
    requested_version: str | None = None
    observed_version: str | None = None
    cache_roots: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def safe_env(self) -> dict[str, str]:
        return {key: ("<redacted>" if _SECRET_RE.search(key) else value) for key, value in sorted(self.env.items())}

    def identity_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "destination": self.destination,
            "executable": self.executable,
            "version": self.version,
            "root": self.root,
            "data_dir": self.data_dir,
            "source": self.source,
            "legacy_version_file": self.legacy_version_file,
            "requested_version": self.requested_version,
            "observed_version": self.observed_version,
            "cache_roots": list(self.cache_roots),
            "env": self.safe_env(),
        }

    def identity(self) -> str:
        data = json.dumps(self.identity_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LifecyclePlan:
    provider: ProviderKind
    source_roots: tuple[str, ...]
    lock_files: tuple[str, ...]
    cache_roots: tuple[str, ...]
    owned_environment: Mapping[str, str] = field(default_factory=dict)
    recursive: bool = True
    qualification_actions: tuple[str, ...] = ()

    def explain(self) -> dict[str, object]:
        return {
            "provider": self.provider.value,
            "source_roots": list(self.source_roots),
            "lock_files": list(self.lock_files),
            "cache_roots": list(self.cache_roots),
            "owned_environment": dict(sorted(self.owned_environment.items())),
            "recursive": self.recursive,
            "qualification_actions": list(self.qualification_actions),
        }


@dataclass(frozen=True)
class QualificationResult:
    provider: ProviderKind
    status: QualificationStatus
    snapshot_identity: str | None
    reasons: tuple[str, ...]
    evidence: Mapping[str, object] = field(default_factory=dict)

    @property
    def qualified(self) -> bool:
        return self.status is QualificationStatus.QUALIFIED


@dataclass(frozen=True)
class GoTarget:
    goos: str
    goarch: str
    cgo_enabled: bool = False
    cc: str | None = None

    def canonical(self) -> str:
        cgo = "cgo" if self.cgo_enabled else "nocgo"
        cc = self.cc or "no-cc"
        return f"{self.goos}/{self.goarch}/{cgo}/{cc}"


@dataclass(frozen=True)
class GradleFeatureRequest:
    feature: GradleFeature
    requested: bool
    evidence: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class GradleFeatureDecision:
    feature: GradleFeature
    enabled: bool
    status: QualificationStatus
    reason: str
    evidence_identity: str | None = None


@dataclass(frozen=True)
class GradleAdaptiveHistory:
    samples: tuple[Mapping[str, object], ...]

    def bounded(self, limit: int = 20) -> "GradleAdaptiveHistory":
        return GradleAdaptiveHistory(tuple(self.samples[-limit:]))

    def summarize(self) -> dict[str, object]:
        bounded = self.bounded()
        if not bounded.samples:
            return {"samples": 0, "median_seconds": None, "last_profile": None}
        durations = sorted(float(sample.get("duration_seconds", 0.0)) for sample in bounded.samples)
        median = durations[len(durations) // 2]
        return {
            "samples": len(bounded.samples),
            "median_seconds": median,
            "last_profile": bounded.samples[-1].get("profile"),
            "bounded": len(self.samples) != len(bounded.samples),
        }


def _normalise_path(path: str | None, *, home: str | None = None) -> str | None:
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    if text.startswith("~") and home:
        text = home.rstrip("/") + text[1:]
    normalised = posixpath.normpath(text)
    if not normalised.startswith("/"):
        raise ValueError(f"destination path must be absolute: {path}")
    return normalised


def _read_project_file(files: Mapping[str, str], names: Sequence[str]) -> tuple[str | None, str | None]:
    for name in names:
        if name in files:
            value = files[name].strip().splitlines()[0].strip() if files[name].strip() else ""
            return name, value
    return None, None


def _parse_tool_versions(text: str, tool: str) -> str | None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0] == tool:
            return parts[1]
    return None


def python_lifecycle(project_files: Mapping[str, str], *, project_root: str = "/repo", cache_root: str = "/cache/python") -> LifecyclePlan:
    lock_candidates = [
        "pyproject.toml",
        "poetry.lock",
        "uv.lock",
        "requirements.txt",
        "requirements-dev.txt",
        "Pipfile.lock",
    ]
    locks = tuple(posixpath.join(project_root, name) for name in lock_candidates if name in project_files)
    return LifecyclePlan(
        provider=ProviderKind.PYTHON,
        source_roots=(project_root,),
        lock_files=locks,
        cache_roots=(_normalise_path(cache_root) or cache_root,),
        owned_environment={"PYTHONPATH": "project", "VIRTUAL_ENV": "destination"},
        recursive=True,
        qualification_actions=("hash-sources", "hash-locks", "verify-destination-snapshot"),
    )


def destination_python_snapshot(
    *,
    destination: str,
    executable: str,
    version: str,
    env: Mapping[str, str] | None = None,
    cache_roots: Sequence[str] = (),
) -> DestinationSnapshot:
    safe_env = dict(env or {})
    return DestinationSnapshot(
        provider=ProviderKind.PYTHON,
        destination=destination,
        executable=_normalise_path(executable),
        version=version,
        root=_normalise_path(safe_env.get("VIRTUAL_ENV")) if safe_env.get("VIRTUAL_ENV") else None,
        data_dir=None,
        env=safe_env,
        source="destination-returned",
        observed_version=version,
        cache_roots=tuple(_normalise_path(path) or path for path in cache_roots),
    )


def qualify_python_snapshot(snapshot: DestinationSnapshot, plan: LifecyclePlan) -> QualificationResult:
    reasons: list[str] = []
    if snapshot.provider is not ProviderKind.PYTHON:
        reasons.append("snapshot provider is not python")
    if not snapshot.executable or not snapshot.version:
        reasons.append("destination did not return executable and version")
    if any(not path.startswith("/") for path in plan.source_roots + plan.lock_files + plan.cache_roots):
        reasons.append("lifecycle plan contains relative paths")
    status = QualificationStatus.QUALIFIED if not reasons else QualificationStatus.UNQUALIFIED
    return QualificationResult(
        provider=ProviderKind.PYTHON,
        status=status,
        snapshot_identity=snapshot.identity() if not reasons else None,
        reasons=tuple(reasons or ["destination snapshot and lifecycle plan are coherent"]),
        evidence={"plan": plan.explain(), "snapshot": snapshot.identity_payload()},
    )


def go_lifecycle(
    project_files: Mapping[str, str],
    *,
    project_root: str = "/repo",
    gocache: str = "/cache/go-build",
    gomodcache: str = "/cache/gomod",
) -> LifecyclePlan:
    locks = []
    for name in ("go.mod", "go.sum", "go.work", "go.work.sum"):
        if name in project_files:
            locks.append(posixpath.join(project_root, name))
    return LifecyclePlan(
        provider=ProviderKind.GO,
        source_roots=(project_root,),
        lock_files=tuple(locks),
        cache_roots=(_normalise_path(gocache) or gocache, _normalise_path(gomodcache) or gomodcache),
        owned_environment={"GOCACHE": gocache, "GOMODCACHE": gomodcache},
        recursive=True,
        qualification_actions=("verify-go-env", "verify-module-security", "verify-cross-target"),
    )


def destination_go_snapshot(
    *,
    destination: str,
    executable: str,
    version: str,
    target: GoTarget,
    env: Mapping[str, str] | None = None,
) -> DestinationSnapshot:
    env_map = dict(env or {})
    env_map.update({"GOOS": target.goos, "GOARCH": target.goarch, "CGO_ENABLED": "1" if target.cgo_enabled else "0"})
    if target.cc:
        env_map["CC"] = target.cc
    return DestinationSnapshot(
        provider=ProviderKind.GO,
        destination=destination,
        executable=_normalise_path(executable),
        version=version,
        root=_normalise_path(env_map.get("GOROOT")) if env_map.get("GOROOT") else None,
        data_dir=_normalise_path(env_map.get("GOMODCACHE")) if env_map.get("GOMODCACHE") else None,
        env=env_map,
        source="destination-returned",
        observed_version=version,
        cache_roots=tuple(_normalise_path(env_map[key]) for key in ("GOCACHE", "GOMODCACHE") if env_map.get(key)),
    )


def qualify_go_target(snapshot: DestinationSnapshot, target: GoTarget, *, module_checksum: str | None, allow_insecure_modules: bool = False) -> QualificationResult:
    reasons: list[str] = []
    if snapshot.provider is not ProviderKind.GO:
        reasons.append("snapshot provider is not go")
    if target.cgo_enabled and not target.cc:
        reasons.append("CGO target requires an explicit C compiler")
    if not module_checksum and not allow_insecure_modules:
        reasons.append("module checksum evidence is required")
    status = QualificationStatus.QUALIFIED if not reasons else QualificationStatus.BLOCKED
    return QualificationResult(
        provider=ProviderKind.GO,
        status=status,
        snapshot_identity=snapshot.identity() if status is QualificationStatus.QUALIFIED else None,
        reasons=tuple(reasons or [f"qualified {target.canonical()}"]),
        evidence={"target": target.canonical(), "module_checksum": module_checksum, "snapshot": snapshot.identity_payload()},
    )


def asdf_snapshot(
    *,
    destination: str,
    tool: str,
    current_version: str,
    executable: str,
    env: Mapping[str, str] | None = None,
    project_files: Mapping[str, str] | None = None,
    home: str = "/home/user",
) -> DestinationSnapshot:
    env_map = dict(env or {})
    files = dict(project_files or {})
    data_dir = _normalise_path(env_map.get("ASDF_DATA_DIR") or posixpath.join(home, ".asdf"), home=home)
    source = "asdf current"
    requested = env_map.get(f"ASDF_{tool.upper()}_VERSION")
    legacy_file = None
    if not requested and ".tool-versions" in files:
        requested = _parse_tool_versions(files[".tool-versions"], tool)
        legacy_file = ".tool-versions" if requested else None
    if not requested:
        legacy_names = [f".{tool}-version"]
        if tool == "python":
            legacy_names.append(".python-version")
        legacy_file, requested = _read_project_file(files, legacy_names)
    warnings: list[str] = []
    if requested and requested != current_version:
        warnings.append("requested version differs from observed current version")
    return DestinationSnapshot(
        provider=ProviderKind.ASDF,
        destination=destination,
        executable=_normalise_path(executable),
        version=current_version,
        root=data_dir,
        data_dir=data_dir,
        env=env_map,
        source=source,
        legacy_version_file=legacy_file,
        requested_version=requested,
        observed_version=current_version,
        cache_roots=(data_dir,) if data_dir else (),
        warnings=tuple(warnings),
    )


def pyenv_snapshot(
    *,
    destination: str,
    executable: str,
    current_version: str,
    env: Mapping[str, str] | None = None,
    project_files: Mapping[str, str] | None = None,
    home: str = "/home/user",
) -> DestinationSnapshot:
    env_map = dict(env or {})
    files = dict(project_files or {})
    pyenv_root = _normalise_path(env_map.get("PYENV_ROOT") or posixpath.join(home, ".pyenv"), home=home)
    legacy_file, requested = _read_project_file(files, [".python-version"])
    requested = env_map.get("PYENV_VERSION") or requested
    warnings: list[str] = []
    if requested and requested != current_version:
        warnings.append("requested version differs from observed current version")
    return DestinationSnapshot(
        provider=ProviderKind.PYENV,
        destination=destination,
        executable=_normalise_path(executable),
        version=current_version,
        root=pyenv_root,
        data_dir=pyenv_root,
        env=env_map,
        source="pyenv version-name",
        legacy_version_file=legacy_file,
        requested_version=requested,
        observed_version=current_version,
        cache_roots=(pyenv_root,) if pyenv_root else (),
        warnings=tuple(warnings),
    )


def qualify_version_manager_snapshot(snapshot: DestinationSnapshot) -> QualificationResult:
    reasons: list[str] = []
    if snapshot.requested_version and snapshot.observed_version and snapshot.requested_version != snapshot.observed_version:
        reasons.append("requested version does not match destination-observed version")
    if not snapshot.data_dir or not snapshot.data_dir.startswith("/"):
        reasons.append("provider data/root directory must be absolute and destination-owned")
    status = QualificationStatus.QUALIFIED if not reasons else QualificationStatus.BLOCKED
    return QualificationResult(
        provider=snapshot.provider,
        status=status,
        snapshot_identity=snapshot.identity() if status is QualificationStatus.QUALIFIED else None,
        reasons=tuple(reasons or ["destination provider snapshot is coherent"]),
        evidence={"snapshot": snapshot.identity_payload()},
    )


def decide_gradle_features(requests: Sequence[GradleFeatureRequest]) -> tuple[GradleFeatureDecision, ...]:
    decisions: list[GradleFeatureDecision] = []
    for request in requests:
        if not request.requested:
            decisions.append(GradleFeatureDecision(request.feature, False, QualificationStatus.QUALIFIED, "not requested"))
            continue
        evidence_json = json.dumps(request.evidence, sort_keys=True, separators=(",", ":"))
        evidence_id = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        missing = [key for key in ("gradle_version", "fixture", "cache_identity") if not request.evidence.get(key)]
        if missing:
            decisions.append(
                GradleFeatureDecision(
                    request.feature,
                    False,
                    QualificationStatus.BLOCKED,
                    "missing opt-in evidence: " + ",".join(missing),
                    evidence_id,
                )
            )
        else:
            decisions.append(
                GradleFeatureDecision(
                    request.feature,
                    True,
                    QualificationStatus.QUALIFIED,
                    "explicitly requested and evidence-backed",
                    evidence_id,
                )
            )
    return tuple(decisions)


def gradle_remote_receipt_converges(request_identity: str, receipt_identity: str, destination: str) -> QualificationResult:
    reasons: list[str] = []
    if not destination:
        reasons.append("destination identity is required")
    if request_identity != receipt_identity:
        reasons.append("remote/federated request identity differs from destination receipt")
    status = QualificationStatus.QUALIFIED if not reasons else QualificationStatus.BLOCKED
    return QualificationResult(
        provider=ProviderKind.GRADLE,
        status=status,
        snapshot_identity=request_identity if status is QualificationStatus.QUALIFIED else None,
        reasons=tuple(reasons or ["remote/federated request and receipt converge"]),
        evidence={"destination": destination, "request_identity": request_identity, "receipt_identity": receipt_identity},
    )


def gradle_explain_doctor_projection(*, plan_identity: str, decisions: Sequence[GradleFeatureDecision], history: GradleAdaptiveHistory) -> dict[str, object]:
    return {
        "plan_identity": plan_identity,
        "features": [
            {
                "feature": decision.feature.value,
                "enabled": decision.enabled,
                "status": decision.status.value,
                "reason": decision.reason,
                "evidence_identity": decision.evidence_identity,
            }
            for decision in decisions
        ],
        "adaptive_history": history.summarize(),
        "benchmark_scope": "bounded-history",
    }


def native_runner_contribution(snapshot: DestinationSnapshot, lifecycle: LifecyclePlan, qualification: QualificationResult) -> dict[str, object]:
    """Return the ENV-owned contribution consumed by EXEC without transferring ownership."""
    return {
        "authority": "ENV",
        "provider": snapshot.provider.value,
        "snapshot_identity": snapshot.identity(),
        "qualified": qualification.qualified,
        "qualification_status": qualification.status.value,
        "lifecycle": lifecycle.explain(),
        "safe_env": snapshot.safe_env(),
        "exec_consumes": ["environment_identity", "toolchain_identity", "cache_ownership", "source_proof"],
    }
