from __future__ import annotations

"""ANDROID-X04 advanced instrumentation/device-farm adoption contracts.

This module deliberately composes existing authorities instead of creating a
second test runner, device fabric, retry engine, or evidence store:

* instrumentation semantics come from ``InstrumentationIntent``;
* physical/emulator identity comes from X02 ``DeviceLease``;
* parallel/resource safety uses TEST-X03 shard scheduling;
* retries remain EXO-owned and are emitted as retry policy, never executed here;
* durable bytes remain RUN-owned; this module emits projection/evidence payloads.
"""

from dataclasses import asdict, dataclass, field
from hashlib import sha256
import json
import os
import time
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from ..test_runtime_optimization import ShardExecution, schedule_shards
from .execution_snapshot import DeviceLease
from .instrumentation import InstrumentationIntent

ANDROID_X04_SCHEMA_VERSION = 1


_ADVANCED_WORKER_HANDOFF_ENV = "DEVTOOL_ANDROID_ADVANCED_HANDOFF"
_ADVANCED_WORKER_HANDOFF_MAX_AGE_SECONDS = 300.0


def create_advanced_worker_handoff(payload: Mapping[str, Any]) -> str:
    """Create a private immediate-child handoff for one typed Android child.

    The handoff is parent-PID bound and mode 0600/0700 through the shared
    invocation capability primitive.  Typed runner capabilities therefore do
    not become repository configuration, argv, or ordinary process env truth.
    """
    from ..invocation_origin import _write_private_handoff

    document = {
        "schema_version": 1,
        "kind": "devtool.android-advanced-worker-handoff",
        "parent_pid": os.getpid(),
        "created_ns": time.time_ns(),
        "payload": dict(payload),
    }
    return _write_private_handoff("devtool-android-advanced-", document)


def consume_advanced_worker_handoff(
    environment: dict[str, str] | os._Environ[str] | None = None,
) -> dict[str, Any]:
    """Consume and verify one immediate-parent advanced worker capability."""
    from ..invocation_origin import _consume_private_handoff

    document = _consume_private_handoff(
        _ADVANCED_WORKER_HANDOFF_ENV,
        max_age_seconds=_ADVANCED_WORKER_HANDOFF_MAX_AGE_SECONDS,
        environment=environment,
    )
    if str(document.get("kind") or "") != "devtool.android-advanced-worker-handoff":
        return {}
    payload = document.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _stable(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class AndroidJUnitShard:
    """Typed AndroidJUnitRunner shard capability.

    Shard identity is semantic and does not depend on a device path/serial.
    AndroidJUnitRunner consumes the standard ``numShards``/``shardIndex``
    arguments; Devtool does not smuggle sharding through arbitrary runner args.
    """

    shard_id: str
    index: int
    count: int
    selectors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("AndroidJUnit shard count must be >= 1")
        if self.index < 0 or self.index >= self.count:
            raise ValueError("AndroidJUnit shard index is out of range")
        object.__setattr__(self, "selectors", tuple(str(x) for x in self.selectors))

    @property
    def runner_args(self) -> Mapping[str, str]:
        return MappingProxyType({"numShards": str(self.count), "shardIndex": str(self.index)})

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_X04_SCHEMA_VERSION,
            "shard_id": self.shard_id,
            "index": self.index,
            "count": self.count,
            "selectors": list(self.selectors),
            "runner_args": dict(self.runner_args),
        }


