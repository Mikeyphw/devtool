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


# M6.4/M6.5: canonical launcher boundaries and retained native receipt views.
def test_m6_launcher_boundary_real_delegation_and_failure(monkeypatch,tmp_path):
    from devtool import launcher_contract_v3 as launcher
    from devtool import m6_operation_view as view
    calls=[]; emitted=[]
    monkeypatch.setattr(view,'boundary_enabled',lambda **kw:True)
    monkeypatch.setattr(view,'emit_boundary',lambda label,state,**kw:emitted.append((label,state,kw)))
    def native_exec(argv,**kwargs):
        calls.append((argv,kwargs))
        if kwargs['stage']=='native-atomic-apply-and-commit':
            raise launcher.Refused('native refused')
        return 'native bytes'
    monkeypatch.setattr(launcher,'exec_cmd',native_exec)
    assert launcher.native(tmp_path,tmp_path,'validate-overlay','artifact.zip',stage='native-validate-overlay',live=True)=='native bytes'
    assert len(calls)==1 and calls[0][1]['live'] is True
    assert calls[0][0][-3:]==['--no-clipboard','validate-overlay','artifact.zip']
    assert [e[1] for e in emitted]==['started','returned; native evidence determines verdict']
    with pytest.raises(launcher.Refused,match='native refused'):
        launcher.native(tmp_path,tmp_path,'apply-overlay','artifact.zip',stage='native-atomic-apply-and-commit',live=True)
    assert len(calls)==2 and 'refused' in emitted[-1][1]
    before=len(emitted)
    assert launcher.native(tmp_path,tmp_path,'status','--json',stage='session-status')=='native bytes'
    assert len(emitted)==before and len(calls)==3


def test_m6_wrapper_boundary_once_native_code_and_exception(monkeypatch):
    from devtool import m6_operation_view as view
    monkeypatch.setattr(view,'boundary_enabled',lambda **kwargs:True)
    trace=[]
    monkeypatch.setattr(view,'emit_boundary',lambda label,state,**kw:trace.append((state,kw)))
    def invoke():
        trace.append('invoked')
        return 73
    assert view.wrapper_boundary('build',['--repo','/private/path'],invoke)==73
    assert trace.count('invoked')==1 and trace[-1][1]['rc']==73
    def refused():
        raise RuntimeError('original native error')
    with pytest.raises(RuntimeError,match='original native error'):
        view.wrapper_boundary('build',[],refused)
    assert trace[-1][0]=='interrupted or refused'


@pytest.mark.parametrize('args,argv',[(SimpleNamespace(json=True),()),(SimpleNamespace(machine='x'),()),(SimpleNamespace(format='json'),()),(None,('--json',)),(None,('--format=json',)),(None,('--quiet',)),(None,('--stdout-payload=raw',))])
def test_m6_inspector_excludes_machine_output(monkeypatch,args,argv):
    from devtool import m6_operation_view as view
    monkeypatch.setattr(view,'terminal_rich_enabled',lambda **kw:True)
    monkeypatch.delenv('DEVTOOL_RICH_LIVE',raising=False)
    assert not view.boundary_enabled(args=args,argv=argv)


def test_m6_inspector_opt_in_no_implicit_read(monkeypatch,tmp_path):
    from devtool import m6_operation_view as view
    monkeypatch.setattr(view,'terminal_rich_enabled',lambda **kw:True)
    monkeypatch.delenv('DEVTOOL_RICH_LIVE',raising=False)
    monkeypatch.delenv('DEVTOOL_UI_INSPECT',raising=False)
    def forbidden(*args,**kw):
        pytest.fail('unexpected native record read')
    monkeypatch.setattr(view,'read_bound_transaction',forbidden)
    assert not view.browse_session({'native_apply':{'transaction_id':'x'}},tmp_path)
    monkeypatch.setenv('DEVTOOL_UI_INSPECT','1')
    assert not view.browse_session({'native_apply':{'transaction_id':'x'}},tmp_path,args=SimpleNamespace(json=True))


