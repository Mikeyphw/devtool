from dataclasses import FrozenInstanceError
import threading, pytest
from devtool.test_model import (
    TestCaseResult as CaseResult, TestResult as Result, TestSpec as Spec,
    TestStatus as Status, TestSuiteResult as SuiteResult,
)
from devtool.test_execution import (
    CommandTestRunner, TestExecutionContext as ExecutionContext,
    TestExecutionError as ExecutionError, TestExecutor as Executor,
    TestRunnerRegistry as RunnerRegistry,
)


def case(i,status): return CaseResult(i,i,status)

def test_model_summary_and_compat_projection():
    r=Result("unit", (SuiteResult("s","suite",(case("a",Status.PASSED),case("b",Status.ERROR),
        case("c",Status.XFAIL),case("d",Status.CANCELLED))),))
    assert r.summary.total == 4
    assert (r.tests_passed,r.tests_failed,r.tests_skipped,r.tests_total)==(1,2,1,4)

def test_nested_maps_are_immutable():
    spec=Spec("x","X","fake",env={"A":"B"},metadata={"k":"v"})
    with pytest.raises(TypeError): spec.env["A"]="C"
    with pytest.raises(TypeError): spec.metadata["k"]="x"

@pytest.mark.parametrize("ctor,args",[(Spec,("bad id!","x","r")),(CaseResult,("","x",Status.PASSED)),
    (SuiteResult,("bad id","x")),(Result,("bad id",))])
def test_ids_are_validated(ctor,args):
    with pytest.raises(ValueError): ctor(*args)

def test_timeout_duration_and_duplicate_invariants():
    with pytest.raises(ValueError): Spec("x","x","r",timeout_seconds=0)
    with pytest.raises(ValueError): CaseResult("x","x",Status.PASSED,duration_seconds=-1)
    with pytest.raises(ValueError): SuiteResult("s","s",(case("x",Status.PASSED),case("x",Status.FAILED)))

class Fake:
    def run(self,spec,context): return Result(spec.id)

def test_registry_and_executor():
    reg=RunnerRegistry(); reg.register("fake",Fake())
    assert Executor(reg).execute(Spec("x","X","fake"),ExecutionContext(".")).id=="x"
    with pytest.raises(ValueError): reg.register("fake",Fake())
    with pytest.raises(ExecutionError): Executor(reg).execute(Spec("x","X","missing"),ExecutionContext("."))

def test_cancel_before_start():
    ev=threading.Event(); ev.set(); reg=RunnerRegistry(); reg.register("fake",Fake())
    with pytest.raises(ExecutionError): Executor(reg).execute(Spec("x","X","fake"),ExecutionContext(".",cancel_event=ev))

def test_malformed_runner_result_rejected():
    class Bad:
        def run(self,s,c): return {"id":s.id}
    reg=RunnerRegistry(); reg.register("bad",Bad())
    with pytest.raises(ExecutionError): Executor(reg).execute(Spec("x","X","bad"),ExecutionContext("."))

def test_command_runner_delegation_contract():
    seen={}
    def command(argv,**kw): seen.update(argv=argv,**kw); return object()
    def build(spec,outcome): return Result(spec.id)
    r=CommandTestRunner(command,build)
    spec=Spec("x","X","cmd",("tool","test"),cwd="sub",env={"A":"B"},timeout_seconds=3)
    assert r.run(spec,ExecutionContext("/repo",executor="chroot")).id=="x"
    assert seen["argv"]==["tool","test"] and seen["cwd"]=="sub" and seen["executor"]=="chroot" and seen["timeout"]==3

def test_json_is_stable_and_json_safe():
    r=Result("x",(SuiteResult("s","S",(case("c",Status.PASSED),)),),metadata={"a":1})
    assert '"schema_version": 1' in r.to_json()
