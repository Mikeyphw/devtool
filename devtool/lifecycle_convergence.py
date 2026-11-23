from __future__ import annotations

"""LIFE-X04 lifecycle convergence/adoption projections.

This module composes the existing LIFE-X01/X02/X03 authorities.  It does not
apply mutations, delete resources, transport secrets, or invent recovery state;
it produces deterministic plans/evidence that existing executors can consume.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LifecycleProjection:
    kind: str
    identity: str
    status: str
    facts: Mapping[str, Any]
    actions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "identity": self.identity, "status": self.status,
                "facts": dict(self.facts), "actions": list(self.actions)}


def transaction_projection(transaction: Mapping[str, Any]) -> LifecycleProjection:
    """Explain/diff/rollback-plan projection without manufacturing checkpoints."""
    txid = str(transaction.get("id") or transaction.get("transaction_id") or "").strip()
    if not txid:
        raise ValueError("transaction identity is required")
    checkpoints = tuple(str(x) for x in transaction.get("checkpoints", ()) if str(x))
    changed = tuple(sorted(str(x) for x in transaction.get("changed_paths", ()) if str(x)))
    mutated = bool(transaction.get("mutation_started") or changed)
    rollback_ready = bool(checkpoints) if mutated else True
    status = "recoverable" if rollback_ready else "blocked"
    actions = ("explain", "diff", "rollback-plan", "recovery-doctor")
    return LifecycleProjection("transaction", txid, status, {
        "mutation_started": mutated, "changed_paths": list(changed),
        "checkpoint_count": len(checkpoints), "rollback_ready": rollback_ready,
        "recovery_identity": str(transaction.get("resume_target_identity") or ""),
    }, actions)


def delivery_projection(plan: Mapping[str, Any]) -> LifecycleProjection:
    artifact = dict(plan.get("artifact") or {})
    qualification = dict(plan.get("qualification") or {})
    aid = str(artifact.get("artifact_id") or plan.get("plan_id") or "").strip()
    if not aid:
        raise ValueError("artifact identity is required")
    qualified = bool(qualification.get("qualified"))
    executable = bool(plan.get("executable", qualified))
    return LifecycleProjection("delivery", aid, "qualified" if qualified and executable else "blocked", {
        "qualified": qualified, "executable": executable,
        "assurance": artifact.get("assurance", "legacy"),
        "semantic_sha256": (artifact.get("identities") or {}).get("semantic_sha256", ""),
        "receipt_id": qualification.get("receipt_id", ""),
    }, ("inspect", "qualification", "apply" if executable else "remediate"))



# Only opaque, typed references may cross LIFE boundaries. Names here identify
# a configured provider, not a path, command, token, or secret value.
_SECRET_REF_TYPES = {
    'fd': re.compile(r'[0-9]{1,6}\Z'),
    'local': re.compile(r'[A-Z_][A-Z0-9_]{0,127}\Z'),
    'keyring': re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z'),
    'secret-provider': re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z'),
}


def canonical_secret_reference(value: str | Mapping[str, Any] | None) -> str:
    if value is None or value == '':
        return ''
    if isinstance(value, Mapping):
        if set(value) != {'kind', 'reference'}:
            raise ValueError('secret reference fields are not canonical')
        kind, ident = value['kind'], value['reference']
        if not isinstance(kind, str) or not isinstance(ident, str):
            raise ValueError('secret reference type and identity must be strings')
        value = kind + ':' + ident
    if not isinstance(value, str) or ':' not in value:
        raise ValueError('secret provider reference must be typed')
    kind, ident = value.split(':', 1)
    matcher = _SECRET_REF_TYPES.get(kind)
    if matcher is None or not matcher.fullmatch(ident):
        raise ValueError('secret provider reference is not a safe opaque identity')
    return value



_SECRET_VALUE = re.compile(
    r'(?i)(?:\b(?:password|passphrase|token|private[_-]?key|authorization|api[_-]?key|secret)\s*[:=]\s*\S+'
    r'|\bgh[psuo]_[A-Za-z0-9_]{12,}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----'
    r'|https?://[^/\s:@]+:[^/\s@]+@)'
)


def _refuse_embedded_secret_material(value: Any) -> None:
    """Fail closed before hashing/serializing an untrusted LIFE envelope."""
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('lifecycle evidence contains non-string keys')
            lowered = key.casefold()
            if lowered == 'secret_provider':
                canonical_secret_reference(item)
                continue
            if lowered == 'secret_provider_ref':
                canonical_secret_reference(item)
                continue
            if any(word in lowered for word in (
                    'password', 'passphrase', 'private_key', 'private-key', 'token',
                    'authorization', 'api_key', 'api-key')) and item not in (None, '', False):
                raise ValueError('lifecycle evidence contains a sensitive field')
            _refuse_embedded_secret_material(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _refuse_embedded_secret_material(item)
    elif isinstance(value, str):
        if _SECRET_VALUE.search(value) or '\x00' in value or any(
                x in value for x in ('\r', '\n')):
            raise ValueError('lifecycle evidence contains secret-like content')
    elif value is None or type(value) in (bool, int, float):
        return
    else:
        raise ValueError('lifecycle evidence contains an unsupported value type')


def signing_projection(identity: Mapping[str, Any]) -> LifecycleProjection:
    forbidden = {"password", "passphrase", "private_key", "token", "secret"}
    for key, value in identity.items():
        lowered = str(key).lower()
        if any(word in lowered for word in forbidden) and lowered not in {"secret_provider", "secret_provider_identity"} and value not in (None, "", False):
            raise ValueError(f"secret material must not enter lifecycle evidence: {key}")
    iid = str(identity.get("identity_id") or "").strip()
    if not iid:
        raise ValueError("signing identity is required")
    provider = identity.get("secret_provider")
    _refuse_embedded_secret_material(identity)
    if provider is not None:
        # Preserve legacy public object shape while rejecting unbounded values.
        canonical_secret_reference(provider)
    return LifecycleProjection("signing", iid, "ready" if identity.get("key_reference") else "incomplete", {
        "provider": identity.get("provider", ""), "key_reference": identity.get("key_reference", ""),
        "certificate_sha256": identity.get("certificate_sha256", ""),
        "secret_provider": provider, "lifecycle": identity.get("lifecycle", "durable"),
    }, ("list", "verify-continuity", "rotate-with-authorization"))


def repository_pack_projection(value: Mapping[str, Any]) -> LifecycleProjection:
    identity = str(value.get("pack_id") or value.get("plan_id") or value.get("request_id") or "").strip()
    if not identity:
        raise ValueError("repository-pack identity is required")
    return LifecycleProjection("repository-pack", identity, "published" if value.get("published") else "planned", {
        "output": value.get("output", ""), "sha256": value.get("sha256", ""),
        "verified": bool(value.get("verified")), "published": bool(value.get("published")),
        "pin": bool(value.get("pin")), "retention": value.get("retention", "policy"),
    }, ("status", "list", "pin", "verify"))


def cleanup_projection(plan: Mapping[str, Any]) -> LifecycleProjection:
    pid = str(plan.get("plan_id") or "").strip()
    if not pid:
        raise ValueError("cleanup plan identity is required")
    candidates = tuple(plan.get("candidates") or ())
    blocked = sum(1 for item in candidates if isinstance(item, Mapping) and not bool(item.get("eligible", item.get("authorized", False))))
    reclaim = sum(int(item.get("reclaimable_bytes", 0) or 0) for item in candidates if isinstance(item, Mapping))
    return LifecycleProjection("cleanup", pid, "blocked" if blocked else "ready", {
        "candidate_count": len(candidates), "blocked_count": blocked,
        "reclaimable_bytes": reclaim, "destination": plan.get("destination", "local"),
    }, ("explain", "doctor", "reclaim-plan", "apply" if not blocked else "remediate"))


def remote_lifecycle_envelope(*, repository_identity: str, operation: str, projection: LifecycleProjection,
                              signing_identity_id: str = "", secret_provider_ref: str = "") -> dict[str, Any]:
    """Transport identity/references only; never secret bytes or flattened repo identity."""
    if not repository_identity.strip():
        raise ValueError("repository identity is required")
    secret_provider_ref = canonical_secret_reference(secret_provider_ref)
    _refuse_embedded_secret_material(projection.as_dict())
    payload = {
        "schema_version": 1, "repository_identity": repository_identity,
        "operation": operation, "projection": projection.as_dict(),
        "signing_identity_id": signing_identity_id, "secret_provider_ref": secret_provider_ref,
    }
    payload["envelope_id"] = "life:" + _digest(payload)[:24]
    return payload


def federation_pack_selection(repositories: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    seen = set()
    for repo in repositories:
        identity = str(repo.get("repository_identity") or repo.get("fingerprint") or "").strip()
        if not identity or identity in seen:
            raise ValueError("federation pack members require unique repository identities")
        seen.add(identity)
        rows.append({"repository_identity": identity, "root": str(repo.get("root") or ""), "selection": str(repo.get("selection") or "tracked")})
    payload = {"schema_version": 1, "repositories": rows, "identity_flattening": False}
    payload["selection_id"] = "fedpack:" + _digest(payload)[:24]
    return payload


def legacy_migration_record(*, source_kind: str, source_identity: str, canonical_identity: str, disposition: str) -> dict[str, Any]:
    if disposition not in {"migrated", "retained-read-only", "quarantined", "retired"}:
        raise ValueError("unsupported legacy disposition")
    payload = {"source_kind": source_kind, "source_identity": source_identity,
               "canonical_identity": canonical_identity, "disposition": disposition}
    payload["migration_id"] = "migration:" + _digest(payload)[:24]
    return payload


def dogfood_matrix() -> tuple[dict[str, str], ...]:
    return tuple({"scenario": scenario, "authority": authority} for scenario, authority in (
        ("overlay-delivery", "artifact-lifecycle"), ("bundle-delivery", "artifact-lifecycle"),
        ("snapshot-delivery", "artifact-lifecycle"), ("repository-pack", "repository-pack"),
        ("signing", "artifact-trust"), ("ssh-cleanup", "cleanup-lifecycle"),
        ("federation-cleanup", "cleanup-lifecycle"), ("transaction-recovery", "transactions"),
    ))


def exported_obligations() -> dict[str, tuple[str, ...]]:
    return {
        "PRES-X08": ("render lifecycle projections; do not own lifecycle state",),
        "DIST-G3": ("prove remote lifecycle envelopes preserve repository/signing identity",),
        "LIFE-G3": ("authoritatively qualify lifecycle convergence and self-host dogfood",),
        "RMP": ("retain attestation semantics outside LIFE ownership",),
        "EXEC": ("retain reusable-execution-result semantics outside LIFE ownership",),
        "CORE": ("retain RUN garbage-collection authority outside LIFE ownership",),
    }
