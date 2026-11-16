"""UX-RICH04 real-producer contract tests; all observation paths are read-only."""
from __future__ import annotations

import io
import threading
from pathlib import Path

import pytest
from rich.console import Console

from devtool import operator_event_feed as feed
from devtool import operator_workspace as ws

HEAD='a'*40
NEW='b'*40


def _seed(tmp_path:Path, monkeypatch, *, run_status='failed', with_validation=True):
    from devtool import core_runtime, run_store, validation_sessions
    root=tmp_path/'repo';root.mkdir(exist_ok=True)
    native=root/'catalog'
    monkeypatch.setattr(core_runtime,'private_run_root',lambda root:native)
    catalog=run_store.RunCatalog.open(native,create=True)
    catalog.add_run({'run_id':'run-a','command_id':'test','status':run_status})
    catalog.coalesce_event(run_id='run-a',kind='process-start',key='first',
        payload={'state':run_status,'phase':'compile','token':'SHOULD-NOT-LEAK',
                 'stdout':'PRIVATE-RAW-OUTPUT','reason_code':'COMPILER_FAILED'})
    catalog.save()
    class Session:
        session_id='sess-a'
    if with_validation:
        monkeypatch.setattr(validation_sessions,'load_active_session',lambda *a,**k:Session())
        monkeypatch.setattr(validation_sessions,'read_events',lambda *a,**k:[
            {'event_id':'val-a','type':'phase-end','timestamp':'2026-11-16T19:30:00Z',
             'session_id':'sess-a','state':'success','revision':3,
             'data':{'phase':'compile','stderr':'NEVER-VISIBLE','state':'success'}}])
    else:
        monkeypatch.setattr(validation_sessions,'load_active_session',lambda *a,**k:None)
    return root,native


