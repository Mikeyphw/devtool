"""R08 local security inventory and negative authority protection."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import subprocess
import sys

import pytest

from devtool.rmp_r08_assessment import (assess, check_phase4, tracked_sources,
                                         FAMILY_REQUIREMENTS)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def accepted_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Reproduce the post-overlay Git commit; the authoring tree is intentionally dirty."""
    dst = tmp_path_factory.mktemp('r08_accepted') / 'devtool'
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(ROOT), str(dst)], check=True)
    for rel in ('devtool/rmp_r08_assessment.py', 'tests/test_rmp_r08_assessment.py'):
        shutil.copy2(ROOT / rel, dst / rel)
    subprocess.run(['git', '-C', str(dst), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(dst), '-c', 'user.name=R08 fixture',
                    '-c', 'user.email=r08@example.invalid', 'commit', '--allow-empty', '-qm', 'R08 fixture commit'], check=True)
    return dst


def test_current_source_assessment_does_not_promote_any_formal_gate(accepted_source: Path):
    result = assess(accepted_source)
    assert result['overall'] == 'PASS_R08_SOURCE_INVENTORY_EXTERNAL_GATES_OPEN', result.get('reason')
    assert result['source_integrity'].startswith('PASS')
    assert result['n01']['rows'] == 308
    assert result['q02']['original_probes'] == 20
    assert result['q02']['historical_mismatches'] == 6
    assert result['phase4']['status'] == 'PENDING_EXACT_NATIVE_RECEIPT'
    assert len(result['external_gates']) == 6
    assert all(v['status'] == 'BLOCKED_EXTERNAL' for v in result['external_gates'].values())
    assert len(result['families']) == len(FAMILY_REQUIREMENTS)
    assert result['selected_tests']['status'] == 'NOT_RUN'
    for key in ('release_qualified', 'formal_h1_closed', 'formal_h2_closed', 'formal_u01_closed'):
        assert result[key] is False


def test_malformed_phase4_input_not_accepted(accepted_source: Path):
    result = assess(accepted_source, phase4_commit='00' * 20)
    assert result['overall'].startswith('BLOCKED')
    assert 'Both' in result['reason']


def test_foreign_and_short_phase4_commit_refused(tmp_path: Path):
    fake = tmp_path / 'forged.json'
    fake.write_text(json.dumps({'overall': 'PASS_H1_PHASE4_CROSS_CONSUMER_ACCEPTANCE',
                                'release_qualified': True}))
    with pytest.raises(ValueError, match='full SHA-1'):
        check_phase4(ROOT, fake, 'abc')
    with pytest.raises(ValueError, match='Git identity'):
        check_phase4(ROOT, fake, 'f' * 40)


def test_current_source_inputs_pinned_to_git(accepted_source: Path):
    digests = tracked_sources(accepted_source)
    assert len(digests) >= 10
    assert all(len(value) == 64 for value in digests.values())


def test_source_tamper_and_untracked_file_fail_closed(tmp_path: Path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    outcome = assess(tmp_path)
    assert outcome['overall'].startswith('BLOCKED')
    assert 'evidence' in outcome['reason'] or 'Git' in outcome['reason']


def test_readonly_command_json_no_output_artifact(tmp_path: Path, accepted_source: Path):
    subprocess.run([sys.executable, '-m', 'devtool.rmp_r08_assessment',
                    '--repo', str(accepted_source)], cwd=accepted_source, capture_output=True,
                   text=True, check=True, timeout=30)
    assert not any(tmp_path.glob("*.json"))


def test_phase4_old_receipt_remains_insufficient_for_release(tmp_path: Path, accepted_source: Path):
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps({'overall': 'PASS_H1_PHASE4_CROSS_CONSUMER_ACCEPTANCE',
                                'release_qualified': False, 'pytest_passed': 206}))
    result = assess(accepted_source, phase4_receipt=path, phase4_commit='a' * 40)
    assert result['overall'].startswith('BLOCKED')
    assert result['release_qualified'] is False