@dataclass(frozen=True, slots=True)
class AndroidTestOrchestratorMode:
    enabled: bool = False
    qualification_id: str = ""
    runner_component: str = "androidx.test.orchestrator/.AndroidTestOrchestrator"
    target_instrumentation: str = ""

    def __post_init__(self) -> None:
        if self.enabled and not self.qualification_id:
            raise ValueError("Android Test Orchestrator mode requires a qualified capability receipt")
        if self.enabled and not self.target_instrumentation:
            raise ValueError("Android Test Orchestrator mode requires target instrumentation identity")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExoRetryPolicy:
    """Retry intent exported to EXO; this module never performs retries."""

    mode: str = "disabled"
    max_attempts: int = 1

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "failed-only"}:
            raise ValueError("retry mode must be disabled or failed-only")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.mode == "disabled" and self.max_attempts != 1:
            raise ValueError("disabled retry policy must use one attempt")

    @property
    def owner(self) -> str:
        return "EXO"

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "owner": self.owner}


@dataclass(frozen=True, slots=True)
class QuarantinePolicy:
    """Explicit evidence-bearing quarantine policy.

    Quarantine never rewrites a failed testcase into an ordinary pass.  The
    reducer only annotates outcome disposition so the final truth stays intact.
    """

    test_ids: tuple[str, ...] = ()
    mode: str = "evidence-only"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.mode not in {"evidence-only", "disabled"}:
            raise ValueError("quarantine mode must be evidence-only or disabled")
        normalized = tuple(sorted({str(x).strip() for x in self.test_ids if str(x).strip()}))
        object.__setattr__(self, "test_ids", normalized)
        if self.mode == "disabled" and normalized:
            raise ValueError("disabled quarantine policy cannot carry test IDs")

    def disposition(self, test_id: str, status: str) -> dict[str, Any]:
        quarantined = self.mode != "disabled" and str(test_id) in self.test_ids
        return {
            "test_id": str(test_id),
            "status": str(status),
            "quarantined": quarantined,
            "reason": self.reason if quarantined else "",
            # Truth preservation: failed remains failed even when quarantined.
            "effective_status": str(status),
        }

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "test_ids": list(self.test_ids), "reason": self.reason}


@dataclass(frozen=True, slots=True)
class AndroidExecutionProfile:
    profile_id: str
    family: str = "android-junit"
    cross_app: bool = False
    ui_automator: bool = False
    evidence_kinds: tuple[str, ...] = ("test-results", "instrumentation-receipt", "device-lease")

    def __post_init__(self) -> None:
        if self.family not in {"android-junit", "ui-automator"}:
            raise ValueError("unsupported Android instrumentation execution profile")
        if self.ui_automator and self.family != "ui-automator":
            raise ValueError("UI Automator profile must use family=ui-automator")
        object.__setattr__(self, "evidence_kinds", tuple(self.evidence_kinds))

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "evidence_kinds": list(self.evidence_kinds)}


def execution_profile(*, ui_automator: bool = False, cross_app: bool = False) -> AndroidExecutionProfile:
    family = "ui-automator" if ui_automator else "android-junit"
    payload = {"family": family, "cross_app": bool(cross_app), "ui_automator": bool(ui_automator)}
    return AndroidExecutionProfile(
        profile_id="aprofile-" + _stable(payload)[:24],
        family=family,
        cross_app=bool(cross_app),
        ui_automator=bool(ui_automator),
    )


@dataclass(frozen=True, slots=True)
class DeviceCapacity:
    lease: DeviceLease
    max_concurrency: int = 1
    emulator_snapshot: str = ""

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("device concurrency cap must be >= 1")
        emulator = bool(dict(self.lease.device).get("emulator", False)) or self.lease.connection_type == "emulator"
        if self.emulator_snapshot and not emulator:
            raise ValueError("emulator snapshot reuse is forbidden for physical devices")

    @property
    def emulator(self) -> bool:
        return bool(dict(self.lease.device).get("emulator", False)) or self.lease.connection_type == "emulator"

    @property
    def physical(self) -> bool:
        return not self.emulator

    def as_dict(self) -> dict[str, Any]:
        return {
            "lease": self.lease.as_dict(),
            "max_concurrency": self.max_concurrency,
            "emulator_snapshot": self.emulator_snapshot,
            "emulator": self.emulator,
            "physical": self.physical,
        }


