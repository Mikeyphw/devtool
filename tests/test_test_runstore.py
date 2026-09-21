from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import json
import devtool.test_runstore as store


def _result(status="failed", case="a", *, result_id="unit", spec_id="unit", target=""):
    tr={"id":result_id,"suites":[{"id":"suite","name":"suite","cases":[{"id":case,"name":case,"status":status}],"artifacts":[]}],"artifacts":[{"kind":"junit-xml","path":"report.xml","metadata":{}}]}
    metadata={"test_result":tr,"test_spec_id":spec_id,"tests_passed":999,"tests_failed":999,"tests_skipped":999,"tests_total":999}
    if target: metadata["target"]=target
    return SimpleNamespace(name=f"test:{spec_id}", metadata=metadata)


def test_build_and_compare_stable_case_ids(tmp_path):
    old=store.build_run_payload(tmp_path,[_result("failed","a")],run_id="old")
    new=store.build_run_payload(tmp_path,[_result("passed","a"),_result("failed","b",result_id="lint",spec_id="lint")],run_id="new")
    cmp=store.compare_test_runs(old,new)
    assert cmp=={"new_failures":["lint::lint::suite::b"],"existing_failures":[],"fixed":["unit::unit::suite::a"]}
    # Counts are derived from the canonical TestResult, never stale metadata.
    assert new["summary"]["passed"]==1 and new["summary"]["failed"]==1 and new["summary"]["total"]==2
    assert new["artifacts"][0]["path"]=="report.xml"


def test_identical_case_ids_from_distinct_test_specs_do_not_collide(tmp_path):
    payload=store.build_run_payload(tmp_path,[
        _result("failed","same",result_id="unit",spec_id="unit"),
        _result("failed","same",result_id="integration",spec_id="integration"),
    ],run_id="r")
    keys=store.compare_test_runs(None,payload)["new_failures"]
    assert keys==["integration::integration::suite::same","unit::unit::suite::same"]


def test_persist_is_atomic_and_repository_scoped(tmp_path, monkeypatch):
    root=tmp_path/"state"; monkeypatch.setattr(store,"_root",lambda:root)
    repo=tmp_path/"repo"; repo.mkdir()
    p1=store.persist_test_run(repo,[_result("failed")],run_id="one")
    p2=store.persist_test_run(repo,[_result("passed")],run_id="two")
    assert p1 and p2 and not list(root.glob("*.tmp"))
    value=json.loads(p2.read_text())
    assert value["comparison"]["fixed"]==["unit::unit::suite::a"]
    assert store.load_test_run("two")["run_id"]=="two"


def test_parallel_nodes_merge_into_one_logical_run(tmp_path, monkeypatch):
    root=tmp_path/"state"; monkeypatch.setattr(store,"_root",lambda:root)
    repo=tmp_path/"repo"; repo.mkdir()
    items=[_result("passed","a",result_id="unit",spec_id="unit"),_result("failed","b",result_id="integration",spec_id="integration")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: store.persist_test_run(repo,[item],run_id="shared"),items))
    value=store.load_test_run("shared")
    assert len(value["operations"])==2
    assert value["summary"]["passed"]==1 and value["summary"]["failed"]==1 and value["summary"]["total"]==2


def test_same_run_retry_replaces_same_stable_operation(tmp_path, monkeypatch):
    root=tmp_path/"state"; monkeypatch.setattr(store,"_root",lambda:root)
    repo=tmp_path/"repo"; repo.mkdir()
    store.persist_test_run(repo,[_result("failed")],run_id="same")
    store.persist_test_run(repo,[_result("passed")],run_id="same")
    value=store.load_test_run("same")
    assert len(value["operations"])==1 and value["summary"]["passed"]==1 and value["summary"]["failed"]==0


