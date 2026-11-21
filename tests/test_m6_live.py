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
