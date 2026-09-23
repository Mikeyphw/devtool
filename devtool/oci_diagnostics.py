from __future__ import annotations

"""OCI-09 diagnostics, HUD visibility, and operator documentation helpers.

This module is intentionally pure: it reads already-produced OCI/SSH metadata
and lifecycle payloads, redacts sensitive values, and produces compact summaries
for CLI, HUD, and machine consumers. It must not probe remote workers or create a
parallel execution authority.
"""

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Iterable, Mapping

from .oci_resilience import is_sensitive_name, stable_json_digest

OCI09_SCHEMA_VERSION = 1
OCI09_DOC_PATH = "docs/architecture/oci-proot-operations.md"
_SECRET_MARKER = "<redacted>"


@dataclass(frozen=True, slots=True)
class OciHudCard:
    id: str
    title: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OciDiagnosticItem:
    id: str
    severity: str
    token: str
    what_happened: str
    next_actions: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["next_actions"] = list(self.next_actions)
        return value


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on", "ok", "ready"}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = _text(value)
    if not text:
        return ""
    lowered = text.lower()
    if "pd_docker_auth" in lowered or "top-secret" in lowered or "super-secret" in lowered:
        return _SECRET_MARKER
    if len(text) > 240:
        return {"sha256": stable_json_digest(text), "redacted_long_value": True}
    return text


