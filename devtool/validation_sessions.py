from __future__ import annotations

"""Durable, resumable validation sessions.

The session engine is intentionally independent from argparse and process
execution.  It persists the validation plan, phase outcomes, repository input
fingerprints, pending apply transaction context, and Android resource guards.
CLI code can therefore execute one phase per process and safely continue after
Android kills Termux, the chroot, or Gradle.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from typing import Any, Iterable, Sequence

from .models import CommandSpec, Issue, OperationResult, ProjectConfig

SESSION_SCHEMA_VERSION = 1
DEFAULT_PHASE_ORDER = (
    "static",
    "preflight",
    "dependencies",
    "compile",
    "resources",
    "unit-test",
    "package",
    "artifact-check",
    "lint",
    "instrumentation",
    "finalize",
)
_TERMINAL_PHASE_STATES = {"succeeded", "skipped"}
_CODE_SUFFIXES = {
    ".java", ".kt", ".groovy", ".scala", ".c", ".cc", ".cpp", ".cxx",
    ".h", ".hh", ".hpp", ".rs", ".go", ".py", ".js", ".ts", ".tsx",
}
_IGNORED_PARTS = {
    ".git", ".gradle", ".idea", ".devtool", "build", "out", "dist",
    "node_modules", ".venv", "venv", "target", "bin", "obj",
}


class ValidationSessionError(RuntimeError):
    """Raised when a validation session cannot be safely resumed."""


@dataclass(slots=True)
class PhaseRecord:
    key: str
    name: str
    label: str
    spec_name: str
    index: int
    total: int
    status: str = "pending"
    attempts: int = 0
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    returncode: int | None = None
    output_tail: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    log_path: str = ""
    invalidated_reason: str = ""


@dataclass(slots=True)
class ValidationSession:
    session_id: str
    repo_root: str
    project_root: str
    project_type: str
    target: str
    variant: str
    status: str
    created_at: str
    updated_at: str
    phases: list[PhaseRecord]
    fingerprints: dict[str, str]
    git_head: str = ""
    transaction_path: str = ""
    apply_context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    paused_reason: str = ""
    invalidated_from: str = ""
    finalized_at: str = ""

    @property
    def complete(self) -> bool:
        return bool(self.phases) and all(item.status in _TERMINAL_PHASE_STATES for item in self.phases)

    @property
    def failed(self) -> bool:
        return any(item.status == "failed" for item in self.phases)

    def next_pending(self) -> PhaseRecord | None:
        return next((item for item in self.phases if item.status == "pending"), None)

    def phase_by_key(self, key: str) -> PhaseRecord | None:
        return next((item for item in self.phases if item.key == key), None)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema_version"] = SESSION_SCHEMA_VERSION
        payload["complete"] = self.complete
        payload["next_phase"] = self.next_pending().name if self.next_pending() else ""
        payload["completed_phases"] = sum(item.status in _TERMINAL_PHASE_STATES for item in self.phases)
        payload["phase_total"] = len(self.phases)
        return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def session_root(repo_root: Path) -> Path:
    return repo_root.expanduser().resolve() / ".devtool" / "state" / "android-validation"


def session_path(repo_root: Path, session_id: str) -> Path:
    return session_root(repo_root) / session_id / "state.json"


def active_session_marker(repo_root: Path) -> Path:
    return session_root(repo_root) / "active.json"


def validation_plan_path(repo_root: Path) -> Path:
    return session_root(repo_root) / "validation-plan.json"


def rollback_point_path(repo_root: Path) -> Path:
    return session_root(repo_root) / "rollback-point.json"


def normalize_phase_name(value: str | None) -> str:
    """Normalize CLI/manifest phase aliases to durable session names."""

    normalized = str(value or "").strip().lower().replace("_", "-")
    return "finalize" if normalized == "final" else normalized


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _phase_from_dict(payload: dict[str, Any]) -> PhaseRecord:
    allowed = {name for name in PhaseRecord.__dataclass_fields__}
    return PhaseRecord(**{key: value for key, value in payload.items() if key in allowed})


def _session_from_dict(payload: dict[str, Any]) -> ValidationSession:
    allowed = {name for name in ValidationSession.__dataclass_fields__}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["phases"] = [_phase_from_dict(item) for item in payload.get("phases", []) if isinstance(item, dict)]
    return ValidationSession(**values)


def save_session(session: ValidationSession, *, active: bool | None = None) -> Path:
    session.updated_at = _utc_now()
    path = session_path(Path(session.repo_root), session.session_id)
    payload = session.as_dict()
    _atomic_write_json(path, payload)
    plan_payload = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session.session_id,
        "status": session.status,
        "validation_mode": str(session.apply_context.get("validation_mode") or "standalone"),
        "artifact": session.apply_context.get("artifact") or session.apply_context.get("artifacts") or "",
        "resource_profile": session.options.get("validation_resource_profile") or "automatic",
        "transaction_path": session.transaction_path,
        "recovery_available": bool(session.transaction_path),
        "phases": [
            {"key": item.key, "name": item.name, "label": item.label, "status": item.status}
            for item in session.phases
        ],
        "updated_at": session.updated_at,
    }
    _atomic_write_json(validation_plan_path(Path(session.repo_root)), plan_payload)
    if active is True:
        _atomic_write_json(
            active_session_marker(Path(session.repo_root)),
            {
                "schema_version": SESSION_SCHEMA_VERSION,
                "session_id": session.session_id,
                "path": str(path),
                "status": session.status,
                "transaction_path": session.transaction_path,
                "updated_at": session.updated_at,
            },
        )
        if session.transaction_path:
            _atomic_write_json(
                rollback_point_path(Path(session.repo_root)),
                {
                    "schema_version": SESSION_SCHEMA_VERSION,
                    "session_id": session.session_id,
                    "transaction_path": session.transaction_path,
                    "status": session.status,
                    "updated_at": session.updated_at,
                },
            )
    elif active is False:
        marker = active_session_marker(Path(session.repo_root))
        try:
            current = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
        except Exception:
            current = {}
        if not current or str(current.get("session_id") or "") == session.session_id:
            marker.unlink(missing_ok=True)
            rollback_point_path(Path(session.repo_root)).unlink(missing_ok=True)
    return path


def load_session(repo_root: Path, session_id: str) -> ValidationSession:
    path = session_path(repo_root, session_id)
    if not path.is_file():
        raise ValidationSessionError(f"Validation session not found: {session_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationSessionError(f"Invalid validation session state: {path}: {exc}") from exc
    if int(payload.get("schema_version", 0) or 0) != SESSION_SCHEMA_VERSION:
        raise ValidationSessionError(f"Unsupported validation session schema in {path}")
    return _session_from_dict(payload)


def load_active_session(repo_root: Path, *, required: bool = False) -> ValidationSession | None:
    marker = active_session_marker(repo_root)
    if not marker.is_file():
        if required:
            raise ValidationSessionError("No active validation session exists for this repository.")
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        session_id = str(payload.get("session_id") or "").strip()
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationSessionError(f"Invalid active validation marker: {marker}: {exc}") from exc
    if not session_id:
        raise ValidationSessionError(f"Active validation marker has no session ID: {marker}")
    return load_session(repo_root, session_id)


def clear_active_session(session: ValidationSession) -> None:
    save_session(session, active=False)


def _git_head(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _candidate_files(repo_root: Path) -> Iterable[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            path = repo_root / raw.decode("utf-8", errors="surrogateescape")
            if path.is_file():
                yield path
        return
    for path in repo_root.rglob("*"):
        if path.is_file():
            yield path


def _relative_parts(repo_root: Path, path: Path) -> tuple[str, ...]:
    try:
        return tuple(path.resolve().relative_to(repo_root.resolve()).parts)
    except (OSError, ValueError):
        return tuple(path.parts)


def _fingerprint_category(repo_root: Path, path: Path) -> str:
    parts = _relative_parts(repo_root, path)
    lower_parts = tuple(item.lower() for item in parts)
    if any(part in _IGNORED_PARTS for part in lower_parts):
        return "ignored"
    name = path.name.lower()
    relative = "/".join(lower_parts)
    if (
        name in {"settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradle.properties", ".devtool.toml"}
        or name.endswith(".versions.toml")
        or relative.startswith("gradle/wrapper/")
        or "buildsrc" in lower_parts
        or "build-logic" in lower_parts
    ):
        return "config"
    if "src/test" in relative or "src/androidtest" in relative or any(part in {"test", "tests"} for part in lower_parts):
        return "tests"
    if name == "androidmanifest.xml" or "/res/" in f"/{relative}/" or "/assets/" in f"/{relative}/":
        return "resources"
    if path.suffix.lower() in _CODE_SUFFIXES:
        return "source"
    return "ignored"


def input_fingerprints(repo_root: Path) -> dict[str, str]:
    digests = {name: hashlib.sha256() for name in ("config", "source", "resources", "tests")}
    entries: dict[str, list[Path]] = {name: [] for name in digests}
    for path in _candidate_files(repo_root):
        category = _fingerprint_category(repo_root, path)
        if category in entries:
            entries[category].append(path)
    for category, paths in entries.items():
        digest = digests[category]
        for path in sorted(paths, key=lambda item: str(item)):
            try:
                relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
                stat = path.stat()
                digest.update(relative.encode("utf-8", errors="surrogateescape"))
                digest.update(str(stat.st_size).encode("ascii"))
                with path.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
            except OSError:
                continue
    return {name: digest.hexdigest() for name, digest in digests.items()}


def phase_name_for_spec(spec: CommandSpec) -> str:
    explicit = str(spec.metadata.get("validation_phase_name") or "").strip().lower()
    if explicit:
        return explicit
    components = [str(item).strip().lower() for item in spec.metadata.get("validation_components") or [] if str(item).strip()]
    mapping = {"restore": "preflight", "build": "package", "test": "unit-test", "lint": "lint", "instrumentation": "instrumentation"}
    for component in components:
        if component in mapping:
            return mapping[component]
    name = spec.name.lower()
    for token, phase in mapping.items():
        if token in name:
            return phase
    return "finalize" if "final" in name else name.replace("android-validation-", "")


def phase_key_for_spec(spec: CommandSpec, index: int) -> str:
    explicit = str(spec.metadata.get("validation_phase_key") or "").strip()
    if explicit:
        return explicit
    phase = phase_name_for_spec(spec)
    scope = str(
        spec.metadata.get("workspace")
        or spec.metadata.get("bundle_target")
        or spec.metadata.get("matrix")
        or spec.metadata.get("target")
        or "repository"
    ).strip()
    return f"{scope}:{phase}:{index}"


def annotate_specs(specs: Sequence[CommandSpec], *, session_id: str = "") -> list[CommandSpec]:
    total = len(specs)
    annotated: list[CommandSpec] = []
    for index, spec in enumerate(specs, start=1):
        metadata = dict(spec.metadata)
        phase_name = phase_name_for_spec(spec)
        metadata.update(
            {
                "validation_resumable": True,
                "validation_session_id": session_id,
                "validation_phase_name": phase_name,
                "validation_phase_key": phase_key_for_spec(spec, index),
                "validation_phase_index": index,
                "validation_phase_total": total,
            }
        )
        spec.metadata = metadata
        annotated.append(spec)
    return annotated


def phase_records(specs: Sequence[CommandSpec]) -> list[PhaseRecord]:
    records: list[PhaseRecord] = []
    total = len(specs)
    for index, spec in enumerate(specs, start=1):
        phase_name = phase_name_for_spec(spec)
        scope = str(spec.metadata.get("workspace") or spec.metadata.get("bundle_target") or spec.metadata.get("matrix") or spec.metadata.get("target") or "").strip()
        label = f"{scope}: {phase_name}" if scope else phase_name
        records.append(
            PhaseRecord(
                key=phase_key_for_spec(spec, index),
                name=phase_name,
                label=label,
                spec_name=spec.name,
                index=index,
                total=total,
            )
        )
    return records


def create_session(
    config: ProjectConfig,
    specs: Sequence[CommandSpec],
    *,
    transaction_path: str = "",
    apply_context: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    replace_active: bool = False,
) -> ValidationSession:
    existing = load_active_session(config.repo_root)
    if existing is not None and existing.status not in {"completed", "aborted"} and not replace_active:
        raise ValidationSessionError(
            f"Validation session {existing.session_id} is already active. Resume or abort it before starting another."
        )
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    annotated = annotate_specs(list(specs), session_id=session_id)
    session = ValidationSession(
        session_id=session_id,
        repo_root=str(config.repo_root.resolve()),
        project_root=str(Path(config.project_root or config.repo_root).expanduser().resolve()),
        project_type=config.kind,
        target=str(config.selected_target or ""),
        variant=str(config.variant or ""),
        status="created",
        created_at=_utc_now(),
        updated_at=_utc_now(),
        phases=phase_records(annotated),
        fingerprints=input_fingerprints(config.repo_root),
        git_head=_git_head(config.repo_root),
        transaction_path=str(transaction_path or ""),
        apply_context=dict(apply_context or {}),
        options=dict(options or {}),
    )
    # Deferred apply sessions keep a transaction/rollback point available.
    # Store this explicitly so checkpoint output and recovery UX do not need
    # to infer it from filesystem state later.
    if transaction_path:
        session.options.setdefault("recovery_available", True)
    elif session.options.get("rollback_point"):
        session.options.setdefault("recovery_available", True)
    save_session(session, active=True)
    return session


def sync_plan(session: ValidationSession, specs: Sequence[CommandSpec]) -> list[CommandSpec]:
    annotated = annotate_specs(list(specs), session_id=session.session_id)
    current = {item.key: item for item in session.phases}
    rebuilt: list[PhaseRecord] = []
    plan_changed = False
    for fresh in phase_records(annotated):
        previous = current.get(fresh.key)
        if previous is None:
            rebuilt.append(fresh)
            plan_changed = True
            continue
        previous.index = fresh.index
        previous.total = fresh.total
        previous.name = fresh.name
        previous.label = fresh.label
        previous.spec_name = fresh.spec_name
        rebuilt.append(previous)
    if set(current) != {item.key for item in rebuilt}:
        plan_changed = True
    session.phases = rebuilt
    if plan_changed:
        invalidate_from(session, "preflight", "validation plan changed")
    save_session(session, active=True)
    return annotated


def _phase_rank(name: str) -> int:
    try:
        return DEFAULT_PHASE_ORDER.index(normalize_phase_name(name))
    except ValueError:
        return len(DEFAULT_PHASE_ORDER)


def invalidate_from(session: ValidationSession, phase_name: str, reason: str) -> None:
    threshold = _phase_rank(phase_name)
    invalidated = False
    for item in session.phases:
        if _phase_rank(item.name) < threshold:
            continue
        if item.status != "pending" or item.returncode is not None:
            invalidated = True
        item.status = "pending"
        item.returncode = None
        item.started_at = ""
        item.finished_at = ""
        item.duration_seconds = 0.0
        item.output_tail = ""
        item.issues = []
        item.log_path = ""
        item.invalidated_reason = reason
    if invalidated:
        session.invalidated_from = phase_name
        session.status = "paused"
        session.paused_reason = reason


def reconcile_inputs(session: ValidationSession, config: ProjectConfig) -> str:
    repo = str(config.repo_root.resolve())
    project = str(Path(config.project_root or config.repo_root).expanduser().resolve())
    if repo != session.repo_root or project != session.project_root:
        raise ValidationSessionError("Validation session belongs to a different repository or project root.")
    if str(config.selected_target or "") != session.target or str(config.variant or "") != session.variant:
        raise ValidationSessionError("Validation session target or variant no longer matches the selected configuration.")
    current = input_fingerprints(config.repo_root)
    invalidation = ""
    if current.get("config") != session.fingerprints.get("config"):
        invalidation = "preflight"
    elif current.get("source") != session.fingerprints.get("source"):
        invalidation = "compile"
    elif current.get("resources") != session.fingerprints.get("resources"):
        invalidation = "resources"
    elif current.get("tests") != session.fingerprints.get("tests"):
        invalidation = "unit-test"
    if invalidation and any(item.name == "static" for item in session.phases):
        # Embedded artifact validators may inspect any repository surface.
        # Re-run them whenever downstream inputs change between checkpoints.
        invalidation = "static"
    if invalidation:
        invalidate_from(session, invalidation, f"{invalidation} inputs changed since the previous checkpoint")
    session.fingerprints = current
    session.git_head = _git_head(config.repo_root)
    save_session(session, active=True)
    return invalidation


def retry_failed(session: ValidationSession) -> str:
    """Reset the cached failed phase and all phases that depend on it."""

    failed = next((item for item in session.phases if item.status == "failed"), None)
    if failed is None:
        return ""
    invalidate_from(session, failed.name, "explicit retry of cached validation failure")
    session.last_error = ""
    save_session(session, active=True)
    return failed.name


def _matching_indices(session: ValidationSession, name: str) -> list[int]:
    normalized = normalize_phase_name(name)
    return [
        index
        for index, item in enumerate(session.phases)
        if normalize_phase_name(item.name) == normalized or item.key == name
    ]


def select_phase_keys(
    session: ValidationSession,
    *,
    phase: str = "",
    exact_phase: bool = False,
    through: str = "",
    from_phase: str = "",
    next_only: bool = False,
    complete: bool = False,
    max_phases: int = 0,
) -> list[str]:
    pending_indices = [index for index, item in enumerate(session.phases) if item.status == "pending"]
    if not pending_indices:
        return []
    selected: list[int]
    if phase:
        matches = [index for index in _matching_indices(session, phase) if session.phases[index].status == "pending"]
        if not matches:
            raise ValidationSessionError(f"No pending validation phase matches: {phase}")
        if exact_phase:
            selected = matches
        else:
            # Named phases include every still-pending prerequisite by default.
            end = matches[-1]
            selected = [index for index in pending_indices if index <= end]
    else:
        start = pending_indices[0]
        if from_phase:
            matches = _matching_indices(session, from_phase)
            if not matches:
                raise ValidationSessionError(f"Unknown validation phase: {from_phase}")
            start = matches[0]
        if complete:
            selected = [index for index in pending_indices if index >= start]
        elif through:
            matches = _matching_indices(session, through)
            if not matches:
                raise ValidationSessionError(f"Unknown validation phase: {through}")
            end = matches[-1]
            if end < start:
                raise ValidationSessionError(f"--through {through} precedes the current/from phase")
            selected = [index for index in pending_indices if start <= index <= end]
        else:
            selected = [start]
        if next_only:
            selected = selected[:1]
    if max_phases > 0:
        selected = selected[:max_phases]
    return [session.phases[index].key for index in selected]


def spec_map(specs: Sequence[CommandSpec]) -> dict[str, CommandSpec]:
    return {str(spec.metadata.get("validation_phase_key") or ""): spec for spec in specs}


def mark_phase_running(session: ValidationSession, key: str) -> PhaseRecord:
    phase = session.phase_by_key(key)
    if phase is None:
        raise ValidationSessionError(f"Validation phase is not part of session {session.session_id}: {key}")
    phase.status = "running"
    phase.attempts += 1
    phase.started_at = _utc_now()
    phase.finished_at = ""
    session.status = "running"
    session.paused_reason = ""
    save_session(session, active=True)
    return phase


def record_phase_result(session: ValidationSession, key: str, result: OperationResult) -> PhaseRecord:
    phase = session.phase_by_key(key)
    if phase is None:
        raise ValidationSessionError(f"Validation phase is not part of session {session.session_id}: {key}")
    phase.returncode = int(result.returncode)
    phase.duration_seconds += float(result.duration_seconds or 0.0)
    phase.finished_at = _utc_now()
    phase.output_tail = str(result.output or "")[-8000:]
    phase.log_path = str(result.log_path or "")
    phase.issues = [
        {
            "severity": issue.severity,
            "command": issue.command,
            "file": issue.file,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in result.issues
    ]
    if result.returncode == 0:
        phase.status = "skipped" if result.skipped else "succeeded"
        session.status = "completed" if session.complete else "paused"
        session.paused_reason = "" if session.complete else "phase boundary reached"
        session.last_error = ""
    else:
        phase.status = "failed"
        session.status = "failed"
        session.last_error = phase.output_tail or f"{phase.label} failed"
        session.paused_reason = "validation phase failed"
    session.fingerprints = input_fingerprints(Path(session.repo_root))
    session.git_head = _git_head(Path(session.repo_root))
    save_session(session, active=True)
    return phase


def mark_paused(session: ValidationSession, reason: str) -> None:
    if not session.complete and session.status != "failed":
        session.status = "paused"
    session.paused_reason = reason
    save_session(session, active=not session.complete)


def mark_completed(session: ValidationSession) -> None:
    session.status = "completed"
    session.paused_reason = ""
    session.last_error = ""
    session.finalized_at = _utc_now()
    save_session(session, active=False)


def mark_aborted(session: ValidationSession, reason: str) -> None:
    session.status = "aborted"
    session.paused_reason = reason
    session.finalized_at = _utc_now()
    save_session(session, active=False)


def session_operation_results(session: ValidationSession) -> list[OperationResult]:
    results: list[OperationResult] = []
    for item in session.phases:
        if item.returncode is None:
            continue
        issues = [
            Issue(
                str(issue.get("severity") or "error"),
                str(issue.get("command") or item.spec_name),
                str(issue.get("file") or ""),
                str(issue.get("code") or ""),
                str(issue.get("message") or ""),
            )
            for issue in item.issues
        ]
        results.append(
            OperationResult(
                item.spec_name,
                ["validation-session", item.name],
                int(item.returncode),
                float(item.duration_seconds),
                item.output_tail,
                issues,
                skipped=item.status == "skipped",
                log_path=item.log_path,
                metadata={
                    "validation_session_id": session.session_id,
                    "validation_phase_name": item.name,
                    "validation_phase_index": item.index,
                    "validation_phase_total": item.total,
                    "validation_phase_key": item.key,
                    "restored_from_session": True,
                },
            )
        )
    return results


def status_payload(session: ValidationSession) -> dict[str, Any]:
    payload = session.as_dict()
    payload["pending"] = [item.name for item in session.phases if item.status == "pending"]
    payload["failed_phases"] = [item.name for item in session.phases if item.status == "failed"]
    context = dict(session.apply_context or {})
    payload["validation_mode"] = str(context.get("validation_mode") or "standalone")
    payload["artifact"] = context.get("artifact") or context.get("artifacts") or ""
    payload["recovery_available"] = bool(session.transaction_path and Path(session.transaction_path).exists())
    payload["resource_profile"] = str(session.options.get("validation_resource_profile") or session.options.get("resource_profile") or "automatic")
    payload["minimum_phase"] = str(context.get("minimum_phase") or "finalize")
    payload["safe_checkpoint"] = not session.failed and session.status in {"created", "paused", "completed"}
    return payload


def status_text(session: ValidationSession) -> str:
    completed = sum(item.status in _TERMINAL_PHASE_STATES for item in session.phases)
    next_phase = session.next_pending()
    context = dict(session.apply_context or {})
    mode = str(context.get("validation_mode") or "standalone")
    artifact = context.get("artifact") or context.get("artifacts") or ""
    lines = [
        f"Validation session: {session.session_id}",
        f"Status: {session.status}",
        f"Mode: {mode}",
        f"Project: {session.project_root}",
        f"Target: {session.target or '-'}",
        f"Variant: {session.variant or '-'}",
        f"Resource profile: {session.options.get('validation_resource_profile') or session.options.get('resource_profile') or 'automatic'}",
        f"Progress: {completed}/{len(session.phases)} phases",
        f"Next: {next_phase.label if next_phase else '-'}",
    ]
    if artifact:
        lines.append(f"Artifact: {artifact}")
    if session.paused_reason:
        lines.append(f"Pause reason: {session.paused_reason}")
    if session.invalidated_from:
        lines.append(f"Invalidated from: {session.invalidated_from}")
    if session.transaction_path:
        lines.append(f"Recovery: available ({session.transaction_path})")
    lines.append("Phases:")
    for item in session.phases:
        marker = {"succeeded": "ok", "skipped": "skip", "failed": "FAIL", "running": "run"}.get(item.status, "wait")
        lines.append(f"  [{marker:4}] {item.index}/{item.total} {item.label}")
    return "\n".join(lines)


def checkpoint_text(session: ValidationSession) -> str:
    """Render a compact, decision-oriented validation checkpoint."""
    payload = status_payload(session)
    completed = int(payload.get("completed_phases") or 0)
    total = int(payload.get("phase_total") or 0)
    next_phase = str(payload.get("next_phase") or "-")
    artifact = payload.get("artifact") or "-"
    return "\n".join([
        "Validation checkpoint",
        f"Session: {session.session_id}",
        f"Artifact: {artifact}",
        f"Apply state: {'validation pending' if session.transaction_path else 'standalone'}",
        f"Validation: {session.status}",
        f"Completed: {completed}/{total}",
        f"Next: {next_phase}",
        f"Minimum phase: {payload.get('minimum_phase') or 'finalize'}",
        f"Resource profile: {payload.get('resource_profile') or 'automatic'}",
        f"Recovery available: {'yes' if payload.get('recovery_available') or session.options.get('recovery_available') else 'no'}",
        f"Safe checkpoint: {'yes' if payload.get('safe_checkpoint') else 'no'}",
    ])


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8", errors="replace").strip())
    except (OSError, ValueError):
        return None


def android_power_state() -> dict[str, Any]:
    battery = Path("/sys/class/power_supply/battery")
    capacity = _read_int(battery / "capacity")
    try:
        status = (battery / "status").read_text(encoding="utf-8", errors="replace").strip().lower()
    except OSError:
        status = ""
    charging = status in {"charging", "full"}
    temperatures: list[float] = []
    raw_battery_temp = _read_int(battery / "temp")
    if raw_battery_temp is not None:
        temperatures.append(raw_battery_temp / 10.0 if raw_battery_temp > 100 else float(raw_battery_temp))
    thermal_root = Path("/sys/class/thermal")
    if thermal_root.is_dir():
        for path in thermal_root.glob("thermal_zone*/temp"):
            raw = _read_int(path)
            if raw is None:
                continue
            value = raw / 1000.0 if raw > 1000 else raw / 10.0 if raw > 100 else float(raw)
            if 0 < value < 150:
                temperatures.append(value)
    return {
        "capacity": capacity,
        "status": status,
        "charging": charging,
        "temperature_c": max(temperatures) if temperatures else None,
    }


def guard_reason(*, stop_below_battery: int = 0, require_charging: bool = False, stop_above_temperature: float = 0.0) -> str:
    state = android_power_state()
    capacity = state.get("capacity")
    temperature = state.get("temperature_c")
    if stop_below_battery > 0 and capacity is not None and int(capacity) < stop_below_battery:
        return f"battery is {capacity}% (minimum {stop_below_battery}%)"
    if require_charging and capacity is not None and not bool(state.get("charging")):
        return f"device is not charging (battery {capacity}%)"
    if stop_above_temperature > 0 and temperature is not None and float(temperature) > stop_above_temperature:
        return f"device temperature is {temperature:.1f}°C (maximum {stop_above_temperature:.1f}°C)"
    return ""


def parse_duration_seconds(value: str | int | float | None) -> float:
    if value in (None, "", 0, 0.0):
        return 0.0
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value).strip().lower()
    multiplier = 1.0
    if text.endswith("ms"):
        multiplier = 0.001
        text = text[:-2]
    elif text.endswith("s"):
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 60.0
        text = text[:-1]
    elif text.endswith("h"):
        multiplier = 3600.0
        text = text[:-1]
    try:
        return max(0.0, float(text) * multiplier)
    except ValueError as exc:
        raise ValidationSessionError(f"Invalid duration: {value}") from exc


def pause_result(session: ValidationSession, reason: str, *, require_complete: bool = False) -> OperationResult:
    next_phase = session.next_pending()
    return OperationResult(
        "validation-paused",
        ["devtool", "resume-validation"],
        1 if require_complete else 0,
        0.0,
        (
            f"Validation session {session.session_id} paused.\n"
            f"reason: {reason}\n"
            f"completed: {sum(item.status in _TERMINAL_PHASE_STATES for item in session.phases)}/{len(session.phases)}\n"
            f"next_phase: {next_phase.name if next_phase else '-'}"
        ),
        [],
        skipped=not require_complete,
        failure_kind="validation_incomplete" if require_complete else "",
        metadata={
            "validation_session_id": session.session_id,
            "validation_session_status": "paused",
            "validation_complete": False,
            "completed_phase": next((item.name for item in reversed(session.phases) if item.status in _TERMINAL_PHASE_STATES), ""),
            "next_phase": next_phase.name if next_phase else "",
            "remaining_phases": sum(item.status == "pending" for item in session.phases),
            "completed_phases": sum(item.status in _TERMINAL_PHASE_STATES for item in session.phases),
            "phase_total": len(session.phases),
            "artifact": session.apply_context.get("artifact") or session.apply_context.get("artifacts") or "",
            "recovery_available": bool(session.transaction_path and Path(session.transaction_path).exists()),
            "resource_profile": session.options.get("validation_resource_profile") or "automatic",
        },
    )


def completion_result(session: ValidationSession) -> OperationResult:
    return OperationResult(
        "validation-session-complete",
        ["devtool", "validation-finalize", session.session_id],
        0,
        0.0,
        f"Validation session {session.session_id} completed all {len(session.phases)} phases.",
        [],
        metadata={
            "validation_session_id": session.session_id,
            "validation_session_status": "completed",
            "validation_complete": True,
            "remaining_phases": 0,
            "completed_phases": len(session.phases),
            "phase_total": len(session.phases),
            "artifact": session.apply_context.get("artifact") or session.apply_context.get("artifacts") or "",
            "recovery_available": False,
            "resource_profile": session.options.get("validation_resource_profile") or "automatic",
        },
    )
