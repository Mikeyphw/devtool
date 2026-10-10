from __future__ import annotations

import pytest
from devtool.presentation_surfaces import PresentationSurface, status_from_exo


@pytest.mark.parametrize("event_status,expected", [
    ("succeeded","passed"),("passed","passed"),("failed","failed"),
    ("cancelled","cancelled"),("blocked","blocked"),("skipped","skipped"),
    ("planned","running"),("unknown","unknown"),("interrupted","unknown"),
])
@pytest.mark.parametrize("surface", [PresentationSurface.MACHINE,PresentationSurface.TUI,PresentationSurface.DESKTOP])
def test_single_exo_record_preserves_state(event_status,expected,surface):
    projected=status_from_exo([{"id":"n1","ref":"exo:n1","status":event_status}],surface=surface)
    assert projected.status==expected
    assert projected.as_machine()["status"]==expected


def test_mixed_results_are_never_false_success():
    for statuses in [("passed","cancelled"),("passed","blocked"),("passed","skipped"),("succeeded","unknown"),("passed","planned")]:
        projected=status_from_exo([{"id":str(i),"status":state} for i,state in enumerate(statuses)])
        assert projected.status!="passed"


def test_empty_projection_remains_empty():
    assert status_from_exo([]).status=="empty"
