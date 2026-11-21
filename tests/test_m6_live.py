"""M6 source-only tests: portrait parity, security and non-mutating views."""
from __future__ import annotations

import io
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from rich.console import Console

from devtool.m6_live import build_live_view, OverlayLiveProjection, icon_mode, safe
from devtool.delivery_rich import NativeRichMonitor


def render(kind, payload, width, **kwargs):
    stream=io.StringIO()
    out=Console(file=stream,force_terminal=False,color_system=None,width=width)
    out.print(build_live_view(kind,payload,width=width,icons='ascii',**kwargs))
    return stream.getvalue()


@pytest.mark.parametrize('width',[40,52,60,80,120])
def test_ci_portrait_and_real_failures(width):
    snap={'state':'CHECKS_FAILED','poll':4,'required':['first','second'],
          'missing':['second'],'runs':[{'name':'Repository contract','status':'completed',
              'conclusion':'success','id':42},
              {'name':'R0 / R1 Android API 102','status':'completed',
               'conclusion':'failure','id':44}],
          'diagnosis':{'failures':[{'jobs':[{'name':'JVM tests',
                                             'excerpt':['AssertionError: real failure']}]}]}}
    original=repr(snap)
    output=render('ci',snap,width,detail=True)
    for needle in ('PR CHECKS','FAILED','Repository contract','JVM tests',
                   'Native CI NOT QUALIFIED','MISSING'):
        assert needle in output
    assert repr(snap)==original
    assert all(len(x)<=width for x in output.splitlines())


@pytest.mark.parametrize('width',[40,60,80,120])
def test_actions_run_preserves_attempt_and_real_steps(width):
    snap={'remote':{'id':993,'run_attempt':2,'status':'completed','conclusion':'failure',
                    'head_sha':'f'*40},'source_binding':'matching',
          'jobs':[{'name':'android','status':'completed','conclusion':'failure',
                   'steps':[{'name':'Gradle tests','status':'completed','conclusion':'failure'}]}]}
    out=render('run',snap,width,detail=True,verbose=True)
    assert 'Gradle tests' in out
    assert 'Attempt 2' in out
    assert 'Source ' + 'f'*8 in out  # Full SHA may be truncated at 40 columns
    assert '993' in out
    assert 'Native CI NOT QUALIFIED' not in out  # provider observation != CI qualification
    assert all(len(x)<=width for x in out.splitlines())


def test_overlay_receipt_is_not_guessed_and_indeterminate_is_not_percent():
    src={'repository':'Mikeyphw/devtool','head_sha':'a'*40,
         'artifact_sha256':'b'*64,'stages':[
         {'name':'Native validation','status':'success'},
         {'name':'Commit','status':'in_progress'}]}
    out=render('overlay',src,60)
    assert 'Native validation' in out
    assert 'ACTIVE / IN_PROGRESS' in out
    assert 'Session' in out
    assert 'not recorded' in out
    assert '%' not in out
    assert 'Native commit/session evidence required' in out


def test_credentials_are_whitelist_only_and_never_authority():
    data={'state':'CHECKS_RUNNING','credential_health':{
        'auth_mode':'app','app_name':'Devtool App','installation_id':123,
        'permission_drift':'unverified', 'token':'github_pat_012345678901234567890',
        'authorized_for_operation':False},'runs':[]}
    out=render('ci',data,100)
    assert 'Devtool App' in out
    assert '123' in out
    assert 'unverified' in out
    assert 'False' in out
    assert 'github_pat_' not in out
    assert 'Native CI NOT QUALIFIED' in out


def test_no_ansi_markup_or_secret_exposure():
    value='Authorization: Bearer exampleSecret\x1b[31m\x1b]0;HACK\x07 [red]'
    cleaned=safe(value)
    assert 'exampleSecret' not in cleaned
    assert '\x1b' not in cleaned
    assert 'HACK' not in cleaned
    assert '[red]' in cleaned  # Text, not markup
    out=render('ci',{'state':'CHECKS_FAILED','runs':[
        {'name':value,'status':'completed','conclusion':'failure'}]},80)
    assert 'exampleSecret' not in out
    assert '\x1b' not in out


def test_modes_use_existing_config_without_new_flags():
    assert icon_mode({'DEVTOOL_UI_ICONS':'ascii'})=='ascii'
    assert icon_mode({'DEVTOOL_UI_ICONS':'unicode'})=='unicode'
    assert icon_mode({'DEVTOOL_UI_ICONS':'nerd'})=='nerd'
    assert icon_mode({'DEVTOOL_ASCII':'1','TERM':'xterm'})=='ascii'


