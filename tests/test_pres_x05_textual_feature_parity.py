from __future__ import annotations

import pytest

from devtool.presentation_surfaces import build_presentation_bundle, status_from_exo, validation_verdict_projection
from devtool.tui_textual import (
    TextualAvailability,
    assert_textual_feature_parity_is_reference_only,
    build_textual_app_spec,
    build_textual_command_palette,
    build_textual_feature_parity_from_projection,
    build_textual_feature_parity_plan,
    build_textual_feature_parity_screens,
    evaluate_textual_feature_coverage,
    textual_required_feature_contract,
)


def running_spec(*, width: int = 118, height: int = 30, transport: str = "local"):
    projection = status_from_exo([
        {"id": "resolve", "ref": "exo:resolve", "status": "passed", "label": "Resolve", "progress": 1.0},
        {"id": "gradle", "ref": "exo:gradle", "status": "running", "label": "Gradle assemble", "progress": 0.42},
    ])
    bundle = build_presentation_bundle(projection, width=width)
    return build_textual_app_spec(
        bundle,
        availability=TextualAvailability(False, reason="missing"),
        width=width,
        height=height,
        session_transport=transport,
    )


def validation_failure_spec():
    projection = validation_verdict_projection({"required": ["unit"]}, {"refs": ["validation:unit"]})
    bundle = build_presentation_bundle(projection, width=120)
    return build_textual_app_spec(bundle, availability=TextualAvailability(False, reason="missing"), width=120, height=30)


def test_feature_parity_plan_schema_and_reference_boundary():
    plan = build_textual_feature_parity_plan(running_spec())
    machine = plan.as_dict()
    assert machine["schema_version"] == 1
    assert machine["backend"] == "textual"
    assert machine["owns_truth"] is False
    assert machine["parity_digest"]
    assert machine["missing_features"] == []
    assert_textual_feature_parity_is_reference_only(plan)


def test_supplemental_screens_cover_current_tui_surfaces():
    screens = build_textual_feature_parity_screens(running_spec())
    assert [screen.id for screen in screens] == ["runs", "validation", "artifacts", "commands", "help"]
    widgets = {widget.id for screen in screens for widget in screen.widgets}
    assert {"run-list", "progress", "node-tree", "validation-session", "artifact-lifecycle", "command-palette", "shortcut-table"} <= widgets


def test_required_feature_contract_lists_replacement_scope():
    feature_ids = [feature.id for feature in textual_required_feature_contract()]
    assert feature_ids == [
        "run-list",
        "active-progress",
        "node-tree",
        "diagnostics",
        "evidence",
        "validation-session",
        "artifact-lifecycle",
        "command-palette",
        "keyboard-shortcuts",
        "copy-share",
        "responsive-layout",
    ]


def test_feature_coverage_fails_closed_when_required_screen_is_missing():
    spec = running_spec()
    screens = tuple(screen for screen in (*spec.screens, *build_textual_feature_parity_screens(spec)) if screen.id != "commands")
    covered = evaluate_textual_feature_coverage(screens, command_palette=build_textual_command_palette(spec))
    by_id = {feature.id: feature for feature in covered}
    assert by_id["command-palette"].covered is False
    assert by_id["keyboard-shortcuts"].covered is False


def test_command_palette_uses_canonical_action_intents_and_bindings():
    palette = build_textual_command_palette(validation_failure_spec())
    by_action = {item.action_id: item for item in palette}
    assert by_action["open-evidence"].intent == "open_refs"
    assert by_action["open-evidence"].key == "e"
    assert by_action["rerun-failed"].intent == "validation_control:rerun_failed"
    assert by_action["rerun-failed"].target_screen == "validation"
    assert by_action["help"].key == "?"


def test_validation_feature_is_covered_when_rerun_action_exists():
    plan = build_textual_feature_parity_plan(validation_failure_spec())
    by_id = {feature.id: feature for feature in plan.features}
    assert by_id["validation-session"].covered is True
    assert "validation" in by_id["validation-session"].screens
    intents = {item.intent for item in plan.command_palette}
    assert "validation_control:rerun_failed" in intents


def test_running_feature_covers_watch_progress_without_owning_progress():
    plan = build_textual_feature_parity_plan(running_spec())
    by_id = {feature.id: feature for feature in plan.features}
    assert by_id["active-progress"].covered is True
    assert "runs" in by_id["active-progress"].screens
    encoded = str(plan.as_dict()).lower()
    assert "owned_progress" not in encoded
    assert "presentation-events" in encoded


def test_artifact_lifecycle_feature_is_reference_only():
    plan = build_textual_feature_parity_plan(running_spec())
    by_id = {feature.id: feature for feature in plan.features}
    assert by_id["artifact-lifecycle"].covered is True
    artifact_screen = next(screen for screen in plan.screens if screen.id == "artifacts")
    assert [widget.source for widget in artifact_screen.widgets] == ["presentation-events", "presentation-sections", "presentation-refs"]
    assert_textual_feature_parity_is_reference_only(plan)


def test_all_widgets_use_allowed_canonical_sources():
    plan = build_textual_feature_parity_plan(running_spec())
    allowed = {
        "presentation-view",
        "presentation-events",
        "presentation-sections",
        "presentation-actions",
        "presentation-refs",
        "projection-digest",
        "terminal-render",
        "machine-render",
        "textual-bindings",
        "command-intents",
        "surface-capabilities",
    }
    for screen in plan.screens:
        for widget in screen.widgets:
            assert widget.source in allowed


def test_mobile_ssh_profile_remains_compact_for_parity_plan():
    plan = build_textual_feature_parity_plan(running_spec(width=58, height=18, transport="ssh"))
    assert {screen.profile for screen in plan.screens} == {"compact"}
    assert plan.base_spec.session_transport == "ssh"


def test_wide_local_profile_remains_detailed_for_parity_plan():
    plan = build_textual_feature_parity_plan(running_spec(width=160, height=42, transport="local"))
    assert {screen.profile for screen in plan.screens} == {"detailed"}


def test_feature_parity_from_projection_convenience_helper_keeps_refs():
    plan = build_textual_feature_parity_from_projection([
        {"id": "unit", "ref": "exo:unit", "status": "passed"},
    ])
    refs = plan.as_dict()["refs"]
    assert refs == [{"authority": "EXO", "ref": "exo:unit", "kind": "execution-node"}]


def test_reference_guard_rejects_leaked_execution_truth_in_base_metadata():
    spec = running_spec()
    with pytest.raises(AssertionError):
        build_textual_app_spec(
            spec.source_bundle,
            availability=TextualAvailability(False, reason="missing"),
            metadata={"stderr": "copied"},
        )