def redact_visibility_value(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe diagnostic value with credential material removed."""

    if key and is_sensitive_name(key):
        return _SECRET_MARKER
    if isinstance(value, Mapping):
        return {str(k): redact_visibility_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_visibility_value(v) for v in value]
    return _safe_scalar(value)


def ensure_visibility_payload_has_no_secrets(payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    forbidden = ("top-secret", "super-secret", "PD_DOCKER_AUTH=", "docker-auth-token")
    if any(marker in text for marker in forbidden):
        raise ValueError("OCI-09 visibility payload contains unredacted secret material")
    for key, value in payload.items():
        if is_sensitive_name(str(key)) and value != _SECRET_MARKER:
            raise ValueError(f"OCI-09 visibility payload exposes sensitive key {key!r}")


def _doc_links() -> dict[str, str]:
    return {
        "operations": OCI09_DOC_PATH,
        "update_policy": OCI09_DOC_PATH + "#image-update-policy",
        "cache_policy": OCI09_DOC_PATH + "#source-sync-cache-and-worker-reuse",
        "diagnostics": OCI09_DOC_PATH + "#diagnostics-and-hud-fields",
        "recovery": OCI09_DOC_PATH + "#resilience-and-artifact-return",
    }


def _metadata_evidence(metadata: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in keys:
        if key in metadata and metadata.get(key) not in (None, "", [], {}, ()):  # keep False/0
            result[key] = redact_visibility_value(metadata.get(key), key=key)
    return result


def _phase_cards(metadata: Mapping[str, Any]) -> list[OciHudCard]:
    provider = _text(metadata.get("execution_provider") or metadata.get("execution_mode") or "ssh-proot-oci")
    image_ref = _text(metadata.get("oci_image_reference"))
    image_digest = _text(metadata.get("oci_image_digest"))
    workspace = _text(metadata.get("ssh_workspace_id") or metadata.get("oci08_workspace_id"))
    cache_ns = _text(metadata.get("oci08_cache_namespace"))
    worker_reuse = _bool(metadata.get("oci08_worker_reuse_enabled"))
    source_policy = _text(metadata.get("oci08_source_sync_policy"))
    cards = [
        OciHudCard(
            id="oci-provider",
            title="OCI provider",
            status="ready" if provider == "ssh-proot-oci" else "attention",
            detail=provider or "unknown provider",
            evidence=_metadata_evidence(metadata, ("oci_phase_node_id", "ssh_session", "ssh_profile")),
        ),
        OciHudCard(
            id="oci-image",
            title="Image provenance",
            status="ready" if image_ref and image_digest else "attention",
            detail=(image_ref + (f" @ {image_digest}" if image_digest else "")) if image_ref else "image provenance missing",
            evidence=_metadata_evidence(metadata, (
                "oci_image_reference", "oci_image_digest", "oci_definition_digest", "oci_build_context_sha256",
            )),
        ),
        OciHudCard(
            id="oci-source-cache",
            title="Source/cache policy",
            status="ready" if source_policy and cache_ns else "attention",
            detail=(f"{source_policy}; cache {cache_ns}" if source_policy or cache_ns else "source/cache visibility missing"),
            evidence=_metadata_evidence(metadata, (
                "oci08_source_sync_policy", "oci08_source_sync_id", "oci08_cache_namespace", "oci08_cache_source_sync_excluded",
            )),
        ),
        OciHudCard(
            id="oci-worker-reuse",
            title="Worker reuse",
            status="ready" if worker_reuse else "disabled",
            detail=_text(metadata.get("oci08_worker_reuse_detail")) or ("enabled" if worker_reuse else "disabled"),
            evidence=_metadata_evidence(metadata, (
                "oci08_worker_reuse_policy", "oci08_worker_reuse_phase_count", "oci08_worker_reuse_lease_id",
            )),
        ),
    ]
    if workspace:
        cards.append(OciHudCard(
            id="oci-workspace",
            title="Managed workspace",
            status="ready",
            detail=workspace,
            evidence=_metadata_evidence(metadata, ("ssh_workspace_id", "ssh_workspace_path", "oci_guest_workspace", "oci_guest_cache")),
        ))
    return cards


def phase_visibility_payload(metadata: Mapping[str, Any], *, run_id: str = "", profile: str = "") -> dict[str, Any]:
    """Build a redacted OCI phase visibility payload from existing metadata."""

    safe_metadata = redact_visibility_value(dict(metadata))
    cards = _phase_cards(metadata)
    attention = [card for card in cards if card.status == "attention"]
    diagnostics = [
        OciDiagnosticItem(
            id="oci09-phase-visibility",
            severity="info" if not attention else "warning",
            token="OCI_VISIBILITY_READY" if not attention else "OCI_VISIBILITY_INCOMPLETE",
            what_happened=(
                "OCI phase provenance, source/cache policy, and worker reuse are visible to CLI/HUD consumers"
                if not attention else "OCI phase visibility is missing one or more provenance fields"
            ),
            next_actions=(
                ("Run `devtool oci diagnostics PROFILE --format json` to inspect the worker-facing summary",)
                if not attention else
                ("Re-run the command with a named managed ssh-proot-oci profile", "Check image validation and managed workspace preparation evidence")
            ),
            evidence={"attention_cards": [card.id for card in attention]},
        ).as_dict()
    ]
    payload = {
        "schema_version": OCI09_SCHEMA_VERSION,
        "kind": "oci-phase-visibility",
        "ok": not attention,
        "run_id": run_id or _text(metadata.get("run_id")),
        "profile": profile or _text(metadata.get("ssh_profile")),
        "provider": _text(metadata.get("execution_provider") or metadata.get("execution_mode") or "ssh-proot-oci"),
        "phase_node_id": _text(metadata.get("oci_phase_node_id")),
        "cards": [card.as_dict() for card in cards],
        "diagnostics": diagnostics,
        "metadata_digest": stable_json_digest(safe_metadata),
        "docs": _doc_links(),
        "detail": "OCI visibility summary is derived from existing durable phase metadata",
    }
    ensure_visibility_payload_has_no_secrets(payload)
    return payload


def _status_card(image_status: Mapping[str, Any]) -> OciHudCard:
    ok = _bool(image_status.get("ok"))
    return OciHudCard(
        id="oci-image-status",
        title="Image/container status",
        status="ready" if ok else "attention",
        detail=_text(image_status.get("detail") or image_status.get("status") or ("ready" if ok else "blocked")),
        evidence={
            "status": redact_visibility_value(image_status.get("status"), key="status"),
            "reference": redact_visibility_value(image_status.get("reference"), key="reference"),
            "container": redact_visibility_value(image_status.get("container"), key="container"),
            "image_cached": bool(image_status.get("image_cached")),
            "container_installed": bool(image_status.get("container_installed")),
            "recommended_action": redact_visibility_value(image_status.get("recommended_action"), key="recommended_action"),
        },
    )


def _runtime_card(doctor: Mapping[str, Any]) -> OciHudCard:
    runtime = doctor.get("runtime") if isinstance(doctor.get("runtime"), Mapping) else {}
    ok = _bool(doctor.get("ok"))
    return OciHudCard(
        id="oci-runtime",
        title="PRoot-Distro runtime",
        status="ready" if ok else "attention",
        detail=_text(doctor.get("detail") or ("runtime ready" if ok else "runtime blocked")),
        evidence={
            "platform": redact_visibility_value(runtime.get("platform"), key="platform"),
            "architecture": redact_visibility_value(runtime.get("architecture"), key="architecture"),
            "container_count": runtime.get("container_count", 0),
            "image_count": runtime.get("image_count", 0),
            "cache_kib": runtime.get("cache_kib", 0),
            "nested_proot": bool(runtime.get("nested_proot")),
            "inventory_healthy": bool(runtime.get("inventory_healthy")),
        },
    )


def diagnostics_payload(
    *,
    run_id: str,
    profile: str,
    destination: str = "",
    image_status: Mapping[str, Any] | None = None,
    doctor: Mapping[str, Any] | None = None,
    phase_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine non-mutating OCI status/doctor/phase metadata for operators."""

    cards: list[OciHudCard] = []
    diagnostics: list[OciDiagnosticItem] = []
    image_status = image_status or {}
    doctor = doctor or {}
    phase_metadata = phase_metadata or {}
    if doctor:
        runtime_card = _runtime_card(doctor)
        cards.append(runtime_card)
        if runtime_card.status != "ready":
            diagnostics.append(OciDiagnosticItem(
                id="oci09-runtime-attention",
                severity="error",
                token="OCI_RUNTIME_BLOCKED",
                what_happened=runtime_card.detail,
                next_actions=("Run `devtool oci bootstrap PROFILE` or repair proot-distro/proot on the worker",),
                evidence=runtime_card.evidence,
            ))
    if image_status:
        image_card = _status_card(image_status)
        cards.append(image_card)
        if image_card.status != "ready":
            action = _text(image_status.get("recommended_action")) or "run oci update"
            diagnostics.append(OciDiagnosticItem(
                id="oci09-image-attention",
                severity="warning",
                token="OCI_IMAGE_NEEDS_ATTENTION",
                what_happened=image_card.detail,
                next_actions=(f"Recommended action: {action}", "Use `devtool oci update PROFILE --apply` only after reviewing the plan"),
                evidence=image_card.evidence,
            ))
    if phase_metadata:
        phase_payload = phase_visibility_payload(phase_metadata, run_id=run_id, profile=profile)
        cards.extend(OciHudCard(**card) for card in phase_payload.get("cards", []))
        for item in phase_payload.get("diagnostics", []):
            diagnostics.append(OciDiagnosticItem(
                id=str(item.get("id") or "oci09-phase"),
                severity=str(item.get("severity") or "info"),
                token=str(item.get("token") or "OCI_PHASE_VISIBILITY"),
                what_happened=str(item.get("what_happened") or "OCI phase visibility summary"),
                next_actions=tuple(str(v) for v in item.get("next_actions", [])),
                evidence=dict(item.get("evidence") or {}),
            ))
    ok = not any(item.severity == "error" for item in diagnostics)
    payload = {
        "schema_version": OCI09_SCHEMA_VERSION,
        "kind": "oci-diagnostics",
        "ok": ok,
        "run_id": str(run_id or ""),
        "profile": str(profile or ""),
        "destination": redact_visibility_value(destination, key="destination"),
        "cards": [card.as_dict() for card in cards],
        "diagnostics": [item.as_dict() for item in diagnostics],
        "summary": {
            "card_count": len(cards),
            "error_count": sum(1 for item in diagnostics if item.severity == "error"),
            "warning_count": sum(1 for item in diagnostics if item.severity == "warning"),
            "ready_count": sum(1 for card in cards if card.status == "ready"),
            "attention_count": sum(1 for card in cards if card.status == "attention"),
        },
        "docs": _doc_links(),
        "detail": "OCI diagnostics are redacted, non-mutating, and derived from existing lifecycle evidence",
    }
    ensure_visibility_payload_has_no_secrets(payload)
    return payload


def oci09_phase_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return compact fields safe to append to durable phase metadata."""

    payload = phase_visibility_payload(metadata)
    cards = payload.get("cards", [])
    state = "attention" if any(str(card.get("status")) == "attention" for card in cards if isinstance(card, Mapping)) else "ready"
    value = {
        "oci09_schema_version": OCI09_SCHEMA_VERSION,
        "oci09_visibility_state": state,
        "oci09_visibility_summary": payload.get("detail"),
        "oci09_hud_card_count": len(cards),
        "oci09_hud_cards_sha256": stable_json_digest(cards),
        "oci09_docs": OCI09_DOC_PATH,
    }
    ensure_visibility_payload_has_no_secrets(value)
    return value


def diagnostic_rows(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    return [
        ("profile", _text(payload.get("profile")) or "-"),
        ("destination", _text(payload.get("destination")) or "-"),
        ("status", "READY" if payload.get("ok") else "ATTENTION"),
        ("cards", str(summary.get("card_count", 0))),
        ("ready", str(summary.get("ready_count", 0))),
        ("attention", str(summary.get("attention_count", 0))),
        ("warnings", str(summary.get("warning_count", 0))),
        ("errors", str(summary.get("error_count", 0))),
        ("docs", OCI09_DOC_PATH),
        ("detail", _text(payload.get("detail")) or "-"),
    ]
