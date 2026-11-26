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
import threading
from typing import Any, Mapping, Callable

from rich.box import SIMPLE
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from .inline_live import InlineLive
from .posix_input import KeyboardWorker as _Keyboard


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
        try: return max(1, int(console.width))
        except (ValueError, TypeError): pass
    return max(1, shutil.get_terminal_size((80, 24)).columns)


def ci_snapshot_view(snapshot: Mapping[str, Any], *, width: int = 80,
                     selected: int = 0, verbose: bool = False,
                     detail: bool = False, logs: bool = False,
                     log_lines: tuple[str,...] = (), log_page: int = 0,
                     log_error: str = "", focus: str = "overview",
                     focus_page: int = 0, height: int | None = None) -> Group:
    """Shared M6 vertical Rich projection of existing CI evidence."""
    from .m6_live import build_live_view
    return build_live_view("ci", snapshot, width=width, selected=selected,
        detail=detail, verbose=verbose, logs=logs, log_lines=log_lines,
        log_page=log_page, log_error=log_error,
        focus=focus, focus_page=focus_page, height=height)


def actions_run_view(observation: Mapping[str, Any], *, width: int = 80,
                     selected: int = 0, verbose: bool = False,
                     detail: bool = False, logs: bool = False,
                     log_lines: tuple[str,...] = (), log_page: int = 0,
                     log_error: str = "", focus: str = "overview",
                     focus_page: int = 0, height: int | None = None) -> Group:
    """Shared M6 vertical Rich projection of an exact provider run."""
    from .m6_live import build_live_view
    return build_live_view("run", observation, width=width, selected=selected,
        detail=detail, verbose=verbose, logs=logs, log_lines=log_lines,
        log_page=log_page, log_error=log_error,
        focus=focus, focus_page=focus_page, height=height)


def _log_panel(lines: tuple[str,...], page: int, *, width: int, error: str = "") -> Panel:
    """Only show sanitized, bounded log lines provided by the native read adapter."""
    output = [Text('Remote GitHub logs · read-only', style='bold')]
    if error:
        output.append(Text(_truncate(error, width-6), style='bold yellow'))
    elif lines:
        offset = min(max(0,page)*18, max(0,len(lines)-1))
        shown = lines[offset:offset+18]
        output.append(Text(f'Lines {offset+1}–{offset+len(shown)} of {len(lines)}',style='dim'))
        for line in shown:
            output.append(Text(_truncate(line,width-6),style='dim'))
        output.append(Text('[ / ] pages  ·  l close  ·  q exit',style='dim'))
    else:
        output.append(Text('Press l to fetch the selected run logs; no background downloads.',style='yellow'))
    return Panel(Group(*output),border_style='yellow',padding=(0,1),expand=False)


class MonitorDetached(RuntimeError):
    """Operator left the foreground observer; CI qualification remains pending."""


