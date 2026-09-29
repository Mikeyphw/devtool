from __future__ import annotations

"""Reusable execution result qualification (EXEC-X02).

This module is deliberately side-effect-light and history-derived.  EXO remains
execution/history authority; RER qualification here only derives immutable
fingerprints, candidate indexes, and explainable dispositions from EXO-owned
metadata/history.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import hashlib
import json
import os
import platform
import subprocess


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    return value


def stable_digest(payload: Mapping[str, Any] | Sequence[Any] | str) -> str:
    encoded = json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InputProvenance(str, Enum):
    EXPLICIT = "explicit"
    RUNNER_INFERRED = "runner-inferred"
    CONSERVATIVE_FALLBACK = "conservative-fallback"


class PortabilityClass(str, Enum):
    PORTABLE = "portable"
    SAME_ENVIRONMENT = "same-environment"
    SAME_PROVIDER = "same-provider"
    SAME_HOST = "same-host"
    SAME_DEVICE = "same-device"
    NON_REUSABLE = "non-reusable"


@dataclass(frozen=True, slots=True)
class ContentIdentity:
    path: str
    kind: str
    digest: str
    exists: bool = True
    algorithm: str = "sha256-merkle-v1"
    git_object: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "digest": self.digest,
            "exists": self.exists,
            "algorithm": self.algorithm,
            "git_object": self.git_object,
        }


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_object(path: Path, repo_root: Path | None) -> str:
    if repo_root is None:
        return ""
    try:
        rel = path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "hash-object", "--", str(rel)],
            check=False, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def content_identity(path: Path, *, base: Path | None = None, repo_root: Path | None = None) -> ContentIdentity:
    resolved = path.expanduser().resolve(strict=False)
    display = str(resolved)
    if base is not None:
        try:
            display = resolved.relative_to(base.resolve(strict=False)).as_posix()
        except ValueError:
            pass
    if not resolved.exists():
        return ContentIdentity(display, "missing", stable_digest({"missing": display}), exists=False)
    if resolved.is_symlink():
        target = os.readlink(resolved)
        return ContentIdentity(display, "symlink", stable_digest({"symlink": target}), git_object=_git_object(resolved, repo_root))
    if resolved.is_file():
        return ContentIdentity(display, "file", _hash_file(resolved), git_object=_git_object(resolved, repo_root))
    if resolved.is_dir():
        rows: list[dict[str, str]] = []
        for child in sorted((p for p in resolved.rglob("*") if not p.is_dir() and ".git" not in p.relative_to(resolved).parts), key=lambda p: p.relative_to(resolved).as_posix()):
            rel = child.relative_to(resolved).as_posix()
            if child.is_symlink():
                rows.append({"path": rel, "kind": "symlink", "digest": stable_digest({"symlink": os.readlink(child)})})
            elif child.is_file():
                rows.append({"path": rel, "kind": "file", "digest": _hash_file(child)})
        return ContentIdentity(display, "directory", stable_digest(rows))
    return ContentIdentity(display, "other", stable_digest({"path": display, "stat": str(resolved.stat().st_mode)}))


def conservative_repository_identity(root: Path) -> ContentIdentity:
    """Content identity for unknown input ownership without timestamp trust.

    Prefer Git's content-addressed HEAD plus actual worktree/index diff and
    untracked file content.  Fall back to a Merkle walk when Git is unavailable.
    """
    root = root.expanduser().resolve(strict=False)
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=False, capture_output=True, text=True, timeout=5)
        diff = subprocess.run(["git", "-C", str(root), "diff", "--binary", "HEAD", "--", "."], check=False, capture_output=True, timeout=15)
        untracked = subprocess.run(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"], check=False, capture_output=True, timeout=5)
        if head.returncode == 0 and diff.returncode == 0 and untracked.returncode == 0:
            rows: list[dict[str, str]] = []
            for raw in untracked.stdout.decode("utf-8", errors="surrogateescape").split("\0"):
                if not raw:
                    continue
                path = root / raw
                if path.is_file():
                    rows.append({"path": raw, "digest": _hash_file(path)})
            digest = stable_digest({
                "git_head": head.stdout.strip(),
                "git_diff_sha256": hashlib.sha256(diff.stdout).hexdigest(),
                "untracked": rows,
            })
            return ContentIdentity(".", "repository", digest, algorithm="git-content-v1", git_object=head.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    ident = content_identity(root, base=root, repo_root=root)
    return ContentIdentity(".", "repository", ident.digest, exists=ident.exists, algorithm=ident.algorithm)


@dataclass(frozen=True, slots=True)
class InputManifest:
    entries: tuple[ContentIdentity, ...]
    provenance: InputProvenance
    root: str
    complete: bool = True
    reason: str = ""

    @property
    def digest(self) -> str:
        return stable_digest({
            "entries": [v.to_dict() for v in self.entries],
            "provenance": self.provenance.value,
            "root": self.root,
            "complete": self.complete,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [v.to_dict() for v in self.entries],
            "provenance": self.provenance.value,
            "root": self.root,
            "complete": self.complete,
            "reason": self.reason,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class DependencyFingerprint:
    results: tuple[tuple[str, str], ...] = ()
    complete: bool = True

    @property
    def digest(self) -> str:
        return stable_digest({"results": list(self.results), "complete": self.complete})

    def to_dict(self) -> dict[str, Any]:
        return {"results": [list(v) for v in self.results], "complete": self.complete, "digest": self.digest}


@dataclass(frozen=True, slots=True)
class ExecutionEnvironmentFingerprint:
    domains: Mapping[str, str] = field(default_factory=dict)
    execution_environment: str = ""
    provider_identity: str = ""
    device_identity: str = ""
    host_identity: str = ""
    portability: PortabilityClass = PortabilityClass.SAME_ENVIRONMENT
    secret_identities: tuple[str, ...] = ()
    reusable: bool = True
    reason: str = ""

    @property
    def digest(self) -> str:
        payload: dict[str, Any] = {
            "domains": dict(sorted((str(k), str(v)) for k, v in self.domains.items())),
            "portability": self.portability.value,
            "secret_identities": list(self.secret_identities),
            "reusable": self.reusable,
        }
        if self.portability is PortabilityClass.SAME_ENVIRONMENT:
            payload["execution_environment"] = self.execution_environment
        elif self.portability is PortabilityClass.SAME_PROVIDER:
            payload["provider_identity"] = self.provider_identity
        elif self.portability is PortabilityClass.SAME_HOST:
            payload["host_identity"] = self.host_identity
        elif self.portability is PortabilityClass.SAME_DEVICE:
            payload["device_identity"] = self.device_identity
            payload["provider_identity"] = self.provider_identity
        elif self.portability is PortabilityClass.NON_REUSABLE:
            payload["non_reusable"] = True
        # PORTABLE deliberately carries no destination/host axis, while still
        # retaining semantic toolchain/environment domains above.
        return stable_digest(payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domains": dict(self.domains),
            "execution_environment": self.execution_environment,
            "provider_identity": self.provider_identity,
            "device_identity": self.device_identity,
            "host_identity": self.host_identity,
            "portability": self.portability.value,
            "secret_identities": list(self.secret_identities),
            "reusable": self.reusable,
            "reason": self.reason,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class OutputManifest:
    entries: tuple[ContentIdentity, ...] = ()
    evidence: tuple[ContentIdentity, ...] = ()
    materialization_refs: tuple[str, ...] = ()
    complete: bool = True

    @property
    def digest(self) -> str:
        return stable_digest({
            "entries": [v.to_dict() for v in self.entries],
            "evidence": [v.to_dict() for v in self.evidence],
            "materialization_refs": list(self.materialization_refs),
            "complete": self.complete,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [v.to_dict() for v in self.entries],
            "evidence": [v.to_dict() for v in self.evidence],
            "materialization_refs": list(self.materialization_refs),
            "complete": self.complete,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class RunnerReuseContribution:
    input_manifest: InputManifest
    environment: ExecutionEnvironmentFingerprint
    declared_outputs: tuple[str, ...] = ()
    materialization_refs: tuple[str, ...] = ()
    reusable: bool = True
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_manifest": self.input_manifest.to_dict(),
            "environment": self.environment.to_dict(),
            "declared_outputs": list(self.declared_outputs),
            "materialization_refs": list(self.materialization_refs),
            "reusable": self.reusable,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ReuseFingerprint:
    node_definition_id: str
    invocation_digest: str
    input_manifest_digest: str
    dependency_digest: str
    environment_digest: str
    reusable: bool = True
    reason: str = ""

    @property
    def digest(self) -> str:
        return stable_digest({
            "node_definition_id": self.node_definition_id,
            "invocation_digest": self.invocation_digest,
            "input_manifest_digest": self.input_manifest_digest,
            "dependency_digest": self.dependency_digest,
            "environment_digest": self.environment_digest,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_definition_id": self.node_definition_id,
            "invocation_digest": self.invocation_digest,
            "input_manifest_digest": self.input_manifest_digest,
            "dependency_digest": self.dependency_digest,
            "environment_digest": self.environment_digest,
            "reusable": self.reusable,
            "reason": self.reason,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class FingerprintDiff:
    changed: tuple[str, ...]
    before: str = ""
    after: str = ""

    @property
    def equal(self) -> bool:
        return not self.changed

    def to_dict(self) -> dict[str, Any]:
        return {"equal": self.equal, "changed": list(self.changed), "before": self.before, "after": self.after}


def diff_fingerprints(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> FingerprintDiff:
    left = dict(before or {})
    right = dict(after or {})
    semantic = ("node_definition_id", "invocation_digest", "input_manifest_digest", "dependency_digest", "environment_digest")
    changed = tuple(key for key in semantic if str(left.get(key) or "") != str(right.get(key) or ""))
    return FingerprintDiff(changed, str(left.get("digest") or ""), str(right.get("digest") or ""))


def _secret_like(key: str) -> bool:
    token = key.upper()
    return any(part in token for part in ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "PRIVATE_KEY", "CREDENTIAL"))


def _paths(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def _local_host_identity() -> str:
    return stable_digest({
        "node": platform.node(),
        "system": platform.system(),
        "machine": platform.machine(),
    })


def contribution_from_spec(spec: Any, *, repo_root: Path | None = None) -> RunnerReuseContribution:
    metadata = dict(getattr(spec, "metadata", {}) or {})
    cwd = Path(str(getattr(spec, "cwd", "") or (repo_root or Path.cwd()))).expanduser().resolve(strict=False)
    repository_root = (repo_root or cwd).expanduser().resolve(strict=False)
    explicit = _paths(metadata.get("workflow_inputs", ()) or metadata.get("reuse_inputs", ()) or ())
    inferred = _paths(metadata.get("runner_inputs", ()) or metadata.get("reuse_inferred_inputs", ()) or ())
    if explicit:
        values, provenance, complete, reason = explicit, InputProvenance.EXPLICIT, True, ""
    elif inferred:
        values, provenance, complete, reason = inferred, InputProvenance.RUNNER_INFERRED, True, ""
    else:
        # Unknown ownership is deliberately conservative: fingerprint the whole
        # execution root rather than guessing a small scope.
        values, provenance, complete, reason = (".",), InputProvenance.CONSERVATIVE_FALLBACK, True, "unknown-input-ownership; conservative-root-scope"
    if provenance is InputProvenance.CONSERVATIVE_FALLBACK and values == (".",):
        entries = (conservative_repository_identity(cwd),)
    else:
        entries = tuple(content_identity(cwd / value, base=cwd, repo_root=repository_root) for value in values)
    manifest = InputManifest(entries, provenance, str(cwd), complete=complete, reason=reason)

    domains = metadata.get("environment_domains")
    domains = dict(domains) if isinstance(domains, Mapping) else {}
    for key in ("environment_identity", "python_environment_identity", "go_environment_identity"):
        value = str(metadata.get(key) or "").strip()
        if value:
            domains.setdefault(key.removesuffix("_identity"), value)
    execution_environment = str(metadata.get("execution_environment") or metadata.get("workflow_execution_environment") or "").strip()
    provider = str(metadata.get("tool_provider_identity") or metadata.get("provider_identity") or metadata.get("execution_provider") or "").strip()
    device = str(metadata.get("device_serial") or metadata.get("workflow_device_serial") or "").strip()
    host_identity = str(metadata.get("host_identity_digest") or metadata.get("execution_host_identity") or "").strip()
    if not host_identity:
        host_identity = _local_host_identity()
    raw_portability = str(metadata.get("reuse_portability") or "").strip().lower()
    if raw_portability in {v.value for v in PortabilityClass}:
        portability = PortabilityClass(raw_portability)
    elif device:
        portability = PortabilityClass.SAME_DEVICE
    elif provider:
        portability = PortabilityClass.SAME_PROVIDER
    elif execution_environment:
        portability = PortabilityClass.SAME_ENVIRONMENT
    else:
        portability = PortabilityClass.SAME_HOST

    safe_secret_ids = _paths(metadata.get("reuse_secret_identities", ()) or ())
    raw_env = dict(getattr(spec, "env", {}) or {})
    secret_keys = tuple(sorted(k for k, v in raw_env.items() if v not in (None, "") and _secret_like(str(k))))
    reusable = True
    env_reason = ""
    if secret_keys and not safe_secret_ids:
        reusable = False
        env_reason = "secret-dependent-work-without-safe-secret-identity"
        portability = PortabilityClass.NON_REUSABLE
    if metadata.get("reuse_disabled") is True:
        reusable = False
        env_reason = str(metadata.get("reuse_disabled_reason") or "runner-marked-non-reusable")
        portability = PortabilityClass.NON_REUSABLE
    environment = ExecutionEnvironmentFingerprint(
        domains=domains,
        execution_environment=execution_environment,
        provider_identity=provider,
        device_identity=device,
        host_identity=host_identity,
        portability=portability,
        secret_identities=safe_secret_ids,
        reusable=reusable,
        reason=env_reason,
    )
    outputs = _paths(metadata.get("workflow_outputs", ()) or metadata.get("reuse_outputs", ()) or ())
    materialization = _paths(metadata.get("reuse_materialization_refs", ()) or ())
    return RunnerReuseContribution(manifest, environment, outputs, materialization, reusable=reusable, reason=env_reason)


def dependency_fingerprint_from_metadata(metadata: Mapping[str, Any]) -> DependencyFingerprint:
    raw = metadata.get("reuse_dependency_results") or {}
    if isinstance(raw, Mapping):
        rows = tuple(sorted((str(k), str(v)) for k, v in raw.items() if str(k) and str(v)))
        expected = tuple(str(v) for v in metadata.get("workflow_depends_on", ()) or ())
        complete = not expected or all(any(k == item for k, _ in rows) for item in expected)
        return DependencyFingerprint(rows, complete=complete)
    return DependencyFingerprint((), complete=not bool(metadata.get("workflow_depends_on")))


def fingerprint_for_spec(spec: Any, *, node_definition_id: str = "", repo_root: Path | None = None) -> tuple[ReuseFingerprint, RunnerReuseContribution, DependencyFingerprint]:
    from .execution_semantics import NodeDefinitionIdentity, ToolInvocation

    contribution = contribution_from_spec(spec, repo_root=repo_root)
    dependency = dependency_fingerprint_from_metadata(dict(getattr(spec, "metadata", {}) or {}))
    definition = node_definition_id or NodeDefinitionIdentity.from_spec(spec).value
    invocation = ToolInvocation.from_spec(spec)
    # Missing dependency result identities do not make the work inherently
    # non-reusable; they make pre-execution qualification incomplete. EXO folds
    # terminal upstream result identities into the final node record.
    reusable = contribution.reusable and contribution.input_manifest.complete
    reason = contribution.reason or ("dependency-result-identity-pending" if not dependency.complete else "")
    return ReuseFingerprint(
        node_definition_id=definition,
        invocation_digest=invocation.digest,
        input_manifest_digest=contribution.input_manifest.digest,
        dependency_digest=dependency.digest,
        environment_digest=contribution.environment.digest,
        reusable=reusable,
        reason=reason,
    ), contribution, dependency


def output_manifest_for_spec(spec: Any, *, evidence_refs: Iterable[str] = ()) -> OutputManifest:
    metadata = dict(getattr(spec, "metadata", {}) or {})
    cwd = Path(str(getattr(spec, "cwd", "") or Path.cwd())).expanduser().resolve(strict=False)
    outputs = _paths(metadata.get("workflow_outputs", ()) or metadata.get("reuse_outputs", ()) or ())
    entries = tuple(content_identity(cwd / value, base=cwd) for value in outputs)
    evidence: list[ContentIdentity] = []
    for value in evidence_refs:
        raw = str(value or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.exists() and path.is_file():
            evidence.append(content_identity(path))
    materialization = _paths(metadata.get("reuse_materialization_refs", ()) or ())
    complete = all(item.exists for item in entries)
    return OutputManifest(entries, tuple(evidence), materialization, complete=complete)


def verify_output_manifest(payload: Mapping[str, Any] | None, *, cwd: Path | None = None) -> tuple[bool, tuple[str, ...]]:
    data = dict(payload or {})
    failures: list[str] = []
    for row in data.get("entries", ()) or ():
        if not isinstance(row, Mapping):
            failures.append("malformed-output-entry")
            continue
        path = Path(str(row.get("path") or ""))
        if cwd is not None and not path.is_absolute():
            path = cwd / path
        expected = str(row.get("digest") or "")
        current = content_identity(path, base=cwd) if str(path) else None
        if current is None or not current.exists:
            failures.append(f"missing-output:{row.get('path')}")
        elif expected and current.digest != expected:
            failures.append(f"mutated-output:{row.get('path')}")
    for row in data.get("evidence", ()) or ():
        if not isinstance(row, Mapping):
            continue
        path = Path(str(row.get("path") or ""))
        expected = str(row.get("digest") or "")
        if not path.exists():
            failures.append(f"missing-evidence:{path}")
        elif expected and content_identity(path).digest != expected:
            failures.append(f"mutated-evidence:{path}")
    return not failures, tuple(failures)


def result_identity(*, fingerprint_digest: str, output_manifest_digest: str, execution_status: str, evidence_refs: Sequence[str] = ()) -> str:
    return stable_digest({
        "fingerprint": fingerprint_digest,
        "outputs": output_manifest_digest,
        "execution_status": execution_status,
        "evidence_refs": list(evidence_refs),
    })


@dataclass(frozen=True, slots=True)
class ReuseQualification:
    disposition: str
    reason: str
    source_run_id: str = ""
    source_node_id: str = ""
    proof_refs: tuple[str, ...] = ()
    diff: FingerprintDiff | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "reason": self.reason,
            "source_run_id": self.source_run_id,
            "source_node_id": self.source_node_id,
            "proof_refs": list(self.proof_refs),
            "diff": self.diff.to_dict() if self.diff else None,
        }


def verified_materialization_reference(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    import re
    if re.fullmatch(r"(?:artifact:)?sha256:[0-9a-fA-F]{64}", raw):
        return True
    if raw.startswith("file:") and "#sha256=" in raw:
        path_part, digest = raw[5:].rsplit("#sha256=", 1)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            return False
        path = Path(path_part).expanduser()
        return path.is_file() and _hash_file(path).lower() == digest.lower()
    return False


def _complete_fingerprint(value: Mapping[str, Any] | None) -> bool:
    if not isinstance(value, Mapping):
        return False
    required = (
        "node_definition_id", "invocation_digest", "input_manifest_digest",
        "dependency_digest", "environment_digest", "digest",
    )
    return all(bool(str(value.get(key) or "").strip()) for key in required)


def _complete_output_proof(value: Mapping[str, Any] | None) -> bool:
    return isinstance(value, Mapping) and bool(str(value.get("digest") or "").strip())


def qualify_candidate(current_fingerprint: Mapping[str, Any], candidate: Mapping[str, Any], *, cwd: Path | None = None) -> ReuseQualification:
    from .execution_semantics import ReuseDisposition

    if not isinstance(candidate, Mapping):
        return ReuseQualification(ReuseDisposition.RUN.value, "missing-or-corrupt-candidate")
    try:
        schema_version = int(candidate.get("schema_version") or 0)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version != 2:
        return ReuseQualification(ReuseDisposition.RUN.value, "unsupported-or-missing-reuse-schema")
    if not _complete_fingerprint(current_fingerprint):
        return ReuseQualification(ReuseDisposition.RUN.value, "current-fingerprint-incomplete")
    if not bool(candidate.get("qualification_complete")) or not bool(candidate.get("reusable")):
        return ReuseQualification(ReuseDisposition.RUN.value, "candidate-not-qualified")
    if str(candidate.get("execution_status") or "") != "succeeded":
        return ReuseQualification(ReuseDisposition.RUN.value, "candidate-not-successful")
    old_fp = candidate.get("fingerprint")
    if not _complete_fingerprint(old_fp if isinstance(old_fp, Mapping) else None):
        return ReuseQualification(ReuseDisposition.RUN.value, "candidate-missing-or-incomplete-fingerprint")
    if not str(candidate.get("source_run_id") or "") or not str(candidate.get("source_node_id") or "") or not str(candidate.get("result_identity") or ""):
        return ReuseQualification(ReuseDisposition.RUN.value, "candidate-incomplete-provenance")
    if not _complete_output_proof(candidate.get("output_manifest") if isinstance(candidate.get("output_manifest"), Mapping) else None):
        return ReuseQualification(ReuseDisposition.RUN.value, "candidate-incomplete-output-proof")
    diff = diff_fingerprints(old_fp, current_fingerprint)
    if not diff.equal:
        return ReuseQualification(
            ReuseDisposition.INVALIDATED.value,
            "semantic-fingerprint-changed:" + ",".join(diff.changed),
            str(candidate.get("source_run_id") or ""),
            str(candidate.get("source_node_id") or ""),
            tuple(str(v) for v in candidate.get("evidence_refs", ()) or ()),
            diff,
        )
    output = candidate.get("output_manifest")
    ok, failures = verify_output_manifest(output if isinstance(output, Mapping) else {}, cwd=cwd)
    if ok:
        return ReuseQualification(
            ReuseDisposition.REUSE.value, "qualified-evidence-and-outputs-intact",
            str(candidate.get("source_run_id") or ""), str(candidate.get("source_node_id") or ""),
            tuple(str(v) for v in candidate.get("evidence_refs", ()) or ()), diff,
        )
    materialization = tuple(str(v) for v in (output or {}).get("materialization_refs", ()) or ()) if isinstance(output, Mapping) else ()
    verified_materialization = tuple(v for v in materialization if verified_materialization_reference(v))
    if materialization and len(verified_materialization) == len(materialization):
        return ReuseQualification(
            ReuseDisposition.MATERIALIZE.value,
            "outputs-missing-or-mutated; verified-materialization-reference-required:" + ",".join(failures),
            str(candidate.get("source_run_id") or ""), str(candidate.get("source_node_id") or ""), verified_materialization, diff,
        )
    return ReuseQualification(
        ReuseDisposition.INVALIDATED.value,
        "required-output-or-evidence-invalid:" + ",".join(failures),
        str(candidate.get("source_run_id") or ""), str(candidate.get("source_node_id") or ""),
        tuple(str(v) for v in candidate.get("evidence_refs", ()) or ()), diff,
    )


@dataclass(slots=True)
class ReuseHistoryIndex:
    """Rebuildable candidate index derived only from terminal EXO history."""

    by_definition: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def add(self, record: Mapping[str, Any]) -> None:
        definition = str(record.get("node_definition_id") or "").strip()
        if not definition:
            return
        self.by_definition.setdefault(definition, []).append(dict(record))

    def candidates(self, node_definition_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self.by_definition.get(str(node_definition_id), ()))

    @classmethod
    def from_history_directory(cls, history_root: Path) -> "ReuseHistoryIndex":
        index = cls()
        if not history_root.exists():
            return index
        for path in sorted(history_root.glob("*/execution.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            execution = doc.get("execution") if isinstance(doc, Mapping) else None
            nodes = execution.get("nodes") if isinstance(execution, Mapping) else None
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, Mapping):
                    continue
                metadata = node.get("metadata")
                record = metadata.get("reusable_node_record") if isinstance(metadata, Mapping) else None
                if isinstance(record, Mapping):
                    index.add(record)
        return index


def annotate_spec_reuse(spec: Any, *, repo_root: Path | None = None, node_definition_id: str = "") -> None:
    """Attach runner contribution + semantic fingerprint to a CommandSpec in place."""
    metadata = dict(getattr(spec, "metadata", {}) or {})
    canonical_definition = str(node_definition_id or metadata.get("node_definition_id") or "").strip()
    fingerprint, contribution, dependency = fingerprint_for_spec(spec, node_definition_id=canonical_definition, repo_root=repo_root)
    metadata.setdefault("node_definition_id", fingerprint.node_definition_id)
    metadata["reuse_fingerprint"] = fingerprint.to_dict()
    metadata["reuse_contribution"] = contribution.to_dict()
    metadata["reuse_dependency_fingerprint"] = dependency.to_dict()
    metadata["reuse_shadow_qualification"] = {
        "mode": "qualified-observational",
        "predicted_disposition": "run",
        "reason": fingerprint.reason or "exec-x02-no-authoritative-skip-before-gate",
        "authoritative_skip": False,
    }
    spec.metadata = metadata


def finalize_spec_reuse_metadata(spec: Any, *, evidence_refs: Iterable[str] = (), execution_status: str = "succeeded") -> dict[str, Any]:
    """Build terminal proof metadata after a spec has actually executed."""
    metadata = dict(getattr(spec, "metadata", {}) or {})
    fp = metadata.get("reuse_fingerprint")
    if not isinstance(fp, Mapping):
        fingerprint, contribution, dependency = fingerprint_for_spec(spec)
        fp = fingerprint.to_dict()
        metadata["reuse_fingerprint"] = fp
        metadata["reuse_contribution"] = contribution.to_dict()
        metadata["reuse_dependency_fingerprint"] = dependency.to_dict()
    outputs = output_manifest_for_spec(spec, evidence_refs=evidence_refs)
    evidence = tuple(str(v) for v in evidence_refs if str(v))
    identity = result_identity(
        fingerprint_digest=str(fp.get("digest") or ""),
        output_manifest_digest=outputs.digest,
        execution_status=execution_status,
        evidence_refs=evidence,
    )
    return {
        "reuse_fingerprint": dict(fp),
        "reuse_output_manifest": outputs.to_dict(),
        "reuse_result_identity": identity,
        "reuse_evidence_refs": list(evidence),
        "reuse_portability_class": str((metadata.get("reuse_contribution") or {}).get("environment", {}).get("portability") or "") if isinstance(metadata.get("reuse_contribution"), Mapping) else "",
    }


def aggregate_fingerprints(node_definition_id: str, fingerprints: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate ordered command-part fingerprints into one workflow-node identity.

    A one-spec workflow node must retain the exact semantic fingerprint that was
    qualified before execution.  Re-hashing each component as a one-item list
    creates a different terminal identity and makes an unchanged fresh result
    immediately fail shadow re-qualification.  Multi-spec nodes still need an
    ordered aggregate because no individual command-part fingerprint represents
    the whole canonical workflow node.
    """
    rows = [dict(v) for v in fingerprints if isinstance(v, Mapping)]
    if len(rows) == 1:
        row = rows[0]
        payload = {
            "node_definition_id": str(node_definition_id or row.get("node_definition_id") or ""),
            "invocation_digest": str(row.get("invocation_digest") or ""),
            "input_manifest_digest": str(row.get("input_manifest_digest") or ""),
            "dependency_digest": str(row.get("dependency_digest") or ""),
            "environment_digest": str(row.get("environment_digest") or ""),
            "reusable": bool(row.get("reusable", True)),
            "reason": str(row.get("reason") or ""),
            "spec_digests": [str(row.get("digest") or "")],
        }
    else:
        payload = {
            "node_definition_id": str(node_definition_id),
            "invocation_digest": stable_digest([str(v.get("invocation_digest") or "") for v in rows]),
            "input_manifest_digest": stable_digest([str(v.get("input_manifest_digest") or "") for v in rows]),
            "dependency_digest": stable_digest([str(v.get("dependency_digest") or "") for v in rows]),
            "environment_digest": stable_digest([str(v.get("environment_digest") or "") for v in rows]),
            "reusable": bool(rows) and all(bool(v.get("reusable", True)) for v in rows),
            "reason": ";".join(dict.fromkeys(str(v.get("reason") or "") for v in rows if str(v.get("reason") or ""))),
            "spec_digests": [str(v.get("digest") or "") for v in rows],
        }
    payload["digest"] = stable_digest({k: payload[k] for k in ("node_definition_id", "invocation_digest", "input_manifest_digest", "dependency_digest", "environment_digest")})
    return payload


