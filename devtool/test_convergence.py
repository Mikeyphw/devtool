from __future__ import annotations

"""TEST-X04 historical/reuse/distributed/presentation/roadmap convergence.

This module owns TEST semantics only.  EXO owns execution, RUN owns durable
history/attachments, RER owns reuse qualification, DIST owns transport/child-run
lineage, PRES owns rendering, and RMP owns roadmap/gate policy.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

_ALLOWED_FINAL_DISPOSITIONS = frozenset({"SATISFIED", "SUPERSEDED", "EXPORTED", "RETIRED"})
_TERMINAL_FAILURES = frozenset({"failed", "error", "cancelled", "xpass"})


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


def _tuple(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass(frozen=True, slots=True)
class HistoricalTestAlias:
    __test__ = False
    alias: str
    check_id: str
    behavior: str
    source_campaign: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("alias", "check_id", "behavior", "source_campaign"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"historical alias requires {name}")
        object.__setattr__(self, "evidence_refs", _tuple(self.evidence_refs))

    def as_dict(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "check_id": self.check_id,
            "behavior": self.behavior,
            "source_campaign": self.source_campaign,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceFixture:
    __test__ = False
    fixture_id: str
    check_id: str
    source_run_id: str
    evidence_refs: tuple[str, ...]
    immutable: bool = True

    def __post_init__(self) -> None:
        if not self.fixture_id or not self.check_id or not self.source_run_id:
            raise ValueError("historical evidence fixture requires fixture/check/source-run identity")
        object.__setattr__(self, "evidence_refs", _tuple(self.evidence_refs))
        if not self.evidence_refs:
            raise ValueError("historical evidence fixture requires at least one evidence ref")

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "check_id": self.check_id,
            "source_run_id": self.source_run_id,
            "evidence_refs": list(self.evidence_refs),
            "immutable": self.immutable,
        }


def historical_cleanup_projection(
    aliases: Iterable[HistoricalTestAlias],
    fixtures: Iterable[HistoricalEvidenceFixture],
    *,
    retired_paths: Iterable[str] = (),
) -> dict[str, Any]:
    alias_rows = tuple(sorted((item.as_dict() for item in aliases), key=lambda row: (row["check_id"], row["alias"])))
    fixture_rows = tuple(sorted((item.as_dict() for item in fixtures), key=lambda row: row["fixture_id"]))
    alias_names = [row["alias"] for row in alias_rows]
    if len(alias_names) != len(set(alias_names)):
        raise ValueError("historical aliases must be unique")
    check_ids = {row["check_id"] for row in alias_rows}
    for row in fixture_rows:
        if row["check_id"] not in check_ids:
            raise ValueError(f"historical fixture {row['fixture_id']} references unknown check {row['check_id']}")
    paths = tuple(sorted(_tuple(retired_paths)))
    return {
        "schema_version": 1,
        "aliases": list(alias_rows),
        "fixtures": list(fixture_rows),
        "retired_paths": list(paths),
        "canonical_history_authority": "RUN-V3",
        "repo_local_history_authoritative": False,
        "digest": _digest({"aliases": alias_rows, "fixtures": fixture_rows, "retired_paths": paths}),
    }


@dataclass(frozen=True, slots=True)
class GeneratedSurfaceReceipt:
    __test__ = False
    receipt_id: str
    root_check_id: str
    source_digest: str
    generated_digest: str
    dependent_check_ids: tuple[str, ...] = ()
    evidence_ref: str = ""

    def __post_init__(self) -> None:
        if not self.receipt_id or not self.root_check_id or not self.source_digest or not self.generated_digest:
            raise ValueError("generated-surface receipt requires receipt/check/source/generated identities")
        object.__setattr__(self, "dependent_check_ids", _tuple(self.dependent_check_ids))


def generated_surface_projection(receipt: GeneratedSurfaceReceipt, raw_outcomes: Mapping[str, str]) -> dict[str, Any]:
    outcomes = {str(k): str(v) for k, v in raw_outcomes.items()}
    if receipt.root_check_id not in outcomes:
        raise ValueError("generated-surface root result is missing")
    missing = sorted(set(receipt.dependent_check_ids) - set(outcomes))
    if missing:
        raise ValueError(f"generated-surface dependent raw outcome(s) missing: {', '.join(missing)}")
    return {
        "receipt_id": receipt.receipt_id,
        "root_check_id": receipt.root_check_id,
        "root_status": outcomes[receipt.root_check_id],
        "dependent_check_ids": list(receipt.dependent_check_ids),
        "raw_outcomes": outcomes,
        "cluster_annotation_only": True,
        "evidence_ref": receipt.evidence_ref,
    }


@dataclass(frozen=True, slots=True)
class RerTestEvidenceRequest:
    __test__ = False
    check_id: str
    selection_id: str
    input_digest: str
    environment_id: str
    output_identity: str
    policy_id: str
    freshness_token: str
    source_run_id: str = ""

    def __post_init__(self) -> None:
        required = (
            self.check_id,
            self.selection_id,
            self.input_digest,
            self.environment_id,
            self.output_identity,
            self.policy_id,
            self.freshness_token,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("RER test reuse requires check, selection, input, environment, output, policy, and freshness identity")

    @property
    def request_id(self) -> str:
        return "test-rer-" + _digest(self.as_dict(include_request_id=False))[:24]

    def as_dict(self, *, include_request_id: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
            "authority": "RER",
            "check_id": self.check_id,
            "selection_id": self.selection_id,
            "input_digest": self.input_digest,
            "environment_id": self.environment_id,
            "output_identity": self.output_identity,
            "policy_id": self.policy_id,
            "freshness_token": self.freshness_token,
            "source_run_id": self.source_run_id,
        }
        if include_request_id:
            payload["request_id"] = self.request_id
        return payload


def rer_reuse_projection(request: RerTestEvidenceRequest, *, qualified: bool, reason: str) -> dict[str, Any]:
    return {
        **request.as_dict(),
        "disposition": "REUSED" if qualified else "RUN",
        "qualified": bool(qualified),
        "reason": str(reason),
        "fabricate_live_events": False,
        "test_owns_reuse_decision": False,
    }


@dataclass(frozen=True, slots=True)
class DelegatedTestLineage:
    __test__ = False
    parent_run_id: str
    child_run_id: str
    check_ids: tuple[str, ...]
    provider: str
    transport_state: str
    child_status: str
    receipt_ref: str

    def __post_init__(self) -> None:
        if not self.parent_run_id or not self.child_run_id or not self.provider or not self.receipt_ref:
            raise ValueError("delegated test lineage requires parent/child/provider/receipt identity")
        object.__setattr__(self, "check_ids", _tuple(self.check_ids))
        if not self.check_ids:
            raise ValueError("delegated test lineage requires at least one check")

    def as_dict(self) -> dict[str, Any]:
        return {
            "authority": "DIST/EXO/RUN",
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "check_ids": list(self.check_ids),
            "provider": self.provider,
            "transport_state": self.transport_state,
            "child_status": self.child_status,
            "receipt_ref": self.receipt_ref,
            "transport_overwrites_test_truth": False,
        }

    @property
    def effective_test_status(self) -> str:
        # A known child outcome remains authoritative even if transport later degrades.
        return self.child_status


@dataclass(frozen=True, slots=True)
class ShuffleRepeatDiagnostic:
    __test__ = False
    selection_id: str
    seed: int
    repetition: int
    total_repetitions: int
    original_failure_ref: str = ""

    def __post_init__(self) -> None:
        if not self.selection_id or self.total_repetitions < 1 or self.repetition < 1 or self.repetition > self.total_repetitions:
            raise ValueError("invalid shuffle/repeat diagnostic identity")

    def as_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "seed": int(self.seed),
            "repetition": int(self.repetition),
            "total_repetitions": int(self.total_repetitions),
            "original_failure_ref": self.original_failure_ref,
            "diagnostic_only": True,
            "retry_semantics": False,
        }


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    __test__ = False
    check_id: str
    owner: str
    reason: str
    expires_at: str
    issue_ref: str = ""

    def __post_init__(self) -> None:
        if not self.check_id or not self.owner or not self.reason or not self.expires_at:
            raise ValueError("quarantine requires check, owner, reason, and expiry")
        _parse_time(self.expires_at)

    def active(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return _parse_time(self.expires_at) > current.astimezone(timezone.utc)

    def disposition(self, outcome: str, *, now: datetime | None = None) -> dict[str, Any]:
        active = self.active(now=now)
        return {
            "check_id": self.check_id,
            "outcome": str(outcome),
            "quarantined": active,
            "owner": self.owner,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "issue_ref": self.issue_ref,
            "truth_preserved": True,
            "final_status": str(outcome),
        }


def _parse_time(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("quarantine expiry must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def presentation_projection(
    outcomes: Mapping[str, str],
    *,
    failure_clusters: Sequence[Mapping[str, Any]] = (),
    profile_ref: str = "",
    raw_result_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    rows = {str(k): str(v) for k, v in outcomes.items()}
    counts: dict[str, int] = {}
    for value in rows.values():
        counts[value] = counts.get(value, 0) + 1
    raw_refs = MappingProxyType(dict(raw_result_refs or {}))
    missing_refs = sorted(set(rows) - set(raw_refs)) if raw_refs else []
    return {
        "schema_version": 1,
        "authority": "TEST-facts/PRES-rendering",
        "counts": dict(sorted(counts.items())),
        "failure_clusters": [dict(cluster) for cluster in failure_clusters],
        "profile_ref": profile_ref,
        "raw_result_refs": dict(raw_refs),
        "raw_outcomes_preserved": not missing_refs,
        "missing_raw_result_refs": missing_refs,
        "presentation_owns_test_truth": False,
    }


def roadmap_evidence_binding(*, gate_id: str, check_ids: Iterable[str], receipt_refs: Iterable[str], rer_refs: Iterable[str] = ()) -> dict[str, Any]:
    checks = tuple(sorted(_tuple(check_ids)))
    receipts = tuple(sorted(_tuple(receipt_refs)))
    reusable = tuple(sorted(_tuple(rer_refs)))
    if not gate_id or not checks or not receipts:
        raise ValueError("roadmap evidence binding requires gate, stable checks, and receipts")
    if any("/" in check or check.endswith(".py") for check in checks):
        raise ValueError("RMP bindings must use stable check IDs, not mutable pytest paths")
    payload = {
        "schema_version": 1,
        "authority": "RMP-consumer",
        "gate_id": gate_id,
        "check_ids": list(checks),
        "receipt_refs": list(receipts),
        "rer_refs": list(reusable),
        "pytest_paths": [],
    }
    payload["binding_id"] = "rmp-test-" + _digest(payload)[:24]
    return payload


def dogfood_lane_plan(*, fast_group: str, system_group: str, full_group: str, delegated_group: str = "") -> dict[str, Any]:
    groups = {"fast": fast_group, "system": system_group, "full": full_group}
    if delegated_group:
        groups["delegated"] = delegated_group
    if any(not str(value).strip() for value in groups.values()):
        raise ValueError("dogfood lane groups must be explicit")
    return {
        "schema_version": 1,
        "groups": groups,
        "selection_authority": "TEST",
        "execution_authority": "EXO",
        "delegation_authority": "DIST" if delegated_group else "",
        "gate_policy_authority": "RMP",
    }


def obligation_disposition(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    normalized: list[dict[str, str]] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for raw in rows:
        item_id = str(raw.get("id") or "").strip()
        status = str(raw.get("status") or "").strip().upper()
        owner = str(raw.get("owner") or "TEST").strip()
        evidence = str(raw.get("evidence") or "").strip()
        if not item_id:
            raise ValueError("obligation row requires id")
        if item_id in seen:
            duplicates.append(item_id)
        seen.add(item_id)
        normalized.append({"id": item_id, "status": status, "owner": owner, "evidence": evidence})
        if status not in _ALLOWED_FINAL_DISPOSITIONS or not evidence:
            unresolved.append(item_id)
    return {
        "schema_version": 1,
        "rows": normalized,
        "unresolved": sorted(set(unresolved)),
        "duplicates": sorted(set(duplicates)),
        "complete": not unresolved and not duplicates,
        "allowed_final_dispositions": sorted(_ALLOWED_FINAL_DISPOSITIONS),
    }


def repo_local_report_projection(*, run_id: str, junit_ref: str, local_path: str = "") -> dict[str, Any]:
    if not run_id or not junit_ref:
        raise ValueError("test report projection requires canonical run and JUnit evidence reference")
    return {
        "run_id": run_id,
        "junit_ref": junit_ref,
        "local_projection": local_path,
        "canonical_history_authority": "RUN-V3",
        "local_projection_replaceable": True,
    }
