from pathlib import Path
import pytest
from devtool.config import load_project_config
from devtool.test_declarations import command_specs, select_tests
from devtool.test_model import TestCaseResult as CaseResult, TestResult as CanonResult, TestStatus as Status, TestSuiteResult as SuiteResult
from devtool.test_ui import TestProgressState as ProgressState
from devtool.rich_progress import ThreeLineProgressState

def repo(tmp_path, body):
    (tmp_path/'.devtool.toml').write_text('[project]\nrunner="python"\n'+body)
    (tmp_path/'pyproject.toml').write_text('[project]\nname="x"\nversion="0"\n')
    return load_project_config(str(tmp_path),None,allow_ambiguous_target=True)

def test_parse_and_select_declared_tests(tmp_path):
    c=repo(tmp_path,'\n[[test]]\nid="unit"\nname="Unit"\nrunner="pytest"\ncommand=["python","-m","pytest","-q"]\nrequires_network=false\n')
    assert c.tests['unit'].runner=='pytest'
    specs=command_specs(c,['unit']); assert specs[0].metadata['canonical_test'] is True and specs[0].argv[-1]=='-q'

def test_gradle_task_sugar(tmp_path):
    c=repo(tmp_path,'\n[[test]]\nid="android-unit"\nrunner="gradle"\ntask=":app:testDebugUnitTest"\n')
    # Semantic task requests are preserved until the canonical Gradle planner.
    assert c.tests['android-unit'].command == ()
    assert c.tests['android-unit'].gradle_tasks == (':app:testDebugUnitTest',)

def test_duplicate_and_invalid_declarations_fail(tmp_path):
    with pytest.raises(SystemExit): repo(tmp_path,'\n[[test]]\nid="x"\nrunner="pytest"\ncommand=["pytest"]\n[[test]]\nid="x"\nrunner="pytest"\ncommand=["pytest"]\n')

def test_unknown_selection_fails(tmp_path):
    c=repo(tmp_path,'\n[[test]]\nid="unit"\nrunner="pytest"\ncommand=["pytest"]\n')
    with pytest.raises(SystemExit): select_tests(c,['missing'])

def test_canonical_ui_projection():
    r=CanonResult('x',(SuiteResult('s','S',(CaseResult('a','a',Status.PASSED),CaseResult('b','b',Status.ERROR),CaseResult('c','c',Status.SKIPPED))),))
    s=ProgressState.from_result(r); assert (s.passed,s.errors,s.skipped,s.total)==(1,1,1,3)
    p=ThreeLineProgressState.from_test_result(r); assert (p.tests_passed,p.tests_failed,p.tests_skipped,p.tests_total)==(1,1,1,3)


def test_cli_declared_test_selectors_parse():
    from devtool.cli import build_parser
    p=build_parser()
    a=p.parse_args(["test","unit"]); assert a.test_names==["unit"]
    a=p.parse_args(["test","--all"]); assert a.all_declared_tests is True
    a=p.parse_args(["validate","--test","unit"]); assert a.declared_tests==["unit"]
