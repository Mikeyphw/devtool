"""G7 optional provider integration regressions; no real network or publishing."""
import io
import json
from pathlib import Path
import types
from urllib.error import URLError, HTTPError
import pytest

from devtool.actions_extensions import (ci_metrics, trusted_runners, gitlab_pipelines,
    upload_plan, publish_asset, _asset_inventory)
from devtool.github_actions import GitHubActionsError, GitHubDispatchOutcomeUnknown

SLUG='Mikeyphw/devtool'
SHA='a'*40
DIGEST='b'*64
class Client:
    _token='test-only-never-print-this'
    timeout=5
    def __init__(self, *, runs=None, endpoints=None, opener=None):
        self._runs=runs or []
        self.endpoints=endpoints or {}
        self._opener=opener
    def runs(self, slug, limit=30):
        assert slug==SLUG
        return self._runs[:limit]
    def _get(self, url):
        return self.endpoints[url]
class Response:
    status=200
    def __init__(self, payload, status=200):
        self.status=status
        self.data=json.dumps(payload).encode()
    def __enter__(self):return self
    def __exit__(self, *a):pass
    def read(self, size=-1):return self.data[:size]
class Opener:
    def __init__(self, response):self.response=response;self.calls=[]
    def open(self, request, timeout):
        self.calls.append((request.full_url,request.get_method()))
        if isinstance(self.response,Exception):raise self.response
        return self.response

def test_metrics_actual_completions_only():
    rows=[{'id':1,'status':'completed','conclusion':'success','run_attempt':1,
           'run_started_at':'2026-10-13T00:00:00Z','updated_at':'2026-10-13T00:02:00Z'},
          {'id':2,'status':'completed','conclusion':'failure','run_attempt':2},
          {'id':3,'status':'in_progress','conclusion':None,'run_attempt':3},
          {'id':4,'status':'completed','conclusion':'success','run_attempt':1,
           'run_started_at':'invalid','updated_at':'2026-10-13T00:02:00Z'}]
    report=ci_metrics(Client(runs=rows),SLUG)
    assert report['observed_runs']==4
    assert report['timing_sample_count']==1 and report['mean_duration_seconds']==120
    assert report['observed_reruns']==2 and report['rerun_sample_count']==4
    assert report['outcomes']=={'success':2,'failure':1,'incomplete_or_other':1}
    assert report['qualified_release'] is False

@pytest.mark.parametrize('rows',[ [{'id':1},{'id':1}], [{'id':1,'repository':{'full_name':'Other/repo'}}], [{'id':0}], [{'id':True}] ])
def test_metrics_reject_identity_ambiguity(rows):
    with pytest.raises(GitHubActionsError):ci_metrics(Client(runs=rows),SLUG)

@pytest.mark.parametrize('limit',[0,101,-1,True])
def test_metrics_reject_invalid_limit(limit):
    with pytest.raises(GitHubActionsError):ci_metrics(Client(),SLUG,limit=limit)

def test_runner_discovery_policy_requires_exact_id_name_labels(tmp_path):
    policy=tmp_path/'runners.json'
    policy.write_text(json.dumps({'schema_version':1,'repository':SLUG,'profiles':[
        {'id':123,'name':'private-ci','labels':['self-hosted','linux']},
        {'id':124,'name':'wrong-name','labels':['self-hosted']}]}))
    data={'total_count':2,'runners':[
        {'id':123,'name':'private-ci','status':'online','busy':False,'labels':[{'name':'self-hosted'},{'name':'linux'}]},
        {'id':124,'name':'different','status':'online','busy':False,'labels':[{'name':'self-hosted'}]}]}
    report=trusted_runners(Client(endpoints={f'/repos/{SLUG}/actions/runners?per_page=100':data}),SLUG,policy_path=str(policy))
    assert [x['trust_profile_matched'] for x in report['runners']]==[True,False]
    assert all(x['eligible_for_release'] is False for x in report['runners'])
    assert report['trust_is_local_assertion_only'] is True

@pytest.mark.parametrize('total,rows',[(3,[{'id':1,'labels':[]}]),(2,[{'id':1,'labels':[]},{'id':1,'labels':[]}]),(0,[{'id':1,'labels':[]}])])
def test_runner_inventory_fails_on_truncation_duplicates(total,rows):
    data={'total_count':total,'runners':rows}
    with pytest.raises(GitHubActionsError):
        trusted_runners(Client(endpoints={f'/repos/{SLUG}/actions/runners?per_page=100':data}),SLUG)

def test_runner_policy_symlink_refused(tmp_path):
    real=tmp_path/'real.json';real.write_text('{}')
    link=tmp_path/'link.json';link.symlink_to(real)
    with pytest.raises(GitHubActionsError):trusted_runners(Client(),SLUG,policy_path=str(link))

def test_gitlab_optin_provider_does_not_claim_github_provenance():
    rows=[{'id':4,'project_id':123,'sha':SHA,'ref':'main','status':'success'}]
    opener=Opener(Response(rows))
    report=gitlab_pipelines(project_id=123,token='fake',limit=5,opener=opener)
    assert report['pipelines'][0]['id']==4
    assert not report['github_provenance_verified'] and not report['qualified_release']
    assert opener.calls==[('https://gitlab.com/api/v4/projects/123/pipelines?per_page=5','GET')]