def test_native_sources_and_sensitive_fields(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    snapshot=feed.native_events(root)
    assert snapshot['read_only'] is True
    assert snapshot['kind']=='devtool.native-event-feed'
    assert snapshot['count']==2
    assert snapshot['run_count']==1 and snapshot['validation_count']==1
    a,b=snapshot['events']
    assert a['domain']=='runs' and a['timestamp'] is None
    assert a['chronology'].startswith('coalesced')
    assert a['update_count']==1
    assert b['domain']=='validation' and b['timestamp']=='2026-11-16T19:30:00Z'
    assert b['revision']==3
    assert a['detail']['reason_code']=='COMPILER_FAILED'
    assert 'SHOULD-NOT-LEAK' not in str(snapshot)
    assert 'NEVER-VISIBLE' not in str(snapshot)
    assert 'PRIVATE-RAW-OUTPUT' not in str(snapshot)
    assert 'events' not in str(snapshot.get('release_qualified'))
    assert snapshot['release_qualified'] is False


def test_single_event_limit_preserves_no_invented_entries(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    single=feed.native_events(root,limit=1)
    assert single['count']==1
    assert single['events'][0]['domain']=='validation'
    for size in (0,-1,121,1.5,'120',True):
        with pytest.raises(ValueError):feed.native_events(root,limit=size)


def test_empty_read_is_not_false_success_or_file_creation(tmp_path,monkeypatch):
    root=tmp_path/'no-store';root.mkdir()
    from devtool import core_runtime, validation_sessions
    catalog=root/'absent'/'catalog'
    monkeypatch.setattr(core_runtime,'private_run_root',lambda *_:catalog)
    monkeypatch.setattr(validation_sessions,'load_active_session',lambda *a,**k:None)
    snapshot=feed.native_events(root)
    assert not catalog.exists()
    assert snapshot['state']=='no-records' and snapshot['count']==0
    assert snapshot['validation_session']=='no active session'


def test_filtering_producer_status_and_sanitized_search(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    snapshot=feed.native_events(root)
    assert [x['domain'] for x in feed.visible_events(snapshot)]==['runs','validation']
    assert [x['domain'] for x in feed.visible_events(snapshot,domain='runs')]==['runs']
    assert [x['domain'] for x in feed.visible_events(snapshot,domain='validation')]==['validation']
    assert [x['domain'] for x in feed.visible_events(snapshot,state='failure')]==['runs']
    assert [x['domain'] for x in feed.visible_events(snapshot,state='terminal')]==['runs','validation']
    assert feed.visible_events(snapshot,state='active')==[]
    assert [x['event_id'] for x in feed.visible_events(snapshot,query='phase-end')]==['val-a']
    assert feed.visible_events(snapshot,query='SHOULD-NOT-LEAK')==[]
    assert feed.visible_events(snapshot,query='NEVER-VISIBLE')==[]
    for key in ('domain','state'):
        with pytest.raises(ValueError):feed.visible_events(snapshot,**{key:'definitely-not-supported'})


def test_domain_scoped_identity_and_unique_selection(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    a={'domain':'runs','event_id':'same','kind':'compile','status':'failure'}
    b={'domain':'validation','event_id':'same','kind':'finish','status':'success'}
    assert feed.event_key(a)!=feed.event_key(b)
    assert feed.event_key({'event_id':'same'})==''
    obj=ws.Workspace(root,HEAD,tab='events')
    monkeypatch.setattr(ws,'_source_head',lambda _:HEAD)
    obj._accept_snapshot('events',{'events':[a,b]})
    obj.selections['events']=1
    obj._accept_snapshot('events',{'events':[b,a]})
    assert obj.selections['events']==0
    obj.selections['events']=1
    obj._accept_snapshot('events',{'events':[a,a]})
    assert obj.selections['events']==0


def test_filter_controls_and_search_modal_do_not_send_q_to_detach(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    obj=ws.Workspace(root,HEAD,tab='events')
    assert obj.press('f') and obj.event_domain=='validation'
    assert obj.press('s') and obj.event_state=='failure'
    assert obj.press('/') and obj.event_editing
    for letter in 'query':assert obj.press(letter)
    assert obj.event_editing  # q is text while in search mode
    assert obj.press('\x7f') and obj.event_draft=='quer'
    assert obj.press('\r') and obj.event_query=='quer'
    obj.press('/')
    obj.press('x')
    obj.press('\x1b')
    assert obj.event_query=='quer' and not obj.event_editing
    obj.press('c')
    assert (obj.event_domain,obj.event_state,obj.event_query)==('all','all','')
    assert obj.press('5') and obj.tab=='events'
    assert not obj.press('q')


def test_search_input_is_bounded_and_control_chars_ignored(tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    obj=ws.Workspace(repo,HEAD,tab='events')
    obj.press('/')
    for _ in range(200):obj.press('x')
    obj.press('\x00')
    obj.press('\n')
    assert obj.event_query=='x'*64


def test_render_does_not_include_raw_private_payload(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    snapshot=feed.native_events(root)
    output=io.StringIO()
    Console(file=output,force_terminal=False,width=88).print(
        feed.event_view(snapshot,width=88,detail=True))
    text=output.getvalue()
    assert 'COMPILER_FAILED' in text
    assert 'SHOULD-NOT-LEAK' not in text
    assert 'PRIVATE-RAW-OUTPUT' not in text
    assert 'NEVER-VISIBLE' not in text
    assert 'coalesced' in text
    Console(file=io.StringIO(),force_terminal=False,width=46).print(
        feed.event_view(snapshot,width=46,selected=0,detail=True))


def test_async_events_are_single_flight_and_do_not_block_keyboard(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    obj=ws.Workspace(root,HEAD,tab='events')
    monkeypatch.setattr(ws,'_source_head',lambda _:HEAD)
    gate=threading.Event()
    observer=ws.SSHReadObserver(tab='events')
    called=[]
    def read():
        called.append(1)
        gate.wait(2)
        return {'read_only':True,'events':[{'domain':'runs','event_id':'one'}]}
    assert observer.request(read)
    assert observer.request(read) is False
    assert obj.press('f') and obj.press('j') and obj.press('k')
    assert observer.consume(obj) is False
    gate.set()
    import time
    for _ in range(80):
        if observer.consume(obj):break
        time.sleep(.01)
    assert len(called)==1
    assert obj.snapshots['events']['events'][0]['event_id']=='one'


def test_async_events_reject_stale_head_and_refuse_mutable_result(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    obj=ws.Workspace(root,HEAD,tab='events')
    obj.snapshots['events']={'events':[{'domain':'runs','event_id':'old'}]}
    monkeypatch.setattr(ws,'_source_head',lambda _:NEW)
    observer=ws.SSHReadObserver(tab='events')
    assert observer.request(lambda:{'events':[{'domain':'runs','event_id':'new'}]})
    import time
    for _ in range(100):
        if observer.consume(obj):break
        time.sleep(.01)
    assert obj.source_drift
    assert obj.snapshots['events']['events'][0]['event_id']=='old'
    obj2=ws.Workspace(root,HEAD,tab='events')
    monkeypatch.setattr(ws,'_source_head',lambda _:HEAD)
    rejected=ws.SSHReadObserver(tab='events')
    assert rejected.request(lambda:{'read_only':False,'events':[]})
    for _ in range(100):
        if rejected.consume(obj2):break
        time.sleep(.01)
    assert 'refused' in obj2.errors['events']
    assert 'events' not in obj2.snapshots


def test_validation_observation_only_reads_active_session(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch,with_validation=False)
    snapshot=feed.native_events(root)
    assert snapshot['run_count']==1 and snapshot['validation_count']==0
    assert snapshot['events'][0]['timestamp'] is None


def test_event_readers_are_never_ssh_or_github_gets(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    from devtool import github_actions, ssh_history
    monkeypatch.setattr(github_actions,'resolve_token',lambda *a,**k:pytest.fail('network auth'),raising=False)
    monkeypatch.setattr(ssh_history,'sessions_payload',lambda *a,**k:pytest.fail('SSH network'),raising=False)
    assert feed.native_events(root)['count']==2


def test_canonical_record_change_marking_uses_observed_versions_not_guessed_time(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    obj=ws.Workspace(root,HEAD,tab='events')
    a={'domain':'runs','event_id':'run1','status':'running','update_count':1}
    b={'domain':'validation','event_id':'valid1','status':'running','revision':2}
    obj._accept_snapshot('events',{'read_only':True,'events':[a,b]})
    assert obj.event_highlights==set()  # first attach is not new evidence
    obj._accept_snapshot('events',{'read_only':True,'events':[a,b]})
    assert obj.event_highlights==set()
    changed={**a,'status':'failed','update_count':2}
    obj._accept_snapshot('events',{'read_only':True,'events':[changed,b]})
    assert obj.event_highlights=={feed.event_key(a)}
    obj._accept_snapshot('events',{'read_only':True,'events':[changed,b]})
    assert obj.event_highlights==set()
    obj._accept_snapshot('events',{'read_only':True,'events':[changed,b,
        {'domain':'validation','event_id':'valid2','status':'success'}]})
    assert obj.event_highlights=={'validation:valid2'}


def test_malformed_status_is_unknown_and_cannot_disclose_embedded_tokens(tmp_path,monkeypatch):
    root,_=_seed(tmp_path,monkeypatch)
    from devtool import core_runtime,run_store
    catalog=run_store.RunCatalog.open(core_runtime.private_run_root(root),create=False)
    catalog.coalesce_event(run_id='run-a',kind='test',key='malicious',
        payload={'status':'success;TOKEN=LEAK-123','reason_code':'ENFORCED'})
    catalog.save()
    # RunCatalog.save() serializes its event map with sorted JSON keys;
    # producer enumeration is NOT insertion order or a chronological stream.
    # Resolve the injected record by its native identity, never a list index.
    events=feed.native_events(root)['events']
    matches=[event for event in events
             if event['domain']=='runs' and event['run_id']=='run-a'
             and event['kind']=='test']
    assert len(matches)==1
    record=matches[0]
    assert record['status']=='unknown'
    assert next(event for event in events if event['kind']=='process-start')['status']=='failed'
    result=io.StringIO()
    Console(file=result,force_terminal=False,width=100).print(
        feed.event_view({'events':[record],'count':1,'state':'observed'},detail=True))
    assert 'LEAK-123' not in result.getvalue()


def test_ctrl_c_remains_detach_even_during_search(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    obj=ws.Workspace(root,HEAD,tab='events')
    obj.press('/')
    assert obj.press('\x03') is False


def test_native_run_event_order_independent_of_coalesced_hash_sorting(tmp_path,monkeypatch):
    """Canonical RunCatalog JSON sorts event IDs; a keyed status survives reorder."""
    root,_=_seed(tmp_path,monkeypatch)
    from devtool import core_runtime,run_store
    import json
    catalog=run_store.RunCatalog.open(core_runtime.private_run_root(root),create=False)
    catalog.coalesce_event(run_id='run-a',kind='unknown-safety',key='identity',
        payload={'state':'failed;ACCESS_TOKEN=HIDDEN-NO-SHOW','reason_code':'REDACTED'})
    catalog.save()
    path=core_runtime.private_run_root(root)/'catalog.json'
    serialized=json.loads(path.read_text())
    # Match the actual RunCatalog.write ordering even in offline stand-ins.
    path.write_text(json.dumps(serialized,sort_keys=True))
    events=feed.native_events(root)['events']
    indexed=[e for e in events if e['domain']=='runs' and e['kind']=='unknown-safety']
    assert len(indexed)==1
    record=indexed[0]
    assert record['status']=='unknown'
    assert 'HIDDEN-NO-SHOW' not in str(record)
    output=io.StringIO()
    Console(file=output,force_terminal=False,width=100).print(
        feed.event_view({'events':[record],'count':1,'state':'observed'},detail=True))
    assert 'HIDDEN-NO-SHOW' not in output.getvalue()
    assert any(e['kind']=='process-start' and e['status']=='failed' for e in events)
