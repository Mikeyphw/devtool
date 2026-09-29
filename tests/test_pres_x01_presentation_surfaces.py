from __future__ import annotations

import pytest

from devtool.presentation_surfaces import (
    PresentationSurface,
    assert_projection_is_reference_only,
    compose_dashboard,
    environment_doctor_projection,
    projection_digest,
    redact_mapping,
    reuse_explain_projection,
    status_from_exo,
    validation_verdict_projection,
)


def test_exo_status_projection_keeps_exo_as_authority():
    projection = status_from_exo([
        {"id": "syntax", "ref": "exo:1", "status": "passed", "label": "source syntax", "progress": 1.0},
        {"id": "tests", "ref": "exo:2", "status": "running", "label": "pytest", "progress": 0.5},
    ])
    assert projection.status == "running"
    assert [event.authority for event in projection.events] == ["EXO", "EXO"]
    assert projection.as_machine()["generated_truth"] is False
    assert_projection_is_reference_only(projection)


def test_exo_projection_is_bounded_with_cursor():
    projection = status_from_exo([{"id": f"n{i}", "ref": f"exo:{i}", "status": "passed"} for i in range(5)], limit=3)
    assert projection.truncated is True
    assert projection.cursor == "exo:2"
    assert len(projection.events) == 3


def test_terminal_rows_are_rendering_only_and_stable():
    projection = status_from_exo([{"id": "a", "ref": "exo:a", "status": "passed", "label": "A"}], surface=PresentationSurface.TERMINAL)
    rows = projection.terminal_rows()
    assert rows[0].startswith("Execution status: passed")
    assert "EXO:a" in rows[1]
    assert projection.as_machine()["projection_digest"] == projection.as_machine()["projection_digest"]


def test_validation_verdict_uses_contract_and_evidence_refs():
    projection = validation_verdict_projection(
        {"required": ["syntax", "unit"], "optional": ["lint"]},
        {"satisfied": ["syntax", "unit"], "refs": ["validation:run-1"]},
    )
    assert projection.status == "passed"
    assert all(ref.authority == "validation" for ref in projection.refs)
    assert_projection_is_reference_only(projection)


def test_required_skip_without_applicability_evidence_fails():
    projection = validation_verdict_projection(
        {"required": ["device"]},
        {"skipped": ["device"], "refs": ["validation:run-2"]},
    )
    assert projection.status == "failed"
    assert "invalid_skips" in projection.summary


def test_required_not_applicable_with_evidence_satisfies_contract():
    projection = validation_verdict_projection(
        {"required": ["device"]},
        {"not_applicable": ["device"], "refs": ["validation:run-3"]},
    )
    assert projection.status == "passed"
    assert projection.events[0].status == "not-applicable"


def test_optional_only_contract_resolves_without_fake_work():
    projection = validation_verdict_projection({"optional": ["lint"]}, {"refs": ["validation:optional"]})
    assert projection.status == "passed"
    assert projection.events[0].status == "optional"


def test_reuse_explain_projection_keeps_rer_authority():
    projection = reuse_explain_projection([
        {"node": "build", "ref": "rer:build", "disposition": "reuse", "reason": "fingerprint matched", "source_proof": "run:1"},
        {"node": "test", "ref": "rer:test", "disposition": "invalidated", "reason": "input changed"},
    ])
    assert projection.status == "rerun"
    assert [ref.authority for ref in projection.refs] == ["RER", "RER"]
    assert projection.events[0].status == "reused"
    assert projection.events[1].status == "rerun"


def test_environment_doctor_redacts_secret_detail():
    projection = environment_doctor_projection([
        {"id": "python", "ref": "env:python", "status": "passed", "message": "python ok", "detail": {"API_TOKEN": "secret", "PATH": "/usr/bin"}},
    ])
    detail = projection.events[0].as_dict()["detail"]
    assert detail["API_TOKEN"] == "<redacted>"
    assert detail["PATH"] == "/usr/bin"


def test_redaction_catches_inline_credentials():
    redacted = redact_mapping({"command": "curl -H 'Authorization: Bearer abc'", "ok": "yes"})
    assert redacted["command"] == "<redacted>"
    assert redacted["ok"] == "yes"


def test_dashboard_composition_keeps_source_refs():
    exo = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}])
    rer = reuse_explain_projection([{"node": "unit", "ref": "rer:unit", "disposition": "reuse"}])
    dashboard = compose_dashboard(exo, rer, surface=PresentationSurface.TUI)
    assert dashboard.status == "passed" or dashboard.status == "reused"
    assert {ref.authority for ref in dashboard.refs} == {"EXO", "RER"}
    assert_projection_is_reference_only(dashboard)


