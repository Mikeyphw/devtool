"""R1R26 offline contracts: real dispatcher and GitHub HTTP decoder; NO live POST."""
from __future__ import annotations
import base64
import importlib.util
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from types import SimpleNamespace

import pytest
import yaml
from devtool.actions_dispatch import dispatch_workflow
from devtool.github_actions import GitHubActionsError, GitHubDispatchOutcomeUnknown

_SOURCE = Path(__file__).resolve().parents[1] / 'tools/fc08_dispatch_rejection_retry.py'
_SPEC = importlib.util.spec_from_file_location('devtool_fc08_r1r26_reviewed', _SOURCE)
assert _SPEC and _SPEC.loader
runner = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner)
SHA = '1' * 40
IDENT = {'repository':runner._cycle.REPO, 'sha':SHA, 'run_id':12345,'run_attempt':1,
         'ref':'main','correlation':'fc08-12345-1'}

class Response:
    status=200
    def __init__(self, data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def read(self,*_):return self.data

class FakeOpener:
    def __init__(self, first=422):
        self.status=first
        self.requests=[]
    def open(self,req,timeout=0):
        data=json.loads(req.data)
        self.requests.append(data)
        if len(self.requests)==1:
            if self.status == 'timeout':
                raise URLError('simulated transport failure with SECRET_DO_NOT_LOG')
            if self.status in (403, 422, 502, 504):
                raise HTTPError(req.full_url,self.status,'no detail',{},None)
            if self.status == 200:
                return Response(json.dumps({'workflow_run_id':999,'run_url':
                    'https://api.github.com/repos/Mikeyphw/devtool/actions/runs/999',
                    'html_url':'https://github.com/Mikeyphw/devtool/actions/runs/999'}).encode())
        return Response(json.dumps({'workflow_run_id':999,
          'run_url':'https://api.github.com/repos/Mikeyphw/devtool/actions/runs/999',
          'html_url':'https://github.com/Mikeyphw/devtool/actions/runs/999'}).encode())

class OfflineClient(runner.RejectOnceClient):
    def __init__(self, first=422):
        super().__init__('test-only-not-a-real-token',source_sha=SHA)
        self._opener=FakeOpener(first)
        self.r1r26_offline_contract_client=True
    def repository(self,slug):return {'full_name':slug,'id':42}
    def workflow(self,slug,workflow_id):
        assert workflow_id==runner._cycle.WORKFLOW
        return {'id':91,'path':'.github/workflows/'+workflow_id,'state':'active'}
    def _get(self,path):
        if '/commits/' in path:return {'sha':SHA}
        doc=(Path(__file__).parents[1]/'.github/workflows'/runner._cycle.WORKFLOW).read_bytes()
        return {'type':'file','path':'.github/workflows/'+runner._cycle.WORKFLOW,
                'encoding':'base64','content':base64.b64encode(doc).decode()}
    def get_run(self,repo,run_id):
        return {'id':run_id,'repository':{'full_name':repo},'workflow_id':91,
            'event':'workflow_dispatch','head_branch':'main','head_sha':SHA,
            'run_attempt':1,'status':'completed','conclusion':'success'}


def test_real_dispatch_decoder_422_then_valid_retry_plus_journal_resume(tmp_path):
    c=OfflineClient()
    data=runner.execute(c,IDENT,tmp_path/'journal',sleep=lambda _:None,polls=1)
    assert c.dispatch_calls==2 and len(c._opener.requests)==2
    assert c._opener.requests[0]['ref']==c.invalid_ref
    assert c._opener.requests[1]['ref']=='main'
    assert c._opener.requests[0]['inputs']==c._opener.requests[1]['inputs']
    assert len(list((tmp_path/'journal').glob('*.json')))==2
    assert data['real_http_422_rejection_proven'] is True
    assert data['resume_without_post_proven'] is True
    assert data['authorized_retry']['remote']['run_id']==999
    assert data['rejection']['run_id'] is None
    assert data['rejection']['request_id'] != data['authorized_retry']['request_id']
    assert all(data[k] is False for k in ('release_qualified','fc08_overall_qualified',
        'real_5xx_or_timeout_proven','unknown_transport_retry_proven'))

@pytest.mark.parametrize('status',[403,502,504,'timeout',200])
def test_non422_cannot_qualify_or_launch_a_second_post(tmp_path,status):
    c=OfflineClient(first=status)
    with pytest.raises((RuntimeError,GitHubActionsError),match='DEFN_REJECTION_UNPROVEN'):
        runner.execute(c,IDENT,tmp_path/'journal',sleep=lambda _:None,polls=1)
    assert c.dispatch_calls==1
    assert len(c._opener.requests)==1
    rows=runner._journal(tmp_path/'journal')
    assert len(rows)==1 and rows[0]['state']=='dispatch-outcome-unknown'
    assert rows[0]['run_id'] is None

@pytest.mark.parametrize('status',[502,504,'timeout'])
def test_real_dispatcher_never_reposts_uncertain_outcome(tmp_path,status):
    c=OfflineClient(first=status)
    root=tmp_path/'journal'
    values={'scenario':'success','correlation':IDENT['correlation']}
    kwargs={'execute':True,'state_root':root,'resume_if_existing':True,
            'expected_source_sha':SHA}
    with pytest.raises(GitHubActionsError,match='outcome uncertain'):
        dispatch_workflow(c,runner._cycle.REPO,runner._cycle.WORKFLOW,'main',values,**kwargs)
    assert c.dispatch_calls==1
    before=runner._journal(root)
    assert len(before)==1 and before[0]['state']=='dispatch-outcome-unknown'
    again=dispatch_workflow(c,runner._cycle.REPO,runner._cycle.WORKFLOW,'main',values,**kwargs)
    assert c.dispatch_calls==1 and again['dispatched'] is False
    assert again['resume_existing'] is True and again['run_id'] is None
    assert again['request_id']==before[0]['request_id']
    assert again['safe_to_redispatch'] is False

@pytest.mark.parametrize('bad',[{'repository':'another/repo'}, {'ref':'not-main'},
 {'sha':'invalid'}, {'correlation':'not-correlated'}])
def test_incorrect_identity_denied_before_any_post(tmp_path,bad):
    c=OfflineClient();identity={**IDENT, **bad}
    with pytest.raises(RuntimeError,match='SOURCE_IDENTITY_INVALID'):
        runner.execute(c,identity,tmp_path/'journal')
    assert c.dispatch_calls==0


def test_nonempty_or_symlink_state_never_used(tmp_path):
    c=OfflineClient();root=tmp_path/'journal';root.mkdir();(root/'old.json').write_text('{}')
    with pytest.raises(RuntimeError,match='NONEMPTY'):
        runner.execute(c,IDENT,root)
    assert c.dispatch_calls==0
    linked=tmp_path/'linked';linked.symlink_to(root,target_is_directory=True)
    with pytest.raises(RuntimeError,match='UNSAFE'):
        runner.execute(c,IDENT,linked)


def test_untrusted_tools_package_cannot_replace_reviewed_source(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'tools',SimpleNamespace(fc08_dispatch_cycle='hostile'))
    assert runner._cycle.__file__==str(_SOURCE.with_name('fc08_dispatch_cycle.py'))
    assert runner.FAMILY=='devtool.fc08.real-api-rejection-retry.v1'


def test_new_workflow_is_manual_write_scoped_and_old_one_readonly():
    root=Path(__file__).resolve().parents[1]
    new=(root/'.github/workflows/devtool-fc08-dispatch-rejection-retry.yml').read_text()
    old=(root/'.github/workflows/devtool-fc08-host-workloads.yml').read_text()
    probe=(root/'.github/workflows/devtool-fc08-dispatch-probe.yml').read_text()
    doc=yaml.safe_load(new)
    assert set(doc[True])=={'workflow_dispatch'}
    assert doc[True]['workflow_dispatch']['inputs']['authorize_real_dispatch']['default'] is False
    assert new.count('actions: write')==1
    assert 'if: inputs.authorize_real_dispatch == true' in new
    assert 'permissions:\n  contents: read\n  actions: none' in old
    assert 'actions: write' not in probe
    assert 'actions: write' not in (root/'.github/workflows/devtool-fc08-dispatch-probe.yml').read_text()
    assert 'exit 37' in probe


def test_no_real_5xx_qualification_or_release_markers():
    src=_SOURCE.read_text()
    assert "'real_5xx_or_timeout_proven': False" in src
    assert "'unknown_transport_retry_proven': False" in src
    assert "'release_qualified': False" in src
    assert 'fc08-r1r26-intentionally-missing-' in src
    assert "'scenario': 'success'" in src


def test_output_failure_receipt_redacts_external_errors(tmp_path, monkeypatch, capsys):
    p=tmp_path/'receipt.json'
    monkeypatch.setattr(runner._cycle,'source_identity',lambda env,root: (_ for _ in ()).throw(RuntimeError('leak SECRETTOKENPATH')))
    monkeypatch.setattr(runner.sys,'argv',['script','--out',str(p)])
    assert runner.main()==2
    assert 'SECRETTOKENPATH' not in p.read_text()
    assert 'SECRETTOKENPATH' not in capsys.readouterr().err
    assert json.loads(p.read_text())['release_qualified'] is False


def test_forged_fc08_prefix_cannot_exfiltrate_error_message(tmp_path,monkeypatch,capsys):
    out=tmp_path/'receipt.json'
    monkeypatch.setattr(runner._cycle,'source_identity',
        lambda env,root: (_ for _ in ()).throw(RuntimeError('FC08_SECRET_DO_NOT_ECHO')))
    monkeypatch.setattr(runner.sys,'argv',['script','--out',str(out)])
    assert runner.main()==2
    assert 'FC08_SECRET_DO_NOT_ECHO' not in out.read_text()
    assert 'FC08_SECRET_DO_NOT_ECHO' not in capsys.readouterr().err
    assert json.loads(out.read_text())['error_category']=='EXTERNAL_DISPATCH_UNVERIFIED'
