import json
from types import SimpleNamespace
import pytest
from devtool.android import cli

def marker(**overrides):
    payload={"schema_version":1,"kind":"devtool.android-advanced-worker-result",
        "child_run_id":"run-1","child_id":"child-1","parent_plan_id":"plan-1","result":{"ok":True,"receipt_id":"receipt-1"}}
    payload.update(overrides)
    return "DEVTOOL_ANDROID_ADVANCED_RESULT "+json.dumps(payload)

def test_single_structured_marker_parses():
    assert cli._advanced_worker_result(marker())["child_id"]=="child-1"

def test_duplicate_or_invalid_worker_markers_rejected():
    assert cli._advanced_worker_result(marker()+"\n"+marker())=={}
    assert cli._advanced_worker_result("DEVTOOL_ANDROID_ADVANCED_RESULT {not-json")=={}
    assert cli._advanced_worker_result("ordinary output")=={}

@pytest.mark.parametrize('field,bad',[("child_run_id","other"),("child_id","other"),("parent_plan_id","other"),("kind","other"),("schema_version",9)])
def test_marker_binding_contract_fields_are_distinct(field,bad):
    assert cli._advanced_worker_result(marker(**{field:bad}))[field]==bad
    assert not cli._valid_advanced_worker_result(
        cli._advanced_worker_result(marker(**{field:bad})),
        child_run_id="run-1",child_id="child-1",parent_plan_id="plan-1",
    )

def test_missing_receipt_is_not_an_acceptable_success_envelope():
    row=cli._advanced_worker_result(marker(result={"ok":True,"receipt_id":""}))
    assert not cli._valid_advanced_worker_result(
        row,child_run_id="run-1",child_id="child-1",parent_plan_id="plan-1",
    )

def test_good_worker_binding():
    assert cli._valid_advanced_worker_result(
        cli._advanced_worker_result(marker()),
        child_run_id="run-1",child_id="child-1",parent_plan_id="plan-1",
    )

@pytest.mark.parametrize("code",[None,1])
def test_android_gradle_build_fails_on_bad_returncode(monkeypatch,code):
    from devtool import project_runners, runners
    monkeypatch.setattr(project_runners.AndroidGradleRunner,"build_specs",lambda *a:[SimpleNamespace(metadata={"gradle_execution_plan":True})])
    monkeypatch.setattr(runners,"run_specs",lambda *a,**k:([SimpleNamespace(returncode=code,metadata={},name="build")],None))
    with pytest.raises(RuntimeError,match="failed"):
        cli._build_instrumentation_artifacts(SimpleNamespace())

def test_android_gradle_build_fails_on_empty_results(monkeypatch):
    from devtool import project_runners,runners
    monkeypatch.setattr(project_runners.AndroidGradleRunner,"build_specs",lambda *a:[SimpleNamespace(metadata={"gradle_execution_plan":True})])
    monkeypatch.setattr(runners,"run_specs",lambda *a,**k:([],None))
    with pytest.raises(RuntimeError,match="incomplete"):
        cli._build_instrumentation_artifacts(SimpleNamespace())
