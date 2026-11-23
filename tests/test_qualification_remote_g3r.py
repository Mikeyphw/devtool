"""G3R hosted-G1 and CI24 archive provenance regressions. All cloud I/O mocked."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from devtool import qualification_workflows as q
from devtool import g3r_evidence_generate as producer
from devtool import g3r_hosted_android as hosted
from devtool.github_actions import GitHubActionsError

HEAD = 'a'*40
TREE = 'b'*40
SLUG = 'Mikeyphw/devtool'
RID = 8219


def fixture(tmp_path, monkeypatch):
    root = tmp_path/'repo'
    root.mkdir()
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
    monkeypatch.setattr(q, '_identity', lambda repo: (HEAD, 'feature/source'))
    monkeypatch.setattr(q, '_git', lambda repo, *a: TREE if a == ('rev-parse','HEAD^{tree}') else '')
    return root


def hosted_zip(tmp_path, source=HEAD, tree=TREE, run_id=RID, exit_code=0):
    log = (json.dumps({'gate':'ANDROID-G1','status':'passed',
                       'host':{'termux':False},'standalone_runtime_sealed':True,
                       'gradle_receipt_id':'gradle-proof',
                       'artifact_sha256':'c'*64})+'\nSTDERR:\n').encode()
    receipt = {'schema': 'devtool.g3r.hosted-g1.v1', 'source_commit':source,
               'source_tree':tree, 'run_id':run_id, 'run_attempt':1,
               'job':'android-g1-hosted', 'runner_os':'Linux',
               'status':'PASS', 'exit_code':exit_code,
               'formal_release_qualified':False,
               'output_sha256':hashlib.sha256(log).hexdigest(),
               'gradle_receipt_id':'gradle-proof', 'artifact_sha256':'c'*64}
    path = tmp_path/'hosted.zip'
    with ZipFile(path, 'w') as z:
        z.writestr('android_g1_receipt.json', json.dumps(receipt))
        z.writestr('android_g1.log', log)
    return path


class Api:
    def __init__(self):
        self.attempt=1
        self.head=HEAD
        self.status='completed'
        self.conclusion='success'
        self.job_status='success'
    def get_run(self, slug, rid):
        return {'id':rid,'repository':{'full_name':slug},'head_sha':self.head,
                'head_branch':'feature/source','event':'workflow_dispatch',
                'workflow_id':91,'run_attempt':self.attempt,
                'status':self.status,'conclusion':self.conclusion}
    def run_attempt_jobs(self, slug, rid, attempt):
        return [{'name':'android-g1-hosted','status':'completed',
                 'conclusion':self.job_status,'run_attempt':attempt}]


def dispatched(root, monkeypatch, api=None):
    api=api or Api()
    calls=[]
    def dispatch(*a,**kw):
        calls.append(kw.get('execute'))
        return {'pre_dispatch_sha':HEAD,'workflow_id':91,
                'workflow_path':'.github/workflows/'+q.G3R_REMOTE_WORKFLOW,
                'run_id':RID,'request_id':'f'*32}
    monkeypatch.setattr(q,'dispatch_workflow',dispatch)
    result=q.g3r_remote_start(root,client=api,slug=SLUG)
    assert result['run_id'] == RID and calls == [False,True]
    with pytest.raises(GitHubActionsError, match='NEVER dispatch twice'):
        q.g3r_remote_start(root,client=api,slug=SLUG)
    return api


def mocked_download(monkeypatch, artifact):
    monkeypatch.setattr(q,'artifact_list',lambda *a,**k:{'artifacts':[
        {'id':88,'name':q.G3R_REMOTE_ARTIFACT,
         'digest':'sha256:'+hashlib.sha256(artifact.read_bytes()).hexdigest()}]})
    monkeypatch.setattr(q,'verify_download',lambda *a,**k:{
        'archive_verified':True,'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest(),
        'file':str(artifact)})


def test_hosted_g1_produces_full_live_bound_receipt(tmp_path, monkeypatch):
    repo=fixture(tmp_path,monkeypatch)
    api=dispatched(repo,monkeypatch)
    path=hosted_zip(tmp_path)
    mocked_download(monkeypatch,path)
    result=q.g3r_remote_resume(repo,client=api,slug=SLUG)
    assert result['remote_verified'] and result['run_id']==RID
    assert q.qualify_status(repo)['g3r_remote']=='PASS_HOSTED_G1_VERIFIED'
    observed=q._read_hosted_g1_archive(path,source=HEAD,tree=TREE,run_id=RID)
    assert observed['run_attempt']==1 and observed['status']=='PASS'
    assert observed['receipt']['gradle_receipt_id']=='gradle-proof'


@pytest.mark.parametrize('mode',['wrong_source','wrong_tree','wrong_id','wrong_attempt','wrong_job',
                                  'run_failure','incomplete','bad_receipt','duplicate_artifact'])
def test_hosted_g1_fails_closed(tmp_path, monkeypatch, mode):
    root=fixture(tmp_path,monkeypatch)
    api=dispatched(root,monkeypatch)
    artifact=hosted_zip(tmp_path, source='0'*40 if mode=='wrong_source' else HEAD,
                        tree='0'*40 if mode=='wrong_tree' else TREE,
                        run_id=999 if mode=='wrong_id' else RID,
                        exit_code=1 if mode=='bad_receipt' else 0)
    mocked_download(monkeypatch,artifact)
    if mode=='wrong_attempt': api.attempt=2
    if mode=='wrong_job':api.job_status='failure'
    if mode=='run_failure':api.conclusion='failure'
    if mode=='duplicate_artifact':
        digest=hashlib.sha256(artifact.read_bytes()).hexdigest()
        monkeypatch.setattr(q,'artifact_list',lambda *a,**k:{'artifacts':[
            {'id':88,'name':q.G3R_REMOTE_ARTIFACT,'digest':'sha256:'+digest},
            {'id':99,'name':q.G3R_REMOTE_ARTIFACT,'digest':'sha256:'+digest}]})
    if mode=='incomplete':
        api.status='in_progress';api.conclusion=None
        result=q.g3r_remote_resume(root,client=api,slug=SLUG)
        assert result['state']=='WAITING_REMOTE' and not result['remote_verified']
    else:
        with pytest.raises(GitHubActionsError):
            q.g3r_remote_resume(root,client=api,slug=SLUG)


def test_local_gradle_never_invoked_without_hosted_evidence(tmp_path,monkeypatch):
    root=tmp_path/'repo'
    root.mkdir()
    monkeypatch.setattr(producer,'git',lambda *a: b'' if 'status' in a else b'a'*40)
    with pytest.raises((ValueError, OSError), match='HOSTED_EVIDENCE_REQUIRED|SOURCE'):
        producer.generate(root,tmp_path/'evidence',run_tests=False,native_gates=True)


def test_hosted_producer_refuses_non_github_context(tmp_path, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    with pytest.raises(RuntimeError,match='no local Gradle'):
        hosted.produce(tmp_path/'evidence')


def test_ci24_verifies_all_25_artifact_bytes(tmp_path,monkeypatch):
    root=fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(q,'_identity',lambda repo:(HEAD,'feature/source'))
    api=Api()
    api.get_run=lambda slug,rid:{'id':rid,'repository':{'full_name':slug},
        'head_sha':HEAD,'head_branch':'feature/source','event':'workflow_dispatch',
        'workflow_id':41,'run_attempt':1,'status':'completed','conclusion':'success'}
    api.run_attempt_jobs=lambda slug,rid,attempt:[
        {'name':f'Python 3.13 / shard {i}/24','status':'completed','conclusion':'success','run_attempt':1}
        for i in range(1,25)]+[{'name':'Summarize all 24 shards (fail closed)',
                                   'status':'completed','conclusion':'success','run_attempt':1}]
    names=sorted(q.EXPECTED_ARTIFACTS)
    monkeypatch.setattr(q,'artifact_list',lambda *a,**k:{'artifacts':[
        {'id':i+1,'name':name,'digest':'sha256:'+'b'*64} for i,name in enumerate(names)]})
    summary={'schema_version':1,'source_sha':HEAD,'status':'PASS',
             'expected_shards':24,'present_shards':24,'total_modules':24,
             'covered_modules':24,'tests':24,'failures':0,'errors':0,'issues':[],
             'failed_modules':[],'formal_release_qualified':False}
    zipfile=tmp_path/'summary.zip'
    with ZipFile(zipfile,'w') as z: z.writestr('summary.json',json.dumps(summary))
    visited=[]
    def verify(client,slug,rid,aid,repo):
        visited.append(aid)
        return {'archive_verified':True,'sha256':'b'*64,'file':str(zipfile)}
    monkeypatch.setattr(q,'verify_download',verify)
    monkeypatch.setattr(q,'_ci24_verified_shard_receipt',lambda path,shard,source,slug: {
        'shard':shard,'modules':[f'tests/test_{shard:02d}.py'], 'tests':1,
        'inventory_count':24,'inventory_sha256':'c'*64})
    record={'source_sha':HEAD,'branch':'feature/source','workflow_id':41}
    passed=q._ci24_evaluate(api,SLUG,root,record,123)
    assert passed['artifact_archives_verified']==25
    assert sorted(visited)==list(range(1,26))
    visited.clear()
    def corrupt(client,slug,rid,aid,repo):
        if aid==7:return {'archive_verified':False,'sha256':'b'*64,'file':str(zipfile)}
        return verify(client,slug,rid,aid,repo)
    monkeypatch.setattr(q,'verify_download',corrupt)
    with pytest.raises(GitHubActionsError,match='byte/digest'):
        q._ci24_evaluate(api,SLUG,root,record,123)


def test_real_ci24_shard_archive_and_log_junit_hashes(tmp_path):
    log=b'pytest 3 passed\n'
    xml=b'<testsuite><testcase name="a"/></testsuite>'
    module={'module':'tests/test_a.py','status':'PASS','exit_code':0,
            'tests':3,'failures':0,'errors':0,
            'log':'logs/module-001.log','log_sha256':hashlib.sha256(log).hexdigest(),
            'junit':'junit/module-001.xml','junit_sha256':hashlib.sha256(xml).hexdigest()}
    receipt={'schema_version':1,'source_sha':HEAD,'repository':SLUG,
             'shard':1,'shards':24,'success':True,'complete':True,
             'formal_release_qualified':False,'inventory_count':1,
             'inventory_sha256':'a'*64,'modules_expected':['tests/test_a.py'],
             'modules':[module]}
    archive=tmp_path/'shard.zip'
    def write():
        with ZipFile(archive,'w') as z:
            z.writestr('receipt.json',json.dumps(receipt))
            z.writestr('logs/module-001.log',log)
            z.writestr('junit/module-001.xml',xml)
    write()
    proof=q._ci24_verified_shard_receipt(archive,1,HEAD,SLUG)
    assert proof['tests']==3 and proof['modules']==['tests/test_a.py']
    receipt['modules'][0]['log_sha256']='0'*64
    write()
    with pytest.raises(GitHubActionsError, match='SHA-256 mismatch'):
        q._ci24_verified_shard_receipt(archive,1,HEAD,SLUG)
    receipt['modules'][0]['log_sha256']=hashlib.sha256(log).hexdigest()
    receipt['source_sha']='0'*40
    write()
    with pytest.raises(GitHubActionsError, match='source/inventory'):
        q._ci24_verified_shard_receipt(archive,1,HEAD,SLUG)
