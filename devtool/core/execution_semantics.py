from __future__ import annotations

"""Canonical execution / reuse / validation semantic contracts.

EXEC-X01 deliberately defines *meaning* before enabling reuse.  EXO remains the
runtime lifecycle authority, while this module supplies stable semantic identity
and validation proof vocabulary that later RER/validation convergence consumes.

The contracts here are intentionally serializable and side-effect free.  They do
not own a second run store, scheduler, event journal, process model, or cache.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


_SCHEMA_VERSION = 1
_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def _slug(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unnamed"
    return _SLUG_RE.sub("-", text).strip("-").lower() or "unnamed"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _jsonable(value.as_dict())
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    return value


def _stable_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(dict(payload)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ExecutionRootKind(str, Enum):
    REPOSITORY = "repository"
    TARGET = "target"
    WORKFLOW = "workflow"
    JOB = "job"
    NATIVE_TASK = "native-task"
    PROJECT = "project"
    PATH = "path"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class ExecutionRoot:
    """Executable scope, deliberately distinct from semantic TestSelection."""

    kind: ExecutionRootKind
    value: str
    target: str = ""
    runner: str = ""
    provenance: str = "canonical"

    def __post_init__(self) -> None:
        if not str(self.value or "").strip():
            raise ValueError("ExecutionRoot.value cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "value": str(self.value),
            "target": str(self.target),
            "runner": str(self.runner),
            "provenance": str(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Stable tool invocation identity.

    ``managed_argv`` contains Devtool-owned arguments. ``raw_passthrough_argv``
    contains caller-owned tokens after ``--``. ``protected_argv_identity`` may
    contain only secret-safe identity tokens/hashes; never raw secret values.
    """

    managed_argv: tuple[str, ...] = ()
    raw_passthrough_argv: tuple[str, ...] = ()
    protected_argv_identity: tuple[str, ...] = ()
    cwd_identity: str = ""
    provider_identity: str = ""

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "managed_argv": list(self.managed_argv),
            "raw_passthrough_argv": list(self.raw_passthrough_argv),
            "protected_argv_identity": list(self.protected_argv_identity),
            "cwd_identity": self.cwd_identity,
            "provider_identity": self.provider_identity,
        }

    @property
    def digest(self) -> str:
        return _stable_digest(self.semantic_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_payload(), "digest": self.digest}

    @classmethod
    def from_spec(cls, spec: Any) -> "ToolInvocation":
        metadata = dict(getattr(spec, "metadata", {}) or {})
        argv = tuple(str(item) for item in (getattr(spec, "argv", ()) or ()))
        raw = tuple(str(item) for item in metadata.get("raw_passthrough_argv", ()) or ())
        protected = tuple(
            str(item)
            for item in metadata.get("protected_argv_identity", ()) or ()
            if str(item)
        )
        return cls(
            managed_argv=argv,
            raw_passthrough_argv=raw,
            protected_argv_identity=protected,
            cwd_identity=str(getattr(spec, "cwd", "") or ""),
            provider_identity=str(
                metadata.get("tool_provider_identity")
                or metadata.get("provider_identity")
                or metadata.get("execution_provider")
                or ""
            ),
        )


