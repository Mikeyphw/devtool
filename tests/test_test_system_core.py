from dataclasses import FrozenInstanceError
import threading, pytest
from devtool.test_model import *
from devtool.test_execution import *

def case(i,status): return TestCaseResult(i,i,status)

def test_model_summary_and_compat_projection():
    r=TestResult("unit", (TestSuiteResult("s","suite",(case("a",TestStatus.PASSED),case("b",TestStatus.ERROR),
        case("c",TestStatus.XFAIL),case("d",TestStatus.CANCELLED))),))
    assert r.summary.total == 4
    assert (r.tests_passed,r.tests_failed,r.tests_skipped,r.tests_total)==(1,2,1,4)

def test_nested_maps_are_immutable():
    spec=TestSpec("x","X","fake",env={"A":"B"},metadata={"k":"v"})
    with pytest.raises(TypeError): spec.env["A"]="C"
    with pytest.raises(TypeError): spec.metadata["k"]="x"

@pytest.mark.parametrize("ctor,args",[(TestSpec,("bad id!","x","r")),(TestCaseResult,("","x",TestStatus.PASSED)),
    (TestSuiteResult,("bad id","x")),(TestResult,("bad id",))])
def test_ids_are_validated(ctor,args):
    with pytest.raises(ValueError): ctor(*args)

def test_timeout_duration_and_duplicate_invariants():
    with pytest.raises(ValueError): TestSpec("x","x","r",timeout_seconds=0)
    with pytest.raises(ValueError): TestCaseResult("x","x",TestStatus.PASSED,duration_seconds=-1)
    with pytest.raises(ValueError): TestSuiteResult("s","s",(case("x",TestStatus.PASSED),case("x",TestStatus.FAILED)))

class Fake:
    def run(self,spec,context): return TestResult(spec.id)

def test_registry_and_executor():
    reg=TestRunnerRegistry(); reg.register("fake",Fake())
    assert TestExecutor(reg).execute(TestSpec("x","X","fake"),TestExecutionContext(".")).id=="x"
    with pytest.raises(ValueError): reg.register("fake",Fake())
    with pytest.raises(TestExecutionError): TestExecutor(reg).execute(TestSpec("x","X","missing"),TestExecutionContext("."))

def test_cancel_before_start():
    ev=threading.Event(); ev.set(); reg=TestRunnerRegistry(); reg.register("fake",Fake())
    with pytest.raises(TestExecutionError): TestExecutor(reg).execute(TestSpec("x","X","fake"),TestExecutionContext(".",cancel_event=ev))

def test_malformed_runner_result_rejected():
    class Bad:
        def run(self,s,c): return {"id":s.id}
    reg=TestRunnerRegistry(); reg.register("bad",Bad())
    with pytest.raises(TestExecutionError): TestExecutor(reg).execute(TestSpec("x","X","bad"),TestExecutionContext("."))

def test_command_runner_delegation_contract():
    seen={}
    def command(argv,**kw): seen.update(argv=argv,**kw); return object()
    def build(spec,outcome): return TestResult(spec.id)
    r=CommandTestRunner(command,build)
    spec=TestSpec("x","X","cmd",("tool","test"),cwd="sub",env={"A":"B"},timeout_seconds=3)
    assert r.run(spec,TestExecutionContext("/repo",executor="chroot")).id=="x"
    assert seen["argv"]==["tool","test"] and seen["cwd"]=="sub" and seen["executor"]=="chroot" and seen["timeout"]==3

def test_json_is_stable_and_json_safe():
    r=TestResult("x",(TestSuiteResult("s","S",(case("c",TestStatus.PASSED),)),),metadata={"a":1})
    assert '"schema_version": 1' in r.to_json()
