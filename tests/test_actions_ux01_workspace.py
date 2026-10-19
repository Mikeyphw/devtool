"""Actions UX01 local operator workspace: no mutation, false success, or guessed ID."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from devtool.actions_workspace import local_recent, local_detail, screen_lines, create_actions_app
from devtool.github_actions import GitHubActionsError, actions_cli

SLUG='Mikeyphw/devtool'
SHA='b'*40
DID='a'*32
PID='c'*32

def write(folder, identity, value):
    folder.mkdir(exist_ok=True)
    (folder/(identity+'.json')).write_text(json.dumps(value))

def prepare(tmp_path):
    d=tmp_path/'dispatches';p=tmp_path/'pipelines';root=tmp_path/'repository';root.mkdir()
    write(d,DID,{'schema':1,'request_id':DID,'repository':SLUG,'created_at':'2026-10-19T01:00:00+00:00',
        'pre_dispatch_sha':SHA,'state':'accepted-uncorrelated','run_id':None,'workflow_path':'.github/workflows/ci.yml'})
    write(p,PID,{'schema':1,'id':PID,'repository':SLUG,'repo_root':str(root.resolve()),'head_sha':SHA,
        'name':'ci_pipeline','manifest_sha256':'1'*64,'created_at':'2026-10-19T01:01:00+00:00',
        'node_order':['source','result'],'nodes':{'source':{'kind':'local-git','state':'success','detail':''},
                 'result':{'kind':'github-result','state':'waiting','detail':''}}})
    return root,d,p

def test_local_discovery_read_only_and_no_false_promotion(tmp_path):
    root,d,p=prepare(tmp_path)
    report=local_recent(root,SLUG,dispatches=d,pipelines=p)
    assert report['read_only'] and not report['qualified_release'] and not report['source_attested']
    assert [x['kind'] for x in report['items']]==['pipeline','dispatch']
    assert report['items'][0]['state']=='waiting'
    assert report['items'][1]['run_id'] is None
    assert not any(x['remote_verified'] for x in report['items'])
    assert 'do not redispatch' in screen_lines(report,compact=True,selected=1)['detail']
    assert local_detail(root,SLUG,'dispatch',DID,dispatches=d)['item']['id']==DID
    assert local_detail(root,SLUG,'pipeline',PID,pipelines=p)['item']['state']=='waiting'

@pytest.mark.parametrize('bad', ['wrong-repository','symlink','bad-json','unknown-state','missing-sha'])
def test_corruption_fails_closed(tmp_path,bad):
    root,d,p=prepare(tmp_path)
    path=d/(DID+'.json')
    raw=json.loads(path.read_text())
    if bad=='wrong-repository':
        raw['repository']='Other/repo';path.write_text(json.dumps(raw))
        with pytest.raises(GitHubActionsError):local_detail(root,SLUG,'dispatch',DID,dispatches=d)
        return
    if bad=='symlink':
        path.unlink();path.symlink_to('/etc/passwd')
    elif bad=='bad-json':path.write_text('{garbled')
    else:
        raw['state']='passed' if bad=='unknown-state' else raw['state']
        if bad=='missing-sha':raw.pop('pre_dispatch_sha')
        path.write_text(json.dumps(raw))
    with pytest.raises(GitHubActionsError):local_recent(root,SLUG,dispatches=d,pipelines=p)

def test_other_repository_and_worktree_excluded(tmp_path):
    root,d,p=prepare(tmp_path)
    row=local_recent(root,SLUG,dispatches=d,pipelines=p)
    assert row['total_matching']==2
    another=tmp_path/'another';another.mkdir()
    assert [x['kind'] for x in local_recent(another,SLUG,dispatches=d,pipelines=p)['items']]==['dispatch']
    assert local_recent(root,'Elsewhere/project',dispatches=d,pipelines=p)['items']==[]

def test_reject_exact_identity_mismatch_and_bounded_scan(tmp_path):
    root,d,p=prepare(tmp_path)
    with pytest.raises(GitHubActionsError):local_detail(root,SLUG,'dispatch','not-exact',dispatches=d)
    with pytest.raises(GitHubActionsError):local_recent(root,SLUG,limit=51,dispatches=d,pipelines=p)
    (p/'1'*32 if False else p/('invalid.json')).write_text('{}')
    with pytest.raises(GitHubActionsError):local_recent(root,SLUG,dispatches=d,pipelines=p)

def test_plain_projector_strips_escapes_and_never_promotes():
    snap={'repository':SLUG,'items':[{'id':DID,'kind':'dispatch','name':'\x1b[31m[bold]danger',
          'state':'dispatch-outcome-unknown','source_sha':SHA,'run_id':None,
          'advice':'Do not retry'}]}
    content=screen_lines(snap,compact=True)
    assert '\x1b' not in str(content)
    assert 'NOT RELEASE QUALIFIED' in content['footer']
    assert DID in content['detail']

def test_textual_44x18_selection_refresh_and_failure_retains_trusted(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import Static
    root,d,p=prepare(tmp_path)
    snapshot=local_recent(root,SLUG,dispatches=d,pipelines=p)
    flag={'bad':False}
    def loader():
        if flag['bad']:raise ValueError('Unavailable')
        return local_recent(root,SLUG,dispatches=d,pipelines=p)
    app=create_actions_app(root,SLUG,load=loader,initial=snapshot)
    async def run():
        async with app.run_test(size=(44,18)) as pilot:
            await pilot.pause()
            assert len(str(app.query_one('#rows',Static).render()).splitlines())==2
            assert PID in str(app.query_one('#selected',Static).render())
            await pilot.press('j');await pilot.pause()
            assert DID in str(app.query_one('#selected',Static).render())
            await pilot.press('r');await pilot.pause()
            assert DID in str(app.query_one('#selected',Static).render())
            flag['bad']=True
            await pilot.press('r');await pilot.pause()
            assert DID in str(app.query_one('#selected',Static).render())
            assert 'Refresh rejected' in str(app.query_one('#note',Static).render())
    asyncio.run(run())

def test_cli_schema_and_json_provider_is_local(monkeypatch,tmp_path):
    from devtool.cli import build_parser
    parser=build_parser()
    assert parser.parse_args(['actions','ui']).actions_command=='ui'
    assert parser.parse_args(['actions','recent','--limit','9','--json']).limit==9
    a=parser.parse_args(['actions','detail','--kind','pipeline','--identity',PID,'--json'])
    assert a.identity==PID
    monkeypatch.setattr('devtool.actions_workspace.local_recent',lambda *_,**__: {'phase':'ACTIONS-UX01','repository':SLUG,'items':[],'read_only':True})
    monkeypatch.setattr('devtool.github_actions.resolve_repository',lambda *_,**__: SLUG)
    # No resolve_token or HTTP client are consulted for offline actions recent.
    monkeypatch.setattr('devtool.github_actions.resolve_token',lambda *_,**__: (_ for _ in ()).throw(AssertionError('network/auth')))
    assert actions_cli(SimpleNamespace(actions_command='recent',github_repo=''),tmp_path)['read_only']
