"""UX-RICH03: foreground Rich workspace over native, read-only Devtool state.

No parallel execution authority, scheduler, webhook, SSH transport, or CI evaluator.
The only periodic work is an interactive terminal refresh of authoritative
snapshots. SSH requests are *never* automatic without an explicit local key.
UI preferences are private, source-scoped and do not contain run receipts.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import copy
import json
import os
from queue import Empty, Queue
from pathlib import Path
import subprocess
import sys
import threading
import tempfile
import time
from typing import Any

from rich.console import Console, Group
from .inline_live import InlineLive, terminal_geometry
from .posix_input import PosixInput

from .operator_rich import (operator_enabled, operator_items, operator_drilldown,
                            _secure)
from .workspace_viewport import LogCursor, detail_lines, log_page, render_workspace
from .operator_event_feed import (DOMAIN_FILTERS, STATE_FILTERS, native_events,
                                  visible_events, event_key)

TABS = ('actions', 'runs', 'validation', 'ssh', 'events')
_MIN_LOCAL_REFRESH = 2.0
_MIN_SSH_REFRESH = 12.0
_MAX_UI_STATE = 4096


def _source_head(root: Path) -> str:
    """Read-only exact current source identity; do not trust ambient branch text."""
    p = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                       capture_output=True, text=True, timeout=10, check=False,
                       env={**os.environ, 'GIT_NO_REPLACE_OBJECTS': '1',
                            'GIT_TERMINAL_PROMPT': '0'})
    sha = p.stdout.strip()
    if p.returncode or len(sha) != 40 or any(x not in '0123456789abcdef' for x in sha):
        raise RuntimeError('Cannot verify the current Devtool repository HEAD')
    return sha


def _safe_root(root: Path) -> Path:
    root = Path(root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Workspace repository is not a directory')
    return root


def _ui_state_path(root: Path) -> Path:
    base = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state')
    key = hashlib.sha256(os.fsencode(str(root))).hexdigest()[:32]
    return base / 'devtool' / 'operator-ui' / (key + '.json')


def _private_folder(path: Path, *, create: bool) -> bool:
    """Reject links, foreign-owned paths and group/world access, fail closed."""
    if create:
        # Refuse pre-existing symlinks in the owned path; no writes inside repo.
        cursor = path
        while not cursor.exists() and not cursor.is_symlink():
            cursor = cursor.parent
        if cursor.is_symlink():
            return False
        try:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            return False
    if path.is_symlink() or not path.is_dir():
        return False
    # A private leaf under an attacker-controlled symlink is not private.
    # Do not follow any parent symlink when accepting custom XDG_STATE_HOME.
    for parent in (path, *path.parents):
        if parent.is_symlink():
            return False
    try:
        st = path.stat()
        return st.st_uid == os.getuid() and not (st.st_mode & 0o077)
    except OSError:
        return False


def load_ui_state(root: Path, head: str) -> str:
    """Restore only the last selected tab. Never restore mutable run identities."""
    path = _ui_state_path(root)
    if not _private_folder(path.parent, create=False) or path.is_symlink():
        return TABS[0]
    try:
        if not path.is_file() or path.stat().st_size > _MAX_UI_STATE:
            return TABS[0]
        obj = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(obj, dict) or obj.get('version') != 1
                or obj.get('source_sha') != head or obj.get('tab') not in TABS):
            return TABS[0]
        return obj['tab']
    except (OSError, ValueError, UnicodeError):
        return TABS[0]


def save_ui_state(root: Path, head: str, tab: str) -> bool:
    """Only UI preferences; atomic private write, no clipboard/Devtool receipt."""
    if tab not in TABS:
        return False
    path = _ui_state_path(root)
    if not _private_folder(path.parent, create=True):
        return False
    if path.is_symlink():
        return False
    try:
        if path.exists() and (not path.is_file() or path.stat().st_uid != os.getuid()):
            return False
        data = json.dumps({'version':1,'source_sha':head,'tab':tab},sort_keys=True).encode()
        if len(data) > _MAX_UI_STATE:
            return False
        fd, name = tempfile.mkstemp(prefix='.operator-', suffix='.tmp',dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return True
    except OSError:
        return False


def native_actions(root: Path, slug: str | None) -> dict[str, Any]:
    if not slug:
        return {'state':'unconfigured','items':[], 'note':'No GitHub repository selected; local tabs are available'}
    from .actions_workspace import local_recent
    return local_recent(root, slug, limit=30)


def native_runs(root: Path) -> dict[str, Any]:
    """Same run query as `devtool runs`, no catalog creation or hidden launch."""
    from .core_runtime import private_run_root
    from .run_store import RunCatalog, RunQuery
    catalog = RunCatalog.open(private_run_root(root), create=False)
    entries = catalog.query(RunQuery(limit=35))
    rows = []
    for item in entries:
        raw = item.get('run', {}) if isinstance(item, Mapping) else {}
        if not isinstance(raw, Mapping):
            continue
        # Whitelist fields: never surface argv, environment, private paths or tokens.
        row = {key:raw[key] for key in ('run_id','command_id','status','provider',
               'execution_provider','created_at','started_at','finished_at','exit_code')
               if key in raw}
        if row:
            rows.append({'run':row})
    return {'kind':'devtool.run-query','repository':str(root),'runs':rows,
            'count':len(rows),'state':'observed','source':'native RunCatalog.open(create=False)'}


def native_validation(root: Path) -> dict[str, Any]:
    from .validation_sessions import load_active_session, status_payload
    session = load_active_session(root, required=False)
    if session is None:
        return {'state':'no-active-session','phases':[], 'note':'No active validation marker'}
    raw = status_payload(session)
    phases=[]
    for item in raw.get('phases', []):
        if not isinstance(item, Mapping):
            continue
        phases.append({key:item[key] for key in ('name','key','status','state',
                      'depends_on','interruption_count','attempt_count') if key in item})
    return {key:raw[key] for key in ('session_id','status','state','revision','safe_checkpoint',
            'current_phase','failed_phases','skipped_phases','recovery_available') if key in raw} | {
            'phases':phases,'source':'native validation_sessions.status_payload',
            'release_qualified':False}


def native_ssh(root: Path) -> dict[str, Any]:
    """Existing SSH history authority; caller must explicitly opt in to GET."""
    from .ssh_worker import resolve_worker_profile
    from .ssh_history import sessions_payload
    worker = resolve_worker_profile('', repo=str(root))
    raw = sessions_payload(worker,repo_root=root)
    sessions=[]
    for item in raw.get('sessions', []):
        if not isinstance(item,Mapping):continue
        sessions.append({key:item[key] for key in ('name','run_id','state','status','phase',
                          'active','target','return_state','output_state') if key in item})
    return {'kind':'ssh-sessions','profile':raw.get('profile'),'sessions':sessions,
            'active_count':raw.get('active_count'),'state':'observed',
            'note':'Remote status observed; output and commands not fetched',
            'release_qualified':False}


def _row_key(item: object) -> str:
    """Only stable native identity fields; labels are not identity proof."""
    if isinstance(item,Mapping):
        src = item.get('run') if isinstance(item.get('run'),Mapping) else item
        for key in ('run_id','session_id','id','key','event_id'):
            value=src.get(key)
            if value not in (None,''):
                # Reused IDs across kinds must never retain unrelated selections.
                scope=str(src.get('domain') or src.get('kind') or item.get('kind') or '')
                return scope + ':' + key + ':' + str(value)
    return ''


@dataclass
class Workspace:
    root: Path
    head: str
    slug: str | None = None
    tab: str = TABS[0]
    automatic: bool = True
    ssh_auto_consent: bool = False
    snapshots: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    selections: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    refreshed_at: dict[str,float] = field(default_factory=dict)
    details: set[str] = field(default_factory=set)
    source_drift: bool = False
    remote_detail: Mapping[str,Any] | None = None
    last_source_check: float = float('-inf')
    source_issue: str = ''
    ssh_pending: bool = False
    events_pending: bool = False
    event_highlights: set[str] = field(default_factory=set)
    event_domain: str = 'all'
    event_state: str = 'all'
    event_query: str = ''
    event_editing: bool = False
    event_draft: str = ''
    auth_mode: str = 'auto'
    remote_selected: int = 0
    remote_history: list = field(default_factory=list)
    remote_generation: int = 0
    remote_pending: bool = False
    remote_scope: str = ""
    remote_results: Queue = field(default_factory=lambda: Queue(maxsize=1),repr=False)
    help_open: bool = False
    cursor: LogCursor = field(default_factory=LogCursor)
    page_history: list[LogCursor] = field(default_factory=list)
    help_restore: tuple | None = None
    viewport_width: int = 80
    viewport_height: int = 24

    def __post_init__(self) -> None:
        self.root = _safe_root(self.root)
        if self.tab not in TABS:raise ValueError('Invalid selected operator tab')
        if not self.head or len(self.head)!=40:raise ValueError('Missing exact HEAD')

    def check_source(self, *, now: float | None = None) -> bool:
        """Check source even while manually paused; never trust stale receipts."""
        if self.source_drift:
            return False
        moment=time.monotonic() if now is None else now
        if moment - self.last_source_check < _MIN_LOCAL_REFRESH:
            return not self.source_issue
        self.last_source_check=moment
        try:
            actual=_source_head(self.root)
        except (RuntimeError,OSError,subprocess.SubprocessError):
            self._clear_remote()
            self.source_issue='HEAD verification unavailable; snapshots are stale'
            return False
        if actual != self.head:
            self.source_drift=True
            self.automatic=False
            self.ssh_auto_consent=False
            self._clear_remote()
            self.source_issue='Repository HEAD changed; detach and reopen to bind the new source'
            return False
        self.source_issue=''
        return True

    def _selection_rows(self, tab: str, snapshot: Mapping[str,Any]) -> list[Any]:
        if tab == 'events':
            return visible_events(snapshot,domain=self.event_domain,
                                  state=self.event_state,query=self.event_query)
        return operator_items(snapshot)

    def _accept_snapshot(self, tab: str, new: Mapping[str,Any], *, now: float | None = None) -> None:
        if not isinstance(new,Mapping):raise TypeError('Invalid native snapshot')
        if new.get('read_only') is False:raise ValueError('Native read-only contract refused')
        old_rows=self._selection_rows(tab,self.snapshots.get(tab,{}))
        index=self.selections.get(tab,0)
        key=_row_key(old_rows[index]) if 0 <= index < len(old_rows) else ''
        new_rows=self._selection_rows(tab,new)
        keys=[_row_key(x) for x in new_rows]
        # Only a unique exact native identity may survive refresh.
        self.selections[tab]=(keys.index(key) if key and keys.count(key)==1 and
                              sum(_row_key(x)==key for x in old_rows)==1 else 0)
        if tab=='events':
            previous=self.snapshots.get('events',{})
            old_versions={event_key(row):(row.get('update_count'),row.get('revision'),row.get('status'))
                          for row in previous.get('events',()) if isinstance(row,Mapping)
                          and event_key(row)}
            # First attach is a snapshot, not a stream of newly emitted events.
            self.event_highlights=({event_key(row) for row in new.get('events',())
                    if isinstance(row,Mapping) and event_key(row)
                    and old_versions.get(event_key(row)) != (
                       row.get('update_count'),row.get('revision'),row.get('status'))}
                    if 'events' in self.snapshots else set())
        if tab=='actions' and self.remote_detail is None and self.remote_pending and self.remote_scope in ('g','l'):
            new_index=self.selections[tab]
            new_key=_row_key(new_rows[new_index]) if 0<=new_index<len(new_rows) else ''
            if not key or key!=new_key:
                self.remote_generation+=1
        self.snapshots[tab]=new
        self.refreshed_at[tab]=time.monotonic() if now is None else now
        self.errors.pop(tab,None)

    def refresh(self, tab: str, *, now: float | None = None,
                readers: Mapping[str,Callable[[],Mapping[str,Any]]] | None = None,
                explicit_ssh: bool = False) -> None:
        if tab not in TABS:raise ValueError('Unknown operator tab')
        if tab == 'ssh' and not (explicit_ssh or self.ssh_auto_consent):
            self.errors['ssh']='SSH polling disabled; press g to make an explicit read-only request'
            return
        # Source must be reverified for explicit reads, even if autoscan is off.
        # No stale native/remote callback can override a failed provenance check.
        if not self.check_source(now=now):
            self.errors[tab]=self.source_issue
            return
        providers=readers if readers is not None else {
            'actions':lambda:native_actions(self.root,self.slug),
            'runs':lambda:native_runs(self.root),
            'validation':lambda:native_validation(self.root),
            'ssh':lambda:native_ssh(self.root),
            'events':lambda:native_events(self.root),
        }
        try:
            observed=providers[tab]()
            # A long native reader may complete after the branch moved.
            self.last_source_check=float('-inf')
            if not self.check_source():
                self.errors[tab]=self.source_issue
                return
            self._accept_snapshot(tab,observed,now=now)
        except Exception as exc:
            # Other code may throw on malformed/unavailable native receipts.
            # Retain the old snapshot with an unmistakable stale/error marker.
            self.errors[tab]='Observation unavailable ('+type(exc).__name__+'); prior snapshot unverified'

    def maybe_refresh(self, *, now: float | None = None,
                      readers: Mapping[str,Callable[[],Mapping[str,Any]]] | None = None,
                      include_ssh: bool = True, include_events: bool = True) -> bool:
        moment=time.monotonic() if now is None else now
        # Even manual mode must monitor provenance; no silent stale green.
        if not self.check_source(now=moment) or not self.automatic:return False
        tabs=tuple(tab for tab in TABS if
                   (tab != 'ssh' or (self.ssh_auto_consent and include_ssh))
                   and (tab != 'events' or include_events))
        changed=False
        for tab in tabs:
            interval=_MIN_SSH_REFRESH if tab=='ssh' else _MIN_LOCAL_REFRESH
            if moment - self.refreshed_at.get(tab,float('-inf')) < interval:continue
            self.refresh(tab,now=moment,readers=readers)
            changed=True
        return changed

    def _reset_page(self) -> None:
        self.cursor=LogCursor()
        self.page_history=[]

    def _clear_remote(self) -> None:
        self.remote_generation+=1
        self.remote_detail=None
        self.remote_selected=0
        self.remote_history=[]
        self.details.discard('actions')
        self._reset_page()

    def current_snapshot(self) -> Mapping[str,Any]:
        if self.tab=='actions' and self.remote_detail is not None:
            return self.remote_detail
        return self.snapshots.get(self.tab,{'state':'not-observed','message':'Press r to refresh; g on SSH makes an explicit read'})

    def current_selection(self) -> int:
        return self.remote_selected if self.tab=='actions' and self.remote_detail is not None else self.selections.get(self.tab,0)

    def current_rows(self) -> list[Any]:
        return self._selection_rows(self.tab,self.current_snapshot())

    def _push_remote(self, response: Mapping[str,Any]) -> None:
        self.remote_history.append((self.remote_detail,self.remote_selected,
                                    'actions' in self.details,self.cursor,list(self.page_history)))
        self.remote_detail=response
        self.remote_selected=0
        self.details.discard('actions')
        self._reset_page()

    def _back(self) -> None:
        self.remote_generation+=1
        if self.help_open:
            self.help_open=False
            if self.help_restore is not None:
                self.cursor,self.page_history=self.help_restore
            self.help_restore=None
            return
        if self.tab in self.details:
            self.details.discard(self.tab)
            self._reset_page()
        elif self.tab=='actions' and self.remote_history:
            self.remote_detail,self.remote_selected,detail,self.cursor,self.page_history=self.remote_history.pop()
            if detail:self.details.add('actions')
        elif self.tab=='actions':
            self._clear_remote()

    def _page_lines(self) -> tuple[str,...] | list[str] | None:
        from .workspace_viewport import HELP_LINES
        if self.help_open:return HELP_LINES
        snapshot=self.current_snapshot()
        if 'log_lines' in snapshot:return snapshot['log_lines']
        rows=self.current_rows()
        selected=self.current_selection()
        if self.tab in self.details and 0<=selected<len(rows):return detail_lines(rows[selected])
        if not rows and self.remote_detail is not None:return detail_lines(snapshot)
        return None

    def _next_page(self) -> None:
        from .workspace_viewport import page_capacity
        lines=self._page_lines()
        if lines is None:return
        _,next_cursor=log_page(lines,self.cursor,self.viewport_width,
                               page_capacity(self.viewport_height,bool(self.source_issue or self.errors.get(self.tab)),self.help_open))
        if next_cursor is not None:
            self.page_history.append(self.cursor)
            self.cursor=next_cursor

    def press(self,key: str, *,readers: Mapping[str,Callable[[],Mapping[str,Any]]] | None = None
              ) -> bool:
        """Deterministic key transition, always non-mutating to Devtool authorities."""
        if key=='\x03':return False
        if self.tab=='events' and self.event_editing:
            if key in ('\r','\n'):
                self.event_query=self.event_draft[:64]
                self.event_editing=False
                self.selections['events']=0
            elif key=='\x1b':
                self.event_editing=False
            elif key in ('\x7f','\b'):
                self.event_draft=self.event_draft[:-1]
            elif len(key)==1 and key.isprintable() and len(self.event_draft)<64:
                self.event_draft += key
            return True
        if key=='q':
            self.remote_generation+=1
            return False
        if key=='?':
            if self.help_open:self._back()
            else:
                self.remote_generation+=1
                self.help_restore=(self.cursor,list(self.page_history))
                self.help_open=True
                self._reset_page()
            return True
        if key=='b' or (key=='\x1b' and self.help_open):
            self._back()
            return True
        if key in (']','[') or (key in ('j','k') and self._page_lines() is not None):
            if key in (']','j'):self._next_page()
            elif self.page_history:self.cursor=self.page_history.pop()
            return True
        if self.help_open:return True
        if key and key in '12345' and len(key)==1:
            self._clear_remote()
            self.tab=TABS[int(key)-1]
            self.details.discard(self.tab)
            save_ui_state(self.root,self.head,self.tab)
        elif key in ('j','k'):
            rows=self.current_rows()
            current=self.current_selection()
            selected=max(0,min(len(rows)-1,current+(1 if key=='j' else -1)))
            if selected!=current:
                self.remote_generation+=1
                if self.tab=='actions' and self.remote_detail is not None:self.remote_selected=selected
                else:self.selections[self.tab]=selected
                self._reset_page()
        elif key in ('\r','\n'):
            if self.tab in self.details:
                self.details.discard(self.tab)
                self._reset_page()
            elif self.tab=='actions' and self.remote_detail is not None:
                child=operator_drilldown(self.remote_detail,self.remote_selected)
                if child is not None:
                    payload,kind=child
                    if self.remote_detail.get('kind')=='run-jobs' and kind=='steps':
                        payload={**payload,'kind':'job-steps','run':self.remote_detail['run'],
                                 'job':self.current_rows()[self.remote_selected],'read_only':True}
                    self.remote_generation+=1
                    self._push_remote(payload)
                else:
                    self.details.add(self.tab)
                    self._reset_page()
            else:
                self.details.add(self.tab)
                self._reset_page()
        elif self.tab=='events' and key in ('f','s','/','c'):
            self.details.discard('events')
            self._reset_page()
            if key=='f':
                index=DOMAIN_FILTERS.index(self.event_domain)
                self.event_domain=DOMAIN_FILTERS[(index+1)%len(DOMAIN_FILTERS)]
                self.selections['events']=0
            elif key=='s':
                index=STATE_FILTERS.index(self.event_state)
                self.event_state=STATE_FILTERS[(index+1)%len(STATE_FILTERS)]
                self.selections['events']=0
            elif key=='/':
                self.event_editing=True
                self.event_draft=self.event_query
            elif key=='c':
                self.event_domain='all'
                self.event_state='all'
                self.event_query=''
                self.event_draft=''
                self.selections['events']=0
        elif key=='a':
            if self.tab=='ssh':
                if not self.source_drift:
                    self.ssh_auto_consent=not self.ssh_auto_consent
                    self.automatic=True
            else:
                if not self.source_drift:self.automatic=not self.automatic
        elif key=='r':
            self._clear_remote()
            self.last_source_check=float('-inf')
            if not self.source_drift:self.refresh(self.tab,readers=readers)
        elif key in ('w','g','l') and self.tab=='actions' and not self.source_drift:
            self.actions_get(key)
        elif key=='g' and self.tab=='ssh':
            if not self.source_drift:self.refresh('ssh',readers=readers,explicit_ssh=True)
        return True

    def actions_get(self, key: str) -> None:
        """Schedule exactly one explicit, source-bound native read off the terminal thread."""
        self.last_source_check=float('-inf')
        if not self.check_source():
            self.errors['actions']=self.source_issue+'; remote GET refused'
            return
        if not self.slug:
            self.errors['actions']='No GitHub repository selected for native read-only GET'
            return
        if self.remote_pending:
            self.errors['actions']='A remote GET is already pending'
            return
        snapshot=self.current_snapshot()
        rows=self.current_rows()
        index=self.current_selection()
        item=copy.deepcopy(rows[index]) if 0<=index<len(rows) and isinstance(rows[index],Mapping) else {}
        kind=snapshot.get('kind')
        root,slug,auth=self.root,self.slug,self.auth_mode
        run=copy.deepcopy(snapshot.get('run'))
        job=copy.deepcopy(snapshot.get('job'))
        from .workspace_actions import workspace_client, workflow_runs, run_jobs, job_logs
        def fetch():
            from importlib import import_module
            actions_operator=import_module(".actions_operator",__package__)
            if key=='w':return actions_operator.operator_workflows(slug,client=workspace_client(slug,auth))
            if key=='g' and kind=='workflows':return workflow_runs(workspace_client(slug,auth),slug,item)
            if key=='g' and kind=='workflow-runs':return run_jobs(workspace_client(slug,auth),slug,item)
            if key=='l' and kind in ('run-jobs','job-steps'):
                return job_logs(workspace_client(slug,auth),slug,run,item if kind=='run-jobs' else job)
            if key=='g' and kind not in ('workflows','workflow-runs','run-jobs','job-steps','job-logs'):
                return actions_operator.operator_review(item,slug,client=workspace_client(slug,auth) if item.get('run_id') is not None else None)
            if key=='l' and kind not in ('workflows','workflow-runs','run-jobs','job-steps','job-logs'):
                return actions_operator.operator_lineage(item,root,slug)
            raise ValueError('No explicit GET for this selection')
        generation=self.remote_generation
        self.remote_pending=True
        self.remote_scope=key
        self.errors.pop('actions',None)
        def worker():
            try:result=(generation,True,fetch())
            except Exception as exc:result=(generation,False,type(exc).__name__)
            self.remote_results.put_nowait(result)
        threading.Thread(target=worker,daemon=True,name='devtool-rich-actions-read').start()

    def consume_actions(self) -> bool:
        try:generation,ok,result=self.remote_results.get_nowait()
        except Empty:return False
        self.remote_pending=False
        self.remote_scope=""
        self.last_source_check=float('-inf')
        if not self.check_source() or generation!=self.remote_generation or self.tab!='actions':
            return True
        if not ok:
            self.errors['actions']='Remote GET unavailable ('+str(result)+'); previous view retained'
        elif (not isinstance(result,Mapping) or result.get('read_only') is False
                or result.get('qualified_release') is True):
            self.errors['actions']='Remote GET refused: invalid read-only result'
        else:
            self._push_remote(result)
            self.errors.pop('actions',None)
        return True

    def current_view(self, *, width: int = 80, height: int = 24) -> Group:
        self.viewport_width,self.viewport_height=max(1,width),max(1,height)
        tabs=['['+str(i)+' '+name.upper()+']' if name==self.tab else str(i)+' '+name.upper()
              for i,name in enumerate(TABS,1)]
        if width<80:
            tabs=['['+str(i)+' '+name[:3].upper()+']' if name==self.tab else str(i)+' '+name[:3].upper()
                  for i,name in enumerate(TABS,1)]
        heading='DEVTOOL · '+' '.join(tabs)
        status='Source '+self.head[:12]+' · '+('AUTO' if self.automatic else 'MANUAL')+' · read-only'
        if self.tab=='actions':status+=' · auth '+self.auth_mode
        if self.ssh_pending or self.remote_pending or self.events_pending:status+=' · GET pending'
        warning=self.source_issue or self.errors.get(self.tab,'')
        if self.tab=='events' and not warning:
            warning='Events '+self.event_domain+'/'+self.event_state+' · '+('Search: '+self.event_draft if self.event_editing else self.event_query)
        snapshot=self.current_snapshot()
        footer='q detach · ? help · b back · j/k · Enter'
        if width>=80:footer+=' · [ ] page · w/g/l GET · r refresh'
        return render_workspace(width=width,height=height,heading=heading,status=status,
                warning=_secure('warning',warning,limit=300) if warning else '',tab=self.tab,
                snapshot=snapshot,selected=self.current_selection(),rows=self.current_rows(),
                detail=self.tab in self.details,help_open=self.help_open,cursor=self.cursor,footer=footer)


class SSHReadObserver:
    """One in-flight native read in a daemon worker; never block terminal input.

    No cancellation, no new SSH transport, no mutation. A pending native read
    cannot be launched again. Late results are rechecked against source identity.
    The 12-second interval is a *minimum cadence*, not an SSH timeout.
    """
    def __init__(self, *, tab: str = 'ssh') -> None:
        if tab not in ('ssh', 'events'):
            raise ValueError('Only native read-only SSH/event observation is supported')
        self.tab=tab
        self._lock=threading.Lock()
        self._pending=False
        self._results: Queue[tuple[bool,object]] = Queue(maxsize=1)

    def request(self, fetch: Callable[[],Mapping[str,Any]]) -> bool:
        with self._lock:
            if self._pending:return False
            self._pending=True
        def worker() -> None:
            try:
                result=(True,fetch())
            except Exception as exc:
                result=(False,type(exc).__name__)
            self._results.put_nowait(result)
        threading.Thread(target=worker,daemon=True,name='devtool-rich-ssh-status').start()
        return True

    def consume(self, workspace: Workspace, *, now: float | None = None) -> bool:
        try:
            ok,result=self._results.get_nowait()
        except Empty:
            return False
        with self._lock:self._pending=False
        if self.tab=='ssh':workspace.ssh_pending=False
        else:workspace.events_pending=False
        workspace.last_source_check=float('-inf')
        if not workspace.check_source(now=now):
            workspace.errors[self.tab]=workspace.source_issue
            return True
        if not ok:
            workspace.errors[self.tab]=(('SSH' if self.tab=='ssh' else 'Native event')+
                ' observation failed ('+str(result)+'); prior state unverified')
        else:
            try:workspace._accept_snapshot(self.tab,result,now=now)
            except (TypeError,ValueError) as exc:
                workspace.errors[self.tab]='Native observation refused ('+type(exc).__name__+')'
        return True


def run_operator_workspace(root: Path, slug: str | None, *, initial: Mapping[str,Any] | None = None,
                           args: Any = None, console: Console | None = None,
                           readers: Mapping[str,Callable[[],Mapping[str,Any]]] | None = None,
                           initial_tab: str | None = None) -> bool:
    """Rich.Live inline, foreground, durable UI tab only; never alternate screen."""
    if not operator_enabled(args=args):return False
    if os.name!='posix' or not sys.stdin.isatty() or not sys.stdout.isatty():return False
    root=_safe_root(root)
    head=_source_head(root)
    if not slug:
        # Use the native local origin resolver; never guess the repository.
        from .github_actions import resolve_repository, GitHubActionsError
        try:slug=resolve_repository('',root)
        except GitHubActionsError:slug=None
    ws=Workspace(root,head,slug=slug,tab=initial_tab or load_ui_state(root,head),
                 auth_mode=str(getattr(args,'auth','auto') or 'auto'))
    if initial is not None and isinstance(initial,Mapping):
        ws.snapshots['actions']=initial
        ws.refreshed_at['actions']=time.monotonic()
    ws.maybe_refresh(readers=readers,include_ssh=False,include_events=False)
    ssh_observer=SSHReadObserver()
    events_observer=SSHReadObserver(tab='events')
    events_reader=(readers or {}).get('events') or (lambda:native_events(root))
    events_last_request=float('-inf')
    # Source-scoped reader function from canonical native SSH authority.
    ssh_reader=(readers or {}).get('ssh') or (lambda:native_ssh(root))
    ssh_last_request=float('-inf')
    out=console or Console()
    fd=sys.stdin.fileno()
    try:
        out.width,out.height=terminal_geometry(out,fd)
        with PosixInput(fd) as keys, InlineLive(ws.current_view(width=out.width,height=out.height),console=out) as live:
            while True:
                # Continuous local projection refresh, never run/SSH command execution.
                key=keys.read(.2)
                if key=='':break
                resized=live.resize(fd)
                now=time.monotonic()
                changed=ws.maybe_refresh(now=now,readers=readers,
                                         include_ssh=False,include_events=False) or resized
                changed=ws.consume_actions() or changed
                changed=events_observer.consume(ws,now=now) or changed
                if (ws.automatic and not ws.source_drift and not ws.source_issue
                    and now-events_last_request>=_MIN_LOCAL_REFRESH):
                    if events_observer.request(events_reader):
                        ws.events_pending=True
                        events_last_request=now
                        changed=True
                changed=ssh_observer.consume(ws,now=now) or changed
                if (ws.ssh_auto_consent and ws.automatic and not ws.source_drift
                    and not ws.source_issue and now-ssh_last_request>=_MIN_SSH_REFRESH):
                    if ssh_observer.request(ssh_reader):
                        ws.ssh_pending=True
                        ssh_last_request=now
                        changed=True
                if key is not None:
                    key={'up':'k','down':'j'}.get(key,key)
                    if key=='r' and ws.tab=='events' and not ws.source_drift:
                        if ws.check_source(now=now) and events_observer.request(events_reader):
                            ws.events_pending=True
                            events_last_request=now
                        elif ws.source_issue:
                            ws.errors['events']=ws.source_issue
                        else:
                            ws.errors['events']='Event observation already pending'
                    elif key=='g' and ws.tab=='ssh' and not ws.source_drift:
                        if ws.check_source(now=now) and ssh_observer.request(ssh_reader):
                            ws.ssh_pending=True
                            ssh_last_request=now
                        elif ws.source_issue:
                            ws.errors['ssh']=ws.source_issue
                        else:
                            ws.errors['ssh']='SSH status GET already pending'
                    elif not ws.press(key,readers=readers):break
                    changed=True
                if changed:
                    live.update(ws.current_view(width=out.width,height=out.height),refresh=True)
    except KeyboardInterrupt:
        return True
    except Exception as exc:
        print("Devtool workspace presentation unavailable ("+type(exc).__name__+")",file=sys.stderr,flush=True)
        return False
    return True


__all__=['Workspace','SSHReadObserver','run_operator_workspace',
         'load_ui_state','save_ui_state',
         'native_runs','native_validation','native_ssh','native_actions','native_events','TABS']
