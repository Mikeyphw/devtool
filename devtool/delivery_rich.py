"""Rich, in-terminal projection of *native* delivery/Actions observations.

Presentation only: no GitHub calls, source selection, CI qualification, process
execution, artifact writes, clipboard calls or remote mutations happen here.
Rich Live is inline (never alternate-screen / Textual). Native CLI remains the
sole producer of the verdict; a renderer exception must never turn CI green.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import sys
import time
from typing import Any, Mapping

from rich.box import SIMPLE
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live


_STATES = {
    'REQUIRED_CHECKS_PASSED': ('PASS', 'green'),
    'CHECKS_FAILED': ('FAILED', 'bold red'),
    'CHECKS_RUNNING': ('RUNNING', 'cyan'),
    'CHECKS_EVIDENCE_PENDING': ('VERIFYING', 'yellow'),
    'WAITING_REGISTRATION': ('WAITING', 'yellow'),
    'REGISTRATION_PROVIDER_UNAVAILABLE': ('UNAVAILABLE', 'yellow'),
    'REGISTRATION_TIMED_OUT': ('TIMEOUT', 'red'),
    'BRANCH_RULES_UNVERIFIED': ('UNVERIFIED', 'yellow'),
    'BLOCKED_AMBIGUOUS': ('BLOCKED', 'red'),
    'BLOCKED_CONDITIONAL': ('BLOCKED', 'yellow'),
    'BLOCKED_TRIGGER': ('BLOCKED', 'red'),
    'DISPATCH_UNCORRELATED': ('UNCORRELATED', 'yellow'),
}
_ICON = {'success': ('✓', 'green'), 'failure': ('✕', 'red'),
         'cancelled': ('✕', 'red'), 'timed_out': ('✕', 'red'),
         'skipped': ('−', 'yellow'), 'neutral': ('·', 'dim'),
         'in_progress': ('●', 'cyan'), 'queued': ('◷', 'yellow'),
         'waiting': ('◷', 'yellow'), 'pending': ('◷', 'yellow'),
         'requested': ('◷', 'yellow')}


def terminal_rich_enabled(*, env: Mapping[str, str] | None = None,
                          stdout: Any = None, stdin: Any = None) -> bool:
    """True only with an actual interactive terminal or launcher-verified relay.

    Never trust FORCE_COLOR alone as proof of a TTY: redirected stdout must
    stay plain, even if the operator or an ancestor process exports it.
    This is independent of Textual/plain-host selectors.
    """
    e = os.environ if env is None else env
    output = sys.stdout if stdout is None else stdout
    input_stream = sys.stdin if stdin is None else stdin
    if ('NO_COLOR' in e or e.get('CLICOLOR') == '0' or e.get('TERM') == 'dumb'
            or str(e.get('CI', '')).lower() in ('1', 'true', 'yes', 'on')):
        return False
    try:
        input_tty = bool(input_stream.isatty())
        output_tty = bool(output.isatty())
    except (OSError, AttributeError, ValueError):
        return False
    return input_tty and output_tty


def _truncate(value: object, limit: int) -> str:
    value = ''.join(' ' if ord(c) < 32 or ord(c) == 127 else c for c in str(value or ''))
    return value if len(value) <= limit else value[:max(1,limit-1)] + '…'


def _mark(status: object, conclusion: object) -> Text:
    state = str(conclusion or status or 'neutral')
    symbol, color = _ICON.get(state, ('·','dim'))
    return Text(symbol + ' ' + (state.replace('_',' ')).upper(), style=color)


def _width(console: Console | None = None) -> int:
    if console is not None:
        try: return max(40, min(180, int(console.width)))
        except (ValueError, TypeError): pass
    return max(40, min(180, shutil.get_terminal_size((80, 24)).columns))


def ci_snapshot_view(snapshot: Mapping[str, Any], *, width: int = 80,
                     selected: int = 0, verbose: bool = False,
                     detail: bool = False, logs: bool = False) -> Group:
    """Pure Rich projection: faithful states, no success inference."""
    narrow = width < 68
    rows = list(snapshot.get('runs') or [])
    state = str(snapshot.get('state') or 'WAITING_REGISTRATION')
    summary, color = _STATES.get(state, (_truncate(state,24), 'yellow'))
    required = list(snapshot.get('required') or [])
    missing = list(snapshot.get('missing') or [])
    phase = Text()
    phase.append('DEVTOOL  ·  PR CHECKS', style='bold')
    phase.append('   ' + summary, style=color)
    info = Text()
    info.append(f"poll {snapshot.get('poll','?')}", style='dim')
    info.append(f"   required {len(required)}   observed {len(rows)}   missing {len(missing)}", style='dim')
    table = Table(show_header=not narrow, header_style='dim', box=None,
                  expand=True, show_edge=False, padding=(0,1))
    if narrow:
        table.add_column('Checks', ratio=1, overflow='fold')
    else:
        table.add_column('Workflow', ratio=3, overflow='ellipsis')
        table.add_column('Result', ratio=1, no_wrap=True)
        table.add_column('Run', ratio=1, no_wrap=True, style='dim')
    for idx, row in enumerate(rows):
        name = _truncate(row.get('name') or 'Unnamed', 58 if narrow else 75)
        mark = _mark(row.get('status'), row.get('conclusion'))
        focus = '› ' if idx == selected else '  '
        if narrow:
            cell=Text(focus+name+'  ', style='bold' if idx == selected else '')
            cell.append_text(mark)
            table.add_row(cell)
        else:
            table.add_row(Text(focus+name,style='bold' if idx==selected else ''),
                          mark, str(row.get('id') or '—'))
    for name in missing:
        if narrow:
            table.add_row(Text('  ' + _truncate(name,35) + '  ◷ MISSING', style='yellow'))
        else:
            table.add_row(Text('  ' + _truncate(name,60),style='yellow'),
                          Text('◷ MISSING',style='yellow'), '—')
    if not rows and not missing:
        table.add_row('Waiting for source-bound GitHub run observations…', *(['',''] if not narrow else []))
    body: list[Any] = [phase, info, table]
    if detail and rows:
        chosen = rows[min(selected,len(rows)-1)]
        more = Text()
        more.append('Selected  ',style='bold')
        more.append(_truncate(chosen.get('name'),width-24))
        more.append(f"\nRun {chosen.get('id')}  attempt {chosen.get('attempt','?')}",style='dim')
        url=chosen.get('url')
        if url:more.append('\n'+_truncate(url,width-5),style='cyan underline')
        body.append(more)
    if state != 'REQUIRED_CHECKS_PASSED':
        body.append(Text('Native CI: NOT QUALIFIED · no release claim', style='yellow'))
    else:
        body.append(Text('Native required checks passed · release not qualified', style='green'))
    if state == 'CHECKS_FAILED':
        problems = (snapshot.get('diagnosis') or {}).get('failures') or []
        if problems:
            for problem in problems[:2]:
                for job in (problem.get('jobs') or [])[:2]:
                    label = _truncate(job.get('name') or 'Failed job', width-10)
                    body.append(Text('✕ '+label,style='bold red'))
                    for excerpt in (job.get('excerpt') or [])[:(8 if logs else 2)]:
                        body.append(Text('  '+_truncate(excerpt,width-8),style='dim'))
        elif snapshot.get('failures'):
            body.append(Text('✕ Required check failed · inspect native diagnostics',style='red'))
    if verbose:
        registration = snapshot.get('registration') or {}
        body.append(Text('Registrar '+_truncate(registration.get('event','?'),20)+
                         ' · scanned '+str(registration.get('run_count','?'))+
                         ' · policy '+str(snapshot.get('policy_sha256') or '')[:12],style='dim'))
    body.append(Text('j/k select  Enter details  l logs  v verbose  r refresh  q detach',style='dim'))
    return Group(Panel(Group(*body),border_style='red' if state=='CHECKS_FAILED' else 'cyan',
                       padding=(0,1),expand=False))


def actions_run_view(observation: Mapping[str, Any], *, width: int = 80,
                     selected: int = 0, verbose: bool = False,
                     detail: bool = False, logs: bool = False) -> Group:
    """Read-only Actions run/job/step projection from exact provider snapshots."""
    remote=observation.get('remote') or {}
    jobs=list(observation.get('jobs') or [])
    header=Text('DEVTOOL · RUN ', style='bold')
    header.append(str(remote.get('id') or '?'),style='cyan')
    header.append('   ' + str(remote.get('conclusion') or remote.get('status') or 'pending').upper(),
                  style='green' if remote.get('conclusion')=='success' else
                        'red' if remote.get('conclusion') in ('failure','cancelled','timed_out') else 'yellow')
    body: list[Any]=[header,Text(f"Jobs {sum(j.get('status')=='completed' for j in jobs)}/{len(jobs)}  ·  Attempt {remote.get('run_attempt','?')}",style='dim')]
    for i,job in enumerate(jobs[:30]):
        t=Text('› ' if i==selected else '  ')
        t.append(_truncate(job.get('name') or 'job',width-30), style='bold' if i==selected else '')
        t.append('  ')
        t.append_text(_mark(job.get('status'),job.get('conclusion')))
        body.append(t)
        if detail and i==selected:
            for step in (job.get('steps') or [])[:25]:
                st=Text('    '+_truncate(step.get('name') or 'step',width-30)+'  ')
                st.append_text(_mark(step.get('status'),step.get('conclusion')))
                body.append(st)
    if not jobs:body.append(Text('Waiting for job details…',style='dim'))
    if logs:
        body.append(Text('Log inspection: native run URL · no silent remote archive fetch',style='dim'))
        if remote.get('html_url'):
            body.append(Text(_truncate(remote['html_url'],width-5),style='cyan underline'))
    if verbose:
        body.append(Text('Source '+str(remote.get('head_sha') or '')[:12]+' · source binding '+str(observation.get('source_binding') or 'unverified'),style='dim'))
    body.append(Text('j/k select  Enter steps  l logs  v verbose  r refresh  q detach',style='dim'))
    return Group(Panel(Group(*body),border_style='red' if remote.get('conclusion')=='failure' else 'cyan',padding=(0,1),expand=False))


class MonitorDetached(RuntimeError):
    """Operator left the foreground observer; CI qualification remains pending."""


class NativeRichMonitor:
    """Bounded inline Rich Live adapter, not an executor or external UI host."""
    def __init__(self, *, kind: str = 'ci', enabled: bool | None = None,
                 console: Console | None = None, env: Mapping[str,str] | None = None):
        self.kind=kind
        self.enabled=terminal_rich_enabled(env=env) if enabled is None else enabled
        self.console=console or Console(force_terminal=bool(self.enabled),
                                        width=shutil.get_terminal_size((80,24)).columns,
                                        no_color=('NO_COLOR' in (env or os.environ)))
        self.last: dict[str,Any]={}
        self.selected=0
        self.verbose=False
        self.detail=False
        self.detached=False
        self.refresh_requested=False
        self.log_requested=False
        self._live: Live | None=None
        self._keyboard=None

    def render(self) -> Group:
        kwargs={'width':_width(self.console),'selected':self.selected,
                'verbose':self.verbose,'detail':self.detail,'logs':self.log_requested}
        return actions_run_view(self.last,**kwargs) if self.kind=='run' else ci_snapshot_view(self.last,**kwargs)

    def __enter__(self):
        if self.enabled:
            self._live=Live(self.render(),console=self.console,refresh_per_second=4,
                            auto_refresh=False,transient=False,screen=False)
            self._live.start()
            # Optional keyboard path; no keyboard hooks when attached to a pipe.
            self._keyboard=_Keyboard(self)
            self._keyboard.start()
        return self

    def __exit__(self,ex_type,exc,tb):
        if self._keyboard is not None:self._keyboard.stop()
        if self._live is not None:
            self._live.stop()
            self._live=None
        return False

    def observe(self,source:Mapping[str,Any]) -> None:
        self.last=dict(source)
        if self.detached:
            raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')
        self.selected=max(0,min(self.selected,len((self.last.get('jobs') if self.kind=='run' else self.last.get('runs')) or [])-1))
        self.refresh()

    def refresh(self)->None:
        if self.enabled and self._live is not None:
            # Fresh attached-input terminal width, including SSH/Termux resize.
            try:
                new_width = os.get_terminal_size(sys.stdin.fileno()).columns
            except (OSError,AttributeError,ValueError):
                new_width = shutil.get_terminal_size((80,24)).columns
            self.console.width = max(40,min(180,new_width))
            self._live.update(self.render(),refresh=True)

    def wait_interval(self,seconds:float)->None:
        """Native observe_ci sleep callback: bounded manual refresh/detach.

        Does NOT poll GitHub, fabricate an observation or alter the CI verdict.
        The native registrar retains its original bounded poll count.
        """
        if not self.enabled:
            time.sleep(seconds)
            return
        deadline=time.monotonic()+max(0.0,float(seconds))
        while time.monotonic()<deadline:
            if self.detached:
                raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')
            if self.refresh_requested:
                self.refresh_requested=False
                return
            time.sleep(min(0.15,max(0.0,deadline-time.monotonic())))
        if self.detached:
            raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')

    def key(self,key:str)->None:
        size=len((self.last.get('jobs') if self.kind=='run' else self.last.get('runs')) or [])
        if key in ('j','down'):self.selected=min(max(0,size-1),self.selected+1)
        elif key in ('k','up'):self.selected=max(0,self.selected-1)
        elif key in ('\r','\n','enter'):self.detail=not self.detail
        elif key=='v':self.verbose=not self.verbose
        elif key=='l':self.detail=True;self.log_requested=not self.log_requested
        elif key=='r':self.refresh_requested=True
        elif key=='q':self.detached=True
        self.refresh()


class _Keyboard:
    """Best-effort local POSIX keys. Always restore terminal attributes."""
    def __init__(self,monitor:NativeRichMonitor):
        self.monitor=monitor
        self.stop_requested=False
        self.thread=None
        self.saved=None
        self.fd=None

    def start(self)->None:
        if os.name != 'posix':return
        try:
            import termios,tty
            if not sys.stdin.isatty():return
            fd=sys.stdin.fileno()
            self.saved=termios.tcgetattr(fd)
            tty.setcbreak(fd)
            self.fd=fd
            from threading import Thread
            self.thread=Thread(target=self._run,daemon=True,name='devtool-rich-monitor-keys')
            self.thread.start()
        except (OSError,ValueError,AttributeError):
            self.stop()

    def _run(self)->None:
        import select
        while not self.stop_requested:
            try:
                ready,_,_=select.select([self.fd],[],[],0.15)
                if not ready:continue
                value=os.read(self.fd,1).decode('utf-8','ignore')
                if value=='\x1b':
                    more,_,_=select.select([self.fd],[],[],0.02)
                    if more:
                        nxt=os.read(self.fd,2)
                        value='up' if nxt==b'[A' else 'down' if nxt==b'[B' else ''
                if value:self.monitor.key(value)
            except (OSError,ValueError):
                return

    def stop(self)->None:
        self.stop_requested=True
        if self.thread is not None:self.thread.join(timeout=0.4)
        if self.saved is not None and self.fd is not None:
            try:
                import termios
                termios.tcsetattr(self.fd,termios.TCSADRAIN,self.saved)
            except (OSError,ValueError):pass
        self.saved=None


__all__=['NativeRichMonitor','MonitorDetached','terminal_rich_enabled',
         'ci_snapshot_view','actions_run_view']
