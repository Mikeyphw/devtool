from __future__ import annotations
from devtool.android.instrumentation import _execution_classification

BASE = 'INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_STATUS_CODE: 0\n'

def classify(output, code=0):
    return _execution_classification(output, code)

def test_complete_standard_android_runner_passes():
    counts, ids, kind, reason = classify(BASE+'INSTRUMENTATION_CODE: -1\n')
    assert kind == '' and counts['executed'] == 1

def test_complete_android_runner_alternate_zero_terminal_passes():
    assert classify(BASE+'INSTRUMENTATION_CODE: 0\n')[2] == ''

def test_missing_terminal_fails_closed():
    assert classify(BASE)[2] == 'incomplete-results'

def test_duplicate_terminal_fails_closed():
    assert classify(BASE+'INSTRUMENTATION_CODE: -1\nINSTRUMENTATION_CODE: -1\n')[2] == 'incomplete-results'

def test_wrong_terminal_fails_closed():
    assert classify(BASE+'INSTRUMENTATION_CODE: 1\n')[2] == 'incomplete-results'

def test_early_terminal_before_last_event_fails_closed():
    stream = 'INSTRUMENTATION_STATUS: numtests=1\nINSTRUMENTATION_STATUS_CODE: 1\nINSTRUMENTATION_CODE: -1\nINSTRUMENTATION_STATUS_CODE: 0\n'
    assert classify(stream)[2] == 'incomplete-results'

def test_unknown_status_code_fails_closed():
    stream = BASE.replace('STATUS_CODE: 0','STATUS_CODE: 3')+'INSTRUMENTATION_CODE: -1\n'
    assert classify(stream)[2] != ''

def test_failing_test_remains_failure_not_silent_pass():
    stream = BASE.replace('STATUS_CODE: 0','STATUS_CODE: -2')+'INSTRUMENTATION_CODE: -1\n'
    assert classify(stream)[2] == 'test-failure'

def test_shell_nonzero_is_runner_error():
    assert classify(BASE+'INSTRUMENTATION_CODE: -1\n',3)[2] == 'runner-error'
