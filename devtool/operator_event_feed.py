"""UX-RICH04: bounded read-only projections of existing Devtool event records.

There is NO new queue, event writer, execution scheduler, or persisted event state.
RunCatalog events are coalesced and have no canonical chronology; validation
session events are append-only and timestamped. These authorities are kept
visibly separate, and only allowlisted fields enter the terminal rendering.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .operator_rich import _secure

DOMAIN_FILTERS = ('all', 'validation', 'runs', 'm8')
STATE_FILTERS = ('all', 'failure', 'active', 'terminal')
_MAX_EVENTS = 120
_MAX_QUERY = 64
_FAILURE = frozenset({'failed', 'failure', 'blocked', 'error', 'cancelled', 'timed_out', 'refused'})
_ACTIVE = frozenset({'active', 'running', 'in_progress', 'waiting', 'queued', 'pending'})
_TERMINAL = frozenset({'success', 'succeeded', 'passed', 'failed', 'failure', 'blocked',
                       'cancelled', 'timed_out', 'skipped', 'completed'})
_ALLOWED_DETAIL = ('status', 'state', 'phase', 'phase_name', 'node_id', 'process_id',
                   'step', 'attempt_id', 'result', 'outcome', 'reason_code')


def _string(value: object, limit: int = 120) -> str:
    return _secure('event_text', value, limit=limit)


def _details(payload: object) -> dict[str, str]:
    """No argv/env/stdout/stderr/raw message/token/private-path disclosure."""
    if not isinstance(payload, Mapping):
        return {}
    safe={key: _string(payload[key], 110) for key in _ALLOWED_DETAIL
          if key not in ('status','state') and key in payload
          and isinstance(payload[key], (str, int, float, bool))}
    for key in ('status','state'):
        if key in payload:safe[key]=_status(payload[key])
    return safe


def _status(value: object) -> str:
    # Native event payloads may be partially untrusted. Show only canonical
    # status tokens; never expose arbitrary embedded text as a state.
    candidate=str(value or 'unknown').strip().lower().replace(' ', '_')
    allowed=_FAILURE | _ACTIVE | _TERMINAL | {'unknown', 'ready', 'paused', 'incomplete',
                                              'unavailable', 'interrupted', 'stopped'}
    return candidate if candidate in allowed else 'unknown'


def _run_events(root: Path, *, limit: int) -> list[dict[str, Any]]:
    from .core_runtime import private_run_root
    from .run_store import RunCatalog
    catalog = RunCatalog.open(private_run_root(root), create=False)
    result=[]
    # Catalog order is NOT a real event timeline; the record supplies no date.
    # Limit materialization and size. Keep insertion order, never invent time.
    for eid, event in (list(catalog.events.items())[-limit:] if limit else []):
        payload = event.payload if isinstance(event.payload,Mapping) else {}
        safe = _details(payload)
        status = _status(payload.get('status') or payload.get('state'))
        result.append({'domain':'runs','event_id':str(event.event_id or eid)[:80],
                       'run_id':str(event.run_id)[:80],
                       'kind':_string(event.kind,70),
                       'status':status,'timestamp':None,
                       'update_count':int(event.update_count),
                       'detail':safe,
                       'chronology':'coalesced; timestamp unavailable'})
    return result


def _validation_events(root: Path, *, limit: int) -> tuple[list[dict[str, Any]],str]:
    from .validation_sessions import load_active_session, read_events
    session = load_active_session(root, required=False)
    if session is None:
        return [], 'no active session'
    rows=[]
    for item in read_events(session, limit=limit):
        if not isinstance(item, Mapping):continue
        eid=str(item.get('event_id') or '')
        if not eid:continue
        timestamp = item.get('timestamp')
        safe = _details(item.get('data'))
        state=_status(item.get('state'))
        rows.append({'domain':'validation','event_id':eid[:80],
                     'session_id':str(item.get('session_id') or session.session_id)[:80],
                     'kind':_string(item.get('type') or 'validation-event',70),
                     'status':state,
                     'timestamp':_string(timestamp,54) if timestamp is not None else None,
                     'revision':item.get('revision') if type(item.get('revision')) is int else None,
                     'detail':safe,'chronology':'append-only validation events'})
    return rows, str(session.session_id)


def native_events(root: Path, *, limit: int = _MAX_EVENTS) -> dict[str, Any]:
    """Read canonical local producer stores, create no files, do no network GET."""
    if type(limit) is not int or not 1 <= limit <= _MAX_EVENTS:
        raise ValueError('Event feed limit must be 1..120')
    # Source quotas ensure a noisy run catalog cannot starve validation proof.
    run_limit = limit // 2
    val_limit = limit - run_limit
    from .m8_automation import m6_events
    m8_records = m6_events(root, limit=max(1, min(40, limit // 4)))
    if m8_records:
        run_limit = (limit - len(m8_records)) // 2
        val_limit = limit - len(m8_records) - run_limit
    runs = _run_events(root, limit=run_limit)
    validations, sid = _validation_events(root, limit=val_limit)
    return {'kind':'devtool.native-event-feed','read_only':True,
            'state':'observed' if runs or validations else 'no-records',
            'events':runs + validations + m8_records,
            'run_count':len(runs),'validation_count':len(validations),
            'validation_session':sid,'m8_count':len(m8_records),'count':len(runs) + len(validations) + len(m8_records),
            'source':'canonical RunCatalog coalesced events + validation_sessions.read_events',
            'chronology':'No cross-authority event ordering is asserted',
            'release_qualified':False}


def event_key(event: Mapping[str,Any]) -> str:
    """Key includes native authority; equal event IDs across producers are distinct."""
    domain=str(event.get('domain') or '')
    identity=str(event.get('event_id') or '')
    return domain+':'+identity if domain in ('validation','runs','m8') and identity else ''


def _severity(event: Mapping[str,Any]) -> str:
    status=_status(event.get('status'))
    return 'failure' if status in _FAILURE else ('active' if status in _ACTIVE else
                                                'terminal' if status in _TERMINAL else 'unknown')


def visible_events(snapshot: Mapping[str,Any], *, domain: str = 'all',
                   state: str = 'all', query: str = '') -> list[dict[str, Any]]:
    if domain not in DOMAIN_FILTERS or state not in STATE_FILTERS:
        raise ValueError('Invalid native event filter')
    needle = query.casefold()[:_MAX_QUERY]
    rows=[]
    for value in snapshot.get('events', ()):
        if not isinstance(value,Mapping):continue
        if domain!='all' and value.get('domain')!=domain:continue
        if state=='terminal' and _status(value.get('status')) not in _TERMINAL:continue
        if state not in ('all','terminal') and _severity(value)!=state:continue
        if needle:
            # Never search hidden payload fields. Only sanitized public labels.
            searchable=' '.join(_string(value.get(name),90) for name in
                                ('domain','event_id','run_id','session_id','kind','status'))
            if needle not in searchable.casefold():continue
        rows.append(dict(value))
    return rows[:_MAX_EVENTS]


def event_view(snapshot: Mapping[str,Any], *, width: int=80, selected: int=0,
               domain: str='all', state: str='all', query: str='',
               editing: bool=False, draft: str='', detail: bool=False,
               recent: set[str] | frozenset[str] = frozenset()) -> Panel:
    """Display actual recorded status; no invented percent, run timing, or green."""
    width=max(40,min(180,int(width)))
    rows=visible_events(snapshot,domain=domain,state=state,query=query)
    header=Text('NATIVE EVENTS  ·  '+str(len(rows))+' visible / '+
                str(snapshot.get('count',0))+' observed',style='bold cyan')
    scope=Text('Run events: coalesced  ·  Validation: append-only  ·  M8: local observations',style='dim')
    filters=Text(f'Source [{domain}]  Status [{state}]  Search [{query[:_MAX_QUERY]}]',style='dim')
    if editing:
        filters.append('  /'+draft+'▌',style='bold yellow')
    table=Table(box=None,show_header=width>=68,expand=True,padding=(0,1))
    table.add_column('Event',ratio=3,overflow='fold')
    table.add_column('State',ratio=1,overflow='ellipsis')
    if width>=78:table.add_column('Source',width=11)
    for index,event in enumerate(rows[:35]):
        marker=('● ' if event_key(event) in recent else '› ' if index==selected else '  ')
        title=Text(marker+_string(event.get('kind'),70),
                   style='bold' if index==selected else '')
        current=_status(event.get('status'))
        style=('red' if current in _FAILURE else 'cyan' if current in _ACTIVE else
               'green' if current in ('success','succeeded','passed') else 'yellow')
        row=[title,Text(current,style=style)]
        if width>=78:row.append(_string(event.get('domain'),14))
        table.add_row(*row)
    if len(rows)>35:
        extra=[Text('… '+str(len(rows)-35)+' other matching events',style='dim'),Text('')]
        if width>=78:extra.append('')
        table.add_row(*extra)
    blocks=[header,scope,filters,table]
    if recent:
        blocks.append(Text('● New or updated canonical record since the previous snapshot (not a timestamp)',style='dim'))
    if detail and rows and 0<=selected<len(rows):
        chosen=rows[selected]
        metadata=Table.grid(padding=(0,2))
        metadata.add_column(style='dim');metadata.add_column(overflow='fold')
        for key in ('domain','event_id','run_id','session_id','kind','status',
                    'timestamp','revision','update_count','chronology'):
            if chosen.get(key) is not None:
                metadata.add_row(key,_string(chosen[key],110))
        for key,value in chosen.get('detail',{}).items():
            metadata.add_row(key,_string(value,100))
        blocks += [Text('Native identity and allowlisted details',style='bold'),metadata]
    if snapshot.get('state') not in ('observed','no-records'):
        blocks.append(Text('Native event observation unavailable or stale',style='bold red'))
    blocks.append(Text('f source · s status · / search · c clear · j/k select · Enter details',style='dim'))
    return Panel(Group(*blocks),title='Devtool · UX-RICH04 Event Explorer',
                 border_style='cyan',expand=False,padding=(0,1))

__all__=['DOMAIN_FILTERS','STATE_FILTERS','native_events','visible_events','event_key','event_view']
