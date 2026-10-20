"""R02: stable source-bound consumer review without unconsented remote execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from devtool.actions_adoption import AdoptionError, _yaml, adopt, _SETUP_GO_SHA
from tests.test_actions_a09_ci_host_contract import repo as skybind_fixture
from tests.test_actions_a03_adoption import _fixture as python_fixture

SHA = 'f40381625178dea16aad189626617e7967b3da41'


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', '-C', str(root), *args], text=True).strip()


def test_go_review_binds_commit_tree_job_and_declared_host(tmp_path: Path):
    repo = skybind_fixture(tmp_path)
    before = _git(repo, 'status', '--porcelain')
    review = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    assert review['overall'] == 'PASS_ACTIONS_A03_REVIEW_ONLY'
    assert review['consumer_commit'] == _git(repo, 'rev-parse', 'HEAD')
    assert review['consumer_tree'] == _git(repo, 'rev-parse', 'HEAD^{tree}')
    assert review['consumer_worktree_clean'] is True
    assert review['ci_host_profile'] == 'go'
    assert review['host_prerequisites'][-2:] == ['go', 'go.mod']
    assert review['host_admission'] == 'SOURCE_DECLARATION_ONLY_NOT_TOOLCHAIN_EXECUTION'
    assert review['remote_dispatch'] is False
    assert review['source_attested'] is False
    assert review['release_qualified'] is False
    assert review['device_qualified'] is False
    assert len(review['selected_jobs']) == 1
    j = review['selected_jobs'][0]
    assert j['name'] == 'native'
    assert j['declared_read_only'] is True
    assert j['program'] == 'bash' and j['host_toolchain'] == 'go'
    assert len(bytes.fromhex(review['review_sha256'])) == 32
    assert _git(repo, 'status', '--porcelain') == before
    assert not (repo / '.github').exists()


def test_review_is_stable_reusable_after_source_commit_not_dirty(tmp_path: Path):
    repo = skybind_fixture(tmp_path)
    first = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    second = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    assert first['review_sha256'] == second['review_sha256']
    (repo/'scripts/devtool/check_native.sh').write_text('go version\necho changed\n')
    dirty = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    assert dirty['consumer_worktree_clean'] is False
    with pytest.raises(AdoptionError, match='clean Git'):
        adopt(repo, target='skybind', workflow='native', devtool_sha=SHA, write=True)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=R02','-c','user.email=r02@example.test','commit','-qm','new-source'],check=True)
    changed = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    assert changed['review_sha256'] != first['review_sha256']
    assert changed['consumer_worktree_clean'] is True


def test_write_and_verify_keep_no_qualified_provenance(tmp_path: Path):
    repo = skybind_fixture(tmp_path)
    plan = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    written = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA, write=True)
    assert written['review_sha256'] == plan['review_sha256']
    assert written['writes_performed'] is True
    verified = adopt(repo, target='skybind', workflow='native', devtool_sha=SHA, verify=True)
    assert verified['overall'] == 'PASS_ACTIONS_A03_ADOPTION_VERIFIED'
    assert verified['release_qualified'] is False
    assert verified['source_attested'] is False
    assert verified['remote_dispatch'] is False
    t = (repo / written['workflow_path']).read_text()
    assert f'actions/setup-go@{_SETUP_GO_SHA}' in t
    assert 'go-version-file: go.mod' in t
    assert 'workflow_dispatch:' in t and 'pull_request:' not in t


def test_python_shell_workflow_sha_remains_unmodified(tmp_path: Path):
    repo = python_fixture(tmp_path)
    review = adopt(repo, target='app', workflow='ci', devtool_sha=SHA)
    assert review['ci_host_profile'] == 'python-shell'
    assert review['host_prerequisites'] == ['python3', 'bash', 'sh']
    assert 'setup-go' not in _yaml('app', 'ci', SHA)
    assert hashlib.sha256(_yaml('app','ci',SHA).encode()).hexdigest() == review['workflow_sha256'] == '76382bc6c5ee0ec474e5b25ae0ce881f01cfefb6c79f84cabd5c07872320aca2'


@pytest.mark.parametrize('metadata,reason', [
    ('read_only = true, ci_toolchain = "jdk"', 'unsupported'),
    ('read_only = true, ci_toolchain = "android-sdk"', 'unsupported'),
    ('read_only = true, ci_toolchain = "root"', 'unsupported'),
    ('read_only = true, ci_toolchain = "$(shell)"', 'unsupported'),
    ('read_only = true, ci_toolchain = "python-shell"', 'conflicts'),
])
def test_unsupported_or_opaque_host_is_refused_before_writes(tmp_path: Path, metadata: str, reason: str):
    repo = skybind_fixture(tmp_path, metadata=metadata)
    with pytest.raises(AdoptionError, match=reason):
        adopt(repo, target='skybind', workflow='native', devtool_sha=SHA)
    assert not (repo/'.github').exists()


def test_conflicting_explicit_python_host_vs_go_runner_is_refused(tmp_path: Path):
    repo = skybind_fixture(tmp_path, args='["go", "test", "./..."]', metadata='read_only = true, ci_toolchain = "python-shell"')
    with pytest.raises(AdoptionError,match='conflicts'):
        adopt(repo,target='skybind',workflow='native',devtool_sha=SHA)


def test_review_hash_binds_selected_inputs(tmp_path: Path):
    repo = skybind_fixture(tmp_path)
    old = adopt(repo,target='skybind',workflow='native',devtool_sha=SHA)
    conf=repo/'.devtool.toml'
    conf.write_text(conf.read_text().replace('inputs = ["go.mod", "scripts/devtool/check_native.sh"]', 'inputs = ["go.mod", "scripts/devtool/other.sh"]'))
    revised=adopt(repo,target='skybind',workflow='native',devtool_sha=SHA)
    assert old['review_sha256'] != revised['review_sha256']
    assert old['selected_jobs'][0]['inputs_sha256'] != revised['selected_jobs'][0]['inputs_sha256']
    assert not (repo/'.github').exists()


def test_refuse_device_root_and_unprovisioned_android(tmp_path: Path):
    for idx, (args, metadata) in enumerate([
        ('["adb", "shell", "id"]','read_only = true'),
        ('["bash", "scripts/devtool/check_native.sh"]','read_only = true, device_required = true'),
        ('["bash", "scripts/devtool/check_native.sh"]','read_only = true, release_signed = true'),
        ('["gradlew", "assembleDebug"]','read_only = true'),
    ]):
        repo=skybind_fixture(tmp_path/str(idx),args=args,metadata=metadata)
        with pytest.raises(AdoptionError):
            adopt(repo,target='skybind',workflow='native',devtool_sha=SHA)
        assert not (repo/'.github').exists()
