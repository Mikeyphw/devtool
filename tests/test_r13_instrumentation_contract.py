"""R13: the R02 positive fixture must obey the later R08 terminal contract."""
from devtool.android.instrumentation import _execution_classification

def test_complete_stream_with_terminal_marker_passes():
    stream = ('INSTRUMENTATION_STATUS: numtests=1\n'
              'INSTRUMENTATION_STATUS_CODE: 1\n'
              'INSTRUMENTATION_STATUS_CODE: 0\n'
              'INSTRUMENTATION_CODE: -1\n')
    counts, _, kind, reason = _execution_classification(stream, 0)
    assert kind == '' and not reason and counts['executed'] == 1

def test_terminal_marker_is_mandatory_not_relaxed():
    stream = ('INSTRUMENTATION_STATUS: numtests=1\n'
              'INSTRUMENTATION_STATUS_CODE: 1\n'
              'INSTRUMENTATION_STATUS_CODE: 0\n')
    assert _execution_classification(stream, 0)[2] == 'incomplete-results'