@dataclass(frozen=True, slots=True)
class NodeDefinitionIdentity:
    """Stable cross-run semantic node identity, distinct from EXO NodeId."""

    value: str
    payload: Mapping[str, Any]
    schema_version: int = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not str(self.value or "").strip():
            raise ValueError("NodeDefinitionIdentity.value cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "value": str(self.value),
            "payload": _jsonable(dict(self.payload)),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, prefix: str = "node") -> "NodeDefinitionIdentity":
        normalized = _jsonable(dict(payload))
        digest = _stable_digest(normalized)
        label = _slug(normalized.get("label") or normalized.get("ref") or normalized.get("name") or prefix)
        return cls(value=f"{_slug(prefix)}:{label}@{digest[:20]}", payload=normalized)

    @classmethod
    def from_workflow_node(cls, node: Any) -> "NodeDefinitionIdentity":
        ref = getattr(node, "ref", None)
        payload = {
            "target": str(getattr(node, "target", "") or ""),
            "workflow": str(getattr(node, "workflow", "") or ""),
            "label": str(getattr(node, "label", "") or ""),
            "ref": str(getattr(ref, "raw", "") or ""),
            "kind": str(getattr(ref, "kind", "") or ""),
            "name": str(getattr(ref, "name", "") or ""),
            "action": str(getattr(ref, "action", "") or ""),
            "resolution_scope": str(getattr(ref, "resolution_scope", "") or ""),
            "resolution_target": str(getattr(ref, "resolution_target", "") or ""),
            "resolution_key": str(getattr(ref, "resolution_key", "") or ""),
            # Dependency *definition* edges are semantic.  Run IDs are not.
            "depends_on": [str(item) for item in (getattr(node, "depends_on", ()) or ())],
        }
        return cls.from_payload(payload, prefix="workflow-node")

    @classmethod
    def from_spec(cls, spec: Any, *, scope: str = "") -> "NodeDefinitionIdentity":
        invocation = ToolInvocation.from_spec(spec)
        metadata = dict(getattr(spec, "metadata", {}) or {})
        payload = {
            "name": str(getattr(spec, "name", "") or ""),
            "scope": str(scope or metadata.get("validation_scope") or metadata.get("target") or ""),
            "tool_invocation": invocation.semantic_payload(),
            "runner": str(metadata.get("runner") or metadata.get("project_type") or ""),
            "action": str(metadata.get("action") or ""),
            "validation_check_id": str(metadata.get("validation_check_id") or ""),
        }
        return cls.from_payload(payload, prefix="command-node")


