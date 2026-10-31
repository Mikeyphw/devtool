"""R1R28 comprehensive offline contracts. Never dispatch to GitHub in pytest."""
from __future__ import annotations
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError, HTTPError
from zipfile import ZipFile
import pytest
import yaml

from devtool.actions_dispatch import dispatch_workflow, review_uncertain_dispatch
from devtool.actions_runs import _read_dispatch_receipt
from devtool.github_actions import GitHubActionsError, GitHubDispatchOutcomeUnknown
from devtool.artifact_cleanup_worker import (cleanup_source_failure_diagnostic,cleanup_source_snapshot,_unlink_owned_source)

ROOT=Path(__file__).resolve().parents[1]
SHA='a'*40

def load_tool(name):
    source=ROOT/'tools'/name
    spec=importlib.util.spec_from_file_location('r1r28_'+name.replace('.','_'),source)
    assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert Path(mod.__file__).resolve()==source.resolve()
    return mod

matrix=load_tool('fc08_r1r28_matrix.py')
evidence=load_tool('fc08_r1r28_evidence.py')
native=load_tool('fc08_r1r28_termux.py')

class FakeClient:
    _token='fake-testing-token'
    posts=0
    def __init__(self,state='accepted-correlated'):
        self.state=state
        self.posts=0
    def repository(self,slug):return {'full_name':slug,'id':42}
    def workflow(self,slug,workflow):return {'id':91,'path':'.github/workflows/trigger.yml','state':'active'}
    def _get(self,path):
        if '/commits/' in path:return {'sha':SHA}
        doc=b'on:\n  workflow_dispatch:\n    inputs:\n      scenario:\n        type: string\n'
        return {'type':'file','path':'.github/workflows/trigger.yml',
                'encoding':'base64','content':base64.b64encode(doc).decode()}
    def dispatch(self,slug,workflow,ref,inputs):
        self.posts+=1
        if self.state=='unknown':raise GitHubDispatchOutcomeUnknown('fake timeout')
        if self.state=='rejected':raise GitHubActionsError('fake HTTP 422')
        return {'state':'accepted-correlated','run_id':101,'run_url':'https://github.com/Mikeyphw/devtool/actions/runs/101'}
    def get_run(self,slug,identity):
        return {'id':identity,'repository':{'full_name':slug},'workflow_id':91,
                'event':'workflow_dispatch','head_sha':SHA,'head_branch':'main',
                'run_attempt':1,'status':'completed','conclusion':'success'}

def dispatch(client,dir,*,resume=True):
    return dispatch_workflow(client,'Mikeyphw/devtool','trigger.yml','main',
                             {'scenario':'one'},execute=True,state_root=dir,
                             expected_source_sha=SHA,resume_if_existing=resume)

@pytest.mark.parametrize('state',['accepted-correlated','unknown'])
def test_all_dispatch_paths_lock_and_never_repeat_existing(tmp_path,state):
    c=FakeClient(state);root=tmp_path/'journal'
    if state=='unknown':
        with pytest.raises(GitHubActionsError,match='outcome uncertain'):dispatch(c,root,resume=False)
    else:dispatch(c,root,resume=False)
    assert c.posts==1
    with pytest.raises(GitHubActionsError,match='Matching dispatch receipt'):dispatch(c,root,resume=False)
    assert c.posts==1
    r=dispatch(c,root,resume=True)
    assert r['dispatched'] is False and r['safe_to_redispatch'] is False
    assert c.posts==1

@pytest.mark.parametrize('state',['rejected'])
def test_definitely_rejected_journal_allows_explicit_new_attempt(tmp_path,state):
    c=FakeClient(state);root=tmp_path/'journal'
    with pytest.raises(GitHubActionsError):dispatch(c,root)
    assert c.posts==1
    c.state='accepted-correlated'
    r=dispatch(c,root)
    assert c.posts==2 and r['dispatched'] is True
    assert len(list(root.glob('*.json')))==2

@pytest.mark.parametrize('alter',[
    {'workflow_id':92},{'head_branch':'other'},{'head_sha':'b'*40},
    {'event':'push'},{'run_attempt':None},{'repository':{'full_name':'foreign/repo'}}])
