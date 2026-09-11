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
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid
from typing import Any, Iterable, Sequence

from .models import CommandSpec, Issue, OperationResult, ProjectConfig
from .validation_failures import FailureClassification, classify_validation_failure

SESSION_SCHEMA_VERSION = 6
DEFAULT_PHASE_ORDER = (
    "static",
    "preflight",
    "dependencies",
    "compile",
    "resources",
    "unit-test",
    "package",
    "lint",
    "instrumentation",
    "finalize",
)
_TERMINAL_PHASE_STATES = {"passed", "not-applicable", "succeeded", "skipped"}
_RUNNABLE_PHASE_STATES = {"pending", "interrupted", "stale"}
_BLOCKING_PHASE_STATES = {"failed"}
_ACTIVE_SESSION_STATES = {
    "created", "ready", "running", "pausing", "paused", "blocked",
    "validated", "validated-uncommitted", "rollback-requested",
    "rollback-running", "rollback-failed", "recovery-required",
}
_TERMINAL_SESSION_STATES = {
    "completed", "aborted-kept-changes", "aborted-rolled-back",
}
_INTERRUPTED_RETURN_CODES = {130}
_ALL_INPUT_DOMAINS = ("config", "source", "resources", "tests")
_DEFAULT_INPUT_DOMAINS: dict[str, tuple[str, ...]] = {
    "static": _ALL_INPUT_DOMAINS,
    "preflight": ("config",),
    "dependencies": ("config",),
    "compile": ("config", "source", "resources"),
    "resources": ("config", "resources"),
    "unit-test": _ALL_INPUT_DOMAINS,
    "package": ("config", "source", "resources"),
    "lint": ("config", "source", "resources"),
    "instrumentation": _ALL_INPUT_DOMAINS,
    "finalize": _ALL_INPUT_DOMAINS,
}
_DEFAULT_DEPENDENCY_NAMES: dict[str, tuple[str, ...]] = {
    "static": (),
    "preflight": ("static",),
    "dependencies": ("preflight",),
    "compile": ("dependencies", "preflight"),
    "resources": ("compile",),
    "unit-test": ("compile",),
    "package": ("compile", "unit-test"),
    "lint": ("compile",),
    "instrumentation": ("package",),
}
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


class ValidationControlRequestError(ValidationSessionError):
    """Raised when an out-of-band validation control request cannot be claimed."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class AttemptRecord:
    attempt_id: str
    started_at: str
    finished_at: str = ""
    verdict: str = "running"
    returncode: int | None = None
    signal: int | None = None
    duration_seconds: float = 0.0
    output_tail: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    log_path: str = ""
    input_snapshot_before: dict[str, str] = field(default_factory=dict)
    input_snapshot_after: dict[str, str] = field(default_factory=dict)
    owner_pid: int = 0
    owner_process_start: str = ""
    owner_run_id: str = ""
    heartbeat_at: str = ""
    failure_kind: str = ""
    interruption_reason: str = ""
    classification_evidence: list[str] = field(default_factory=list)
    category: str = ""
    retryable: bool = False
    recommended_profile: str = ""
    input_domains: list[str] = field(default_factory=list)
    inputs_changed: bool = False
    supervisor_events: list[dict[str, Any]] = field(default_factory=list)
    argv: list[str] = field(default_factory=list)
    resource_profile: str = ""
    automatic_retry: bool = False
    session_revision: int = 0


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
    attempt_history: list[AttemptRecord] = field(default_factory=list)
    current_attempt_id: str = ""
    owner_pid: int = 0
    owner_process_start: str = ""
    owner_run_id: str = ""
    heartbeat_at: str = ""
    last_verdict: str = ""
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    returncode: int | None = None
    output_tail: str = ""
    issues: list[dict[str, Any]] = field(default_factory=list)
    log_path: str = ""
    invalidated_reason: str = ""
    spec_fingerprint: str = ""
    interruption_count: int = 0
    last_interrupted_at: str = ""
    last_interrupted_reason: str = ""
    last_interrupted_returncode: int | None = None
    depends_on: list[str] = field(default_factory=list)
    input_domains: list[str] = field(default_factory=list)
    required: bool = True
    scope: str = "repository"
    supports_progress: bool = False
    progress_percent: int = 0
    progress_detail: str = ""
    progress_completed: int | None = None
    progress_total: int | None = None
    progress_unit: str = ""
    progress_updated_at: str = ""
    plan_change: str = ""


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
    plan_specs: list[dict[str, Any]] = field(default_factory=list)
    git_head: str = ""
    transaction_path: str = ""
    apply_context: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    last_error: str = ""
    paused_reason: str = ""
    invalidated_from: str = ""
    finalized_at: str = ""
    revision: int = 0
    failure_action: str = "pause"
    validated_at: str = ""
    required_checkpoint_key: str = ""
    required_checkpoint_keys: list[str] = field(default_factory=list)
    required_checkpoint_name: str = ""
    plan_fingerprint: str = ""
    plan_drift_status: str = ""
    plan_drift_details: list[dict[str, Any]] = field(default_factory=list)
    initial_git_head: str = ""
    final_git_head: str = ""
    report_path: str = ""
    commit_status: str = "not-requested"
    commit_hash: str = ""

    @property
    def complete(self) -> bool:
        required = [item for item in self.phases if item.required]
        return bool(required) and all(item.status in _TERMINAL_PHASE_STATES for item in required)

    @property
    def failed(self) -> bool:
        return any(item.status in _BLOCKING_PHASE_STATES for item in self.phases)

    def next_pending(self) -> PhaseRecord | None:
        if self.status in {"rollback-running", "rollback-failed", "recovery-required"}:
            return None
        return next((item for item in self.phases if item.status in _RUNNABLE_PHASE_STATES), None)

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


def _process_start_token(pid: int) -> str:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").split()
        return fields[21] if len(fields) > 21 else ""
    except OSError:
        return ""


def _process_is_alive(pid: int, start_token: str = "") -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    current = _process_start_token(pid)
    return not start_token or not current or current == start_token


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


def validation_event_log_path(repo_root: Path, session_id: str) -> Path:
    return session_root(repo_root) / session_id / "events.jsonl"


def validation_control_path(repo_root: Path, session_id: str) -> Path:
    return session_root(repo_root) / session_id / "control.json"


def validation_control_lock_path(repo_root: Path, session_id: str) -> Path:
    return session_root(repo_root) / session_id / "control.lock"


@contextmanager
def _validation_control_lock(session: ValidationSession):
    path = validation_control_lock_path(Path(session.repo_root), session.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_event(
    session: ValidationSession,
    event_type: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one durable validation event for app/CLI consumers.

    Events are append-only and carry the session revision that produced them.
    Heartbeats deliberately do not advance that revision, so optimistic control
    tokens remain stable while a long-running phase is merely proving liveness.
    """

    path = validation_event_log_path(Path(session.repo_root), session.session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "api_version": 1,
        "event_id": uuid.uuid4().hex,
        "type": str(event_type or "validation-event"),
        "timestamp": _utc_now(),
        "session_id": session.session_id,
        "revision": int(session.revision or 0),
        "state": session.status,
        "data": dict(data or {}),
    }
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    return event


