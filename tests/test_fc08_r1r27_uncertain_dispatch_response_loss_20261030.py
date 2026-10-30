"""R1R27: controlled fault-injection contracts. NO GitHub network in pytest."""
from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
import yaml
from devtool.github_actions import GitHubActionsError

_SOURCE = Path(__file__).resolve().parents[1] / 'tools' / 'fc08_dispatch_uncertain_outcome.py'
_SPEC = importlib.util.spec_from_file_location('devtool_fc08_r1r27_reviewed', _SOURCE)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)
SHA = '1'*40
IDENT = {'repository':runner._cycle.REPO, 'sha':SHA, 'run_id':12345,
         'run_attempt':1, 'ref':'main', 'correlation':'fc08-12345-1'}

class Response:
    def __init__(self, status, data=b''):
        self.status = status
        self.data = data
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, *_): return self.data

class FakeOpener:
    def __init__(self, first=200):
        self.first = first
        self.requests = []
    def open(self, req, timeout=0):
        self.requests.append(json.loads(req.data))
        status=self.first
        if status=='timeout':
            raise URLError('SECRET-MUST-NEVER-BE-LOGGED')
        if status in (403, 422, 502, 504):
            raise HTTPError(req.full_url,status,'secret error',{},None)
        if status == 204: return Response(204)
        if status == 'malformed': return Response(200,b'{}')
        return Response(200,json.dumps({'workflow_run_id':11,
          'run_url':'https://api.github.com/repos/Mikeyphw/devtool/actions/runs/11',
          'html_url':'https://github.com/Mikeyphw/devtool/actions/runs/11'}).encode())

class OfflineClient(runner.LostResponseClient):
    def __init__(self, first=200):
        super().__init__('offline-test-token')
        self._opener=FakeOpener(first)
        self.state_root=None
        self.mutate_journal_during_get=False
        self.get_calls=0
    def repository(self, slug): return {'full_name':slug,'id':42}
    def workflow(self, slug, workflow):
        assert workflow==runner._cycle.WORKFLOW
        return {'id':91,'state':'active','path':'.github/workflows/'+workflow}
    def _get(self, uri):
        if '/commits/' in uri:return {'sha':SHA}
        doc=(Path(__file__).parents[1]/'.github/workflows'/runner._cycle.WORKFLOW).read_bytes()
        return {'type':'file','path':'.github/workflows/'+runner._cycle.WORKFLOW,
                'encoding':'base64','content':base64.b64encode(doc).decode()}
    def get_run(self, repo, run_id):
        self.get_calls += 1
        if self.mutate_journal_during_get:
            record=next(self.state_root.glob('*.json'))
            data=json.loads(record.read_text())
            data['run_id']=11
            record.write_text(json.dumps(data))
        return {'id':run_id,'repository':{'full_name':repo},'workflow_id':91,
                'event':'workflow_dispatch','head_branch':'main','head_sha':SHA,
                'run_attempt':1,'status':'completed','conclusion':'success'}


def test_real_http_decoder_injected_loss_journal_and_two_safe_resumes(tmp_path):
    client=OfflineClient()
    root=tmp_path/'journal'
    receipt=runner.execute(client,IDENT,root,sleep=lambda _:None,polls=1)
    assert receipt['fault_type']=='INJECTED_RESPONSE_LOSS_AFTER_REAL_ACCEPTED_200'
    assert receipt['real_github_post_count']==1 and receipt['extra_post_count']==0
    assert receipt['resume_attempts']==2 and not receipt['safe_to_redispatch']
    assert receipt['unknown_journal_state']=='dispatch-outcome-unknown'
    assert receipt['unknown_journal_run_id'] is None
    assert receipt['separate_fault_injector_observed_remote']['run_id']==11
    assert receipt['separate_fault_injector_observed_remote']['conclusion']=='success'
    assert client.dispatch_calls==1 and len(client._opener.requests)==1
    assert client.get_calls==1 and not client.post_budget_exceeded
    original=runner._unknown_journal(root,IDENT)
    assert original['run_id'] is None
    assert original['state']=='dispatch-outcome-unknown'
    assert receipt['unknown_journal_request_id']==original['request_id']
    assert all(receipt[k] is False for k in ('real_network_timeout_or_5xx_proven',
        'real_unknown_outcome_reconciled','unknown_outcome_retry_authorized',
        'fc08_overall_qualified','release_qualified','device_qualified','consumer_qualified'))
    req=client._opener.requests[0]
    assert req['ref']=='main' and req['inputs']['scenario']=='success'
    assert req['inputs']['correlation']==IDENT['correlation']


