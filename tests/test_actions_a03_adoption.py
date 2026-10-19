from __future__ import annotations
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.actions_adoption import AdoptionError, adopt, _yaml

PIN = '83eb211f14bfe16e37e246162b6a47a452403c9d'


def _git(root, *args):
    subprocess.run(['git', '-C', str(root), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _fixture(tmp_path):
    root = tmp_path / 'repo'; root.mkdir()
    (root / '.devtool.toml').write_text('''[project]
runner = "python"
[workspace]
default_target = "app"
[wrapper]
schema = 2
[targets.app]
runner = "python"
root = "."
[targets.app.jobs.test]
runner = "command"
command = ["python3", "-m", "pytest"]
[targets.app.workflows]
ci = [{id="unit", ref="job:test"}]
''')
    wrapper = root / 'devtoolw'; wrapper.write_text('#!/bin/sh\nexit 0\n'); wrapper.chmod(0o755)
    _git(root, 'init', '-q')
    _git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'add', '.')
    _git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-qm', 'fixture')
    return root


def test_a03_review_is_read_only(tmp_path):
    r = _fixture(tmp_path)
    result = adopt(r, target='app', workflow='ci', devtool_sha=PIN)
    assert result['overall'] == 'PASS_ACTIONS_A03_REVIEW_ONLY'
    assert result['target_local_jobs'] == 1
    assert not (r / '.github').exists()
    assert b'ci-app-ci' not in (r / '.devtool.toml').read_bytes()


def test_a03_writes_pinned_workflow_and_wrapper_alias(tmp_path):
    r = _fixture(tmp_path)
    before = (r / '.devtool.toml').read_bytes()
    out = adopt(r, target='app', workflow='ci', devtool_sha=PIN, write=True)
    assert out['overall'] == 'PASS_ACTIONS_A03_ADOPTION_WRITTEN'
    path = r / out['workflow_path']
    raw = path.read_bytes()
    assert PIN.encode() in raw and b'uses: actions/checkout@11bd719' in raw
    assert b'actions/upload-artifact@ea165' in raw
    assert b'workflow_dispatch:' in raw
    assert b'pull_request:' not in raw
    assert hashlib.sha256(raw).hexdigest() == out['workflow_sha256']
    assert (r / '.devtool.toml').read_bytes().startswith(before)
    assert b'[wrapper.commands.ci-app-ci]' in (r / '.devtool.toml').read_bytes()
    verified = adopt(r, target='app', workflow='ci', devtool_sha=PIN, verify=True)
    assert verified['overall'] == 'PASS_ACTIONS_A03_ADOPTION_VERIFIED'


def test_a03_reject_moving_ref_and_invalid_slug(tmp_path):
    r = _fixture(tmp_path)
    for bad in ['main', 'v4', 'abc123', 'F'*40]:
        with pytest.raises(AdoptionError):
            adopt(r, target='app', workflow='ci', devtool_sha=bad)
    for bad in ['../app', 'app;echo', 'two words', 'app.x']:
        with pytest.raises(AdoptionError):
            adopt(r, target=bad, workflow='ci', devtool_sha=PIN)


def test_a03_does_not_overwrite_existing_workflow(tmp_path):
    r = _fixture(tmp_path)
    path = r / '.github/workflows/devtool-app-ci.yml';path.parent.mkdir(parents=True);path.write_text('hand authored')
    with pytest.raises(AdoptionError, match='overwriting'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN)
    with pytest.raises(AdoptionError, match='already exists'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN, write=True)
    assert path.read_text() == 'hand authored'


def test_a03_verify_detects_workflow_tamper(tmp_path):
    r = _fixture(tmp_path)
    out=adopt(r, target='app', workflow='ci', devtool_sha=PIN, write=True)
    f=r/out['workflow_path'];f.write_text(f.read_text()+'\n# injected\n')
    with pytest.raises(AdoptionError, match='changed or mismatched'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN, verify=True)


def test_a03_refuses_dirty_source_before_writing(tmp_path):
    r = _fixture(tmp_path); (r / '.devtool.toml').write_text((r / '.devtool.toml').read_text()+'\n#dirty\n')
    with pytest.raises(AdoptionError, match='clean Git'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN, write=True)
    assert not (r / '.github').exists()


def test_a03_rejects_missing_job_or_wrapper(tmp_path):
    r = _fixture(tmp_path)
    (r / 'devtoolw').unlink()
    with pytest.raises(AdoptionError, match='devtoolw'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN)
    wrapper=r/'devtoolw';wrapper.write_text('#!/bin/sh\n');wrapper.chmod(0o755)
    with pytest.raises(AdoptionError):
        adopt(r, target='app', workflow='absent', devtool_sha=PIN)


def test_a03_refuses_symlink_destination(tmp_path):
    r = _fixture(tmp_path)
    (r / '.github').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(AdoptionError, match='symlinks'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN)


def test_a03_flow_is_non_mutating_remote():
    text = _yaml('app','ci',PIN)
    assert 'git -C "$RUNNER_TEMP/devtool-source" checkout --detach "$DEVTOOL_SOURCE_SHA"' in text
    assert 'cancel-in-progress: false' in text
    assert 'permissions:\n  contents: read' in text
    assert 'gh release' not in text.lower() and 'publish' not in text.lower()
    assert 'devtool-invoke.json' in text


def test_a03_verify_detects_alias_tamper(tmp_path):
    r = _fixture(tmp_path)
    adopt(r, target='app', workflow='ci', devtool_sha=PIN, write=True)
    p=r/'.devtool.toml'
    p.write_text(p.read_text().replace('workflow = "ci"', 'workflow = "other"'))
    with pytest.raises(AdoptionError, match='Invalid Devtool project contract|missing or changed'):
        adopt(r, target='app', workflow='ci', devtool_sha=PIN, verify=True)