def test_manual_review_rejects_wrong_remote_identity_and_preserves_unknown(tmp_path,alter):
    c=FakeClient('unknown');root=tmp_path/'journal'
    with pytest.raises(GitHubActionsError):dispatch(c,root)
    file=next(root.glob('*.json'));orig=file.read_bytes()
    prev=c.get_run
    def different(slug,i):
        data=prev(slug,i);data.update(alter);return data
    c.get_run=different
    with pytest.raises(GitHubActionsError):review_uncertain_dispatch(c,file.stem,101,state_root=root)
    assert file.read_bytes()==orig and c.posts==1

def test_manual_review_is_readonly_and_refuses_attribution(tmp_path):
    c=FakeClient('unknown');root=tmp_path/'journal'
    with pytest.raises(GitHubActionsError):dispatch(c,root)
    file=next(root.glob('*.json'));orig=file.read_bytes()
    r=review_uncertain_dispatch(c,file.stem,101,state_root=root)
    assert r['source_compatible'] and r['journal_unchanged']
    assert r['explicit_observed_run_id']==101
    assert r['request_attribution_proven'] is False
    assert r['automatically_reconciled'] is False
    assert r['safe_to_redispatch'] is False
    assert r['release_qualified'] is False
    assert file.read_bytes()==orig

@pytest.mark.parametrize('state',['accepted-correlated','rejected'])
def test_manual_review_refuses_non_unknown_journal(tmp_path,state):
    c=FakeClient(state);root=tmp_path/'journal'
    if state=='rejected':
        with pytest.raises(GitHubActionsError):dispatch(c,root)
    else:dispatch(c,root)
    file=next(root.glob('*.json'))
    with pytest.raises(GitHubActionsError,match='not an unresolved'):
        review_uncertain_dispatch(c,file.stem,101,state_root=root)

def test_manual_review_detects_mid_get_journal_modification(tmp_path):
    c=FakeClient('unknown');root=tmp_path/'journal'
    with pytest.raises(GitHubActionsError):dispatch(c,root)
    file=next(root.glob('*.json'));old=c.get_run
    def mutate(slug,i):
        payload=json.loads(file.read_text());payload['run_id']=i;file.write_text(json.dumps(payload))
        return old(slug,i)
    c.get_run=mutate
    with pytest.raises(GitHubActionsError,match='journal changed'):review_uncertain_dispatch(c,file.stem,101,state_root=root)

@pytest.mark.parametrize('name',['cycle','rejection-retry','lost-response','all'])
def test_matrix_optin_and_scenario_bounding(name):
    env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'Mikeyphw/devtool',
         'GITHUB_REF':'refs/heads/main','GITHUB_SHA':SHA,'FC08_DISPATCH_AUTHORIZED':'true'}
    selection=matrix.plan(name,authorized=True,env=env)
    assert selection==list(matrix.SCRIPTS) if name=='all' else selection==[name]
    with pytest.raises(RuntimeError,match='OPT_IN'):matrix.plan(name,authorized=False,env=env)
    with pytest.raises(RuntimeError,match='OPT_IN'):matrix.plan(name,authorized=True,env={**env,'FC08_DISPATCH_AUTHORIZED':'false'})

def test_matrix_rejects_unexpected_host_before_subprocess(tmp_path):
    env={'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'foreign/repo','GITHUB_REF':'refs/heads/main',
         'GITHUB_SHA':SHA,'FC08_DISPATCH_AUTHORIZED':'true'}
    with pytest.raises(RuntimeError,match='HOSTED_SOURCE'):matrix.execute('all',authorized=True,env=env,out=tmp_path/'x')
    assert not (tmp_path/'x').exists()

