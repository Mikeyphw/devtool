from __future__ import annotations

"""OCI-06 resilience/provenance helpers for SSH-owned OCI execution.

This module deliberately contains only pure metadata helpers.  tmux/SSH remain
responsible for durable remote process ownership; OCI contributes canonical
provider/image/source/cache identity that resume, cancel, recover and artifact
return can target without persisting secrets.
"""

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping


_SECRET_KEY_RE = re.compile(
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|PASS_PHRASE|PASSPHRASE|API[_-]?KEY|PRIVATE[_-]?KEY|CREDENTIAL|AUTH|SIGNING|KEYSTORE|STORE_PASSWORD|KEY_PASSWORD|DOCKER_AUTH)",
    re.IGNORECASE,
)
_PHASE_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OCI_PROVENANCE_KEYS: tuple[str, ...] = (
    "execution_mode",
    "execution_provider",
    "ssh_profile",
    "ssh_destination",
    "ssh_workspace_id",
    "ssh_workspace_instance_id",
    "ssh_workspace_path",
    "ssh_source_sync_id",
    "ssh_source_revision_digest",
    "ssh_session",
    "ssh_return_manifest",
    "ssh_return_manifest_sha256",
    "oci_runtime",
    "oci_container",
    "oci_architecture",
    "oci_image_reference",
    "oci_image_digest",
    "oci_image_archive_sha256",
    "oci_image_archive_size",
    "oci_definition_digest",
    "oci_build_context_sha256",
    "oci_guest_workspace",
    "oci_guest_cache",
    "oci_source_sync_id",
    "oci_dry_run",
    "oci_phase_node_id",
)


def is_sensitive_name(name: str) -> bool:
    return bool(_SECRET_KEY_RE.search(str(name or "")))


def stable_json_digest(value: Mapping[str, Any] | list[Any] | tuple[Any, ...] | str) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if value is None:
        return ""
    if isinstance(value, (int, float, str)):
        return str(value)
    return stable_json_digest(value)


def redacted_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return metadata safe to persist in local/remote records.

    Sensitive names are recorded only as REDACTED markers.  Non-scalar values are
    represented by deterministic digests unless they are expected structured
    public evidence, such as source revision.
    """
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        name = str(key)
        if is_sensitive_name(name):
            result[name] = "<redacted>"
            continue
        if isinstance(value, Mapping):
            if name in {"ssh_source_revision"}:
                result[name] = dict(value)
                result[name + "_digest"] = stable_json_digest(value)
            else:
                result[name + "_digest"] = stable_json_digest(value)
            continue
        if isinstance(value, (list, tuple, set)):
            result[name + "_digest"] = stable_json_digest(list(value))
            continue
        text = _scalar(value)
        if _SECRET_KEY_RE.search(text):
            # This catches accidental values such as literal JSON auth payloads
            # stored under public-looking keys.  Do not persist the content.
            result[name] = "<redacted>"
        else:
            result[name] = text
    return result


def phase_node_id(run_id: str, session: str, *, provider: str = "ssh-proot-oci") -> str:
    body = f"{provider}|{run_id}|{session}"
    return "oci-phase-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:24]


def phase_metadata_lines(metadata: Mapping[str, Any], *, run_id: str = "", session: str = "") -> tuple[str, ...]:
    safe = redacted_metadata(metadata)
    if run_id and session and not safe.get("oci_phase_node_id"):
        safe["oci_phase_node_id"] = phase_node_id(run_id, session, provider=str(safe.get("execution_provider") or "ssh-proot-oci"))
    lines: list[str] = []
    for key in sorted(OCI_PROVENANCE_KEYS):
        if key not in safe:
            continue
        if not _PHASE_KEY_RE.fullmatch(key):
            continue
        value = str(safe[key])
        if "\n" in value or "\r" in value or "\x00" in value:
            value = stable_json_digest(value)
        lines.append(f"{key}={value}")
    return tuple(lines)


def ensure_no_secret_values(payload: Mapping[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if "top-secret" in text or "super-secret" in text or "PD_DOCKER_AUTH=" in text:
        raise ValueError("OCI metadata attempted to persist secret credential material")
    for key, value in payload.items():
        if is_sensitive_name(str(key)) and value not in ("", "<redacted>", [], {}, None):
            raise ValueError(f"OCI metadata contains unredacted sensitive key {key!r}")


def producer_identity(
    *,
    profile: str,
    destination: str,
    run_id: str,
    session: str,
    source_sync_id: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = redacted_metadata(metadata or {})
    producer = {
        "schema_version": 1,
        "transport": "ssh",
        "profile": str(profile or ""),
        "destination": str(destination or ""),
        "run_id": str(run_id or ""),
        "session": str(session or ""),
        "source_sync_id": str(source_sync_id or meta.get("ssh_source_sync_id") or meta.get("oci_source_sync_id") or ""),
        "execution_provider": str(meta.get("execution_provider") or meta.get("execution_mode") or "ssh-command"),
        "phase_node_id": str(meta.get("oci_phase_node_id") or phase_node_id(str(run_id or ""), str(session or ""), provider=str(meta.get("execution_provider") or meta.get("execution_mode") or "ssh-command"))),
    }
    if producer["execution_provider"] == "ssh-proot-oci":
        for key in (
            "oci_runtime", "oci_container", "oci_architecture", "oci_image_reference",
            "oci_image_digest", "oci_image_archive_sha256", "oci_image_archive_size",
            "oci_definition_digest", "oci_build_context_sha256", "oci_guest_workspace",
            "oci_guest_cache", "ssh_source_revision_digest",
        ):
            value = meta.get(key)
            if value not in (None, ""):
                producer[key] = value
    ensure_no_secret_values(producer)
    producer["identity_sha256"] = stable_json_digest({k: v for k, v in producer.items() if k != "identity_sha256"})
    return producer


def attach_producer_identity(payload: dict[str, Any], producer: Mapping[str, Any]) -> None:
    payload["producer"] = dict(producer)
    payload["producer_sha256"] = stable_json_digest(producer)


def validate_producer_identity(manifest: Mapping[str, Any]) -> None:
    producer = manifest.get("producer")
    if not producer:
        # Native/legacy return bundles remain readable for compatibility.
        return
    if not isinstance(producer, Mapping):
        raise ValueError("SSH return manifest producer identity is not an object")
    ensure_no_secret_values(producer)
    expected = str(manifest.get("producer_sha256") or "")
    actual = stable_json_digest(producer)
    if expected != actual:
        raise ValueError("SSH return manifest producer identity digest mismatch")
    if str(producer.get("execution_provider") or "") == "ssh-proot-oci":
        required = (
            "phase_node_id", "source_sync_id", "oci_container", "oci_image_reference",
            "oci_image_digest", "oci_definition_digest", "oci_build_context_sha256",
            "oci_guest_workspace", "oci_guest_cache",
        )
        missing = [key for key in required if not str(producer.get(key) or "")]
        if missing:
            raise ValueError("SSH return manifest OCI producer identity is incomplete: " + ", ".join(missing))