class ReuseDisposition(str, Enum):
    REUSE = "reuse"
    MATERIALIZE = "materialize"
    RUN = "run"
    INVALIDATED = "invalidated"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ReusableNodeRecord:
    """Immutable reusable-result shell attached to EXO terminal history.

    EXEC-X01 records only schema/identity/provenance and remains fail-safe RUN.
    EXEC-X02 fills input/dependency/environment/output qualification fields.
    """

    node_definition_id: str
    source_run_id: str
    source_node_id: str
    execution_status: str
    disposition: ReuseDisposition = ReuseDisposition.RUN
    reusable: bool = False
    qualification_complete: bool = False
    reason: str = "qualification-not-implemented"
    evidence_refs: tuple[str, ...] = ()
    schema_version: int = _SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "node_definition_id": self.node_definition_id,
            "source_run_id": self.source_run_id,
            "source_node_id": self.source_node_id,
            "execution_status": self.execution_status,
            "disposition": self.disposition.value,
            "reusable": bool(self.reusable),
            "qualification_complete": bool(self.qualification_complete),
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_history_payload(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback_node_definition_id: str = "unknown",
        fallback_run_id: str = "",
        fallback_node_id: str = "",
    ) -> "ReusableNodeRecord":
        """Parse historical shell metadata fail-safe.

        X01 has no authority to qualify reuse yet.  Unknown schema, corrupt
        values, or even a future payload claiming reusable therefore normalize
        to a visible RUN record until EXEC-X02 performs full qualification.
        """

        data = dict(payload or {}) if isinstance(payload, Mapping) else {}
        try:
            schema_version = int(data.get("schema_version") or 0)
        except (TypeError, ValueError):
            schema_version = 0
        definition = str(data.get("node_definition_id") or fallback_node_definition_id or "unknown")
        source_run = str(data.get("source_run_id") or fallback_run_id or "")
        source_node = str(data.get("source_node_id") or fallback_node_id or "")
        execution_status = str(data.get("execution_status") or "unknown")
        reason = "exec-x01-history-unqualified"
        if schema_version != _SCHEMA_VERSION:
            reason = "unsupported-or-missing-reuse-schema"
        elif not definition or definition == "unknown":
            reason = "missing-node-definition-identity"
        elif not source_run or not source_node:
            reason = "incomplete-reuse-provenance"
        return cls(
            node_definition_id=definition or "unknown",
            source_run_id=source_run,
            source_node_id=source_node,
            execution_status=execution_status,
            disposition=ReuseDisposition.RUN,
            reusable=False,
            qualification_complete=False,
            reason=reason,
            evidence_refs=tuple(str(v) for v in data.get("evidence_refs", ()) or () if str(v)),
        )


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    operation_kind: str
    canonical_ref: str
    execution_roots: tuple[ExecutionRoot, ...] = ()
    test_selection: Mapping[str, Any] | None = None
    validation_contract_ref: str = ""
    tool_invocation: ToolInvocation | None = None
    repository_identity: Mapping[str, Any] = field(default_factory=dict)
    semantic_config_ids: tuple[str, ...] = ()

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "operation_kind": self.operation_kind,
            "canonical_ref": self.canonical_ref,
            "execution_roots": [root.to_dict() for root in self.execution_roots],
            "test_selection": _jsonable(dict(self.test_selection or {})),
            "validation_contract_ref": self.validation_contract_ref,
            "tool_invocation": self.tool_invocation.semantic_payload() if self.tool_invocation else None,
            "repository_identity": _jsonable(dict(self.repository_identity or {})),
            "semantic_config_ids": list(self.semantic_config_ids),
        }

    @property
    def digest(self) -> str:
        return _stable_digest(self.semantic_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self.semantic_payload(), "digest": self.digest}

    @classmethod
    def from_workflow_node(cls, node: Any, *, entrypoint: str = "invoke") -> "ExecutionIntent":
        """Normalize invoke/workflow nodes onto the canonical execution intent model.

        This is compatibility scaffolding only: existing workflow execution still
        owns lowering/scheduling.  The intent is attached to EXO metadata so later
        EXEC overlays can converge on it without inventing a second graph.
        """

        # ``invoke`` and the future canonical ``run`` are presentation/entry
        # surfaces only; both intentionally lower to the same semantic intent.
        _ = "run" if str(entrypoint or "invoke").strip().lower() in {"invoke", "run"} else str(entrypoint or "")
        ref = getattr(node, "ref", None)
        ref_kind = str(getattr(ref, "kind", "") or "").strip().lower()
        action = str(getattr(ref, "action", "") or getattr(node, "action", "") or "").strip().lower()
        if ref_kind == "test":
            operation_kind = "test"
            root_kind = ExecutionRootKind.TARGET
        elif ref_kind == "job":
            operation_kind = "job"
            root_kind = ExecutionRootKind.JOB
        elif action in {"validate", "wine-validate", "validation"}:
            operation_kind = "validation"
            root_kind = ExecutionRootKind.WORKFLOW
        elif action in {"package", "publish", "release", "deliver", "install"}:
            operation_kind = "delivery"
            root_kind = ExecutionRootKind.WORKFLOW
        else:
            operation_kind = "action"
            root_kind = ExecutionRootKind.WORKFLOW
        target = str(getattr(node, "target", "") or "")
        workflow = str(getattr(node, "workflow", "") or "")
        canonical_ref = str(getattr(ref, "raw", "") or getattr(node, "key", "") or "workflow-node")
        root_value = str(getattr(ref, "name", "") or canonical_ref) if root_kind is ExecutionRootKind.JOB else f"{target}::{workflow}"
        return cls(
            operation_kind=operation_kind,
            canonical_ref=canonical_ref,
            execution_roots=(ExecutionRoot(
                root_kind,
                root_value,
                target=target,
                runner=str(getattr(node, "runner", "") or getattr(ref, "name", "") or ""),
                provenance="workflow-compat",
            ),),
        )

    @classmethod
    def from_app_action(cls, package: str, action: str, *, target: str = "") -> "ExecutionIntent":
        """Compatibility seam for historical app ``run`` -> canonical ``launch``.

        The current CLI no longer exposes a separate ``app run`` command, but
        retained callers/records can normalize onto the future launch identity
        without changing semantic intent.
        """

        raw_action = str(action or "").strip().lower()
        canonical_action = "launch" if raw_action in {"run", "app-run", "launch"} else raw_action
        package_name = str(package or "").strip()
        if not package_name:
            raise ValueError("app execution intent requires a package")
        return cls(
            operation_kind="action",
            canonical_ref=f"android-app:{package_name}#{canonical_action}",
            execution_roots=(ExecutionRoot(
                ExecutionRootKind.COMMAND,
                package_name,
                target=str(target or ""),
                runner="android",
                provenance="app-run-launch-compat" if raw_action != canonical_action else "canonical",
            ),),
        )