@dataclass(frozen=True, slots=True)
class DeviceMatrixChild:
    child_id: str
    parent_plan_id: str
    shard_id: str
    shard_index: int
    shard_count: int
    device_lease_id: str
    serial: str
    concurrency_slot: int
    resource_lock: str
    runner_args: Mapping[str, str]
    profile_id: str
    emulator_snapshot: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "runner_args", MappingProxyType(dict(self.runner_args)))

    def as_dict(self) -> dict[str, Any]:
        return {
            "child_id": self.child_id,
            "parent_plan_id": self.parent_plan_id,
            "shard_id": self.shard_id,
            "shard_index": self.shard_index,
            "shard_count": self.shard_count,
            "device_lease_id": self.device_lease_id,
            "serial": self.serial,
            "concurrency_slot": self.concurrency_slot,
            "resource_lock": self.resource_lock,
            "runner_args": dict(self.runner_args),
            "profile_id": self.profile_id,
            "emulator_snapshot": self.emulator_snapshot,
        }


@dataclass(frozen=True, slots=True)
class AdvancedInstrumentationPlan:
    plan_id: str
    intent_id: str
    shards: tuple[AndroidJUnitShard, ...]
    children: tuple[DeviceMatrixChild, ...]
    orchestrator: AndroidTestOrchestratorMode
    retry: ExoRetryPolicy
    quarantine: QuarantinePolicy
    profile: AndroidExecutionProfile
    device_capacities: tuple[DeviceCapacity, ...]
    requested_workers: int
    execution_waves: tuple[tuple[str, ...], ...]
    migration_diagnostics: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "shards", tuple(self.shards))
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "device_capacities", tuple(self.device_capacities))
        object.__setattr__(self, "execution_waves", tuple(tuple(w) for w in self.execution_waves))
        object.__setattr__(self, "migration_diagnostics", tuple(MappingProxyType(dict(x)) for x in self.migration_diagnostics))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ANDROID_X04_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "intent_id": self.intent_id,
            "shards": [x.as_dict() for x in self.shards],
            "children": [x.as_dict() for x in self.children],
            "orchestrator": self.orchestrator.as_dict(),
            "retry": self.retry.as_dict(),
            "quarantine": self.quarantine.as_dict(),
            "profile": self.profile.as_dict(),
            "device_capacities": [x.as_dict() for x in self.device_capacities],
            "requested_workers": self.requested_workers,
            "execution_waves": [list(x) for x in self.execution_waves],
            "migration_diagnostics": [dict(x) for x in self.migration_diagnostics],
            "authorities": {
                "instrumentation": "ANDROID-X03",
                "device_lease": "ANDROID-X02/HOST",
                "scheduling": "TEST-X03",
                "retry": "EXO",
                "evidence": "RUN-V3",
            },
        }


def android_junit_shards(intent: InstrumentationIntent, count: int) -> tuple[AndroidJUnitShard, ...]:
    count = int(count)
    if count < 1:
        raise ValueError("shard count must be >= 1")
    selectors = tuple(sorted({*intent.classes, *intent.methods}))
    return tuple(
        AndroidJUnitShard(
            shard_id=f"ajshard-{_stable({'intent': intent.intent_id, 'index': index, 'count': count})[:24]}",
            index=index,
            count=count,
            selectors=selectors,
        )
        for index in range(count)
    )


def _matrix_slots(capacities: Sequence[DeviceCapacity]) -> list[tuple[DeviceCapacity, int]]:
    slots: list[tuple[DeviceCapacity, int]] = []
    for capacity in sorted(capacities, key=lambda x: (x.lease.serial, x.lease.lease_id)):
        for slot in range(capacity.max_concurrency):
            slots.append((capacity, slot))
    return slots


