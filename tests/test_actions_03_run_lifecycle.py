"""ACTIONS-03: exact remote run identity, no speculative completion and safe control."""
from __future__ import annotations
import io, json, zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import pytest

from devtool.actions_runs import (_normalize_run, _normalize_job, _safe_receipt,
                                 select_run, snapshot, watch, remote_logs, run_id)
from devtool.actions_dispatch import _durable_record
from devtool.github_actions import GitHubActionsClient, GitHubActionsError, actions_cli
from devtool.cli import build_parser

SLUG='owner/repo';ID=193
class Response:
 def __init__(self,obj,status=200):
  self.status=status;self.headers={};self.stream=io.BytesIO(json.dumps(obj).encode() if isinstance(obj,dict) else obj)
 def __enter__(self):return self
 def __exit__(self,*a):return None
 def read(self,n):return self.stream.read(n)
class Fake:
 def __init__(self,*items):self.items=list(items);self.requests=[]
 def open(self,req,timeout):
  self.requests.append(req)
  if not self.items:raise AssertionError('unexpected HTTP request')
  item=self.items.pop(0)
  if isinstance(item,Exception):raise item
  return item

def remote(status='in_progress',conclusion=None,id=ID,attempt=1,sha='a'*40):
 return {'id':id,'status':status,'conclusion':conclusion,'repository':{'full_name':SLUG},
         'workflow_id':17,'run_attempt':attempt,'head_sha':sha}

def receipt(folder, identity=ID):
 obj={'schema':1,'request_id':'f'*32,'state':'accepted-correlated','run_id':identity,
      'repository':SLUG,'pre_dispatch_sha':'a'*40,'input_names':['secret_name']}
 path=folder/('f'*32+'.json');_durable_record(path,obj)
 return path,obj

@pytest.mark.parametrize('bad',['','0','-1','1.5','1/2','x','999999999999999999999999999999'])
def test_run_id_is_strict(bad):
 with pytest.raises(GitHubActionsError):run_id(bad)

def test_progress_not_conflated_with_completion():
 assert not _normalize_run(remote('in_progress'),SLUG)['completed']
 assert not _normalize_run(remote('completed'),SLUG)['completed']
 assert _normalize_run(remote('completed','failure'),SLUG)['completed']
 assert _normalize_run(remote('completed','failure'),SLUG)['conclusion']=='failure'
 assert _normalize_run(remote('nonexistent'),SLUG)['status']=='unknown'

def test_run_repo_mismatch_fails_closed():
 raw=remote();raw['repository']['full_name']='other/repo'
 with pytest.raises(GitHubActionsError,match='another repository'):_normalize_run(raw,SLUG)
 with pytest.raises(GitHubActionsError,match='identity mismatch'):_normalize_run(remote(id=ID+1),SLUG,ID)

def test_exact_receipt_lookup_no_unlinked_or_foreign_run(tmp_path):
 receipt(tmp_path)
 assert select_run('',None,'f'*32,lambda: 'another/repo',root=tmp_path)[:2]==(SLUG,ID)
 with pytest.raises(GitHubActionsError,match='different'):
  select_run('other/repo',None,'f'*32,lambda:SLUG,root=tmp_path)
 with pytest.raises(GitHubActionsError,match='different'):
  select_run('',ID+1,'f'*32,lambda:SLUG,root=tmp_path)
 with pytest.raises(GitHubActionsError,match='guessing'):
  path,obj=receipt(tmp_path,identity=None)
  _safe_receipt('f'*32,tmp_path)

def test_receipt_symlink_rejected(tmp_path):
 path,obj=receipt(tmp_path)
 path.unlink();path.symlink_to(tmp_path/'elsewhere')
 with pytest.raises(GitHubActionsError):_safe_receipt('f'*32,tmp_path)