def test_matrix_source_workflow_manual_only():
    text=(ROOT/'.github/workflows/devtool-fc08-resilience-matrix.yml').read_text()
    doc=yaml.safe_load(text)
    assert set(doc[True])=={'workflow_dispatch'}
    assert doc[True]['workflow_dispatch']['inputs']['authorize_real_dispatch']['default'] is False
    assert 'if: inputs.authorize_real_dispatch == true' in text
    assert text.count('actions: write')==1
    assert 'persist-credentials: false' in text
    assert 'cancel-in-progress: false' in text
    old=(ROOT/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    assert 'permissions:\n  contents: read\n  actions: none' in old

def test_cleanup_diagnostic_reports_drift_without_exposing_path_or_unlink(tmp_path):
    victim=tmp_path/'token=SECRET_OVERLAY.zip';victim.write_bytes(b'bytes')
    before=cleanup_source_snapshot(victim)
    victim.write_bytes(b'changed content')
    with pytest.raises(ValueError,match='identity changed'):
        _unlink_owned_source(victim,before)
    assert victim.read_bytes()==b'changed content'
    d=cleanup_source_failure_diagnostic(victim,before,ValueError('SECRET'))
    assert d['category']=='identity-drift'
    assert 'size' in d['mismatched_identity_fields']
    assert d['safe_to_retry_unlink'] is False
    assert d['policy_relaxed'] is False
    assert 'SECRET' not in json.dumps(d)

def test_cleanup_diagnostic_refuses_symlink_without_following(tmp_path):
    target=tmp_path/'victim';target.write_bytes(b'keep')
    link=tmp_path/'link';link.symlink_to(target)
    d=cleanup_source_failure_diagnostic(link,None,ValueError('symlink'))
    assert d['observed']=='source-snapshot-refused'
    assert target.read_bytes()==b'keep' and link.is_symlink()
    assert not d['safe_to_retry_unlink']

def test_termux_native_environment_refuses_ubuntu(monkeypatch):
    monkeypatch.setattr(native.platform,'machine',lambda:'x86_64')
    with pytest.raises(RuntimeError,match='NATIVE_TERMUX_ARM64_REQUIRED'):
        native.environment()

def test_termux_workflow_has_real_build_ctest_meson_and_identity_requirements():
    text=(ROOT/'tools/fc08_r1r28_termux.py').read_text()
    assert 'cmake' in text and 'ctest' in text and 'meson' in text
    assert "int.from_bytes(data[18:20], 'little') != 183" in text
    assert 'sys.prefix' in text and 'cleanup_source_snapshot' in text
    assert 'transaction_rollback_integration_qualified' in text

def archive_bytes(files):
    buf=io.BytesIO()
    with ZipFile(buf,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    return buf.getvalue()

def test_archive_sha256_and_member_boundaries():
    b=archive_bytes({'host-summary.json':b'{"release_qualified": false}'})
    info={'digest':'sha256:'+hashlib.sha256(b).hexdigest()}
    result=evidence.verify_archive(b,info)
    assert result['json_entries']==['host-summary.json']
    with pytest.raises(ValueError,match='SHA256_MISMATCH'):evidence.verify_archive(b,{**info,'digest':'sha256:'+'0'*64})
    z=archive_bytes({'../escape.txt':b'secret'})
    with pytest.raises(ValueError,match='UNSAFE_ARCHIVE'):evidence.verify_archive(z,{'digest':'sha256:'+hashlib.sha256(z).hexdigest()})

def test_evidence_never_qualifies_release_by_source_contract():
    text=(ROOT/'tools/fc08_r1r28_evidence.py').read_text()
    assert "'g1_junit_independently_recounted':False" in text
    assert "'release_qualified':False" in text
    assert "'raw_windows_trx_coverage_and_signed_binary_verified':False" in text
    assert 'StripCrossHostAuthorization' in text

def test_two_concurrent_clients_share_atomic_dispatch_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    import time
    class SharedClient(FakeClient):
        def __init__(self):
            super().__init__();self.guard=Lock()
        def dispatch(self,*args):
            with self.guard:self.posts+=1
            time.sleep(0.02)
            return {'state':'accepted-correlated','run_id':101}
    c=SharedClient();folder=tmp_path/'journal'
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda _:dispatch(c,folder,resume=True),range(2)))
    assert c.posts==1
    assert sorted(x['dispatched'] for x in result)==[False,True]
    assert result[0]['request_id']==result[1]['request_id']


def test_matrix_artifact_retains_each_individual_scenario_receipt():
    text=(ROOT/'tools/fc08_r1r28_matrix.py').read_text()
    workflow=(ROOT/'.github/workflows/devtool-fc08-resilience-matrix.yml').read_text()
    assert "_stage_bytes(out.parent/('r1r28-matrix-'+name+'.json'),content)" in text
    assert "staged.append((name,raw))" in text
    assert 'receipt_sha256' in text and 'receipt_name' in text
    assert 'path: out/fc08/r1r28-matrix*.json' in workflow