def qualify_from_history(
    index: ReuseHistoryIndex,
    current_fingerprint: Mapping[str, Any],
    *,
    cwd: Path | None = None,
) -> ReuseQualification:
    """Qualify newest compatible history candidate without mutating EXO truth."""
    definition = str(current_fingerprint.get("node_definition_id") or "")
    candidates = list(index.candidates(definition))
    if not candidates:
        return ReuseQualification("run", "no-history-candidate")
    # History file order is deterministic; inspect newest derived record first.
    fallback: ReuseQualification | None = None
    for candidate in reversed(candidates):
        result = qualify_candidate(current_fingerprint, candidate, cwd=cwd)
        if result.disposition in {"reuse", "materialize"}:
            return result
        if fallback is None:
            fallback = result
    return fallback or ReuseQualification("run", "no-qualified-history-candidate")


def aggregate_output_manifests(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    refs: list[str] = []
    complete = True
    for manifest in manifests:
        if not isinstance(manifest, Mapping):
            complete = False
            continue
        complete = complete and bool(manifest.get("complete", True))
        for row in manifest.get("entries", ()) or ():
            if isinstance(row, Mapping) and dict(row) not in entries:
                entries.append(dict(row))
        for row in manifest.get("evidence", ()) or ():
            if isinstance(row, Mapping) and dict(row) not in evidence:
                evidence.append(dict(row))
        for value in manifest.get("materialization_refs", ()) or ():
            token = str(value or "").strip()
            if token and token not in refs:
                refs.append(token)
    payload = {
        "entries": entries,
        "evidence": evidence,
        "materialization_refs": refs,
        "complete": complete,
    }
    payload["digest"] = stable_digest(payload)
    return payload