def test_failed_skipped_unknown_never_turn_green():
    for state in ('CHECKS_FAILED','skipped','BLOCKED_TRIGGER','unknown','CHECKS_RUNNING'):
        out=render('ci',{'state':state,'runs':[]},52)
        assert 'Native CI NOT QUALIFIED' in out
        assert 'Native required checks passed' not in out
    out=render('ci',{'state':'REQUIRED_CHECKS_PASSED','runs':[]},52)
    assert 'Native required checks passed' in out
    assert 'release NOT qualified' in out


def test_logs_only_display_provided_sanitized_data():
    src={'state':'CHECKS_FAILED','runs':[{'name':'Run X'}]}
    out=render('ci',src,80,logs=True,log_lines=('token=verysecret','real failure'),log_page=0)
    assert 'real failure' in out
    assert 'verysecret' not in out
    assert 'Lines' in out


def test_overlay_monitor_never_modifies_native_result_or_calls_provider():
    source={'repository':'local repo','artifact':'source.zip'}
    before=repr(source)
    result=SimpleNamespace(returncode=2,skipped=False,metadata={'transaction_id':'tx1'})
    stream=io.StringIO()
    console=Console(file=stream,width=52,color_system=None,force_terminal=False)
    monitor=OverlayLiveProjection(source=source,enabled=True,console=console)
    monitor.start()
    try:
        monitor.stage('test','unit',None)
        monitor.stage('test','unit',result)
        monitor.finish(result)
    finally:
        monitor.close()
    assert repr(source)==before
    assert result.returncode==2
    assert monitor._live is None
    assert 'tx1' in stream.getvalue()


def test_delivery_monitor_still_owns_keys_and_does_not_qualify():
    native=NativeRichMonitor(enabled=False)
    native.observe({'state':'CHECKS_FAILED','runs':[{'name':'A'},{'name':'B'}]})
    for key in ('j','enter','v','l','r','q'):
        native.key(key)
    assert native.detached
    assert native.last['state']=='CHECKS_FAILED'
    assert native.refresh_requested
    assert native.log_requested


def test_progress_only_from_exact_native_counters():
    snapshot={'state':'CHECKS_RUNNING','runs':[
        {'name':'Real count','status':'in_progress','completed':3,'total':10},
        {'name':'Unknown count','status':'in_progress'},
        {'name':'Unsafe count','status':'in_progress','completed':20,'total':10}]}
    out=render('ci',snapshot,80)
    assert '3/10' in out
    assert 'activity ongoing' in out
    assert '20/10' not in out
    assert '30%' not in out


def test_real_callback_events_keep_time_unrecorded_without_timestamp():
    from devtool.m6_live import OverlayLiveProjection
    console=Console(file=io.StringIO(),width=60,color_system=None)
    m=OverlayLiveProjection(source={'repository':'local'},enabled=True,console=console)
    m.start()
    try:
        m.stage('test','run',None)
        m.stage('test','finish',SimpleNamespace(returncode=0,skipped=False,
                                               metadata={'timestamp':'2026-11-21T01:00Z'}))
        assert m.events[0]['name']=='test · run'
        assert 'at' not in m.events[0]
        assert m.events[1]['at']=='2026-11-21T01:00Z'
    finally:
        m.close()


@pytest.mark.parametrize('width',[40,52,60,80,120])
def test_m5_run_receipt_exact_text_compatibility(width):
    # Guard the exact presentation invariants of legacy
    # RichMonitorTests.test_run_job_steps_and_failure_render.
    sample={'remote':{'id':23,'status':'completed','conclusion':'failure',
                      'run_attempt':2,'head_sha':'abcd'},
            'source_binding':'matching',
            'jobs':[{'name':'android','status':'completed','conclusion':'failure',
                     'steps':[{'name':'Gradle tests','status':'completed',
                               'conclusion':'failure'}]}]}
    value=render('run',sample,width,detail=True,verbose=True)
    assert 'Attempt 2' in value
    assert 'Source abcd' in value
    assert 'Gradle tests' in value
    assert 'FAILURE' in value
    assert all(len(line)<=width for line in value.splitlines())


# M6.1 real interaction regression, appended to M6 exact source suite.
@pytest.mark.parametrize('width',[40,52,60,80,120])
def test_m6_ux_verbose_toggles_visible_details(width):
    snap={'remote':{'id':23,'status':'completed','conclusion':'failure',
                    'run_attempt':2,'head_sha':'abcd'},
          'jobs':[{'name':'Android tests','status':'completed','conclusion':'failure',
                   'steps':[{'name':'Gradle tests','status':'completed','conclusion':'failure'}]}]}
    compact=render('run',snap,width,verbose=False)
    detailed=render('run',snap,width,verbose=True)
    assert 'VERBOSE ON' not in compact
    assert 'VERBOSE ON' in detailed
    assert 'VERBOSE DETAILS' in detailed
    assert 'Recorded steps 1' in detailed
    assert all(len(line)<=width for line in detailed.splitlines())