class ValidationControlAction(str, Enum):
    START = "start"
    RUN = "run"
    RESUME = "resume"
    RETRY = "retry"
    NEXT = "next"
    FINISH = "finish"
    UNTIL_CHECKPOINT = "until-checkpoint"
    PAUSE = "pause"
    STOP = "stop"
    STATUS = "status"
    PLAN = "plan"
    EVENTS = "events"
    REPORT = "report"
    ABORT = "abort"
    RECOVER = "recover"
    RESET = "reset"
    ACCEPT_PLAN_CHANGE = "accept-plan-change"
    FINALIZE_COMPAT = "finalize-compat"


@dataclass(frozen=True, slots=True)
class ValidationControlIntent:
    action: ValidationControlAction
    scope: str = ""
    selected_checks: tuple[str, ...] = ()
    through: str = ""
    expected_revision: int | None = None
    compatibility_source: str = ""

    def validate(self) -> None:
        scope = str(self.scope or "").strip().lower()
        if scope == "through" and not self.through:
            raise ValueError("validation control scope 'through' requires a through checkpoint/check")
        if scope == "selected" and not self.selected_checks:
            raise ValueError("validation control scope 'selected' requires selected checks")
        if scope in {"next", "remaining"} and (self.selected_checks or self.through):
            raise ValueError(f"validation control scope {scope!r} cannot include selected checks or through")
        if self.expected_revision is not None and self.expected_revision < 0:
            raise ValueError("expected revision cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "action": self.action.value,
            "scope": self.scope,
            "selected_checks": list(self.selected_checks),
            "through": self.through,
            "expected_revision": self.expected_revision,
            "compatibility_source": self.compatibility_source,
        }


@dataclass(frozen=True, slots=True)
class ValidationRunRequest:
    action: str
    execution_roots: tuple[ExecutionRoot, ...] = ()
    selected_checks: tuple[str, ...] = ()
    test_selection: Mapping[str, Any] | None = None
    contract_ref: str = ""
    control: ValidationControlIntent | None = None
    fresh: bool = False
    # Presentation fields are intentionally excluded from semantic identity.
    output_format: str = "text"
    verbosity: str = ""
    copy_output: bool = False

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "execution_roots": [root.to_dict() for root in self.execution_roots],
            "selected_checks": list(self.selected_checks),
            "test_selection": _jsonable(dict(self.test_selection or {})),
            "contract_ref": self.contract_ref,
            "control": self.control.to_dict() if self.control else None,
            "fresh": bool(self.fresh),
        }

    @property
    def semantic_digest(self) -> str:
        return _stable_digest(self.semantic_payload())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.semantic_payload(),
            "semantic_digest": self.semantic_digest,
            "presentation": {
                "output_format": self.output_format,
                "verbosity": self.verbosity,
                "copy_output": bool(self.copy_output),
            },
        }


