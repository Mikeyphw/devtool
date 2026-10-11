from devtool.federation_bridge import _federated_job_result_code, execute_federated_job
from devtool.models import CommandSpec, OperationResult
from test_frx07_wrapper_tlj_federation_bridge import _fixture, _load
from devtool.core.events import EventBus
import pytest

def spec(name="lint"):
    return CommandSpec(name,["echo",name])

def result(s,code=0,**changes):
    op=OperationResult(s.name,list(s.argv),code,0.01,"",[])
    for key,value in changes.items(): setattr(op,key,value)
    return op

def test_explicit_complete_success():
    a=spec("a");b=spec("b")
    assert _federated_job_result_code([a,b],[result(a),result(b)])==0

def test_preserves_real_failure():
    a=spec("a");b=spec("b")
    assert _federated_job_result_code([a,b],[result(a),result(b,7)])==7

@pytest.mark.parametrize("bad", [[], [result(spec(),None)], [result(spec(),"0")],
    [result(spec(),True)], [result(spec(),0,skipped=True)],
    [result(spec(),0,failure_kind="transport")], [result(spec("other"))],
    [result(spec(),0,argv=["incorrect"])]])
def test_missing_unknown_or_unbound_results_rejected(bad):
    assert _federated_job_result_code([spec()],bad)==1

def test_extra_and_duplicated_results_rejected():
    s=spec();assert _federated_job_result_code([s],[result(s),result(s)])==1

def test_real_job_path_reports_failure_on_empty_result(tmp_path):
    parent_dir,_=_fixture(tmp_path);parent=_load(parent_dir);bus=EventBus()
    child=execute_federated_job(parent,"child","lint",publish=bus.publish,
        snapshot_lookup=bus.execution_store.snapshot,
        run_specs_fn=lambda *_args,**_kwargs:([],""))
    assert child.status=="failed" and child.exit_code==1
