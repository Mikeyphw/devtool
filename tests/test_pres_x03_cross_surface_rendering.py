from __future__ import annotations

import pytest

from devtool.presentation_surfaces import (
    PresentationConsumer,
    PresentationProfile,
    PresentationSurface,
    assert_bundle_is_reference_only,
    assert_render_is_reference_only,
    build_presentation_bundle,
    cross_surface_parity,
    environment_doctor_projection,
    normalize_consumer,
    render_for_consumer,
    render_projection_view,
    status_from_exo,
    validation_verdict_projection,
)


def sample_projection(status: str = "passed"):
    return status_from_exo([
        {"id": "compile", "ref": "exo:compile", "status": status, "label": "Compile app", "progress": 1.0},
    ])


def test_bundle_renders_machine_terminal_tui_desktop_hud_and_ipc():
    bundle = build_presentation_bundle(sample_projection())
    consumers = [render.consumer for render in bundle.renders]
    assert consumers == [
        PresentationConsumer.MACHINE,
        PresentationConsumer.TERMINAL,
        PresentationConsumer.TUI,
        PresentationConsumer.DESKTOP,
        PresentationConsumer.HUD,
        PresentationConsumer.IPC,
    ]
    assert bundle.parity["consistent"] is True
    assert_bundle_is_reference_only(bundle)


def test_all_rendered_surfaces_share_parity_key_and_refs():
    bundle = build_presentation_bundle(sample_projection("running"))
    keys = {render.parity_key for render in bundle.renders}
    assert len(keys) == 1
    for render in bundle.renders:
        assert [ref.ref for ref in render.refs] == ["exo:compile"]


def test_terminal_render_uses_text_payload_without_new_truth():
    view = render_projection_view(sample_projection(), surface=PresentationSurface.TERMINAL)
    render = render_for_consumer(view, PresentationConsumer.TERMINAL)
    assert render.payload["format"] == "plain-text"
    assert "Execution status" in render.payload["text"]
    assert_render_is_reference_only(render)


def test_tui_render_preserves_sections_actions_and_cursor():
    projection = status_from_exo([{"id": f"n{i}", "ref": f"exo:{i}", "status": "passed"} for i in range(4)], limit=2)
    view = render_projection_view(projection, surface=PresentationSurface.TUI, width=140, profile=PresentationProfile.DETAILED)
    render = render_for_consumer(view, "tui")
    assert render.payload["format"] == "structured-tui"
    assert render.payload["panes"] == 3
    assert render.payload["cursor"] == "exo:1"
    assert any(action["id"] == "next-page" for action in render.payload["actions"])


def test_desktop_render_requests_notification_for_failed_or_active_states():
    failed = render_for_consumer(render_projection_view(sample_projection("failed")), "desktop")
    running = render_for_consumer(render_projection_view(sample_projection("running")), "desktop")
    passed = render_for_consumer(render_projection_view(sample_projection("passed")), "desktop")
    assert failed.payload["notification"] is True
    assert running.payload["notification"] is True
    assert passed.payload["notification"] is False


def test_hud_render_is_compact_but_keeps_primary_action_and_refs():
    render = render_for_consumer(render_projection_view(sample_projection("failed")), "hud")
    assert render.fidelity == "compact"
    assert render.payload["format"] == "android-live-hud"
    assert render.payload["primary_action"]["id"] == "open-evidence"
    assert render.payload["refs"][0]["ref"] == "exo:compile"


def test_ipc_render_exposes_schema_v2_view_digest_and_refs():
    render = render_for_consumer(render_projection_view(sample_projection()), "ipc")
    assert render.payload["format"] == "presentation-view-v2"
    assert render.payload["schema_version"] == 2
    assert render.payload["view_digest"]
    assert render.payload["refs"][0]["ref"] == "exo:compile"


def test_validation_failure_cross_surface_actions_match():
    projection = validation_verdict_projection({"required": ["unit"]}, {"satisfied": [], "refs": ["validation:unit"]})
    bundle = build_presentation_bundle(projection, consumers=["machine", "terminal", "tui", "desktop", "hud", "ipc"])
    intents = bundle.parity["action_intents_by_consumer"]
    assert all("validation_control:rerun_failed" in values for values in intents.values())


def test_environment_secret_redaction_survives_all_consumers():
    projection = environment_doctor_projection([
        {"id": "env", "ref": "env:1", "status": "passed", "detail": {"API_TOKEN": "secret-value"}},
    ])
    bundle = build_presentation_bundle(projection)
    encoded = str(bundle.as_machine())
    assert "secret-value" not in encoded
    assert "<redacted>" in encoded


def test_unknown_consumer_fails_closed():
    with pytest.raises(ValueError):
        normalize_consumer("watch-app")


def test_android_hud_alias_maps_to_hud_consumer():
    assert normalize_consumer("android-hud") == PresentationConsumer.HUD


def test_consumer_surface_for_hud_uses_terminal_layout_contract():
    bundle = build_presentation_bundle(sample_projection(), consumers=["hud"])
    assert bundle.source_view.layout.surface == PresentationSurface.TERMINAL


def test_cross_surface_parity_detects_mixed_keys():
    passed = render_for_consumer(render_projection_view(sample_projection("passed")), "machine")
    failed = render_for_consumer(render_projection_view(sample_projection("failed")), "terminal")
    parity = cross_surface_parity([passed, failed])
    assert parity["consistent"] is False
    assert parity["parity_key"] is None


def test_bundle_machine_contract_is_schema_v3():
    bundle = build_presentation_bundle(sample_projection())
    machine = bundle.as_machine()
    assert machine["schema_version"] == 3
    assert machine["bundle_digest"]
    assert machine["source_view"]["schema_version"] == 2


def test_render_rejects_execution_truth_leak_from_source_view():
    projection = status_from_exo([{"id": "bad", "ref": "exo:bad", "status": "passed", "detail": {"stderr": "copied"}}])
    view = render_projection_view(projection)
    with pytest.raises(AssertionError):
        render_for_consumer(view, "desktop")
