import pytest
from devtool.android.instrumentation import _execution_classification

SUCCESS=("INSTRUMENTATION_STATUS: numtests=1\n"
         "INSTRUMENTATION_STATUS_CODE: 1\n"
         "INSTRUMENTATION_STATUS_CODE: 0\n"
         "INSTRUMENTATION_CODE: -1\n")

@pytest.mark.parametrize("returncode",[None,True,False,"0",0.0,object()])
def test_unknown_exit_never_becomes_success(returncode):
    counts,ids,kind,detail=_execution_classification(SUCCESS,returncode)
    assert kind=="runner-error" and "exit code" in detail

def test_terminal_success_remains_success():
    counts,ids,kind,detail=_execution_classification(SUCCESS,0)
    assert kind==""

def test_nonzero_exit_remains_failure():
    assert _execution_classification(SUCCESS,2)[2]=="runner-error"

def test_missing_final_marker_still_fails():
    assert _execution_classification(SUCCESS.replace("INSTRUMENTATION_CODE: -1", ""),0)[2]=="incomplete-results"
