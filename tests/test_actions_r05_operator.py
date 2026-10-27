"""R05: real Textual operator controls, read-only by default and two-step collection.

Network callbacks are injected; these tests cannot dispatch or download anything.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

import pytest

from devtool.actions_operator import (
    operator_review, operator_collect, operator_lineage, operator_workflows,
    operator_lines, selected_identity,
)
from devtool.actions_workspace import create_actions_app
from devtool.github_actions import GitHubActionsError

SLUG='Mikeyphw/devtool'
RID='a'*32
PID='b'*32
SHA='c'*40

def snapshot(*,correlated=True):
    return {'repository':SLUG,'read_only':True,'items':[
        {'kind':'pipeline','id':PID,'repository':SLUG,'source_sha':SHA,'state':'waiting','name':'CI chain',
         'run_id':None,'advice':'read only','qualified_release':False,'created_at':'2026-10-20T01:00:00Z'},
        {'kind':'dispatch','id':RID,'repository':SLUG,'source_sha':SHA,'state':'correlated', 'name':'Build CI',
         'run_id':12 if correlated else None,'advice':'review separately',
         'qualified_release':False,'created_at':'2026-10-20T01:01:00Z'}]}

def test_operator_guards_refuse_wrong_repo_kind_and_unconfirmed_collection(tmp_path):
    row=snapshot()['items'][1]
    assert selected_identity(row,SLUG,kind='dispatch')==RID
    for change in ({'repository':'Other/repo'}, {'id':'invalid'}, {'kind':'pipeline'}, {'qualified_release':True}):
        with pytest.raises(GitHubActionsError):
            selected_identity(dict(row,**change),SLUG,kind='dispatch')
    with pytest.raises(GitHubActionsError,match='separate interactive confirmation'):
        operator_collect(row,tmp_path,SLUG,'candidate',client=object())
    with pytest.raises(GitHubActionsError):
        operator_collect(row,tmp_path,SLUG,'../bad',confirmed=True,client=object())
    uncorrelated=dict(row,run_id=None)
    assert operator_review(uncorrelated,SLUG)['state']=='uncorrelated'
    with pytest.raises(GitHubActionsError,match='uncorrelated'):
        operator_collect(uncorrelated,tmp_path,SLUG,'candidate',confirmed=True,client=object())


def test_operator_lines_sanitize_untrusted_remote_display():
    review={'kind':'review','request_id':RID,'state':'incomplete', 'run_id':10,
            'run_attempt':1,'remote_verified':False,'message':'\x1b[31mDANGER\x1b[0m'}
    display=operator_lines(review,width=40)
    assert '\x1b' not in display and 'NOT QUALIFIED' in display
    assert 'NO' in display


def test_textual_40_columns_identity_pin_and_explicit_get(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import Static
    calls=[]
    def review(row,repository):
        calls.append(('review',row['kind'],row['id'],repository))
        return {'kind':'review','request_id':RID,'state':'succeeded','run_id':12,
                'run_attempt':2,'remote_verified':True,'qualified_release':False,'message':'test only'}
    def lineage(row,root,repository):
        calls.append(('lineage',row['id'],repository))
        # An explicit view payload, no network or mutation.
        return {'kind':'lineage','pipeline_id':PID,'repository':SLUG,'qualified_release':False,
                'source_attested':False,'nodes':[], 'state':'waiting'}
    def workflows(repository):
        calls.append(('workflows',repository))
        return {'kind':'workflows','repository':SLUG,'items':[], 'qualified_release':False}
    app=create_actions_app(tmp_path,SLUG,initial=snapshot(),
                           review=review,lineage=lineage,workflows=workflows)
    async def check():
        async with app.run_test(size=(40,19)) as pilot:
            await pilot.pause()
            assert calls==[]  # mount cannot make remote requests
            assert PID in str(app.query_one('#selected',Static).render())
            await pilot.press('j');await pilot.pause()
            assert RID in str(app.query_one('#selected',Static).render())
            assert calls==[]
            await pilot.press('v');await pilot.pause(0.2)
            assert calls==[('review','dispatch',RID,SLUG)]
            assert 'REMOTE REVIEW' in str(app.query_one('#selected',Static).render())
            await pilot.press('w');await pilot.pause(0.2)
            assert calls[-1]==('workflows',SLUG)
            await pilot.press('k');await pilot.pause()
            assert PID in str(app.query_one('#selected',Static).render())
            assert 'REMOTE REVIEW' not in str(app.query_one('#selected',Static).render())
    asyncio.run(check())


def test_textual_collection_requires_exact_enter_then_y(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import Input, Static
    invoked=[]
    def collector(row,root,repository,name,*,confirmed):
        invoked.append((row['id'],name,confirmed))
        return {'kind':'collect','archive_verified':True,'sha256':'1'*64,'qualified_release':False}
    app=create_actions_app(tmp_path,SLUG,initial=snapshot(),collect=collector)
    async def check():
        async with app.run_test(size=(40,20)) as pilot:
            await pilot.press('j','c');await pilot.pause()
            name=app.query_one('#artifact',Input)
            assert name.display and name.has_focus
            # Printable 'y' remains part of artifact name; no accidental approval.
            await pilot.press(*list('candidate'));await pilot.pause()
            assert name.value=='candidate' and invoked==[]
            await pilot.press('enter');await pilot.pause()
            assert invoked==[] and not name.has_focus
            assert app._collection_ready=='candidate'
            await pilot.press('y');await pilot.pause(0.2)
            assert invoked==[(RID,'candidate',True)]
            assert 'ARCHIVE HANDOFF' in str(app.query_one('#selected',Static).render())
    asyncio.run(check())


def test_textual_unsubmitted_and_cancelled_collection_never_invokes(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import Input
    invoked=[]
    app=create_actions_app(tmp_path,SLUG,initial=snapshot(),collect=lambda *args,**kw: invoked.append('collect'))
    async def check():
        async with app.run_test(size=(40,20)) as pilot:
            await pilot.press('j','c'); await pilot.pause()
            await pilot.press(*list('candidate'))
            app.action_collect_confirm()  # cannot bypass input submission
            assert invoked==[]
            await pilot.press('escape');await pilot.pause()
            assert not app.query_one('#artifact',Input).display
            assert app._confirmation is None and app._collection_ready is None
            assert invoked==[]
    asyncio.run(check())


def test_textual_stale_remote_completion_not_applied_after_selection(tmp_path):
    pytest.importorskip('textual')
    from textual.widgets import Static
    start=Event();done=Event()
    def review(row,repository):
        start.set()
        done.wait(3)
        return {'kind':'review','state':'succeeded','request_id':RID,'run_id':12,
                'run_attempt':1,'remote_verified':True,'qualified_release':False,'message':'STALE'}
    app=create_actions_app(tmp_path,SLUG,initial=snapshot(),review=review)
    async def check():
        try:
            async with app.run_test(size=(40,19)) as pilot:
                await pilot.press('j','v');await pilot.pause(0.1)
                assert start.is_set()
                await pilot.press('k'); await pilot.pause()
                assert PID in str(app.query_one('#selected',Static).render())
                done.set();await pilot.pause(0.3)
                assert 'STALE' not in str(app.query_one('#selected',Static).render())
        finally:
            done.set()
    asyncio.run(check())
