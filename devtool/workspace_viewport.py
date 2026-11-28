"""Height-bounded Rich workspace projection with a sticky command footer.

A row or log source position is independent of the physical terminal geometry.
Rendering consumes only native snapshots; it does not poll or change selection.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text

from .operator_rich import _secure, _style, operator_items
from .display_graphemes import display_clusters


@dataclass(frozen=True)
class LogCursor:
    line: int = 0
    offset: int = 0


def _chunks(line: str, width: int, offset: int = 0):
    """Yield width-bounded slices from one source line, including empty lines."""
    if not line:
        yield '', 0
        return
    start = offset
    end = offset
    used = 0
    for cluster in display_clusters(line,offset=offset):
        cells = cell_len(cluster)
        if used and used + cells > width:
            yield line[start:end], end
            start = end
            used = 0
        used += cells
        end += len(cluster)
    if end > start:
        yield line[start:end], end


def log_page(lines: Sequence[str], cursor: LogCursor, width: int, height: int) -> tuple[list[str], LogCursor | None]:
    """Fold only the visible source lines and retain the next source anchor."""
    width, height = max(1, width), max(1, height)
    if not lines:
        return [], None
    line_no = max(0, min(cursor.line, len(lines)-1))
    output: list[str] = []
    for i in range(line_no, len(lines)):
        offset = min(max(0, cursor.offset), len(lines[i])) if i == line_no else 0
        for piece, end in _chunks(lines[i], width, offset):
            output.append(piece)
            if len(output) == height:
                next_cursor = (LogCursor(i,end) if end < len(lines[i]) else
                               LogCursor(i+1) if i+1 < len(lines) else None)
                return output, next_cursor
    return output, None


def _row(item: Any) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        return {'name': item}
    if isinstance(item.get('run'), Mapping):
        return {**item, **item['run']}
    return item


def row_label(item: Any) -> str:
    row = _row(item)
    return _secure('name', next((row[k] for k in ('name','label','title','command_id','workflow','path','kind','id','run_id','session_id','event_id') if row.get(k) not in (None,'')), 'Unnamed record'), limit=300)


def row_status(item: Any) -> str:
    row = _row(item)
    return _secure('status', next((row[k] for k in ('conclusion','status','state','classification','result','disposition') if row.get(k) not in (None,'')), 'unknown'), limit=36)


def _one(text: str, width: int, style: str = '') -> Text:
    return Text(text, style=style, no_wrap=True, overflow='ellipsis', end='\n')


def detail_lines(item: Any) -> tuple[str,...]:
    row = _row(item)
    fields = ('name','label','title','command_id','kind','path','id','run_id','run_attempt','number','session_id','event_id','workflow_id','repository','head_sha','source_sha','status','conclusion','state','provider','created_at','started_at','completed_at','finished_at','timestamp','message','reason','advice','event','domain','chronology','revision','update_count')
    lines = [f'{key.replace("_"," ")}: {_secure(key,row[key],limit=2000)}' for key in fields if row.get(key) is not None]
    if isinstance(row.get('detail'), Mapping):
        for key, value in row['detail'].items():
            lines.append(f'{_secure("field",key,limit=100)}: {_secure(key,value,limit=1000)}')
    return tuple(lines) or (row_label(row),)


HELP_LINES = ('1–5 tabs · j/k or arrows: select', 'Enter: details / cached children',
           'g: explicit Actions/SSH GET · w: workflows', 'l: lineage / selected job log',
           '[ / ]: previous / next page', 'b: back · r: refresh local · a: auto',
           'Events: f/s filters · / search · c clear', '?: help · q: detach (no cancel)')


def page_capacity(height: int, warning: bool, help_open: bool) -> int:
    height=max(1,height)
    chrome=min(3+int(warning),max(0,height-2))
    available=max(0,height-chrome-1)
    return max(1,available-int(available>1 and not help_open))

def render_workspace(*, width: int, height: int, heading: str, status: str,
                     warning: str, tab: str, snapshot: Mapping[str,Any],
                     selected: int, rows: Sequence[Any] | None = None,
                     detail: bool = False, help_open: bool = False,
                     cursor: LogCursor = LogCursor(), footer: str = '') -> Group:
    width, height = max(1,width), max(1,height)
    rows = list(operator_items(snapshot) if rows is None else rows)
    selected = max(0,min(selected,len(rows)-1))
    # The compact footer always occupies exactly one physical line. At very
    # small heights preserve controls before optional chrome.
    chrome = [_one(heading,width,'bold cyan'), _one(status,width,'dim')]
    if warning:
        chrome.append(_one(warning,width,'bold yellow'))
    label = _secure('kind',snapshot.get('kind') or tab,limit=60).replace('-',' ').upper()
    title = f'{label} · {len(rows)} records' if rows else label
    if snapshot.get('state'):
        title += ' · '+_secure('state',snapshot['state'],limit=36)
    chrome.append(_one(title,width,'bold'))
    chrome = chrome[:max(0,height-2)]
    available = max(0,height-len(chrome)-1)
    body: list[Text] = []
    if help_open:
        content = HELP_LINES
        rendered, _ = log_page(content,cursor,width,max(1,available))
        body.extend(_one(x,width) for x in rendered[:available])
    elif 'log_lines' in snapshot or (detail and rows) or (not rows and snapshot.get('read_only') is True):
        lines = snapshot.get('log_lines', ()) if 'log_lines' in snapshot else detail_lines(rows[selected] if rows else snapshot)
        if available>1:
            body.append(_one(f'Line {min(cursor.line+1,max(1,len(lines)))}/{len(lines)} · [ / ] page',width,'dim'))
        rendered,_ = log_page(lines,cursor,width,max(1,available-len(body)))
        body.extend(_one(x,width) for x in rendered[:max(0,available-len(body))])
    elif rows and available:
        # Reserve a readable selected record when there is room, and place the
        # list window around it. Every list row is one physical display line.
        list_height = max(1,available-(2 if available>=4 else 0))
        first = max(0,min(selected-list_height//2,len(rows)-list_height))
        for i,item in enumerate(rows[first:first+list_height],first):
            state=row_status(item)
            marker='›' if i==selected else ' '
            value=Text(f'{marker} {state.upper()} · {row_label(item)}',no_wrap=True,overflow='ellipsis',style='bold' if i==selected else '')
            if state:
                value.stylize(_style(state),2,2+len(state))
            body.append(value)
        if available-len(body)>=1:
            body.append(_one(f'Selected {selected+1}/{len(rows)} · {row_label(rows[selected])}',width,'bold'))
        if available-len(body)>=1:
            row=_row(rows[selected]); identity=next((row[k] for k in ('id','run_id','session_id','event_id','number') if row.get(k) is not None),'—')
            body.append(_one(f'ID {_secure("id",identity)} · {row_status(row)}',width,'dim'))
    elif available:
        body.append(_one(_secure('message',snapshot.get('message') or snapshot.get('note') or 'No records observed',limit=300),width,'dim'))
    footer_text = footer or 'q detach · ? help · j/k · Enter · b back'
    return Group(*chrome,*body[:available],_one(footer_text,width,'reverse'))
