"""DT-28/29: bounded, source-bound session events and truthful App health."""
import io
from rich.console import Console
from devtool.m6_live import build_live_view


def draw(payload, width=100, focus='overview'):
    dest=io.StringIO()
    screen=Console(file=dest,width=width,color_system=None,force_terminal=False)
    screen.print(build_live_view('ci',payload,width=width,icons='ascii',focus=focus))
    return dest.getvalue()


def test_historical_events_are_labeled_not_live():
    status={'state':'REQUIRED_CHECKS_PASSED','runs':[],
        'events':[{'kind':'session_started','at':'2026-11-24T03:37:00Z','historical':True}]}
    message=draw(status,focus='events')
    assert 'saved: session_started' in message
    assert 'Native required checks passed' not in message or status['state']=='REQUIRED_CHECKS_PASSED'


def test_app_read_health_does_not_claim_write_authorization():
    status={'state':'CHECKS_RUNNING','runs':[],
        'credential_health':{'auth_mode':'app','status':'VALID','identity_verified':True,
           'effective_read_verified':True,'repository_coverage':'selected'}}
    message=draw(status,focus='evidence')
    assert 'M4 effective_read_verified' in message
    assert 'M4 identity_verified' in message
    assert 'authorized_for_operation' in message
    assert 'UNVERIFIED' in message


def test_empty_credential_display_never_presumes_access():
    # Real Rich layout splits into columns at 100 cells. Verify the complete
    # reason on a single-column viewport and the nonauthorization state at
    # both narrow and wide widths where the detail may be ellipsized.
    status={'state':'CHECKS_RUNNING','runs':[]}
    assert 'no M4 provider diagnostic supplied' in draw(status,width=80,focus='evidence')
    for width in (40,50,80,100,120):
        message=draw(status,width=width,focus='evidence')
        assert 'Credential health UNVERIFIED' in message
        assert 'Native required checks passed' not in message


def test_ci_route_refreshes_saved_events_and_keeps_read_health_separate(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from devtool import delivery_cli as cli, delivery_github as github, delivery_ci_policy as policy, m7_credentials
    bound={'id':'a'*32,'repo_root':str(tmp_path),'head_sha':'b'*40,'head':'feature/test',
           'base':'main','base_repository':'Owner/project','head_repository':'Owner/project','repository':'Owner/project'}
    saved=[{'kind':'ci_observed','at':'2026-11-25T00:00:00Z','historical':True,'state':'CHECKS_RUNNING'}]
    reads=[];views=[];probe=[]
    def status(identity, *, inspect_git=True):
        reads.append((identity,inspect_git))
        if inspect_git:return {**bound,'event_stream':{'source_sha':'b'*40,'events':[]}}
        if len(reads)==3:return {**bound,'head_sha':'c'*40,'event_stream':{'source_sha':'c'*40,'events':saved}}
        return {**bound,'event_stream':{'source_sha':'b'*40,'events':list(saved)}}
    class Monitor:
        enabled=True
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def observe(self,v):views.append(v)
        def wait_interval(self,*a):pass
        def review_failed(self):pass
    def observe(identity,**kw):
        for poll in (1,2,3):
            kw['progress']({'poll':poll,'state':'CHECKS_RUNNING','source_sha':'b'*40,'runs':[],'missing':[]})
        return {'state':'CHECKS_RUNNING','checks_qualified':False}
    def health(repo,**kw):
        probe.append((repo,kw))
        return {'installation_id':13,'repository_selection':'selected','status':'VALID','identity_verified':True,'effective_read_verified':True}
    monkeypatch.setattr(cli,'status',status);monkeypatch.setattr(cli,'NativeRichMonitor',Monitor)
    monkeypatch.setattr(github,'observe_ci',observe);monkeypatch.setattr(m7_credentials,'inspect_app',health)
    monkeypatch.setattr(policy,'load_policy',lambda *a,**k:SimpleNamespace(names=('CI',),workflow_ids={'CI':1},sha256='f'*64))
    monkeypatch.setattr(policy,'check_requested',lambda *a:None);monkeypatch.setattr(policy,'assert_observation',lambda *a:None)
    args=SimpleNamespace(delivery_session_command='ci',repo=str(tmp_path),delivery_id='a'*32,auth='app',checks=3,interval=1)
    assert cli.run_delivery_command(args)==2
    assert reads==[('a'*32,True)]+[('a'*32,False)]*3
    assert len(probe)==1 and probe[0][1]=={'probe':True}
    assert views[0]['events']==saved and views[2]['events']==saved
    assert 'events' not in views[1] and views[1]['event_stream_state']=='UNVERIFIED'
    assert views[0]['credential_health']['effective_read_verified'] is True
    assert 'authorized_for_operation' not in views[0]['credential_health']
    assert 'CHECKS_RUNNING' in draw(views[0],width=120,focus='events')
    assert 'Native journal readback unavailable' in draw(views[1],width=100,focus='events')