def test_snapshot_persists_status_but_not_claims_success(tmp_path):
 path,obj=receipt(tmp_path);client=GitHubActionsClient(opener=Fake(Response(remote())))
 result=snapshot(client,SLUG,ID,path,obj)
 assert result['remote']['completed'] is False
 stored=json.loads(path.read_text()); assert stored['state']=='accepted-correlated'
 assert stored['last_observation']['status']=='in_progress'
 assert stored['last_observation']['completed'] is False
 assert 'input_values' not in path.read_text()

def test_commit_mismatch_warns_without_qualification(tmp_path):
 path,obj=receipt(tmp_path)
 result=snapshot(GitHubActionsClient(opener=Fake(Response(remote(sha='b'*40)))),SLUG,ID,path,obj)
 assert result['source_binding']=='not-confirmed' and 'warning' in result

def test_watch_bounded_and_terminates_when_remote_concludes(tmp_path):
 path,obj=receipt(tmp_path)
 op=Fake(Response(remote('queued')),Response(remote('in_progress')),Response(remote('completed','success')))
 calls=[]
 result=watch(GitHubActionsClient(opener=op),SLUG,ID,path,obj,max_checks=20,interval=1,sleep=calls.append)
 assert result['complete'] and result['checks']==3 and len(calls)==2
 assert len(op.requests)==3
 assert result['last']['remote']['conclusion']=='success'

@pytest.mark.parametrize('max_checks,interval',[(0,1),(121,1),(1,0),(1,301)])
def test_watch_bounds_fail_without_api_calls(max_checks,interval):
 with pytest.raises(GitHubActionsError):watch(GitHubActionsClient(),SLUG,ID,None,None,max_checks=max_checks,interval=interval)

def test_jobs_are_paginated_and_include_steps():
 job={'id':1,'run_id':ID,'status':'completed','conclusion':'failure',
      'steps':[{'name':'build','status':'completed','conclusion':'failure','number':1}]}
 op=Fake(Response({'jobs':[job]}))
 res=GitHubActionsClient(opener=op).run_jobs(SLUG,ID)
 assert _normalize_job(res[0])['steps'][0]['conclusion']=='failure'
 assert op.requests[0].full_url.endswith(f'/actions/runs/{ID}/jobs?per_page=100&page=1')

def test_runs_list_bounded_and_repository_scoped():
 op=Fake(Response({'workflow_runs':[remote()]}))
 client=GitHubActionsClient(opener=op)
 assert len(client.runs(SLUG,limit=5))==1
 assert '/repos/owner/repo/actions/runs?' in op.requests[0].full_url
 with pytest.raises(GitHubActionsError):client.runs(SLUG,limit=101)

def test_mutations_require_token_and_write_only_explicit_commands():
 op=Fake(Response(b'',202),Response(b'',201))
 client=GitHubActionsClient('fake-secret',opener=op)
 assert client.run_mutation(SLUG,ID,'cancel')['outcome']=='pending-remote-confirmation'
 assert client.run_mutation(SLUG,ID,'rerun-failed-jobs')['execution_complete'] is False
 assert all(req.get_method()=='POST' for req in op.requests)
 assert op.requests[0].full_url.endswith(f'/actions/runs/{ID}/cancel')
 with pytest.raises(GitHubActionsError):GitHubActionsClient(opener=op).run_mutation(SLUG,ID,'cancel')
 with pytest.raises(GitHubActionsError):client.run_mutation(SLUG,ID,'delete')

def test_mutation_transport_uncertain_and_no_retry():
 op=Fake(URLError('offline'))
 with pytest.raises(GitHubActionsError,match='uncertain'):
  GitHubActionsClient('x',opener=op).run_mutation(SLUG,ID,'cancel')
 assert len(op.requests)==1

