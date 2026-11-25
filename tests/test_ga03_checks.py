"""Native GA03 production publisher with real Git/source/session and simulated API.
No live GitHub writes or credentials. Tests verify durable on-disk intent ordering.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import hashlib
import os
import subprocess
import sys
import time
import pytest
from devtool import ga03_checks as c, delivery_session as ds
from devtool.github_app import _askpass_source, _PERMISSIONS, GitHubAppError
from devtool.ga01_scoped_auth import capability_for_operation
from devtool.delivery_github import _git_error_code


def git(root,*argv):
    return subprocess.check_output(['git','-C',str(root),*argv],text=True).strip()


class FakeChecks:
    def __init__(self,sha,state,sid):
        self.sha=sha; self.state=state; self.sid=sid; self.rows=[]; self.writes=[]; self.lost=''
        self.access=SimpleNamespace(app_id=71,installation_id=81,capability='checks_read')
        self.client=SimpleNamespace(repository=lambda slug:{'id':91})
    def runs(self,sha):
        assert sha==self.sha
        return deepcopy(self.rows)
    def get(self,i):return deepcopy(next(x for x in self.rows if x['id']==i))
    def annotations(self,i):return self.get(i).get('annotations',[])
    def create(self,payload):
        assert ds._read(self.state/(self.sid+'.json'))['check_publications'][-1]['phase']=='create_intent'
        self.writes.append('POST'); item=deepcopy(payload); item['output']['annotations_count']=0
        item.update(id=len(self.rows)+1,app={'id':71},conclusion=None);self.rows.append(item)
        if self.lost=='create':raise c.CheckRefused('GA03_RESPONSE_LOST')
    def finish(self,i,payload):
        assert ds._read(self.state/(self.sid+'.json'))['check_publications'][-1]['phase']=='complete_intent'
        self.writes.append('PATCH');item=next(x for x in self.rows if x['id']==i)
        item.update(deepcopy(payload));item['annotations']=item['output'].pop('annotations',[])
        item['output']['annotations_count']=len(item['annotations'])
        if self.lost=='complete':raise c.CheckRefused('GA03_RESPONSE_LOST')


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    root=tmp_path/'repo';root.mkdir();git(root,'init','-q','-b','main');git(root,'config','user.name','Fixture');git(root,'config','user.email','fixture@example.invalid')
    (root/'README').write_text('base');git(root,'add','.');git(root,'commit','-qm','base');git(root,'switch','-c','feature/check')
    (root/'README').write_text('checked');(root/'config').mkdir();(root/'config/delivery-required-ci.json').write_text('{}\n');git(root,'add','.');git(root,'commit','-qm','source');git(root,'remote','add','origin','https://github.com/Example/repo.git')
    state=tmp_path/'state';rec=ds.start(root,state_root=state);sid=rec['id'];sha=rec['head_sha']
    policy=tmp_path/'policy.json';body={'schema':'devtool.ga02.relationship-registry.v1','policy_id':'test','source_sha':sha,'repositories':[{'repository':'Example/repo','repository_id':91,'owner_id':92,'installation_id':81,'role':'base','visibility':'private'}],'relationships':[]}
    # Real load_registry input uses exact production field names below.
    from devtool.github_relationships import load_registry
    registry=SimpleNamespace(sha256='a'*64,node=lambda slug:SimpleNamespace(repository_id=91,role='base'))
    fake=FakeChecks(sha,state,sid);proof={'verdict':'success','scope':'native-committed-required-validation','step_count':1}
    monkeypatch.setattr(c,'load_registry',lambda *a,**kw:registry)
    monkeypatch.setattr(c,'_scope',lambda *a,**kw:fake)
    monkeypatch.setattr(c,'_remote_head',lambda *a:sha)
    monkeypatch.setattr(c,'_evidence',lambda *a:deepcopy(proof))
    def run(kind='source',attempt=1,yes=False):
        return c.publish(sid,kind=kind,attempt=attempt,policy=policy,policy_sha256=registry.sha256,yes=yes,state_root=state)
    return SimpleNamespace(root=root,state=state,sid=sid,sha=sha,run=run,fake=fake,proof=proof,registry=registry)


def test_preview_no_write_no_journal(fixture):
    f=fixture;assert f.run()['phase']=='preview';assert f.fake.writes==[]
    assert 'check_publications' not in ds._read(f.state/(f.sid+'.json'))


@pytest.mark.parametrize('kind',tuple(c.NAMES))
def test_create_finish_independent_observation_and_idempotence(fixture,kind):
    f=fixture;r=f.run(kind,yes=True);assert r['phase']=='confirmed';assert r['release_qualified'] is False
    assert f.fake.writes==['POST','PATCH'];assert f.fake.rows[0]['name']==c.NAMES[kind]
    r=f.run(kind,yes=True);assert r['remote_mutation'] is False;assert f.fake.writes==['POST','PATCH']
    assert ds.status(f.sid,state_root=f.state)['check_publications'][0]['phase']=='confirmed'


def test_lost_create_reconciled_only_then_separate_completion(fixture):
    f=fixture;f.fake.lost='create'
    with pytest.raises(c.CheckRefused,match='LOST'):f.run(yes=True)
    assert f.fake.writes==['POST'];f.fake.lost=''
    assert f.run(yes=True)['phase']=='created';assert f.fake.writes==['POST']
    assert f.run(yes=True)['phase']=='confirmed';assert f.fake.writes==['POST','PATCH']


def test_lost_complete_no_replay(fixture):
    f=fixture;f.fake.lost='complete'
    with pytest.raises(c.CheckRefused,match='LOST'):f.run(yes=True)
    f.fake.lost='';assert f.run()['phase']=='confirmed';assert f.fake.writes==['POST','PATCH']


def test_lost_absent_create_never_replays(fixture):
    f=fixture;f.fake.lost='create'
    with pytest.raises(c.CheckRefused):f.run(yes=True)
    f.fake.rows=[]
    with pytest.raises(c.CheckRefused,match='UNCERTAIN'):f.run(yes=True)
    assert f.fake.writes==['POST']
    with pytest.raises(ds.DeliverySessionError,match='GA03'):ds.record_intent(f.sid,'pr_create','new',approved=True,state_root=f.state)


def test_competing_check_and_duplicate_identity(fixture):
    f=fixture;f.fake.rows=[{'id':5,'name':c.NAMES['source'],'app':{'id':71},'external_id':'other'}]
    with pytest.raises(c.CheckRefused,match='UNREGISTERED'):f.run(yes=True)
    assert f.fake.writes==[]
    f.fake.rows=[];f.run(yes=True);f.fake.rows.append({**f.fake.rows[0],'id':9})
    with pytest.raises(c.CheckRefused,match='DUPLICATE'):f.run(yes=True)


@pytest.mark.parametrize('field,value',[('head_sha','f'*40),('app',{'id':999}),('details_url','https://evil.invalid/'),('conclusion','failure')])
def test_remote_mismatch_refused(fixture,field,value):
    f=fixture;f.run(yes=True);f.fake.rows[0][field]=value
    with pytest.raises(c.CheckRefused,match='MISMATCH'):f.run(yes=True)
    assert f.fake.writes==['POST','PATCH']


def test_stale_and_duplicate_attempts(fixture):
    f=fixture;f.run(yes=True)
    with pytest.raises(c.CheckRefused,match='DUPLICATE_EVIDENCE'):f.run(attempt=2,yes=True)
    f.proof['step_count']=2
    with pytest.raises(c.CheckRefused,match='STALE_EVIDENCE'):f.run(yes=True)
    f.run(attempt=2,yes=True)
    with pytest.raises(c.CheckRefused,match='STALE_OR_NONCONTIGUOUS'):f.run(attempt=1)
    with pytest.raises(c.CheckRefused,match='STALE_OR_NONCONTIGUOUS'):f.run(attempt=4)


def test_failure_annotation_is_bounded_and_verified(fixture):
    f=fixture;f.proof.update(verdict='failure',scope='independent-required-actions-attempts')
    r=f.run('integration',yes=True);assert r['verdict']=='failure';assert r['annotation_count']==1
    assert f.fake.rows[0]['annotations'][0]['path']=='config/delivery-required-ci.json'
    f.fake.rows[0]['annotations'][0]['path']='../../secret'
    with pytest.raises(c.CheckRefused,match='ANNOTATION_MISMATCH'):f.run('integration')


def test_dirty_source_and_remote_drift(fixture,monkeypatch):
    f=fixture;(f.root/'README').write_text('dirty')
    with pytest.raises(c.CheckRefused,match='SOURCE_DRIFT'):f.run(yes=True)
    git(f.root,'checkout','--','README');monkeypatch.setattr(c,'_remote_head',lambda *a:'f'*40)
    with pytest.raises(c.CheckRefused,match='REMOTE_HEAD_DRIFT'):f.run(yes=True)
    assert f.fake.writes==[]


def test_pending_actions_not_green():
    assert c._integration({},None)['verdict']=='neutral'


def test_absolute_askpass_runs_without_env(tmp_path):
    p=tmp_path/'askpass';p.write_text(_askpass_source());p.chmod(0o700)
    assert p.read_text().splitlines()[0]=='#!'+os.path.realpath(sys.executable)
    result=subprocess.run([str(p),'Username for \'https://github.com\': '],
        env={'DEVTOOL_APP_AUTH_REPOSITORY':'Example/repo'},capture_output=True,timeout=5)
    assert result.returncode==0 and result.stdout==b'x-access-token\n'


def test_no_checks_write_union_or_false_diagnostics():
    assert capability_for_operation('delivery.checks.publish')=='checks_write'
    assert _PERMISSIONS['checks_write']=={'checks':'write','contents':'read'}
    assert _git_error_code('HTTP/2 401 TLS handshake','',0)=='GIT_EXIT_ZERO'
    assert _git_error_code('fatal: cannot run askpass.py: No such file or directory','',128)=='GIT_ASKPASS_UNAVAILABLE'
    assert _git_error_code('fatal: SSL certificate problem: self-signed certificate','',128)=='GIT_TLS_CERTIFICATE_FAILURE'
    assert _git_error_code('fatal: Could not resolve host: github.com','',128)=='GIT_DNS_FAILURE'
    assert _git_error_code('error: src refspec main does not match any','',1)=='GIT_REF_MISSING'


def test_native_history_reader_rejects_changed_evidence(tmp_path,monkeypatch):
    from test_native_delivery_continuity_m3 import fixture as native_fixture
    repo,archive,state,receipt,_,_ = native_fixture(tmp_path,monkeypatch)
    rec=ds.adopt_applied(repo,archive,state_root=state)
    raw=ds._read(state/(rec['id']+'.json'))
    assert c._native_source(raw)['verdict']=='success'
    receipt['validation_plan']['validation_step_outcomes'][0]['skipped']=True
    with pytest.raises(c.CheckRefused): c._native_source(raw)


def test_static_history_legacy_migration(tmp_path,monkeypatch):
    from test_native_delivery_continuity_m3 import fixture as native_fixture
    repo,archive,state,h,_,_=native_fixture(tmp_path,monkeypatch)
    h['validation_status']='not-run'
    h['validation_plan']['validation_step_outcomes'][0]['name']='artifact-test:unit'
    h['child_outcomes']=[{'name':name,'returncode':0,'skipped':False} for name in ('apply-overlay','artifact-test:unit','git-commit')]
    original=deepcopy(h)
    rec=ds.adopt_applied(repo,archive,state_root=state)
    raw=ds._read(state/(rec['id']+'.json'))
    assert c._native_source(raw)['verdict']=='success'
    assert h==original
    assert raw['native_apply']['validation_evidence_source']=='original-static-child-outcomes'


def test_actual_independent_actions_evidence(tmp_path):
    from test_delivery_ci_evidence import Client
    from devtool.delivery_ci_policy import load_policy
    root=tmp_path/'repo';root.mkdir();git(root,'init','-q','-b','main');git(root,'config','user.name','QA');git(root,'config','user.email','qa@example.invalid')
    (root/'config').mkdir();(root/'config/delivery-required-ci.json').write_text(json.dumps({'schema_version':1,'repository':'Example/repo','base_branch':'main','event':'pull_request','required_workflows':[{'name':'Hosted','workflow_id':117}],'release_qualification':'separate'}))
    git(root,'add','.');git(root,'commit','-qm','base');git(root,'switch','-c','feature/check');git(root,'remote','add','origin','https://github.com/Example/repo.git')
    record=ds.start(root,state_root=tmp_path/'state');client=Client(record)
    from devtool.delivery_github import _ci_candidate
    policy=load_policy(root,expected_repository='Example/repo',expected_base='main')
    pr={'number':17,'head':record['head'],'base':'main','head_sha':record['head_sha'],'url':'https://github.com/Example/repo/pull/17'}
    row=_ci_candidate(client.rows[0],record,pr,'b'*40)
    from devtool.delivery_ci_evidence import verify_exact_attempts
    evidence=verify_exact_attempts(client,record['repository'],[row],policy.workflow_ids,validate_run=lambda raw:_ci_candidate(raw,record,pr,'b'*40))
    record.update(pr=pr,ci={'state':'REQUIRED_CHECKS_PASSED','checks_qualified':True,'source_sha':record['head_sha'],'runs':[row],**policy.receipt(),'required':['Hosted'],'registration':{'event':'pull_request','run_count':1,'started_at':'2026-11-25T00:00:00Z','deadline_at':'2026-11-25T00:01:00Z'},'branch_rules':{'protection_checked':True,'ruleset_checked':True,'required_status_contexts_verified':True,'release_qualified':False},'exact_run_evidence':evidence})
    assert c._integration(record,client)['verdict']=='success'
    client.jobs[0]['conclusion']='failure'
    assert c._integration(record,client)['verdict']=='neutral'
    client.rows[0]['conclusion']='failure';row=_ci_candidate(client.rows[0],record,pr,'b'*40)
    record['ci'].update(state='CHECKS_FAILED',checks_qualified=False,runs=[row])
    assert c._integration(record,client)['verdict']=='failure'


def test_standalone_federation_neutral(fixture):
    record=ds._read(fixture.state/(fixture.sid+'.json'))
    assert c._federation(record,None)['state']=='NOT_CONFIGURED'


def test_check_transport_fixed_endpoints_and_token_scope():
    import io
    class Response(io.BytesIO):
        status=200
    class Opener:
        def __init__(self):self.calls=[]
        def open(self,req,timeout):
            self.calls.append(req);return Response(b'{"total_count":0,"check_runs":[]}')
    opener=Opener();access=SimpleNamespace(repository='Example/repo',token='synthetic-test-token',expires_at=int(time.time())+1000,capability='checks_read')
    t=c.ChecksTransport(access,opener=opener)
    assert t.runs('a'*40)==[]
    assert opener.calls[0].full_url=='https://api.github.com/repos/Example/repo/commits/'+'a'*40+'/check-runs?filter=all&per_page=100&page=1'
    with pytest.raises(c.CheckRefused,match='WRITE_SCOPE_REQUIRED'):t.create({})
    with pytest.raises(c.CheckRefused,match='HTTP_PATH_REFUSED'):t._call('GET','../other')
    assert len(opener.calls)==1
