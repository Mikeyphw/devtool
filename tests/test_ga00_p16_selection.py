"""P16 phase and actual App-issuer integration, simulated HTTP only."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import pytest

from devtool import ga00_p16_selection as p16
from devtool.github_app import AppConfig, AppInstallationProvider, GitHubAppError


@pytest.fixture(scope='module')
def pem():
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    return rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


class Transport:
    def __init__(self, pins):
        self.pins = pins
        self.selection = 'selected'
        self.removed = False
        self.error = None
        self.control_error = None
        self.wrong_id = False
        self.excess = False
        self.calls = []
        self.tokens = {}
        self.owner_id = pins.owner_id
        self.repo_id = pins.sandbox_id
        self.installation_app = pins.app_id
        self.remote_source = pins.source_sha

    def owner_repo(self, slug):
        p = self.pins
        return {'id':p.repository_id if slug==p.repository else self.repo_id,
                'owner':{'id':self.owner_id,'login':'Owner','type':'User'}, 'full_name':slug,
                'private':False,'visibility':'public','fork':False,'archived':False,
                'default_branch':'main','permissions':{'admin':True}}

    def owner_get(self, path):
        if path=='user':return {'id':self.owner_id,'login':'Owner','type':'User'}
        if path.endswith('/branches/main'):return {'commit':{'sha':self.remote_source}}
        assert path in ('repos/'+self.pins.repository,'repos/'+self.pins.sandbox)
        return self.owner_repo(path[6:])

    def request(self, method, path, credential, payload=None):
        p=self.pins
        self.calls.append((method,path))
        installation={'id':p.installation_id,'app_id':self.installation_app,
                      'account':{'id':p.owner_id,'login':'Owner','type':'User'},
                      'repository_selection':self.selection,'suspended_at':None,
                      'permissions':{'contents':'write'}}
        if path=='/app/installations/'+str(p.installation_id):return installation
        if path=='/repos/'+p.repository+'/installation':
            if self.control_error:raise GitHubAppError(self.control_error)
            return installation
        if path=='/repos/'+p.sandbox+'/installation':
            if self.error:raise GitHubAppError(self.error)
            if self.removed:raise GitHubAppError(p16._UNAVAILABLE)
            return installation
        if path.endswith('/access_tokens'):
            assert method=='POST' and payload['permissions']=={'contents':'read'}
            slug='Owner/'+payload['repositories'][0]
            token='ghs_test_credential_'+str(len(self.tokens)+1)
            self.tokens[token]=slug
            return {'token':token,'expires_at':datetime.fromtimestamp(time.time()+3500,timezone.utc).isoformat(),
                    'permissions':{'contents':'write' if self.excess else 'read','metadata':'read'},
                    'repositories':[{'id':p.repository_id if slug==p.repository else p.sandbox_id,'full_name':slug}]}
        if path=='/installation/repositories':
            slug=self.tokens[credential]
            rid=p.repository_id if slug==p.repository else p.sandbox_id
            return {'total_count':1,'repositories':[{'id':rid+1 if self.wrong_id else rid,'full_name':slug}]}
        if path=='/repos/'+self.tokens[credential]:return self.owner_repo(self.tokens[credential])
        raise AssertionError((method,path))


@pytest.fixture
def fixture(tmp_path, monkeypatch, pem):
    root=tmp_path/'repo';root.mkdir()
    def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
    git('init','-q','-b','main');git('config','user.name','P16 fixture');git('config','user.email','fixture@example.invalid')
    (root/'tracked.txt').write_text('fixture\n');git('add','.');git('commit','-qm','fixture');git('remote','add','origin','https://github.com/Owner/project.git')
    pins=p16.SelectionPins('Owner/project',101,'Owner/sandbox',102,11,12,13,git('rev-parse','HEAD'),git('rev-parse','HEAD^{tree}'))
    key=tmp_path/'key.pem';key.write_bytes(pem);key.chmod(0o600)
    transport=Transport(pins)
    def factory():return AppInstallationProvider(AppConfig(12,13,'Owner',key),http=transport)
    monkeypatch.setattr(AppInstallationProvider,'from_environment',lambda:factory())
    monkeypatch.setattr(p16,'_object',transport.owner_get)
    return root,pins,transport,tmp_path/'state'


def test_full_sequence_real_native_issuer_and_durable_readback(fixture):
    root,pins,t,state=fixture
    before=p16.campaign(root,pins,'baseline',state=state)
    assert before['selection_revocation_qualified'] is False and len(t.tokens)==4
    t.removed=True
    denied=p16.campaign(root,pins,'denied',state=state)
    assert len(t.tokens)==6
    assert denied['stages'][1]['observations'][0]['sandbox']['token_mint_attempted'] is False
    t.removed=False
    after=p16.campaign(root,pins,'restored',state=state)
    assert after['selection_revocation_qualified'] is True and len(t.tokens)==10
    assert after['full_p16_qualified'] is False and after['full_w3_qualified'] is False
    assert not any(x['remote_configuration_mutated'] for x in after['stages'])
    path=Path(after['receipt']);assert path.stat().st_mode&0o777==0o600
    assert 'ghs_test_credential' not in path.read_text() and 'PRIVATE KEY' not in path.read_text()
    n=len(t.calls);assert p16.campaign(root,pins,'status',state=state)['live_observation'] is False
    assert len(t.calls)==n
    with pytest.raises(p16.SelectionRefusal,match='EXACT_NEXT_PHASE'):
        p16.campaign(root,pins,'restored',state=state)
    assert len(t.calls)==n


@pytest.mark.parametrize('kind', ['all','owner','sandbox-id','app','source','dirty','scope','excess','control-denied'])
def test_baseline_negative_no_qualification(fixture,kind):
    root,pins,t,state=fixture
    if kind=='all':t.selection='all'
    elif kind=='owner':t.owner_id+=1
    elif kind=='sandbox-id':t.repo_id+=1
    elif kind=='app':t.installation_app+=1
    elif kind=='source':t.remote_source='a'*40
    elif kind=='dirty':(root/'tracked.txt').write_text('drift')
    elif kind=='scope':t.wrong_id=True
    elif kind=='excess':t.excess=True
    else:t.control_error=p16._UNAVAILABLE
    with pytest.raises((p16.SelectionRefusal,GitHubAppError)):
        p16.campaign(root,pins,'baseline',state=state)
    assert not list(state.glob('*.json'))


@pytest.mark.parametrize('code',[None,'APP_AUTHENTICATION_FAILED','APP_PERMISSION_DENIED_OR_RATE_LIMIT','APP_PROVIDER_UNAVAILABLE'])
def test_denial_cannot_be_inferred_from_success_or_wrong_error(fixture,code):
    root,pins,t,state=fixture
    before=p16.campaign(root,pins,'baseline',state=state)
    raw=Path(before['receipt']).read_bytes();t.error=code
    with pytest.raises(p16.SelectionRefusal):p16.campaign(root,pins,'denied',state=state)
    assert Path(before['receipt']).read_bytes()==raw


def test_skip_phase_tamper_and_wrong_source_refused(fixture):
    root,pins,t,state=fixture
    with pytest.raises(p16.SelectionRefusal,match='EXACT_NEXT_PHASE'):p16.campaign(root,pins,'denied',state=state)
    assert not t.calls
    a=p16.campaign(root,pins,'baseline',state=state);path=Path(a['receipt']);doc=json.loads(path.read_text());doc['selection_revocation_qualified']=True;path.write_text(json.dumps(doc))
    with pytest.raises(p16.SelectionRefusal,match='INTEGRITY'):p16.campaign(root,pins,'status',state=state)


def test_wrong_sandbox_cannot_alias_production(fixture):
    _,pins,_,_=fixture
    with pytest.raises(p16.SelectionRefusal,match='DISTINCT'):
        p16.SelectionPins(**{**asdict(pins),'sandbox_id':pins.repository_id})
