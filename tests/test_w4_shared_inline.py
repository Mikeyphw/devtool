"""Real shared-host, native projection and PTY ownership regressions."""
from __future__ import annotations
import io
import os
from pathlib import Path
import signal
import sys
import threading
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text
from devtool.inline_live import InlineLive
from devtool.delivery_rich import NativeRichMonitor
from devtool.m6_live import build_live_view
from devtool.tui_textual import TextualAvailability, run_tui_entrypoint

ROOT=Path(__file__).resolve().parents[1]


def test_only_one_live_constructor_and_posix_owner():
    imports=[p.name for p in (ROOT/'devtool').glob('*.py') if 'from rich.live import Live' in p.read_text()]
    assert imports==['inline_live.py']
    for name in ('m6_operation_view.py','operator_workspace.py','operator_rich.py','delivery_rich.py'):
        source=(ROOT/'devtool'/name).read_text()
        assert 'InlineLive' in source and 'tty.setcbreak' not in source
        assert 'PosixInput' in source or 'KeyboardWorker' in source


def test_exclusive_live_lease_released_after_nested_and_thread_refusal():
    out=Console(file=io.StringIO(),width=28,height=14,force_terminal=False)
    errors=[]
    def attempt(owner):
        try:owner.start()
        except RuntimeError as e:errors.append(str(e))
    with InlineLive(Text('first'),console=out) as live:
        with pytest.raises(RuntimeError,match='Another inline'):
            InlineLive(Text('second'),console=out).start()
        worker=threading.Thread(target=attempt,args=(live,));worker.start();worker.join()
        assert errors and 'another thread' in errors[0]
    with InlineLive(Text('reacquired'),console=out):pass


def test_renderer_failure_releases_native_result(monkeypatch):
    from devtool import inline_live
    class BadLive:
        def __init__(self,*a,**kw):assert kw['screen'] is False and not kw['redirect_stdout'] and not kw['redirect_stderr']
        def start(self,**kw):raise ValueError('bad render')
        def stop(self):raise RuntimeError('bad stop')
    out=Console(file=io.StringIO(),width=40)
    with monkeypatch.context() as m:
        m.setattr(inline_live,'Live',BadLive)
        with pytest.raises(ValueError,match='bad render'):InlineLive(Text('x'),console=out).start()
        monitor=NativeRichMonitor(enabled=True,console=out)
        with monitor:
            assert not monitor.enabled
            evidence={'state':'CHECKS_FAILED','runs':[]};monitor.observe(evidence);assert monitor.last==evidence
    with InlineLive(Text('again'),console=out):pass


def test_source_or_attempt_drift_invalidates_logs_no_implicit_fetch():
    calls=[]
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO()),log_fetcher=lambda r: calls.append(r.copy()) or ('original',))
    old={'state':'CHECKS_FAILED','head_sha':'a'*40,'runs':[{'id':23,'attempt':1,'head_sha':'a'*40}]}
    m.observe(old);m.key('l');m._consume_log_fetch();assert len(calls)==1 and m.log_lines
    new={'state':'CHECKS_FAILED','head_sha':'a'*40,'runs':[{'id':23,'attempt':2,'head_sha':'a'*40}]}
    m.observe(new);assert m.log_lines==() and len(calls)==1
    m.key('l');m.key('l');m._consume_log_fetch();assert len(calls)==2 and calls[-1]['attempt']==2


@pytest.mark.parametrize('change',['attempt','source','workflow','repository','close','detach','select','refresh'])
def test_pending_log_authorization_is_exact_and_revocable(change):
    calls=[];m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO()),log_fetcher=lambda r:calls.append(r.copy()) or ('safe',))
    row={'id':23,'attempt':1,'head_sha':'a'*40,'workflow_id':33,'event':'pull_request'}
    old={'state':'CHECKS_RUNNING','source_sha':'a'*40,'repository':'Mikeyphw/devtool','runs':[row,dict(row,id=24)]}
    m.observe(old);m.key('l')
    if change=='close':m.key('l')
    elif change=='detach':m.key('q')
    elif change=='select':m.key('j')
    elif change=='refresh':m.key('r');m.key('l')
    elif change=='repository':m.observe(dict(old,repository='Other/Repo'))
    elif change=='source':m.observe(dict(old,source_sha='b'*40))
    else:m.observe(dict(old,runs=[dict(row,**({'attempt':2} if change=='attempt' else {'workflow_id':34})),dict(row,id=24)]))
    m._consume_log_fetch()
    assert calls==[] and not m.log_lines