@pytest.mark.parametrize('width,page_size',[ (40,4),(52,4),(60,8),(80,8),(120,8)])
def test_m6_ux_every_log_line_reachable(width,page_size):
    from devtool.m6_live import log_page_size
    lines=tuple('DEMO_LOG_LINE_%02d'%n for n in range(1,26))
    assert log_page_size(width)==page_size
    all_seen=''
    for page in range((len(lines)+page_size-1)//page_size):
        output=render('run',{'remote':{'id':23,'status':'completed',
                      'conclusion':'failure'}},width,logs=True,
                      log_lines=lines,log_page=page)
        all_seen+=output
        assert 'Log page ' in output
        assert 'Log GET refused' not in output
    for line in lines:
        assert line in all_seen, (width,line)


def test_m6_ux_native_refresh_is_visible_and_source_immutable():
    fixture={'state':'CHECKS_RUNNING','runs':[{'name':'A','status':'in_progress'}]}
    snapshot=repr(fixture)
    monitor=NativeRichMonitor(enabled=True,
         console=Console(file=io.StringIO(),width=80,color_system=None))
    monitor.observe(fixture)
    monitor.key('r')
    out=io.StringIO()
    Console(file=out,width=80,color_system=None).print(monitor.render())
    assert 'Refresh requested' in out.getvalue()
    monitor.wait_interval(0.1)
    assert not monitor.refresh_requested
    out=io.StringIO()
    Console(file=out,width=80,color_system=None).print(monitor.render())
    assert 'Refresh shortcut consumed' in out.getvalue()
    monitor.observe(fixture)
    out=io.StringIO()
    Console(file=out,width=80,color_system=None).print(monitor.render())
    assert 'Native observation rendered' in out.getvalue()
    assert repr(fixture)==snapshot
    assert monitor.last==fixture


def test_m6_ux_real_log_fetch_and_navigation_are_offline():
    calls=[]
    def local_logs(row):
        calls.append(row['id'])
        return tuple(f'DEMO_LOG_LINE_{n:02d}' for n in range(25))
    monitor=NativeRichMonitor(kind='run',enabled=True,
         console=Console(file=io.StringIO(),width=80,color_system=None),
         log_fetcher=local_logs)
    fixture={'remote':{'id':23,'run_attempt':2,'status':'completed',
                       'conclusion':'failure'}, 'jobs':[
                  {'name':'Android tests','status':'completed','conclusion':'failure'}]}
    monitor.observe(fixture)
    monitor.key('l')
    monitor.observe(fixture)  # consume explicit existing-provider callback
    assert calls==[23]
    assert len(monitor.log_lines)==25
    assert monitor.log_page==0
    monitor.key(']')
    assert monitor.log_page==1
    out=io.StringIO()
    Console(file=out,width=80,color_system=None).print(monitor.render())
    assert 'Log page 2/4' in out.getvalue()
    assert 'DEMO_LOG_LINE_08' in out.getvalue()
    assert 'DEMO_LOG_LINE_07' not in out.getvalue()
    monitor.key('[')
    assert monitor.log_page==0
    assert calls==[23]


@pytest.mark.parametrize('width',[40,52,60,80,120])
def test_m6_ux_legacy_log_lines_contract(width):
    source={'state':'CHECKS_FAILED','runs':[{'name':'Run X'}]}
    output=render('ci',source,width,logs=True,log_lines=('token=verysecret','real failure'),log_page=0)
    assert 'Lines' in output
    assert 'real failure' in output
    assert 'verysecret' not in output
    assert all(len(line)<=width for line in output.splitlines())

# M6.2 UX audit: full-width composition, compact log focus, callback resilience.
@pytest.mark.parametrize('width',[40,52,60,80,82,99,100,106,120])
def test_m6_v4_full_width_and_responsive_mode(width):
    sample={'remote':{'id':23,'run_attempt':2,'head_sha':'abcd',
                       'status':'completed','conclusion':'failure'},
            'source_binding':'matching',
            'jobs':[{'name':'Android tests','status':'completed','conclusion':'failure'}]}
    output=render('run',sample,width,verbose=True,detail=True,
                  logs=True,log_lines=tuple('LOCAL_LINE_%02d'%i for i in range(1,33)),
                  log_page=1)
    lines=output.splitlines()
    assert max(map(len,lines)) == width
    assert all(len(line) <= width for line in lines)
    assert 'Log page 2/' in output
    assert 'Lines ' in output
    assert 'Attempt 2' in output
    if width < 100:
        assert 'STAGES' not in output, 'compact log-focus should keep logs visible'
        assert len(lines) <= 22
    else:
        assert 'STAGES' in output
        assert 'EVIDENCE' in output
        assert 'VERBOSE DETAILS' in output
        assert 'LOCAL_LINE_09' in output
        # The responsive grid puts identity and output in the same row.
        assert 'IDENTITY' in output and 'VERBOSE DETAILS' in output
        assert output.index('OUTPUT') < output.index('VERBOSE DETAILS')


@pytest.mark.parametrize('width',[40,52,82,106])
def test_m6_v4_refresh_feedback_visible_during_logs(width):
    source={'remote':{'id':23,'run_attempt':2,'conclusion':'failure'},
            'source_binding':'matching',
            'm6_ui_feedback':'REFRESH_REQUESTED', 'jobs':[]}
    output=render('run',source,width,logs=True,log_lines=('real failure',))
    assert 'Refresh requested' in output
    assert 'real failure' in output
    assert all(len(line)<=width for line in output.splitlines())


def test_m6_v4_keyboard_keeps_processing_q_after_bad_callback():
    from devtool.delivery_rich import _Keyboard
    from unittest.mock import patch
    keys=[]
    class FailingCallback:
        def key(self,value):
            keys.append(value)
            if value=='r':
                raise AttributeError('synthetic callback regression')
            if value=='q':
                keyboard.stop_requested=True
    keyboard=_Keyboard(FailingCallback())
    keyboard.fd=123
    with (patch('select.select',side_effect=[([123],[],[]),([123],[],[])]),
          patch('os.read',side_effect=[b'r',b'q'])):
        keyboard._run()
    assert keys==['r','q']


def test_m6_v4_refresh_and_quit_state_preserved():
    monitor=NativeRichMonitor(enabled=False)
    monitor.observe({'state':'CHECKS_RUNNING','runs':[]})
    monitor.key('r')
    monitor.key('q')
    assert monitor.refresh_requested
    assert monitor.detached
    assert monitor.ui_feedback == 'REFRESH_REQUESTED'
    assert monitor.last['state']=='CHECKS_RUNNING'

# M6.3: focused inspection, navigation, first-fetch redraw and bounded feedback.
@pytest.mark.parametrize('kind',["run","ci","overlay"])
@pytest.mark.parametrize('width',[40,52,60,82,100,106,120])
def test_m6_v6_focused_pages_are_source_only(kind,width):
    payload={'repository':'Mikeyphw/devtool','state':'CHECKS_FAILED',
             'source_sha':'abcd','stages':[{'name':'validation','status':'failed'}],
             'events':[{'name':'native event','at':'2026-11-21T01:00Z'}],
             'evidence':{'ci_state':'CHECKS_FAILED'},
             'remote':{'id':23,'run_attempt':2,'conclusion':'failure'},
             'jobs':[{'name':'android','status':'completed','conclusion':'failure'}],
             'runs':[{'name':'android','status':'completed','conclusion':'failure'}]}
    unchanged=repr(payload)
    for focus,label in (('identity','IDENTITY'),('stages','STAGES'),
                        ('events','EVENTS'),('output','OUTPUT'),('evidence','EVIDENCE')):
        out=render(kind,payload,width,focus=focus)
        assert 'PAGE '+label in out
        assert max(map(len,out.splitlines()))<=width
        assert out.count('M6 · NATIVE LIVE')==1
    assert repr(payload)==unchanged


def test_m6_v6_overview_has_sections_and_legend_without_top_verbose():
    payload={'remote':{'id':23,'run_attempt':2,'conclusion':'failure'},'jobs':[]}
    out=render('run',payload,106,verbose=True)
    assert '0 overview' in out
    assert 'VERBOSE DETAILS' in out
    assert 'VERBOSE ON' not in out.splitlines()[1]
    assert 'IDENTITY' in out and 'OUTPUT' in out and 'EVIDENCE' in out


def test_m6_v6_focused_log_uses_first_local_fetch_without_provider_network():
    snapshots=[]
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=82),
                        log_fetcher=lambda row: tuple('LOCAL_%02d'%i for i in range(20)))
    m.observe({'remote':{'id':23,'conclusion':'failure'},'jobs':[]})
    m.kind='run'
    m.key('l')
    assert m.focus=='output' and m._pending_log_fetch
    original=m.refresh
    m.refresh=lambda: snapshots.append(render('run',m.last,82,focus=m.focus,
        logs=m.log_requested,log_lines=m.log_lines,log_page=m.log_page))
    try:m._consume_log_fetch()
    finally:m.refresh=original
    assert len(snapshots)==1 and 'LOCAL_00' in snapshots[-1]
    assert len(m.log_lines)==20