def test_legacy_payload_shapes_are_normalized():
    legacy={"run_id":"r","repository":"/repo","test_result":{"id":"unit","suites":[{"id":"s","cases":[{"id":"a","status":"passed"}]}]}}
    value=store.normalize_test_run_payload(legacy)
    assert value["schema_version"]==store.SCHEMA_VERSION
    assert value["summary"]["passed"]==1 and value["summary"]["total"]==1
    assert value["operations"][0]["operation_id"]=="unit"


def test_ipc_snapshot_merges_and_normalizes_canonical_test_run(tmp_path, monkeypatch):
    import devtool.ipc as ipc
    from devtool.ipc import IpcBroker
    persistent=tmp_path/"persistent"; persistent.mkdir()
    monkeypatch.setattr(ipc,"state_dir",lambda: persistent)
    runs=tmp_path/"runs"; runs.mkdir()
    (runs/"r1.json").write_text(json.dumps({"run_id":"r1","status":"passed","sequence":1}))
    tests=persistent/"test-runs"; tests.mkdir()
    # Legacy shape intentionally omits schema_version/summary.
    (tests/"r1.json").write_text(json.dumps({"run_id":"r1","repository":"/repo","test_result":{"id":"unit","suites":[{"id":"s","cases":[{"id":"a","status":"passed"}]}]}}))
    broker=IpcBroker(tmp_path/"sock",state_dir=tmp_path); broker._load_snapshot_files()
    assert broker.runs["r1"]["status"]=="passed"
    assert broker.runs["r1"]["tests"]["summary"]["passed"]==1


def test_validation_evidence_discovers_canonical_test_artifacts(tmp_path, monkeypatch):
    import devtool.validation_evidence as evidence
    report=tmp_path/"junit.xml"; report.write_text("<testsuite/>")
    tr={"id":"unit","suites":[],"artifacts":[{"kind":"junit-xml","path":str(report),"metadata":{}}]}
    result=SimpleNamespace(name="test:unit",returncode=1,failure_kind="tests",log_path="",metadata={"test_result":tr})
    state=tmp_path/"state"; monkeypatch.setattr(evidence,"state_dir",lambda:state)
    dest=evidence.preserve_validation_evidence(tmp_path,[result],run_id="r1")
    assert dest and (dest/"junit.xml").is_file()


def test_run_specs_persists_canonical_test_results(tmp_path, monkeypatch):
    import sys
    import devtool.test_runstore as runstore
    from devtool.models import ProjectConfig, CommandSpec
    from devtool.runners import run_specs
    seen={}
    def fake(repo, results, *, run_id=""):
        seen["results"]=results; return tmp_path/"run.json"
    monkeypatch.setattr(runstore,"persist_test_run",fake)
    cfg=ProjectConfig(repo_root=tmp_path,kind="python",project_root=str(tmp_path))
    spec=CommandSpec("test:unit",[sys.executable,"-c","print('1 passed in 0.01s')"],metadata={"canonical_test":True,"test_runner":"pytest","runner":"pytest","test_spec_id":"unit"})
    results,_=run_specs(cfg,[spec],verbose=False)
    assert "results" in seen and results[0].metadata["test_run_path"].endswith("run.json")
    assert results[0].metadata["test_result"]["suites"]


def test_missing_previous_failure_is_not_claimed_fixed(tmp_path):
    old=store.build_run_payload(tmp_path,[_result("failed","a")],run_id="old")
    current=store.build_run_payload(tmp_path,[_result("passed","b",result_id="other",spec_id="other")],run_id="new")
    assert store.compare_test_runs(old,current)["fixed"]==[]


def test_run_id_is_sanitized_to_runstore_filename(tmp_path, monkeypatch):
    root=tmp_path/"state"; monkeypatch.setattr(store,"_root",lambda:root)
    repo=tmp_path/"repo"; repo.mkdir()
    path=store.persist_test_run(repo,[_result("passed")],run_id="../../unsafe/run")
    assert path.parent==root
    assert path.name=="unsafe-run.json"
    assert store.load_test_run("../../unsafe/run")["run_id"]=="unsafe-run"
