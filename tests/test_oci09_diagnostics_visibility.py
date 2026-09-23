from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.oci_diagnostics import (
    OCI09_DOC_PATH,
    diagnostic_rows,
    diagnostics_payload,
    ensure_visibility_payload_has_no_secrets,
    oci09_phase_metadata,
    phase_visibility_payload,
    redact_visibility_value,
)
from devtool.cli import build_parser


def _phase_metadata() -> dict[str, object]:
    return {
        "execution_provider": "ssh-proot-oci",
        "ssh_profile": "tablet",
        "ssh_destination": "u0_a123@tablet.lan",
        "ssh_workspace_id": "repo-abc",
        "ssh_workspace_path": "/home/u/.cache/devtool/workspaces/repo-abc",
        "ssh_source_sync_id": "sync-1",
        "ssh_session": "devtool-tablet-run",
        "oci_phase_node_id": "oci-phase-123",
        "oci_image_reference": "devtool/android-build:arm64-api36-v2",
        "oci_image_digest": "sha256:image",
        "oci_definition_digest": "sha256:def",
        "oci_build_context_sha256": "sha256:ctx",
        "oci_guest_workspace": "/workspace",
        "oci_guest_cache": "/devtool-cache",
        "oci08_source_sync_policy": "reuse-verified-managed-source",
        "oci08_source_sync_id": "sync-1",
        "oci08_cache_namespace": "oci-cache-123",
        "oci08_cache_source_sync_excluded": True,
        "oci08_worker_reuse_enabled": True,
        "oci08_worker_reuse_policy": "single-preparation-per-run",
        "oci08_worker_reuse_phase_count": 3,
        "oci08_worker_reuse_lease_id": "lease-1",
        "oci08_worker_reuse_detail": "one named managed worker preparation is reused for all OCI command phases",
    }


def test_oci09_phase_visibility_groups_provider_image_cache_worker_and_workspace() -> None:
    payload = phase_visibility_payload(_phase_metadata(), run_id="run-1", profile="tablet")
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert payload["phase_node_id"] == "oci-phase-123"
    card_ids = {card["id"] for card in payload["cards"]}
    assert {
        "oci-provider",
        "oci-image",
        "oci-source-cache",
        "oci-worker-reuse",
        "oci-workspace",
    } <= card_ids
    assert payload["diagnostics"][0]["token"] == "OCI_VISIBILITY_READY"
    assert payload["docs"]["diagnostics"].endswith("#diagnostics-and-hud-fields")


def test_oci09_phase_metadata_is_compact_and_hud_safe() -> None:
    metadata = oci09_phase_metadata(_phase_metadata())
    assert metadata["oci09_schema_version"] == 1
    assert metadata["oci09_visibility_state"] == "ready"
    assert metadata["oci09_hud_card_count"] >= 5
    assert len(str(metadata["oci09_hud_cards_sha256"])) == 64
    assert metadata["oci09_docs"] == OCI09_DOC_PATH


def test_oci09_diagnostics_payload_combines_doctor_status_and_phase_visibility() -> None:
    payload = diagnostics_payload(
        run_id="run-1",
        profile="tablet",
        destination="u0_a123@tablet.lan",
        doctor={
            "ok": True,
            "detail": "PRoot-Distro runtime ready",
            "runtime": {
                "platform": "termux",
                "architecture": "aarch64",
                "container_count": 1,
                "image_count": 1,
                "cache_kib": 2048,
                "nested_proot": False,
                "inventory_healthy": True,
            },
        },
        image_status={
            "ok": True,
            "status": "ready",
            "detail": "canonical image/container ready",
            "reference": "devtool/android-build:arm64-api36-v2",
            "container": "devtool-android-api36",
            "image_cached": True,
            "container_installed": True,
        },
        phase_metadata=_phase_metadata(),
    )
    assert payload["ok"] is True
    assert payload["summary"]["card_count"] >= 7
    rows = dict(diagnostic_rows(payload))
    assert rows["status"] == "READY"
    assert rows["docs"] == OCI09_DOC_PATH
    json.dumps(payload)


def test_oci09_reports_runtime_attention_without_hiding_next_action() -> None:
    payload = diagnostics_payload(
        run_id="run-2",
        profile="tablet",
        doctor={
            "ok": False,
            "detail": "PRoot-Distro runtime is not ready; run devtool oci bootstrap PROFILE",
            "runtime": {"platform": "termux", "nested_proot": False, "inventory_healthy": False},
        },
        image_status={
            "ok": False,
            "status": "runtime-blocked",
            "recommended_action": "bootstrap-runtime",
            "detail": "PRoot-Distro runtime is not ready; run devtool oci bootstrap first",
        },
    )
    assert payload["ok"] is False
    tokens = {item["token"] for item in payload["diagnostics"]}
    assert "OCI_RUNTIME_BLOCKED" in tokens
    assert "OCI_IMAGE_NEEDS_ATTENTION" in tokens
    assert payload["summary"]["attention_count"] >= 1


def test_oci09_visibility_redacts_secret_names_and_values() -> None:
    redacted = redact_visibility_value({
        "PD_DOCKER_AUTH": "docker-auth-token",
        "normal": "value",
        "note": "top-secret should not persist",
    })
    assert redacted["PD_DOCKER_AUTH"] == "<redacted>"
    assert redacted["note"] == "<redacted>"
    assert redacted["normal"] == "value"
    with pytest.raises(ValueError):
        ensure_visibility_payload_has_no_secrets({"safe": "PD_DOCKER_AUTH=top-secret"})


def test_oci_diagnostics_parser_surface_is_documented() -> None:
    args = build_parser().parse_args(["oci", "diagnostics", "tablet", "--format", "json"])
    assert args.command == "oci"
    assert args.oci_command == "diagnostics"
    assert args.profile == "tablet"
    assert args.format == "json"


def test_oci09_operator_documentation_exists_and_covers_required_topics() -> None:
    doc = Path(OCI09_DOC_PATH)
    text = doc.read_text(encoding="utf-8")
    assert "Image update policy" in text
    assert "Source-sync, cache, and worker reuse" in text
    assert "Diagnostics and HUD fields" in text
    assert "Secret handling" in text