def build_advanced_instrumentation_plan(
    intent: InstrumentationIntent,
    *,
    device_capacities: Sequence[DeviceCapacity],
    shard_count: int = 1,
    workers: int = 1,
    orchestrator: AndroidTestOrchestratorMode | None = None,
    retry: ExoRetryPolicy | None = None,
    quarantine: QuarantinePolicy | None = None,
    profile: AndroidExecutionProfile | None = None,
    migration_diagnostics: Iterable[Mapping[str, Any]] = (),
) -> AdvancedInstrumentationPlan:
    capacities = tuple(device_capacities)
    if not capacities:
        raise ValueError("advanced Android execution requires at least one DeviceLease-bound capacity")
    if len({x.lease.lease_id for x in capacities}) != len(capacities):
        raise ValueError("device matrix contains duplicate DeviceLease identities")
    shards = android_junit_shards(intent, shard_count)
    orchestrator = orchestrator or AndroidTestOrchestratorMode()
    retry = retry or ExoRetryPolicy()
    quarantine = quarantine or QuarantinePolicy(mode="disabled")
    profile = profile or execution_profile()
    worker_count = max(1, int(workers))

    semantic = {
        "intent_id": intent.intent_id,
        "shards": [x.as_dict() for x in shards],
        "orchestrator": orchestrator.as_dict(),
        "retry": retry.as_dict(),
        "quarantine": quarantine.as_dict(),
        "profile": profile.as_dict(),
        "leases": [x.lease.lease_id for x in capacities],
        "caps": [x.max_concurrency for x in capacities],
        "snapshots": [x.emulator_snapshot for x in capacities],
    }
    plan_id = "aix04-" + _stable(semantic)[:24]
    slots = _matrix_slots(capacities)
    if not slots:
        raise ValueError("device matrix has no execution slots")

    children: list[DeviceMatrixChild] = []
    schedulable: list[ShardExecution] = []
    for shard in shards:
        capacity, slot = slots[shard.index % len(slots)]
        runner_args = dict(shard.runner_args)
        if orchestrator.enabled:
            runner_args["targetInstrumentation"] = orchestrator.target_instrumentation
        child_payload = {
            "plan": plan_id,
            "shard": shard.shard_id,
            "lease": capacity.lease.lease_id,
            "slot": slot,
            "profile": profile.profile_id,
        }
        child_id = "aichild-" + _stable(child_payload)[:24]
        lock = f"android-device:{capacity.lease.serial}:slot:{slot}"
        child = DeviceMatrixChild(
            child_id=child_id,
            parent_plan_id=plan_id,
            shard_id=shard.shard_id,
            shard_index=shard.index,
            shard_count=shard.count,
            device_lease_id=capacity.lease.lease_id,
            serial=capacity.lease.serial,
            concurrency_slot=slot,
            resource_lock=lock,
            runner_args=runner_args,
            profile_id=profile.profile_id,
            emulator_snapshot=capacity.emulator_snapshot,
        )
        children.append(child)
        schedulable.append(ShardExecution(child.child_id, (shard.shard_id,), resources=(lock,), serial_constraints=(capacity.lease.serial,)))

    waves = schedule_shards(schedulable, workers=worker_count)
    execution_waves = tuple(tuple(item.shard_id for item in wave) for wave in waves)
    return AdvancedInstrumentationPlan(
        plan_id=plan_id,
        intent_id=intent.intent_id,
        shards=shards,
        children=tuple(children),
        orchestrator=orchestrator,
        retry=retry,
        quarantine=quarantine,
        profile=profile,
        device_capacities=capacities,
        requested_workers=worker_count,
        execution_waves=execution_waves,
        migration_diagnostics=tuple(migration_diagnostics),
    )