@pytest.mark.parametrize('value',[{'id':5,'project_id':125,'sha':SHA}, {'id':5,'project_id':123,'sha':'wrong'}, {'id':'5','project_id':123,'sha':SHA}])
def test_gitlab_rejects_foreign_or_invalid_identity(value):
    with pytest.raises(GitHubActionsError):gitlab_pipelines(project_id=123,token='fake',opener=Opener(Response([value])))

@pytest.mark.parametrize('error',[URLError('secret-token'),HTTPError('https://gitlab.com/api/v4/projects/1/pipelines',403,'secret-token',{},None)])
def test_gitlab_failure_never_reports_credential(error):
    with pytest.raises(GitHubActionsError) as caught:
        gitlab_pipelines(project_id=1,token='SHOULD_NOT_LEAK',opener=Opener(error))
    assert 'SHOULD_NOT_LEAK' not in str(caught.value) and 'secret-token' not in str(caught.value)

def _publish_case(tmp_path,monkeypatch,*,draft=True,head=SHA):
    import devtool.actions_extensions as ex
    root=tmp_path
    artifact=root/'signed.apk';artifact.write_bytes(b'tiny signed candidate')
    digest=__import__('hashlib').sha256(artifact.read_bytes()).hexdigest()
    def evidence(r,**kw):
        return {'remote_repository':SLUG,'signed_sha256':digest,'signed_registry_id':'123456abcdef'}
    monkeypatch.setattr('devtool.actions_local_evidence.signed_local_evidence',evidence)
    monkeypatch.setattr('devtool.artifact_registry.load_registry',lambda r:{'items':[{'id':'123456abcdef','path':'signed.apk'}]})
    release={'id':55,'tag_name':'v1.0.0','draft':draft,'prerelease':True}
    endpoints={f'/repos/{SLUG}/releases/tags/v1.0.0':release,
               f'/repos/{SLUG}/git/ref/tags/v1.0.0':{'object':{'sha':head,'type':'commit'}}}
    client=Client(endpoints=endpoints,opener=Opener(Response([])))
    return root,client,digest

def test_publish_plan_requires_draft_matching_sha(tmp_path,monkeypatch):
    root,client,digest=_publish_case(tmp_path,monkeypatch)
    plan,asset=upload_plan(client,SLUG,root,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA)
    assert asset==root/'signed.apk'
    assert plan['asset_sha256']==digest and plan['published'] is False
    assert plan['qualified_release'] is False
    assert client._opener.calls==[(f'https://api.github.com/repos/{SLUG}/releases/55/assets?per_page=100','GET')]

@pytest.mark.parametrize('draft,sha',[(False,SHA),(True,'f'*40)])
def test_publish_refuses_nondraft_or_wrong_commit(tmp_path,monkeypatch,draft,sha):
    root,client,_=_publish_case(tmp_path,monkeypatch,draft=draft,head=sha)
    with pytest.raises(GitHubActionsError):upload_plan(client,SLUG,root,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA)

def test_publish_requires_double_consent_before_any_network(tmp_path):
    client=Client()
    with pytest.raises(GitHubActionsError,match='--yes'):
        publish_asset(client,SLUG,tmp_path,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA)

@pytest.mark.parametrize('assets',[[{'name':'hud.apk'}],[{'name':'other.apk'}]*100])
def test_publish_rejects_occupied_or_incomplete_inventory(tmp_path,monkeypatch,assets):
    root,client,_=_publish_case(tmp_path,monkeypatch)
    client._opener=Opener(Response(assets))
    with pytest.raises(GitHubActionsError):upload_plan(client,SLUG,root,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA)

def test_publish_receipt_is_accepted_not_qualified(tmp_path,monkeypatch):
    root,client,digest=_publish_case(tmp_path,monkeypatch)
    responses=[Response([]),Response({'id':99,'name':'hud.apk','size':len(b'tiny signed candidate'),'digest':'sha256:'+digest},201)]
    class Sequence:
        calls=[]
        def open(self,request,timeout):
            self.calls.append((request.full_url,request.get_method()))
            return responses.pop(0)
    client._opener=Sequence()
    report=publish_asset(client,SLUG,root,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA,yes=True,allow_unqualified=True)
    assert report['upload_accepted'] is True and report['published'] is False
    assert report['qualified_release'] is False and report['remote_digest_verified'] is True
    assert client._opener.calls[1][1]=='POST'
    assert client._opener.calls[1][0].startswith('https://uploads.github.com/')

def test_publish_transport_failure_is_ambiguous(tmp_path,monkeypatch):
    root,client,_=_publish_case(tmp_path,monkeypatch)
    class Sequence:
        i=0
        def open(self,req,timeout):
            self.i+=1
            if self.i==1:return Response([])
            raise URLError('secret-token')
    client._opener=Sequence()
    with pytest.raises(GitHubDispatchOutcomeUnknown) as caught:
        publish_asset(client,SLUG,root,registry_id='123456abcdef',tag='v1.0.0',asset_name='hud.apk',expected_sha=SHA,yes=True,allow_unqualified=True)
    assert 'secret-token' not in str(caught.value)

