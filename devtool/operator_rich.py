"""UX-RICH02: shared *presentation-only* Rich operator views for Devtool.

All operations remain owned by their existing native Devtool producers. This
module neither polls, executes, modifies files, records sessions, nor decides
whether a source, CI check or release is qualified. The optional browser is
an explicit read-only user action, not a background agent or a Textual host.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import os
import sys
from typing import Any

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .delivery_rich import terminal_rich_enabled
from .inline_live import InlineLive, terminal_geometry
from .posix_input import PosixInput
from .logstore import redact_sensitive_text

# Never interpret an absent or unrecognized status as a success.
_PASS = frozenset(('passed','pass','success','succeeded','completed-successfully',
                   'ready','required_checks_passed','verified'))
_FAIL = frozenset(('failure','failed','error','blocked','refused','conflict',
                   'cancelled','timed_out','rejected','checks_failed'))
_BUSY = frozenset(('running','in_progress','queued','pending','waiting','active','planned'))
_BLOCKED_FIELDS = frozenset(('token','authorization','password','secret','private_key',
                             'client_secret','access_token','refresh_token','credential'))


def operator_enabled(*, args: Any = None, env: Mapping[str,str] | None = None,
                     stdout: Any = None, stdin: Any = None) -> bool:
    if args is not None and any(bool(getattr(args, key, False)) for key in
                                ('json','_devtool_json','quiet','trace')):
        return False
    if args is not None and str(getattr(args,'machine','') or ''):
        return False
    if args is not None and str(getattr(args,'format','') or '').lower()=='json':
        return False
    if args is not None and str(getattr(args,'stdout_payload','auto') or 'auto').lower()!='auto':
        return False
    return terminal_rich_enabled(env=env,stdout=stdout,stdin=stdin)


def _secure(key: object, value: object, *, limit: int = 150) -> str:
    k=str(key or '').lower()
    if any(word in k for word in _BLOCKED_FIELDS):
        return '[redacted]'
    if isinstance(value, Mapping):
        return f'{len(value)} fields'
    if isinstance(value, (list,tuple)):
        return f'{len(value)} records'
    if value is None:
        return '—'
    raw=redact_sensitive_text(str(value))
    raw=''.join(' ' if ord(ch)<32 or 127<=ord(ch)<160 or ord(ch) in (0x202a,0x202b,0x202c,0x202d,0x202e,0x2066,0x2067,0x2068,0x2069) else ch for ch in raw)
    return raw[:limit-1]+'…' if len(raw)>limit else raw


def _style(value: object) -> str:
    state=str(value or '').strip().lower().replace(' ','_')
    if state in _PASS: return 'green'
    if state in _FAIL: return 'bold red'
    if state in _BUSY: return 'cyan'
    return 'yellow' if state in ('unknown','unverified','skipped','missing') else 'dim'


def _mapping(value: object) -> Mapping[str,Any]:
    return value if isinstance(value, Mapping) else {}


def _records(value: object) -> list[Any]:
    if isinstance(value, Mapping):
        return [{**v, 'id':v.get('id',k)} if isinstance(v,dict) else {'id':k,'value':v}
                for k,v in value.items()]
    return list(value) if isinstance(value,(list,tuple)) else []


def _field(data: Mapping[str,Any], *names: str) -> object:
    for key in names:
        if data.get(key) not in (None,''):
            return data[key]
    return None


def _line(data: Mapping[str,Any], keys: tuple[str,...], *, limit: int=6) -> Table:
    table=Table.grid(padding=(0,2),expand=True)
    table.add_column(style='dim',width=18,no_wrap=True)
    table.add_column(ratio=1,overflow='fold')
    n=0
    for key in keys:
        if key in data and data[key] not in (None,''):
            v=data[key]
            table.add_row(key.replace('_',' '),Text(_secure(key,v),style=_style(v) if key in ('status','state','decision','disposition') else 'default'))
            n+=1
        if n>=limit:break
    return table


def _rows(values: object, *, kind: str, width: int, selected: int = 0,
          detail: bool=False, limit: int=35) -> object:
    records=_records(values)
    if not records:
        return Text('No records returned by native authority',style='dim')
    narrow=width<68
    table=Table(box=None,show_header=not narrow,expand=True,padding=(0,1))
    table.add_column('Item',ratio=3,overflow='fold')
    if not narrow:
        table.add_column('State',width=12,no_wrap=True,overflow='ellipsis')
        table.add_column('Identity',width=15,no_wrap=True,overflow='ellipsis')
    start=max(0,min(selected,len(records)-1)-limit+1)
    for i,item in enumerate(records[start:start+limit],start):
        row=_mapping(item)
        if row.get('run') and isinstance(row.get('run'),Mapping):
            row={**_mapping(row['run']),**{k:v for k,v in row.items() if k!='run'}}
        label=_field(row,'name','label','title','command_id','workflow','path','kind','type','session','id','run_id','session_id','event_id')
        if label is None and not isinstance(item,Mapping): label=item
        status=_field(row,'conclusion','status','state','classification','result','disposition')
        identity=_field(row,'run_id','id','session_id','attempt','phase_node_id','head_sha','node_id','event_id')
        label_text=Text(('› ' if i==selected else '  ')+_secure('item',label,limit=82),style='bold' if i==selected else '')
        status_text=Text(_secure('status',status,limit=30),style=_style(status))
        if narrow:
            label_text.append('  ')
            label_text.append_text(status_text)
            table.add_row(label_text)
        else:
            table.add_row(label_text,status_text,_secure('identity',identity,limit=30))
        if detail and i==selected:
            remaining={k:v for k,v in row.items() if k not in ('name','label','title','status','state','conclusion','id','run_id','command_id')}
            if remaining:
                table.add_row(*([Text('    '+', '.join(f'{_secure(k,k,limit=20)}: {_secure(k,v,limit=55)}' for k,v in list(remaining.items())[:5]),style='dim')]+(['',''] if not narrow else [])))
    if len(records)>limit:
        table.add_row(*([f'Rows {start+1}–{min(start+limit,len(records))}/{len(records)}; additional records in native JSON']+(['',''] if not narrow else [])))
    return table


# These are existing provider/journal fields, not new transport or authority.
_HIERARCHY = ('items', 'workflows', 'runs', 'attempts', 'jobs', 'steps',
              'nodes', 'phases', 'events', 'checks', 'sessions', 'entries')


def operator_items(payload: Mapping[str,Any]) -> list[Any]:
    """Visible collection, in native order; nested identities never guessed."""
    for name in _HIERARCHY:
        if name in payload and isinstance(payload[name], (Mapping,list,tuple)):
            return _records(payload[name])
    data=_mapping(payload.get('data'))
    if data:
        return operator_items(data)
    return []


def operator_drilldown(payload: Mapping[str,Any], index: int
                       ) -> tuple[Mapping[str,Any],str] | None:
    """Follow only existing nested receipts; never implicitly make a GET."""
    rows=operator_items(payload)
    if not 0 <= index < len(rows):return None
    record=rows[index]
    if not isinstance(record,Mapping):return None
    for key in _HIERARCHY:
        if key in record and isinstance(record[key],(Mapping,list,tuple)):
            label=str(_field(record,'name','title','id','run_id','session_id') or 'selected')
            # Preserve source/provenance for context, never synthesize new status.
            nested={'items':_records(record[key]), 'context':label,
                    'repository':payload.get('repository'),
                    'source_sha':payload.get('source_sha'),
                    'head_sha':payload.get('head_sha')}
            return nested,key
    return None


def _pipeline_graph(payload: Mapping[str,Any], *, width: int) -> object | None:
    """Source-backed DAG edges. A node can depend on many parents; show all.

    This is a dependency-labelled tree *projection*, not an alternate graph
    evaluator. Legacy journal nodes without edges are explicitly unknown.
    """
    raw=payload.get('nodes',payload.get('graph'))
    if raw is None:return None
    rows=_records(raw)
    if not rows:return None
    tree=Tree(Text('Pipeline nodes (native order; needs = incoming edges)',style='bold'))
    for item in rows[:35]:
        node=_mapping(item)
        identity=_secure('id',_field(node,'id','name'),limit=36)
        state=_field(node,'state','status')
        label=Text(identity+'   ')
        label.append(_secure('state',state,limit=25),style=_style(state))
        entry=tree.add(label)
        if 'needs' not in node:
            entry.add(Text('needs: unavailable in older journal',style='yellow'))
        else:
            needs=node.get('needs')
            if not isinstance(needs,list) or any(not isinstance(x,str) for x in needs):
                entry.add(Text('needs: invalid metadata (not inferred)',style='bold red'))
            else:
                entry.add(Text('needs: '+(', '.join(_secure('dependency',x,limit=50) for x in needs[:12])
                                          if needs else 'none'),style='dim'))
        for key in ('run_id','artifact_id'):
            if node.get(key) is not None:
                entry.add(Text(key+': '+_secure(key,node[key],limit=60),style='dim'))
    if len(rows)>35:tree.add(Text(f'{len(rows)-35} more nodes in native JSON',style='dim'))
    return tree


def build_operator_view(kind: str, payload: Mapping[str,Any], *, width: int = 80,
                        selected: int = 0, detail: bool = False, verbose: bool = False) -> Panel:
    """Pure rendering of structured native receipts. No side effects or polling."""
    if not isinstance(payload,Mapping):
        raise TypeError('Operator view requires a structured mapping')
    width=max(1,int(width))
    if kind == 'operator_status':
        from .operator_status import render_status_text
        return Panel(Text(render_status_text(payload)), title='Devtool · Operator Status',
                     border_style='yellow' if payload.get('blockers') else 'cyan',
                     expand=False, padding=(0,1))
    domain={'delivery':'DELIVERY & PR','actions':'GITHUB ACTIONS','pipeline':'PIPELINE DAG',
            'validation':'VALIDATION','runs':'LOCAL RUNS','logs':'LOCAL LOGS',
            'ssh':'SSH WORKERS','overlay':'OVERLAY RECOVERY','transaction':'TRANSACTION',
            'retry':'WORKFLOW RETRY','evidence':'EVIDENCE','qualification':'QUALIFICATION',
            'campaign':'CAMPAIGN'}.get(kind,kind.upper()[:40])
    headline=Text('DEVTOOL  ·  '+domain,style='bold cyan')
    state=_field(payload,'state','status','decision','disposition','classification')
    if state is not None:
        headline.append('   '+_secure('state',state,limit=36),style=_style(state))
    blocks: list[object]=[headline]
    fields=('repository','repo_root','branch','head_sha','source_sha','id','session_id',
            'stage','status','state','disposition','next_action','reason','message',
            'target','profile','destination','run_id','transaction_id','original_run_id','revision','count')
    blocks.append(_line(payload,fields,limit=8 if verbose else 5))
    data=_mapping(payload.get('data'))
    if kind=='validation' and data:
        fields2=('session_id','revision','status','state','current_phase','phase','next_action')
        blocks.append(_line(data,fields2,limit=5))
    if kind=='delivery':
        ci=_mapping(payload.get('ci'))
        if ci:
            blocks.append(Text('Native CI receipt',style='bold'))
            blocks.append(_line(ci,('state','checks_qualified','classification','aggregate','policy_sha256'),limit=5))
        if payload.get('receipt'):
            receipt=_mapping(payload['receipt'])
            blocks.append(_line(receipt,('session_id','status','ci_state','next_safe_action','head_sha'),limit=5))
    if kind=='qualification':
        q=_mapping(payload.get('qualification'))
        blocks.append(_line(q,('qualified','status','reason','contract_id'),limit=5))
    if kind=='pipeline':
        graph=_pipeline_graph(payload,width=width)
        if graph is not None:blocks.append(graph)
    if kind=='actions':
        items=operator_items(payload)
        if items and 0 <= selected < len(items):
            current=_mapping(items[selected])
            blocks.append(Text('Selected native identity '+str(selected+1)+'/'+str(len(items)),style='bold'))
            blocks.append(_line(current,('kind','name','id','run_id','source_sha','status','state','advice'),limit=7))
    collections={
        'actions':('items','runs','jobs','nodes','checks','workflows'),
        'pipeline':(),
        'validation':('phases','events','checks'),
        'runs':('runs','items','sessions'),
        'logs':('entries',),
        'ssh':('sessions','runs','records','items','checks'),
        'overlay':('conflicts','active_transactions','marker_issues','messages','blockers'),
        'transaction':('blockers','transactions'),
        'retry':('nodes','selected','skipped','failures','results'),
        'evidence':('blockers','failures','issues','checks'),
        'qualification':('blockers','checks','issues'),
        'campaign':('generations','steps'),
        'delivery':('runs','missing','failures'),
    }
    sources=[payload]
    if data:sources.append(data)
    seen=set()
    for source in sources:
        for name in collections.get(kind,('items',)):
            if name in seen: continue
            value=source.get(name)
            if value is None:continue
            seen.add(name)
            blocks.append(Text(name.replace('_',' ').upper()+' · '+str(len(_records(value)))+' recorded',style='bold'))
            blocks.append(_rows(value,kind=kind,width=width,selected=selected,detail=detail))
    if kind in ('actions','pipeline') and isinstance(payload.get('item'),Mapping):
        blocks.append(_line(_mapping(payload['item']),('repository','name','id','run_id','status','state','head_sha'),limit=7))
    if kind=='actions' and isinstance(payload.get('remote'),Mapping):
        blocks.append(_line(_mapping(payload['remote']),('repository','id','run_attempt','head_sha','status','conclusion'),limit=7))
    if kind in ('evidence','qualification','delivery'):
        blocks.append(Text('Source / CI / release qualification is authoritative only in native receipts.',style='dim'))
    if verbose and payload.get('schema_version') is not None:
        blocks.append(Text('schema '+_secure('schema_version',payload['schema_version']),style='dim'))
    from .m6_live import shared_panel
    return shared_panel('Devtool · Operator Workspace', blocks,
                        color='red' if _style(state)=='bold red' else 'cyan',width=width)


def render_operator(kind: str, payload: Mapping[str,Any], *, args: Any = None,
                    console: Console | None = None) -> bool:
    """True only after actual interactive Rich output; otherwise emit nothing."""
    if not operator_enabled(args=args):return False
    try:
        out=console or Console()
        out.print(build_operator_view(kind,payload,width=out.width,
                                      verbose=bool(getattr(args,"verbose",False))))
    except Exception:
        # Presentation failure cannot alter native success/failure or block an
        # existing read-only status operation. Caller prints its plain fallback.
        return False
    return True


def browse_operator(kind: str, initial: Mapping[str,Any], *, args: Any = None,
                    refresh: Callable[[],Mapping[str,Any]] | None = None,
                    remote_views: Mapping[str,Callable[[Mapping[str,Any]],Mapping[str,Any]]] | None = None,
                    console: Console | None = None) -> bool:
    """Foreground read-only key browser. Only r invokes supplied native GET.

    Enter drills into existing nested records, h/backspace returns, j/k select,
    v changes detail, r refreshes local data, g/w/l invoke only explicitly
    provided existing read-only Actions APIs; q detaches. No implicit GET.
    Never invokes mutation APIs or starts background refresh. On non-POSIX or
    non-TTY terminals falls back to one-shot Rich rendering.
    """
    if not operator_enabled(args=args):return False
    out=console or Console()
    if os.name!='posix' or not sys.stdin.isatty():
        out.print(build_operator_view(kind,initial,width=out.width))
        return True
    fd=sys.stdin.fileno()
    selected=0;detail=False;verbose=False;snapshot=initial;trail=[]
    try:
        out.width,out.height=terminal_geometry(out,fd)
        with PosixInput(fd) as keys, InlineLive(build_operator_view(kind,snapshot,width=out.width),console=out) as live:
            while True:
                key=keys.read(.2)
                if key=='':break
                resized=live.resize(fd)
                if key is None:
                    if resized:live.update(build_operator_view(kind,snapshot,width=out.width,selected=selected,detail=detail,verbose=verbose))
                    continue
                key={'up':'k','down':'j'}.get(key,key)
                if key=='q': break
                rows=operator_items(snapshot)
                if key=='j':selected=min(max(0,len(rows)-1),selected+1)
                elif key=='k':selected=max(0,selected-1)
                elif key in ('\n','\r'):
                    child=operator_drilldown(snapshot,selected)
                    if child is not None:
                        trail.append((snapshot,selected,detail))
                        snapshot=child[0]
                        selected=0
                        detail=False
                    else:detail=not detail
                elif key in ('\x7f','h'):
                    if trail:snapshot,selected,detail=trail.pop()
                elif key=='v':verbose=not verbose
                elif key in ('w','g','l') and remote_views is not None and key in remote_views:
                    # Explicit opt-in key uses the already-existing native
                    # read-only Actions operator. The original selected row is
                    # pinned before the GET; no implicit attempt, dispatch,
                    # archive collection or source inference is allowed here.
                    choice=_mapping(rows[selected]) if 0 <= selected < len(rows) else {}
                    try:
                        observed=remote_views[key](choice)
                        if not isinstance(observed,Mapping) or observed.get('read_only') is False:
                            raise ValueError('Read-only provider result invalid')
                    except Exception as exc:
                        observed={'state':'refused', 'reason':'Native read-only query failed ('+
                                   type(exc).__name__+'); no remote mutation performed'}
                    trail.append((snapshot,selected,detail))
                    snapshot=observed
                    selected=0
                    detail=False
                elif key=='r' and refresh is not None:
                    # Refresh only on keypress. Do not reattach stale nested
                    # selection to new source/attempt identities.
                    snapshot=refresh()
                    selected=0
                    trail.clear()
                    detail=False
                else:continue
                width=out.width
                live.update(build_operator_view(kind,snapshot,width=width,selected=selected,
                                                detail=detail,verbose=verbose),refresh=True)
    except KeyboardInterrupt:
        return True
    except Exception:
        return False
    return True

__all__=['operator_enabled','render_operator','browse_operator','build_operator_view',
         'operator_items','operator_drilldown']
