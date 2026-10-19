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


def create_actions_app(root: Path, slug: str, *, load=None, initial=None):
    """Lazy Textual import; deterministic testable load/selection; no side effects."""
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import VerticalScroll
    from textual.widgets import Header, Footer, Static
    from rich.text import Text

    getter = load if load is not None else lambda: local_recent(root,slug)
    class ActionsWorkspace(App):
        TITLE = 'Devtool · GitHub Actions'
        SUB_TITLE = 'Local history · read-only'
        CSS = """
        Screen { layout: vertical; }
        #label { text-style: bold; height: auto; padding: 0 1; }
        #history { height: 2fr; min-height: 3; padding: 0 1; }
        #rows { height: auto; }
        #detail { height: 1fr; min-height: 4; padding: 0 1; }
        #note { height: auto; padding: 0 1; color: $warning; }
        """
        BINDINGS = [Binding('r','refresh','Refresh'), Binding('j','next_item','Next'),
                    Binding('k','previous_item','Previous'), Binding('down','next_item','Next',show=False),
                    Binding('up','previous_item','Previous',show=False), Binding('q','quit','Close')]
        _payload = None
        _selected = 0
        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            yield Static('',id='label')
            with VerticalScroll(id='history'):
                yield Static('',id='rows')
            with VerticalScroll(id='detail'):
                yield Static('',id='selected')
            yield Static('',id='note')
            yield Footer()
        def on_mount(self):
            self.apply_snapshot(initial if initial is not None else getter())
        def apply_snapshot(self, snapshot):
            if snapshot.get('repository','').lower()!=slug.lower() or snapshot.get('read_only') is not True:
                raise ValueError('Actions workspace repository/authority mismatch')
            # Refresh cannot silently change selection to a different workflow.
            prior = (self._payload['items'][self._selected]['kind'],self._payload['items'][self._selected]['id'])                     if self._payload and self._payload['items'] and self._selected<len(self._payload['items']) else None
            self._payload=snapshot
            ids=[(x['kind'],x['id']) for x in snapshot['items']]
            self._selected=ids.index(prior) if prior in ids else 0
            self._render()
        def _render(self):
            if self._payload is None:return
            compact = self.size.width < 65 or self.size.height < 22
            data=screen_lines(self._payload,compact=compact,selected=self._selected)
            for key,name in [('title','#label'),('rows','#rows'),('detail','#selected'),('footer','#note')]:
                self.query_one(name,Static).update(Text(data[key]))
        def on_resize(self,event):self._render()
        def action_next_item(self):
            if self._payload and self._payload['items']:
                self._selected=min(len(self._payload['items'])-1,self._selected+1);self._render()
        def action_previous_item(self):
            if self._payload and self._payload['items']:
                self._selected=max(0,self._selected-1);self._render()
        def action_refresh(self):
            try:self.apply_snapshot(getter())
            except Exception:
                self.query_one('#note',Static).update(Text('Refresh rejected: existing local snapshot retained.'))
    return ActionsWorkspace()
