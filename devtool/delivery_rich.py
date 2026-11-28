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
from copy import deepcopy
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
                 log_fetcher: Callable[[Mapping[str,Any]],tuple[str,...]] | None = None,
                 run_fetcher: Callable[[Mapping[str,Any]],Mapping[str,Any]] | None = None,
                 job_log_fetcher: Callable[[Mapping[str,Any],Mapping[str,Any]],tuple[str,...]] | None = None):
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
        self.run_fetcher=run_fetcher
        self.job_log_fetcher=job_log_fetcher
        self._drill_run=None
        self._drill_binding=None
        self._drill_jobs=[]
        self._drill_page='jobs'
        self._drill_job=0
        self._drill_step=0
        self._drill_detail=False
        self._drill_back='jobs'
        self._drill_identity_back='jobs'
        self._drill_lines=()
        self._drill_error=''
        self._drill_generation=0
        self._drill_request=None
        self._drill_loading=False
        self._drill_anchors=[]
        self._drill_cursor=None
        self._drill_focus_before='overview'
        self._drill_notice=''


    def render(self) -> Group:
        with self._state_lock:
            return self._render_locked()

    def _render_locked(self) -> Group:
        if self.kind=='ci' and self.run_fetcher is not None and self.enabled and (
                self._drill_run is not None or (self.focus=='stages' and not self.log_requested)):
            from .m6_live import BoundedLiveFrame
            primary=self._render_drilldown()
            return BoundedLiveFrame(primary) if self.enabled else primary
        kwargs={'width':_width(self.console),'selected':self.selected,
                'verbose':self.verbose,'detail':self.detail,'logs':self.log_requested,
                'log_lines':self.log_lines,'log_page':self.log_page,
                'log_error':self.log_error,
                'focus':self.focus,'focus_page':self.focus_page,'height':self.console.height if self._live is not None else None}
        presentation = dict(self.last) if self.ui_feedback or self.run_fetcher is not None else self.last
        if self.run_fetcher is not None:
            presentation['m6_drilldown_available']=True
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
            if self._drill_run is not None and not self._drill_current():
                self._close_drilldown('Run identity changed; select the current run again')
        self._consume_drill_fetch()
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

    @staticmethod
    def _run_key(row: Mapping[str,Any]):
        return (row.get('id'),row.get('attempt',row.get('run_attempt')),
                row.get('head_sha'),row.get('workflow_id'),row.get('event'))

    def _drill_context(self):
        return (self.last.get('repository'),self.last.get('source_sha'),
                self.last.get('head_sha'),self.last.get('session_id'))

    def _drill_current(self):
        if self._drill_run is None or self._drill_binding is None:
            return False
        context,identity=self._drill_binding
        matches=[row for row in self._selection_rows() if row.get('id')==identity[0]]
        return context==self._drill_context() and len(matches)==1 and self._run_key(matches[0])==identity

    def _clear_drill_request(self):
        self._drill_generation+=1
        self._drill_request=None
        self._drill_loading=False

    def _reset_drill_cursor(self):
        from .workspace_viewport import LogCursor
        self._drill_cursor=LogCursor()
        self._drill_anchors=[]

    def _close_drilldown(self,notice=''):
        self._clear_drill_request()
        self._drill_run=None
        self._drill_binding=None
        self._drill_jobs=[]
        self._drill_lines=()
        self._drill_error=''
        self._drill_notice=notice
        self.focus='stages' if notice else self._drill_focus_before
        self._reset_drill_cursor()

    def _queue_drill(self,kind):
        self._clear_drill_request()
        self._drill_error=''
        self._drill_loading=True
        self._drill_request=(self._drill_generation,self._drill_binding,kind,
                             deepcopy(self._drill_run),
                             deepcopy(self._drill_selected_job()) if kind=='logs' else None)

    def _open_drilldown(self):
        row=self._selected_log_row()
        if not row:
            self._drill_notice='No observed workflow run selected'
            self.focus='stages'
            return
        rid,attempt,sha,workflow,event=self._run_key(row)
        import re
        if (type(rid) is not int or rid<=0 or type(attempt) is not int or attempt<=0
                or type(workflow) is not int or workflow<=0 or not isinstance(sha,str)
                or not re.fullmatch(r'[0-9a-f]{40}',sha) or not isinstance(event,str) or not event
                or not isinstance(self._drill_context()[0],str)
                or not isinstance(self._drill_context()[1],str)
                or not re.fullmatch(r'[0-9a-f]{40}',self._drill_context()[1])
                or not isinstance(self._drill_context()[3],str)
                or not re.fullmatch(r'[0-9a-f]{32}',self._drill_context()[3])
                or sum(x.get('id')==rid for x in self._selection_rows())!=1):
            self._drill_notice='Selected run lacks exact source/attempt identity';self.focus='stages';return
        self._drill_run=deepcopy(row)
        self._drill_binding=(self._drill_context(),self._run_key(row))
        self._drill_focus_before=self.focus
        self._drill_notice=''
        self._drill_jobs=[]
        self._drill_job=0
        self._drill_step=0
        self._drill_detail=False
        self._drill_page='jobs'
        self._drill_lines=()
        self._reset_drill_cursor()
        self._queue_drill('jobs')

    def _drill_selected_job(self):
        return self._drill_jobs[self._drill_job] if 0<=self._drill_job<len(self._drill_jobs) else None

    def _drill_steps(self):
        return (self._drill_selected_job() or {}).get('steps') or []

    def _consume_drill_fetch(self):
        # Same foreground observer and lock discipline as the existing log
        # route. Keys enqueue read intents; render never contacts a provider.
        with self._state_lock:
            request=self._drill_request
            self._drill_request=None
            if request is None or not self.enabled or self.detached or not self._drill_current():
                return
            generation,binding,kind,run,job=request
            if generation!=self._drill_generation or binding!=self._drill_binding:
                return
            fetch=self.run_fetcher if kind=='jobs' else self.job_log_fetcher
        value=None;error=''
        try:
            if fetch is None:
                raise ValueError('Read-only provider unavailable')
            value=fetch(run) if kind=='jobs' else fetch(run,job)
            if kind=='jobs':
                # The native workspace adapter has already independently
                # checked repository, workflow, source, attempt and membership.
                if (not isinstance(value,Mapping) or value.get('kind')!='run-jobs'
                        or value.get('read_only') is not True or value.get('qualified_release') is not False
                        or not isinstance(value.get('run'),Mapping)
                        or self._run_key(value['run'])!=self._run_key(run)
                        or not isinstance(value.get('items'),list)):
                    raise ValueError('Run detail identity mismatch')
                expected_repo=binding[0][0]
                if (not isinstance(expected_repo,str) or not isinstance(value.get('repository'),str)
                        or value['repository'].casefold()!=expected_repo.casefold()):
                    raise ValueError('Run detail repository mismatch')
                jobs=value['items'];seen=set()
                if len(jobs)>200:raise ValueError('Job inventory exceeds native bound')
                for item in jobs:
                    if (not isinstance(item,Mapping) or type(item.get('id')) is not int or item['id']<=0
                            or item['id'] in seen or item.get('run_id')!=run['id']
                            or item.get('run_attempt')!=self._run_key(run)[1]
                            or not isinstance(item.get('steps'),list)):
                        raise ValueError('Invalid job membership')
                    seen.add(item['id'])
                    steps=set()
                    for step in item['steps']:
                        if (not isinstance(step,Mapping) or type(step.get('number')) is not int
                                or step['number']<=0 or step['number'] in steps):
                            raise ValueError('Invalid step membership')
                        steps.add(step['number'])
            elif not isinstance(value,tuple) or len(value)>50000 or any(not isinstance(x,str) for x in value):
                raise ValueError('Invalid bounded job logs')
        except Exception as exc:
            # Never include provider exception text (may contain credentials).
            error='Read refused: '+type(exc).__name__+' · verify source/permissions'
        with self._state_lock:
            if (generation!=self._drill_generation or binding!=self._drill_binding
                    or not self._drill_current() or self.detached or not self.enabled):
                return
            self._drill_loading=False
            self._drill_error=error
            if not error and kind=='jobs':
                old=self._drill_selected_job()
                self._drill_jobs=deepcopy(value['items'])
                matches=[i for i,x in enumerate(self._drill_jobs) if old and x['id']==old['id']]
                self._drill_job=matches[0] if len(matches)==1 else 0
                self._drill_step=0
            elif not error:
                from .m6_live import safe
                # Complete native lines are retained for paging, not truncated
                # to the compact run-log preview's display limit.
                self._drill_lines=tuple(safe(x,limit=len(x)) for x in value)
        self.refresh()

    def _drill_dimensions(self):
        return _width(self.console),max(1,self.console.height-3)

    def _render_drilldown(self):
        from .m6_live import safe
        from .workspace_viewport import render_workspace, LogCursor
        width,height=self._drill_dimensions()
        native=str(self.last.get('state') or 'UNVERIFIED')
        # A green provider job is not a native qualification verdict.
        verdict='CI '+safe(native,limit=80)
        if native=='REQUIRED_CHECKS_PASSED' and self.last.get('checks_qualified') is not True:
            verdict='CI UNVERIFIED'
        if self._drill_run is None:
            return render_workspace(width=width,height=height,heading='M6 · NATIVE LIVE · PR CHECKS',
                status=verdict,warning=self._drill_notice or (self.ui_feedback.replace('_',' ') if self.ui_feedback else ''),tab='PR checks',snapshot={'kind':'workflow runs'},
                rows=self._selection_rows(),selected=self.selected,
                footer='↑↓ Enter open · 0 home · r · q')
        run=self._drill_run
        attempt=self._run_key(run)[1]
        heading=f'M6 · RUN {safe(run.get("id"))} / #{safe(attempt)}'
        job=self._drill_selected_job()
        job_label='Job '+safe(job.get('id')) if job else 'No job'
        page=self._drill_page
        snapshot={'kind': 'run jobs' if page=='jobs' else f'{job_label} {page}',
                  'message':'No observed records', 'read_only':False}
        warning=self._drill_error or ('Reading exact run attempt…' if self._drill_loading else '')
        if page=='jobs' and len(self._drill_jobs)>=200 and not warning:
            warning='Native 200-job limit reached; additional jobs possible'
        if page=='jobs':
            rows=self._drill_jobs if not self._drill_loading and not self._drill_error else []
            selected=self._drill_job
        elif page=='steps':
            rows=self._drill_steps();selected=self._drill_step
        elif page=='identity':
            rows=[];selected=0;snapshot['log_lines']=self._drill_identity_lines()
        else:
            rows=[];selected=0;snapshot['log_lines']=self._drill_lines
        if page=='jobs' and not rows and not warning:
            snapshot['message']='No jobs observed for this attempt'
        if page=='steps' and not rows:
            snapshot['message']='No steps observed for this job'
        # Include canonical source and workflow in the selected run details,
        # with full identity retained in the native observation, not guessed.
        status=f'{verdict} · {safe(run.get("head_sha"),limit=12)}'
        if page=='jobs' and not warning:
            warning='Workflow '+safe(run.get('workflow_id'))+' · '+safe(run.get('name'),limit=160)
        return render_workspace(width=width,height=height,heading=heading,status=status,
            warning=warning,tab=page,snapshot=snapshot,rows=rows,selected=selected,
            detail=self._drill_detail if page=='steps' else False,
            cursor=self._drill_cursor or LogCursor(),
            footer='q b ↑↓ Enter l v [ ] r')

    def _drill_identity_lines(self):
        from .m6_live import safe
        run=self._drill_run or {}
        lines=[f'Repository: {safe(self._drill_context()[0])}',
               f'PR source: {safe(self._drill_context()[1])}',
               f'Run source: {safe(run.get("head_sha"))}',
               f'Workflow: {safe(run.get("workflow_id"))}',
               f'Run: {safe(run.get("id"))}',
               f'Attempt: {safe(self._run_key(run)[1])}',
               f'Event: {safe(run.get("event"))}',
               f'Name: {safe(run.get("name"),limit=300)}']
        job=self._drill_selected_job()
        if job:
            lines.extend(f'Job {k}: {safe(job.get(k))}' for k in ('id','name','status','conclusion','started_at','completed_at'))
        return tuple(lines)

    def _drill_page_move(self,forward):
        from .workspace_viewport import log_page,LogCursor,page_capacity,detail_lines
        width,height=self._drill_dimensions()
        if self._drill_page in ('logs','identity') or self._drill_detail:
            if not forward:
                if self._drill_anchors:self._drill_cursor=self._drill_anchors.pop()
                return
            if self._drill_page=='logs':lines=self._drill_lines
            elif self._drill_page=='identity':lines=self._drill_identity_lines()
            else:
                steps=self._drill_steps()
                lines=detail_lines(steps[self._drill_step]) if steps else ()
            # render_workspace uses three chrome lines, one footer, and one
            # line-position indicator when space permits. Error adds a line.
            warning=bool(self._drill_error or self._drill_loading)
            available=max(0,height-min(3+int(warning),max(0,height-2))-1)
            capacity=max(1,available-int(available>1))
            cursor=self._drill_cursor or LogCursor()
            _,next_cursor=log_page(lines,cursor,width,capacity)
            if next_cursor is not None:
                self._drill_anchors.append(cursor);self._drill_cursor=next_cursor
            return
        count=len(self._drill_jobs if self._drill_page=='jobs' else self._drill_steps())
        field='_drill_job' if self._drill_page=='jobs' else '_drill_step'
        step=max(1,page_capacity(height,True,False)-1)
        setattr(self,field,max(0,min(count-1,getattr(self,field)+(step if forward else -step))))

    def _drill_key(self,key):
        if key in ('b','\x1b','\x7f','h','0'):
            if self._drill_page=='logs':
                self._clear_drill_request();self._drill_lines=();self._drill_error='';self._drill_page=self._drill_back
            elif self._drill_page=='identity':self._drill_page=self._drill_identity_back
            elif self._drill_detail:self._drill_detail=False
            elif self._drill_page=='steps':self._drill_page='jobs'
            else:self._close_drilldown()
            self._reset_drill_cursor()
        elif key in ('j','down','k','up') and self._drill_page in ('jobs','steps'):
            field='_drill_job' if self._drill_page=='jobs' else '_drill_step'
            count=len(self._drill_jobs if self._drill_page=='jobs' else self._drill_steps())
            setattr(self,field,max(0,min(count-1,getattr(self,field)+(1 if key in ('j','down') else -1))))
            self._reset_drill_cursor()
        elif key in ('\r','\n','enter') and not self._drill_loading and not self._drill_error:
            if self._drill_page=='jobs' and self._drill_selected_job() is not None:
                self._drill_page='steps';self._drill_step=0;self._drill_detail=False;self._reset_drill_cursor()
            elif self._drill_page=='steps' and self._drill_steps():
                self._drill_detail=not self._drill_detail;self._reset_drill_cursor()
        elif key=='v' and not self._drill_loading:
            if self._drill_page=='identity':self._drill_key('b')
            elif self._drill_page in ('jobs','steps'):
                self._drill_identity_back=self._drill_page;self._drill_page='identity';self._reset_drill_cursor()
        elif key=='l':
            if self._drill_page=='logs':self._drill_key('b')
            elif self._drill_page in ('jobs','steps') and self._drill_selected_job() is not None and not self._drill_loading and not self._drill_error:
                self._drill_back=self._drill_page;self._drill_page='logs';self._drill_lines=();self._reset_drill_cursor();self._queue_drill('logs')
        elif key=='r':
            if self._drill_page=='identity':return
            if self._drill_page=='logs':
                self._drill_lines=();self._reset_drill_cursor();self._queue_drill('logs')
            else:
                self._drill_page='jobs';self._drill_detail=False;self._reset_drill_cursor();self._queue_drill('jobs')
        elif key in ('[',']'):self._drill_page_move(key==']')
        elif key=='q':
            self.detached=True;self._close_drilldown();self._invalidate_logs()
        self._ui_dirty=True

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

        No CI polling occurs here; explicit drill-down reads may finish.
        A completed success only waits if the operator already opened a run.
        q/back leave the native result untouched.
        """
        if (not self.enabled or self._live is None or self._keyboard is None or
                self._keyboard.thread is None or not self._keyboard.thread.is_alive()):
            return
        state = (self.last.get('remote') or {}).get('conclusion') if self.kind=='run' else self.last.get('state')
        failed=state in ('failure','timed_out','CHECKS_FAILED')
        if not failed and self._drill_run is None:
            return
        while not self.detached and self._keyboard.thread.is_alive() and (failed or self._drill_run is not None):
            self._consume_drill_fetch()
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
            self._consume_drill_fetch()
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
        if self._drill_run is not None:
            self._drill_key(key)
            return
        interactive_runs=self.kind=='ci' and self.run_fetcher is not None and self.enabled
        if interactive_runs and key in ('\r','\n','enter') and not self.log_requested:
            self._open_drilldown();self._ui_dirty=True;return
        if interactive_runs and key in ('j','down','k','up') and not self.log_requested:
            self.focus='stages';self.focus_page=0
        old_identity=self._selected_log_identity()
        size=len((self.last.get('jobs') if self.kind=='run' else self.last.get('runs')) or [])
        if key in ('j','down'):self.selected=min(max(0,size-1),self.selected+1)
        elif key in ('k','up'):self.selected=max(0,self.selected-1)
        elif key in ('\r','\n','enter'):self.detail=not self.detail
        elif key=='v':self.verbose=not self.verbose
        elif key in ('0','h','b','\x1b','\x7f'):
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
            if interactive_runs and self.focus=='stages' and not self.log_requested:self.selected=max(0,self.selected-max(1,self.console.height-9))
            elif self.log_requested:self.log_page=max(0,self.log_page-1)
            else:self.focus_page=max(0,self.focus_page-1)
        elif key==']':
            if interactive_runs and self.focus=='stages' and not self.log_requested:self.selected=min(max(0,size-1),self.selected+max(1,self.console.height-9))
            elif self.log_requested:
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