def test_selection_reorder_preserves_exact_run_and_navigation_does_not_fetch():
    calls=[];m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO()),log_fetcher=lambda r:calls.append(r.copy()) or ('safe',))
    row={'id':23,'attempt':1,'head_sha':'a'*40,'workflow_id':33}
    old={'state':'CHECKS_RUNNING','source_sha':'a'*40,'runs':[row,dict(row,id=24)]}
    m.observe(old);m.key('l');m.observe(dict(old,runs=list(reversed(old['runs']))))
    assert m.selected==1 and [r['id'] for r in calls]==[23]
    m.key('k');m._consume_log_fetch();assert not m.log_lines and len(calls)==1
    m.key('r');m._consume_log_fetch();assert len(calls)==1
    m.key('l');m.key('l');m._consume_log_fetch();assert [r['id'] for r in calls]==[23,24]


@pytest.mark.parametrize('key',['l','q','j'])
def test_inflight_log_result_discarded_after_keyboard_revokes(key):
    ready=threading.Event();finish=threading.Event();calls=[]
    def get(row):
        calls.append(row.copy());ready.set();assert finish.wait(5);return ('stale',)
    m=NativeRichMonitor(enabled=True,console=Console(file=io.StringIO()),log_fetcher=get)
    row={'id':23,'attempt':1,'head_sha':'a'*40}
    m.observe({'runs':[row,dict(row,id=24)]});m.key('l')
    worker=threading.Thread(target=m._consume_log_fetch);worker.start()
    try:
        assert ready.wait(5);m.key(key)
    finally:finish.set();worker.join(5)
    assert not worker.is_alive() and len(calls)==1 and not m.log_lines


def test_overlay_callback_timestamps_real_and_distinguished():
    from datetime import datetime,timezone
    from devtool.m6_live import OverlayLiveProjection
    p=OverlayLiveProjection(source={},enabled=True,console=Console(file=io.StringIO()))
    before=datetime.now(timezone.utc);p.stage('apply','start');after=datetime.now(timezone.utc)
    assert before<=datetime.fromisoformat(p.events[-1]['observed_at'])<=after
    assert p.events[-1]['time_origin']=='observed-callback'
    p.stage('apply','done',SimpleNamespace(returncode=0,metadata={'timestamp':'2026-11-26T10:00:00+00:00'}))
    assert p.events[-1]['at']=='2026-11-26T10:00:00+00:00' and p.events[-1]['time_origin']=='native'
    p.finish(SimpleNamespace(returncode=0,name='apply',metadata={}))
    assert p.events[-1]['observed_at'] and 'at' not in p.events[-1] and p.events[-1]['status']=='APPLY_RETURNED_COMMIT_SESSION_UNVERIFIED'


def test_rich_default_selects_native_route_and_once_is_unchanged(tmp_path):
    calls=[]
    def rich(args,**kwargs):calls.append('rich');return 0
    def textual(args,**kwargs):calls.append('textual');return 0
    for backend,once in [('auto',False),('rich',False),('textual',False),('auto',True)]:
        assert run_tui_entrypoint(SimpleNamespace(backend=backend,once=once),repo_root=tmp_path,
            availability=TextualAvailability(True),rich_runner=rich,textual_runner=textual)==0
    assert calls==['rich','rich','textual','textual']
    assert run_tui_entrypoint(SimpleNamespace(backend='rich',require_textual=True),repo_root=tmp_path,
        availability=TextualAvailability(True),rich_runner=rich,textual_runner=textual)==2


@pytest.mark.parametrize('width',[28,32,36,40,48,60,80,100,120])
def test_focus_source_identity_not_lost(width):
    sha='0123456789abcdef'*2+'01234567';artifact='ab'*32
    obj={'completion_status':'success','source_sha':sha,'artifact_sha256':artifact}
    visible=''
    for page in range(20):
        s=io.StringIO();Console(file=s,width=width,color_system=None).print(build_live_view('overlay',obj,width=width,height=19,focus='identity',focus_page=page,icons='ascii'))
        visible+=s.getvalue()
        assert 'PASS / SUCCESS' not in s.getvalue()
    import re
    # Reconstruct source hash fragments across folded native focus rows.
    assert sha in ''.join(re.findall(r'(?m)^│\s*(?:Source |Artifact )?([a-f0-9]+)\s*│$',visible))
    assert artifact in ''.join(re.findall(r'(?m)^│\s*(?:Source |Artifact )?([a-f0-9]+)\s*│$',visible))


