"""RMP-C02 caller entrypoint and parent-alias safety acceptance checks."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from devtool.artifact_cleanup_worker import cleanup_source_snapshot, _unlink_owned_source
from devtool.rmp_c02_acceptance import LANES


def test_parent_symlink_retarget_cannot_redirect_consumed_artifact_unlink(tmp_path: Path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir(); second.mkdir()
    owned = first / 'overlay.zip'
    victim = second / 'overlay.zip'
    owned.write_bytes(b'original consumed source')
    victim.write_bytes(b'unrelated newer source')
    alias = tmp_path / 'selected-downloads'
    alias.symlink_to(first, target_is_directory=True)
    selected = alias / 'overlay.zip'
    original = cleanup_source_snapshot(selected)
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)
    with pytest.raises(ValueError, match='identity changed'):
        _unlink_owned_source(selected, original)
    assert owned.read_bytes() == b'original consumed source'
    assert victim.read_bytes() == b'unrelated newer source'


def test_direct_external_worker_refuses_file_without_private_apply_capability(tmp_path: Path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    source = tmp_path / 'download.zip'
    source.write_bytes(b'not consumed')
    command = [sys.executable, '-m', 'devtool.artifact_cleanup_worker',
               '--repo-root', str(repo), '--reason', 'after successful apply', '--', str(source)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload['returncode'] != 0
    assert source.read_bytes() == b'not consumed'


def test_local_acceptance_inventory_only_names_repository_controlled_tests():
    assert 'tests/test_rmp_c02_acceptance.py' in LANES
    assert len(LANES) == len(set(LANES))
    assert all(path.startswith('tests/') and path.endswith('.py') and '..' not in path for path in LANES)
