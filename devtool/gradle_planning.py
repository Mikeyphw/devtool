from __future__ import annotations

"""Canonical Gradle semantic planning and execution evidence.

ENV-X03 deliberately keeps Gradle's own task graph/build caches authoritative.
This module owns only Devtool's semantic request/context/policy/invocation plan
and the evidence projection returned to EXO/RER/RUN consumers.
"""

from dataclasses import dataclass, field, replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping, Sequence

from .execution_environments import detect_current_environment
from .models import ProjectConfig


class GradlePlanningError(ValueError):
    pass


def _ordered(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _bool(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _positive(value: object) -> int | None:
    if value in (None, "", "auto"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _version_tuple(value: str | None) -> tuple[int, ...]:
    parts: list[int] = []
    for token in str(value or "").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _hash_payload(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolved_executable(path_or_command: str, *, project_root: Path | None = None) -> Path | None:
    text = str(path_or_command or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() and ("/" in text or "\\" in text):
        candidate = ((project_root or Path.cwd()) / candidate).resolve(strict=False)
    elif candidate.is_absolute():
        candidate = candidate.resolve(strict=False)
    else:
        resolved = shutil.which(text)
        candidate = Path(resolved).resolve(strict=False) if resolved else candidate
    return candidate if candidate.is_file() else None


def _file_identity(path: Path | None) -> tuple[str, str]:
    if path is None:
        return "", ""
    try:
        real = path.resolve(strict=True)
        return str(real), _hash_file(real)
    except (OSError, RuntimeError):
        return str(path.resolve(strict=False)), "<unreadable>"


def _wrapper_distribution_sha256(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    props = project_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not props.is_file():
        return ""
    try:
        for line in props.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("distributionSha256Sum="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _jdk_identity(jdk_home: str, *, project_root: Path) -> tuple[str, str]:
    home = str(jdk_home or "").strip()
    if home:
        suffix = "bin/java.exe" if os.name == "nt" else "bin/java"
        candidate = Path(home).expanduser() / suffix
    else:
        candidate = _resolved_executable("java", project_root=project_root)
    real, digest = _file_identity(candidate if isinstance(candidate, Path) and candidate.is_file() else None)
    return real, digest


def _telemetry_adapter_identity(level: str) -> str:
    """Stable identity of the evidence adapter selected by the planner.

    Summary/off are parser protocol identities. Standard uses the modern
    BuildEvents shared-service adapter; full deliberately uses the legacy
    detail adapter and therefore remains Configuration-Cache incompatible.
    """
    normalized = str(level or "summary").strip().lower()
    if normalized in {"standard", "full"}:
        filename = "devtool-events.init.gradle" if normalized == "standard" else "devtool-events-full.init.gradle"
        init_script = Path(__file__).resolve().parent / "resources" / "gradle" / filename
        kind = "build-events-shared-service" if normalized == "standard" else "legacy-detail-init-script"
        try:
            return _hash_payload({"kind": kind, "level": normalized, "digest": _hash_file(init_script)})
        except OSError:
            return _hash_payload({"kind": kind, "level": normalized, "digest": "<missing>"})
    return _hash_payload({"kind": "console-report-adapter", "protocol": 1, "level": normalized})


def _is_shared_android_path(value: str | Path) -> bool:
    text = str(value or "").replace("\\", "/").rstrip("/").lower()
    return text == "/sdcard" or text.startswith((
        "/sdcard/", "/storage/emulated/", "/storage/self/primary/",
    ))


def _semantic_environment(
    env: Mapping[str, str], *, extra_keys: Sequence[str] = (),
) -> tuple[tuple[str, ...], str]:
    explicit = {
        "GRADLE_OPTS", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JAVA_HOME",
        "ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_NDK_HOME", "ANDROID_NDK_ROOT",
        "KOTLIN_DAEMON_JVMARGS", "CI",
    }
    explicit.update(str(key).strip() for key in extra_keys if str(key).strip())
    keys = sorted(
        key for key in env
        if key in explicit or key.startswith("ORG_GRADLE_PROJECT_")
    )
    # Values are context-significant but never serialized. Only the key list
    # plus one opaque digest leaves this boundary.
    digest = _hash_payload([(key, str(env.get(key) or "")) for key in keys])
    return tuple(keys), digest


def build_logic_identity(root: Path, *, max_files: int = 256) -> tuple[str, tuple[str, ...], bool, int]:
    """Bounded build-logic identity used by aggregate/CC qualification.

    This intentionally excludes generated .gradle/build trees. ENV-X04 may add
    richer project-model/affected identities; X03 needs a stable fail-closed
    identity for planner/qualification correctness now.
    """

    root = root.resolve(strict=False)
    candidates: list[Path] = []
    direct = (
        "settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts",
        "gradle.properties", "gradle/libs.versions.toml",
        "gradle/wrapper/gradle-wrapper.properties", "gradle/wrapper/gradle-wrapper.jar",
    )
    for rel in direct:
        path = root / rel
        if path.is_file():
            candidates.append(path)
    for base_name in ("buildSrc", "build-logic", "gradle"):
        base = root / base_name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or any(part in {"build", ".gradle", ".git"} for part in path.parts):
                continue
            if path.suffix.lower() in {".gradle", ".kts", ".kt", ".java", ".toml", ".properties"}:
                candidates.append(path)
    # Never silently treat a bounded partial scan as complete qualification.
    # The digest contains the overflow count for evidence, while callers fail
    # closed for aggregate/CC reuse when the build-logic set exceeds the bound.
    total_candidates = len(candidates)
    complete = total_candidates <= max_files
    overflow_count = max(0, total_candidates - max_files)
    candidates = candidates[:max_files]
    records: list[tuple[str, str]] = []
    for path in candidates:
        try:
            records.append((path.relative_to(root).as_posix(), _hash_file(path)))
        except OSError:
            records.append((path.relative_to(root).as_posix(), "<unreadable>"))
    digest = _hash_payload({"files": records, "complete": complete, "overflow_count": overflow_count})
    return digest, tuple(name for name, _ in records), complete, overflow_count


def gradle_selection_identity(config: ProjectConfig) -> str:
    """Stable semantic identity for the active cross-runner TestSelection."""
    try:
        from .test_selection import resolve_test_selection
        selection = resolve_test_selection(config)
    except Exception:
        return ""
    if not selection.active():
        return ""
    return _hash_payload(selection.as_dict())


@dataclass(frozen=True, slots=True)
class GradleLauncherIdentity:
    executable: str
    source: str
    required_version: str = ""
    effective_version: str = ""
    wrapper_distribution: str = ""
    executable_realpath: str = ""
    executable_digest: str = ""
    wrapper_properties_digest: str = ""
    wrapper_jar_digest: str = ""
    wrapper_distribution_sha256: str = ""
    identity: str = ""

    @classmethod
    def create(
        cls, *, executable: str, source: str, required_version: str = "",
        effective_version: str = "", wrapper_distribution: str = "",
        project_root: Path | str | None = None,
    ) -> "GradleLauncherIdentity":
        root = Path(project_root).expanduser().resolve(strict=False) if project_root else None
        realpath, executable_digest = _file_identity(_resolved_executable(executable, project_root=root))
        props_digest = ""
        jar_digest = ""
        distribution_sha256 = ""
        if root is not None:
            props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
            jar = root / "gradle" / "wrapper" / "gradle-wrapper.jar"
            try:
                props_digest = _hash_file(props) if props.is_file() else ""
            except OSError:
                props_digest = "<unreadable>"
            try:
                jar_digest = _hash_file(jar) if jar.is_file() else ""
            except OSError:
                jar_digest = "<unreadable>"
            distribution_sha256 = _wrapper_distribution_sha256(root)
        payload = {
            "executable": str(executable), "source": str(source),
            "required_version": str(required_version or ""),
            "effective_version": str(effective_version or ""),
            "wrapper_distribution": str(wrapper_distribution or ""),
            "executable_realpath": realpath,
            "executable_digest": executable_digest,
            "wrapper_properties_digest": props_digest,
            "wrapper_jar_digest": jar_digest,
            "wrapper_distribution_sha256": distribution_sha256,
        }
        return cls(**payload, identity=_hash_payload(payload))

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class GradleCapabilitySet:
    configuration_cache: bool
    vfs_watch: bool
    build_cache: bool
    daemon: bool
    parallel: bool
    isolated_projects: bool
    parallel_configuration_cache: bool
    telemetry_levels: tuple[str, ...] = ("summary", "standard", "full")

    @classmethod
    def for_version(cls, version: str) -> "GradleCapabilitySet":
        parsed = _version_tuple(version)
        return cls(
            configuration_cache=parsed >= (6, 5) if parsed else True,
            vfs_watch=parsed >= (6, 5) if parsed else True,
            build_cache=True,
            daemon=True,
            parallel=True,
            isolated_projects=parsed >= (8, 8) if parsed else False,
            parallel_configuration_cache=parsed >= (8, 11) if parsed else False,
        )

    def as_dict(self) -> dict[str, object]:
        return {name: (list(value) if isinstance(value, tuple) else value) for name, value in ((n, getattr(self, n)) for n in self.__dataclass_fields__)}


@dataclass(frozen=True, slots=True)
class GradleExecutionContext:
    project_root: str
    substrate: str
    launcher: GradleLauncherIdentity
    jdk_home: str
    jdk_executable: str
    jdk_identity: str
    java_requirement: str
    gradle_user_home: str
    project_cache_dir: str
    cache_domain: str
    semantic_environment_keys: tuple[str, ...]
    semantic_environment_digest: str
    trusted_context_inputs: tuple[str, ...]
    build_logic_digest: str
    build_logic_files: tuple[str, ...]
    build_logic_complete: bool
    build_logic_overflow_count: int
    environment_domain: str
    context_digest: str

    @classmethod
    def create(
        cls, *, project_root: Path, launcher: GradleLauncherIdentity,
        jdk_home: str = "", java_requirement: str = "", gradle_user_home: str = "",
        substrate: str = "", environment_domain: str = "",
        semantic_environment: Mapping[str, str] | None = None,
        semantic_environment_keys: Sequence[str] = (),
        trusted_context_inputs: Sequence[str] = (),
    ) -> "GradleExecutionContext":
        root = project_root.resolve(strict=False)
        logic_digest, logic_files, logic_complete, logic_overflow_count = build_logic_identity(root)
        actual_substrate = substrate or detect_current_environment(dict(os.environ))
        domain = environment_domain or actual_substrate
        effective_gradle_user_home = str(gradle_user_home or os.environ.get("GRADLE_USER_HOME") or (Path.home() / ".gradle"))
        project_cache_dir = str((root / ".gradle").resolve(strict=False))
        cache_domain = (
            "shared-android-storage"
            if _is_shared_android_path(effective_gradle_user_home) or _is_shared_android_path(root)
            else "host-private"
        )
        env_keys, env_digest = _semantic_environment(
            semantic_environment or os.environ,
            extra_keys=semantic_environment_keys,
        )
        trusted_inputs = _ordered(trusted_context_inputs)
        jdk_executable, jdk_digest = _jdk_identity(jdk_home, project_root=root)
        jdk_identity = _hash_payload({
            "jdk_home": str(jdk_home or ""),
            "java_executable": jdk_executable,
            "java_digest": jdk_digest,
            "requirement": str(java_requirement or ""),
        })
        payload = {
            "project_root": str(root), "substrate": actual_substrate,
            "launcher_identity": launcher.identity, "jdk_home": str(jdk_home or ""),
            "jdk_identity": jdk_identity,
            "java_requirement": str(java_requirement or ""),
            "gradle_user_home": effective_gradle_user_home,
            "project_cache_dir": project_cache_dir,
            "cache_domain": cache_domain,
            "semantic_environment_digest": env_digest,
            "trusted_context_inputs": trusted_inputs,
            "build_logic_digest": logic_digest, "build_logic_complete": logic_complete,
            "build_logic_overflow_count": logic_overflow_count, "environment_domain": domain,
        }
        return cls(
            project_root=str(root), substrate=actual_substrate, launcher=launcher,
            jdk_home=str(jdk_home or ""), jdk_executable=jdk_executable, jdk_identity=jdk_identity,
            java_requirement=str(java_requirement or ""), gradle_user_home=effective_gradle_user_home,
            project_cache_dir=project_cache_dir, cache_domain=cache_domain, build_logic_digest=logic_digest,
            semantic_environment_keys=env_keys, semantic_environment_digest=env_digest,
            trusted_context_inputs=trusted_inputs,
            build_logic_files=logic_files, build_logic_complete=logic_complete,
            build_logic_overflow_count=logic_overflow_count, environment_domain=domain,
            context_digest=_hash_payload(payload),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "project_root": self.project_root, "substrate": self.substrate,
            "launcher": self.launcher.as_dict(), "jdk_home": self.jdk_home,
            "jdk_executable": self.jdk_executable, "jdk_identity": self.jdk_identity,
            "java_requirement": self.java_requirement, "gradle_user_home": self.gradle_user_home,
            "project_cache_dir": self.project_cache_dir, "cache_domain": self.cache_domain,
            "semantic_environment_keys": list(self.semantic_environment_keys),
            "semantic_environment_digest": self.semantic_environment_digest,
            "trusted_context_inputs": list(self.trusted_context_inputs),
            "build_logic_digest": self.build_logic_digest, "build_logic_files": list(self.build_logic_files),
            "build_logic_complete": self.build_logic_complete,
            "build_logic_overflow_count": self.build_logic_overflow_count,
            "environment_domain": self.environment_domain, "context_digest": self.context_digest,
        }


@dataclass(frozen=True, slots=True)
class PolicyValue:
    requested: object
    effective: object
    provenance: str
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"requested": self.requested, "effective": self.effective, "provenance": self.provenance, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class GradleHeapPolicy:
    default_mb: int
    phase_minimum_mb: int
    requested_mb: int | None
    host_safe_ceiling_mb: int
    effective_mb: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ConfigurationCacheQualification:
    requested: str
    effective: str
    compatible: bool
    telemetry_level: str
    context_digest: str
    launcher_identity: str
    jdk_identity: str
    build_logic_digest: str
    telemetry_adapter_identity: str
    semantic_argument_digest: str
    fallback_reason: str = ""
    qualification_id: str = ""

    @classmethod
    def create(
        cls, *, requested: str, effective: str, compatible: bool, telemetry_level: str,
        context: GradleExecutionContext, semantic_argument_digest: str,
        telemetry_adapter_identity: str, fallback_reason: str = "",
    ) -> "ConfigurationCacheQualification":
        payload = {
            "requested": requested, "effective": effective, "compatible": compatible,
            "telemetry_level": telemetry_level, "context_digest": context.context_digest,
            "launcher_identity": context.launcher.identity,
            "jdk_identity": context.jdk_identity,
            "build_logic_digest": context.build_logic_digest,
            "telemetry_adapter_identity": telemetry_adapter_identity,
            "semantic_argument_digest": semantic_argument_digest,
            "fallback_reason": fallback_reason,
        }
        return cls(**payload, qualification_id=_hash_payload(payload))

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class VfsQualification:
    requested: object
    effective: bool
    compatible: bool
    substrate: str
    cache_domain: str
    context_digest: str
    fallback_reason: str = ""
    qualification_id: str = ""

    @classmethod
    def create(
        cls, *, requested: object, effective: bool, compatible: bool,
        context: GradleExecutionContext, fallback_reason: str = "",
    ) -> "VfsQualification":
        payload = {
            "requested": requested,
            "effective": bool(effective),
            "compatible": bool(compatible),
            "substrate": context.substrate,
            "cache_domain": context.cache_domain,
            "context_digest": context.context_digest,
            "fallback_reason": fallback_reason,
        }
        return cls(**payload, qualification_id=_hash_payload(payload))

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class GradleExecutionPolicy:
    resource_profile: str
    resource_profile_provenance: str
    resource_profile_reason: str
    resource_profile_evidence: Mapping[str, object]
    configuration_cache: PolicyValue
    configuration_cache_qualification: ConfigurationCacheQualification
    build_cache: PolicyValue
    daemon: PolicyValue
    vfs_watch: PolicyValue
    vfs_qualification: VfsQualification
    parallel: PolicyValue
    max_workers: PolicyValue
    heap: GradleHeapPolicy
    telemetry_level: PolicyValue
    failure_policy: PolicyValue
    policy_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "resource_profile": self.resource_profile,
            "resource_profile_provenance": self.resource_profile_provenance,
            "resource_profile_reason": self.resource_profile_reason,
            "resource_profile_evidence": dict(self.resource_profile_evidence),
            "configuration_cache": self.configuration_cache.as_dict(),
            "configuration_cache_qualification": self.configuration_cache_qualification.as_dict(),
            "build_cache": self.build_cache.as_dict(), "daemon": self.daemon.as_dict(),
            "vfs_watch": self.vfs_watch.as_dict(), "vfs_qualification": self.vfs_qualification.as_dict(),
            "parallel": self.parallel.as_dict(),
            "max_workers": self.max_workers.as_dict(), "heap": self.heap.as_dict(),
            "telemetry_level": self.telemetry_level.as_dict(), "failure_policy": self.failure_policy.as_dict(),
            "policy_digest": self.policy_digest,
        }


@dataclass(frozen=True, slots=True)
class GradleInvocationBarrier:
    kind: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "detail": self.detail}


def aggregate_qualification_id(
    *, name: str, replacement: str, roots: Sequence[str], context_digest: str,
    build_logic_digest: str, semantic_request_digest: str = "",
    evidence_receipt_id: str = "", evidence_outcome_digest: str = "",
) -> str:
    """Return the durable identity required to authorize an aggregate root."""
    payload = {
        "name": str(name), "replacement": str(replacement), "roots": _ordered(roots),
        "context_digest": str(context_digest), "build_logic_digest": str(build_logic_digest),
        "semantic_request_digest": str(semantic_request_digest or ""),
        "evidence_receipt_id": str(evidence_receipt_id or ""),
        "evidence_outcome_digest": str(evidence_outcome_digest or ""),
    }
    return _hash_payload(payload)


_AGGREGATE_SUCCESS_OUTCOMES = {
    "success", "succeeded", "executed", "up-to-date", "from-cache", "no-source",
}


def _aggregate_receipt_evidence(
    receipt: Mapping[str, object], *, roots: Sequence[str], replacement: str,
    context: GradleExecutionContext,
) -> tuple[str, str, str] | None:
    """Validate one embedded execution receipt as aggregate qualification proof.

    X03 deliberately validates the self-contained receipt rather than trusting a
    repository boolean/hash.  X04 may later add discovery/refresh UX and durable
    history lookup, but the planner already fails closed unless the proof says a
    real Gradle process ran the aggregate in the same context and observed every
    replaced root with a successful Gradle outcome.
    """
    receipt_id = str(receipt.get("receipt_id") or "").strip()
    if not receipt_id:
        return None
    payload = {str(key): value for key, value in receipt.items() if str(key) != "receipt_id"}
    if receipt_id != _hash_payload(payload):
        return None
    if not bool(receipt.get("success")) or not bool(receipt.get("process_executed")):
        return None
    if str(receipt.get("context_digest") or "") != context.context_digest:
        return None
    requested = set(str(item) for item in (receipt.get("requested_roots") or ()) if str(item).strip())
    optimized = set(str(item) for item in (receipt.get("optimized_roots") or ()) if str(item).strip())
    if str(replacement) not in requested and str(replacement) not in optimized:
        return None
    # Qualification must be tied to durable execution lineage, not a detached
    # synthetic payload.  Either RUN or EXO process identity is sufficient here;
    # RER remains optional because a fresh qualification need not be reused.
    run_ref = str(receipt.get("run_ref") or "").strip()
    exo_ref = str(receipt.get("exo_run_ref") or "").strip()
    if not run_ref and not exo_ref:
        return None
    outcomes = {
        str(key): str(value).strip().lower()
        for key, value in dict(receipt.get("task_outcomes") or {}).items()
        if str(key).strip()
    }
    observed = set(str(item) for item in (receipt.get("observed_tasks") or ()) if str(item).strip())
    proof: dict[str, str] = {}
    for root in _ordered(roots):
        outcome = outcomes.get(root, "")
        if root not in observed or outcome not in _AGGREGATE_SUCCESS_OUTCOMES:
            return None
        proof[root] = outcome
    outcome_digest = _hash_payload({
        "receipt_id": receipt_id,
        "replacement": str(replacement),
        "roots": list(_ordered(roots)),
        "outcomes": proof,
    })
    return receipt_id, outcome_digest, run_ref or exo_ref


def aggregate_qualification_proof(
    *, name: str, replacement: str, roots: Sequence[str], context: GradleExecutionContext,
    semantic_request_digest: str, evidence_receipt: Mapping[str, object],
) -> dict[str, object]:
    """Build the fail-closed config payload for a proven aggregate alias."""
    evidence = _aggregate_receipt_evidence(
        evidence_receipt, roots=roots, replacement=replacement, context=context,
    )
    if evidence is None:
        raise GradlePlanningError(
            "Aggregate qualification receipt must be self-consistent, same-context, process-executed, "
            "and observe every replaced root with a successful outcome"
        )
    receipt_id, outcome_digest, lineage_ref = evidence
    qualification_id = aggregate_qualification_id(
        name=name, replacement=replacement, roots=roots,
        context_digest=context.context_digest, build_logic_digest=context.build_logic_digest,
        semantic_request_digest=semantic_request_digest,
        evidence_receipt_id=receipt_id, evidence_outcome_digest=outcome_digest,
    )
    return {
        "context_digest": context.context_digest,
        "build_logic_digest": context.build_logic_digest,
        "semantic_request_digest": semantic_request_digest,
        "evidence_receipt": dict(evidence_receipt),
        "evidence_receipt_id": receipt_id,
        "evidence_outcome_digest": outcome_digest,
        "evidence_lineage_ref": lineage_ref,
        "qualification_id": qualification_id,
    }


@dataclass(frozen=True, slots=True)
class AggregateQualification:
    name: str
    replacement: str
    roots: tuple[str, ...]
    context_digest: str
    build_logic_digest: str
    semantic_request_digest: str
    evidence_receipt_id: str
    evidence_outcome_digest: str
    evidence_lineage_ref: str
    qualification_id: str

    @classmethod
    def from_mapping(
        cls, name: str, raw: Mapping[str, object], context: GradleExecutionContext,
        semantic_request_digest: str,
    ) -> "AggregateQualification | None":
        if not context.build_logic_complete:
            return None
        replacement = str(raw.get("replacement") or raw.get("task") or raw.get("alias") or "").strip()
        roots_raw = raw.get("roots") or raw.get("tasks") or raw.get("source") or raw.get("sources") or ()
        roots = _ordered(roots_raw if isinstance(roots_raw, (list, tuple, set)) else str(roots_raw).split())
        # Qualification must be a durable receipt binding, not an optimistic
        # boolean or a caller-computable context hash.
        bound_context = str(raw.get("context_digest") or "").strip()
        bound_logic = str(raw.get("build_logic_digest") or "").strip()
        bound_request = str(raw.get("semantic_request_digest") or "").strip()
        evidence_receipt = raw.get("evidence_receipt")
        if not replacement or len(roots) < 2 or not isinstance(evidence_receipt, Mapping):
            return None
        evidence = _aggregate_receipt_evidence(
            evidence_receipt, roots=roots, replacement=replacement, context=context,
        )
        if evidence is None:
            return None
        receipt_id, outcome_digest, lineage_ref = evidence
        if str(raw.get("evidence_receipt_id") or "").strip() != receipt_id:
            return None
        if str(raw.get("evidence_outcome_digest") or "").strip() != outcome_digest:
            return None
        if str(raw.get("evidence_lineage_ref") or "").strip() != lineage_ref:
            return None
        declared = str(raw.get("qualification_id") or "").strip()
        expected = aggregate_qualification_id(
            name=name, replacement=replacement, roots=roots,
            context_digest=bound_context, build_logic_digest=bound_logic,
            semantic_request_digest=bound_request,
            evidence_receipt_id=receipt_id, evidence_outcome_digest=outcome_digest,
        )
        if (
            declared != expected
            or bound_context != context.context_digest
            or bound_logic != context.build_logic_digest
            or bound_request != semantic_request_digest
        ):
            return None
        return cls(
            name=name, replacement=replacement, roots=roots,
            context_digest=bound_context, build_logic_digest=bound_logic,
            semantic_request_digest=bound_request,
            evidence_receipt_id=receipt_id, evidence_outcome_digest=outcome_digest,
            evidence_lineage_ref=lineage_ref, qualification_id=expected,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name, "replacement": self.replacement, "roots": list(self.roots),
            "context_digest": self.context_digest, "build_logic_digest": self.build_logic_digest,
            "semantic_request_digest": self.semantic_request_digest,
            "evidence_receipt_id": self.evidence_receipt_id,
            "evidence_outcome_digest": self.evidence_outcome_digest,
            "evidence_lineage_ref": self.evidence_lineage_ref,
            "qualification_id": self.qualification_id,
        }


@dataclass(frozen=True, slots=True)
class GradleArgumentClasses:
    raw: tuple[str, ...] = ()
    managed: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    passthrough: tuple[str, ...] = ()
    semantic_properties: tuple[str, ...] = ()
    argument_digest: str = ""

    def as_dict(self) -> dict[str, object]:
        # Raw Gradle properties may contain credentials. Evidence preserves
        # their semantic keys/classes but never serializes property values.
        raw_safe = tuple(_evidence_safe_raw_arg(arg) for arg in self.raw)
        passthrough_safe = tuple(_evidence_safe_raw_arg(arg) for arg in self.passthrough)
        return {
            "raw": list(raw_safe),
            "managed": list(self.managed),
            "protected": list(self.protected),
            "passthrough": list(passthrough_safe),
            "semantic_properties": list(self.semantic_properties),
            "argument_digest": self.argument_digest,
        }


def classify_gradle_arguments(args: Sequence[str]) -> GradleArgumentClasses:
    """Classify configured raw Gradle argv against Devtool-owned policy.

    Managed switches may be supplied raw only when they agree with typed policy;
    protected switches mutate semantic context or Devtool-owned JVM/telemetry
    policy and therefore fail closed. Everything else remains passthrough.
    """
    raw = tuple(str(arg) for arg in args if str(arg).strip())
    managed_exact = {
        "--configuration-cache", "--no-configuration-cache",
        "--build-cache", "--no-build-cache", "--daemon", "--no-daemon",
        "--parallel", "--no-parallel", "--continue",
    }
    protected_exact = {
        "-p", "--project-dir", "-c", "--settings-file",
        "-g", "--gradle-user-home", "-I", "--init-script",
        "--project-cache-dir",
    }
    managed: list[str] = []
    protected: list[str] = []
    passthrough: list[str] = []
    index = 0
    while index < len(raw):
        arg = raw[index]
        if arg == "--max-workers" and index + 1 < len(raw):
            managed.extend((arg, raw[index + 1]))
            index += 2
            continue
        if (
            arg in managed_exact
            or arg.startswith("--max-workers=")
            or arg.startswith("-Dorg.gradle.vfs.watch=")
            or arg.startswith("-Dorg.gradle.parallel=")
            or arg.startswith("-Dorg.gradle.caching=")
            or arg.startswith("-Dorg.gradle.configuration-cache=")
            or arg.startswith("-Dorg.gradle.daemon=")
            or arg.startswith("-Dorg.gradle.workers.max=")
        ):
            managed.append(arg)
        elif (
            arg in protected_exact
            or arg.startswith("--project-dir=")
            or arg.startswith("--settings-file=")
            or arg.startswith("--gradle-user-home=")
            or arg.startswith("--init-script=")
            or arg.startswith("--project-cache-dir=")
            or (arg.startswith("-I") and arg != "-I")
            or arg.startswith("-Dorg.gradle.jvmargs=")
            or arg.startswith("-Dorg.gradle.java.home=")
            or arg.startswith("-Dgradle.user.home=")
        ):
            protected.append(arg)
        else:
            passthrough.append(arg)
        index += 1
    # Identity is computed from the real argv in-memory, while evidence below
    # serializes only redacted forms. This keeps semantic property value changes
    # cache/plan-significant without leaking credentials or feature values.
    identity_payload = {
        "raw": raw,
        "managed": tuple(managed),
        "protected": tuple(protected),
        "passthrough": tuple(passthrough),
    }
    return GradleArgumentClasses(
        raw=raw, managed=tuple(managed), protected=tuple(protected), passthrough=tuple(passthrough),
        semantic_properties=_semantic_raw_properties(passthrough), argument_digest=_hash_payload(identity_payload),
    )


@dataclass(frozen=True, slots=True)
class GradleInvocationRequest:
    caller: str
    action: str
    project_root: str
    requested_roots: tuple[str, ...]
    # Optional caller-owned conservative narrowing (for example Android
    # component pruning). Original requested roots remain authoritative evidence.
    preoptimized_roots: tuple[str, ...] = ()
    validation: bool = False
    validation_phase: str = ""
    raw_args: tuple[str, ...] = ()
    barriers: tuple[GradleInvocationBarrier, ...] = ()
    selection_identity: str = ""
    expected_artifact_producers: tuple[str, ...] = ()
    expected_report_producers: tuple[str, ...] = ()
    trusted_context_inputs: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "caller": self.caller, "action": self.action, "project_root": self.project_root,
            "requested_roots": list(self.requested_roots), "preoptimized_roots": list(self.preoptimized_roots), "validation": self.validation,
            "validation_phase": self.validation_phase, "raw_args": [_evidence_safe_raw_arg(arg) for arg in self.raw_args],
            "barriers": [item.as_dict() for item in self.barriers], "selection_identity": self.selection_identity,
            "expected_artifact_producers": list(self.expected_artifact_producers),
            "expected_report_producers": list(self.expected_report_producers),
            "trusted_context_inputs": list(self.trusted_context_inputs),
        }


@dataclass(frozen=True, slots=True)
class GradlePhysicalInvocation:
    roots: tuple[str, ...]
    args: tuple[str, ...]
    barriers: tuple[GradleInvocationBarrier, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {"roots": list(self.roots), "args": list(self.args), "barriers": [item.as_dict() for item in self.barriers]}


@dataclass(frozen=True, slots=True)
class GradleInvocationPlan:
    request: GradleInvocationRequest
    context: GradleExecutionContext
    capabilities: GradleCapabilitySet
    policy: GradleExecutionPolicy
    requested_roots: tuple[str, ...]
    optimized_roots: tuple[str, ...]
    aggregate_qualifications: tuple[AggregateQualification, ...]
    rejected_aggregates: tuple[str, ...]
    arguments: GradleArgumentClasses
    semantic_request_digest: str
    invocations: tuple[GradlePhysicalInvocation, ...]
    plan_digest: str

    def as_dict(self) -> dict[str, object]:
        return {
            "request": self.request.as_dict(), "context": self.context.as_dict(),
            "capabilities": self.capabilities.as_dict(), "policy": self.policy.as_dict(),
            "requested_roots": list(self.requested_roots), "optimized_roots": list(self.optimized_roots),
            "aggregate_qualifications": [item.as_dict() for item in self.aggregate_qualifications],
            "rejected_aggregates": list(self.rejected_aggregates),
            "arguments": self.arguments.as_dict(),
            "semantic_request_digest": self.semantic_request_digest,
            "invocations": [item.as_dict() for item in self.invocations], "plan_digest": self.plan_digest,
        }


@dataclass(frozen=True, slots=True)
class GradleExecutionReceipt:
    """Bounded RUN/EXO evidence projection for one Gradle process boundary."""

    receipt_id: str
    plan_digest: str
    context_digest: str
    policy_digest: str
    requested_roots: tuple[str, ...]
    optimized_roots: tuple[str, ...]
    observed_tasks: tuple[str, ...]
    task_outcomes: Mapping[str, str]
    process_executed: bool
    success: bool
    selection_identity: str
    launcher_identity: str = ""
    gradle_version: str = ""
    jdk_home: str = ""
    jdk_identity: str = ""
    substrate: str = ""
    resource_profile: str = ""
    resource_profile_provenance: str = ""
    resource_profile_reason: str = ""
    resource_profile_evidence: Mapping[str, object] = field(default_factory=dict)
    configuration_cache: Mapping[str, object] = field(default_factory=dict)
    configuration_cache_qualification: Mapping[str, object] = field(default_factory=dict)
    build_cache: Mapping[str, object] = field(default_factory=dict)
    daemon: Mapping[str, object] = field(default_factory=dict)
    vfs: Mapping[str, object] = field(default_factory=dict)
    vfs_qualification: Mapping[str, object] = field(default_factory=dict)
    workers: Mapping[str, object] = field(default_factory=dict)
    heap: Mapping[str, object] = field(default_factory=dict)
    parallel: Mapping[str, object] = field(default_factory=dict)
    failure_policy: Mapping[str, object] = field(default_factory=dict)
    telemetry: Mapping[str, object] = field(default_factory=dict)
    arguments: Mapping[str, object] = field(default_factory=dict)
    expected_artifact_producers: tuple[str, ...] = ()
    expected_report_producers: tuple[str, ...] = ()
    producer_artifact_refs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    artifact_refs: tuple[str, ...] = ()
    report_refs: tuple[str, ...] = ()
    diagnostic_ids: tuple[str, ...] = ()
    duration_seconds: float = 0.0
    exo_run_ref: str = ""
    rer_ref: str = ""
    run_ref: str = ""

    @classmethod
    def from_plan_metadata(
        cls, plan: Mapping[str, object], *, progress: Mapping[str, object] | None,
        success: bool, process_executed: bool, artifact_refs: Sequence[str] = (),
        producer_artifact_refs: Mapping[str, Sequence[str]] | None = None,
        report_refs: Sequence[str] = (), diagnostic_ids: Sequence[str] = (),
        duration_seconds: float = 0.0, exo_run_ref: str = "", rer_ref: str = "", run_ref: str = "",
    ) -> "GradleExecutionReceipt":
        progress = dict(progress or {})
        request = dict(plan.get("request") or {})
        context = dict(plan.get("context") or {})
        policy = dict(plan.get("policy") or {})
        launcher = dict(context.get("launcher") or {})
        observed = _ordered(progress.get("observed_tasks") or dict(progress.get("task_outcomes") or {}).keys())
        outcomes = {str(k): str(v) for k, v in dict(progress.get("task_outcomes") or {}).items()}
        requested = tuple(str(x) for x in plan.get("requested_roots") or ())
        optimized = tuple(str(x) for x in plan.get("optimized_roots") or ())
        expected = tuple(str(x) for x in request.get("expected_artifact_producers") or ())
        expected_reports = tuple(str(x) for x in request.get("expected_report_producers") or ())
        producer_refs = {
            str(producer): tuple(_ordered(str(ref) for ref in refs))
            for producer, refs in dict(producer_artifact_refs or {}).items()
            if str(producer).strip()
        }
        cc = dict(policy.get("configuration_cache") or {})
        cc["observed_state"] = str(progress.get("configuration_cache_state") or "unknown")
        daemon = dict(policy.get("daemon") or {})
        daemon["observed_state"] = str(progress.get("daemon_state") or "unknown")
        payload = {
            "plan_digest": str(plan.get("plan_digest") or ""),
            "context_digest": str(context.get("context_digest") or ""),
            "policy_digest": str(policy.get("policy_digest") or ""),
            "requested_roots": list(requested), "optimized_roots": list(optimized),
            "observed_tasks": list(observed), "task_outcomes": outcomes,
            "process_executed": bool(process_executed), "success": bool(success),
            "selection_identity": str(request.get("selection_identity") or ""),
            "launcher_identity": str(launcher.get("identity") or ""),
            "gradle_version": str(launcher.get("effective_version") or launcher.get("required_version") or ""),
            "jdk_home": str(context.get("jdk_home") or ""),
            "jdk_identity": str(context.get("jdk_identity") or ""),
            "substrate": str(context.get("substrate") or ""),
            "resource_profile": str(policy.get("resource_profile") or ""),
            "resource_profile_provenance": str(policy.get("resource_profile_provenance") or ""),
            "resource_profile_reason": str(policy.get("resource_profile_reason") or ""),
            "resource_profile_evidence": dict(policy.get("resource_profile_evidence") or {}),
            "configuration_cache": cc,
            "configuration_cache_qualification": dict(policy.get("configuration_cache_qualification") or {}),
            "build_cache": dict(policy.get("build_cache") or {}), "daemon": daemon,
            "vfs": dict(policy.get("vfs_watch") or {}), "workers": dict(policy.get("max_workers") or {}),
            "vfs_qualification": dict(policy.get("vfs_qualification") or {}),
            "heap": dict(policy.get("heap") or {}), "parallel": dict(policy.get("parallel") or {}),
            "failure_policy": dict(policy.get("failure_policy") or {}),
            "telemetry": dict(policy.get("telemetry_level") or {}),
            "arguments": dict(plan.get("arguments") or {}),
            "expected_artifact_producers": list(expected),
            "expected_report_producers": list(expected_reports),
            "producer_artifact_refs": {key: list(value) for key, value in sorted(producer_refs.items())},
            "artifact_refs": list(artifact_refs), "report_refs": list(report_refs),
            "diagnostic_ids": list(diagnostic_ids), "duration_seconds": round(float(duration_seconds), 6),
            "exo_run_ref": exo_run_ref, "rer_ref": rer_ref, "run_ref": run_ref,
        }
        return cls(
            receipt_id=_hash_payload(payload), plan_digest=payload["plan_digest"], context_digest=payload["context_digest"],
            policy_digest=payload["policy_digest"], requested_roots=requested, optimized_roots=optimized,
            observed_tasks=observed, task_outcomes=outcomes, process_executed=bool(process_executed),
            success=bool(success), selection_identity=payload["selection_identity"],
            launcher_identity=payload["launcher_identity"], gradle_version=payload["gradle_version"],
            jdk_home=payload["jdk_home"], jdk_identity=payload["jdk_identity"],
            substrate=payload["substrate"], resource_profile=payload["resource_profile"],
            resource_profile_provenance=payload["resource_profile_provenance"],
            resource_profile_reason=payload["resource_profile_reason"],
            resource_profile_evidence=payload["resource_profile_evidence"],
            configuration_cache=payload["configuration_cache"],
            configuration_cache_qualification=payload["configuration_cache_qualification"],
            build_cache=payload["build_cache"], daemon=payload["daemon"], vfs=payload["vfs"],
            vfs_qualification=payload["vfs_qualification"], workers=payload["workers"], heap=payload["heap"],
            parallel=payload["parallel"], failure_policy=payload["failure_policy"],
            telemetry=payload["telemetry"], arguments=payload["arguments"],
            expected_artifact_producers=expected, expected_report_producers=expected_reports,
            producer_artifact_refs=producer_refs,
            artifact_refs=tuple(artifact_refs), report_refs=tuple(report_refs),
            diagnostic_ids=tuple(diagnostic_ids), duration_seconds=float(duration_seconds),
            exo_run_ref=exo_run_ref, rer_ref=rer_ref, run_ref=run_ref,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "receipt_id": self.receipt_id, "plan_digest": self.plan_digest, "context_digest": self.context_digest,
            "policy_digest": self.policy_digest, "requested_roots": list(self.requested_roots),
            "optimized_roots": list(self.optimized_roots), "observed_tasks": list(self.observed_tasks),
            "task_outcomes": dict(self.task_outcomes), "process_executed": self.process_executed,
            "success": self.success, "selection_identity": self.selection_identity,
            "launcher_identity": self.launcher_identity, "gradle_version": self.gradle_version,
            "jdk_home": self.jdk_home, "jdk_identity": self.jdk_identity,
            "substrate": self.substrate, "resource_profile": self.resource_profile,
            "resource_profile_provenance": self.resource_profile_provenance,
            "resource_profile_reason": self.resource_profile_reason,
            "resource_profile_evidence": dict(self.resource_profile_evidence),
            "configuration_cache": dict(self.configuration_cache),
            "configuration_cache_qualification": dict(self.configuration_cache_qualification),
            "build_cache": dict(self.build_cache), "daemon": dict(self.daemon), "vfs": dict(self.vfs),
            "vfs_qualification": dict(self.vfs_qualification), "workers": dict(self.workers),
            "heap": dict(self.heap), "parallel": dict(self.parallel),
            "failure_policy": dict(self.failure_policy), "telemetry": dict(self.telemetry),
            "arguments": dict(self.arguments),
            "expected_artifact_producers": list(self.expected_artifact_producers),
            "expected_report_producers": list(self.expected_report_producers),
            "producer_artifact_refs": {key: list(value) for key, value in sorted(self.producer_artifact_refs.items())},
            "artifact_refs": list(self.artifact_refs), "report_refs": list(self.report_refs),
            "diagnostic_ids": list(self.diagnostic_ids), "duration_seconds": self.duration_seconds,
            "exo_run_ref": self.exo_run_ref, "rer_ref": self.rer_ref, "run_ref": self.run_ref,
        }


def _normalize_cc(value: object) -> str:
    text = str(value if value not in (None, "") else "auto").strip().lower().replace("_", "-")
    aliases = {"true": "on", "enabled": "on", "false": "off", "disabled": "off"}
    text = aliases.get(text, text)
    if text not in {"auto", "on", "off", "required"}:
        raise GradlePlanningError(f"Unsupported Gradle configuration_cache policy: {value!r}")
    return text


def _evidence_safe_raw_arg(arg: object) -> str:
    text = str(arg or "")
    lower = text.lower()
    if text.startswith(("-P", "-D")) and "=" in text:
        key = text.split("=", 1)[0]
        return f"{key}=<redacted>"
    if "=" in text and any(token in lower.split("=", 1)[0] for token in ("password", "passwd", "token", "secret", "credential", "apikey", "api_key")):
        return text.split("=", 1)[0] + "=<redacted>"
    return text


def _semantic_raw_properties(raw_args: Sequence[str]) -> tuple[str, ...]:
    out: list[str] = []
    for arg in raw_args:
        text = str(arg)
        if text.startswith(("-P", "-D")):
            # Preserve property identity but never the value in plan/receipt evidence.
            out.append(_evidence_safe_raw_arg(text))
    return _ordered(out)


def _raw_switches(raw_args: Sequence[str]) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {name: set() for name in ("configuration_cache", "build_cache", "daemon", "parallel", "vfs", "max_workers", "failure_policy")}
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg == "--configuration-cache": values["configuration_cache"].add("on")
        elif arg == "--no-configuration-cache": values["configuration_cache"].add("off")
        elif arg == "--build-cache": values["build_cache"].add("on")
        elif arg == "--no-build-cache": values["build_cache"].add("off")
        elif arg == "--daemon": values["daemon"].add("on")
        elif arg == "--no-daemon": values["daemon"].add("off")
        elif arg in {"-Dorg.gradle.daemon=true", "-Dorg.gradle.daemon=false"}:
            values["daemon"].add("on" if arg.endswith("=true") else "off")
        elif arg == "--parallel": values["parallel"].add("on")
        elif arg == "--no-parallel": values["parallel"].add("off")
        elif arg in {"-Dorg.gradle.parallel=true", "-Dorg.gradle.parallel=false"}:
            values["parallel"].add("on" if arg.endswith("=true") else "off")
        elif arg in {"-Dorg.gradle.caching=true", "-Dorg.gradle.caching=false"}:
            values["build_cache"].add("on" if arg.endswith("=true") else "off")
        elif arg in {"-Dorg.gradle.vfs.watch=true", "-Dorg.gradle.vfs.watch=false"}:
            values["vfs"].add("on" if arg.endswith("=true") else "off")
        elif arg in {"-Dorg.gradle.configuration-cache=true", "-Dorg.gradle.configuration-cache=false"}:
            values["configuration_cache"].add("on" if arg.endswith("=true") else "off")
        elif arg.startswith("--max-workers="):
            values["max_workers"].add(arg.split("=", 1)[1])
        elif arg == "--max-workers" and index + 1 < len(raw_args):
            values["max_workers"].add(str(raw_args[index + 1]))
            index += 1
        elif arg.startswith("-Dorg.gradle.workers.max="):
            values["max_workers"].add(arg.split("=", 1)[1])
        elif arg == "--continue":
            values["failure_policy"].add("collect")
        index += 1
    for name, seen in values.items():
        if len(seen) > 1:
            raise GradlePlanningError(f"Contradictory raw Gradle {name} flags: {sorted(seen)}")
    return values


def _typed_or_raw_bool(options: Mapping[str, object], key: str, raw: Mapping[str, set[str]], default: bool) -> PolicyValue:
    raw_values = raw.get(key, set())
    typed_present = key in options and options.get(key) not in (None, "", "auto")
    typed = _bool(options.get(key), default)
    if raw_values:
        raw_value = next(iter(raw_values)) == "on"
        if typed_present and typed != raw_value:
            raise GradlePlanningError(f"Typed Gradle {key}={typed} conflicts with raw flag requesting {raw_value}")
        return PolicyValue(raw_value, raw_value, "raw-argv")
    return PolicyValue(options.get(key, "auto") if typed_present else "auto", typed, "typed-config" if typed_present else "planner-default")


def _phase_floor(phase: str) -> int:
    return {
        "preflight": 768, "dependencies": 768, "compile": 768, "resources": 1024,
        "unit-test": 768, "test": 768, "package": 1280, "lint": 1536,
        "instrumentation": 768, "finalize": 768,
    }.get(str(phase or "").lower().replace("_", "-"), 768)


def _safe_heap_ceiling(memory_mb: int | None, profile: str) -> int:
    basis = int(memory_mb or 4096)
    ratio = 0.45 if profile == "balanced" else 0.35 if profile == "constrained" else 0.25
    return max(768, int(basis * ratio))


def resolve_gradle_policy(
    *, options: Mapping[str, object], context: GradleExecutionContext, capabilities: GradleCapabilitySet,
    action: str = "", validation: bool = False, validation_phase: str = "",
    cpu_count: int = 1, memory_mb: int | None = None,
) -> GradleExecutionPolicy:
    raw_args = tuple(str(x) for x in (options.get("args") or ()) if str(x).strip()) if not isinstance(options.get("args"), str) else tuple(str(options.get("args")).split())
    argument_classes = classify_gradle_arguments(raw_args)
    raw = _raw_switches(raw_args)
    requested_profile = str(options.get("resource_profile") or options.get("profile") or "auto").strip().lower()
    if requested_profile not in {"auto", "balanced", "constrained", "emergency"}:
        raise GradlePlanningError(f"Unsupported Gradle resource profile: {requested_profile}")
    if requested_profile == "auto":
        basis_memory = int(memory_mb or 0)
        basis_cpu = max(1, int(cpu_count or 1))
        if basis_memory and basis_memory <= 1536:
            profile = "emergency"
            profile_reason = f"host memory {basis_memory}MiB <= 1536MiB emergency threshold"
        elif (basis_memory and basis_memory <= 3072) or basis_cpu <= 1:
            profile = "constrained"
            profile_reason = (
                f"host memory {basis_memory}MiB <= 3072MiB constrained threshold"
                if basis_memory and basis_memory <= 3072
                else f"host CPU capacity {basis_cpu} <= 1 constrained threshold"
            )
        else:
            profile = "balanced"
            profile_reason = "host evidence within balanced thresholds"
        profile_provenance = "adaptive-host-evidence"
    else:
        profile = requested_profile
        profile_reason = f"explicit {requested_profile} profile"
        profile_provenance = str(options.get("_resource_profile_provenance") or "typed-config")
    profile_evidence = {
        "requested": requested_profile,
        "cpu_count": max(1, int(cpu_count or 1)),
        "memory_mb": int(memory_mb or 0),
    }

    requested_workers = _positive(options.get("max_workers"))
    raw_workers = raw.get("max_workers", set())
    env_workers = None
    # Explicit typed or raw Gradle policy has authority over inherited host
    # defaults. The legacy environment ceiling applies only to auto policy.
    if requested_workers is None and not raw_workers:
        env_raw = str(os.environ.get("DEVTOOL_MAX_WORKERS", "") or "").strip()
        if env_raw:
            env_workers = _positive(env_raw)
            if env_workers is None:
                raise GradlePlanningError("DEVTOOL_MAX_WORKERS must be a positive integer")
            requested_workers = env_workers
    if raw_workers:
        raw_worker_value = _positive(next(iter(raw_workers)))
        if raw_worker_value is None:
            raise GradlePlanningError("Invalid raw Gradle --max-workers value")
        if requested_workers is not None and requested_workers != raw_worker_value:
            raise GradlePlanningError(
                f"Typed Gradle max_workers={requested_workers} conflicts with raw --max-workers={raw_worker_value}"
            )
        requested_workers = raw_worker_value
    base_workers = max(1, min(int(cpu_count or 1), requested_workers or int(cpu_count or 1)))
    if profile == "constrained": effective_workers = min(base_workers, 2)
    elif profile == "emergency": effective_workers = 1
    else: effective_workers = base_workers
    workers = PolicyValue(
        requested_workers or "auto", effective_workers,
        "raw-argv" if raw_workers else "typed-config" if requested_workers and env_workers is None else "environment:DEVTOOL_MAX_WORKERS" if env_workers is not None else "host-capability",
        "resource-profile clamp" if requested_workers and effective_workers != requested_workers else "",
    )

    daemon_default = profile != "emergency"
    vfs_default = profile != "emergency"
    parallel_default = profile == "balanced" and effective_workers > 1
    build_cache_default = True
    daemon = _typed_or_raw_bool(options, "daemon", raw, daemon_default)
    if "no_daemon" in options and options.get("no_daemon") not in (None, ""):
        no_daemon = _bool(options.get("no_daemon"), False)
        typed = not no_daemon
        if daemon.provenance == "raw-argv" and bool(daemon.effective) != typed:
            raise GradlePlanningError("Gradle no_daemon conflicts with raw daemon flag")
        daemon = PolicyValue(not no_daemon, typed, "typed-config:no_daemon")
    vfs_requested = _typed_or_raw_bool(options, "vfs", raw, vfs_default)
    vfs_compatible = capabilities.vfs_watch and context.cache_domain != "shared-android-storage"
    vfs_reason = ""
    vfs_effective = bool(vfs_requested.effective)
    if vfs_effective and not vfs_compatible:
        vfs_effective = False
        vfs_reason = (
            "gradle-user-home-on-shared-android-storage"
            if context.cache_domain == "shared-android-storage"
            else "gradle-version-not-vfs-watch-capable"
        )
    vfs = PolicyValue(
        vfs_requested.requested,
        vfs_effective,
        vfs_requested.provenance,
        vfs_reason or vfs_requested.reason,
    )
    vfs_qualification = VfsQualification.create(
        requested=vfs.requested,
        effective=bool(vfs.effective),
        compatible=vfs_compatible,
        context=context,
        fallback_reason=vfs_reason,
    )
    parallel = _typed_or_raw_bool(options, "parallel", raw, parallel_default)
    build_cache = _typed_or_raw_bool(options, "build_cache", raw, build_cache_default)
    if bool(build_cache.effective) and context.cache_domain == "shared-android-storage":
        build_cache = PolicyValue(
            build_cache.requested,
            False,
            build_cache.provenance,
            "shared-android-storage-not-canonical-cache-domain",
        )

    telemetry_raw = options.get("telemetry_level")
    if telemetry_raw in (None, ""):
        if "structured_events" in options:
            structured = _bool(options.get("structured_events"), False)
            telemetry = "standard" if structured else "summary"
            telemetry_provenance = "legacy-structured-events"
        else:
            # ENV-X03 default: bounded console/report telemetry is compatible
            # with Configuration Cache. Legacy init-script telemetry remains an
            # explicit standard/full choice until ENV-G2 real-process proof.
            telemetry = "summary"
            telemetry_provenance = "planner-default"
    else:
        telemetry = str(telemetry_raw).strip().lower()
        telemetry_provenance = "typed-config"
    if telemetry not in capabilities.telemetry_levels and telemetry != "off":
        raise GradlePlanningError(f"Unsupported Gradle telemetry_level: {telemetry}")
    # X03 implementation: standard uses the public BuildEvents shared-service
    # completion adapter and is structurally Configuration-Cache compatible.
    # Full retains legacy per-test/task detail and therefore intentionally
    # disables CC. ENV-G2 owns real-process miss/hit qualification.
    telemetry_cc_compatible = telemetry in {"summary", "standard", "off"}

    cc_requested = _normalize_cc(options.get("configuration_cache"))
    raw_cc = raw.get("configuration_cache", set())
    if raw_cc:
        raw_value = next(iter(raw_cc))
        if cc_requested not in {"auto", raw_value} and not (cc_requested == "required" and raw_value == "on"):
            raise GradlePlanningError(f"Typed configuration_cache={cc_requested} conflicts with raw --{'' if raw_value == 'on' else 'no-'}configuration-cache")
        cc_requested = "on" if raw_value == "on" else "off"
    cc_storage_compatible = context.cache_domain != "shared-android-storage"
    cc_build_logic_compatible = context.build_logic_complete
    cc_compatible = (
        capabilities.configuration_cache
        and telemetry_cc_compatible
        and cc_storage_compatible
        and cc_build_logic_compatible
    )
    fallback = ""
    if cc_requested == "off":
        cc_effective = "off"
    elif cc_compatible:
        cc_effective = "on"
    elif cc_requested == "required":
        if not cc_storage_compatible:
            raise GradlePlanningError(
                "Configuration Cache is required but shared Android storage is not a qualified canonical cache domain"
            )
        if not cc_build_logic_compatible:
            raise GradlePlanningError(
                "Configuration Cache is required but build-logic identity exceeded the bounded qualification set"
            )
        raise GradlePlanningError("Configuration Cache is required but the selected Gradle/telemetry context is not qualified")
    else:
        cc_effective = "off"
        if not telemetry_cc_compatible:
            fallback = "telemetry-level-not-configuration-cache-qualified"
        elif not cc_storage_compatible:
            fallback = "shared-android-storage-not-configuration-cache-qualified"
        elif not cc_build_logic_compatible:
            fallback = "build-logic-identity-incomplete"
        else:
            fallback = "gradle-version-not-configuration-cache-capable"
    cc_qualification = ConfigurationCacheQualification.create(
        requested=cc_requested, effective=cc_effective, compatible=cc_compatible,
        telemetry_level=telemetry, context=context,
        semantic_argument_digest=argument_classes.argument_digest,
        telemetry_adapter_identity=_telemetry_adapter_identity(telemetry),
        fallback_reason=fallback,
    )
    cc = PolicyValue(cc_requested, cc_effective, "typed/raw/planner", fallback)

    default_heap = (
        _positive(options.get("heap_default_mb"))
        or _positive(options.get("gradle_heap_mb"))
        or _positive(options.get("validation_heap_mb"))
        or max(768, min(2048, int((memory_mb or 4096) * 0.30)))
    )
    phase_min = _positive(options.get("phase_minimum_heap_mb")) or _phase_floor(validation_phase)
    user_heap = _positive(options.get("explicit_heap_request_mb") or options.get("heap_mb"))
    ceiling = _positive(options.get("host_safe_heap_ceiling_mb")) or _safe_heap_ceiling(memory_mb, profile)
    # Explicit invocation intent may deliberately choose a heap below the
    # workload recommendation. Preserve that request (subject only to the real
    # host-safe ceiling) and record the departure instead of silently raising it.
    desired = user_heap if user_heap is not None else max(default_heap, phase_min)
    effective_heap = min(desired, ceiling)
    if user_heap is not None and user_heap < phase_min:
        reason = f"explicit request {user_heap}MiB overrides phase recommendation {phase_min}MiB"
    elif effective_heap < phase_min:
        reason = f"host-safe ceiling {ceiling}MiB is below phase minimum {phase_min}MiB"
    elif user_heap and effective_heap < user_heap:
        reason = f"user request {user_heap}MiB clamped by host-safe ceiling {ceiling}MiB"
    elif effective_heap > default_heap:
        reason = f"raised from default {default_heap}MiB to phase/user requirement"
    else:
        reason = "default"
    heap = GradleHeapPolicy(default_heap, phase_min, user_heap, ceiling, effective_heap, reason)

    raw_failure = raw.get("failure_policy", set())
    if options.get("failure_policy") not in (None, ""):
        failure = str(options.get("failure_policy")).strip().lower()
        failure_provenance = "typed-config"
    elif options.get("fail_fast") is not None:
        failure = "fail-fast" if _bool(options.get("fail_fast"), False) else "collect"
        failure_provenance = "typed-config:fail_fast"
    elif raw_failure:
        failure = next(iter(raw_failure))
        failure_provenance = "raw-argv"
    elif validation or str(action or "").strip().lower() in {"gate", "release", "publish", "sign"}:
        failure = "collect"
        failure_provenance = "planner-intent"
    else:
        failure = "fail-fast"
        failure_provenance = "planner-intent"
    if failure not in {"collect", "fail-fast"}:
        raise GradlePlanningError(f"Unsupported Gradle failure_policy: {failure!r}")
    if raw_failure and next(iter(raw_failure)) != failure:
        raise GradlePlanningError(
            f"Typed Gradle failure_policy={failure} conflicts with raw --continue"
        )
    policy_payload = {
        "profile": profile, "profile_provenance": profile_provenance,
        "profile_reason": profile_reason, "profile_evidence": profile_evidence,
        "cc": cc.as_dict(), "cc_qualification": cc_qualification.as_dict(),
        "build_cache": build_cache.as_dict(), "daemon": daemon.as_dict(),
        "vfs": vfs.as_dict(), "vfs_qualification": vfs_qualification.as_dict(),
        "parallel": parallel.as_dict(), "workers": workers.as_dict(), "heap": heap.as_dict(),
        "telemetry": {"level": telemetry, "provenance": telemetry_provenance},
        "failure": {"effective": failure, "provenance": failure_provenance},
    }
    return GradleExecutionPolicy(
        resource_profile=profile, resource_profile_provenance=profile_provenance,
        resource_profile_reason=profile_reason, resource_profile_evidence=profile_evidence,
        configuration_cache=cc, configuration_cache_qualification=cc_qualification,
        build_cache=build_cache, daemon=daemon, vfs_watch=vfs, vfs_qualification=vfs_qualification,
        parallel=parallel, max_workers=workers,
        heap=heap, telemetry_level=PolicyValue(telemetry, telemetry, telemetry_provenance),
        failure_policy=PolicyValue(options.get("failure_policy", "auto"), failure, failure_provenance),
        policy_digest=_hash_payload(policy_payload),
    )


def _aggregate_entries(options: Mapping[str, object]) -> list[tuple[str, Mapping[str, object]]]:
    raw = options.get("task_aliases") or options.get("task_coalescing") or {}
    if isinstance(raw, Mapping):
        return [(str(name), value) for name, value in raw.items() if isinstance(value, Mapping)]
    if isinstance(raw, (list, tuple)):
        return [(str(i), value) for i, value in enumerate(raw, 1) if isinstance(value, Mapping)]
    return []


def _apply_qualified_aggregates(
    roots: tuple[str, ...], options: Mapping[str, object], context: GradleExecutionContext,
    semantic_request_digest: str,
) -> tuple[tuple[str, ...], tuple[AggregateQualification, ...], tuple[str, ...]]:
    optimized = list(roots)
    applied: list[AggregateQualification] = []
    rejected: list[str] = []
    for name, raw in _aggregate_entries(options):
        phases_raw = raw.get("phases") or raw.get("phase") or ()
        phases = _ordered(phases_raw if isinstance(phases_raw, (list, tuple, set)) else str(phases_raw).replace(",", " ").split())
        if phases:
            phase = str(options.get("_validation_phase") or "validation").strip().lower().replace("_", "-")
            normalized = {item.lower().replace("_", "-") for item in phases}
            if "*" not in normalized and phase not in normalized and not (phase == "validation" and "coalesced" in normalized):
                continue
        requested = _ordered(raw.get("tasks") or raw.get("roots") or raw.get("sources") or ())
        replacement = str(raw.get("replacement") or raw.get("task") or raw.get("alias") or "").strip()
        if not replacement or len(requested) < 2:
            continue
        current = set(optimized)
        if not set(requested).issubset(current):
            continue
        qualification = AggregateQualification.from_mapping(name, raw, context, semantic_request_digest)
        if qualification is None:
            rejected.append(name)
            continue
        first = min(optimized.index(task) for task in requested)
        next_roots = [task for task in optimized if task not in set(requested)]
        if replacement not in next_roots:
            next_roots.insert(min(first, len(next_roots)), replacement)
        optimized = next_roots
        applied.append(qualification)
    return tuple(optimized), tuple(applied), tuple(rejected)


def _managed_args(policy: GradleExecutionPolicy) -> tuple[str, ...]:
    args: list[str] = []
    args.append("--configuration-cache" if policy.configuration_cache.effective == "on" else "--no-configuration-cache")
    args.append("--build-cache" if policy.build_cache.effective else "--no-build-cache")
    args.append("--daemon" if policy.daemon.effective else "--no-daemon")
    args.append("--parallel" if policy.parallel.effective else "--no-parallel")
    args.append(f"--max-workers={int(policy.max_workers.effective)}")
    args.append(f"-Dorg.gradle.vfs.watch={'true' if policy.vfs_watch.effective else 'false'}")
    if policy.failure_policy.effective == "collect":
        args.append("--continue")
    # Keep heap policy explicit for direct Gradle execution. Android's staged
    # builder consumes the same value via --gradle-jvm-heap-mb.
    args.append(f"-Dorg.gradle.jvmargs=-Xmx{int(policy.heap.effective_mb)}m")
    return tuple(args)


def _strip_managed_raw_args(args: Sequence[str]) -> tuple[str, ...]:
    managed = {
        "--configuration-cache", "--no-configuration-cache", "--build-cache", "--no-build-cache",
        "--daemon", "--no-daemon", "--parallel", "--no-parallel", "--continue",
    }
    return tuple(
        arg for arg in args
        if arg not in managed and not arg.startswith("--max-workers=")
        and not arg.startswith("-Dorg.gradle.vfs.watch=")
        and not arg.startswith("-Dorg.gradle.parallel=")
        and not arg.startswith("-Dorg.gradle.caching=")
        and not arg.startswith("-Dorg.gradle.configuration-cache=")
        and not arg.startswith("-Dorg.gradle.daemon=")
        and not arg.startswith("-Dorg.gradle.workers.max=")
        and not arg.startswith("-Dorg.gradle.jvmargs=")
    )


def plan_gradle_invocation(
    *, config: ProjectConfig, request: GradleInvocationRequest, launcher: GradleLauncherIdentity,
    java_requirement: str = "", jdk_home: str = "", gradle_user_home: str = "",
    cpu_count: int | None = None, memory_mb: int | None = None,
) -> GradleInvocationPlan:
    options_raw = config.runner_options.get("gradle", {})
    options: dict[str, object] = dict(options_raw) if isinstance(options_raw, Mapping) else {}
    if request.validation:
        validation_raw = config.runner_options.get("validation", {})
        validation = dict(validation_raw) if isinstance(validation_raw, Mapping) else {}
        # Validation may request policy, but validation mode itself is not a
        # semantic reason to disable Gradle reuse layers. Only explicit values
        # are merged here.
        for key in (
            "resource_profile", "max_workers", "gradle_heap_mb", "heap_mb",
            "host_safe_heap_ceiling_mb", "configuration_cache", "build_cache",
            "daemon", "no_daemon", "vfs", "parallel", "telemetry_level",
            "structured_events", "failure_policy", "fail_fast",
        ):
            if key in validation and validation.get(key) not in (None, ""):
                options[key] = validation.get(key)
        profiles = validation.get("phase_profiles")
        if isinstance(profiles, Mapping) and request.validation_phase:
            key = request.validation_phase.strip().lower().replace("_", "-")
            phase = profiles.get(key) or profiles.get(key.replace("-", "_"))
            if isinstance(phase, Mapping):
                for name, value in phase.items():
                    if value in (None, ""):
                        continue
                    # A phase profile describes the workload floor/default for
                    # that phase. It is not an explicit operator request and
                    # must remain separately visible from CLI intent.
                    if str(name) in {"gradle_heap_mb", "heap_mb"}:
                        options["phase_minimum_heap_mb"] = value
                    else:
                        options[str(name)] = value
        cli_overrides = validation.get("_cli_resource_overrides")
        if isinstance(cli_overrides, Mapping):
            for key, record in cli_overrides.items():
                if isinstance(record, Mapping) and record.get("value") not in (None, ""):
                    if str(key) in {"gradle_heap_mb", "heap_mb"}:
                        options["explicit_heap_request_mb"] = record.get("value")
                    else:
                        options[str(key)] = record.get("value")
    termux = config.runner_options.get("termux", {})
    if isinstance(termux, Mapping) and options.get("resource_profile") in (None, "") and termux.get("resource_profile"):
        options["resource_profile"] = termux.get("resource_profile")
        options["_resource_profile_provenance"] = "termux-resource-policy"
    env_fail_fast = os.environ.get("DEVTOOL_GRADLE_FAIL_FAST", "").strip()
    if env_fail_fast and options.get("failure_policy") in (None, "") and options.get("fail_fast") in (None, ""):
        options["fail_fast"] = env_fail_fast
    context_env_keys_raw = options.get("context_env_keys") or options.get("semantic_env_keys") or ()
    if isinstance(context_env_keys_raw, str):
        context_env_keys = tuple(part for part in context_env_keys_raw.replace(",", " ").split() if part)
    elif isinstance(context_env_keys_raw, (list, tuple, set)):
        context_env_keys = tuple(str(part) for part in context_env_keys_raw if str(part).strip())
    else:
        context_env_keys = ()
    context = GradleExecutionContext.create(
        project_root=Path(request.project_root), launcher=launcher, jdk_home=jdk_home,
        java_requirement=java_requirement, gradle_user_home=gradle_user_home,
        substrate=("" if str(config.execution_environment or "auto").strip().lower() == "auto" else str(config.execution_environment or "")),
        semantic_environment=os.environ,
        semantic_environment_keys=context_env_keys,
        trusted_context_inputs=request.trusted_context_inputs,
    )
    capabilities = GradleCapabilitySet.for_version(launcher.effective_version or launcher.required_version)
    if memory_mb is None:
        try:
            from .build_optimization import memory_info_mb
            memory_mb = memory_info_mb()[0]
        except Exception:
            memory_mb = None
    if cpu_count is None:
        cpu_count = os.cpu_count() or 1
    if request.raw_args:
        raw_args = tuple(request.raw_args)
    elif isinstance(options.get("args"), str):
        import shlex
        raw_args = tuple(shlex.split(str(options.get("args"))))
    else:
        raw_args = tuple(str(x) for x in (options.get("args") or ()) if str(x).strip())
    effective_request = request if tuple(request.raw_args) == tuple(raw_args) else replace(request, raw_args=tuple(raw_args))
    argument_classes = classify_gradle_arguments(raw_args)
    if argument_classes.protected:
        raise GradlePlanningError(
            "Raw Gradle arguments may not override protected Devtool context/JVM policy: "
            + ", ".join(argument_classes.protected)
        )
    if raw_args:
        options = dict(options)
        options["args"] = list(raw_args)
    policy = resolve_gradle_policy(
        options=options, context=context, capabilities=capabilities,
        action=request.action, validation=request.validation,
        validation_phase=request.validation_phase, cpu_count=cpu_count, memory_mb=memory_mb,
    )
    requested = _ordered(request.requested_roots)
    planning_roots = _ordered(request.preoptimized_roots) or requested
    aggregate_options = dict(options)
    aggregate_options["_validation_phase"] = request.validation_phase
    semantic_request_digest = _hash_payload({
        "selection_identity": effective_request.selection_identity,
        "argument_digest": argument_classes.argument_digest,
        "trusted_context_inputs": list(effective_request.trusted_context_inputs),
    })
    optimized, qualifications, rejected = _apply_qualified_aggregates(
        planning_roots, aggregate_options, context, semantic_request_digest,
    )
    clean = argument_classes.passthrough
    rendered = (*clean, *_managed_args(policy))

    base_barriers = list(request.barriers)
    invocations: list[GradlePhysicalInvocation] = []
    normal_roots: list[str] = []

    def flush_normal_roots() -> None:
        if not normal_roots:
            return
        invocations.append(GradlePhysicalInvocation(
            roots=tuple(normal_roots), args=tuple(rendered), barriers=tuple(base_barriers),
        ))
        normal_roots.clear()

    # Preserve semantic root ordering while still coalescing each contiguous
    # barrier-free region into the minimum number of Gradle processes.  In
    # particular, ``clean build`` must never be rewritten as ``build`` then
    # ``clean`` merely because clean is isolated as a destructive barrier.
    for root in optimized:
        leaf = root.lstrip(":").rsplit(":", 1)[-1].lower()
        barrier: GradleInvocationBarrier | None = None
        if leaf == "clean":
            barrier = GradleInvocationBarrier("clean", "explicit destructive Gradle clean intent")
        elif leaf == "release" or leaf.startswith(("publish", "upload", "deploy", "sign")):
            barrier = GradleInvocationBarrier("effect", f"effectful Gradle root {root}")
        elif "connected" in leaf or "device" in leaf or "instrumentation" in leaf:
            barrier = GradleInvocationBarrier("device", f"device-bound Gradle root {root}")

        if barrier is None:
            normal_roots.append(root)
            continue
        flush_normal_roots()
        invocations.append(GradlePhysicalInvocation(
            roots=(root,), args=tuple(rendered), barriers=tuple((*base_barriers, barrier)),
        ))
    flush_normal_roots()
    if request.action in {"publish", "release", "sign"} and invocations:
        invocations = [
            GradlePhysicalInvocation(
                roots=item.roots, args=item.args,
                barriers=tuple((*item.barriers, GradleInvocationBarrier("effect", f"{request.action} carries external/release side effects"))),
            )
            for item in invocations
        ]
    payload = {
        "request": effective_request.as_dict(), "context": context.as_dict(), "policy": policy.as_dict(),
        "optimized_roots": list(optimized), "aggregates": [q.as_dict() for q in qualifications],
        "rejected": list(rejected), "arguments": argument_classes.as_dict(),
        "semantic_request_digest": semantic_request_digest,
        "invocations": [item.as_dict() for item in invocations],
    }
    return GradleInvocationPlan(
        request=effective_request, context=context, capabilities=capabilities, policy=policy,
        requested_roots=requested, optimized_roots=optimized,
        aggregate_qualifications=qualifications, rejected_aggregates=rejected,
        arguments=argument_classes, semantic_request_digest=semantic_request_digest,
        invocations=tuple(invocations), plan_digest=_hash_payload(payload),
    )


def plan_metadata(plan: GradleInvocationPlan) -> dict[str, object]:
    return {"gradle_execution_plan": plan.as_dict(), "gradle_plan_digest": plan.plan_digest, "gradle_context_digest": plan.context.context_digest, "gradle_policy_digest": plan.policy.policy_digest}