def legacy_instrumentation_migration_diagnostics(android_options: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Describe legacy Android instrumentation keys without mutating config."""
    options = dict(android_options or {})
    nested = options.get("instrumentation")
    nested = dict(nested) if isinstance(nested, Mapping) else {}
    diagnostics: list[dict[str, Any]] = []
    mapping = {
        "test_runner": "android.instrumentation.test_runner",
        "target_package": "android.instrumentation.target_package",
        "keep_installed": "android.instrumentation.keep_installed",
        "capture_logcat_on_failure": "android.instrumentation.capture_logcat_on_failure",
        "capture_screenshot_on_failure": "android.instrumentation.capture_screenshot_on_failure",
    }
    for old, new in mapping.items():
        if old in options and old not in nested:
            diagnostics.append({
                "code": "android-x04-legacy-instrumentation-key",
                "severity": "warning",
                "legacy": f"android.{old}",
                "replacement": new,
            })
    return tuple(diagnostics)


def plan_projection(plan: AdvancedInstrumentationPlan) -> dict[str, Any]:
    """Canonical machine/UI projection; renderers must not reconstruct state."""
    payload = plan.as_dict()
    return {
        "schema_version": ANDROID_X04_SCHEMA_VERSION,
        "kind": "android-instrumentation-advanced-plan",
        "plan_id": plan.plan_id,
        "intent_id": plan.intent_id,
        "summary": {
            "shards": len(plan.shards),
            "devices": len(plan.device_capacities),
            "children": len(plan.children),
            "waves": len(plan.execution_waves),
            "orchestrator": plan.orchestrator.enabled,
            "retry_mode": plan.retry.mode,
            "quarantine_count": len(plan.quarantine.test_ids),
            "profile": plan.profile.family,
        },
        "plan": payload,
    }


def matrix_evidence(plan: AdvancedInstrumentationPlan, child_receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Reduce shard/device/profile evidence without inventing testcase truth."""
    by_child = {str(row.get("child_id") or ""): dict(row) for row in child_receipts}
    expected = {child.child_id for child in plan.children}
    missing = sorted(expected - set(by_child))
    unexpected = sorted(set(by_child) - expected)
    duplicate = len(by_child) != len(child_receipts)
    receipt_ids: list[str] = []
    malformed: list[str] = []
    for child in plan.children:
        row = by_child.get(child.child_id)
        if row is None:
            continue
        receipt_id = str(row.get("receipt_id") or "").strip()
        receipt_ids.append(receipt_id)
        if (row.get("ok") is not True
            or not receipt_id
            or not str(row.get("final_child_run_id") or "").strip()
            or row.get("device_lease_id") != child.device_lease_id
            or row.get("serial") != child.serial
            or row.get("profile_id") != child.profile_id):
            malformed.append(child.child_id)
    duplicate_receipts = len(receipt_ids) != len(set(receipt_ids))
    return {
        "schema_version": ANDROID_X04_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "complete": not missing and not unexpected and not duplicate and not malformed and not duplicate_receipts,
        "missing_children": missing,
        "unexpected_children": unexpected,
        "malformed_children": malformed,
        "duplicate_children": duplicate,
        "duplicate_receipts": duplicate_receipts,
        "children": [
            {
                "child_id": child.child_id,
                "shard_id": child.shard_id,
                "device_lease_id": child.device_lease_id,
                "serial": child.serial,
                "profile_id": child.profile_id,
                "receipt": by_child.get(child.child_id),
            }
            for child in plan.children
        ],
        "retry_owner": plan.retry.owner,
        "quarantine": plan.quarantine.as_dict(),
    }


__all__ = [
    "ANDROID_X04_SCHEMA_VERSION",
    "AndroidJUnitShard",
    "AndroidTestOrchestratorMode",
    "ExoRetryPolicy",
    "QuarantinePolicy",
    "AndroidExecutionProfile",
    "DeviceCapacity",
    "DeviceMatrixChild",
    "AdvancedInstrumentationPlan",
    "android_junit_shards",
    "build_advanced_instrumentation_plan",
    "execution_profile",
    "legacy_instrumentation_migration_diagnostics",
    "matrix_evidence",
    "plan_projection",
    "create_advanced_worker_handoff",
    "consume_advanced_worker_handoff",
]