def test_logs_download_does_not_forward_authorization_and_is_unextracted(tmp_path):
 binary=io.BytesIO()
 with zipfile.ZipFile(binary,'w') as z:z.writestr('jobs/a.txt','trace')
 url='https://results.actions.githubusercontent.com/test?sig=REDACTED'
 redirect=HTTPError(f'https://api.github.com/repos/{SLUG}/actions/runs/{ID}/logs',302,'Found',{'Location':url},None)
 op=Fake(redirect,Response(binary.getvalue()))
 result=remote_logs(GitHubActionsClient('secret-token',opener=op),SLUG,ID,root=tmp_path)
 assert result['qualified_artifact'] is False and not result['extracted']
 assert Path(result['archive']).is_file() and Path(result['archive']).stat().st_mode & 0o777 == 0o600
 assert op.requests[0].get_header('Authorization')=='Bearer secret-token'
 assert op.requests[1].get_header('Authorization') is None
 assert 'REDACTED' not in json.dumps(result)

def test_logs_host_rejects_signed_url_to_attacker(tmp_path):
 url='https://evil.example/log.zip?signature=private'
 redirect=HTTPError('https://api.github.com',302,'Found',{'Location':url},None)
 op=Fake(redirect)
 with pytest.raises(GitHubActionsError,match='Unsupported'):
  remote_logs(GitHubActionsClient('secret',opener=op),SLUG,ID,root=tmp_path)
 assert len(op.requests)==1

def test_404_diagnostics_distinguish_repository_and_workflow():
 notfound=HTTPError('https://api.github.com/repos/owner/repo',404,'Not Found',{},None)
 with pytest.raises(GitHubActionsError,match='Repository owner/repo'):
  GitHubActionsClient(opener=Fake(notfound)).repository(SLUG)
 with pytest.raises(GitHubActionsError,match='Workflow ci.yml'):
  GitHubActionsClient(opener=Fake(notfound)).workflow(SLUG,'ci.yml')

def test_new_cli_commands_and_no_confirmation_no_mutation(monkeypatch):
 parser=build_parser()
 for op in ('runs','view','jobs','watch','resume','cancel','rerun','logs','diagnose'):
  args=parser.parse_args(['actions',op,'--github-repo',SLUG])
  assert args.actions_command==op
 called=[]
 monkeypatch.setattr('devtool.github_actions.GitHubActionsClient',lambda *a,**kw:called.append(True))
 for action in ('cancel','rerun'):
  args=SimpleNamespace(actions_command=action,github_repo=SLUG,auth='none',run_id=ID,request_id='',yes=False)
  with pytest.raises(GitHubActionsError,match='--yes'):actions_cli(args,Path.cwd())
 assert called

def test_control_intent_is_durable_before_write_and_uncertain_remains(tmp_path):
 from devtool.actions_runs import control_run
 op=Fake(URLError('no connectivity'))
 with pytest.raises(GitHubActionsError,match='outcome uncertain'):
  control_run(GitHubActionsClient('token',opener=op),SLUG,ID,'cancel',root=tmp_path)
 saved=json.loads(next(tmp_path.glob('*.json')).read_text())
 assert saved['state']=='outcome-unknown' and saved['run_id']==ID
 assert 'token' not in json.dumps(saved) and len(op.requests)==1

def test_control_success_is_only_acceptance(tmp_path):
 from devtool.actions_runs import control_run
 result=control_run(GitHubActionsClient('token',opener=Fake(Response(b'',202))),SLUG,ID,'cancel',root=tmp_path)
 assert result['execution_complete'] is False
 saved=json.loads(next(tmp_path.glob('*.json')).read_text())
 assert saved['state']=='accepted-pending-remote-confirmation'

def test_resume_requires_correlated_receipt_before_remote_query(monkeypatch):
 args=SimpleNamespace(actions_command='resume',github_repo=SLUG,auth='none',run_id=ID,request_id='')
 monkeypatch.setattr('devtool.github_actions.GitHubActionsClient',lambda *a,**k:object())
 with pytest.raises(GitHubActionsError,match='requires a prior --request-id'):
  actions_cli(args,Path.cwd())
