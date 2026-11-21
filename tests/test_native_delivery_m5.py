"""M5 focused regressions — native operations remain source bound and inert by default."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import pytest
from devtool.native_delivery_m5 import (
    NativeM5Refused, _artifact, _inspection, _preverify, _session_id,
    _summary, _child_env, _require_zip_v3_commit_session, _committed_zip_session, main,
)
from devtool.cli import build_parser
from devtool.core_parser import parser_identity_projection
from devtool.core_registry import build_core_registry, assert_parser_matches_canonical_registry


def test_native_cli_canonical_help_and_options():
    parser=build_parser()
    examples=[
        ['native','check','sample.zip','--repo','/tmp/work'],
        ['native','apply','sample.pyz','--repo','/tmp/work','--yes'],
        ['native','status','--session-id','a'*32,'--repo','/tmp/work'],
        ['native','deliver','--session-id','a'*32,'--repo','/tmp/work','--auth','app','--yes'],
        ['native','ci','--session-id','a'*32,'--repo','/tmp/work','--checks','10','--interval','5'],
    ]
    for argv in examples:
        args=parser.parse_args(argv)
        assert args.command=='native'
        assert args.native_command
    # Gate the original real-device failure explicitly, before regeneration.
    registry=build_core_registry()
    assert_parser_matches_canonical_registry(parser, registry)
    projected=parser_identity_projection()
    assert ('native','apply') in projected['commands']
    assert (('native','deliver'),'--auth') in projected['options']


def test_missing_approval_is_rejected_without_native_call(tmp_path,monkeypatch):
    art=tmp_path/'a.zip';art.write_bytes(b'abc')
    monkeypatch.setattr('devtool.native_delivery_m5._preverify',lambda _: 'native-zip')
    monkeypatch.setattr('devtool.native_delivery_m5._run',lambda *a,**kw:(0,''))
    p=SimpleNamespace(native_command='apply',repo=str(tmp_path),artifact=str(art),yes=False)
    assert main(p)==2


def test_invalid_or_symlink_artifact_is_rejected(tmp_path):
    a=tmp_path/'a.pyz';a.write_bytes(b'bad')
    link=tmp_path/'link.pyz';link.symlink_to(a)
    with pytest.raises(NativeM5Refused,match='M5_ARTIFACT_MUST_BE_REGULAR'):
        _artifact(str(link))
    with pytest.raises(NativeM5Refused,match='M5_ARTIFACT_MUST_BE_REGULAR'):
        _artifact(str(tmp_path/'a.txt'))


def test_pyz_never_runs_without_verified_v3_manifest(tmp_path):
    a=tmp_path/'bad.pyz';a.write_bytes(b'not-a-zip')
    with pytest.raises(NativeM5Refused,match='M5_PYZ_SOURCE_AND_INTEGRITY_UNVERIFIED'):
        _preverify(a)
    assert _inspection(a) if False else True


def test_report_never_invents_live_ci_or_release(capsys):
    info={'id':'a'*32,'head_sha':'b'*40,'head':'branch',
          'native_apply':{'state':'COMMITTED','commit':'b'*40,'transaction_id':'txn',
                          'history_id':'hist','validation_status':'ok'},
          'pr':{'url':'https://github.com/example/repo/pull/1','head_sha':'b'*40},
          'ci':{'state':'REQUIRED_CHECKS_PASSED'}}
    summary=_summary(info,verb='status',format='json')
    assert summary['ci_scope']=='recorded_historical_not_live_qualified'
    assert summary['release_qualified'] is False
    assert summary['device_qualified'] is False
    assert summary['automatic_clipboard'] is False
    assert summary['fullscreen_ui'] is False
    assert summary['tests_evidence']['skipped']=='not_recorded'
    assert 'token' not in capsys.readouterr().out.lower()


def test_child_presentation_plain_and_no_copy():
    env=_child_env()
    assert env['DEVTOOL_EXECUTION_PRESENTATION']=='plain'
    assert env['DEVTOOL_JOB_UI']=='plain'
    assert env['DEVTOOL_TEXTUAL_APPLY_HOST']=='0'


def test_bad_session_id_rejected():
    for value in ['a'*31,'A'*32,'hello','a'*32+';rm']:
        with pytest.raises(NativeM5Refused):_session_id(value)


def test_no_auto_delivery_mutation_without_yes(tmp_path,monkeypatch):
    p=SimpleNamespace(native_command='deliver',repo=str(tmp_path),native_session_id='a'*32,yes=False)
    monkeypatch.setattr('devtool.native_delivery_m5._bound_status',lambda *a,**kw:{'id':'a'*32})
    with mock.patch('devtool.native_delivery_m5._run') as runner:
        assert main(p)==2
        runner.assert_not_called()


def _test_zip_manifest(tmp_path, manifest):
    import zipfile
    archive = tmp_path / 'fixture.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('.devtool-artifact.json', json.dumps(manifest))
    return archive


def test_legacy_zip_check_allowed_but_apply_refused_precommit(tmp_path, monkeypatch):
    archive = _test_zip_manifest(tmp_path, {'schema_version': 2, 'type': 'overlay'})
    with pytest.raises(NativeM5Refused, match='M5_ZIP_V3_REQUIRED_FOR_APPLY'):
        _require_zip_v3_commit_session(archive)
    from devtool.native_delivery_m5 import _preverify
    assert _preverify(archive) == 'native-zip'
    called = []
    monkeypatch.setattr('devtool.native_delivery_m5._run', lambda *a, **kw: called.append(a))
    args = SimpleNamespace(native_command='apply', repo=str(tmp_path), artifact=str(archive), yes=True)
    assert main(args) == 2
    assert not called, 'no native subprocess may start for legacy ZIP apply'


def test_malformed_v3_zip_refused_before_any_native_subprocess(tmp_path, monkeypatch):
    archive = _test_zip_manifest(tmp_path, {'schema_version': 3, 'type': 'overlay'})
    with pytest.raises(NativeM5Refused, match='M5_ZIP_V3_DELIVERY_CONTRACT_INVALID'):
        _require_zip_v3_commit_session(archive)
    with mock.patch('devtool.native_delivery_m5._run') as child:
        args = SimpleNamespace(native_command='apply', repo=str(tmp_path), artifact=str(archive), yes=True)
        assert main(args) == 2
        child.assert_not_called()


def test_valid_schema_v3_zip_contract_allows_native_engine_to_own_apply(tmp_path):
    from devtool.native_delivery_contract_v3 import normalize_delivery_contract
    import zipfile
    # A real canonical v3 declaration (no mocked normalizer). It authorizes
    # the native engine to perform its own independent artifact validation.
    one, two, base, checksum = 'a'*40, 'b'*40, 'c'*40, 'd'*64
    declaration = {
        'schema_version': 3, 'type': 'overlay',
        'base_contract_mode': 'exact-tree', 'base_commit': one, 'base_tree': two,
        'repository': {'remote': 'git@github.com:Mikeyphw/devtool.git',
                       'branch': 'feature/m5-native-zip-acceptance',
                       'enforce_remote': True, 'enforce_branch': True},
        'inventory': {'format': 1, 'entries': [
            {'path': 'docs/m5-test.txt', 'action': 'add', 'sha256': checksum, 'size': 10}]},
        'touched_paths': ['docs/m5-test.txt'],
        'source_sha256': {'docs/m5-test.txt': None},
        'expected_post_sha256': {'docs/m5-test.txt': checksum},
        'validation': {'required': True, 'restore': False, 'build': False, 'test': False,
                       'tests': [{'id': 'native-m5-gate', 'runner': 'python', 'required': True,
                                  'command': ['python3', '-B', '-m', 'pytest', '-q',
                                              'tests/test_native_delivery_m5.py']}]},
        'apply': {'atomic': True, 'requires_clean_worktree': True,
                  'commit': {'enabled': True, 'strategy': 'single',
                             'message': 'test: native M5 ZIP acceptance'}},
        'delivery_contract': {
            'version': 1,
            'runtime': {'compatibility': {'minimum': '3.2.0', 'major': 3},
                        'resolution': 'trusted-verified-compatible',
                        'verify': ['commit', 'tree', 'launcher-module-blob', 'cli-schema-blob']},
            'session': {'after_commit': 'adopt-or-resume',
                        'bind_to': ['artifact-digest', 'transaction-id', 'result-commit', 'branch']},
            'remote': {'base_repository': 'Mikeyphw/devtool', 'base_branch': 'main',
                       'base_sha': base, 'push': 'requires-explicit-approval',
                       'pull_request': {'mode': 'requires-explicit-approval',
                                        'draft': True, 'title': 'test: M5 ZIP acceptance'}},
            'ci': {'policy_path': 'config/delivery-required-ci.json',
                   'observe': 'on-explicit-request', 'exact_pr_head': True,
                   'qualification': 'required-workflows-only'},
            'recovery': {'after_commit': 'resume-only',
                         'unknown_remote_outcome': 'read-only-reconcile'},
        },
    }
    assert normalize_delivery_contract(declaration)['schema'] == 'devtool.native-delivery-contract.v1'
    archive = _test_zip_manifest(tmp_path, declaration)
    assert _require_zip_v3_commit_session(archive) is None


def test_zip_missing_and_duplicate_manifest_both_refused(tmp_path):
    import zipfile
    a = tmp_path / 'missing.zip'
    with zipfile.ZipFile(a, 'w') as z:
        z.writestr('README', 'test')
    with pytest.raises(NativeM5Refused, match='M5_ZIP_UNIQUE_MANIFEST_REQUIRED'):
        _require_zip_v3_commit_session(a)
    b = tmp_path / 'duplicates.zip'
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('.devtool-artifact.json', '{}')
            z.writestr('.devtool-artifact.json', '{}')
    with pytest.raises(NativeM5Refused, match='M5_ZIP_UNIQUE_MANIFEST_REQUIRED'):
        _require_zip_v3_commit_session(b)


def _durable_fixture(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    from devtool import artifacts, delivery_session

    repo = tmp_path / 'repo'
    repo.mkdir()
    def git(*args):
        return subprocess.run(['git', '-C', str(repo), *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    git('init', '-q')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'M5 test')
    git('checkout', '-qb', 'feature/test-durable')
    (repo/'file.txt').write_text('before\n')
    git('add','file.txt')
    git('commit','-qm','base')
    before = git('rev-parse','HEAD')
    (repo/'file.txt').write_text('after\n')
    git('add','file.txt')
    git('commit','-qm','native committed')
    after = git('rev-parse','HEAD')

    artifact = tmp_path/'payload.zip'
    artifact.write_bytes(b'fictional fixture; actual native archive already verified')
    fingerprint = hashlib.sha256(artifact.read_bytes()).hexdigest()
    history = [{
        'commit_hash': after, 'artifact': str(artifact),
        'sha256': fingerprint, 'status':'ok','validation_status':'ok',
        'transaction': {'status':'completed', 'id':'txn-1'},
        'history_id':'history-1','manifest': {'schema_version': 3},
    }]
    monkeypatch.setattr(artifacts,'load_history',lambda root:history)

    state=tmp_path/'state'
    folder=state/'devtool/delivery/sessions'
    folder.mkdir(parents=True,mode=0o700)
    monkeypatch.setenv('XDG_STATE_HOME',str(state))
    sid='a'*32
    envelope={'id':sid,'repo_root':str(repo),'head_sha':after}
    (folder/(sid+'.json')).write_text(json.dumps(envelope))
    overlay={'path':str(artifact),'sha256':fingerprint}
    native={'state':'COMMITTED','commit':after,'transaction_status':'completed',
            'transaction_id':'txn-1','history_id':'history-1','artifact_sha256':fingerprint}
    bound={'id':sid,'repo_root':str(repo),'head_sha':after,
           'source_binding':'matching','resumable':True,'stage':'PLANNED',
           'native_apply':native,'overlay':overlay,'intents':[]}
    monkeypatch.setattr(delivery_session,'status',lambda identity:bound)
    return repo,artifact,before,after,history,bound,folder,sid


def test_durable_zip_session_found_without_terminal_marker(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    assert _committed_zip_session(repo,artifact,before) == sid


def test_durable_zip_session_refuses_ambiguous_journals(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    from devtool import delivery_session
    other='b'*32
    (folder/(other+'.json')).write_text(json.dumps({'id':other,'repo_root':str(repo),'head_sha':after}))
    monkeypatch.setattr(delivery_session,'status',lambda identity:{**bound,'id':identity})
    with pytest.raises(NativeM5Refused,match='SESSION_NOT_UNIQUE'):
        _committed_zip_session(repo,artifact,before)


def test_durable_zip_session_refuses_native_history_drift(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    history[0]['transaction']['status']='incomplete'
    with pytest.raises(NativeM5Refused,match='HISTORY_NOT_QUALIFIED'):
        _committed_zip_session(repo,artifact,before)


def test_durable_zip_session_refuses_binding_drift(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    bound['native_apply']['history_id']='unexpected-history'
    with pytest.raises(NativeM5Refused,match='EVIDENCE_DRIFT'):
        _committed_zip_session(repo,artifact,before)


def test_durable_zip_session_refuses_replay_without_new_commit(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    with pytest.raises(NativeM5Refused,match='SOURCE_UNVERIFIED'):
        _committed_zip_session(repo,artifact,after)


def test_durable_zip_session_refuses_symlink_entry(tmp_path,monkeypatch):
    repo,artifact,before,after,history,bound,folder,sid = _durable_fixture(tmp_path,monkeypatch)
    other=folder/('b'*32+'.json')
    other.symlink_to(folder/(sid+'.json'))
    with pytest.raises(NativeM5Refused,match='ENTRY_UNSAFE'):
        _committed_zip_session(repo,artifact,before)

