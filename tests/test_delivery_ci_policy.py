"""S3: committed repository policy, exact workflow IDs, event and source binding."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.delivery_ci_policy import (
    DeliveryCIPolicyError, _validate, load_policy, check_requested,
    assert_observation, POLICY_PATH,
)
from devtool.delivery_session import start, record_pr_observation, status
from devtool import delivery_github, delivery_cli


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout.strip()


def base_policy():
    return {'schema_version': 1, 'repository': 'Mikeyphw/devtool',
            'base_branch': 'main', 'event': 'pull_request',
            'release_qualification': 'separate',
            'required_workflows': [{'name': 'Workflow A', 'workflow_id': 101},
                                   {'name': 'Workflow B', 'workflow_id': 102}]}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Test')
    git(root, 'config', 'user.email', 'test@example.invalid')
    p = root / POLICY_PATH
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(base_policy(), indent=2) + '\n')
    (root / 'README.md').write_text('fixture\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'policy')
    git(root, 'switch', '-c', 'feature/test')
    git(root, 'remote', 'add', 'origin', 'git@github.com:Mikeyphw/devtool.git')
    return root


def policy(repo):
    return load_policy(repo, expected_repository='Mikeyphw/devtool', expected_base='main')


def test_load_committed_policy(repo):
    p = policy(repo)
    assert p.names == ('Workflow A', 'Workflow B')
    assert p.workflow_ids == {'Workflow A': 101, 'Workflow B': 102}
    assert p.sha256 == hashlib.sha256((repo / POLICY_PATH).read_bytes()).hexdigest()
    assert p.receipt()['release_qualified'] is False
    assert p.receipt()['policy_sha256'] == p.sha256


def test_missing_or_untracked_policy_fails(repo):
    path = repo / POLICY_PATH
    path.unlink()
    with pytest.raises(DeliveryCIPolicyError, match='missing'):
        policy(repo)
    path.write_text(json.dumps(base_policy()))
    with pytest.raises(DeliveryCIPolicyError, match='differs'):
        policy(repo)
    git(repo, 'rm', '-f', POLICY_PATH)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(base_policy()))
    with pytest.raises(DeliveryCIPolicyError, match='staged changes'):
        policy(repo)
    git(repo, 'commit', '-m', 'remove tracked policy')
    with pytest.raises(DeliveryCIPolicyError, match='not tracked'):
        policy(repo)


def test_symlink_and_dirty_policy_fail(repo):
    path = repo / POLICY_PATH
    target = repo / 'external.json'
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(DeliveryCIPolicyError, match='regular file'):
        policy(repo)
    path.unlink()
    path.write_bytes(target.read_bytes() + b' ')
    with pytest.raises(DeliveryCIPolicyError, match='differs'):
        policy(repo)


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(schema_version=True),
    lambda p: p.update(release_qualification='ci'),
    lambda p: p.update(repository='SomeoneElse/devtool'),
    lambda p: p.update(event='push'),
    lambda p: p.update(required_workflows=[]),
    lambda p: p['required_workflows'].append(deepcopy(p['required_workflows'][0])),
    lambda p: p['required_workflows'][1].update(workflow_id=101),
    lambda p: p['required_workflows'][1].update(workflow_id=True),
    lambda p: p.update(extra='not supported'),
    lambda p: p['required_workflows'][0].update(name='\nInjected'),
])
def test_shape_refusals(mutation):
    value = base_policy()
    mutation(value)
    with pytest.raises(DeliveryCIPolicyError):
        _validate(value, expected_repository='Mikeyphw/devtool',
                  expected_base='main', digest='a'*64)


def test_manual_list_cannot_weaken_policy(repo):
    p = policy(repo)
    check_requested((), p)
    check_requested(('Workflow B', 'Workflow A'), p)
    with pytest.raises(DeliveryCIPolicyError):
        check_requested(('Workflow A',), p)
    with pytest.raises(DeliveryCIPolicyError):
        check_requested(('Workflow A', 'Workflow A'), p)


def observation(repo, *, ids=(101, 102), state='REQUIRED_CHECKS_PASSED'):
    p = policy(repo)
    return {'state': state, 'policy_sha256': p.sha256,
            'required': list(p.names), 'release_qualified': False,
            'runs': [{'name': name, 'workflow_id': oid,
                      'status': 'completed', 'conclusion': 'success'}
                     for name, oid in zip(p.names, ids)]}


def test_policy_receipt_rejects_wrong_id_and_release(repo):
    p = policy(repo)
    value = observation(repo)
    assert_observation(value, p)
    value['runs'][0]['workflow_id'] = 500
    with pytest.raises(DeliveryCIPolicyError, match='mismatched'):
        assert_observation(value, p)
    value = observation(repo)
    value['release_qualified'] = True
    with pytest.raises(DeliveryCIPolicyError, match='release'):
        assert_observation(value, p)
    value = observation(repo)
    value['policy_sha256'] = 'f'*64
    with pytest.raises(DeliveryCIPolicyError, match='digest'):
        assert_observation(value, p)


class Client:
    def __init__(self, item, runs):
        self.item = item
        self.data = list(runs)
        self.calls = 0
    def _get(self, path):
        if path.endswith('/pulls/17'):
            return {'number':17,'state':'open',
                    'head':{'sha':self.item['head_sha'],'ref':self.item['head'],
                            'repo':{'full_name':self.item['repository']}},
                    'base':{'ref':self.item['base'],
                            'repo':{'full_name':self.item['repository']}}}
        if path.endswith('/merge'):
            return {'object': {'sha':'b'*40}}
        return {'commit': {'sha': self.item['head_sha']}}
    def runs(self, slug, *, limit):
        assert slug == self.item['repository'] and limit == 100
        self.calls += 1
        return list(self.data)


def run(item, name, workflow_id, *, event='pull_request'):
    return {'id': 10 + workflow_id, 'workflow_id': workflow_id,
            'name': name, 'event': event,
            'head_sha': item['head_sha'], 'head_branch': item['head'],
            'status': 'completed', 'conclusion':'success',
            'run_attempt':1,
            'pull_requests':[{'number':17,'head':{'sha':item['head_sha']}}]}


def session_with_pr(repo, tmp_path):
    folder = tmp_path / 'sessions'
    item = start(repo, state_root=folder)
    record_pr_observation(item['id'],
                          {'number':17, 'head':item['head'], 'base':item['base'],
                           'head_sha':item['head_sha'],
                           'url':'https://github.com/Mikeyphw/devtool/pull/17',
                           'merge_commit_sha':'b'*40},state_root=folder)
    return item, folder


def observe(repo, tmp_path, runs, **kwargs):
    item, folder = session_with_pr(repo, tmp_path)
    c = Client(item, runs(item))
    p = policy(repo)
    data = delivery_github.observe_ci(
        item['id'], required=p.names, workflow_ids=p.workflow_ids,
        policy_sha256=p.sha256, required_event='pull_request',
        policy_guard=lambda: policy(repo),
        state_root=folder, client=c, checks=1, **kwargs)
    return data, status(item['id'],state_root=folder)['ci']


def test_all_required_workflow_ids_pass(repo, tmp_path):
    rows = lambda item: [run(item, 'Workflow A', 101),run(item, 'Workflow B', 102)]
    current, durable = observe(repo, tmp_path, rows)
    assert current['state'] == 'REQUIRED_CHECKS_PASSED'
    assert current['checks_qualified'] is True
    assert durable['policy_sha256'] == policy(repo).sha256
    assert durable['release_qualified'] is False


def test_same_name_wrong_workflow_id_is_blocked(repo, tmp_path):
    rows = lambda item: [run(item,'Workflow A',999),run(item,'Workflow B',102)]
    current,durable = observe(repo, tmp_path, rows)
    assert current['state'] == 'BLOCKED_AMBIGUOUS'
    assert current['policy_mismatches'] == ['Workflow A']
    assert durable['checks_qualified'] is False


def test_push_event_not_accepted_for_pr_policy(repo, tmp_path):
    rows = lambda item: [run(item,'Workflow A',101,event='push'),run(item,'Workflow B',102)]
    current,_ = observe(repo,tmp_path,rows)
    assert current['state'] == 'WAITING_REGISTRATION'
    assert current['missing'] == ['Workflow A']


def test_policy_guard_rejects_change_before_success(repo, tmp_path):
    item,folder = session_with_pr(repo,tmp_path)
    c = Client(item,[run(item,'Workflow A',101),run(item,'Workflow B',102)])
    called=[]
    def guard():
        called.append('check')
        if len(called) > 2:
            raise DeliveryCIPolicyError('Concurrent policy drift')
    with pytest.raises(DeliveryCIPolicyError, match='drift'):
        delivery_github.observe_ci(item['id'],required=('Workflow A','Workflow B'),
                                   workflow_ids={'Workflow A':101,'Workflow B':102},
                                   policy_sha256='a'*64, required_event='pull_request',
                                   policy_guard=guard,client=c,state_root=folder,checks=1)
    assert status(item['id'],state_root=folder).get('ci') is None


def test_policy_cli_uses_committed_set_automatically(repo, tmp_path, monkeypatch, capsys):
    item,folder = session_with_pr(repo,tmp_path)
    captured={}
    monkeypatch.setattr(delivery_cli,'status',lambda identity: status(identity,state_root=folder))
    monkeypatch.setattr(delivery_github,'observe_ci',lambda identity,**kw:
                         (captured.update(kw) or
                          {'state':'REQUIRED_CHECKS_PASSED','checks_qualified':True,
                           'required':list(policy(repo).names),
                           'policy_sha256':policy(repo).sha256,
                           'runs':[{'name':name, 'workflow_id':oid,'status':'completed',
                                    'conclusion':'success','url':'https://github.com/test'}
                                   for name,oid in policy(repo).workflow_ids.items()],
                           'release_qualified':False,'missing':[]}))
    args=SimpleNamespace(delivery_session_command='ci',repo=str(repo),delivery_id=item['id'],
                         require_workflow=[],checks=1,interval=2,auth='none',json=False,_devtool_json=False)
    assert delivery_cli.run_delivery_command(args)==0
    assert captured['required']==('Workflow A','Workflow B')
    assert captured['workflow_ids']=={'Workflow A':101,'Workflow B':102}
    assert captured['required_event']=='pull_request'
    assert captured['policy_sha256']==policy(repo).sha256
    assert 'REQUIRED_CHECKS_PASSED' in capsys.readouterr().out


def test_policy_cli_rejects_incomplete_manual_override(repo,tmp_path,monkeypatch,capsys):
    item,folder = session_with_pr(repo,tmp_path)
    monkeypatch.setattr(delivery_cli,'status',lambda identity: status(identity,state_root=folder))
    args=SimpleNamespace(delivery_session_command='ci',repo=str(repo),delivery_id=item['id'],
                         require_workflow=['Workflow A'],checks=1,interval=2,
                         auth='none',json=False,_devtool_json=False)
    assert delivery_cli.run_delivery_command(args)==2
    assert 'policy' in capsys.readouterr().err.lower()


def test_existing_remote_tip_requires_proven_fast_forward(repo, tmp_path):
    previous = git(repo, 'rev-parse', 'HEAD')
    (repo/'README.md').write_text('next commit\n')
    git(repo,'add','README.md')
    git(repo,'commit','-m','extend PR branch')
    item = start(repo,state_root=tmp_path/'new-sessions')
    folder=tmp_path/'new-sessions'
    # Read-only stage identifies a valid fast-forward, never pushes.
    result=delivery_github.push(item['id'],state_root=folder,
                                remote_probe=lambda *_: previous)
    assert result['state']=='PUSH_REQUIRES_APPROVAL'
    assert result['mode']=='fast-forward'
    probes=iter((previous,item['head_sha']))
    invoked=[]
    pushed=delivery_github.push(item['id'],yes=True,state_root=folder,
        remote_probe=lambda *_:next(probes),
        git_push=lambda *_: invoked.append('regular-git-push') or 0)
    assert pushed['state']=='PUSH_VERIFIED'
    assert invoked==['regular-git-push']
    assert status(item['id'],state_root=folder)['resumable'] is True


def test_unknown_or_nonancestor_remote_refuses_nonforce_update(repo, tmp_path):
    item=start(repo,state_root=tmp_path/'sessions')
    with pytest.raises(Exception, match='fast-forward ancestor'):
        delivery_github.push(item['id'],yes=True,state_root=tmp_path/'sessions',
                             remote_probe=lambda *_:'b'*40,
                             git_push=lambda *_:pytest.fail('must not push'))
