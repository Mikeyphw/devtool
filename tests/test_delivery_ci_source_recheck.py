"""Live source-binding regression for legacy S2 CI observation."""
from __future__ import annotations
import subprocess
import pytest
from devtool.delivery_session import DeliverySessionError, start, record_pr_observation
from devtool import delivery_github as delivery


def git(where, *args):
    out = subprocess.run(['git','-C',str(where),*args], capture_output=True,text=True,check=True)
    return out.stdout.strip()


@pytest.fixture
def sess(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    git(repo,'init','-b','main')
    git(repo,'config','user.name','Test')
    git(repo,'config','user.email','test@example.invalid')
    (repo/'a').write_text('a')
    git(repo,'add','a');git(repo,'commit','-m','fixture')
    git(repo,'switch','-c','feature/a')
    git(repo,'remote','add','origin','git@github.com:Mikeyphw/devtool.git')
    root=tmp_path/'sessions'
    item=start(repo,state_root=root)
    return item,root


def pr_info(item):
    return {'number':18,'state':'open',
            'head':{'sha':item['head_sha'],'ref':item['head'],
                    'repo':{'full_name':item['repository']}},
            'base':{'ref':item['base'],'repo':{'full_name':item['repository']}},
            'merge_commit_sha':'c'*40}


def run(item, sha=None, event='push'):
    return {'id':22,'event':event,'head_branch':item['head'],
            'head_sha':sha or item['head_sha'],'name':'Unit CI','workflow_id':204,
            'status':'completed','conclusion':'success','run_attempt':1,
            'repository':{'full_name':item['repository']}}


class Client:
    def __init__(self,item):
        self.item=item;self.remote_sha=item['head_sha'];self.merge='c'*40
        self.calls=0;self.change=None
        self.rows=[run(item)]
    def _get(self,path):
        if '/branches/' in path:
            return {'commit':{'sha':self.remote_sha}}
        if path.endswith('/pulls/18'):
            return pr_info(self.item)
        if path.endswith('/merge'):
            return {'object':{'sha':self.merge}}
        raise AssertionError(path)
    def runs(self,slug,*,limit):
        self.calls += 1
        if self.change: self.change(self)
        return list(self.rows)


def test_push_only_head_changes_during_run_list_cannot_pass(sess):
    item,root=sess
    c=Client(item)
    c.change=lambda client:setattr(client,'remote_sha','a'*40)
    with pytest.raises(DeliverySessionError,match='Remote branch moved'):
        delivery.observe_ci(item['id'],required=('Unit CI',),client=c,state_root=root)


def test_push_only_head_changes_between_polls_cannot_pass(sess):
    item,root=sess
    c=Client(item)
    def change(client):
        if client.calls==1:
            client.rows=[]
        else:
            client.remote_sha='a'*40
            client.rows=[run(item)]
    c.change=change
    with pytest.raises(DeliverySessionError,match='Remote branch moved'):
        delivery.observe_ci(item['id'],required=('Unit CI',),client=c,state_root=root,
                            checks=2,interval=1,sleep=lambda x:None)


def test_pr_merge_changes_during_run_list_cannot_pass(sess):
    item,root=sess
    record_pr_observation(item['id'],{'number':18,'head':item['head'],
        'base':item['base'],'head_sha':item['head_sha'],
        'url':f"https://github.com/{item['repository']}/pull/18"},state_root=root)
    c=Client(item)
    c.rows=[run(item,sha='c'*40,event='pull_request')]
    c.change=lambda client:setattr(client,'merge','d'*40)
    with pytest.raises(DeliverySessionError,match='merge identity changed'):
        delivery.observe_ci(item['id'],required=('Unit CI',),client=c,state_root=root)
