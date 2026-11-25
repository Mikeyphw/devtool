"""DT-33: runner counts from terminal evidence, never contract prose."""
from devtool.models import OperationResult
from devtool.summary import _extract_test_totals


def result(output, *, name='test'):
    return OperationResult(name=name, argv=['python3', '-B', '-m', 'pytest'],
                           returncode=0, duration_seconds=0.01,
                           output=output, issues=[])


def test_contract_description_does_not_manufacture_failures():
    assert _extract_test_totals([result('PASS UI-05 failed hook/group routes and same-package identity regression tests')]) is None


def test_pytest_summary_real_failures_and_skips():
    assert _extract_test_totals([result('========= 3 failed, 15 passed, 2 skipped in 0.32s =========')]) == (15, 3, 2, 20)


def test_pytest_success_and_ansi():
    assert _extract_test_totals([result('\x1b[32m== 18 passed in 0.02s ==\x1b[0m')]) == (18, 0, 0, 18)


def test_pytest_multi_suite_aggregate():
    assert _extract_test_totals([result('2 passed in 0.01s'), result('1 failed, 4 passed in 0.03s')]) == (6, 1, 0, 7)


def test_pytest_error_counts_as_failure():
    assert _extract_test_totals([result('==== 1 error, 3 passed in 0.02s ====')]) == (3, 1, 0, 4)


def test_unittest_multiple_successful_suites():
    text='Ran 15 tests in 0.001s\n\nOK\nRan 8 tests in 0.002s\n\nOK\nRan 25 tests in 0.03s\n\nOK'
    assert _extract_test_totals([result(text)]) == (48, 0, 0, 48)


def test_unittest_failure_and_skip():
    assert _extract_test_totals([result('Ran 5 tests in 0.02s\n\nFAILED (failures=1, errors=1, skipped=1)')]) == (2, 2, 1, 5)


def test_unittest_truncated_output_is_unknown():
    assert _extract_test_totals([result('Ran 5 tests in 0.02s\n\nTerminated')]) is None


def test_same_line_contract_and_true_test_summary():
    assert _extract_test_totals([result('PASS UI-05 failed hook routes\nRan 3 tests in 0.01s\nOK')]) == (3, 0, 0, 3)
