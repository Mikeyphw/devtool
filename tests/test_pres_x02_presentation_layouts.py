from __future__ import annotations

import pytest

from devtool.presentation_surfaces import (
    PresentationProfile,
    PresentationSurface,
    actions_for_projection,
    assert_view_is_reference_only,
    compose_dashboard,
    diagnostic_sections_from_projection,
    environment_doctor_projection,
    render_projection_view,
    resolve_layout_intent,
    resolve_surface_capabilities,
    reuse_explain_projection,
    status_from_exo,
    status_severity,
    validation_verdict_projection,
)


def test_terminal_layout_is_single_pane_and_reference_only():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}], surface=PresentationSurface.TERMINAL)
    view = render_projection_view(projection, surface=PresentationSurface.TERMINAL, width=80)
    assert view.layout.panes == 1
    assert view.layout.density == "standard"
    assert_view_is_reference_only(view)


def test_narrow_tui_uses_compact_single_pane():
    layout = resolve_layout_intent(PresentationSurface.TUI, width=50)
    assert layout.panes == 1
    assert layout.density == "compact"


def test_wide_tui_detailed_uses_three_panes():
    layout = resolve_layout_intent(PresentationSurface.TUI, width=140, profile=PresentationProfile.DETAILED)
    assert layout.panes == 3
    assert layout.density == "comfortable"


def test_desktop_standard_wide_layout_uses_two_panes():
    layout = resolve_layout_intent(PresentationSurface.DESKTOP, width=128)
    assert layout.panes == 2


def test_machine_capabilities_are_structured_not_text_panes():
    caps = {cap.id: cap for cap in resolve_surface_capabilities(PresentationSurface.MACHINE, requested=["structured", "panes", "refs"])}
    assert caps["structured"].supported is True
    assert caps["refs"].supported is True
    assert caps["panes"].supported is False
    assert caps["panes"].fallback == "structured"


def test_default_capabilities_include_actions_sections_and_refs():
    caps = {cap.id for cap in resolve_surface_capabilities(PresentationSurface.TERMINAL)}
    assert caps == {"actions", "refs", "sections"}


def test_failed_validation_sections_include_guidance():
    projection = validation_verdict_projection({"required": ["unit"]}, {"satisfied": [], "refs": ["validation:1"]})
    titles = [section.title for section in diagnostic_sections_from_projection(projection)]
    assert titles[:4] == ["What happened", "Why", "Where", "What you can do"]


def test_running_projection_sections_tell_user_to_watch_or_control():
    projection = status_from_exo([{"id": "gradle", "ref": "exo:gradle", "status": "running"}])
    sections = diagnostic_sections_from_projection(projection)
    assert any("Continue watching" in section.body for section in sections)


def test_success_projection_has_no_fake_failure_action():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    actions = actions_for_projection(projection)
    assert [action.id for action in actions] == ["open-evidence", "copy-summary"]


def test_failed_projection_offers_rerun_failed_intent():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "failed"}])
    actions = actions_for_projection(projection)
    assert any(action.intent == "validation_control:rerun_failed" for action in actions)


def test_running_projection_offers_watch_intent():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "running"}])
    actions = actions_for_projection(projection)
    assert any(action.id == "watch" for action in actions)


def test_truncated_projection_exposes_next_page_action():
    projection = status_from_exo([{"id": f"n{i}", "ref": f"exo:{i}", "status": "passed"} for i in range(5)], limit=2)
    actions = actions_for_projection(projection)
    assert actions[-1].id == "next-page"


def test_view_machine_contract_is_schema_v2_and_keeps_projection_digest():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    view = render_projection_view(projection, surface=PresentationSurface.MACHINE)
    payload = view.as_machine()
    assert payload["schema_version"] == 2
    assert payload["projection"]["projection_digest"]
    assert payload["view_digest"]


def test_view_digest_changes_when_layout_changes():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    compact = render_projection_view(projection, width=50).as_machine()["view_digest"]
    wide = render_projection_view(projection, width=140, profile="detailed").as_machine()["view_digest"]
    assert compact != wide


def test_terminal_text_includes_sections_and_actions():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    text = render_projection_view(projection, surface=PresentationSurface.TERMINAL).terminal_text()
    assert "What happened:" in text
    assert "Actions:" in text
    assert "open-evidence=Open evidence" in text


def test_view_refs_are_deduplicated():
    projection = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    view = render_projection_view(projection)
    refs = [ref.as_dict() for ref in view.refs]
    assert refs == [projection.refs[0].as_dict()]


def test_validation_not_applicable_stays_success_in_view():
    projection = validation_verdict_projection({"required": ["device"]}, {"not_applicable": ["device"], "refs": ["validation:device"]})
    view = render_projection_view(projection)
    assert view.projection.status == "passed"
    assert status_severity(view.projection.status) == "success"


def test_reuse_view_keeps_rer_authority_in_actions_and_sections():
    projection = reuse_explain_projection([{"node": "unit", "ref": "rer:unit", "disposition": "reuse"}])
    view = render_projection_view(projection)
    assert {ref.authority for ref in view.refs} == {"RER"}
    assert_view_is_reference_only(view)


def test_environment_view_redacts_secret_details_through_projection():
    projection = environment_doctor_projection([{"id": "token", "ref": "env:token", "status": "passed", "detail": {"API_TOKEN": "abc"}}])
    view = render_projection_view(projection)
    encoded = str(view.as_machine())
    assert "abc" not in encoded
    assert "<redacted>" in encoded


def test_dashboard_view_keeps_all_authorities():
    dashboard = compose_dashboard(
        status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}]),
        validation_verdict_projection({"required": ["unit"]}, {"satisfied": ["unit"], "refs": ["validation:unit"]}),
        reuse_explain_projection([{"node": "unit", "ref": "rer:unit", "disposition": "reuse"}]),
    )
    view = render_projection_view(dashboard, surface=PresentationSurface.TUI)
    assert {ref.authority for ref in view.refs} == {"EXO", "validation", "RER"}


def test_unknown_profile_falls_back_to_standard():
    layout = resolve_layout_intent(PresentationSurface.TUI, profile="weird")
    assert layout.profile == PresentationProfile.STANDARD


def test_status_severity_mapping_is_stable():
    assert status_severity("failed") == "error"
    assert status_severity("running") == "active"
    assert status_severity("reused") == "success"
    assert status_severity("empty") == "neutral"
    assert status_severity("mystery") == "unknown"


def test_action_without_refs_is_disabled_for_empty_projection():
    projection = status_from_exo([])
    first = actions_for_projection(projection)[0]
    assert first.id == "open-evidence"
    assert first.enabled is False


def test_layout_width_has_floor():
    layout = resolve_layout_intent(PresentationSurface.TERMINAL, width=5)
    assert layout.width == 20


def test_requested_notifications_supported_on_desktop_only():
    desktop = {cap.id: cap.supported for cap in resolve_surface_capabilities(PresentationSurface.DESKTOP, requested=["notifications"])}
    terminal = {cap.id: cap.supported for cap in resolve_surface_capabilities(PresentationSurface.TERMINAL, requested=["notifications"])}
    assert desktop["notifications"] is True
    assert terminal["notifications"] is False


def test_view_rejects_execution_owned_truth_leak():
    projection = status_from_exo([{"id": "bad", "ref": "exo:bad", "status": "passed", "detail": {"stderr": "copied"}}])
    view = render_projection_view(projection)
    with pytest.raises(AssertionError):
        assert_view_is_reference_only(view)
