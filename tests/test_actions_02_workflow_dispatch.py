"""ACTIONS-02 tests: mocked network only; never contact GitHub."""
import base64
import io
import json
from pathlib import Path
from urllib.error import HTTPError,URLError
from types import SimpleNamespace
import pytest

from devtool.actions_dispatch import _input_values, _parse_workflow_yaml, _ref, dispatch_workflow
from devtool.github_actions import GitHubActionsClient,GitHubActionsError,actions_baseline,actions_cli
from devtool.cli import build_parser

YAML = b'''name: Build\non:\n  push: {}\n  workflow_dispatch:\n    inputs:\n      mode:\n        required: true\n        type: choice\n        options: [debug, release]\n      verbose:\n        type: boolean\n        default: false\n'''

class Response:
 def __init__(self,obj,status=200):
  self.status=status;self.headers={};self.stream=io.BytesIO(json.dumps(obj).encode() if obj is not None else b'')
 def read(self,size):return self.stream.read(size)
 def __enter__(self):return self
 def __exit__(self,*args):pass
class Opener:
 def __init__(self,replies):self.replies=list(replies);self.requests=[]
 def open(self,req,timeout):
  self.requests.append(req)
  if not self.replies:raise AssertionError('unplanned API')
  item=self.replies.pop(0)
  if isinstance(item,Exception):raise item
  return item

def prepared(status=200, details=True):
 slug='owner/repo';runid=3434
 reply={'workflow_run_id':runid,'run_url':f'https://api.github.com/repos/{slug}/actions/runs/{runid}',
        'html_url':f'https://github.com/{slug}/actions/runs/{runid}'}
 return Opener([
  Response({'full_name':slug,'id':66,'default_branch':'main'}),
  Response({'id':11,'name':'CI','path':'.github/workflows/ci.yml','state':'active'}),
  Response({'sha':'a'*40}),
  Response({'type':'file','path':'.github/workflows/ci.yml','encoding':'base64','content':base64.b64encode(YAML).decode()}),
  Response(reply if details else None,status=status),
 ])

@pytest.mark.parametrize('bad',['../ref','refs/heads/main','a//b','a/../b','x.lock','', 'a?x=1'])
def test_ref_injection_blocked(bad):
 with pytest.raises(GitHubActionsError):_ref(bad)

def test_workflow_input_schema_required_and_typed():
 assert 'mode' in _parse_workflow_yaml(YAML)
 from devtool.actions_dispatch import _validated_inputs
 for values in ({},{'mode':'x'},{'mode':'release','verbose':'yes'},{'mode':'debug','random':'x'}):
  with pytest.raises(GitHubActionsError):_validated_inputs(_parse_workflow_yaml(YAML),values)
 assert _validated_inputs(_parse_workflow_yaml(YAML),{'mode':'debug'})=={'mode':'debug'}

@pytest.mark.parametrize('doc',[b'on: push\n',b'on: {push: {}}\n',b'[]',b'on: {workflow_dispatch: {inputs: [x]}}'])
def test_yaml_non_dispatch_rejected(doc):
 with pytest.raises(GitHubActionsError):_parse_workflow_yaml(doc)

def test_plan_no_post_no_file(tmp_path):
 opener=prepared()
 output=dispatch_workflow(GitHubActionsClient(opener=opener),'owner/repo','ci.yml','main',{'mode':'debug'},execute=False,state_root=tmp_path)
 assert output['state']=='validated-plan' and output['pre_dispatch_sha']=='a'*40
 assert len(opener.requests)==4 and all(r.get_method()=='GET' for r in opener.requests)
 assert list(tmp_path.glob("*.json"))==[]

def test_dispatch_returns_run_id_and_durable_journal_no_sensitive_value(tmp_path):
 opener=prepared();client=GitHubActionsClient('fake-token',opener=opener)
 result=dispatch_workflow(client,'owner/repo','ci.yml','main',{'mode':'release','verbose':'true'},execute=True,state_root=tmp_path)
 assert result['state']=='accepted-correlated' and result['run_id']==3434
 assert result['execution_complete'] is False
 assert opener.requests[-1].get_method()=='POST'
 body=json.loads(opener.requests[-1].data)
 assert body=={'ref':'main','inputs':{'mode':'release','verbose':'true'},'return_run_details':True}
 records=list(tmp_path.glob('*.json'))
 assert len(records)==1
 raw=records[0].read_text()
 assert 'fake-token' not in raw and '"mode": "release"' not in raw
 assert json.loads(raw)['run_id']==3434
 assert records[0].stat().st_mode & 0o777==0o600

def test_204_is_accepted_but_never_assigned_guess_run(tmp_path):
 opener=prepared(204,False)
 result=dispatch_workflow(GitHubActionsClient('test',opener=opener),'owner/repo','ci.yml','main',{'mode':'debug'},execute=True,state_root=tmp_path)
 assert result['state']=='accepted-uncorrelated' and result['run_id'] is None

def test_reject_mismatched_or_redirected_run_url(tmp_path):
 op=prepared();op.replies[-1]=Response({'workflow_run_id':9,'run_url':'https://evil.io/9','html_url':'https://github.com/owner/repo/actions/runs/9'})
 with pytest.raises(GitHubActionsError,match='uncertain'):
  dispatch_workflow(GitHubActionsClient('token',opener=op),'owner/repo','ci.yml','main',{'mode':'debug'},execute=True,state_root=tmp_path)
 assert json.loads(next(tmp_path.glob('*.json')).read_text())['state']=='dispatch-outcome-unknown'

def test_transport_uncertainty_is_preserved_without_retry(tmp_path):
 op=prepared();op.replies[-1]=URLError('connection dropped')
 with pytest.raises(GitHubActionsError,match='uncertain'):
  dispatch_workflow(GitHubActionsClient('token',opener=op),'owner/repo','ci.yml','main',{'mode':'debug'},execute=True,state_root=tmp_path)
 assert len(op.requests)==5
 assert json.loads(next(tmp_path.glob('*.json')).read_text())['state']=='dispatch-outcome-unknown'

def test_no_yes_no_dispatch_even_with_auth(monkeypatch):
 called=[]
 monkeypatch.setattr('devtool.github_actions.GitHubActionsClient',lambda *a,**k:called.append(True))
 args=SimpleNamespace(actions_command='run',github_repo='owner/repo',auth='none',yes=False)
 with pytest.raises(GitHubActionsError,match='--yes'):
  actions_cli(args,Path.cwd())
 assert called  # client constructed, no dispatch

def test_parser_and_action_baseline():
 p=build_parser()
 r=p.parse_args(['actions','run','ci.yml','--github-repo','owner/repo','--ref','main','--input','mode=debug','--yes'])
 assert r.command=='actions' and r.actions_command=='run' and r.yes
 assert 'run' in actions_baseline()['operations']
 assert actions_baseline()['remote_execution'] is False
