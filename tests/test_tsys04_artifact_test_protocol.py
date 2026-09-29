from devtool.artifacts import artifact_validation_refs, artifact_validation_tests
from devtool.test_protocol import EventAccumulator, parse_line
from devtool.test_model import TestStatus as Status

def test_validation_tests_refs_and_inline():
    m={"validation":{"tests":[{"ref":"unit"},{"id":"contract","runner":"pytest","command":["python","-m","pytest"]}]}}
    x=artifact_validation_tests(m); assert x[0]=={"ref":"unit"} and x[1]["id"]=="contract"
def test_validation_tests_gradle_task_stays_semantic():
    x=artifact_validation_tests({"validation":{"tests":[{"id":"u","runner":"gradle","task":":app:test"}]}})
    assert x[0]["task"] == ":app:test"
    assert "command" not in x[0]
def test_protocol_accumulator_preserves_rich_status():
    a=EventAccumulator(); a.feed({"type":"suite_started","id":"s","name":"S"}); a.feed({"type":"case","suite":"s","id":"s#a","name":"a","status":"cancelled"}); a.feed({"type":"case","suite":"s","id":"s#b","name":"b","status":"failed","message":"boom"}); r=a.to_result("x"); assert r.tests_total==2 and r.tests_failed==2 and r.suites[0].cases[0].status==Status.CANCELLED
def test_protocol_rejects_unversioned_noise():
    assert parse_line('{"type":"case"}') is None; assert parse_line('not json') is None


def test_event_fd_crosses_real_subprocess_boundary(tmp_path):
    import gc
    import sys
    import warnings
    from pathlib import Path
    from devtool.hooks.engine import _run_live_with_events
    code="from devtool.testing import suite;\nwith suite(\"wire\") as t: t.pass_(\"case\")"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        rc,out,tracker,events=_run_live_with_events([sys.executable,"-c",code],cwd=tmp_path,env={**dict(__import__("os").environ),"PYTHONPATH":str(Path(__file__).parents[1])},timeout_seconds=5,activity_label="test",script_label="test",stdin_text=None)
        gc.collect()
    assert rc==0 and any(e.get("type")=="case" and e.get("id")=="case" for e in events)
    assert not [warning for warning in caught if issubclass(warning.category, ResourceWarning)]


def test_validation_refs_normalize_test_workflow_and_invoke():
    refs = artifact_validation_refs({"validation":{"refs":["test:unit", {"ref":"workflow:gate"}, "invoke:seal"]}})
    assert refs == [
        {"ref":"test:unit", "kind":"test", "name":"unit"},
        {"ref":"workflow:gate", "kind":"workflow", "name":"gate"},
        {"ref":"workflow:seal", "kind":"workflow", "name":"seal"},
    ]


def test_validation_refs_reject_ambiguous_or_unknown_refs():
    import pytest
    with pytest.raises(ValueError, match="test:<id>"):
        artifact_validation_refs({"validation":{"refs":["unit"]}})
    with pytest.raises(ValueError, match="test:<id>"):
        artifact_validation_refs({"validation":{"refs":["federation:gate"]}})
