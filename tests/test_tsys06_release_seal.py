import json

from devtool.test_adapters import EVIDENCE_PRECEDENCE, reconcile_results
from devtool.test_model import TestCaseResult as CaseResult, TestResult as Result, TestStatus as Status, TestSuiteResult as SuiteResult
from devtool.test_system_seal import REQUIRED_RUNNERS, assert_test_system_sealed


def _runner(*args, **kwargs):
    raise AssertionError("seal registry construction must not execute tests")


def test_release_seal_contract_is_complete():
    checks = assert_test_system_sealed(_runner)
    assert checks and all(item.ok for item in checks)
    assert set(REQUIRED_RUNNERS) == {"pytest", "gradle", "dotnet", "go", "cargo", "maven", "node"}


def test_evidence_precedence_is_public_and_monotonic():
    assert [EVIDENCE_PRECEDENCE[x] for x in ("exit", "stdout", "report", "protocol")] == [0, 1, 2, 3]


def test_protocol_evidence_beats_report_and_stdout_without_duplication():
    def r(evidence, status):
        case=CaseResult("C#x","x",status,metadata={"evidence":evidence})
        return Result("unit",(SuiteResult("suite","suite",(case,),metadata={"evidence":evidence}),),metadata={"evidence":evidence})
    merged=reconcile_results("unit", [r("stdout",Status.FAILED), r("report",Status.ERROR), r("protocol",Status.PASSED)])
    assert merged.tests_total == 1
    assert merged.tests_passed == 1
    assert merged.tests_failed == 0


def test_rich_status_compatibility_projection_remains_stable():
    cases=(
      CaseResult("p","p",Status.PASSED), CaseResult("f","f",Status.FAILED),
      CaseResult("e","e",Status.ERROR), CaseResult("s","s",Status.SKIPPED),
      CaseResult("c","c",Status.CANCELLED), CaseResult("xf","xf",Status.XFAIL),
      CaseResult("xp","xp",Status.XPASS),
    )
    result=Result("all",(SuiteResult("s","s",cases),))
    assert (result.tests_passed,result.tests_failed,result.tests_skipped,result.tests_total)==(1,4,2,7)
    json.dumps(result.to_dict())