class NativeRichMonitor:
    """Bounded inline Rich Live adapter, not an executor or external UI host."""
    def __init__(self, *, kind: str = 'ci', enabled: bool | None = None,
                 console: Console | None = None, env: Mapping[str,str] | None = None,
                 log_fetcher: Callable[[Mapping[str,Any]],tuple[str,...]] | None = None):
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
        self.ui_feedback=''
        self.log_requested=False
        self.focus='overview'
        self.focus_before_logs='overview'
        self.focus_page=0
        self._feedback_deadline=0.0
        self._live: InlineLive | None=None
        self._keyboard=None
        self.log_fetcher=log_fetcher
        self.log_lines:tuple[str,...]=()
        self.log_page=0
        self.log_error=''
        self._pending_log_fetch=None
        self._ui_dirty=False
        self._log_identity=None
        self._log_generation=0
        self._state_lock=threading.RLock()

    def render(self) -> Group:
        with self._state_lock:
            return self._render_locked()

    def _render_locked(self) -> Group:
        kwargs={'width':_width(self.console),'selected':self.selected,
                'verbose':self.verbose,'detail':self.detail,'logs':self.log_requested,
                'log_lines':self.log_lines,'log_page':self.log_page,
                'log_error':self.log_error,
                'focus':self.focus,'focus_page':self.focus_page,'height':self.console.height if self._live is not None else None}
        presentation = dict(self.last) if self.ui_feedback else self.last
        if self.ui_feedback:
            presentation['m6_ui_feedback'] = self.ui_feedback
        primary = actions_run_view(presentation,**kwargs) if self.kind=='run' else ci_snapshot_view(presentation,**kwargs)
        if self.enabled:
            from .m6_live import BoundedLiveFrame
            return BoundedLiveFrame(primary)
        return primary

    def __enter__(self):
        if self.enabled:
            try:
                self._live=InlineLive(self.render(), console=self.console)
                if self._live.resize():self._live.frame=self.render()
                self._live.start()
                self._keyboard=_Keyboard(self)
                self._keyboard.start()
            except Exception as exc:
                self._presentation_failed(exc)
        return self

    def _close(self):
        keyboard,self._keyboard=self._keyboard,None
        live,self._live=self._live,None
        try:
            if keyboard is not None:keyboard.stop()
        finally:
            if live is not None:live.stop()

    def _presentation_failed(self, exc):
        self.enabled=False
        try:self._close()
        except Exception:pass
        print('M6_PRESENTATION_WARNING '+type(exc).__name__+'; native execution/result unaffected',file=sys.stderr,flush=True)

    def __exit__(self,ex_type,exc,tb):
        try:self._close()
        except Exception as error:self._presentation_failed(error)
        return False

    def observe(self,source:Mapping[str,Any]) -> None:
        with self._state_lock:
            if self.ui_feedback in ('REFRESH_REQUESTED','REFRESH_CONSUMED'):
                self.ui_feedback='NATIVE_OBSERVATION_RENDERED'
                self._feedback_deadline=time.monotonic()+1.0
            elif self.ui_feedback == 'NATIVE_OBSERVATION_RENDERED':
                self.ui_feedback=''
            prior=self._selection_identity()
            log_before=self._selected_log_identity()
            self.last=dict(source)
            if self.detached:
                raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')
            rows=self._selection_rows()
            matches=[i for i,row in enumerate(rows) if self._row_selection_identity(row)==prior]
            self.selected=matches[0] if prior is not None and len(matches)==1 else max(0,min(self.selected,len(rows)-1))
            if self._selected_log_identity()!=log_before:
                self._invalidate_logs('Source or attempt changed; close and reopen logs to request this run')
        self._consume_log_fetch()
        self.refresh()

    def _selection_rows(self):
        return (self.last.get('jobs') if self.kind=='run' else self.last.get('runs')) or []

    def _row_selection_identity(self,row):
        return (row.get('id'),row.get('name'),row.get('attempt',row.get('run_attempt')),row.get('head_sha'))

    def _selection_identity(self):
        rows=self._selection_rows()
        return self._row_selection_identity(rows[self.selected]) if 0<=self.selected<len(rows) else None

    def _selected_log_row(self):
        if self.kind=='run':
            return self.last.get('remote') or {}
        rows=self.last.get('runs') or []
        return rows[self.selected] if 0<=self.selected<len(rows) else {}

    def _selected_log_identity(self):
        row=self._selected_log_row()
        return (row.get('id'),row.get('attempt',row.get('run_attempt')),
                row.get('head_sha'),row.get('workflow_id'),row.get('event'),
                self.last.get('source_sha'),self.last.get('head_sha'),self.last.get('repository'))

    def _invalidate_logs(self,reason=''):
        # Called under state lock; revokes queued and in-flight publication.
        self._log_generation+=1
        self._pending_log_fetch=None
        self._log_identity=None
        self.log_lines=();self.log_page=0;self.log_error=reason

    def _consume_log_fetch(self)->None:
        with self._state_lock:
            request=self._pending_log_fetch
            self._pending_log_fetch=None
            if request is None or not self.enabled or self.detached or not self.log_requested:
                return
            generation,identity,row=request
            if generation!=self._log_generation or identity!=self._selected_log_identity():
                return
            self.log_page=0;self.log_lines=();self.log_error=''
            if not row.get('id') or self.log_fetcher is None:
                self.log_error='No verified run or log provider is available'
                return
            fetch=self.log_fetcher
        # The native foreground thread claims exactly the authorized request.
        # No lock is held across network I/O; keyboard close/detach can revoke
        # publication, and the provider receives only the immutable selection.
        values=();error=''
        try:
            values=fetch(row)
            if not isinstance(values,tuple) or any(not isinstance(v,str) for v in values):
                raise ValueError('Invalid bounded log result')
            values=values[:50000]
        except Exception as exc:
            error='Log fetch refused: '+type(exc).__name__+' · check exact source/permissions'
        with self._state_lock:
            if (generation!=self._log_generation or identity!=self._selected_log_identity()
                    or not self.log_requested or self.detached or not self.enabled):
                return
            self._log_identity=identity
            self.log_lines=values;self.log_error=error
        self.refresh()

    def review_failed(self) -> None:
        """Leave a failed foreground monitor available for explicit log review.

        No network polling occurs here; q exits, preserving native CI failure.
        """
        if (not self.enabled or self._live is None or self._keyboard is None or
                self._keyboard.thread is None or not self._keyboard.thread.is_alive()):
            return
        state = (self.last.get('remote') or {}).get('conclusion') if self.kind=='run' else self.last.get('state')
        if state not in ('failure','timed_out','CHECKS_FAILED'):
            return
        while not self.detached and self._keyboard.thread.is_alive():
            self._consume_log_fetch()
            self.refresh()
            time.sleep(0.12)

    def refresh(self)->None:
        if self.enabled and self._live is not None:
            try:
                fd=sys.stdin.fileno()
            except (OSError,AttributeError,ValueError):
                fd=None
            try:
                self._live.resize(fd)
                self._live.update(self.render(),refresh=True)
            except Exception as exc:
                self._presentation_failed(exc)

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
            self._consume_log_fetch()
            if self._live is not None:
                try:
                    if self._live.resize():self.refresh()
                except Exception as exc:self._presentation_failed(exc)
            if self._ui_dirty:
                # Only the native main observer renders to the PTY.
                self._ui_dirty=False
                self.refresh()
            if (self.ui_feedback and self._feedback_deadline and
                    time.monotonic() >= self._feedback_deadline):
                self.ui_feedback=''
                self._feedback_deadline=0.0
                self.refresh()
            if self.detached:
                raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')
            if self.refresh_requested:
                self.refresh_requested=False
                self.ui_feedback='REFRESH_CONSUMED'
                self._feedback_deadline=time.monotonic()+1.0
                self.refresh()
                return
            time.sleep(min(0.15,max(0.0,deadline-time.monotonic())))
        if self.detached:
            raise MonitorDetached('Foreground UI detached; native session retained; CI NOT qualified')

    def key(self,key:str)->None:
        with self._state_lock:
            self._key_locked(key)
        # Never render while holding the input state lock.
        if self._keyboard is None or threading.current_thread() is not self._keyboard.thread:
            self._ui_dirty=False
            self.refresh()

    def _key_locked(self,key:str)->None:
        old_identity=self._selected_log_identity()
        size=len((self.last.get('jobs') if self.kind=='run' else self.last.get('runs')) or [])
        if key in ('j','down'):self.selected=min(max(0,size-1),self.selected+1)
        elif key in ('k','up'):self.selected=max(0,self.selected-1)
        elif key in ('\r','\n','enter'):self.detail=not self.detail
        elif key=='v':self.verbose=not self.verbose
        elif key in ('0','h'):
            self.focus='overview';self.focus_page=0
        elif key in ('1','2','3','4','5'):
            self.focus={'1':'identity','2':'stages','3':'events',
                        '4':'output','5':'evidence'}[key]
            self.focus_page=0
        elif key=='\t':
            pages=('overview','identity','stages','events','output','evidence')
            self.focus=pages[(pages.index(self.focus)+1)%len(pages)]
            self.focus_page=0
        elif key=='l':
            self.detail=True
            self.log_requested=not self.log_requested
            if self.log_requested:
                self.focus_before_logs=self.focus
                self.focus='output'
                self._invalidate_logs()
                self._pending_log_fetch=(self._log_generation,self._selected_log_identity(),dict(self._selected_log_row()))
            else:
                self._invalidate_logs()
                self.focus=self.focus_before_logs
        elif key=='[':
            if self.log_requested:self.log_page=max(0,self.log_page-1)
            else:self.focus_page=max(0,self.focus_page-1)
        elif key==']':
            if self.log_requested:
                from .m6_live import log_page_size, log_display_rows
                width=_width(self.console)
                fragments=log_display_rows(self.log_lines,width)
                self.log_page=min(max(0,(len(fragments)-1)//log_page_size(width,self.console.height if self._live is not None else None)),self.log_page+1)
            else:
                self.focus_page=min(1000,self.focus_page+1)
        elif key=='r':self.refresh_requested=True;self.ui_feedback='REFRESH_REQUESTED'
        elif key=='q':
            self.detached=True;self._invalidate_logs()
        if old_identity!=self._selected_log_identity():
            self._invalidate_logs('Selection changed; close and reopen logs to request this run' if self.log_requested else '')
        # Keyboard worker updates state only; never prints/repositions Live.
        # The next bounded native wait/observer cycle owns redrawing.
        self._ui_dirty=True


__all__=['NativeRichMonitor','MonitorDetached','terminal_rich_enabled',
         'ci_snapshot_view','actions_run_view']
