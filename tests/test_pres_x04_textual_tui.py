from __future__ import annotations

import pytest

from devtool.presentation_surfaces import (
    PresentationConsumer,
    build_presentation_bundle,
    status_from_exo,
    validation_verdict_projection,
)
from devtool.tui_textual import (
    TextualAvailability,
    assert_textual_spec_is_reference_only,
    build_textual_app_spec,
    build_textual_screens,
    build_textual_spec_from_projection,
    choose_textual_launch_plan,
    classify_terminal_profile,
    default_textual_bindings,
    normalize_session_transport,
    textual_dependency_contract,
)


def sample_bundle(status: str = "running"):
    projection = status_from_exo([
        {"id": "gradle", "ref": "exo:gradle", "status": status, "label": "Gradle assemble", "progress": 0.42},
    ])
    return build_presentation_bundle(projection, width=120)


def test_textual_app_spec_is_reference_only_and_schema_v1():
    spec = build_textual_app_spec(sample_bundle(), availability=TextualAvailability(False, reason="missing"))
    machine = spec.as_dict()
    assert machine["schema_version"] == 1
    assert machine["backend"] == "textual"
    assert machine["owns_truth"] is False
    assert machine["source_bundle_digest"]
    assert machine["source_view_digest"]
    assert_textual_spec_is_reference_only(spec)


def test_textual_screens_cover_dashboard_details_and_evidence():
    spec = build_textual_app_spec(sample_bundle(), width=118, height=32)
    assert [screen.id for screen in spec.screens] == ["dashboard", "details", "evidence"]
    dashboard = spec.screens[0]
    assert [widget.id for widget in dashboard.widgets] == ["status", "progress", "diagnostics", "actions"]
    assert dashboard.route == "/"


def test_ssh_small_terminal_uses_compact_profile():
    profile = classify_terminal_profile(width=62, height=18, session_transport="ssh")
    assert profile.value == "compact"


def test_wide_local_terminal_uses_detailed_profile():
    profile = classify_terminal_profile(width=160, height=42, session_transport="local")
    assert profile.value == "detailed"


def test_ssh_wide_terminal_stays_standard_to_avoid_overdraw():
    profile = classify_terminal_profile(width=160, height=42, session_transport="ssh")
    assert profile.value == "standard"


def test_default_bindings_map_canonical_actions_to_keys():
    failed = validation_verdict_projection({"required": ["unit"]}, {"refs": ["validation:unit"]})
    failed_bundle = build_presentation_bundle(failed)
    failed_bindings = {binding.action_id: binding for binding in default_textual_bindings(failed_bundle)}
    assert failed_bindings["help"].key == "?"
    assert failed_bindings["quit"].key == "q"
    assert failed_bindings["open-evidence"].key == "e"
    assert failed_bindings["rerun-failed"].key == "r"
    assert failed_bindings["rerun-failed"].intent == "validation_control:rerun_failed"

    passed_bundle = sample_bundle("passed")
    passed_bindings = {binding.action_id: binding for binding in default_textual_bindings(passed_bundle)}
    assert passed_bindings["copy-summary"].key == "c"


def test_textual_launch_plan_prefers_textual_when_available():
    plan = choose_textual_launch_plan(availability=TextualAvailability(True), session_transport="termux-ssh")
    assert plan.selected_backend == "textual"
    assert plan.fallback_backend is None
    assert plan.session_transport == "ssh"
    assert plan.command == ("python", "-m", "devtool.tui_textual_runtime")


def test_textual_launch_plan_falls_back_when_optional_dependency_missing():
    plan = choose_textual_launch_plan(availability=TextualAvailability(False, reason="not installed"))
    assert plan.selected_backend == "unavailable"
    assert plan.fallback_backend is None
    assert plan.reason == "not installed"


def test_textual_launch_plan_can_fail_closed_when_required():
    plan = choose_textual_launch_plan(availability=TextualAvailability(False, reason="not installed"), require_textual=True)
    assert plan.selected_backend == "unavailable"
    assert plan.command == ()
    assert plan.reason == "not installed"


def test_dependency_contract_declares_lazy_optional_textual():
    contract = textual_dependency_contract()
    assert contract["primary_backend"] == "textual"
    assert contract["dependency"] == "textual"
    assert contract["declared_optional_extra"] == "textual"
    assert contract["must_import_lazily"] is True
    assert contract["validation_requires_installed_textual"] is False


def test_build_textual_spec_from_projection_keeps_canonical_refs():
    spec = build_textual_spec_from_projection([
        {"id": "unit", "ref": "exo:unit", "status": "passed"},
    ])
    refs = spec.as_dict()["refs"]
    assert refs == [{"authority": "EXO", "ref": "exo:unit", "kind": "execution-node"}]


def test_textual_widgets_use_rendered_consumer_sources():
    bundle = sample_bundle("passed")
    screens = build_textual_screens(bundle)
    details = {widget.id: widget for widget in screens[1].widgets}
    assert details["terminal"].source == "terminal-render"
    assert details["machine"].source == "machine-render"


def test_textual_spec_rejects_execution_owned_truth_leak_in_metadata():
    with pytest.raises(AssertionError):
        build_textual_app_spec(sample_bundle(), metadata={"stdout": "copied"})


def test_session_transport_normalization_is_stable():
    assert normalize_session_transport("termux-ssh") == "ssh"
    assert normalize_session_transport("native-termux") == "local"
    assert normalize_session_transport(None) == "local"


def test_app_spec_contains_enabled_action_intents():
    projection = validation_verdict_projection({"required": ["unit"]}, {"refs": ["validation:unit"]})
    bundle = build_presentation_bundle(projection, consumers=[PresentationConsumer.MACHINE, PresentationConsumer.TERMINAL, PresentationConsumer.TUI])
    spec = build_textual_app_spec(bundle)
    intents = spec.as_dict()["action_intents"]
    assert "open_refs" in intents
    assert "validation_control:rerun_failed" in intents