def test_provider_and_all_thirty_source_bound_zip_integrity_review(tmp_path):
    source={'sha':SHA,'tree':'b'*40}
    host=['fc08-real-'+x+'-attempt1' for x in ('github-actions-dag','linux-polyglot','windows-dotnet-msvc','macos-native','host-summary')]
    g1=[f'devtool-full-shard-{i}' for i in range(1,25)]+['devtool-full-pytest-summary']
    def artifact(name,owner):
        if owner=='host':
            member='host-summary.json' if name.startswith('fc08-real-host-summary') else name.removeprefix('fc08-real-').removesuffix('-attempt1')+'.json'
            contents=json.dumps({'release_qualified':False,'identity':{'sha':SHA,'attempt':1,'run_id':123}}).encode()
        else:
            member='receipt.json';contents=b'{}'
        raw=archive_bytes({member:contents})
        (tmp_path/(name+'.zip')).write_bytes(raw)
        return {'id':len(name),'name':name,'digest':'sha256:'+hashlib.sha256(raw).hexdigest(),
                'expired':False,'workflow_run':{'head_sha':SHA}}
    host_art=[artifact(n,'host') for n in host]
    g1_art=[artifact(n,'g1') for n in g1]
    class Provider:
        def json(self,url):
            if '/commits/' in url:return {'commit':{'tree':{'sha':source['tree']}}}
            if '/actions/runs/123/jobs' in url:return {'total_count':5,'jobs':[{'conclusion':'success','run_attempt':1} for _ in range(5)]}
            if '/actions/runs/456/jobs' in url:return {'total_count':25,'jobs':[{'conclusion':'success','run_attempt':1} for _ in range(25)]}
            if '/actions/runs/123/artifacts' in url:return {'total_count':5,'artifacts':host_art}
            if '/actions/runs/456/artifacts' in url:return {'total_count':25,'artifacts':g1_art}
            return {'head_sha':SHA,'run_attempt':1,'status':'completed','conclusion':'success',
                    'repository':{'full_name':evidence.REPO}}
    result=evidence.review(Provider(),123,456,source,tmp_path,fetch_archives=False)
    assert result['archive_count']==30
    assert result['all_provider_zip_digests_match'] is True
    assert result['g1_junit_independently_recounted'] is False
    assert result['raw_windows_trx_coverage_and_signed_binary_verified'] is False
    assert result['release_qualified'] is False
    altered=g1_art[0]['digest'];g1_art[0]['digest']='sha256:'+'0'*64
    with pytest.raises(ValueError,match='SHA256_MISMATCH'):
        evidence.review(Provider(),123,456,source,tmp_path,fetch_archives=False)
    g1_art[0]['digest']=altered


# R1R28-H1: integration contracts for the matrix's clean-source and failure
# evidence lifecycle. No GitHub API is contacted by these tests.
def _matrix_host_env():
    return {'GITHUB_ACTIONS':'true','GITHUB_REPOSITORY':'Mikeyphw/devtool',
            'GITHUB_REF':'refs/heads/main','GITHUB_SHA':SHA,
            'FC08_DISPATCH_AUTHORIZED':'true'}


def test_h1_matrix_keeps_checkout_clean_until_last_source_preflight(tmp_path,monkeypatch):
    """The second and third child must see no untracked output from child one."""
    out=tmp_path/'out'/'fc08'/'r1r28-matrix.json'
    calls=[]
    def child(argv,**kwargs):
        scenario=Path(argv[1]).name
        # Every child performs source_identity(), which rejects out/ files.
        assert not out.parent.exists(), 'intermediate matrix receipts dirtied source'
        calls.append(scenario)
        path=Path(argv[argv.index('--out')+1])
        path.write_text(json.dumps({'release_qualified':False,
                                    'fc08_overall_qualified':False,'scenario':scenario}))
        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
    monkeypatch.setattr(matrix.subprocess,'run',child)
    result=matrix.execute('all',authorized=True,env=_matrix_host_env(),out=out)
    assert calls==list(matrix.SCRIPTS.values())
    assert result['status']=='PASS'
    assert [x['status'] for x in result['scenarios']]==['PASS']*3
    assert len(list(out.parent.glob('r1r28-matrix*.json')))==4
    for scenario in matrix.SCRIPTS:
        data=(out.parent/f'r1r28-matrix-{scenario}.json').read_bytes()
        assert hashlib.sha256(data).hexdigest()==next(
            e['receipt_sha256'] for e in result['scenarios'] if e['scenario']==scenario)


