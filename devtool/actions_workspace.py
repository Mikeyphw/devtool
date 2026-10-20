"""Read-only GitHub Actions operator context from canonical durable records.

No GitHub write permissions, no guessing a run ID, no journal mutations.
Not release/source qualification: statuses are observations, not authority.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .actions_dispatch import _state_root as dispatch_root
from .actions_runs import _read_dispatch_receipt
from .actions_pipeline import _journal_root, pipeline_status
from .actions_presentation import project_pipeline
from .github_actions import GitHubActionsError, _slug
from .actions_pipeline_textual import _safe_text

_HEX = re.compile(r"[0-9a-f]{32}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_LIMIT = 50
_SCAN_LIMIT = 512
_ALLOWED_DISPATCH = frozenset({'dispatch-outcome-unknown','rejected-by-api','accepted-uncorrelated',
                               'accepted','correlated','requested','queued','in_progress',
                               'completed','success','failure','cancelled','timed_out'})


def _filenames(folder: Path) -> list[str]:
    if folder.is_symlink():
        raise GitHubActionsError('Actions journal directory must not be a symlink')
    if not folder.exists():
        return []
    if not folder.is_dir():
        raise GitHubActionsError('Actions journal root is not a directory')
    items = []
    try:
        # Stop unbounded directory enumeration. Files are opened only by existing
        # canonical, size-limited, symlink-rejecting record readers.
        for index, item in enumerate(folder.iterdir()):
            if index >= _SCAN_LIMIT:
                raise GitHubActionsError('Actions journal inventory exceeds bounded 512-entry scan')
            if item.name.endswith('.json'):
                if item.is_symlink():
                    raise GitHubActionsError('Actions journal contains symlink')
                if not _HEX.fullmatch(item.name[:-5]):
                    raise GitHubActionsError('Actions journal contains malformed identity')
                items.append(item.name[:-5])
    except OSError:
        raise GitHubActionsError('Cannot inspect local Actions journal') from None
    return items


def _dispatch_row(identity: str, folder: Path, slug: str) -> dict[str, Any] | None:
    _, receipt = _read_dispatch_receipt(identity, folder)
    if _slug(receipt['repository']).lower() != slug.lower():
        return None
    sha = str(receipt.get('pre_dispatch_sha') or '')
    if not _SHA.fullmatch(sha):
        raise GitHubActionsError('Dispatch receipt lacks exact source SHA')
    raw_state = receipt.get('state')
    if not isinstance(raw_state, str) or raw_state not in _ALLOWED_DISPATCH:
        raise GitHubActionsError('Dispatch receipt state is invalid')
    rid = receipt.get('run_id')
    if rid is not None:
        from .actions_runs import run_id
        rid = run_id(rid)
    created = str(receipt.get('created_at') or '')
    if len(created) > 64 or not created:
        raise GitHubActionsError('Dispatch receipt missing bounded time')
    # A correlated run ID is not the same as a verified successful run.
    return {'kind':'dispatch','id':identity,'repository':slug,'source_sha':sha.lower(),
            'state':raw_state,'run_id':rid,'name':str(receipt.get('workflow_path') or 'Workflow')[:120],
            'created_at':created,'qualified_release':False,'remote_verified':False,
            'advice':('Inspect exact request ID; do not redispatch uncertain outcome' if rid is None else
                      'Inspect exact run and source binding; remote success is NOT inferred')}


def _pipeline_row(identity: str, folder: Path, slug: str, root: Path) -> dict[str, Any] | None:
    from .actions_pipeline import _read, _public
    _, record = _read(identity, state_root=folder)
    if _slug(record.get('repository','')).lower() != slug.lower():
        return None
    if Path(str(record.get('repo_root') or '')).resolve() != root.resolve():
        return None
    view = project_pipeline(_public(record))
    return {'kind':'pipeline','id':identity,'repository':slug,
            'source_sha':view['source_sha'],'state':view['state'],
            'run_id':None,'name':str(view['name'])[:120],
            'created_at':str(record.get('created_at') or '')[:64],
            'qualified_release':False,'remote_verified':False,
            'advice': ('Blocked/uncertain: manual reconciliation; no automatic replay' if view['state']=='blocked' else
                       'Success is pipeline-local only; release NOT qualified' if view['state']=='success' else
                       'Continue pipeline ONLY with separate explicit reviewed command'),
            'counts':view['counts'],'resume_enabled':view['resume_enabled']}


def local_recent(root: Path, slug: str, *, limit: int = 20,
                 dispatches: Path | None = None, pipelines: Path | None = None) -> dict[str, Any]:
    slug = _slug(slug)
    if type(limit) is not int or not 1 <= limit <= _LIMIT:
        raise GitHubActionsError('Local Actions history limit must be 1..50')
    root = root.resolve()
    d = dispatches if dispatches is not None else dispatch_root()
    p = pipelines if pipelines is not None else _journal_root()
    rows = []
    for identity in _filenames(d):
        row = _dispatch_row(identity,d,slug)
        if row is not None:
            rows.append(row)
    for identity in _filenames(p):
        row = _pipeline_row(identity,p,slug,root)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item:(item['created_at'],item['kind'],item['id']),reverse=True)
    return {'phase':'ACTIONS-UX01','repository':slug,'read_only':True,
            'source_attested':False,'qualified_release':False,
            'items':rows[:limit],'total_matching':len(rows),'limit':limit,
            'note':'Local journals only; observations may be stale. No implicit run selection, dispatch or release promotion.'}


def local_detail(root: Path, slug: str, kind: str, identity: str,
                 *, dispatches: Path | None = None, pipelines: Path | None = None) -> dict[str, Any]:
    if kind not in ('dispatch','pipeline') or not _HEX.fullmatch(identity):
        raise GitHubActionsError('Specify exact dispatch or pipeline identity')
    slug = _slug(slug)
    folder = ((dispatches if dispatches is not None else dispatch_root()) if kind=='dispatch' else
              (pipelines if pipelines is not None else _journal_root()))
    # Canonical readers validate file size, symlinks and identity again.
    row = (_dispatch_row(identity,folder,slug) if kind=='dispatch' else
           _pipeline_row(identity,folder,slug,root.resolve()))
    if row is None:
        raise GitHubActionsError('Actions identity does not belong to selected repository/worktree')
    return {'phase':'ACTIONS-UX01','read_only':True,'source_attested':False,
            'qualified_release':False,'item':row}


def screen_lines(payload: Mapping[str, Any], *, compact: bool = False, selected: int = 0) -> dict[str, str]:
    rows = payload['items']
    symbols = {'success':'✓','blocked':'!','failure':'✕','cancelled':'—',
               'waiting':'◷','dispatch-outcome-unknown':'?', 'accepted-uncorrelated':'?',
               'rejected-by-api':'✕'}
    lines=[]
    for index,item in enumerate(rows):
        marker = '>' if index==selected else ' '
        symbol = symbols.get(item['state'],'·')
        label = _safe_text(item['name'], 32 if compact else 64)
        lines.append(f"{marker} {symbol} {item['kind'][:1].upper()} {_safe_text(item['state'],23):23s} {label}" if not compact else
                     f"{marker}{symbol} {item['kind'][:1].upper()} {_safe_text(item['state'],15):15s} {label}")
    current = rows[selected] if rows and 0 <= selected < len(rows) else None
    if current:
        detail = (f"{current['kind']} {current['id']}\n"
                  f"Source: {current['source_sha'][:12]}… (not reverified)\n"
                  f"Run: {current['run_id'] or 'unbound'}\n"
                  f"{_safe_text(current['advice'], 165)}")
        if current['kind']=='dispatch':
            detail += f"\nInspect: devtool actions resume --request-id {current['id']}" if current['run_id'] else                       f"\nUncorrelated: inspect candidate runs explicitly; DO NOT redispatch automatically"
        else:
            detail += f"\nInspect: devtool actions pipeline status --pipeline-id {current['id']}"
    else:
        detail = 'No local records for this repository. Use actions workflows to discover remote workflows.'
    return {'title':f"Devtool Actions · {_safe_text(payload['repository'],64)}",
            'rows':'\n'.join(lines) or '(no records)', 'detail':detail,
            'footer':'LOCAL ONLY · NOT RELEASE QUALIFIED · r refresh · j/k select · q exit'}


def create_actions_app(root: Path, slug: str, *, load=None, initial=None,
                       review=None, collect=None, lineage=None, workflows=None):
    """Single UX01+UX02+R04 operator UI; Textual imported only for real TTY use.

    No remote requests on mount/refresh. Read-only operations are user-initiated;
    archive collection requires an exact name plus a separate y confirmation.
    """
    from threading import Event
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import Header, Footer, Static, Input
    from rich.text import Text
    from .actions_operator import (operator_review, operator_collect, operator_lineage,
                                   operator_workflows, operator_lines)

    getter = load if load is not None else lambda: local_recent(root,slug)
    review_fn = review if review is not None else operator_review
    collect_fn = collect if collect is not None else operator_collect
    lineage_fn = lineage if lineage is not None else operator_lineage
    workflows_fn = workflows if workflows is not None else operator_workflows
    class ActionsWorkspace(App):
        TITLE = 'Devtool · GitHub Actions'
        SUB_TITLE = 'Exact source and run · no automatic mutation'
        CSS = """
        Screen { layout: vertical; }
        #label { text-style: bold; height: auto; padding: 0 1; }
        #history { height: 2fr; min-height: 3; padding: 0 1; }
        #rows { height: auto; }
        #detail { height: 2fr; min-height: 4; padding: 0 1; }
        #artifact { display: none; height: 3; margin: 0 1; }
        #note { height: auto; max-height: 4; padding: 0 1; color: $warning; }
        """
        BINDINGS = [
            Binding('r','refresh','Refresh'),Binding('j','next_item','Next'),
            Binding('k','previous_item','Previous'),
            Binding('down','next_item','Next',show=False),
            Binding('up','previous_item','Previous',show=False),
            Binding('v','review_remote','Review GET'),
            Binding('f','follow_remote','Follow GET'),
            Binding('l','lineage_local','Lineage'),
            Binding('w','discover_workflows','Workflows GET'),
            Binding('c','collect_prepare','Collect plan'),
            Binding('y','collect_confirm','Confirm collect'),
            Binding('escape','cancel_operation','Cancel',show=False),
            Binding('x','cancel_operation','Cancel'),
            Binding('q','quit','Close')]
        _payload = None
        _selected = 0
        _panel = None
        _panel_key = None
        _confirmation = None
        _collection_ready = None
        _busy = False
        _generation = 0
        _cancel = None
        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static('',id='label')
            with VerticalScroll(id='history'):
                yield Static('',id='rows')
            with VerticalScroll(id='detail'):
                yield Static('',id='selected')
            yield Input(placeholder='Exact artifact name (then press y)',id='artifact')
            yield Static('',id='note')
            yield Footer()
        def on_mount(self):
            self.apply_snapshot(initial if initial is not None else getter())
        def _key(self):
            row=self._current()
            return (row['kind'],row['id']) if row else None
        def _current(self):
            items=self._payload.get('items',[]) if self._payload else []
            return items[self._selected] if 0<=self._selected<len(items) else None
        def _cancel_current(self):
            self._generation+=1
            if self._cancel is not None:self._cancel.set()
            self._cancel=None
            self._busy=False
            self._confirmation=None
            self._collection_ready=None
            self.query_one('#artifact',Input).display=False
        def apply_snapshot(self,snapshot):
            if (snapshot.get('repository','').casefold()!=slug.casefold()
                    or snapshot.get('read_only') is not True):
                raise ValueError('Actions workspace repository/authority mismatch')
            prior=self._key()
            self._payload=snapshot
            ids=[(x['kind'],x['id']) for x in snapshot['items']]
            self._selected=ids.index(prior) if prior in ids else 0
            if self._key()!=prior:
                self._cancel_current()
                self._panel=None
                self._panel_key=None
            self._render()
        def _render(self):
            if self._payload is None:return
            compact=self.size.width<65 or self.size.height<22
            data=screen_lines(self._payload,compact=compact,selected=self._selected)
            detail=data['detail']
            if self._panel is not None and self._panel_key==self._key():
                detail=self._panel
            note=('REMOTE READ ONLY until separately confirmed collection. '
                  'v review · f follow · l lineage · w workflows · c collect · x cancel · q quit')
            if self._confirmation is not None:
                note='COLLECTION PENDING · type name, Enter, then y to CONFIRM. x/Esc cancels.'
            elif self._busy:
                note='Remote operation in progress; x cancels follow/display. No automatic POST.'
            for key,name in [('title','#label'),('rows','#rows')]:
                self.query_one(name,Static).update(Text(data[key]))
            self.query_one('#selected',Static).update(Text(detail))
            self.query_one('#note',Static).update(Text(note))
        def on_resize(self,event):self._render()
        def action_next_item(self):
            if self._payload and self._payload['items']:
                previous=self._key()
                self._selected=min(len(self._payload['items'])-1,self._selected+1)
                if self._key()!=previous:self._cancel_current();self._panel=None;self._panel_key=None
                self._render()
        def action_previous_item(self):
            if self._payload and self._payload['items']:
                previous=self._key()
                self._selected=max(0,self._selected-1)
                if self._key()!=previous:self._cancel_current();self._panel=None;self._panel_key=None
                self._render()
        def action_refresh(self):
            try:self.apply_snapshot(getter())
            except Exception:
                self.query_one('#note',Static).update(Text('Refresh rejected: existing local snapshot retained.'))
        def _operation(self,name,row,fn,follow=False):
            # Identity pinned at keypress, never 'latest' after asynchronous return.
            if self._busy:return
            self._cancel_current()
            self._busy=True
            key=self._key(); token=self._generation
            cancel=Event();self._cancel=cancel
            self._panel='Requesting '+name+' for '+str(key[1] if key else '?')+'...'
            self._panel_key=key
            self._render()
            def runner():
                try:
                    if follow:
                        for index in range(12):
                            if cancel.is_set():return
                            result=fn()
                            if cancel.is_set():return
                            self.call_from_thread(self._complete_op,token,key,result,False)
                            if result.get('state') not in ('incomplete',):break
                            if cancel.wait(5):return
                        self.call_from_thread(self._complete_op,token,key,result,True)
                    else:
                        result=fn()
                        if not cancel.is_set():
                            self.call_from_thread(self._complete_op,token,key,result,True)
                except Exception as exc:
                    if not cancel.is_set():
                        self.call_from_thread(self._operation_error,token,key,str(exc))
            self.run_worker(runner,thread=True,group='actions-operator',exclusive=False)
        def _complete_op(self,token,key,result,finished):
            if token!=self._generation or key!=self._key():return
            self._panel=operator_lines(result,width=self.size.width)
            self._panel_key=key
            if finished:self._busy=False;self._cancel=None
            self._render()
        def _operation_error(self,token,key,message):
            if token!=self._generation or key!=self._key():return
            self._panel='Operation refused: '+_safe_text(message,200)
            self._panel_key=key
            self._busy=False;self._cancel=None
            self._render()
        def action_review_remote(self):
            row=self._current()
            if row and row['kind']=='dispatch':
                self._operation('exact GitHub review',row,lambda:review_fn(row,slug))
        def action_follow_remote(self):
            row=self._current()
            if row and row['kind']=='dispatch':
                self._operation('bounded exact-request follow',row,lambda:review_fn(row,slug),follow=True)
        def action_lineage_local(self):
            row=self._current()
            if row and row['kind']=='pipeline':
                self._operation('offline lineage',row,lambda:lineage_fn(row,root,slug))
        def action_discover_workflows(self):
            row=self._current()
            if row:
                self._operation('GitHub workflows',row,lambda:workflows_fn(slug))
        def action_collect_prepare(self):
            row=self._current()
            if self._busy or not row or row['kind']!='dispatch' or row.get('run_id') is None:return
            self._cancel_current()
            self._confirmation=self._key()
            self._collection_ready=None
            self.query_one('#artifact',Input).value=''
            self.query_one('#artifact',Input).display=True
            self.query_one('#artifact',Input).focus()
            self._panel=('COLLECT REQUIRES TWO EXPLICIT STEPS\n'
                         'Type an exact artifact name, Enter, then y to confirm.\n'
                         'Canonical collector will re-review source, run, attempt and SHA-256.\n'
                         'No install, publish or release qualification.')
            self._panel_key=self._key()
            self._render()
        def on_input_changed(self,event:Input.Changed):
            if event.input.id=='artifact':
                self._collection_ready=None

        def on_input_submitted(self,event:Input.Submitted):
            if event.input.id!='artifact' or self._confirmation!=self._key():return
            from .actions_operator import _ARTIFACT
            if not _ARTIFACT.fullmatch(event.value):
                self.query_one('#note',Static).update(Text('Invalid exact artifact name; no collection.'))
                return
            self._collection_ready=event.value
            event.input.blur()
            self._panel='ARTIFACT '+_safe_text(event.value,128)+' ARMED. Press y to CONFIRM or x to cancel. No download yet.'
            self._panel_key=self._key()
            self._render()

        def action_collect_confirm(self):
            row=self._current()
            if not row or self._confirmation!=self._key() or self._busy:return
            artifact_name=self.query_one('#artifact',Input).value
            if self._collection_ready!=artifact_name:return
            from .actions_operator import _ARTIFACT
            if not _ARTIFACT.fullmatch(artifact_name):
                self.query_one('#note',Static).update(Text('Invalid exact artifact name; no collection.'))
                return
            self._confirmation=None
            self._collection_ready=None
            self.query_one('#artifact',Input).display=False
            self._operation('verified artifact collection',row,
                            lambda:collect_fn(row,root,slug,artifact_name,confirmed=True))
        def action_cancel_operation(self):
            self._cancel_current()
            self._panel='Local follow/display cancelled; confirmed collection may continue; remote jobs not cancelled.'
            self._panel_key=self._key()
            self._render()
        def on_unmount(self):
            if self._cancel is not None:self._cancel.set()
    return ActionsWorkspace()