def test_m6_saved_session_no_fabricated_success_or_raw_credentials():
    from devtool import m6_operation_view as view
    payload={'native_apply':{'state':'COMMITTED','validation_steps':[{'name':'actual','outcome':'failed','returncode':17,'skipped':False}], 'changed_paths':['src/real.py']},'operator_status':{'state':'BLOCKED','target':{'head_sha':'a'*40,'source_binding':'drifted'},'session':{'id':'b'*32,'resumable':False},'ci':{'required':{'state':'RECORDED_NEEDS_LIVE_RECHECK','passed':False},'regression':{'state':'NOT_ATTESTED'},'release_qualification':'NOT_PERFORMED'}},'credential':{'mode':'app','installation_id':123,'token':'ghp_'+ 'A'*40,'granted_permissions':{'contents':'read','password':'hidden'},'expires_at':'2030-01-01'}}
    before=repr(payload)
    pages=view.session_pages(payload,tx_state='archived',tx={'status':'completed','affected_paths':[{'path':'a.py','verified':False,'restore_state':'pending'}]})
    text=repr(pages)
    for value in ('actual','failed','17','src/real.py','drifted','RECORDED_NEEDS_LIVE_RECHECK','NOT_ATTESTED','NOT_PERFORMED','archived','Historical receipt only','pending'):
        assert value in text
    assert 'ghp_' not in text and 'hidden' not in text and repr(payload)==before
    assert '[redacted]'==view._public('https://user:pass@example.invalid')
    assert '[redacted]'==view._public('-----BEGIN PRIVATE KEY-----\nsecret')
    assert '\x1b' not in view._public('\x1b[2Jnative')


@pytest.mark.parametrize('width',[40,60,80,106])
def test_m6_saved_inspection_all_hash_cells_reachable(width):
    from devtool import m6_operation_view as view
    pages={1:[('source', '1234567890abcdef'*4),('artifact','目标/e\u0301/very-long-artifact.zip'),('state','UNVERIFIED')]}
    previous=-1; output=[]
    for page in range(20):
        frame,index=view.render_inspection(pages,1,page,width=width,height=15)
        if index==previous: break
        previous=index
        buf=io.StringIO(); out=Console(file=buf,width=width,height=15,color_system=None)
        out.print(frame)
        output.append(buf.getvalue())
    result=''.join(output)
    assert 'UNVERIFIED' in result and 'source:' in result and '目标' in result
    assert result.count('1234567890abcdef')>=2
    assert 'q exit' in result


def test_m6_native_transaction_real_readers_no_recovery(tmp_path,monkeypatch):
    from devtool import transactions
    from devtool import m6_operation_view as view
    root=tmp_path/'private'; root.mkdir()
    repo=tmp_path/'repo'; repo.mkdir()
    txid='20261121T010101Z-aabbccddeeff'
    monkeypatch.setattr(transactions,'transaction_root',lambda path:root)
    active=root/txid/'transaction.json'; active.parent.mkdir()
    history=root/'history'; history.mkdir()
    archived=history/(txid+'.json')
    import json
    raw=json.dumps({'transaction_id':txid,'repo_root':str(repo.resolve()),'status':'active','affected_paths':[{'path':'a.py','verified':False}]})
    active.write_text(raw)
    assert view.read_bound_transaction(repo,txid)[0]=='active'
    assert active.read_text()==raw and not archived.exists()
    archived.write_text(raw)
    assert view.read_bound_transaction(repo,txid)[0]=='ambiguous transaction records'
    active.unlink()
    state,result=view.read_bound_transaction(repo,txid)
    assert state=='archived' and result['transaction_id']==txid and archived.read_text()==raw
    active.symlink_to(archived)
    assert view.read_bound_transaction(repo,txid)[0]=='unsafe transaction path'
    assert view.read_bound_transaction(repo,'../foreign')[0]=='invalid transaction identity'