def test_dashboard_failure_dominates_rendered_status():
    exo = status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "failed"}])
    val = validation_verdict_projection({"required": ["unit"]}, {"satisfied": []})
    dashboard = compose_dashboard(exo, val)
    assert dashboard.status == "failed"


def test_dashboard_is_bounded():
    projections = [status_from_exo([{"id": f"n{i}", "ref": f"exo:{i}", "status": "passed"}]) for i in range(5)]
    dashboard = compose_dashboard(*projections, limit=3)
    assert dashboard.truncated is True
    assert len(dashboard.events) == 3
    assert dashboard.cursor == "n2"


def test_projection_digest_changes_when_source_ref_changes():
    a = status_from_exo([{"id": "unit", "ref": "exo:a", "status": "passed"}]).as_machine()["projection_digest"]
    b = status_from_exo([{"id": "unit", "ref": "exo:b", "status": "passed"}]).as_machine()["projection_digest"]
    assert a != b


def test_projection_digest_helper_is_stable_for_sorted_payloads():
    assert projection_digest({"b": 2, "a": 1}) == projection_digest({"a": 1, "b": 2})


def test_projection_rejects_copied_stdout_truth():
    projection = status_from_exo([{"id": "bad", "ref": "exo:bad", "status": "passed", "detail": {"stdout": "copied"}}])
    with pytest.raises(AssertionError):
        assert_projection_is_reference_only(projection)


def test_machine_and_terminal_surfaces_share_event_identities():
    records = [{"id": "unit", "ref": "exo:unit", "status": "passed"}]
    machine = status_from_exo(records, surface=PresentationSurface.MACHINE)
    terminal = status_from_exo(records, surface=PresentationSurface.TERMINAL)
    assert [e.id for e in machine.events] == [e.id for e in terminal.events]
    assert machine.as_machine()["surface"] == "machine"
    assert terminal.as_machine()["surface"] == "terminal"


def test_empty_projection_is_explicitly_empty_not_passed():
    projection = status_from_exo([])
    assert projection.status == "empty"
    assert projection.summary.startswith("0 canonical")


def test_environment_error_marks_doctor_failed():
    projection = environment_doctor_projection([{"id": "gradle", "ref": "env:gradle", "status": "error", "message": "missing"}])
    assert projection.status == "failed"


def test_reuse_override_is_rendered_as_explanation_not_authority_change():
    projection = reuse_explain_projection([{"node": "unit", "ref": "rer:unit", "disposition": "rerun", "override": "--fresh"}])
    assert projection.events[0].authority == "RER"
    assert projection.events[0].as_dict()["detail"]["override"] == "--fresh"


def test_validation_output_flags_do_not_change_verdict_payload():
    contract = {"required": ["unit"]}
    evidence = {"satisfied": ["unit"], "refs": ["validation:1"]}
    machine = validation_verdict_projection(contract, evidence, surface=PresentationSurface.MACHINE)
    desktop = validation_verdict_projection(contract, evidence, surface=PresentationSurface.DESKTOP)
    assert [e.status for e in machine.events] == [e.status for e in desktop.events]
    assert machine.status == desktop.status == "passed"


def test_all_projection_refs_have_explicit_authority_and_kind():
    dashboard = compose_dashboard(
        status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}]),
        validation_verdict_projection({"required": ["unit"]}, {"satisfied": ["unit"], "refs": ["validation:unit"]}),
        reuse_explain_projection([{"node": "unit", "ref": "rer:unit", "disposition": "reuse"}]),
    )
    for ref in dashboard.refs:
        data = ref.as_dict()
        assert data["authority"]
        assert data["kind"]
        assert data["ref"]


def test_liveness_is_presented_from_exo_record_not_owned_heartbeat():
    projection = status_from_exo([{"id": "gradle", "ref": "exo:gradle", "status": "running", "detail": {"last_event_ref": "run:event:7"}}])
    assert projection.events[0].detail["last_event_ref"] == "run:event:7"
    assert "heartbeat_owner" not in projection.as_machine()


def test_desktop_surface_can_render_same_dashboard_without_desktop_only_truth():
    projection = compose_dashboard(status_from_exo([{"id": "unit", "ref": "exo:unit", "status": "passed"}]), surface=PresentationSurface.DESKTOP)
    payload = projection.as_machine()
    assert payload["surface"] == "desktop"
    assert payload["generated_truth"] is False
