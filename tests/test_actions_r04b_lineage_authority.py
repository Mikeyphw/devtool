"""R04B: canonical RUN child correlation and explicit GET-only live observation."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool.actions_pipeline import _local_invoke, _execute_node
from devtool.actions_lineage import lineage_snapshot
from devtool.github_actions import GitHubActionsError, actions_cli
from devtool.cli import build_parser
from devtool.run_store import RunCatalog
from devtool.core_runtime import private_run_root
from test_actions_r04_lineage import _fixture, _run, P, Q, SHA, SLUG


def _record_child(root, child_id, *, status='succeeded', exit_code=0):
    catalog=RunCatalog.open(private_run_root(root))
    catalog.add_run({'run_id':child_id, 'command_id':'invoke', 'status':status,
                     'exit_code':exit_code,'finished_at':'2026-10-20T00:00:00Z'})
    catalog.save()


def test_native_child_handoff_is_one_use_and_checked_against_run_v3(tmp_path, monkeypatch):
    import devtool.actions_pipeline as pipeline
    root=tmp_path/'project';root.mkdir()
    calls=[]
    def spawn(cmd, *, cwd, env, **kw):
        assert cmd[:3]==[pipeline.sys.executable, '-m', 'devtool']
        assert env['DEVTOOL_MANAGED_RUN_HANDOFF'].startswith('/')
        path=Path(env['DEVTOOL_MANAGED_RUN_HANDOFF'])
        record=json.loads(path.read_text())
        assert record['parent_pid']==pipeline.os.getpid()
        assert path.stat().st_mode & 0o077 == 0
        calls.append(record['run_id'])
        # stand-in for the real child consuming the handoff
        from devtool.invocation_origin import consume_managed_run_handoff
        # synthetic subprocess cannot satisfy os.getppid(); validate exact secret
        assert len(record['run_id'])==32
        path.unlink()
        _record_child(root,record['run_id'])
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.subprocess,'run',spawn)
    actual=_local_invoke(root,{'action':'validate'})
    assert actual == calls[0]
    assert len(list(tmp_path.rglob('handoff.json')))==0


@pytest.mark.parametrize('status,exit_code', [('failed',1),('running',0),('succeeded',1)])
def test_native_child_missing_or_unfinalized_refused(tmp_path,monkeypatch,status,exit_code):
    import devtool.actions_pipeline as pipeline
    root=tmp_path/'project';root.mkdir()
    def spawn(cmd, *, cwd, env, **kw):
        record=json.loads(Path(env['DEVTOOL_MANAGED_RUN_HANDOFF']).read_text())
        _record_child(root,record['run_id'],status=status,exit_code=exit_code)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pipeline.subprocess,'run',spawn)
    with pytest.raises(GitHubActionsError,match='finalized exact child'):
        _local_invoke(root,{'action':'validate'})


def test_historical_local_node_is_unknown_and_exact_record_is_verified(tmp_path):
    f=_fixture(tmp_path)
    assert _run(f)['rows'][0]['exo_run_id'] is None
    child='e'*32
    raw=f[3];raw['nodes']['local']['exo_run_id']=child
    (f[1]/(P+'.json')).write_text(json.dumps(raw))
    with pytest.raises(GitHubActionsError,match='child RUN identity'):
        _run(f)
    _record_child(f[0],child)
    v=_run(f)
    assert v['rows'][0]['exo_run_id']==child
    assert v['rows'][0]['exo_catalog_verified'] is True
    row=RunCatalog.open(private_run_root(f[0]),create=False)
    desc=dict(row.runs[child]);desc['status']='failed';row.add_run(desc);row.save()
    with pytest.raises(GitHubActionsError,match='finalized successful'):
        _run(f)


class FakeClient:
    def __init__(self, result): self.result=result;self.calls=[]
    def get_run(self,slug,rid):
        self.calls.append((slug,rid));return dict(self.result)


def _remote(*,sha=SHA,attempt=2,workflow=42,event='workflow_dispatch',conclusion='success',status='completed'):
    return {'id':123,'head_sha':sha,'run_attempt':attempt,'workflow_id':workflow,
            'event':event,'status':status,'conclusion':conclusion,'head_branch':'main'}


def test_live_is_explicit_and_never_mutates_or_qualifies(tmp_path):
    f=_fixture(tmp_path)
    client=FakeClient(_remote())
    before=(f[1]/(P+'.json')).read_bytes()
    view=lineage_snapshot(f[0],SLUG,P,pipelines=f[1],dispatches=f[2],live_client=client)
    assert len(client.calls)==1
    assert view['live_observation']['observed_runs']==1
    assert view['rows'][2]['state']=='CI_PASS_REMOTE_OBSERVED'
    assert view['rows'][3]['live_observation']['source_matched'] is True
    assert not view['remote_verified'] and not view['release_qualified']
    assert (f[1]/(P+'.json')).read_bytes()==before
    assert _run(f)['rows'][2]['state']=='CI_PASS_UNVERIFIED'


@pytest.mark.parametrize('change', [{'sha':'d'*40},{'attempt':1},{'workflow':17},{'event':'push'},
                                  {'status':'completed','conclusion':'failure'}])
def test_live_mismatch_cannot_promote(tmp_path,change):
    f=_fixture(tmp_path)
    client=FakeClient(_remote(**change))
    if change.get('conclusion')=='failure':
        v=lineage_snapshot(f[0],SLUG,P,pipelines=f[1],dispatches=f[2],live_client=client)
        assert v['rows'][2]['state']=='CI_FAILED' and not v['release_qualified']
    else:
        with pytest.raises(GitHubActionsError,match='mismatch|differs'):
            lineage_snapshot(f[0],SLUG,P,pipelines=f[1],dispatches=f[2],live_client=client)


def test_live_uncorrelated_does_not_contact_github(tmp_path):
    f=_fixture(tmp_path, correlated=False, states=('pending','uncertain','pending','pending'))
    client=FakeClient(_remote())
    v=lineage_snapshot(f[0],SLUG,P,pipelines=f[1],dispatches=f[2],live_client=client)
    assert client.calls == [] and v['live_observation']['observed_runs']==0


def test_live_parser_and_cli_rejects_missing_token(tmp_path,monkeypatch):
    import devtool.github_actions as gh
    args=build_parser().parse_args(['actions','lineage','--pipeline-id',P,'--github-repo',SLUG,'--live','--auth','none','--json'])
    assert args.live and args.auth == 'none'
    monkeypatch.setattr(gh,'resolve_repository',lambda explicit,root: SLUG)
    monkeypatch.setattr(gh,'resolve_token',lambda mode: (None,'none'))
    with pytest.raises(GitHubActionsError,match='credentialed'):
        actions_cli(args,tmp_path)


def test_result_node_records_child_id_from_verified_invoke(tmp_path,monkeypatch):
    import devtool.actions_pipeline as pipeline
    root=tmp_path/'project';root.mkdir()
    monkeypatch.setattr(pipeline,'_local_invoke',lambda root,node:'a'*32)
    monkeypatch.setattr(pipeline,'_source',lambda root:SHA)
    entry={}
    assert _execute_node(None,SLUG,root,{'kind':'local-invoke','action':'validate'},entry,{},SHA)=='success'
    assert entry['exo_run_id']=='a'*32


def test_live_read_may_observe_unpinned_attempt_without_pinning_archive(tmp_path):
    f=_fixture(tmp_path, observed=False)
    client=FakeClient(_remote(attempt=4))
    v=lineage_snapshot(f[0],SLUG,P,pipelines=f[1],dispatches=f[2],live_client=client)
    assert v['rows'][2]['state']=='CI_PASS_REMOTE_OBSERVED'
    assert v['rows'][2]['live_observation']['run_attempt']==4
    assert v['rows'][2]['live_observation']['attempt_locally_pinned'] is False
    assert v['rows'][3]['state']=='ARCHIVE_NOT_VERIFIED'
    assert v['rows'][3]['artifact']['digest_verified_locally'] is False
    assert len(client.calls)==1