@pytest.mark.parametrize('first',[204,403,422,502,504,'timeout','malformed'])
def test_noncorrelated_or_unverifiable_real_response_never_reposts(tmp_path,first):
    client=OfflineClient(first)
    root=tmp_path/'journal'
    with pytest.raises(RuntimeError,match='REAL_RESPONSE_NOT_OBSERVED|UNKNOWN_OUTCOME_NOT_ATTESTED'):
        runner.execute(client,IDENT,root,sleep=lambda _:None,polls=1)
    assert client.dispatch_calls==1 and len(client._opener.requests)==1
    assert client.observed_accepted_response is None
    assert client.get_calls==0
    assert not client.post_budget_exceeded
    record=next(root.glob('*.json'))
    data=json.loads(record.read_bytes())
    assert data['run_id'] is None
    assert data['state'] in {'dispatch-outcome-unknown','rejected-by-api'}


def test_mutation_during_remote_observation_cannot_promote_unknown(tmp_path):
    client=OfflineClient()
    client.state_root=tmp_path/'journal'
    client.mutate_journal_during_get=True
    with pytest.raises(RuntimeError,match='JOURNAL_UNKNOWN_NOT_DURABLE|JOURNAL_MUTATED_BY_REMOTE_OBSERVATION|RESUME_REPOST_OR_CORRELATED_UNKNOWN'):
        runner.execute(client,IDENT,client.state_root,sleep=lambda _:None,polls=1)
    assert client.dispatch_calls==1 and len(client._opener.requests)==1


@pytest.mark.parametrize('bad',[{'repository':'other/repo'},{'sha':'not-a-sha'},
    {'ref':'wrong'},{'correlation':'wrong'}])
def test_invalid_host_identity_fails_before_post(tmp_path,bad):
    client=OfflineClient()
    with pytest.raises(RuntimeError,match='SOURCE_IDENTITY_INVALID'):
        runner.execute(client,{**IDENT,**bad},tmp_path/'journal')
    assert not client.dispatch_calls


def test_nonempty_or_symlink_journal_fails_before_post(tmp_path):
    client=OfflineClient()
    folder=tmp_path/'journal';folder.mkdir();(folder/'old').write_text('x')
    with pytest.raises(RuntimeError,match='JOURNAL_UNSAFE'):
        runner.execute(client,IDENT,folder)
    link=tmp_path/'symlink';link.symlink_to(folder,target_is_directory=True)
    with pytest.raises(RuntimeError,match='JOURNAL_UNSAFE'):
        runner.execute(client,IDENT,link)
    assert client.dispatch_calls==0


def test_foreign_tools_import_is_ignored(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'tools',SimpleNamespace(fc08_dispatch_cycle='hostile'))
    assert Path(runner._cycle.__file__).resolve()==_SOURCE.with_name('fc08_dispatch_cycle.py')
    assert runner.FAMILY=='devtool.fc08.injected-lost-response-real-post.v1'


def test_workflow_manual_explicit_scoped_single_post():
    root=Path(__file__).resolve().parents[1]
    workflow=(root/'.github/workflows/devtool-fc08-dispatch-unknown-response.yml').read_text()
    probe=(root/'.github/workflows/devtool-fc08-dispatch-probe.yml').read_text()
    main=(root/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    doc=yaml.safe_load(workflow)
    assert set(doc[True])=={'workflow_dispatch'}
    assert doc[True]['workflow_dispatch']['inputs']['authorize_real_dispatch']['default'] is False
    assert 'if: inputs.authorize_real_dispatch == true' in workflow
    assert workflow.count('actions: write')==1
    assert 'permissions:\n  contents: read\n  actions: none' in main
    assert 'actions: write' not in probe
    assert 'if-no-files-found: error' in workflow
    assert 'FC08_DISPATCH_AUTHORIZED:' in workflow
    runner_source=_SOURCE.read_text()
    assert "'real_network_timeout_or_5xx_proven': False" in runner_source
    assert "'release_qualified': False" in runner_source
    assert 'raise GitHubDispatchOutcomeUnknown' in runner_source


def test_failure_receipt_redacts_external_data(tmp_path,monkeypatch,capsys):
    output=tmp_path/'out.json'
    def bad(*args): raise RuntimeError('FC08_SECRET_TOKEN_PATH_DO_NOT_LOG')
    monkeypatch.setattr(runner._cycle,'source_identity',bad)
    monkeypatch.setattr(runner.sys,'argv',['script','--out',str(output)])
    assert runner.main()==2
    assert 'FC08_SECRET_TOKEN_PATH_DO_NOT_LOG' not in output.read_text()
    assert 'FC08_SECRET_TOKEN_PATH_DO_NOT_LOG' not in capsys.readouterr().err
    payload=json.loads(output.read_text())
    assert payload['error_category']=='EXTERNAL_DISPATCH_UNVERIFIED'
    assert payload['release_qualified'] is False


def test_no_external_unreviewed_client(tmp_path):
    with pytest.raises(RuntimeError,match='UNREVIEWED_CLIENT'):
        runner.execute(SimpleNamespace(),IDENT,tmp_path/'journal')
