"""B01: no stale/cross-lane log reuse for exact-source G3R receipts."""
from __future__ import annotations

import pytest

from devtool.g3r_evidence_gate import REQUIRED_LANES
from devtool.g3r_independent_verify import expected_lane_log_name, sha, validate_lane_log


def test_canonical_lane_mapping_matches_all_thirteen_producer_lanes():
    assert len(REQUIRED_LANES) == 13
    names = [expected_lane_log_name(lane) for lane in REQUIRED_LANES]
    assert len(set(names)) == 13
    assert all(name.endswith('.log') for name in names)


def test_lane_log_requires_matching_lane_identity_and_nonempty_output(tmp_path):
    runs = tmp_path / 'runs'; runs.mkdir()
    content = b'1 passed\n'; name = expected_lane_log_name('tests/test_android_instrumentation.py')
    (runs / name).write_bytes(content)
    receipt = {'status': 'PASS', 'exit_code': 0, 'output_file': name, 'output_sha256': sha(content)}
    seen = set()
    validate_lane_log(tmp_path, 'tests/test_android_instrumentation.py', receipt, seen)
    assert seen == {name}
    with pytest.raises(ValueError, match='duplicate lane log'):
        validate_lane_log(tmp_path, 'tests/test_android_instrumentation.py', receipt, seen)
    with pytest.raises(ValueError, match='lane log identity mismatch'):
        validate_lane_log(tmp_path, 'tests/test_dist_x04_operational_adoption.py', receipt, set())


@pytest.mark.parametrize('change, expected', [
    ({'output_file': 'native-android_g1_gate.log'}, 'lane log identity mismatch'),
    ({'output_file': '../escape'}, 'lane log identity mismatch'),
    ({'output_sha256': '0' * 64}, 'log SHA mismatch'),
    ({'exit_code': True}, 'invalid exit code'),
])
def test_forged_lane_receipts_are_rejected(tmp_path, change, expected):
    (tmp_path / 'runs').mkdir()
    lane = 'tests/test_android_instrumentation.py'
    name = expected_lane_log_name(lane)
    (tmp_path / 'runs' / name).write_bytes(b'1 passed\n')
    receipt = {'status': 'PASS', 'exit_code': 0, 'output_file': name,
               'output_sha256': sha(b'1 passed\n')}
    receipt.update(change)
    with pytest.raises(ValueError, match=expected):
        validate_lane_log(tmp_path, lane, receipt, set())


def test_empty_success_log_and_missing_success_log_rejected(tmp_path):
    (tmp_path / 'runs').mkdir()
    lane = 'android_g1_gate'; name = expected_lane_log_name(lane)
    (tmp_path / 'runs' / name).write_bytes(b'')
    with pytest.raises(ValueError, match='empty execution log'):
        validate_lane_log(tmp_path, lane,
                          {'status': 'PASS', 'exit_code': 0, 'output_file': name,
                           'output_sha256': sha(b'')}, set())
    with pytest.raises(ValueError, match='passed lane missing log'):
        validate_lane_log(tmp_path, lane, {'status': 'PASS', 'exit_code': 0}, set())