def test_m6_v6_pages_tab_and_quit_are_data_preserving():
    m=NativeRichMonitor(enabled=False)
    original={'state':'CHECKS_FAILED','runs':[{'name':'A','conclusion':'failure'}]}
    m.observe(original)
    for key,page in (('1','identity'),('2','stages'),('3','events'),
                     ('4','output'),('5','evidence'),('0','overview'),
                     ('\t','identity'),('h','overview')):
        m.key(key)
        assert m.focus==page
    m.key(']');assert m.focus_page==1
    m.key('[');assert m.focus_page==0
    m.key('q');assert m.detached
    assert m.last==original


def test_m6_v6_refresh_feedback_expires_without_extra_poll():
    import time
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=82))
    m.observe({'state':'CHECKS_RUNNING','runs':[]})
    m.key('r')
    m.wait_interval(0.05)
    assert m.ui_feedback=='REFRESH_CONSUMED'
    m._feedback_deadline=time.monotonic()-0.01
    m.wait_interval(0.05)
    assert m.ui_feedback==''
    assert m.last['state']=='CHECKS_RUNNING'


def test_m6_v6_explicit_output_focus_does_not_fetch_automatically():
    fetches=[]
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=82),
                        log_fetcher=lambda row: fetches.append(row) or ('test',))
    m.kind='run'
    m.observe({'remote':{'id':23},'jobs':[]})
    m.key('4');m._consume_log_fetch()
    assert m.focus=='output' and fetches==[]
    m.key('l');m._consume_log_fetch()
    assert len(fetches)==1