class ApplicabilityPolicy(str, Enum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    EXPLICIT = "explicit"


@dataclass(frozen=True, slots=True)
class CheckpointPolicy:
    name: str = "finalize"
    required_check_ids: tuple[str, ...] = ()
    source: str = "validation-contract"

    @property
    def digest(self) -> str:
        return _stable_digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "required_check_ids": list(self.required_check_ids),
            "source": self.source,
        }
        if include_digest:
            payload["digest"] = self.digest
        return payload


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    check_id: str
    label: str
    required: bool = True
    applicability_policy: ApplicabilityPolicy = ApplicabilityPolicy.ALWAYS
    depends_on: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    administrative: bool = False
    identity_provenance: str = "canonical"

    def __post_init__(self) -> None:
        if not self.check_id:
            raise ValueError("ValidationCheck.check_id cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "label": self.label,
            "required": bool(self.required),
            "applicability_policy": self.applicability_policy.value,
            "depends_on": list(self.depends_on),
            "evidence_requirements": list(self.evidence_requirements),
            "administrative": bool(self.administrative),
            "identity_provenance": self.identity_provenance,
        }


class ValidationOutcomeState(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not-applicable"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    check_id: str
    state: ValidationOutcomeState
    evidence_refs: tuple[str, ...] = ()
    applicability_evidence: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "state": self.state.value,
            "evidence_refs": list(self.evidence_refs),
            "applicability_evidence": list(self.applicability_evidence),
            "reason": self.reason,
        }


class ValidationVerdictStatus(str, Enum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ValidationVerdict:
    status: ValidationVerdictStatus
    contract_digest: str
    required_total: int
    required_satisfied: int
    unsatisfied_check_ids: tuple[str, ...] = ()
    blocked_check_ids: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.status is ValidationVerdictStatus.SATISFIED

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "satisfied": self.satisfied,
            "contract_digest": self.contract_digest,
            "required_total": int(self.required_total),
            "required_satisfied": int(self.required_satisfied),
            "unsatisfied_check_ids": list(self.unsatisfied_check_ids),
            "blocked_check_ids": list(self.blocked_check_ids),
        }


