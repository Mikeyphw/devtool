"""R09 current-source campaign and fail-closed observation tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from devtool import rmp_r09_campaign as campaign

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def checked_out(tmp_path_factory):
    target = tmp_path_factory.mktemp('r09-committed') / 'devtool'
    subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(ROOT), str(target)], check=True)
    for rel in ('devtool/rmp_r09_campaign.py', 'docs/actions/DEVTOOL_R09_ACCEPTANCE_CONTRACT_20261021.json'):
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)
    subprocess.run(['git', '-C', str(target), 'add', '-A'], check=True)
    subprocess.run(['git', '-C', str(target), '-c', 'user.name=R09 fixture',
                    '-c', 'user.email=r09@example.invalid', 'commit', '--allow-empty', '-qm',
                    'R09 source fixture'], check=True)
    return target


def test_current_clean_source_inventory_honestly_pending(checked_out):
    result = campaign.assess(checked_out)
    assert result['overall'] == 'PASS_R09_INVENTORY_TESTS_NOT_RUN_EXTERNAL_OPEN', result.get('reason')
    assert result['source_pass'] is False
    assert result['native_pass'] is False
    assert result['hosted_pass'] is False
    assert result['consumer_pass'] is False
    assert result['device_pass'] is False
    assert result['formal_release_qualified'] is False
    assert result['security_inventory']['n01_rows'] == 308
    assert result['security_inventory']['q02_historical_probes'] == 20
    assert result['security_inventory']['q02_historical_mismatches'] == 6
    assert result['matrix_sha256']
    assert not result['evidence']


def test_source_tests_do_not_promote_other_gates(monkeypatch, checked_out):
    monkeypatch.setattr(campaign, '_selected_tests', lambda *args, **kwargs:
                        {'status': 'PASS', 'passed': 100, 'skipped': 0})
    monkeypatch.setattr(campaign, '_native_smoke', lambda: {
        'native_confirmed': False, 'installed_cli': 'NOT_CHECKED'})
    result = campaign.assess(checked_out, run_tests=True)
    assert result['source_pass'] is True
    assert result['native_pass'] is False
    assert result['overall'] == 'PASS_R09_LOCAL_SOURCE_EXTERNAL_OPEN'
    assert not result['hosted_pass'] and not result['formal_release_qualified']


def test_native_observation_requires_tests_and_cli(monkeypatch, checked_out):
    monkeypatch.setattr(campaign, '_selected_tests', lambda *args, **kwargs: {'status': 'PASS'})
    monkeypatch.setattr(campaign, '_native_smoke', lambda: {
        'native_confirmed': True, 'installed_cli': 'HELP_FAILED'})
    assert campaign.assess(checked_out, run_tests=True)['native_pass'] is False
    monkeypatch.setattr(campaign, '_native_smoke', lambda: {
        'native_confirmed': True, 'installed_cli': 'HELP_PASS'})
    accepted = campaign.assess(checked_out, run_tests=True)
    assert accepted['native_pass'] is True
    assert accepted['formal_release_qualified'] is False


def test_bad_security_or_failed_regression_blocks_source(monkeypatch, checked_out):
    monkeypatch.setattr(campaign, '_selected_tests', lambda *args, **kwargs: {'status': 'FAILED'})
    result = campaign.assess(checked_out, run_tests=True)
    assert result['overall'] == 'BLOCKED_R09_SOURCE'
    assert result['source_pass'] is False


def test_selected_skips_cannot_be_reported_as_completed_source(monkeypatch, checked_out):
    monkeypatch.setattr(campaign, '_selected_tests', lambda *args, **kwargs: {
        'status': 'PASS', 'passed': 200, 'skipped': 4})
    result = campaign.assess(checked_out, run_tests=True)
    assert result['overall'] == 'BLOCKED_R09_SOURCE'
    assert result['source_pass'] is False
    assert result['native_pass'] is False
    assert result['formal_release_qualified'] is False


def test_json_claims_cannot_make_hosted_or_release_pass(tmp_path, checked_out):
    data = {
        'producer': 'fake-ci', 'source_tree': campaign._git(checked_out, 'rev-parse', 'HEAD^{tree}'),
        'source_commit': campaign._git(checked_out, 'rev-parse', 'HEAD'),
        'release_qualified': True, 'device_pass': True, 'hosted_pass': True,
        'formal_h1_closed': True, 'ci': 'PASS', 'signed': True,
    }
    path = tmp_path / 'self_signed.json'
    path.write_text(json.dumps(data))
    result = campaign.assess(checked_out, evidence=(path,))
    observed = result['evidence'][0]
    assert observed['source_identity'] == 'MATCH_CURRENT_SOURCE'
    assert observed['authority'] == 'OPERATOR_SUPPLIED_UNTRUSTED'
    assert observed['qualified'] is False
    assert observed['sha256'] == campaign._hash(path.read_bytes())
    for flag in ('hosted_pass', 'device_pass', 'formal_release_qualified', 'security_formal_pass'):
        assert result[flag] is False


def test_other_source_and_missing_source_never_match(tmp_path, checked_out):
    foreign = tmp_path / 'foreign.json'
    foreign.write_text(json.dumps({'source_commit': 'a' * 40, 'source_tree': 'b' * 40}))
    missing = tmp_path / 'missing.json'
    missing.write_text('{"release_qualified":true}')
    bad = tmp_path / 'bad.json'
    bad.write_text('["not an object"]')
    result = campaign.assess(checked_out, evidence=(foreign, missing, bad))
    assert [v['source_identity'] for v in result['evidence'][:2]] == [
        'FOREIGN_SOURCE', 'MISSING_EXACT_SOURCE_IDENTITY']
    assert result['evidence'][2]['format'] == 'REFUSED'
    assert result['overall'].startswith('PASS_R09_INVENTORY')


def test_symlink_and_oversized_receipts_refused(tmp_path, checked_out):
    original = tmp_path / 'original.json'
    original.write_text('{}')
    shortcut = tmp_path / 'alias.json'
    shortcut.symlink_to(original)
    big = tmp_path / 'big.json'
    big.write_bytes(b' ' * (campaign.MAX_EVIDENCE_BYTES + 1))
    result = campaign.assess(checked_out, evidence=(shortcut, big))
    assert all(v['format'] == 'REFUSED' for v in result['evidence'])
    assert result['overall'].startswith('PASS_R09_INVENTORY')


def test_bad_directory_symlink_or_excess_items_blocks(tmp_path, checked_out):
    source = tmp_path / 'source'
    source.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    blocked = campaign.assess(checked_out, evidence_dirs=(alias,))
    assert blocked['overall'] == 'BLOCKED_R09_SOURCE'
    for i in range(campaign.MAX_EVIDENCE_FILES + 1):
        (source / f'file-{i}').touch()
    assert campaign.assess(checked_out, evidence_dirs=(source,))['overall'].startswith('BLOCKED')


def test_clean_tree_and_git_object_required(tmp_path, checked_out):
    path = checked_out / campaign.CONTRACT
    saved = path.read_bytes()
    try:
        path.write_text('{"forgery":true}')
        blocked = campaign.assess(checked_out)
        assert blocked['overall'] == 'BLOCKED_R09_SOURCE'
    finally:
        path.write_bytes(saved)
    source = tmp_path / 'untracked'
    (checked_out / 'UNTRACKED_R09_SENTINEL').write_text('dirty')
    try:
        assert campaign.assess(checked_out)['overall'] == 'BLOCKED_R09_SOURCE'
    finally:
        (checked_out / 'UNTRACKED_R09_SENTINEL').unlink()


def test_cli_explicit_private_output_and_require_full(tmp_path, checked_out):
    env = {**os.environ, 'PYTHONPATH': str(checked_out), 'PYTHONDONTWRITEBYTECODE': '1'}
    output = tmp_path / 'report.json'
    command = [sys.executable, '-m', 'devtool.rmp_r09_campaign', '--repo', str(checked_out),
               '--output', str(output)]
    run = subprocess.run(command, cwd=checked_out, capture_output=True, text=True,
                         timeout=40, env=env)
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    assert report['formal_release_qualified'] is False
    assert (output.stat().st_mode & 0o777) == 0o600
    repeat = subprocess.run(command, cwd=checked_out, capture_output=True,
                            timeout=40, env=env)
    assert repeat.returncode != 0
    demand = subprocess.run([sys.executable, '-m', 'devtool.rmp_r09_campaign',
                             '--repo', str(checked_out), '--require-full'], cwd=checked_out,
                            capture_output=True, timeout=40, env=env)
    assert demand.returncode == 3


def test_missing_repository_fails_closed(tmp_path):
    report = campaign.assess(tmp_path)
    assert report['overall'] == 'BLOCKED_R09_SOURCE'
    assert report['release_qualified'] is False