# M6.3.1: terminal frame stability is an additional presentation contract.
@pytest.mark.parametrize('width,height',[(40,19),(52,21),(82,25),(106,30),(120,32)])
def test_m6_v8_fixed_height_inline_viewport(width,height):
    from devtool.m6_live import BoundedLiveFrame, build_live_view
    import io
    from rich.console import Console
    snapshot={'remote':{'id':23,'head_sha':'abcd','conclusion':'failure'},
              'jobs':[{'name':f'job {i}','status':'completed','conclusion':'success'}
                      for i in range(25)]}
    views=[]
    for focus in ('overview','identity','stages','events','output','evidence'):
        sink=io.StringIO()
        console=Console(file=sink,width=width,height=height,
                        color_system=None,force_terminal=False)
        console.print(BoundedLiveFrame(build_live_view(
            'run',snapshot,width=width,focus=focus,icons='ascii',verbose=True)))
        text=sink.getvalue()
        assert text.count('M6 · NATIVE LIVE')==1
        assert len(text.splitlines())==height-3
        assert max(map(len,text.splitlines()))<=width
        views.append(text)
    assert len({len(v.splitlines()) for v in views})==1


def test_m6_v8_keyboard_worker_only_queues_presentation():
    import threading,types,io
    from rich.console import Console
    from devtool.delivery_rich import NativeRichMonitor
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=82,height=25))
    m.observe({'remote':{'id':23,'conclusion':'failure'},'jobs':[]})
    m.kind='run'
    calls=[]
    m.refresh=lambda: calls.append(threading.current_thread().name)
    m._keyboard=types.SimpleNamespace(thread=threading.current_thread())
    m.key('v')
    assert m.verbose is True and m._ui_dirty is True
    assert calls==[], 'Keyboard worker must not write a Rich frame'
    m.wait_interval(0.03)
    assert calls and m._ui_dirty is False


def test_m6_v8_refresh_and_detach_do_not_need_keyboard_render():
    import threading,types,io
    from rich.console import Console
    from devtool.delivery_rich import NativeRichMonitor, MonitorDetached
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO(),width=82,height=25))
    m.observe({'remote':{'id':23,'conclusion':'failure'},'jobs':[]})
    m.kind='run'
    calls=[]
    m.refresh=lambda: calls.append('render')
    m._keyboard=types.SimpleNamespace(thread=threading.current_thread())
    m.key('r')
    assert m.refresh_requested and m.ui_feedback=='REFRESH_REQUESTED'
    assert calls==[]
    m.wait_interval(0.07)
    assert m.ui_feedback=='REFRESH_CONSUMED'
    m.key('q')
    try:
        m.wait_interval(0.07)
    except MonitorDetached: pass
    else: assert False,'detach must interrupt native wait'
    assert m.detached
