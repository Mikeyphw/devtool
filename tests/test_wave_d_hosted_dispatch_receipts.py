"""Wave D fail-closed boundaries: offline, no Gradle, network or GitHub POST."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from devtool.actions_dispatch import local_head_for_dispatch, GitHubDispatchPreflightError, dispatch_workflow
from devtool.github_actions import GitHubActionsClient, GitHubActionsError
from devtool.actions_artifacts import verify_embedded_debug_receipt
from devtool.android._standalone import BuildError, enforce_hosted_gradle_only, main as standalone_main
from devtool.cli import build_parser
from tests.test_actions_02_workflow_dispatch import prepared

SHA = 'a'*40
REPO = 'owner/repo'
REMOTE = {'id': 425, 'head_sha': SHA, 'run_attempt': 2}


def init_repo(path):
    subprocess.run(['git','init','-q',str(path)], check=True)
    subprocess.run(['git','-C',str(path),'config','user.name','CI'], check=True)
    subprocess.run(['git','-C',str(path),'config','user.email','ci@example.invalid'], check=True)
    (path/'README.md').write_text('source\n')
    subprocess.run(['git','-C',str(path),'add','README.md'], check=True)
    subprocess.run(['git','-C',str(path),'commit','-q','-m','test'], check=True)
    return path


def write_cfg(root, mode):
    (root/'.devtool.toml').write_text('[execution]\ngradle_execution = '+json.dumps(mode)+'\n')


def test_hosted_guard_rejects_native_builder_before_chroot(monkeypatch,tmp_path):
    write_cfg(tmp_path,'github-actions-only')
    (tmp_path/'settings.gradle').write_text('rootProject.name = \"test\"\n')
    monkeypatch.setattr('devtool.android._standalone.maybe_reexec_in_chroot',
                        lambda _:pytest.fail('chroot must never be invoked'))
    with pytest.raises(BuildError, match='LOCAL_GRADLE_FORBIDDEN'):
        standalone_main([str(tmp_path)])
    with pytest.raises(BuildError, match='LOCAL_GRADLE_FORBIDDEN'):
        enforce_hosted_gradle_only(tmp_path)
    enforce_hosted_gradle_only(tmp_path,bootstrap_only=True)


def test_hosted_policy_invalid_setting_fails_closed(tmp_path):
    write_cfg(tmp_path,'typo-unsafe')
    with pytest.raises(BuildError, match='Unsupported'):
        enforce_hosted_gradle_only(tmp_path)
    write_cfg(tmp_path,'local-allowed')
    enforce_hosted_gradle_only(tmp_path)


def test_local_git_head_rejects_dirty_and_unborn(tmp_path):
    init_repo(tmp_path)
    clean=local_head_for_dispatch(tmp_path)
    assert len(clean)==40
    (tmp_path/'untracked.txt').write_text('bad')
    with pytest.raises(GitHubDispatchPreflightError, match='clean committed'):
        local_head_for_dispatch(tmp_path)
    (tmp_path/'untracked.txt').unlink()
    (tmp_path/'README.md').write_text('changed')
    with pytest.raises(GitHubDispatchPreflightError):local_head_for_dispatch(tmp_path)
    with pytest.raises(GitHubDispatchPreflightError):local_head_for_dispatch(tmp_path/'missing')


def test_local_head_flag_added_to_canonical_actions_plan_and_run():
    parser=build_parser()
    for command in ('plan','run'):
        argv=['actions',command,'ci.yml','--ref','main','--require-local-head']
        if command=='run':argv.append('--yes')
        assert parser.parse_args(argv).require_local_head is True


def test_wrong_remote_sha_aborts_prior_to_post_or_journal(tmp_path):
    opener=prepared(); opener.replies.pop()  # no POST response permitted
    with pytest.raises(GitHubDispatchPreflightError, match='SHA'):
        dispatch_workflow(GitHubActionsClient('token',opener=opener),REPO,'ci.yml','main',
                          {'mode':'debug'},execute=True,state_root=tmp_path,expected_source_sha='b'*40)
    assert not any(x.get_method()=='POST' for x in opener.requests)
    assert not list(tmp_path.glob('*.json'))


def embedded_zip(path, **mutations):
    apk=mutations.pop('apk',b'APK-CONTENT-TEST')
    receipt={
        'schema_version':2,'repository':REPO,'source_sha':SHA,
        'github_run_id':425,'github_run_attempt':2,
        'workflow_run_url':f'https://github.com/{REPO}/actions/runs/425',
        'artifact_member':'app-debug.apk','apk_sha256':hashlib.sha256(apk).hexdigest(),
        'apk_size_bytes':len(apk),'signing_certificate_sha256':'1'*64,
        'apk_signature_verified':True,'apk_manifest_verified':True,
        'unit_tests_and_lint_passed':True,'device_qualified':False,
        'qualified_release':False,'variant':'debug','format':'apk',
        'module':'app','package':'com.example.debug',
    }
    receipt.update(mutations)
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        z.writestr('app-debug.apk',apk)
        z.writestr('build-evidence.json',json.dumps(receipt))
    return path


def test_receipt_integrity_verified_without_signature_qualification(tmp_path):
    path=embedded_zip(tmp_path/'a.zip')
    result=verify_embedded_debug_receipt(path,slug=REPO,remote=REMOTE)
    assert result['receipt_integrity_verified'] is True
    assert result['apk_signature_independently_verified'] is False
    assert result['qualified_release'] is False


@pytest.mark.parametrize('tamper',[
    {'github_run_attempt':3}, {'github_run_id':426}, {'source_sha':'b'*40},
    {'repository':'other/repo'}, {'apk_sha256':'0'*64},
    {'apk_signature_verified':False}, {'qualified_release':True},
    {'artifact_member':'../bad.apk'}, {'signing_certificate_sha256':'nope'},
])
def test_receipt_tampering_rejected(tmp_path,tamper):
    with pytest.raises(GitHubActionsError):
        verify_embedded_debug_receipt(embedded_zip(tmp_path/'a.zip',**tamper),slug=REPO,remote=REMOTE)


def test_archive_without_receipt_is_unqualified(tmp_path):
    path=tmp_path/'diag.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('test-results.xml','failures')
    result=verify_embedded_debug_receipt(path,slug=REPO,remote=REMOTE)
    assert result['receipt_status']=='absent' and result['receipt_integrity_verified'] is False