def test_h1_matrix_preserves_only_allowlisted_failure_diagnostics(tmp_path,monkeypatch):
    out=tmp_path/'out'/'r1r28-matrix.json'
    calls=[]
    def child(argv,**kwargs):
        scenario=Path(argv[1]).name
        assert not out.parent.exists()
        calls.append(scenario)
        path=Path(argv[argv.index('--out')+1])
        if 'rejection_retry' in scenario:
            path.write_text(json.dumps({'release_qualified':False,
                'fc08_overall_qualified':False,'status':'BLOCKED_UNQUALIFIED',
                'error_category':'FC08_DISPATCH_DIRTY_REVIEWED_SOURCE',
                'api_body':'bearer SUPER_SECRET_TOKEN',
                'credential':'SUPER_SECRET_TOKEN'}))
            return SimpleNamespace(returncode=2,stdout=b'SUPER_SECRET_TOKEN',
                                   stderr=b'local private path SUPER_SECRET_TOKEN')
        path.write_text(json.dumps({'release_qualified':False,'fc08_overall_qualified':False}))
        return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
    monkeypatch.setattr(matrix.subprocess,'run',child)
    with pytest.raises(RuntimeError,match='SCENARIO_FAILED_REJECTION_RETRY'):
        matrix.execute('all',authorized=True,env=_matrix_host_env(),out=out)
    assert len(calls)==2
    report=json.loads(out.read_text())
    assert report['status']=='BLOCKED_UNQUALIFIED'
    assert report['failure']=={'scenario':'rejection-retry','reason':'CHILD_EXIT_NONZERO',
                                'error_category':'FC08_DISPATCH_DIRTY_REVIEWED_SOURCE'}
    assert [r['status'] for r in report['scenarios']]==['PASS','BLOCKED_UNQUALIFIED']
    failure=out.parent/'r1r28-matrix-rejection-retry.json'
    details=json.loads(failure.read_text())
    assert details['error_category']=='FC08_DISPATCH_DIRTY_REVIEWED_SOURCE'
    assert details['release_qualified'] is False
    assert 'SUPER_SECRET_TOKEN' not in ''.join(p.read_text() for p in out.parent.glob('*.json'))
    assert not (out.parent/'r1r28-matrix-lost-response.json').exists()


def test_h1_matrix_redacts_forged_failure_category(tmp_path,monkeypatch):
    out=tmp_path/'out'/'r1r28-matrix.json'
    def child(argv,**kwargs):
        Path(argv[-1]).write_text(json.dumps({'status':'BLOCKED_UNQUALIFIED',
                                              'error_category':'FC08_SECRET_CREDENTIAL_ABC123',
                                              'exception':'secret'}))
        return SimpleNamespace(returncode=2,stdout=b'',stderr=b'')
    monkeypatch.setattr(matrix.subprocess,'run',child)
    with pytest.raises(RuntimeError):matrix.execute('cycle',authorized=True,env=_matrix_host_env(),out=out)
    text=''.join(p.read_text() for p in out.parent.glob('*.json'))
    assert 'SECRET' not in text and 'secret' not in text
    assert 'EXTERNAL_DISPATCH_UNVERIFIED' in text


def test_h1_matrix_missing_child_receipt_is_unqualified(tmp_path,monkeypatch):
    out=tmp_path/'out'/'r1r28-matrix.json'
    monkeypatch.setattr(matrix.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError):matrix.execute('cycle',authorized=True,env=_matrix_host_env(),out=out)
    result=json.loads(out.read_text())
    assert result['failure']['reason']=='MISSING_CHILD_RECEIPT'
    assert result['release_qualified'] is False


def test_h1_matrix_child_timeout_has_bounded_failure_receipt(tmp_path,monkeypatch):
    out=tmp_path/'out'/'r1r28-matrix.json'
    def timeout(*args,**kwargs):raise matrix.subprocess.TimeoutExpired('offline',1)
    monkeypatch.setattr(matrix.subprocess,'run',timeout)
    with pytest.raises(RuntimeError):matrix.execute('cycle',authorized=True,env=_matrix_host_env(),out=out)
    r=json.loads(out.read_text())
    assert r['failure']['reason']=='CHILD_TIMEOUT'
    assert r['status']=='BLOCKED_UNQUALIFIED'