@dataclass(frozen=True, slots=True)
class ResolvedValidationContract:
    checks: tuple[ValidationCheck, ...]
    checkpoint_policy: CheckpointPolicy
    source: str = "resolved-validation"
    schema_version: int = _SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return _stable_digest({
            "schema_version": self.schema_version,
            "checks": [check.to_dict() for check in self.checks],
            "checkpoint_policy": self.checkpoint_policy.to_dict(include_digest=False),
            "source": self.source,
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": int(self.schema_version),
            "checks": [check.to_dict() for check in self.checks],
            "checkpoint_policy": self.checkpoint_policy.to_dict(),
            "source": self.source,
            "digest": self.digest,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResolvedValidationContract":
        checks: list[ValidationCheck] = []
        for item in payload.get("checks", []) or []:
            if not isinstance(item, Mapping):
                continue
            try:
                policy = ApplicabilityPolicy(str(item.get("applicability_policy") or "always"))
            except ValueError:
                policy = ApplicabilityPolicy.ALWAYS
            checks.append(ValidationCheck(
                check_id=str(item.get("check_id") or ""),
                label=str(item.get("label") or item.get("check_id") or ""),
                required=bool(item.get("required", True)),
                applicability_policy=policy,
                depends_on=tuple(str(v) for v in item.get("depends_on", []) or []),
                evidence_requirements=tuple(str(v) for v in item.get("evidence_requirements", []) or []),
                administrative=bool(item.get("administrative", False)),
                identity_provenance=str(item.get("identity_provenance") or "canonical"),
            ))
        cp = payload.get("checkpoint_policy") if isinstance(payload.get("checkpoint_policy"), Mapping) else {}
        checkpoint = CheckpointPolicy(
            name=str(cp.get("name") or "finalize"),
            required_check_ids=tuple(str(v) for v in cp.get("required_check_ids", []) or []),
            source=str(cp.get("source") or "validation-contract"),
        )
        return cls(
            checks=tuple(checks),
            checkpoint_policy=checkpoint,
            source=str(payload.get("source") or "resolved-validation"),
            schema_version=int(payload.get("schema_version") or _SCHEMA_VERSION),
        )

    def evaluate(self, outcomes: Sequence[ValidationOutcome]) -> ValidationVerdict:
        by_id = {outcome.check_id: outcome for outcome in outcomes}
        required_checks = [check for check in self.checks if check.required and not check.administrative]
        satisfied = 0
        unsatisfied: list[str] = []
        blocked: list[str] = []
        for check in required_checks:
            outcome = by_id.get(check.check_id)
            if outcome is None:
                unsatisfied.append(check.check_id)
                continue
            if outcome.state is ValidationOutcomeState.PASSED:
                satisfied += 1
                continue
            if outcome.state is ValidationOutcomeState.NOT_APPLICABLE:
                if (
                    check.applicability_policy is not ApplicabilityPolicy.ALWAYS
                    and bool(outcome.applicability_evidence)
                ):
                    satisfied += 1
                else:
                    unsatisfied.append(check.check_id)
                continue
            if outcome.state is ValidationOutcomeState.BLOCKED:
                blocked.append(check.check_id)
                continue
            # Generic SKIPPED/CANCELLED/FAILED/MISSING never satisfies a
            # required proof obligation.
            unsatisfied.append(check.check_id)

        # Empty required set is vacuously satisfied. Optional checks do not
        # become accidentally required merely because they were executed.
        if not required_checks or (satisfied == len(required_checks) and not blocked and not unsatisfied):
            status = ValidationVerdictStatus.SATISFIED
        elif blocked:
            status = ValidationVerdictStatus.BLOCKED
        else:
            status = ValidationVerdictStatus.UNSATISFIED
        return ValidationVerdict(
            status=status,
            contract_digest=self.digest,
            required_total=len(required_checks),
            required_satisfied=satisfied,
            unsatisfied_check_ids=tuple(unsatisfied),
            blocked_check_ids=tuple(blocked),
        )


def canonical_validation_check_identity(spec: Any, *, index: int = 0, allow_legacy: bool = False) -> tuple[str, str]:
    """Return stable check identity + provenance without substring inference.

    Existing durable sessions may still use legacy phase-name heuristics for
    compatibility, but the new canonical contract never depends on command-name
    substrings such as ``'test' in spec.name``.
    """

    metadata = dict(getattr(spec, "metadata", {}) or {})
    stored_provenance = str(metadata.get("validation_identity_provenance") or "").strip()
    explicit = str(metadata.get("validation_check_id") or metadata.get("validation_phase_key") or "").strip()
    if explicit:
        return explicit, stored_provenance or "explicit-check-id"
    scope = str(metadata.get("validation_scope") or metadata.get("target") or "repository").strip() or "repository"
    phase = str(metadata.get("validation_phase_name") or "").strip().lower()
    occurrence = int(metadata.get("validation_phase_occurrence") or 1)
    if phase:
        return f"{_slug(scope)}:{_slug(phase)}:{occurrence}", stored_provenance or "explicit-phase"
    components = [str(v).strip().lower() for v in metadata.get("validation_components", []) or [] if str(v).strip()]
    component_map = {
        "restore": "preflight",
        "build": "package",
        "test": "unit-test",
        "lint": "lint",
        "instrumentation": "instrumentation",
    }
    for component in components:
        if component in component_map:
            return f"{_slug(scope)}:{component_map[component]}:{occurrence}", stored_provenance or "declared-component"
    if not allow_legacy:
        raise ValueError(
            "ValidationCheck requires validation_check_id, validation_phase_name/key, "
            "or declared validation_components; command-name inference is migration-only."
        )
    # Compatibility fallback uses the *exact* spec name as opaque identity;
    # no semantic phase is inferred from substrings.
    name = str(getattr(spec, "name", "") or f"spec-{index or 1}")
    return f"{_slug(scope)}:spec:{_slug(name)}", stored_provenance or "legacy-exact-spec-name"


def validation_check_from_spec(spec: Any, *, index: int = 0, allow_legacy_identity: bool = False) -> ValidationCheck:
    metadata = dict(getattr(spec, "metadata", {}) or {})
    check_id, provenance = canonical_validation_check_identity(spec, index=index, allow_legacy=allow_legacy_identity)
    raw_policy = str(metadata.get("validation_applicability_policy") or "always").strip().lower()
    try:
        applicability = ApplicabilityPolicy(raw_policy)
    except ValueError:
        applicability = ApplicabilityPolicy.ALWAYS
    label = str(metadata.get("validation_phase_name") or getattr(spec, "name", "") or check_id)
    deps = tuple(str(v) for v in metadata.get("validation_depends_on", []) or [] if str(v))
    evidence = tuple(str(v) for v in metadata.get("validation_evidence_requirements", []) or [] if str(v))
    administrative = bool(metadata.get("validation_administrative", False))
    return ValidationCheck(
        check_id=check_id,
        label=label,
        required=bool(metadata.get("validation_required", True)),
        applicability_policy=applicability,
        depends_on=deps,
        evidence_requirements=evidence,
        administrative=administrative,
        identity_provenance=provenance,
    )


def resolved_validation_contract_from_specs(
    specs: Sequence[Any],
    *,
    minimum_checkpoint: str = "finalize",
    source: str = "resolved-validation",
    allow_legacy_identity: bool = False,
) -> ResolvedValidationContract:
    checks = tuple(
        validation_check_from_spec(spec, index=index, allow_legacy_identity=allow_legacy_identity)
        for index, spec in enumerate(specs, 1)
    )
    canonical_checks = tuple(check for check in checks if not check.administrative)
    required_ids = tuple(check.check_id for check in canonical_checks if check.required)
    checkpoint = CheckpointPolicy(
        name=str(minimum_checkpoint or "finalize"),
        required_check_ids=required_ids,
        source="semantic-checkpoint",
    )
    return ResolvedValidationContract(checks=canonical_checks, checkpoint_policy=checkpoint, source=source)


def validation_outcome_from_operation(check_id: str, result: Any) -> ValidationOutcome:
    metadata = dict(getattr(result, "metadata", {}) or {})
    applicability = str(metadata.get("validation_applicability") or "").strip().lower()
    applicability_evidence = tuple(
        str(v) for v in metadata.get("validation_applicability_evidence", []) or [] if str(v)
    )
    evidence_refs = tuple(str(v) for v in metadata.get("evidence_refs", []) or [] if str(v))
    if bool(getattr(result, "skipped", False)):
        if applicability == "not-applicable" and applicability_evidence:
            state = ValidationOutcomeState.NOT_APPLICABLE
        else:
            state = ValidationOutcomeState.SKIPPED
    elif int(getattr(result, "returncode", 1) or 0) == 0:
        state = ValidationOutcomeState.PASSED
    else:
        state = ValidationOutcomeState.FAILED
    return ValidationOutcome(
        check_id=check_id,
        state=state,
        evidence_refs=evidence_refs,
        applicability_evidence=applicability_evidence,
        reason=str(metadata.get("validation_reason") or ""),
    )


def shadow_reuse_metadata(definition: NodeDefinitionIdentity) -> dict[str, Any]:
    """Fail-safe X01 shadow prediction: compute identity, never skip execution."""

    return {
        "mode": "shadow",
        "node_definition_id": definition.value,
        "node_definition": definition.to_dict(),
        "predicted_disposition": ReuseDisposition.RUN.value,
        "reason": "exec-x01-qualification-not-enabled",
        "authoritative_skip": False,
    }
