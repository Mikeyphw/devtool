"""QW01 fail-closed source-bound wrappers; all remote and native effects mocked."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile

import pytest

from devtool import qualification_workflows as q
from devtool.github_actions import GitHubActionsError

SHA = 'a'*40
SLUG = 'Mikeyphw/devtool'


def fixture_source(tmp_path, monkeypatch):
    root = tmp_path/'repo'
    root.mkdir()
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    monkeypatch.setattr(q,'_identity',lambda r:(SHA,'feature/source'))
    return root


class FakeApi:
    def __init__(self, sha=SHA):
        self.sha=sha
        self.job_count=24
        self.attempt=1
        self.remote_status='completed'
        self.remote_conclusion='success'
        self.artifact_missing=False
    def get_run(self, slug, rid):
        return {'id':rid,'repository':{'full_name':slug},'head_sha':self.sha,'head_branch':'feature/source',
                'event':'workflow_dispatch','workflow_id':42,'run_attempt':self.attempt,
                'status':self.remote_status,'conclusion':self.remote_conclusion}
    def run_attempt_jobs(self, slug, rid, attempt):
        jobs = [dict(name=f'Python 3.13 / shard {i}/24',run_attempt=1,
                     conclusion='success',status='completed') for i in range(1,self.job_count+1)]
        jobs.append({'name':'Summarize all 24 shards (fail closed)', 'run_attempt':1,
                     'status':'completed','conclusion':'success'})
        return jobs


def start(root, monkeypatch, api=None, run_id=1234):
    api=api or FakeApi()
    dispatched=[]
    def fake_dispatch(*a,**kw):
        dispatched.append(1)
        return {'pre_dispatch_sha':SHA,'workflow_id':42,'workflow_path':'.github/workflows/'+q.WORKFLOW,
                'request_id':'c'*32,'run_id':run_id}
    monkeypatch.setattr(q,'dispatch_workflow',fake_dispatch)
    result=q.ci24_start(root,client=api,slug=SLUG)
    return api, result, dispatched


def fake_artifacts(monkeypatch, tmp_path, sha=SHA):
    names=sorted(q.EXPECTED_ARTIFACTS)
    monkeypatch.setattr(q,'artifact_list',lambda client,slug,identity:{'artifacts':[
        {'name':name,'id':i+1,'digest':'sha256:'+'b'*64} for i,name in enumerate(names)]})
    content={'schema_version':1,'source_sha':sha,'status':'PASS','expected_shards':24,
             'present_shards':24,'total_modules':800,'covered_modules':800,
             'tests':20000,'failures':0,'errors':0,'issues':[], 'failed_modules':[],
             'formal_release_qualified':False}
    archive=tmp_path/'summary.zip'
    with ZipFile(archive,'w') as z: z.writestr('summary.json',json.dumps(content))
    monkeypatch.setattr(q,'verify_download',lambda client,slug,rid,artifact,root:{
        'archive_verified':True,'sha256':'b'*64,'file':str(archive)})
    return archive


def test_config_commands_and_engine_contract():
    from devtool.config import load_project_config
    config=load_project_config(str(Path(__file__).resolve().parents[1]),None)
    for command in ('ci24-start','ci24-resume','g3r-native','g3r-verify','qualify-status','qualify-seal'):
        assert config.wrapper.commands[command].workflow in config.targets['devtool'].workflows


def test_ci24_idempotent_dispatch_and_pass(tmp_path,monkeypatch):
    root=fixture_source(tmp_path,monkeypatch)
    api,started,requests=start(root,monkeypatch)
    assert requests == [1] and not started['ci_verified']
    with pytest.raises(GitHubActionsError,match='NEVER dispatch twice'):
        q.ci24_start(root,client=api,slug=SLUG)
    assert requests==[1]
    fake_artifacts(monkeypatch,tmp_path)
    result=q.ci24_resume(root,client=api,slug=SLUG)
    assert result['ci_verified'] and result['verification']['shards_verified']==24
    assert result['run_correlated']
    assert q.qualify_status(root)['ci24']=='PASS_CI24_VERIFIED'
    assert q.qualify_status(root)['automated_milestone_verified'] is False


def test_ci24_204_requires_explicit_run_and_does_not_claim_correlation(tmp_path,monkeypatch):
    root=fixture_source(tmp_path,monkeypatch)
    api,started,_=start(root,monkeypatch,run_id=None)
    with pytest.raises(GitHubActionsError,match='uncorrelated'):
        q.ci24_resume(root,client=api,slug=SLUG)
    fake_artifacts(monkeypatch,tmp_path)
    result=q.ci24_resume(root,explicit_run_id=1234,client=api,slug=SLUG)
    assert result['ci_verified'] and not result['run_correlated']
    result=q.ci24_resume(root,client=api,slug=SLUG)
    assert result['ci_verified'] and not result['run_correlated']
    with pytest.raises(GitHubActionsError,match='contradicts'):
        q.ci24_resume(root,explicit_run_id=999,client=api,slug=SLUG)


@pytest.mark.parametrize('mode',('source','incomplete','rerun','run_failure','missing_shard','bad_summary'))
def test_ci24_fail_closed(tmp_path,monkeypatch,mode):
    root=fixture_source(tmp_path,monkeypatch)
    api,_,_=start(root,monkeypatch)
    fake_artifacts(monkeypatch,tmp_path)
    if mode=='source':api.sha='b'*40
    if mode=='incomplete':api.remote_status='in_progress';api.remote_conclusion=None
    if mode=='rerun':api.attempt=2
    if mode=='run_failure':api.remote_conclusion='failure'
    if mode=='missing_shard':api.job_count=23
    if mode=='bad_summary':
        arch=tmp_path/'summary.zip'
        with ZipFile(arch,'w') as z:z.writestr('summary.json',json.dumps({'status':'PASS','source_sha':SHA}))
    if mode=='incomplete':
        result=q.ci24_resume(root,client=api,slug=SLUG)
        assert result['state']=='WAITING_REMOTE' and not result['ci_verified']
    else:
        with pytest.raises(GitHubActionsError):q.ci24_resume(root,client=api,slug=SLUG)


def test_ci24_missing_or_duplicate_artifact_fails(tmp_path,monkeypatch):
    root=fixture_source(tmp_path,monkeypatch)
    api,_,_=start(root,monkeypatch)
    monkeypatch.setattr(q,'artifact_list',lambda *x,**kw:{'artifacts':[{'name':'devtool-full-pytest-summary','digest':'sha256:'+'a'*64,'id':12}]})
    with pytest.raises(GitHubActionsError,match='artifact set'):
        q.ci24_resume(root,client=api,slug=SLUG)


def test_ci24_interrupted_dispatch_cannot_repeat(tmp_path,monkeypatch):
    root=fixture_source(tmp_path,monkeypatch)
    def fail(*a,**k):raise OSError('read timeout after POST')
    monkeypatch.setattr(q,'dispatch_workflow',fail)
    with pytest.raises(GitHubActionsError,match='DO NOT RETRY'):
        q.ci24_start(root,client=FakeApi(),slug=SLUG)
    with pytest.raises(GitHubActionsError,match='NEVER dispatch twice'):
        q.ci24_start(root,client=FakeApi(),slug=SLUG)
    assert q.qualify_status(root)['ci24']=='DISPATCH_OUTCOME_UNKNOWN'


def test_native_verifier_rejects_stale_and_false_success(tmp_path,monkeypatch):
    root=fixture_source(tmp_path,monkeypatch)
    archive=tmp_path/'candidate.zip';archive.write_bytes(b'fixture')
    def verdict(a,b,*,require_native):
        assert require_native
        return {'source_commit':SHA,'status':'PASS_CURRENT_SOURCE_AND_NATIVE_LANES',
                'passed_lanes':13,'required_lanes':13}
    monkeypatch.setattr(q,'verify_native',verdict)
    result=q.g3r_verify(root,candidate=archive)
    assert result['passed_lanes']==13
    assert q.g3r_verify(root)['source_sha']==SHA
    monkeypatch.setattr(q,'verify_native',lambda *a,**k:{'source_commit':'0'*40,'status':'PASS_CURRENT_SOURCE_AND_NATIVE_LANES'})
    with pytest.raises(GitHubActionsError,match='failed'):q.g3r_verify(root)


def fake_fs03_report(root):
    checks = [{'id': name, 'status': 'pass'} for name in sorted(q.FS03_REQUIRED_CHECKS)]
    return {'schema_version': q.FINAL_SEAL_SCHEMA_VERSION,
            'gate_scope': 'repository_contracts_only', 'repo_root': str(root.resolve()),
            'formal_release_qualified': False, 'status': 'pass',
            'summary': {'checks': len(checks), 'passed': len(checks),
                        'failed': 0, 'compatibility_entries': 24},
            'checks': checks}


def test_qualification_seal_demands_independent_checks_and_non_release(tmp_path, monkeypatch):
    root = fixture_source(tmp_path, monkeypatch)
    monkeypatch.setattr(q, 'ci24_resume', lambda *a, **k: {'ci_verified': True, 'run_id': 42})
    monkeypatch.setattr(q, 'g3r_verify', lambda *a, **k: {'status': 'PASS_G3R_VERIFIED'})
    calls = []
    def audit(path):
        calls.append(path)
        return fake_fs03_report(path)
    monkeypatch.setattr(q, 'final_seal_report', audit)
    proof = q.qualify_seal(root)
    assert calls == [root]
    assert proof['fs03_evidence'] == {'status': 'PASS', 'checks': 16,
                                     'passed': 16, 'failed': 0, 'schema_version': 1}
    assert proof['automated_milestone_verified'] and proof['formal_release_qualified'] is False
    assert set(proof['open_real_substrate_obligations']) == set(q.G3R_REAL_SUBSTRATE_CARRY)
    monkeypatch.setattr(q, 'final_seal_report', lambda path: {**fake_fs03_report(path), 'status': 'fail'})
    with pytest.raises(GitHubActionsError, match='FS03'): q.qualify_seal(root)


@pytest.mark.parametrize('mode', ('missing', 'duplicate', 'failed', 'false_summary',
                                  'wrong_root', 'wrong_scope', 'release_flag',
                                  'wrong_schema', 'not_dict', 'exception'))
def test_fs03_wrapper_fails_closed_on_bad_audit(tmp_path, monkeypatch, mode):
    root = fixture_source(tmp_path, monkeypatch)
    report = fake_fs03_report(root)
    if mode == 'missing':
        report['checks'].pop()
        report['summary']['checks'] -= 1
        report['summary']['passed'] -= 1
    elif mode == 'duplicate':
        report['checks'][0]['id'] = report['checks'][1]['id']
    elif mode == 'failed':
        report['checks'][0]['status'] = 'fail'
    elif mode == 'false_summary':
        report['summary']['failed'] = 1
    elif mode == 'wrong_root':
        report['repo_root'] = '/some/other/repository'
    elif mode == 'wrong_scope':
        report['gate_scope'] = 'unverified_release'
    elif mode == 'release_flag':
        report['formal_release_qualified'] = True
    elif mode == 'wrong_schema':
        report['schema_version'] += 1
    elif mode == 'not_dict':
        report = None
    if mode == 'exception':
        def boom(path): raise OSError('audit failure')
        monkeypatch.setattr(q, 'final_seal_report', boom)
    else:
        monkeypatch.setattr(q, 'final_seal_report', lambda path: report)
    with pytest.raises(GitHubActionsError, match='FS03'):
        q._fs03_strict_in_process(root)


def test_fs03_wrapper_real_auditor_and_no_nested_cli(monkeypatch):
    # This tests actual FS03 behavior, not a mocked status flag; the nested CLI
    # never runs, so Termux frontend reentrancy cannot stall the wrapper.
    root = Path(__file__).resolve().parents[1]
    result = q._fs03_strict_in_process(root)
    assert result['status'] == 'PASS' and result['checks'] >= 16
    assert result['failed'] == 0