@pytest.mark.skipif(os.name!='posix',reason='POSIX PTY required')
@pytest.mark.parametrize('mode',['normal','exception','SIGINT','SIGTERM','SIGHUP'])
def test_real_pty_shared_host_keyboard_resize_restore(mode):
    import pexpect
    code='''import os,sys,termios,signal
from rich.console import Console
from rich.text import Text
from devtool.inline_live import InlineLive
from devtool.posix_input import PosixInput
fd=sys.stdin.fileno();before=termios.tcgetattr(fd);handlers={s:signal.getsignal(s) for s in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP)}
out=Console();mode=sys.argv[1];rc=0
try:
    with PosixInput(fd) as keys, InlineLive(Text('READY'),console=out) as live:
        while True:
            key=keys.read(.2)
            live.resize(fd)
            if key in ('up','down'):
                print('KEY='+key+' GEOMETRY='+str(out.width),flush=True)
            if key=='x':raise RuntimeError('fixture')
            if key=='q':break
except KeyboardInterrupt:rc=130
except SystemExit as e:rc=e.code
except RuntimeError:rc=17
assert termios.tcgetattr(fd)==before
assert all(signal.getsignal(s)==handler for s,handler in handlers.items())
with InlineLive(Text('AFTER'),console=out):pass
print('TTY_RESTORED',rc,flush=True)
raise SystemExit(rc)
'''
    env={**os.environ,'TERM':'xterm-256color','PYTHONPATH':str(ROOT),'PYTHONDONTWRITEBYTECODE':'1'}
    for k in ('CI','NO_COLOR','COLUMNS','LINES','CLICOLOR'):env.pop(k,None)
    p=pexpect.spawn(sys.executable,['-B','-c',code,mode],env=env,encoding='utf-8',timeout=10,dimensions=(24,80))
    import time
    p.delaybeforesend=0
    try:
        p.expect('READY');p.setwinsize(19,28);p.send('\x1b');time.sleep(.015);p.send('[A');p.expect('KEY=up GEOMETRY=28')
        p.setwinsize(32,120);p.send('\x1b[B');p.expect('KEY=down GEOMETRY=120')
        if mode=='normal':p.send('q');rc=0
        elif mode=='exception':p.send('x');rc=17
        elif mode=='SIGINT':p.sendcontrol('c');rc=130
        else:os.kill(p.pid,getattr(signal,mode));rc=128+getattr(signal,mode)
        p.expect('TTY_RESTORED '+str(rc));p.expect(pexpect.EOF);p.close();assert p.exitstatus==rc
    finally:
        if p.isalive():p.close(force=True)

@pytest.mark.parametrize('width',[28,40,60,80,120])
def test_long_log_fragments_are_reachable_without_additional_fetch(width):
    from devtool.m6_live import log_display_rows,log_page_size
    # Deliberately longer than the previous 140-character / terminal-width cap.
    line=''.join(f'{i:03d}' for i in range(90))+'TAIL'
    console=Console(file=io.StringIO(),width=width,height=22,color_system=None)
    calls=[];m=NativeRichMonitor(enabled=True,console=console,log_fetcher=lambda row:calls.append(row) or (line,))
    m.observe({'runs':[{'id':23,'attempt':1,'head_sha':'a'*40}]});m.key('l');m._consume_log_fetch()
    rows=log_display_rows(m.log_lines,width)
    assert ''.join(r.plain[2:] for _,r in rows)==line
    seen=''
    for i in range((len(rows)+log_page_size(width)-1)//log_page_size(width)):
        out=io.StringIO();Console(file=out,width=width,color_system=None).print(m.render())
        for row in out.getvalue().splitlines():
            # Prefix is the log-row marker, not outer panel border.
            if row.startswith('│ │ '):seen+=row[4:].rstrip(' │')
        m.key(']')
    assert seen==line,(width,seen)
    assert len(calls)==1


@pytest.mark.parametrize('width',[28,40,60,80,120])
def test_log_display_clusters_preserve_emoji_and_combining_boundaries(width):
    from rich.cells import cell_len
    from devtool.m6_live import log_display_rows, safe
    from devtool.delivery_remote_logs import _safe_text
    units=('界','👨‍👩‍👧‍👦','👍🏽','🇧🇷','e\u0301','🧑🏽‍💻','1️⃣','한','क्ष')
    for unit in units:
        clusters=['a','b','c']+[unit]*19+['e','n','d']
        line=''.join(clusters); end=0; allowed=set()
        for cluster in clusters:
            end+=len(cluster); allowed.add(end)
        rows=[row.plain[2:] for _,row in log_display_rows((line,),width)]
        assert ''.join(rows)==line
        end=0
        for row in rows:
            end+=len(row)
            assert end in allowed, (width,unit,row)
            assert cell_len(row)<=width-6
        cut='a'*279+unit+'tail'
        if len(unit)>1:
            assert _safe_text(cut)=='a'*279
            assert safe(cut,limit=280)=='a'*279+'…'
