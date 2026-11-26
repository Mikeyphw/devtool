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
import json
import os
from queue import Empty, Queue
from pathlib import Path
import select
import subprocess
import sys
import threading
import tempfile
import time
from typing import Any

from rich.console import Console, Group
from .inline_live import InlineLive, terminal_geometry
from .posix_input import PosixInput
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .operator_rich import (build_operator_view, operator_enabled, operator_items,
                            _secure)
from .operator_event_feed import (DOMAIN_FILTERS, STATE_FILTERS, native_events,
                                  visible_events, event_key, event_view)

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
            self.source_issue='HEAD verification unavailable; snapshots are stale'
            return False
        if actual != self.head:
            self.source_drift=True
            self.automatic=False
            self.ssh_auto_consent=False
            self.remote_detail=None
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
        if key in ('q','\x03'):return False
        if key and key in '12345' and len(key)==1:
            self.remote_detail=None
            self.tab=TABS[int(key)-1]
            save_ui_state(self.root,self.head,self.tab)
        elif key=='j':
            rows=self._selection_rows(self.tab,self.snapshots.get(self.tab,{}))
            self.selections[self.tab]=min(max(0,len(rows)-1),self.selections.get(self.tab,0)+1)
        elif key=='k':
            self.selections[self.tab]=max(0,self.selections.get(self.tab,0)-1)
        elif key in ('\r','\n'):
            if self.tab in self.details:self.details.remove(self.tab)
            else:self.details.add(self.tab)
        elif self.tab=='events' and key in ('f','s','/','c'):
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
                # Only an explicit SSH-tab keypress can grant periodic SSH GETs.
                if not self.source_drift:
                    self.ssh_auto_consent=not self.ssh_auto_consent
                    self.automatic=True
            else:
                if not self.source_drift:self.automatic=not self.automatic
        elif key=='r':
            self.remote_detail=None
            self.last_source_check=float('-inf')
            if not self.source_drift:self.refresh(self.tab,readers=readers)
        elif key=='b':
            self.remote_detail=None
        elif key in ('w','g','l') and self.tab=='actions' and not self.source_drift:
            self.actions_get(key)
        elif key=='g' and self.tab=='ssh':
            if not self.source_drift:self.refresh('ssh',readers=readers,explicit_ssh=True)
        return True

    def actions_get(self, key: str) -> None:
        """Legacy UX-RICH02 explicit Actions GET keys; never dispatch/collect."""
        # Force current identity re-check before any network GET, irrespective
        # of the periodic cadence and of the automatic/manual setting.
        self.last_source_check=float('-inf')
        if not self.check_source():
            self.errors['actions']=self.source_issue+'; remote GET refused'
            return
        if not self.slug:
            self.errors['actions']='No GitHub repository selected for native read-only GET'
            return
        rows=operator_items(self.snapshots.get('actions',{}))
        index=self.selections.get('actions',0)
        item=rows[index] if 0<=index<len(rows) and isinstance(rows[index],Mapping) else {}
        try:
            from .actions_operator import operator_workflows, operator_review, operator_lineage
            if key=='w': response=operator_workflows(self.slug)
            elif key=='g': response=operator_review(item,self.slug)
            else:response=operator_lineage(item,self.root,self.slug)
            if not isinstance(response,Mapping) or response.get('read_only') is False:
                raise ValueError('Unverified read-only response')
            self.remote_detail=response
            self.errors.pop('actions',None)
        except Exception as exc:
            self.remote_detail=None
            self.errors['actions']='Remote GET unavailable ('+type(exc).__name__+')'

    def current_view(self, *, width: int = 80) -> Group:
        table=Table.grid(expand=True)
        table.add_column(ratio=1)
        tabs=[]
        for i,name in enumerate(TABS,1):
            tabs.append(('['+str(i)+' '+name.upper()+']' if name==self.tab
                         else str(i)+' '+name.upper()))
        heading=Text('DEVTOOL · UX-RICH03  '+(' · '.join(tabs)),style='bold cyan')
        status=Text('Source '+self.head[:12]+'  • '+('AUTO' if self.automatic else 'MANUAL')+
            ('  • SSH AUTO OPT-IN' if self.ssh_auto_consent else '  • SSH MANUAL')+
            ('  • SSH GET PENDING' if self.ssh_pending else '')+
            ('  • EVENT READ PENDING' if self.events_pending else '')+
            '  • read-only',style='dim')
        snapshot=self.snapshots.get(self.tab)
        if snapshot is None:
            snapshot={'state':'not-observed','message':'Press r to refresh, or g on SSH to query the worker'}
        error=self.source_issue or self.errors.get(self.tab)
        if error:
            warning=Text(_secure('warning',error,limit=180),style='bold red')
        else:
            warning=Text('Native observation; no execution, cancellation or release qualification',style='dim')
        selected=self.selections.get(self.tab,0)
        if self.tab=='actions' and self.remote_detail is not None:
            snapshot=self.remote_detail
            selected=0
        if self.tab=='events':
            panel=event_view(snapshot,width=width,selected=selected,
                             domain=self.event_domain,state=self.event_state,
                             query=self.event_query,editing=self.event_editing,
                             draft=self.event_draft,detail=self.tab in self.details,
                             recent=self.event_highlights)
        else:
            panel=build_operator_view(self.tab,snapshot,width=width,selected=selected,
                                      detail=self.tab in self.details)
        footer=Text('1–5 tabs · j/k select · Enter details · r refresh · a auto toggle'
                    ' · events: f/s filters / search c clear'
                    ' · g SSH/Actions GET · w/l Actions GET · b back · q detach',style='dim')
        return Group(heading,status,warning,panel,footer)


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
    ws=Workspace(root,head,slug=slug,tab=initial_tab or load_ui_state(root,head))
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
        with PosixInput(fd) as keys, InlineLive(ws.current_view(width=out.width),console=out) as live:
            while True:
                # Continuous local projection refresh, never run/SSH command execution.
                key=keys.read(.2)
                if key=='':break
                resized=live.resize(fd)
                now=time.monotonic()
                changed=ws.maybe_refresh(now=now,readers=readers,
                                         include_ssh=False,include_events=False) or resized
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
                    live.update(ws.current_view(width=out.width),refresh=True)
    except KeyboardInterrupt:
        return True
    except Exception as exc:
        print("Devtool workspace presentation unavailable ("+type(exc).__name__+")",file=sys.stderr,flush=True)
        return False
    return True


__all__=['Workspace','SSHReadObserver','run_operator_workspace',
         'load_ui_state','save_ui_state',
         'native_runs','native_validation','native_ssh','native_actions','native_events','TABS']