def read_events(
    session: ValidationSession,
    *,
    after_event_id: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    path = validation_event_log_path(Path(session.repo_root), session.session_id)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    found_cursor = not bool(after_event_id)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if not found_cursor:
            if str(item.get("event_id") or "") == after_event_id:
                found_cursor = True
            continue
        events.append(item)
    bounded_limit = max(1, min(int(limit or 100), 1000))
    # Polling clients must never skip events after a cursor. Initial reads are
    # intentionally tail-oriented so a newly attached app does not have to
    # replay an unbounded historical log before rendering current state.
    return events[:bounded_limit] if after_event_id else events[-bounded_limit:]


def write_control_request(
    session: ValidationSession,
    action: str,
    *,
    expected_revision: int | None = None,
    requested_by: str = "cli",
) -> dict[str, Any]:
    normalized = str(action or "").strip().lower()
    if normalized not in {"pause-after-phase", "stop-now", "cancel-pause"}:
        raise ValidationSessionError(f"Unsupported validation control action: {action}")
    with _validation_control_lock(session):
        latest = load_session(Path(session.repo_root), session.session_id)
        current_revision = int(latest.revision)
        existing = read_control_request(latest)
        if existing:
            if (
                str(existing.get("action") or "") == normalized
                and (
                    expected_revision is None
                    or int(existing.get("expected_revision", -1) or -1) == int(expected_revision)
                )
            ):
                duplicate = dict(existing)
                duplicate["reused"] = True
                session.revision = current_revision
                return duplicate
            if expected_revision is not None and int(expected_revision) != current_revision:
                raise ValidationControlRequestError(
                    "stale_revision",
                    f"Validation session revision is stale: expected {expected_revision}, current {current_revision}.",
                )
            raise ValidationControlRequestError(
                "control_unavailable",
                "Another validation control request is already pending and must be consumed first.",
            )
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise ValidationControlRequestError(
                "stale_revision",
                f"Validation session revision is stale: expected {expected_revision}, current {current_revision}.",
            )
        previous_revision = current_revision
        save_session(latest, active=True)
        request = {
            "api_version": 1,
            "request_id": uuid.uuid4().hex,
            "session_id": latest.session_id,
            "expected_revision": previous_revision,
            "accepted_revision": int(latest.revision),
            "action": normalized,
            "requested_at": _utc_now(),
            "requested_by": requested_by,
            "reused": False,
        }
        active_phase = next((item for item in latest.phases if item.status == "running"), None)
        request["active_phase_key"] = active_phase.key if active_phase is not None else ""
        request["active_attempt_id"] = active_phase.current_attempt_id if active_phase is not None else ""
        _atomic_write_json(validation_control_path(Path(latest.repo_root), latest.session_id), request)
        session.revision = int(latest.revision)
        session.updated_at = latest.updated_at
        append_event(
            latest,
            "pause-requested" if normalized == "pause-after-phase" else "stop-requested" if normalized == "stop-now" else "pause-cancelled",
            data=request,
        )
        return request


def read_control_request(session: ValidationSession) -> dict[str, Any]:
    path = validation_control_path(Path(session.repo_root), session.session_id)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or str(payload.get("session_id") or "") != session.session_id:
        return {}
    return payload


def clear_control_request(session: ValidationSession, *, request_id: str = "") -> bool:
    with _validation_control_lock(session):
        path = validation_control_path(Path(session.repo_root), session.session_id)
        if not path.is_file():
            return False
        if request_id:
            current = read_control_request(session)
            if current and str(current.get("request_id") or "") != request_id:
                return False
        path.unlink(missing_ok=True)
        return True


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


def _attempt_from_dict(payload: dict[str, Any]) -> AttemptRecord:
    allowed = {name for name in AttemptRecord.__dataclass_fields__}
    return AttemptRecord(**{key: value for key, value in payload.items() if key in allowed})


def _phase_from_dict(payload: dict[str, Any]) -> PhaseRecord:
    allowed = {name for name in PhaseRecord.__dataclass_fields__}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["attempt_history"] = [
        _attempt_from_dict(item)
        for item in payload.get("attempt_history", [])
        if isinstance(item, dict)
    ]
    return PhaseRecord(**values)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def serialize_spec(spec: CommandSpec) -> dict[str, Any]:
    """Serialize an executable validation command into durable session state.

    Android signing passwords are execution-boundary secrets and must never be
    durable even if a third-party caller accidentally placed them in spec.env.
    """

    from .android.signing import strip_signing_secrets
    safe_env = strip_signing_secrets(spec.env)
    return {
        "name": str(spec.name),
        "argv": [str(item) for item in spec.argv],
        "env": {str(key): str(value) for key, value in safe_env.items()},
        "cwd": None if spec.cwd is None else str(spec.cwd),
        "destructive": bool(spec.destructive),
        "metadata": _json_safe(dict(spec.metadata)),
    }


def deserialize_spec(payload: dict[str, Any]) -> CommandSpec:
    """Restore a validation command exactly enough to resume without replanning."""

    return CommandSpec(
        name=str(payload.get("name") or "validation"),
        argv=[str(item) for item in payload.get("argv", [])],
        env={str(key): str(value) for key, value in dict(payload.get("env") or {}).items()},
        cwd=None if payload.get("cwd") is None else str(payload.get("cwd")),
        destructive=bool(payload.get("destructive", False)),
        metadata=dict(payload.get("metadata") or {}),
    )


def stored_specs(session: ValidationSession) -> list[CommandSpec]:
    """Return the immutable command plan captured when the session was created."""

    return [deserialize_spec(item) for item in session.plan_specs if isinstance(item, dict)]


def _session_from_dict(payload: dict[str, Any]) -> ValidationSession:
    allowed = {name for name in ValidationSession.__dataclass_fields__}
    values = {key: value for key, value in payload.items() if key in allowed}
    values["phases"] = [_phase_from_dict(item) for item in payload.get("phases", []) if isinstance(item, dict)]
    return ValidationSession(**values)


def _session_payload_for_repo(payload: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    current_repo = str(repo_root.expanduser().resolve())
    stored_repo = str(payload.get("repo_root") or "").strip()
    if not stored_repo or stored_repo == current_repo:
        return payload

    relocated = dict(payload)
    relocated["repo_root"] = current_repo
    stored_project = str(payload.get("project_root") or "").strip()
    if stored_project:
        old_repo = Path(stored_repo).expanduser()
        old_project = Path(stored_project).expanduser()
        try:
            relative_project = old_project.relative_to(old_repo)
        except ValueError:
            relative_project = None
        if old_project == old_repo:
            relocated["project_root"] = current_repo
        elif relative_project is not None:
            relocated["project_root"] = str((Path(current_repo) / relative_project).resolve())
    return relocated


def _legacy_attempt_verdict(phase: dict[str, Any]) -> str:
    status = str(phase.get("status") or "").strip().lower()
    if status in {"succeeded", "passed", "skipped", "not-applicable"}:
        return "passed"
    if status in {"pending", "running", "interrupted"} and int(phase.get("interruption_count", 0) or 0) > 0:
        return "interrupted"
    if status == "failed":
        return "code-failed"
    return "unknown"


def _migrate_session_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Upgrade durable validation sessions without discarding evidence."""

    version = int(payload.get("schema_version", 0) or 0)
    if version == SESSION_SCHEMA_VERSION:
        return payload
    if version not in {1, 2, 3, 4, 5}:
        raise ValidationSessionError(
            f"Unsupported validation session schema: {version or 'missing'}"
        )

    migrated = dict(payload)
    legacy_status = str(migrated.get("status") or "created")
    if version == 1:
        status_map = {
            "created": "ready",
            "failed": "blocked",
            "failed-rolled-back": "aborted-rolled-back",
            "aborted": "aborted-rolled-back" if not migrated.get("transaction_path") else "aborted-kept-changes",
        }
        migrated["status"] = status_map.get(legacy_status, legacy_status)
        context = dict(migrated.get("apply_context") or {})
        context.setdefault("legacy_session_status", legacy_status)
        migrated["apply_context"] = context
        options = dict(migrated.get("options") or {})
        failure_action = str(options.get("failure_action") or context.get("failure_action") or "").strip().lower()
        if not failure_action:
            if "rollback_on_failure" in context:
                legacy_rollback = bool(context.get("rollback_on_failure"))
            else:
                legacy_rollback = bool(options.get("rollback_on_failure", False))
            failure_action = "rollback" if legacy_rollback else "pause"
        migrated["failure_action"] = failure_action

    migrated["revision"] = max(1, int(migrated.get("revision", 0) or 0))
    phase_status_map = {
        "succeeded": "passed",
        "skipped": "not-applicable",
        "cancelled": "skipped",
        "running": "interrupted",
    }
    phases: list[dict[str, Any]] = []
    for raw in migrated.get("phases", []) or []:
        if not isinstance(raw, dict):
            continue
        phase = dict(raw)
        legacy_phase_status = str(phase.get("status") or "pending")
        phase["status"] = phase_status_map.get(legacy_phase_status, legacy_phase_status)
        phase.setdefault("last_verdict", _legacy_attempt_verdict(phase))
        phase_name = normalize_phase_name(str(phase.get("name") or ""))
        phase.setdefault("scope", "repository")
        phase.setdefault("required", True)
        phase.setdefault("supports_progress", False)
        phase.setdefault("input_domains", list(_DEFAULT_INPUT_DOMAINS.get(phase_name, _ALL_INPUT_DOMAINS)))
        phase.setdefault("depends_on", [])
        phase.setdefault("plan_change", "")
        history = [item for item in phase.get("attempt_history", []) if isinstance(item, dict)]
        has_evidence = bool(
            phase.get("started_at")
            or phase.get("finished_at")
            or phase.get("returncode") is not None
            or phase.get("output_tail")
            or int(phase.get("interruption_count", 0) or 0)
        )
        if not history and has_evidence:
            history.append({
                "attempt_id": f"legacy-{uuid.uuid4().hex[:12]}",
                "started_at": str(phase.get("started_at") or migrated.get("created_at") or _utc_now()),
                "finished_at": str(phase.get("finished_at") or migrated.get("updated_at") or ""),
                "verdict": _legacy_attempt_verdict(phase),
                "returncode": phase.get("returncode"),
                "duration_seconds": float(phase.get("duration_seconds", 0.0) or 0.0),
                "output_tail": str(phase.get("output_tail") or ""),
                "issues": list(phase.get("issues") or []),
                "log_path": str(phase.get("log_path") or ""),
                "interruption_reason": str(phase.get("last_interrupted_reason") or ""),
                "input_domains": list(phase.get("input_domains") or []),
            })
        for attempt in history:
            attempt.setdefault("classification_evidence", [])
            attempt.setdefault("category", "")
            attempt.setdefault("retryable", attempt.get("verdict") in {"interrupted", "environment-failed", "resource-failed", "timed-out"})
            attempt.setdefault("recommended_profile", "")
            attempt.setdefault("input_domains", list(phase.get("input_domains") or []))
            attempt.setdefault("inputs_changed", False)
            attempt.setdefault("supervisor_events", [])
            attempt.setdefault("argv", [])
            attempt.setdefault("resource_profile", "")
            attempt.setdefault("automatic_retry", False)
        phase["attempt_history"] = history
        phase["current_attempt_id"] = ""
        phase["owner_pid"] = 0
        phase["owner_process_start"] = ""
        phase["owner_run_id"] = ""
        phase["heartbeat_at"] = ""
        phases.append(phase)
    migrated["phases"] = phases
    migrated.setdefault("plan_specs", [])
    migrated.setdefault("required_checkpoint_key", "")
    migrated.setdefault("required_checkpoint_keys", [migrated.get("required_checkpoint_key")] if migrated.get("required_checkpoint_key") else [])
    migrated.setdefault("required_checkpoint_name", str((migrated.get("apply_context") or {}).get("minimum_phase") or "finalize"))
    migrated.setdefault("plan_fingerprint", "")
    migrated.setdefault("plan_drift_status", "")
    migrated.setdefault("plan_drift_details", [])
    migrated.setdefault("initial_git_head", str(migrated.get("git_head") or ""))
    migrated.setdefault("final_git_head", "")
    migrated.setdefault("report_path", "")
    migrated.setdefault("commit_status", "not-requested")
    migrated.setdefault("commit_hash", "")
    migrated["schema_version"] = SESSION_SCHEMA_VERSION
    return migrated

def save_session(
    session: ValidationSession,
    *,
    active: bool | None = None,
    bump_revision: bool = True,
) -> Path:
    path = session_path(Path(session.repo_root), session.session_id)
    # A control request can advance the durable revision while the validation
    # worker still holds an older in-memory session. Never let a heartbeat or
    # phase result move the revision backwards when it saves that object.
    if path.is_file():
        try:
            durable = json.loads(path.read_text(encoding="utf-8"))
            session.revision = max(
                int(session.revision or 0),
                int(durable.get("revision", 0) or 0),
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    if bump_revision:
        session.revision = max(0, int(session.revision or 0)) + 1
    session.updated_at = _utc_now()
    payload = session.as_dict()
    _atomic_write_json(path, payload)
    plan_payload = {
        "schema_version": SESSION_SCHEMA_VERSION,
        "session_id": session.session_id,
        "repo_root": session.repo_root,
        "project_root": session.project_root,
        "project_type": session.project_type,
        "target": session.target,
        "variant": session.variant,
        "status": session.status,
        "validation_mode": str(session.apply_context.get("validation_mode") or "standalone"),
        "artifact": session.apply_context.get("artifact") or session.apply_context.get("artifacts") or "",
        "resource_profile": session.options.get("validation_resource_profile") or "automatic",
        "transaction_path": session.transaction_path,
        "recovery_available": bool(session.transaction_path),
        "revision": session.revision,
        "failure_action": session.failure_action,
        "required_checkpoint_name": session.required_checkpoint_name,
        "required_checkpoint_keys": list(session.required_checkpoint_keys),
        "required_checkpoint_satisfied": required_checkpoint_satisfied(session),
        "plan_fingerprint": session.plan_fingerprint,
        "plan_drift_status": session.plan_drift_status,
        "phases": [
            {
                "key": item.key,
                "name": item.name,
                "label": item.label,
                "status": item.status,
                "depends_on": list(item.depends_on),
                "input_domains": list(item.input_domains),
                "required": item.required,
                "scope": item.scope,
            }
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
    migrated = _session_payload_for_repo(_migrate_session_payload(payload), repo_root)
    return _session_from_dict(migrated)


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


def _session_is_unfinished(session: ValidationSession) -> bool:
    return session.status not in _TERMINAL_SESSION_STATES


def unfinished_sessions(repo_root: Path) -> list[ValidationSession]:
    """Return durable unfinished sessions ordered newest first."""

    root = session_root(repo_root)
    sessions: list[ValidationSession] = []
    if not root.is_dir():
        return sessions
    for state_path in root.glob("*/state.json"):
        try:
            session = load_session(repo_root, state_path.parent.name)
        except ValidationSessionError:
            continue
        if _session_is_unfinished(session):
            sessions.append(session)
    sessions.sort(key=lambda item: (item.updated_at, item.created_at, item.session_id), reverse=True)
    return sessions


def resolve_resumable_session(
    repo_root: Path,
    requested_session: str = "",
    *,
    required: bool = False,
    repair_active_marker: bool = True,
) -> ValidationSession | None:
    """Resolve an unfinished session without silently discarding progress.

    ``active.json`` is only an index.  If it is missing or damaged, recover the
    session referenced by ``validation-plan.json`` or the sole unfinished
    durable session.  An orphaned plan and ambiguous unfinished sessions are
    safety errors rather than permission to create a replacement session.
    """

    repo_root = repo_root.expanduser().resolve()
    requested = str(requested_session or "").strip()
    if requested:
        session = load_session(repo_root, requested)
        if not _session_is_unfinished(session):
            raise ValidationSessionError(
                f"Validation session {requested} is already terminal ({session.status})."
            )
        return session

    marker_error: ValidationSessionError | None = None
    try:
        active = load_active_session(repo_root)
    except ValidationSessionError as exc:
        active = None
        marker_error = exc
    if active is not None and _session_is_unfinished(active):
        return active

    plan = validation_plan_path(repo_root)
    if plan.is_file():
        try:
            payload = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationSessionError(f"Invalid validation plan: {plan}: {exc}") from exc
        plan_session_id = str(payload.get("session_id") or "").strip()
        plan_status = str(payload.get("status") or "").strip()
        if plan_session_id and plan_status not in _TERMINAL_SESSION_STATES:
            try:
                session = load_session(repo_root, plan_session_id)
            except ValidationSessionError as exc:
                raise ValidationSessionError(
                    "Validation plan references session "
                    f"{plan_session_id}, but its state.json is missing or invalid. "
                    "No new validation session was created. Restore that session state, "
                    "select another session explicitly, or run validation-reset after review."
                ) from exc
            if _session_is_unfinished(session):
                if repair_active_marker:
                    save_session(session, active=True, bump_revision=False)
                return session

    candidates = unfinished_sessions(repo_root)
    if len(candidates) == 1:
        session = candidates[0]
        if repair_active_marker:
            save_session(session, active=True, bump_revision=False)
        return session
    if len(candidates) > 1:
        details = "\n".join(
            f"  - {item.session_id}  target={item.target or '-'}  "
            f"status={item.status}  updated={item.updated_at}"
            for item in candidates
        )
        raise ValidationSessionError(
            "Multiple unfinished validation sessions exist and no active marker selects one:\n"
            f"{details}\nUse --session <id>."
        )
    if required:
        if marker_error is not None:
            raise ValidationSessionError(
                f"No recoverable active validation session exists. {marker_error}"
            ) from marker_error
        raise ValidationSessionError("No active validation session exists for this repository.")
    return None


def validation_session_target_hint(repo_root: Path, requested_session: str = "") -> str:
    """Best-effort target hint used before full project configuration loading."""

    requested = str(requested_session or "").strip()
    if requested:
        return load_session(repo_root, requested).target
    try:
        active = load_active_session(repo_root)
    except ValidationSessionError:
        active = None
    if active is not None and _session_is_unfinished(active):
        return active.target
    plan = validation_plan_path(repo_root)
    if plan.is_file():
        try:
            payload = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        target = str(payload.get("target") or "").strip()
        if target:
            return target
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            try:
                return load_session(repo_root, session_id).target
            except ValidationSessionError:
                pass
    candidates = unfinished_sessions(repo_root)
    return candidates[0].target if len(candidates) == 1 else ""


def live_running_phase(session: ValidationSession) -> PhaseRecord | None:
    """Return a running phase whose recorded owner process is still alive."""

    return next(
        (
            phase
            for phase in session.phases
            if phase.status == "running"
            and _process_is_alive(phase.owner_pid, phase.owner_process_start)
        ),
        None,
    )


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


def spec_fingerprint(spec: CommandSpec) -> str:
    """Return a stable fingerprint for validation-relevant command inputs."""

    metadata_keys = (
        "validation_phase_name",
        "validation_components",
        "validation_depends_on",
        "validation_input_domains",
        "validation_required",
        "validation_scope",
        "validation_supports_progress",
        "gradle_tasks",
        "project_root",
        "workspace",
        "bundle_target",
        "matrix",
        "target",
        "test_selection",
    )
    payload = {
        "name": spec.name,
        "argv": list(spec.argv),
        "cwd": str(spec.cwd or ""),
        "env": {
            str(key): str(value)
            for key, value in sorted(spec.env.items())
            if str(key) not in {"DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD", "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD"}
        },
        "metadata": {key: spec.metadata.get(key) for key in metadata_keys if key in spec.metadata},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _phase_scope(spec: CommandSpec) -> str:
    return str(
        spec.metadata.get("validation_scope")
        or spec.metadata.get("workspace")
        or spec.metadata.get("bundle_target")
        or spec.metadata.get("matrix")
        or spec.metadata.get("target")
        or "repository"
    ).strip() or "repository"

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


def phase_key_for_spec(spec: CommandSpec, index: int = 0) -> str:
    explicit = str(spec.metadata.get("validation_phase_key") or "").strip()
    if explicit:
        return explicit
    phase = phase_name_for_spec(spec)
    scope = _phase_scope(spec)
    occurrence = int(spec.metadata.get("validation_phase_occurrence") or 1)
    return f"{scope}:{phase}:{occurrence}"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace(",", " ").split()
    elif isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value]
    else:
        values = []
    return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))


def _phase_input_domains(spec: CommandSpec, phase_name: str) -> list[str]:
    declared = _string_list(spec.metadata.get("validation_input_domains"))
    domains = declared or list(_DEFAULT_INPUT_DOMAINS.get(normalize_phase_name(phase_name), _ALL_INPUT_DOMAINS))
    invalid = [item for item in domains if item not in _ALL_INPUT_DOMAINS]
    if invalid:
        raise ValidationSessionError(
            f"Validation phase {phase_name!r} declares unsupported input domain(s): {', '.join(invalid)}"
        )
    return domains


def annotate_specs(specs: Sequence[CommandSpec], *, session_id: str = "") -> list[CommandSpec]:
    total = len(specs)
    annotated: list[CommandSpec] = []
    occurrences: dict[tuple[str, str], int] = {}
    for index, spec in enumerate(specs, start=1):
        metadata = dict(spec.metadata)
        phase_name = phase_name_for_spec(spec)
        scope = str(metadata.get("validation_scope") or _phase_scope(spec)).strip() or "repository"
        identity = (scope, phase_name)
        occurrences[identity] = occurrences.get(identity, 0) + 1
        metadata["validation_phase_occurrence"] = occurrences[identity]
        existing_key = str(metadata.get("validation_phase_key") or "").strip()
        generated_suffix = existing_key.rsplit(":", 1)[-1] if existing_key else ""
        if existing_key and not generated_suffix.isdigit():
            phase_key = existing_key
        else:
            metadata.pop("validation_phase_key", None)
            metadata["validation_scope"] = scope
            phase_key = phase_key_for_spec(
                CommandSpec(spec.name, list(spec.argv), env=dict(spec.env), cwd=spec.cwd, metadata=metadata),
                index,
            )
        metadata.update(
            {
                "validation_resumable": True,
                "validation_session_id": session_id,
                "validation_phase_name": phase_name,
                "validation_phase_key": phase_key,
                "validation_phase_index": index,
                "validation_phase_total": total,
                "validation_scope": scope,
                "validation_input_domains": _phase_input_domains(spec, phase_name),
                "validation_required": bool(metadata.get("validation_required", True)),
                "validation_supports_progress": bool(
                    metadata.get("validation_supports_progress", False)
                    or metadata.get("progress_parser")
                ),
            }
        )
        spec.metadata = metadata
        annotated.append(spec)
    return annotated


def _resolve_dependency_token(
    token: str,
    *,
    current: PhaseRecord,
    records: Sequence[PhaseRecord],
) -> str | None:
    exact = next((item.key for item in records if item.key == token), None)
    if exact:
        return exact
    normalized = normalize_phase_name(token)
    before = [item for item in records if item.index < current.index]
    same_scope = [
        item for item in before
        if item.scope == current.scope and normalize_phase_name(item.name) == normalized
    ]
    if same_scope:
        return same_scope[-1].key
    repository = [
        item for item in before
        if item.scope == "repository" and normalize_phase_name(item.name) == normalized
    ]
    if repository:
        return repository[-1].key
    matches = [item for item in before if normalize_phase_name(item.name) == normalized]
    return matches[-1].key if len(matches) == 1 else None


def _default_dependency_tokens(record: PhaseRecord, records: Sequence[PhaseRecord]) -> list[str]:
    name = normalize_phase_name(record.name)
    if name == "finalize":
        return [item.key for item in records if item.index < record.index and item.required]
    if name == "compile":
        available = {normalize_phase_name(item.name) for item in records if item.index < record.index}
        return ["dependencies"] if "dependencies" in available else (["preflight"] if "preflight" in available else [])
    tokens = list(_DEFAULT_DEPENDENCY_NAMES.get(name, ()))
    if tokens:
        return tokens
    previous = [
        item for item in records
        if item.index < record.index and item.required and item.scope == record.scope
    ]
    return [previous[-1].key] if previous else []


def _validate_dependency_graph(records: Sequence[PhaseRecord]) -> None:
    by_key = {item.key: item for item in records}
    for phase in records:
        missing = [key for key in phase.depends_on if key not in by_key]
        if missing:
            raise ValidationSessionError(
                f"Validation phase {phase.label!r} depends on missing phase key(s): {', '.join(missing)}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        if key in visiting:
            raise ValidationSessionError(f"Validation dependency cycle detected at phase: {by_key[key].label}")
        visiting.add(key)
        for dependency in by_key[key].depends_on:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for phase in records:
        visit(phase.key)


def _plan_fingerprint(records: Sequence[PhaseRecord]) -> str:
    payload = [
        {
            "key": item.key,
            "name": item.name,
            "scope": item.scope,
            "depends_on": item.depends_on,
            "input_domains": item.input_domains,
            "required": item.required,
            "spec_fingerprint": item.spec_fingerprint,
        }
        for item in records
    ]
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def phase_records(specs: Sequence[CommandSpec]) -> list[PhaseRecord]:
    records: list[PhaseRecord] = []
    total = len(specs)
    declared_dependencies: dict[str, list[str]] = {}
    for index, spec in enumerate(specs, start=1):
        phase_name = phase_name_for_spec(spec)
        scope = str(spec.metadata.get("validation_scope") or _phase_scope(spec)).strip() or "repository"
        label = f"{scope}: {phase_name}" if scope != "repository" else phase_name
        key = phase_key_for_spec(spec, index)
        record = PhaseRecord(
            key=key,
            name=phase_name,
            label=label,
            spec_name=spec.name,
            index=index,
            total=total,
            spec_fingerprint=spec_fingerprint(spec),
            input_domains=_phase_input_domains(spec, phase_name),
            required=bool(spec.metadata.get("validation_required", True)),
            scope=scope,
            supports_progress=bool(
                spec.metadata.get("validation_supports_progress", False)
                or spec.metadata.get("progress_parser")
            ),
        )
        records.append(record)
        declared_dependencies[key] = _string_list(spec.metadata.get("validation_depends_on"))

    for record in records:
        tokens = declared_dependencies.get(record.key) or _default_dependency_tokens(record, records)
        resolved: list[str] = []
        for token in tokens:
            dependency = _resolve_dependency_token(token, current=record, records=records)
            if dependency is None:
                if token in _DEFAULT_DEPENDENCY_NAMES.get(normalize_phase_name(record.name), ()):
                    continue
                raise ValidationSessionError(
                    f"Validation phase {record.label!r} declares unknown dependency: {token}"
                )
            if dependency != record.key and dependency not in resolved:
                resolved.append(dependency)
        record.depends_on = resolved
    _validate_dependency_graph(records)
    return records


def _required_checkpoint(records: Sequence[PhaseRecord], minimum_phase: str) -> tuple[str, list[str]]:
    normalized = normalize_phase_name(minimum_phase or "finalize")
    matches = [item.key for item in records if normalize_phase_name(item.name) == normalized]
    if matches:
        return normalized, matches
    if normalized == "finalize":
        # Finalize may be an administrative boundary rather than an executable
        # command. Requiring all required phases preserves that meaning.
        return normalized, [item.key for item in records if item.required]
    available = ", ".join(dict.fromkeys(item.name for item in records)) or "<none>"
    raise ValidationSessionError(
        f"Validation requires minimum phase {normalized!r}, but the generated plan cannot reach it. "
        f"Available phases: {available}"
    )


def required_checkpoint_satisfied(session: ValidationSession) -> bool:
    checkpoint_keys = getattr(session, "required_checkpoint_keys", []) or []
    checkpoint_key = str(getattr(session, "required_checkpoint_key", "") or "")
    keys = list(checkpoint_keys or ([] if not checkpoint_key else [checkpoint_key]))
    if not keys:
        return True
    by_key = {item.key: item for item in session.phases if hasattr(item, "key")}
    return all(key in by_key and by_key[key].status in _TERMINAL_PHASE_STATES for key in keys)

def create_session(
    config: ProjectConfig,
    specs: Sequence[CommandSpec],
    *,
    transaction_path: str = "",
    apply_context: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    replace_active: bool = False,
    persist: bool = True,
) -> ValidationSession:
    if persist:
        try:
            existing = load_active_session(config.repo_root)
        except ValidationSessionError:
            if not replace_active:
                raise
            existing = None
    else:
        existing = None
    if existing is not None and existing.status not in _TERMINAL_SESSION_STATES and not replace_active:
        raise ValidationSessionError(
            f"Validation session {existing.session_id} is already active. Resume or abort it before starting another."
        )
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:10]
    annotated = annotate_specs(list(specs), session_id=session_id)
    session_context = dict(apply_context or {})
    session_options = dict(options or {})
    failure_action = str(
        session_options.get("failure_action")
        or session_context.get("failure_action")
        or (
            "rollback"
            if bool(session_context.get("legacy_rollback_on_failure_explicit"))
            and bool(session_context.get("rollback_on_failure"))
            else "pause"
        )
    ).strip().lower()
    if failure_action not in {"pause", "rollback", "keep", "prompt"}:
        failure_action = "pause"
    records = phase_records(annotated)
    minimum_phase, checkpoint_keys = _required_checkpoint(
        records, str(session_context.get("minimum_phase") or "finalize")
    )
    session = ValidationSession(
        session_id=session_id,
        repo_root=str(config.repo_root.resolve()),
        project_root=str(Path(config.project_root or config.repo_root).expanduser().resolve()),
        project_type=config.kind,
        target=str(config.selected_target or ""),
        variant=str(config.variant or ""),
        status="ready",
        created_at=_utc_now(),
        updated_at=_utc_now(),
        phases=records,
        fingerprints=input_fingerprints(config.repo_root),
        plan_specs=[serialize_spec(spec) for spec in annotated],
        git_head=_git_head(config.repo_root),
        initial_git_head=_git_head(config.repo_root),
        transaction_path=str(transaction_path or ""),
        apply_context=session_context,
        options=session_options,
        failure_action=failure_action,
        required_checkpoint_key=checkpoint_keys[0] if len(checkpoint_keys) == 1 else "",
        required_checkpoint_keys=checkpoint_keys,
        required_checkpoint_name=minimum_phase,
        plan_fingerprint=_plan_fingerprint(records),
    )
    # Deferred apply sessions keep a transaction/rollback point available.
    # Store this explicitly so checkpoint output and recovery UX do not need
    # to infer it from filesystem state later.
    if transaction_path:
        session.options.setdefault("recovery_available", True)
    elif session.options.get("rollback_point"):
        session.options.setdefault("recovery_available", True)
    if persist:
        save_session(session, active=True)
        append_event(session, "session-created", data={
            "phase_total": len(session.phases),
            "transaction_attached": bool(session.transaction_path),
        })
    return session


def _reset_phase(phase: PhaseRecord, reason: str) -> None:
    phase.status = "pending"
    phase.returncode = None
    phase.started_at = ""
    phase.finished_at = ""
    phase.duration_seconds = 0.0
    phase.output_tail = ""
    phase.issues = []
    phase.log_path = ""
    phase.invalidated_reason = reason
    phase.last_verdict = ""
    _clear_phase_owner(phase)


def _phase_map(session: ValidationSession) -> dict[str, PhaseRecord]:
    return {item.key: item for item in session.phases}


def _dependency_closure(session: ValidationSession, keys: Iterable[str]) -> set[str]:
    by_key = _phase_map(session)
    closure: set[str] = set()

    def include(key: str) -> None:
        if key in closure or key not in by_key:
            return
        closure.add(key)
        for dependency in by_key[key].depends_on:
            include(dependency)

    for key in keys:
        include(key)
    return closure


def _dependent_closure(session: ValidationSession, keys: Iterable[str]) -> set[str]:
    seeds = {key for key in keys}
    closure = set(seeds)
    changed = True
    while changed:
        changed = False
        for phase in session.phases:
            if phase.key in closure:
                continue
            if any(dependency in closure for dependency in phase.depends_on):
                closure.add(phase.key)
                changed = True
    return closure


def _invalidate_keys(session: ValidationSession, keys: Iterable[str], reason: str) -> list[str]:
    selected = _dependent_closure(session, keys)
    invalidated: list[str] = []
    for phase in session.phases:
        if phase.key not in selected:
            continue
        if phase.status != "pending" or phase.returncode is not None or phase.last_verdict:
            invalidated.append(phase.key)
        _reset_phase(phase, reason)
    if selected:
        first = next((item for item in session.phases if item.key in selected), None)
        session.invalidated_from = first.name if first else ""
        session.status = "paused"
        session.paused_reason = reason
        session.validated_at = ""
    return invalidated


def _copy_plan_fields(previous: PhaseRecord, fresh: PhaseRecord) -> None:
    previous.index = fresh.index
    previous.total = fresh.total
    previous.name = fresh.name
    previous.label = fresh.label
    previous.spec_name = fresh.spec_name
    previous.spec_fingerprint = fresh.spec_fingerprint
    previous.depends_on = list(fresh.depends_on)
    previous.input_domains = list(fresh.input_domains)
    previous.required = fresh.required
    previous.scope = fresh.scope
    previous.supports_progress = fresh.supports_progress


def sync_plan(
    session: ValidationSession,
    specs: Sequence[CommandSpec],
    *,
    accept_unsafe: bool = False,
    acceptance_reason: str = "",
) -> list[CommandSpec]:
    """Synchronize a durable dependency graph and block unsafe plan drift.

    Success reuse requires the exact durable phase key. A phase with a similar
    label or command name can never inherit another phase's verdict.
    """

    annotated = annotate_specs(list(specs), session_id=session.session_id)
    fresh_records = phase_records(annotated)
    old_by_key = _phase_map(session)
    fresh_by_key = {item.key: item for item in fresh_records}
    details: list[dict[str, Any]] = []

    removed = [item for item in session.phases if item.key not in fresh_by_key]
    added = [item for item in fresh_records if item.key not in old_by_key]
    for item in removed:
        details.append({
            "kind": "removed",
            "key": item.key,
            "phase": item.name,
            "scope": item.scope,
            "required": item.required,
            "unsafe": item.required and normalize_phase_name(item.name) not in {"artifact-check", "archive-check"},
        })
    for item in added:
        details.append({
            "kind": "added",
            "key": item.key,
            "phase": item.name,
            "scope": item.scope,
            "required": item.required,
            "unsafe": False,
        })

    old_common_order = [item.key for item in session.phases if item.key in fresh_by_key]
    new_common_order = [item.key for item in fresh_records if item.key in old_by_key]
    if old_common_order != new_common_order:
        details.append({
            "kind": "reordered",
            "old": old_common_order,
            "new": new_common_order,
            "unsafe": True,
        })

    changed_keys: set[str] = set()
    dependency_changed_keys: set[str] = set()
    for fresh in fresh_records:
        previous = old_by_key.get(fresh.key)
        if previous is None:
            continue
        if previous.scope != fresh.scope or normalize_phase_name(previous.name) != normalize_phase_name(fresh.name):
            details.append({
                "kind": "identity-changed",
                "key": fresh.key,
                "old_phase": previous.name,
                "new_phase": fresh.name,
                "old_scope": previous.scope,
                "new_scope": fresh.scope,
                "unsafe": True,
            })
        if previous.spec_fingerprint and previous.spec_fingerprint != fresh.spec_fingerprint:
            changed_keys.add(fresh.key)
            details.append({
                "kind": "command-changed",
                "key": fresh.key,
                "phase": fresh.name,
                "unsafe": False,
            })
        if set(previous.depends_on) != set(fresh.depends_on):
            dependency_changed_keys.add(fresh.key)
            details.append({
                "kind": "dependencies-changed",
                "key": fresh.key,
                "phase": fresh.name,
                "old": previous.depends_on,
                "new": fresh.depends_on,
                "unsafe": False,
            })
        if previous.required and not fresh.required:
            details.append({
                "kind": "required-phase-became-optional",
                "key": fresh.key,
                "phase": fresh.name,
                "unsafe": True,
            })

    unsafe = [item for item in details if bool(item.get("unsafe"))]
    if unsafe and not accept_unsafe:
        session.plan_drift_status = "acceptance-required"
        session.plan_drift_details = details
        session.status = "recovery-required"
        session.paused_reason = "validation plan changed incompatibly; explicit acceptance or restart is required"
        session.last_error = "; ".join(
            f"{item.get('kind')}: {item.get('phase') or item.get('key') or 'plan'}"
            for item in unsafe
        )
        save_session(session, active=True)
        raise ValidationSessionError(
            "Validation plan changed incompatibly. Run `devtool validate restart-session` "
            "or `devtool validation-accept-plan-change --reason ...`."
        )

    rebuilt: list[PhaseRecord] = []
    for fresh in fresh_records:
        previous = old_by_key.get(fresh.key)
        if previous is None:
            rebuilt.append(fresh)
        else:
            _copy_plan_fields(previous, fresh)
            previous.plan_change = ""
            rebuilt.append(previous)
    session.phases = rebuilt
    _validate_dependency_graph(rebuilt)

    invalidation_seeds = set(changed_keys) | set(dependency_changed_keys)
    for added_phase in added:
        for phase in rebuilt:
            if added_phase.key in phase.depends_on and phase.status in _TERMINAL_PHASE_STATES:
                invalidation_seeds.add(phase.key)
    if invalidation_seeds:
        _invalidate_keys(session, invalidation_seeds, "validation plan changed for this phase or dependency")

    minimum_name, checkpoint_keys = _required_checkpoint(
        rebuilt, session.required_checkpoint_name or str(session.apply_context.get("minimum_phase") or "finalize")
    )
    session.required_checkpoint_name = minimum_name
    session.required_checkpoint_keys = checkpoint_keys
    session.required_checkpoint_key = checkpoint_keys[0] if len(checkpoint_keys) == 1 else ""
    session.plan_fingerprint = _plan_fingerprint(rebuilt)
    session.plan_drift_status = "accepted" if unsafe and accept_unsafe else ("safe-change" if details else "")
    session.plan_drift_details = details
    if unsafe and accept_unsafe:
        context = dict(session.apply_context or {})
        accepted = list(context.get("accepted_plan_changes") or [])
        accepted.append({
            "accepted_at": _utc_now(),
            "reason": acceptance_reason or "explicit plan change acceptance",
            "details": unsafe,
        })
        context["accepted_plan_changes"] = accepted[-20:]
        session.apply_context = context

    session.plan_specs = [serialize_spec(spec) for spec in annotated]

    if any(item.status == "failed" for item in rebuilt):
        session.status = "blocked"
    elif session.complete and required_checkpoint_satisfied(session):
        session.status = "validated"
    elif session.status not in {"running", "rollback-failed", "recovery-required"}:
        session.status = "paused" if any(item.status in _TERMINAL_PHASE_STATES for item in rebuilt) else "ready"
        if session.status == "paused" and not session.paused_reason:
            session.paused_reason = "validation plan synchronized"
    save_session(session, active=session.status not in _TERMINAL_SESSION_STATES)
    return annotated


def accept_plan_change(
    session: ValidationSession,
    specs: Sequence[CommandSpec],
    *,
    reason: str,
) -> list[CommandSpec]:
    if not reason.strip():
        raise ValidationSessionError("Accepting an unsafe validation plan change requires --reason.")
    return sync_plan(session, specs, accept_unsafe=True, acceptance_reason=reason.strip())


def invalidate_from(session: ValidationSession, phase_name: str, reason: str) -> None:
    matches = [item.key for item in session.phases if normalize_phase_name(item.name) == normalize_phase_name(phase_name) or item.key == phase_name]
    if not matches:
        raise ValidationSessionError(f"Unknown validation phase: {phase_name}")
    _invalidate_keys(session, matches, reason)


def reset_phase(session: ValidationSession, phase_name: str, reason: str = "explicit phase reset") -> list[str]:
    matches = [item.key for item in session.phases if normalize_phase_name(item.name) == normalize_phase_name(phase_name) or item.key == phase_name]
    if not matches:
        raise ValidationSessionError(f"Unknown validation phase: {phase_name}")
    reset = sorted(_dependent_closure(session, matches), key=lambda key: _phase_map(session)[key].index)
    _invalidate_keys(session, matches, reason)
    save_session(session, active=True)
    return reset


def reset_all(session: ValidationSession, reason: str = "explicit validation progress reset") -> list[str]:
    keys = [item.key for item in session.phases]
    for item in session.phases:
        _reset_phase(item, reason)
    session.status = "ready"
    session.paused_reason = reason
    session.last_error = ""
    session.invalidated_from = session.phases[0].name if session.phases else ""
    session.validated_at = ""
    save_session(session, active=True)
    return keys

def reconcile_inputs(
    session: ValidationSession,
    config: ProjectConfig,
    *,
    ignore_domains: Iterable[str] = (),
) -> str:
    repo = str(config.repo_root.resolve())
    project = str(Path(config.project_root or config.repo_root).expanduser().resolve())
    if repo != session.repo_root or project != session.project_root:
        raise ValidationSessionError("Validation session belongs to a different repository or project root.")
    if str(config.selected_target or "") != session.target or str(config.variant or "") != session.variant:
        raise ValidationSessionError("Validation session target or variant no longer matches the selected configuration.")
    current = input_fingerprints(config.repo_root)
    changed_domains = [
        domain for domain in _ALL_INPUT_DOMAINS
        if current.get(domain) != session.fingerprints.get(domain)
    ]
    if not changed_domains:
        session.git_head = _git_head(config.repo_root)
        save_session(session, active=True)
        return ""

    ignored = {str(domain).strip() for domain in ignore_domains if str(domain).strip()}
    invalidating_domains = [domain for domain in changed_domains if domain not in ignored]
    seeds = [
        item.key for item in session.phases
        if any(domain in item.input_domains for domain in invalidating_domains)
    ]
    if seeds:
        reason = (
            "validation input domain(s) changed since the previous clean checkpoint: "
            + ", ".join(invalidating_domains)
        )
        _invalidate_keys(session, seeds, reason)
    # A durable resume plan intentionally pins validation configuration to the
    # exact plan that started the session. Config-only edits therefore become
    # the new fingerprint baseline without replaying already-passed phases.
    # Source/resource/test changes still invalidate their dependent phases.
    session.fingerprints = current
    session.git_head = _git_head(config.repo_root)
    save_session(session, active=True)
    return ",".join(changed_domains)

def _interruption_reason(result: OperationResult) -> str:
    failure_kind = str(result.failure_kind or "").strip().lower()
    output = str(result.output or "")
    if failure_kind in {"cancelled", "canceled", "interrupted", "user_interrupt"}:
        return failure_kind.replace("_", " ")
    if int(result.returncode or 0) in _INTERRUPTED_RETURN_CODES:
        return "interrupted by user or control-center cancellation"
    lowered = output.lower()
    if "interrupted by user" in lowered or "cancelled from devtool control center" in lowered:
        return "interrupted by user or control-center cancellation"
    return ""


def classification_for_result(result: OperationResult) -> FailureClassification:
    return classify_validation_failure(result)


def classify_result(result: OperationResult) -> str:
    """Compatibility wrapper returning only the durable verdict string."""

    return classification_for_result(result).verdict

def _clear_phase_owner(phase: PhaseRecord) -> None:
    phase.current_attempt_id = ""
    phase.owner_pid = 0
    phase.owner_process_start = ""
    phase.owner_run_id = ""
    phase.heartbeat_at = ""


def recover_interrupted_phases(
    session: ValidationSession,
    *,
    reason: str = "previous validation process ended before recording a phase result",
    stale_after_seconds: float = 0.0,
) -> list[str]:
    """Return persisted ``running`` phases to the pending queue.

    A phase is marked running before its child process starts. Recovery only
    reclaims a persisted running phase when its recorded PID/process-start token
    is no longer alive. Live owners remain untouched, including during status
    and checkpoint reads.
    """

    recovered: list[str] = []
    now = _utc_now()
    for phase in session.phases:
        if phase.status != "running":
            continue
        if _process_is_alive(phase.owner_pid, phase.owner_process_start):
            continue
        if stale_after_seconds > 0 and phase.heartbeat_at:
            try:
                heartbeat = datetime.fromisoformat(phase.heartbeat_at.replace("Z", "+00:00"))
                current = datetime.now(timezone.utc)
                if (current - heartbeat).total_seconds() < stale_after_seconds:
                    continue
            except ValueError:
                pass
        phase.status = "interrupted"
        phase.returncode = None
        phase.finished_at = now
        phase.interruption_count += 1
        phase.last_interrupted_at = now
        phase.last_interrupted_reason = reason
        phase.last_interrupted_returncode = None
        phase.invalidated_reason = reason
        phase.last_verdict = "interrupted"
        attempt = next(
            (item for item in reversed(phase.attempt_history) if item.attempt_id == phase.current_attempt_id),
            None,
        )
        if attempt is not None:
            attempt.finished_at = now
            attempt.verdict = "interrupted"
            attempt.interruption_reason = reason
            attempt.heartbeat_at = now
        _clear_phase_owner(phase)
        recovered.append(phase.name)
    if recovered:
        names = ", ".join(recovered)
        session.status = "paused"
        session.paused_reason = f"recovered interrupted phase(s): {names}"
        session.last_error = ""
        save_session(session, active=True)
    return recovered


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


def _unsatisfied_dependencies(session: ValidationSession, phase: PhaseRecord) -> list[PhaseRecord]:
    by_key = _phase_map(session)
    return [
        by_key[key] for key in phase.depends_on
        if key in by_key and by_key[key].status not in _TERMINAL_PHASE_STATES
    ]


def select_phase_keys(
    session: ValidationSession,
    *,
    phase: str = "",
    selected_phases: Sequence[str] = (),
    exact_phase: bool = False,
    through: str = "",
    from_phase: str = "",
    next_only: bool = False,
    complete: bool = False,
    max_phases: int = 0,
) -> list[str]:
    runnable = [item for item in session.phases if item.status in _RUNNABLE_PHASE_STATES]
    if not runnable:
        return []
    runnable_keys = {item.key for item in runnable}
    selected_keys: set[str]

    requested_phases = [str(item).strip() for item in selected_phases if str(item).strip()]
    if requested_phases:
        matches = [
            item for item in session.phases
            if item.status in _RUNNABLE_PHASE_STATES
            and any(normalize_phase_name(item.name) == normalize_phase_name(requested) or item.key == requested for requested in requested_phases)
        ]
        missing_names = [
            requested for requested in requested_phases
            if not any(normalize_phase_name(item.name) == normalize_phase_name(requested) or item.key == requested for item in matches)
        ]
        if missing_names:
            raise ValidationSessionError("No pending validation phase matches: " + ", ".join(missing_names))
        selected_keys = _dependency_closure(session, [item.key for item in matches]) & runnable_keys
    elif phase:
        matches = [
            item for item in session.phases
            if item.status in _RUNNABLE_PHASE_STATES
            and (normalize_phase_name(item.name) == normalize_phase_name(phase) or item.key == phase)
        ]
        if not matches:
            raise ValidationSessionError(f"No pending validation phase matches: {phase}")
        if exact_phase:
            blocked = [dependency for item in matches for dependency in _unsatisfied_dependencies(session, item)]
            if blocked:
                labels = ", ".join(dict.fromkeys(item.label for item in blocked))
                raise ValidationSessionError(
                    f"Cannot run phase-only {phase!r}; unsatisfied dependencies: {labels}. "
                    "Use --phase to include prerequisite closure."
                )
            selected_keys = {item.key for item in matches}
        else:
            selected_keys = _dependency_closure(session, [item.key for item in matches]) & runnable_keys
    else:
        start_index = runnable[0].index
        if from_phase:
            matches = [
                item for item in session.phases
                if normalize_phase_name(item.name) == normalize_phase_name(from_phase) or item.key == from_phase
            ]
            if not matches:
                raise ValidationSessionError(f"Unknown validation phase: {from_phase}")
            start_index = matches[0].index
            unsatisfied = _unsatisfied_dependencies(session, matches[0])
            if unsatisfied:
                labels = ", ".join(item.label for item in unsatisfied)
                raise ValidationSessionError(
                    f"Cannot start from {from_phase!r}; prerequisite phases are not complete: {labels}"
                )

        if complete:
            selected_keys = {item.key for item in runnable if item.index >= start_index}
        elif through:
            matches = [
                item for item in session.phases
                if normalize_phase_name(item.name) == normalize_phase_name(through) or item.key == through
            ]
            if not matches:
                raise ValidationSessionError(f"Unknown validation phase: {through}")
            end_index = matches[-1].index
            if end_index < start_index:
                raise ValidationSessionError(f"--through {through} precedes the current/from phase")
            selected_keys = {
                item.key for item in runnable
                if start_index <= item.index <= end_index
            }
            selected_keys |= _dependency_closure(session, selected_keys) & runnable_keys
        else:
            eligible = [
                item for item in runnable
                if item.index >= start_index and not _unsatisfied_dependencies(session, item)
            ]
            if eligible:
                selected_keys = {eligible[0].key}
            else:
                first = next((item for item in runnable if item.index >= start_index), runnable[0])
                selected_keys = _dependency_closure(session, [first.key]) & runnable_keys
        if next_only:
            ordered_next = [item.key for item in session.phases if item.key in selected_keys]
            selected_keys = set(ordered_next[:1])

    ordered = [item.key for item in session.phases if item.key in selected_keys]
    # Validate that every selected phase either has a passed prerequisite or its
    # prerequisite appears earlier in this invocation.
    selected_so_far: set[str] = set()
    by_key = _phase_map(session)
    for key in ordered:
        missing = [
            dependency for dependency in by_key[key].depends_on
            if dependency in by_key
            and by_key[dependency].status not in _TERMINAL_PHASE_STATES
            and dependency not in selected_so_far
        ]
        if missing:
            labels = ", ".join(by_key[item].label for item in missing)
            raise ValidationSessionError(
                f"Validation selection is dependency-incomplete for {by_key[key].label}: {labels}"
            )
        selected_so_far.add(key)
    if max_phases > 0:
        ordered = ordered[:max_phases]
    return ordered

def spec_map(specs: Sequence[CommandSpec]) -> dict[str, CommandSpec]:
    return {str(spec.metadata.get("validation_phase_key") or ""): spec for spec in specs}


def mark_phase_running(session: ValidationSession, key: str) -> PhaseRecord:
    phase = session.phase_by_key(key)
    if phase is None:
        raise ValidationSessionError(f"Validation phase is not part of session {session.session_id}: {key}")
    if phase.status not in _RUNNABLE_PHASE_STATES:
        raise ValidationSessionError(
            f"Validation phase {phase.name!r} is {phase.status}, not runnable. "
            "Run `devtool validate checkpoint` and resume the next pending phase."
        )
    phase.status = "running"
    phase.attempts += 1
    phase.started_at = _utc_now()
    phase.finished_at = ""
    attempt_id = uuid.uuid4().hex
    phase.current_attempt_id = attempt_id
    phase.owner_pid = os.getpid()
    phase.owner_process_start = _process_start_token(os.getpid())
    phase.owner_run_id = os.environ.get("DEVTOOL_RUN_ID", "")
    phase.heartbeat_at = phase.started_at
    phase.progress_percent = 0
    phase.progress_detail = ""
    phase.progress_completed = None
    phase.progress_total = None
    phase.progress_unit = ""
    phase.progress_updated_at = phase.started_at
    phase.attempt_history.append(AttemptRecord(
        attempt_id=attempt_id,
        started_at=phase.started_at,
        input_snapshot_before=input_fingerprints(Path(session.repo_root)),
        input_domains=list(phase.input_domains),
        owner_pid=phase.owner_pid,
        owner_process_start=phase.owner_process_start,
        owner_run_id=phase.owner_run_id,
        heartbeat_at=phase.heartbeat_at,
        session_revision=int(session.revision or 0),
    ))
    session.status = "running"
    session.paused_reason = ""
    save_session(session, active=True)
    append_event(session, "phase-started", data={
        "phase_key": phase.key,
        "phase": phase.name,
        "label": phase.label,
        "attempt_id": phase.current_attempt_id,
        "index": phase.index,
        "total": phase.total,
    })
    return phase


def update_phase_progress(session: ValidationSession, key: str, snapshot: dict[str, Any]) -> bool:
    """Persist structured live phase progress and publish an API event.

    Progress updates deliberately do not bump the optimistic-concurrency revision;
    they are telemetry for observers, not control-state mutations.
    """

    phase = session.phase_by_key(key)
    if phase is None or phase.status != "running":
        return False
    raw_percent = snapshot.get("percent")
    completed = snapshot.get("completed")
    total = snapshot.get("total")
    percent = phase.progress_percent
    try:
        if raw_percent is not None:
            percent = max(0, min(100, int(round(float(raw_percent)))))
        elif completed is not None and total not in (None, 0, "0"):
            percent = max(0, min(100, int(round(float(completed) * 100.0 / float(total)))))
    except (TypeError, ValueError, ZeroDivisionError):
        percent = phase.progress_percent
    detail = str(snapshot.get("detail") or snapshot.get("current_task") or snapshot.get("current_test") or "").strip()
    unit = str(snapshot.get("unit_label") or "").strip()
    changed = (
        percent != phase.progress_percent
        or detail != phase.progress_detail
        or completed != phase.progress_completed
        or total != phase.progress_total
        or unit != phase.progress_unit
    )
    if not changed:
        return False
    phase.progress_percent = percent
    phase.progress_detail = detail
    phase.progress_completed = completed if isinstance(completed, int) else None
    phase.progress_total = total if isinstance(total, int) else None
    phase.progress_unit = unit
    phase.progress_updated_at = _utc_now()
    save_session(session, active=True, bump_revision=False)
    append_event(session, "phase-progress", data={
        "phase_key": phase.key,
        "phase": phase.name,
        "attempt_id": phase.current_attempt_id,
        "progress_kind": "determinate" if raw_percent is not None or phase.progress_total else "detail",
        "percent": phase.progress_percent,
        "completed": phase.progress_completed,
        "total": phase.progress_total,
        "unit_label": phase.progress_unit,
        "detail": phase.progress_detail,
        "updated_at": phase.progress_updated_at,
    })
    return True


def heartbeat_phase(session: ValidationSession, key: str, *, persist_interval_seconds: float = 4.0) -> bool:
    """Refresh a live attempt lease without rewriting state on every poll."""

    phase = session.phase_by_key(key)
    if phase is None or phase.status != "running":
        return False
    now = _utc_now()
    should_persist = True
    if phase.heartbeat_at:
        try:
            previous = datetime.fromisoformat(phase.heartbeat_at.replace("Z", "+00:00"))
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
            elapsed = (current - previous).total_seconds()
            should_persist = elapsed >= persist_interval_seconds
        except ValueError:
            should_persist = True
    if not should_persist:
        return False
    phase.heartbeat_at = now
    attempt = next(
        (item for item in reversed(phase.attempt_history) if item.attempt_id == phase.current_attempt_id),
        None,
    )
    if attempt is not None:
        attempt.heartbeat_at = now
    save_session(session, active=True, bump_revision=False)
    emit_progress = True
    for event in reversed(read_events(session, limit=32)):
        if str(event.get("type") or "") != "phase-progress":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if str(data.get("attempt_id") or "") != phase.current_attempt_id:
            continue
        try:
            event_time = datetime.fromisoformat(str(event.get("timestamp") or "").replace("Z", "+00:00"))
            current_time = datetime.fromisoformat(now.replace("Z", "+00:00"))
            emit_progress = (current_time - event_time).total_seconds() >= 30.0
        except ValueError:
            emit_progress = True
        break
    if emit_progress:
        append_event(session, "phase-progress", data={
            "phase_key": phase.key,
            "phase": phase.name,
            "attempt_id": phase.current_attempt_id,
            "progress_kind": "heartbeat",
            "heartbeat_at": now,
        })
    return True


def record_phase_result(session: ValidationSession, key: str, result: OperationResult) -> PhaseRecord:
    phase = session.phase_by_key(key)
    if phase is None:
        raise ValidationSessionError(f"Validation phase is not part of session {session.session_id}: {key}")
    phase.returncode = int(result.returncode)
    phase.duration_seconds += float(result.duration_seconds or 0.0)
    phase.finished_at = _utc_now()
    if int(result.returncode) == 0:
        phase.progress_percent = 100
    phase.progress_updated_at = phase.finished_at
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
    classification = classification_for_result(result)
    verdict = classification.verdict
    attempt = next(
        (item for item in reversed(phase.attempt_history) if item.attempt_id == phase.current_attempt_id),
        None,
    )
    if attempt is None:
        attempt = AttemptRecord(
            attempt_id=phase.current_attempt_id or uuid.uuid4().hex,
            started_at=phase.started_at or _utc_now(),
            input_domains=list(phase.input_domains),
        )
        phase.attempt_history.append(attempt)
    attempt.finished_at = phase.finished_at
    attempt.returncode = int(result.returncode)
    attempt.signal = classification.signal
    attempt.duration_seconds = float(result.duration_seconds or 0.0)
    attempt.output_tail = phase.output_tail
    attempt.issues = list(phase.issues)
    attempt.log_path = phase.log_path
    attempt.input_snapshot_after = input_fingerprints(Path(session.repo_root))
    if not attempt.input_snapshot_before:
        # Compatibility for direct callers/tests that record a result without
        # first marking the phase running. The durable baseline is the best
        # available pre-attempt snapshot in that case.
        attempt.input_snapshot_before = dict(session.fingerprints or attempt.input_snapshot_after)
    attempt.failure_kind = str(result.failure_kind or "")
    attempt.heartbeat_at = phase.finished_at
    attempt.classification_evidence = list(classification.evidence)
    attempt.category = classification.category
    attempt.retryable = classification.retryable
    attempt.recommended_profile = classification.recommended_profile
    events = result.metadata.get("supervisor_events") if isinstance(result.metadata, dict) else None
    attempt.supervisor_events = [dict(item) for item in events if isinstance(item, dict)] if isinstance(events, list) else []
    attempt.argv = [str(item) for item in result.argv]
    attempt.resource_profile = str(
        (result.metadata or {}).get("validation_retry_profile")
        or (result.metadata or {}).get("validation_resource_profile")
        or session.options.get("validation_resource_profile")
        or ""
    )
    attempt.automatic_retry = bool((result.metadata or {}).get("validation_automatic_retry", False))

    relevant_domains = phase.input_domains or list(_ALL_INPUT_DOMAINS)
    inputs_changed = any(
        attempt.input_snapshot_before.get(domain) != attempt.input_snapshot_after.get(domain)
        for domain in relevant_domains
    )
    attempt.inputs_changed = inputs_changed
    if inputs_changed:
        verdict = "inputs-changed"
        classification = FailureClassification(
            verdict="inputs-changed",
            retryable=True,
            category="input-integrity",
            evidence=[f"changed_domain={domain}" for domain in relevant_domains if attempt.input_snapshot_before.get(domain) != attempt.input_snapshot_after.get(domain)],
        )
        attempt.classification_evidence.extend(classification.evidence)
        attempt.category = classification.category
        attempt.retryable = True

    attempt.verdict = verdict
    phase.last_verdict = verdict
    interruption = _interruption_reason(result)
    if verdict == "inputs-changed":
        phase.status = "stale"
        phase.returncode = None
        phase.invalidated_reason = "validation inputs changed while this phase was running"
        session.status = "paused"
        session.paused_reason = f"{phase.label} result was discarded because its inputs changed during execution"
        session.last_error = ""
    elif verdict in {"interrupted", "environment-failed", "resource-failed", "timed-out"}:
        phase.status = "interrupted"
        phase.returncode = None
        phase.interruption_count += 1
        phase.last_interrupted_at = phase.finished_at
        phase.last_interrupted_reason = interruption or verdict.replace("-", " ")
        phase.last_interrupted_returncode = int(result.returncode)
        phase.invalidated_reason = phase.last_interrupted_reason
        attempt.interruption_reason = phase.last_interrupted_reason
        session.status = "paused"
        session.paused_reason = f"{phase.label} was interrupted and remains retryable"
        session.last_error = ""
        if classification.recommended_profile:
            session.options["recommended_retry_profile"] = classification.recommended_profile
    elif result.returncode == 0:
        phase.status = "not-applicable" if result.skipped else "passed"
        session.fingerprints = attempt.input_snapshot_after
        session.status = "validated" if session.complete and required_checkpoint_satisfied(session) else "paused"
        if session.status == "validated":
            session.validated_at = session.validated_at or phase.finished_at
            session.paused_reason = ""
        else:
            session.paused_reason = "phase boundary reached"
        session.last_error = ""
    elif verdict == "internal-error":
        phase.status = "failed"
        session.status = "recovery-required"
        session.last_error = phase.output_tail or f"{phase.label} failed internally"
        session.paused_reason = "validation engine recovery is required"
    else:
        phase.status = "failed"
        session.status = "blocked"
        session.last_error = phase.output_tail or f"{phase.label} failed"
        session.paused_reason = "validation phase failed; applied changes were retained"
    _clear_phase_owner(phase)
    session.git_head = _git_head(Path(session.repo_root))
    save_session(session, active=True)
    append_event(session, "phase-finished", data={
        "phase_key": phase.key,
        "phase": phase.name,
        "label": phase.label,
        "attempt_id": attempt.attempt_id,
        "status": phase.status,
        "verdict": phase.last_verdict,
        "returncode": attempt.returncode,
    })
    if session.status == "blocked":
        append_event(session, "session-blocked", data={"phase": phase.name, "verdict": phase.last_verdict})
    elif session.status == "validated":
        append_event(session, "session-validated", data={"completed_phases": len(session.phases)})
    if session.status in {"blocked", "validated", "recovery-required"}:
        _refresh_validation_report(session)
        save_session(session, active=True)
    return phase

def _refresh_validation_report(session: ValidationSession, *, commit_status: str = "", commit_hash: str = "") -> None:
    try:
        from .validation_reports import write_validation_report
        path = write_validation_report(session, commit_status=commit_status, commit_hash=commit_hash)
        session.report_path = str(path)
    except Exception as exc:
        context = dict(session.apply_context or {})
        warnings = list(context.get("report_warnings") or [])
        warnings.append(f"{type(exc).__name__}: {exc}")
        context["report_warnings"] = warnings[-10:]
        session.apply_context = context


def mark_paused(session: ValidationSession, reason: str) -> None:
    if not session.complete and session.status not in {"blocked", "recovery-required", "rollback-failed"}:
        session.status = "paused"
    session.paused_reason = reason
    save_session(session, active=not session.complete)
    append_event(session, "session-paused", data={"reason": reason})


def mark_completed(session: ValidationSession, *, commit_status: str = "not-requested", commit_hash: str = "") -> None:
    session.status = "completed"
    session.paused_reason = ""
    session.last_error = ""
    session.finalized_at = _utc_now()
    session.final_git_head = _git_head(Path(session.repo_root))
    session.commit_status = commit_status or "not-requested"
    session.commit_hash = commit_hash
    _refresh_validation_report(session, commit_status=session.commit_status, commit_hash=commit_hash)
    save_session(session, active=False)
    append_event(session, "session-completed", data={"report_path": session.report_path, "commit_status": session.commit_status, "commit_hash": session.commit_hash})


def mark_validated_uncommitted(session: ValidationSession, reason: str) -> None:
    session.status = "validated-uncommitted"
    session.validated_at = session.validated_at or _utc_now()
    session.final_git_head = _git_head(Path(session.repo_root))
    session.commit_status = "failed"
    session.last_error = reason
    session.paused_reason = "validation passed; commit remains pending"
    _refresh_validation_report(session, commit_status="failed")
    save_session(session, active=True)
    append_event(session, "commit-failed", data={"reason": reason, "report_path": session.report_path})


def _skip_unfinished_phases(session: ValidationSession, reason: str) -> list[str]:
    """Seal remaining phases only after an explicit destructive rollback."""

    skipped: list[str] = []
    finished_at = _utc_now()
    for phase in session.phases:
        if phase.status not in _RUNNABLE_PHASE_STATES | {"running"}:
            continue
        phase.status = "skipped"
        phase.returncode = None
        phase.finished_at = finished_at
        phase.invalidated_reason = reason
        _clear_phase_owner(phase)
        skipped.append(phase.name)
    return skipped


def mark_failure_terminal(
    session: ValidationSession,
    reason: str,
    *,
    rollback_attempted: bool,
    rollback_succeeded: bool,
) -> None:
    """Seal an attached validation failure after its rollback attempt.

    Deferred and sequential apply validation own a repository transaction. When
    an explicit rollback policy is selected, continuing later phases would
    validate source that is no longer applied. Seal every unfinished phase and
    clear the active marker only after a successful rollback.
    A failed rollback deliberately remains active so ``validation-abort`` can
    retry recovery without allowing validation to continue.
    """

    skipped = _skip_unfinished_phases(session, reason)
    context = dict(session.apply_context or {})
    if session.transaction_path:
        context["failed_transaction_path"] = session.transaction_path
    context["rollback_attempted"] = bool(rollback_attempted)
    context["rollback_succeeded"] = bool(rollback_succeeded)
    context["skipped_phases"] = skipped
    session.apply_context = context
    session.paused_reason = reason
    session.finalized_at = _utc_now()

    if rollback_succeeded:
        session.status = "aborted-rolled-back"
        session.transaction_path = ""
        session.final_git_head = _git_head(Path(session.repo_root))
        _refresh_validation_report(session)
        save_session(session, active=False)
    elif rollback_attempted:
        session.status = "rollback-failed"
        session.final_git_head = _git_head(Path(session.repo_root))
        _refresh_validation_report(session)
        save_session(session, active=True)
    else:
        session.status = "blocked"
        save_session(session, active=True)


def mark_aborted(
    session: ValidationSession,
    reason: str,
    *,
    kept_changes: bool = False,
    rolled_back: bool = False,
) -> None:
    _skip_unfinished_phases(session, reason)
    if session.transaction_path and (kept_changes or rolled_back or not Path(session.transaction_path).exists()):
        context = dict(session.apply_context or {})
        context["aborted_transaction_path"] = session.transaction_path
        session.apply_context = context
        session.transaction_path = ""
    session.status = "aborted-kept-changes" if kept_changes else "aborted-rolled-back"
    session.paused_reason = reason
    session.finalized_at = _utc_now()
    session.final_git_head = _git_head(Path(session.repo_root))
    _refresh_validation_report(session)
    save_session(session, active=False)
    append_event(
        session,
        "session-aborted",
        data={"reason": reason, "kept_changes": kept_changes, "rolled_back": rolled_back},
    )


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
                skipped=item.status in {"not-applicable", "skipped"},
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
    payload["pending"] = [item.name for item in session.phases if item.status in _RUNNABLE_PHASE_STATES]
    payload["failed_phases"] = [item.name for item in session.phases if item.status == "failed"]
    payload["skipped_phases"] = [item.name for item in session.phases if item.status == "skipped"]
    context = dict(session.apply_context or {})
    payload["validation_mode"] = str(context.get("validation_mode") or "standalone")
    payload["artifact"] = context.get("artifact") or context.get("artifacts") or ""
    payload["recovery_available"] = bool(session.transaction_path and Path(session.transaction_path).exists())
    payload["resource_profile"] = str(session.options.get("validation_resource_profile") or session.options.get("resource_profile") or "automatic")
    payload["minimum_phase"] = str(context.get("minimum_phase") or "finalize")
    payload["safe_checkpoint"] = not session.failed and session.status in {"ready", "paused", "validated", "completed"}
    payload["interrupted_phases"] = [item.name for item in session.phases if item.interruption_count > 0]
    payload["interruption_count"] = sum(item.interruption_count for item in session.phases)
    payload["revision"] = session.revision
    payload["failure_action"] = session.failure_action
    payload["required_checkpoint_name"] = session.required_checkpoint_name
    payload["required_checkpoint_keys"] = list(session.required_checkpoint_keys)
    payload["required_checkpoint_satisfied"] = required_checkpoint_satisfied(session)
    payload["plan_fingerprint"] = session.plan_fingerprint
    payload["plan_drift_status"] = session.plan_drift_status
    payload["plan_drift_details"] = list(session.plan_drift_details)
    payload["recommended_retry_profile"] = str(session.options.get("recommended_retry_profile") or "")
    execution_kind = str(session.options.get("execution_environment") or session.options.get("environment") or "native-termux").strip().lower()
    if execution_kind in {"auto", "termux", "native"}:
        execution_kind = "native-termux"
    chroot_name = str(session.options.get("chroot_name") or os.environ.get("DEVTOOL_CHROOT_CONTEXT") or "")
    linux_user = str(session.options.get("linux_user") or os.environ.get("USER") or "")
    transport = str(session.options.get("execution_transport") or "cli")
    payload["execution_environment"] = execution_kind
    payload["execution_context"] = {
        "kind": execution_kind,
        "name": chroot_name if execution_kind == "chroot" else "",
        "transport": transport,
        "workdir": session.project_root,
        "linux_user": linux_user,
    }
    repository_identity = {
        "environment": execution_kind,
        "chroot_name": chroot_name if execution_kind == "chroot" else "",
        "linux_user": linux_user,
        "canonical_path": session.repo_root,
    }
    identity_text = "\x1f".join(str(repository_identity[key]) for key in ("environment", "chroot_name", "linux_user", "canonical_path"))
    repository_identity["repository_id"] = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()
    payload["repository_identity"] = repository_identity
    payload["repository_id"] = repository_identity["repository_id"]
    control = read_control_request(session)
    passed_keys = {item.key for item in session.phases if item.status in _TERMINAL_PHASE_STATES}
    for item in payload.get("phases", []):
        if not isinstance(item, dict):
            continue
        effective_status = str(item.get("status") or "pending")
        if effective_status == "running" and str(control.get("action") or "") == "pause-after-phase":
            if str(control.get("active_phase_key") or "") in {"", str(item.get("key") or "")}:
                effective_status = "pausing"
        elif effective_status == "pending":
            dependencies = [str(value) for value in item.get("depends_on", []) or []]
            if any(value not in passed_keys for value in dependencies):
                effective_status = "waiting"
        item["effective_status"] = effective_status
    payload["uncommitted_changes"] = session.status == "validated-uncommitted"
    payload["report_path"] = session.report_path
    payload["final_verdict"] = ""
    payload["commit_status"] = session.commit_status
    payload["commit_hash"] = session.commit_hash
    payload["initial_git_head"] = session.initial_git_head
    payload["final_git_head"] = session.final_git_head
    try:
        from .validation_reports import load_validation_report
        report = load_validation_report(Path(session.repo_root), session.session_id)
        if report:
            payload["report_summary"] = {
                "schema_version": report.get("schema_version", 1),
                "generated_at": report.get("generated_at", ""),
                "final_verdict": report.get("final_verdict", ""),
                "diagnostics": report.get("diagnostics", {}),
                "commit": report.get("commit", {}),
                "rollback": report.get("rollback", {}),
            }
            payload["final_verdict"] = str(report.get("final_verdict") or "")
    except Exception:
        pass
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=session.repo_root,
            capture_output=True, text=True, check=False, timeout=5,
        )
        if dirty.returncode == 0:
            payload["uncommitted_changes"] = bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    if session.transaction_path and Path(session.transaction_path).exists():
        try:
            from .transactions import read_transaction
            transaction = read_transaction(session.transaction_path)
            entries = [item for item in transaction.get("affected_paths", []) or [] if isinstance(item, dict)]
            payload["rollback"] = {
                "status": str(transaction.get("status") or ""),
                "total_paths": len(entries),
                "verified_paths": sum(bool(item.get("verified")) for item in entries),
                "backup_healthy": bool(transaction.get("backup_dir")) and not bool(transaction.get("rollback_error")),
                "error": str(transaction.get("rollback_error") or ""),
            }
        except Exception as exc:
            payload["rollback"] = {"status": "unavailable", "total_paths": 0, "verified_paths": 0, "backup_healthy": False, "error": str(exc)}
    control = read_control_request(session)
    payload["pending_control"] = control
    control_action = str(control.get("action") or "")
    payload["effective_state"] = (
        "pausing" if control_action == "pause-after-phase" and session.status == "running"
        else "stopping" if control_action == "stop-now" and session.status == "running"
        else session.status
    )
    events = read_events(session, limit=1)
    payload["latest_event_id"] = str(events[-1].get("event_id") or "") if events else ""
    payload["live_attempts"] = [
        {
            "phase": item.name,
            "attempt_id": item.current_attempt_id,
            "owner_pid": item.owner_pid,
            "owner_run_id": item.owner_run_id,
            "heartbeat_at": item.heartbeat_at,
            "owner_alive": _process_is_alive(item.owner_pid, item.owner_process_start),
        }
        for item in session.phases
        if item.status == "running"
    ]
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
        f"Revision: {session.revision}",
        f"Failure action: {session.failure_action}",
        f"Mode: {mode}",
        f"Project: {session.project_root}",
        f"Target: {session.target or '-'}",
        f"Variant: {session.variant or '-'}",
        f"Resource profile: {session.options.get('validation_resource_profile') or session.options.get('resource_profile') or 'automatic'}",
        f"Progress: {completed}/{len(session.phases)} phases",
        f"Minimum checkpoint: {session.required_checkpoint_name or 'finalize'} ({'satisfied' if required_checkpoint_satisfied(session) else 'pending'})",
        f"Plan drift: {session.plan_drift_status or 'none'}",
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
        marker = {
            "passed": "ok", "not-applicable": "n/a", "skipped": "skip",
            "failed": "FAIL", "running": "run", "interrupted": "hold", "stale": "old",
        }.get(item.status, "wait")
        retry_note = f" (retry after {item.interruption_count} interruption(s))" if item.status in _RUNNABLE_PHASE_STATES and item.interruption_count else ""
        owner_note = f" (pid {item.owner_pid}, heartbeat {item.heartbeat_at or '-'})" if item.status == "running" else ""
        dependency_note = f" deps={','.join(item.depends_on)}" if item.depends_on else ""
        domain_note = f" inputs={','.join(item.input_domains)}" if item.input_domains else ""
        required_note = " required" if item.required else " optional"
        lines.append(f"  [{marker:4}] {item.index}/{item.total} {item.label}{required_note}{retry_note}{owner_note}{dependency_note}{domain_note}")
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
        f"Minimum phase: {payload.get('required_checkpoint_name') or payload.get('minimum_phase') or 'finalize'}",
        f"Minimum satisfied: {'yes' if payload.get('required_checkpoint_satisfied') else 'no'}",
        f"Plan drift: {payload.get('plan_drift_status') or 'none'}",
        f"Resource profile: {payload.get('resource_profile') or 'automatic'}",
        f"Recovery available: {'yes' if payload.get('recovery_available') or session.options.get('recovery_available') else 'no'}",
        f"Recovered interruptions: {payload.get('interruption_count') or 0}",
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
    completed_records = [item for item in session.phases if item.status in _TERMINAL_PHASE_STATES]
    completed_count = len(completed_records)
    latest = max(completed_records, key=lambda item: item.index) if completed_records else None
    latest_line = (
        f"Last completed: phase {latest.index} of {latest.total} · {latest.label}\n"
        if latest is not None
        else "Last completed: none\n"
    )
    next_line = (
        f"Next phase: {next_phase.index} of {next_phase.total} · {next_phase.label}"
        if next_phase is not None
        else "Next phase: none"
    )
    return OperationResult(
        "validation-paused",
        ["devtool", "resume-validation"],
        1 if require_complete else 0,
        0.0,
        (
            "VALIDATION PAUSED AT A PHASE BOUNDARY\n"
            f"Session: {session.session_id}\n"
            f"Reason: {reason}\n"
            f"Overall progress: {completed_count} of {len(session.phases)} phases completed\n"
            f"{latest_line}"
            f"{next_line}"
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
    total = len(session.phases)
    completed = sum(item.status in _TERMINAL_PHASE_STATES for item in session.phases)
    session_complete = bool(getattr(session, "complete", all(item.status in _TERMINAL_PHASE_STATES for item in session.phases)))
    if not session_complete or not required_checkpoint_satisfied(session):
        raise ValidationSessionError(
            f"Validation session {session.session_id} cannot complete before its required checkpoint is satisfied."
        )
    return OperationResult(
        "validation-session-complete",
        ["devtool", "validation-finalize", session.session_id],
        0,
        0.0,
        (
            "VALIDATION COMPLETE\n"
            f"Session: {session.session_id}\n"
            f"Completed: {completed} of {total} phases\n"
            f"Required checkpoint: {getattr(session, 'required_checkpoint_name', '') or 'finalize'}\n"
            "All required validation phases completed successfully.\n"
            "No pending validation phases remain."
        ),
        [],
        metadata={
            "validation_session_id": session.session_id,
            "validation_session_status": "completed",
            "validation_complete": True,
            "remaining_phases": 0,
            "completed_phases": completed,
            "phase_total": len(session.phases),
            "artifact": session.apply_context.get("artifact") or session.apply_context.get("artifacts") or "",
            "recovery_available": False,
            "resource_profile": session.options.get("validation_resource_profile") or "automatic",
            "validation_report_path": getattr(session, "report_path", ""),
            "validation_final_verdict": "validated",
            "commit_status": getattr(session, "commit_status", "not-requested"),
            "commit_hash": getattr(session, "commit_hash", ""),
            "initial_git_head": getattr(session, "initial_git_head", ""),
            "final_git_head": getattr(session, "final_git_head", ""),
        },
    )
