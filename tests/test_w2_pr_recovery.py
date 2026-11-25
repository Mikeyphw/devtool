"""Native PR recovery against real session journals and simulated provider."""
import datetime
import subprocess
from urllib.parse import parse_qs, urlparse
import pytest
from devtool import delivery_github as g, delivery_session as s
BASE='b'*40

def git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()

@pytest.fixture
def recovery(tmp_path,monkeypatch):
    repo=tmp_path/'repo';repo.mkdir();git(repo,'init','-b','main');git(repo,'config','user.name','Fixture');git(repo,'config','user.email','fixture@example.invalid');(repo/'a').write_text('base');git(repo,'add','.');git(repo,'commit','-m','base');git(repo,'switch','-c','feature/recovery');git(repo,'remote','add','origin','https://github.com/Owner/repo.git')
    root=tmp_path/'sessions';item=s.start(repo,state_root=root,base_sha=BASE)
    sid=item['id'];sha=item['head_sha']
    class Provider:
        _token='fixture'
        posts=0;reads=0;outcome='uncertain';rows=[];base=BASE
        def _get(self,path):return {'commit':{'sha':self.base if path.endswith('/main') else sha}}
        def row(self,state='open'):
            return {'number':1,'state':state,'head':{'sha':sha,'ref':item['head'],'label':'Owner:'+item['head'],'repo':{'full_name':'Owner/repo'}},'base':{'sha':BASE,'ref':'main','repo':{'full_name':'Owner/repo'}}}
        def request(self,client,method,path,payload=None):
            if method=='GET':
                scope=parse_qs(urlparse(path).query)['state'][0]
                if scope=='all':self.reads+=1
                return [r for r in self.rows if scope=='all' or r['state']=='open']
            assert method=='POST';self.posts+=1
            if self.outcome=='uncertain':raise s.DeliverySessionError('POST outcome uncertain')
            self.rows=[self.row()];return self.rows[0]
    p=Provider();monkeypatch.setattr(g,'_github_request',p.request)
    times=iter(datetime.datetime(2026,1,1,tzinfo=datetime.timezone.utc)+datetime.timedelta(seconds=i*2) for i in range(100))
    monkeypatch.setattr(g,'_utc',lambda:next(times).isoformat());monkeypatch.setattr(g.time,'sleep',lambda t:None)
    def run(**kw):return g.pr(sid,client=p,state_root=root,**kw)
    def raw():return s._read(root/(sid+'.json'))
    def pending():return run()['intent_id']
    with pytest.raises(s.DeliverySessionError,match='uncertain'):run(yes=True)
    assert run()['state']=='PR_RECONCILIATION_REQUIRED'
    return p,run,raw,pending,root,sid

def test_one_successor_confirmed(recovery):
    p,run,raw,pending,root,sid=recovery;old=pending();p.outcome='ok'
    result=run(yes=True,recovery_intent_id=old,prior_process_finished=True)
    assert result['created_now'] and p.posts==2 and p.reads==3
    a,b=raw()['intents'];assert a['id']==old and a['state']=='reconciled_no_remote_change' and b['predecessor_intent_id']==old and b['state']=='confirmed'
    assert s.status(sid,state_root=root)['resumable']
    assert not run()['created_now'] and p.posts==2

def test_uncertain_successor_never_third(recovery):
    p,run,raw,pending,root,sid=recovery;old=pending()
    with pytest.raises(s.DeliverySessionError,match='uncertain'):run(yes=True,recovery_intent_id=old,prior_process_finished=True)
    result=run();assert result['attempt']==2 and not result['successor_permitted']
    with pytest.raises(s.DeliverySessionError):run(yes=True,recovery_intent_id=result['intent_id'],prior_process_finished=True)
    assert p.posts==2 and not s.status(sid,state_root=root)['resumable']
    p.rows=[p.row()];assert run()['state']=='PR_VERIFIED';assert s.status(sid,state_root=root)['resumable']

@pytest.mark.parametrize('fault',['ordinary','unapproved','not_finished','wrong_id','wrong_request','historical','base_drift','legacy'])
def test_blocked_recovery(recovery,fault):
    p,run,raw,pending,root,sid=recovery;old=pending();kwargs={'yes':True,'recovery_intent_id':old,'prior_process_finished':True}
    if fault=='ordinary':kwargs={'yes':True}
    elif fault=='unapproved':kwargs['yes']=False
    elif fault=='not_finished':kwargs['prior_process_finished']=False
    elif fault=='wrong_id':kwargs['recovery_intent_id']='a'*32
    elif fault=='wrong_request':kwargs['title']='changed'
    elif fault=='historical':p.rows=[p.row('closed')]
    elif fault=='base_drift':p.base='c'*40
    elif fault=='legacy':
        doc=raw();doc['intents'][0].pop('request_sha256');s._write(root/(sid+'.json'),doc)
    with pytest.raises(s.DeliverySessionError):run(**kwargs)
    assert p.posts==1 and len(raw()['intents'])==1

def test_positive_reconciliation_no_post(recovery):
    p,run,raw,pending,root,sid=recovery;p.rows=[p.row()];assert run()['state']=='PR_VERIFIED';assert p.posts==1;assert s.status(sid,state_root=root)['resumable']

def test_legacy_explicit_acknowledgement(recovery):
    p,run,raw,pending,root,sid=recovery;old=pending();doc=raw();doc['intents'][0].pop('request_sha256');s._write(root/(sid+'.json'),doc);p.outcome='ok'
    assert run(yes=True,recovery_intent_id=old,prior_process_finished=True,acknowledge_original_request=True)['created_now'];assert p.posts==2