def test_m6_saved_browser_real_pty(tmp_path):
    import sys
    import pexpect
    from pathlib import Path
    code='''import os,termios,sys
from devtool.m6_operation_view import browse_inspection
fd=sys.stdin.fileno(); old=termios.tcgetattr(fd)
assert browse_inspection({0:[('state','UNVERIFIED')],1:[('source','HEAD-EXACT')],5:[('validation','FAILED-REAL')]})
assert termios.tcgetattr(fd)==old
print('M6_PTY_RESTORED',flush=True)
'''
    env=os.environ.copy(); env.update(TERM='xterm-256color',DEVTOOL_UI_INSPECT='1',PYTHONPATH=str(Path(__file__).resolve().parents[1]),PYTHONDONTWRITEBYTECODE='1');
    for name in ('CI','NO_COLOR','CLICOLOR','DEVTOOL_RICH_LIVE','COLUMNS','LINES'): env.pop(name,None)
    child=pexpect.spawn(sys.executable,['-B','-c',code],env=env,encoding='utf-8',timeout=12,dimensions=(24,60))
    try:
        child.expect('UNVERIFIED'); child.send('1'); child.expect('HEAD-EXACT')
        child.setwinsize(19,40); child.send('5'); child.expect('FAILED-REAL'); child.send('q'); child.expect('M6_PTY_RESTORED'); child.expect(pexpect.EOF)
        child.close(); assert child.exitstatus==0
    finally:
        if child.isalive(): child.close(force=True)


def test_m6_history_projection_real_child_and_rollback_only():
    from devtool.m6_operation_view import history_pages
    payload={'history_id':'a'*32,'child_outcomes':[{'name':'artifact-test:real','returncode':1,'skipped':False}],'validation_plan':{'validation_step_outcomes':[{'name':'real','outcome':'failed','returncode':1}]},'validation_report_path':'/private/reports/real.json','rollback_status':'rolled-back','transaction':{'id':'real','status':'rolled-back'},'token':'secret-private'}
    pages=history_pages(payload); assert 'artifact-test:real' in repr(pages[2]); assert 'failed' in repr(pages[2]); assert 'real.json' in repr(pages[4]); assert 'rolled-back' in repr(pages[5]); assert 'secret-private' not in repr(pages)


def test_m6_native_history_real_identity_read_only(tmp_path,monkeypatch):
    from devtool import artifacts
    from devtool.m6_operation_view import read_bound_history
    import json
    native={'history_id':'a'*32,'operation_id':'native-run','artifact_sha256':'b'*64,'commit':'c'*40,'transaction_id':'20261121T010101Z-aabbccddeeff'}
    monkeypatch.setattr(artifacts,'operation_history_entries_dir',lambda _:tmp_path)
    path=tmp_path/(native['history_id']+'.json')
    payload={'history_id':native['history_id'],'operation_id':native['operation_id'],'sha256':native['artifact_sha256'],'commit_hash':native['commit'],'transaction':{'id':native['transaction_id']}}
    raw=json.dumps(payload); path.write_text(raw)
    assert read_bound_history(tmp_path,native)==('exact native runtime history',payload)
    assert path.read_text()==raw
    assert read_bound_history(tmp_path,{**native,'commit':'d'*40})[0]=='runtime history identity mismatch'
    assert read_bound_history(tmp_path,{**native,'history_id':'../foreign'})[0]=='history identity not recorded'


def test_m6_closure_saved_ui_fallback_40_columns_keeps_quit_visible():
    from devtool.m6_operation_view import render_inspection
    frame, _ = render_inspection({0: [('state','NO_PROVIDER_PROBE')]}, 0, 0,
                                  width=40, height=16, title='M6 · SAVED SESSION')
    output=io.StringIO()
    Console(file=output,force_terminal=False,color_system=None,width=40).print(frame)
    assert 'q exit' in output.getvalue()
    assert '[/]' not in output.getvalue()


def test_m6_closure_missing_credential_proof_stays_explicitly_unverified():
    snapshot={'state':'CHECKS_RUNNING', 'runs':[], 'head_sha':'b'*40}
    output=render('ci',snapshot,100,focus='evidence')
    assert 'UNVERIFIED' in output
    assert 'Credential health' in output
    assert 'REQUIRED_CHECKS_PASSED' not in output
