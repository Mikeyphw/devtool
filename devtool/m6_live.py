"""M6 native portrait-first *projection* for overlays, CI, runs and operator data.

No GitHub client, source mutation, orchestration, clipboard, network, qualification
or guessed progress lives here. Rich Live is always inline and terminal-safe.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import os
import re
import sys
from typing import Any

from rich.console import Console, Group, ConsoleOptions, RenderResult
from rich.segment import Segment
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Provider output, filenames, paths and journal fields are ALL untrusted text.
_ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-_])')
_SECRETS = (
    re.compile(r'(?i)\b(?:Bearer|Basic)\s+[^\s,;]+'),
    re.compile(r'(?i)\b(?:github_pat_[A-Za-z0-9_]{10,}|gh[pousr]_[A-Za-z0-9_]{8,})\b'),
    re.compile(r'(?i)\b(?:token|authorization|password|secret|api[_-]?key|private[_-]?key)\s*[:=]\s*[^\s,;]+'),
    re.compile(r'-----BEGIN\s+[^-]*(?:PRIVATE|SECRET)\s+KEY-----', re.I),
)
_SENSITIVE_KEYS = frozenset(('token', 'secret', 'password', 'authorization', 'credential',
                              'private_key', 'api_key', 'client_secret', 'jwt'))
_FAILURE = frozenset(('failure', 'failed', 'timed_out', 'cancelled', 'canceled', 'error',
                     'checks_failed', 'blocked_trigger', 'blocked_ambiguous', 'refused'))
_SUCCESS = frozenset(('success', 'passed', 'pass', 'ok', 'completed_successfully',
                     'required_checks_passed'))
_RUNNING = frozenset(('in_progress', 'running', 'checks_running', 'queued', 'waiting',
                     'pending', 'requested', 'waiting_registration', 'checks_evidence_pending'))


def safe(value: object, *, key: str = '', limit: int = 300) -> str:
    """Remove terminal injection and credentials; Rich Text prevents markup execution."""
    if any(word in key.casefold() for word in _SENSITIVE_KEYS):
        return '[redacted]'
    if value is None:
        return 'not recorded'
    if isinstance(value, (dict, list, tuple, set)):
        return '[structured value omitted]'
    text = _ANSI.sub('', str(value))
    text = ''.join(' ' if ord(ch) < 32 or 127 <= ord(ch) < 160 else ch for ch in text)
    for expression in _SECRETS:
        text = expression.sub('[redacted]', text)
    return text[:limit] + ('…' if len(text) > limit else '')


def _facts(source: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        value = source.get(key)
        if value is not None and value != '':
            return value
    return None


def _status(value: object) -> tuple[str, str]:
    state = str(value or 'not recorded').lower().replace(' ', '_')
    if state in _FAILURE or state.startswith('blocked'):
        return 'FAIL / ' + safe(value, limit=35).upper(), 'bold red'
    if state in _SUCCESS:
        return 'PASS / ' + safe(value, limit=35).upper(), 'green'
    if state in _RUNNING:
        return 'ACTIVE / ' + safe(value, limit=35).upper(), 'cyan'
    if state in ('skipped', 'neutral'):
        return safe(value, limit=35).upper(), 'yellow'
    return safe(value, limit=35).upper(), 'yellow'


def _icon(key: str, mode: str) -> str:
    symbols = {
        'ascii': {'repo':'#','stage':'*','events':'@','logs':'>','evidence':'=','keys':'?'},
        'unicode': {'repo':'◆','stage':'●','events':'◷','logs':'▸','evidence':'◇','keys':'?'},
        'nerd': {'repo':'\ue702','stage':'\uf0ae','events':'\uf017','logs':'\uf15c',
                 'evidence':'\uf02b','keys':'\uf11c'},
    }
    return symbols[mode].get(key, '*')


def icon_mode(env: Mapping[str,str] | None = None) -> str:
    """Use the existing Devtool user icon preference and no new CLI flags."""
    values = os.environ if env is None else env
    forced = str(values.get('DEVTOOL_UI_ICONS') or '').strip().lower()
    if forced in ('nerd','ascii','unicode'):
        return forced
    if str(values.get('DEVTOOL_ASCII') or '').lower() in ('1','yes','true'):
        return 'ascii'
    try:
        from .tui_icons import nerd_font_enabled
        if (nerd_font_enabled() if env is None else nerd_font_enabled(env=values)):
            return 'nerd'
    except (ImportError, RuntimeError, ValueError):
        pass
    return 'unicode'


def _row(label: object, value: object = None, *, key: str = '', style: str = 'default') -> Text:
    line = Text()
    line.append(safe(label, limit=100), style='dim')
    if value is not None:
        line.append(' ')
        line.append(safe(value, key=key, limit=140), style=style)
    line.no_wrap = True
    line.overflow = 'ellipsis'
    return line


def _header(title: str, icon: str, mode: str) -> Text:
    return Text(f'{_icon(icon,mode)}  {title}', style='bold cyan')


def _segment(title: str, icon: str, mode: str, children: Sequence[object]) -> Group:
    return Group(_header(title, icon, mode), *children)



class BoundedLiveFrame:
    """Stable, inline PTY viewport for native Rich Live.

    Render at most terminal height minus a safety margin, and always emit the
    same number of rows on successive updates. A shorter/longer focused view
    therefore cannot leave an earlier frame above the next one. Source data
    and the native stdout/stderr remain unchanged.
    """
    def __init__(self, renderable: object, *, reserve_rows: int = 3):
        self.renderable = renderable
        self.reserve_rows = reserve_rows

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        limit = max(5, options.size.height - self.reserve_rows)
        rendered = console.render_lines(self.renderable, options, pad=False)
        if len(rendered) > limit:
            # Focus pages expose the retained rows without a separate provider
            # request. Do not claim that hidden lines are absent.
            hint = Text('… view clipped · 1–5 inspect sections', style='yellow',
                        no_wrap=True, overflow='ellipsis')
            footer = console.render_lines(hint, options, pad=False)
            rendered = rendered[:limit - 1] + footer[:1]
        while len(rendered) < limit:
            rendered.append([Segment(' ')])
        for i, line in enumerate(rendered):
            yield from line
            if i + 1 < len(rendered):
                yield Segment.line()


def shared_panel(title: str, blocks: Sequence[object], *, color: str = 'cyan',
                 width: int = 80) -> Panel:
    """Shared inline single-column chrome for CI, overlay, and operator views."""
    return Panel(Group(*blocks), title=title, border_style=color, padding=(0,1),
                 expand=True, width=None)


def _limited(rows: Sequence[Any], *, width: int) -> list[Any]:
    # Portrait display is intentionally bounded; native journals are not.
    return list(rows[:(4 if width < 60 else 7 if width < 85 else 12)])


def _as_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [v for v in value if isinstance(v, Mapping)]


def log_page_size(width: int) -> int:
    """Keep each paged log line reachable on portrait terminals."""
    return 4 if width < 60 else 8


def _progress_evidence(item: Mapping[str,Any], width: int) -> Text | None:
    """Render *only* measured native counters. Never infer a percent."""
    done = item.get('completed')
    total = item.get('total')
    if type(done) is int and type(total) is int and 0 <= done <= total and total > 0:
        cells = max(4,min(14,width//5))
        filled = (done*cells)//total
        return _row('    ['+'#'*filled+'-'*(cells-filled)+']',f'{done}/{total}')
    if _facts(item,'conclusion','state','status') in ('running','in_progress'):
        return _row('    activity ongoing · count unrecorded')
    return None


def build_live_view(kind: str, source: Mapping[str, Any], *, width: int = 80,
                    selected: int = 0, detail: bool = False, verbose: bool = False,
                    logs: bool = False, log_lines: tuple[str,...] = (),
                    log_page: int = 0, log_error: str = '',
                    icons: str | None = None, focus: str = 'overview',
                    focus_page: int = 0) -> Group:
    """One projection; no I/O, source inference or mutation side effects."""
    if not isinstance(source, Mapping):
        raise TypeError('M6 native view requires existing structured evidence')
    width = max(40, min(180, int(width)))
    mode = icons or icon_mode()
    if mode not in ('ascii','unicode','nerd'):
        raise ValueError('Unsupported existing icon preference')
    if kind not in ('ci','run','overlay'):
        raise ValueError('Unsupported M6 live evidence type')
    if focus not in ('overview','identity','stages','events','output','evidence'):
        raise ValueError('Unsupported M6 focus page')

    remote = source.get('remote') if isinstance(source.get('remote'), Mapping) else {}
    evidence = source.get('evidence') if isinstance(source.get('evidence'), Mapping) else {}
    native = source.get('native_apply') if isinstance(source.get('native_apply'), Mapping) else {}
    ci = source.get('ci') if isinstance(source.get('ci'), Mapping) else {}
    credential = source.get('credential_health') if isinstance(source.get('credential_health'), Mapping) else {}
    state = (_facts(remote, 'conclusion','status') if kind == 'run' else
             _facts(source, 'state','completion_status','status'))
    if kind == 'ci' and state == 'REQUIRED_CHECKS_PASSED':
        # This is the *native* qualified state, not a guess from green jobs.
        status_msg = 'Native required checks passed; release NOT qualified'
    elif kind == 'ci':
        status_msg = 'Native CI NOT QUALIFIED · release NOT qualified'
    elif kind == 'run':
        status_msg = 'Provider run observed · native qualification independent'
    else:
        status_msg = 'Native commit/session evidence required · no release claim'
    name = {'ci':'PR CHECKS','run':'GITHUB RUN','overlay':'OVERLAY APPLY'}[kind]
    status_label,status_color = _status(state)
    title = Text(f'DEVTOOL · {name}', style='bold')
    title.append('  ' + status_label, style=status_color)

    identity = [
        _row('Repository', _facts(source,'repository','repo_root') or _facts(remote,'repository') or 'not recorded'),
        _row('Branch',_facts(source,'branch','head_branch') or _facts(remote,'head_branch') or 'not recorded'),
        _row('Source',_facts(source,'source_sha','head_sha') or _facts(remote,'head_sha') or 'not recorded'),
    ]
    if kind == 'overlay':
        identity.append(_row('Artifact',_facts(source,'artifact_sha256','artifact','artifact_path')))
        if _facts(source,'runtime_sha') is not None:
            identity.append(_row('Runtime',_facts(source,'runtime_sha')))
    if kind == 'run':
        identity.append(_row('Run',_facts(remote,'id','run_id')))
        identity.append(_row('Attempt',_facts(remote,'run_attempt')))

    rows = (_as_rows(source.get('runs')) if kind == 'ci' else
            _as_rows(source.get('jobs')) if kind == 'run' else
            _as_rows(source.get('stages')))
    stages: list[Text] = []
    if kind == 'ci':
        required = source.get('required') if isinstance(source.get('required'),list) else None
        missing = source.get('missing') if isinstance(source.get('missing'),list) else None
        info = []
        if source.get('poll') is not None:info.append('poll '+safe(source['poll']))
        if required is not None:info.append('required '+str(len(required)))
        info.append('observed '+str(len(rows)))
        if missing is not None:info.append('missing '+str(len(missing)))
        stages.append(_row(' · '.join(info)))
    elif kind == 'run':
        done = sum(x.get('status') == 'completed' for x in rows)
        stages.append(_row(f'Jobs {done}/{len(rows)}' if rows else 'No observed jobs'))
    visible_rows=(rows[:80] if focus=='stages' else _limited(rows,width=width))
    for i,item in enumerate(visible_rows):
        label = _facts(item,'name','label','phase','kind')
        state_item = _facts(item,'conclusion','state','status','result')
        indicator,style=_status(state_item)
        prefix='› ' if i==selected else '  '
        caption=prefix+safe(label,limit=180)
        if width<60:
            # Do not sacrifice workflow names to fit long status labels.
            stages.append(_row(caption))
            stages.append(_row('    '+indicator,style=style))
        else:
            stages.append(_row(caption,indicator,style=style))
        measured = _progress_evidence(item,width)
        if measured is not None:
            stages.append(measured)
        if detail and i==selected:
            identifier = _facts(item,'id','run_id','phase_id','attempt')
            if identifier is not None:stages.append(_row('    ID',identifier))
            if kind=='run':
                for step in _limited(_as_rows(item.get('steps')),width=width)[:3]:
                    flag,stepstyle = _status(_facts(step,'conclusion','status'))
                    stages.append(_row('    '+safe(step.get('name')),flag,style=stepstyle))
            if kind=='ci':
                attempt = _facts(item,'attempt','run_attempt')
                if attempt is not None:stages.append(_row('    Attempt',attempt))
    if kind=='ci' and isinstance(source.get('missing'), list):
        for missing in _limited(source['missing'],width=width)[:3]:
            stages.append(_row('MISSING',missing,style='yellow'))
    if not stages:stages=[_row('No native stages observed')]
    if len(rows)>len(visible_rows):
        stages.append(_row('Additional stages retained in native JSON'))

    events: list[Text]=[]
    provided = _as_rows(source.get('events'))
    for row in (provided[-80:] if focus=='events' else provided[-(2 if width<60 else 4):]):
        stamp = _facts(row,'timestamp','observed_at','at')
        line = _facts(row,'message','name','phase','type','kind')
        if row.get('historical') is True:
            line = 'saved: ' + str(line or 'event')
        detail_state = _facts(row, 'state', 'status', 'conclusion')
        if detail_state is not None:
            line = str(line or 'event') + ' · ' + safe(detail_state)
        events.append(_row(safe(stamp) if stamp else 'time unrecorded',safe(line)))
    if source.get('event_stream_state') == 'UNVERIFIED':
        events.insert(0, _row('Native journal readback unavailable',style='yellow'))
    if kind=='ci' and state=='CHECKS_FAILED':
        diagnosis = source.get('diagnosis') if isinstance(source.get('diagnosis'),Mapping) else {}
        failures = _as_rows(diagnosis.get('failures'))
        for failure in failures[:2]:
            for job in _as_rows(failure.get('jobs'))[:2]:
                events.append(_row('FAIL',_facts(job,'name') or 'native job failed',style='red'))
                for snippet in list(job.get('excerpt') or [])[:(3 if logs else 1)]:
                    events.append(_row('  ',snippet))
    if not events:events.append(_row('No timestamped native events provided'))

    output = [_row('Original stdout/stderr and exit status remain with native command/log')]
    if logs:
        if log_error:
            output.append(_row('Log GET refused',log_error,style='yellow'))
        elif log_lines:
            size = log_page_size(width)
            page_count = (len(log_lines) + size - 1) // size
            current = min(max(0, log_page), page_count - 1)
            offset = current * size
            end = min(len(log_lines), offset + size)
            output.append(_row(f'Log page {current+1}/{page_count}',
                               f'Lines {offset+1}-{end}/{len(log_lines)}'))
            for line in log_lines[offset:end]:
                output.append(_row('│',line))
        else:
            output.append(_row('Logs not fetched; press l on a selected verified run'))
    else:
        output.append(_row('l logs (explicit GET only) · [ prev  ] next' if kind!='overlay' else
                           'Native output is not buffered or replaced by M6'))

    proof: list[Text] = []
    if kind=='ci':
        proof.append(_row('Native CI',_facts(source,'state')))
        for key in ('policy_sha256','source_binding','session_id'):
            if source.get(key) is not None:proof.append(_row(key,source[key]))
    elif kind=='run':
        proof.append(_row('Provider conclusion',_facts(remote,'conclusion','status')))
        proof.append(_row('Source binding',_facts(source,'source_binding')))
    else:
        proof.append(_row('Transaction',_facts(native,'transaction_id') or _facts(source,'transaction_id') or 'not recorded'))
        proof.append(_row('Commit',_facts(native,'commit') or _facts(source,'commit') or 'not recorded'))
        proof.append(_row('Session',_facts(source,'session_id','native_delivery_session_id') or 'not recorded'))
    for key in ('pr_url','pr_head','ci_state'):
        if evidence.get(key) is not None:
            proof.append(_row(key,evidence[key]))
    if credential:
        # Only explicitly supplied *public* fields from existing M4 diagnostics.
        for key in ('auth_mode','app_name','installation_id','repository_coverage',
                    'status','reason','identity_verified','effective_read_verified',
                    'required_capability','granted_capability','expiry_state','permission_drift',
                    'fork_access','base_access','authenticated','authorized_for_operation',
                    'remote_verified','required_ci_qualified'):
            if credential.get(key) is not None:
                proof.append(_row('M4 '+key,credential[key],key=key))
    if not credential:
        proof.append(_row('Credential health', 'UNVERIFIED; no M4 provider diagnostic supplied'))
    else:
        for name in ('authenticated', 'authorized_for_operation', 'remote_verified', 'required_ci_qualified'):
            if name not in credential:
                proof.append(_row('M4 ' + name, 'UNVERIFIED; no evidence'))
    if kind == 'ci' and state == 'REQUIRED_CHECKS_PASSED':
        proof.extend((_row('Native required checks passed'),_row('release NOT qualified')))
    else:
        proof.append(_row(status_msg))
    hints = ('j/k navigate · Enter detail · v verbose · l logs · [ prev  ] next · r refresh · q detach'
             if kind!='overlay' else 'Native lifecycle events only · original logs preserved')

    output_block = _segment('OUTPUT','logs',mode,output)
    blocks = [title,
              _segment('IDENTITY','repo',mode,
                       identity[:(5 if kind=='run' else 4 if kind=='overlay' else 3)]
                       if width<60 else identity),
              _segment('STAGES','stage',mode,stages),
              _segment('EVENTS','events',mode,events),
              output_block,
              _segment('EVIDENCE','evidence',mode,proof[:5 if width<60 else len(proof)]),
              _segment(hints,'keys',mode,[]) if verbose or width>=60 else _row('j/k · Enter · l · v · r · q')]
    feedback_messages = {
        'REFRESH_REQUESTED': 'Refresh requested · native bounded interval only',
        'REFRESH_CONSUMED': 'Refresh shortcut consumed · awaiting native observation',
        'NATIVE_OBSERVATION_RENDERED': 'Native observation rendered · provider verdict unchanged',
    }
    feedback_code = source.get('m6_ui_feedback')
    feedback = feedback_messages.get(feedback_code) if isinstance(feedback_code, str) else None
    if feedback:
        blocks.insert(1, _row('UI', feedback, style='cyan'))

    if verbose and kind in ('run', 'ci'):
        extra = []
        if kind == 'run':
            extra.append(_row('Run status', _facts(remote, 'status')))
        else:
            extra.append(_row('CI state', _facts(source, 'state')))
        if rows:
            current = rows[min(max(0, selected), len(rows)-1)]
            extra.extend([
                _row('Selected', f'{min(max(0, selected),len(rows)-1)+1}/{len(rows)}'),
                _row('Item status', _facts(current, 'status')),
                _row('Item conclusion', _facts(current, 'conclusion')),
            ])
            if kind == 'run':
                extra.append(_row('Recorded steps',len(_as_rows(current.get('steps')))))
        blocks.insert(len(blocks)-1,
                      _segment('VERBOSE DETAILS · VERBOSE ON','evidence',mode,extra))

    if logs and kind in ('run', 'ci'):
        # A requested log inspection must be visible on short portrait PTYs.
        # Preserve every panel, but move OUTPUT directly after IDENTITY;
        # otherwise Live(vertical_overflow='ellipsis') hides the log pages.
        output_index = next(i for i, block in enumerate(blocks)
                            if block is output_block)
        blocks.insert(2 + bool(feedback) + 2 * bool(verbose),
                      blocks.pop(output_index))

    if width >= 100:
        # Full-width, true two-column layout on roomy Termux PTYs. A single
        # panel uses the entire viewport; log inspection is not duplicated.
        # Pure Rich composition only: no provider calls or new facts.
        left = Group(
            _segment('IDENTITY','repo',mode,identity),
            _segment('STAGES','stage',mode,stages),
            _segment('EVENTS','events',mode,events),
        )
        right_parts = [output_block,
                       _segment('EVIDENCE','evidence',mode,proof)]
        if verbose and kind in ('run','ci'):
            right_parts.append(_segment('VERBOSE DETAILS · VERBOSE ON','evidence',mode,extra))
        grid = Table.grid(expand=True, padding=(0,2))
        grid.add_column(ratio=1, overflow='fold')
        grid.add_column(ratio=1, overflow='fold')
        grid.add_row(left,Group(*right_parts))
        blocks = [title]
        if feedback:
            blocks.append(_row('UI',feedback,style='cyan'))
        # Verbose details belong in the evidence column, not in the title area.
        blocks.extend((grid, _row(hints)))

    if logs and width < 100 and kind in ('ci','run'):
        # Log-focus mode prevents short portrait PTYs from clipping requested
        # evidence beneath stages, events and expanded verbose metadata.
        # 'l' restores the complete vertical view, without changing source.
        focused = [title]
        if feedback:
            focused.append(_row('UI',feedback,style='cyan'))
        # Do not displace log data with a redundant verbose banner.
        if kind == 'run':
            focused.extend((_row('Run',_facts(remote,'id')),
                            _row('Attempt',_facts(remote,'run_attempt')),
                            _row('Source',_facts(remote,'head_sha'))))
        else:
            focused.append(_row('Native CI',_facts(source,'state')))
        focused.extend((output_block, _row(status_msg), _row(hints)))
        blocks = focused

    # Focus pages work for CI, provider runs AND overlays. They are pure
    # projections; only the existing CI monitor handles keyboard/provider GET.
    # The overlay runner never captures stdin because native prompts own it.
    page_labels={'identity':'IDENTITY','stages':'STAGES','events':'EVENTS',
                 'output':'OUTPUT','evidence':'EVIDENCE'}
    if focus != 'overview':
        entries = {'identity':identity, 'stages':stages, 'events':events,
                   'output':output, 'evidence':proof}[focus]
        if focus in ('stages','events','evidence'):
            # Bounded pager exposes evidence omitted from the overview.
            page_size = 10 if width < 100 else 18
            page_count = max(1,(len(entries)+page_size-1)//page_size)
            current = min(max(0,int(focus_page)),page_count-1)
            visible = entries[current*page_size:(current+1)*page_size]
            page_info = _row('Page',f'{current+1}/{page_count} · recorded {len(entries)}')
        else:
            visible = entries
            page_info = _row('Details',f'{len(entries)} native rows')
        if focus == 'identity' or focus == 'evidence':
            if width >= 100 and len(visible)>1:
                columns=Table.grid(expand=True,padding=(0,2))
                columns.add_column(ratio=1,overflow='fold')
                columns.add_column(ratio=1,overflow='fold')
                for index in range(0,len(visible),2):
                    columns.add_row(visible[index], visible[index+1] if index+1<len(visible) else '')
                content=[columns]
            else:
                content=list(visible)
        else:
            content=list(visible)
        # The details drawer is anchored BELOW the selected section; never
        # flood the top of a portrait screen with verbose metadata.
        if verbose and kind in ('ci','run') and focus in ('stages','evidence'):
            content.append(_segment('VERBOSE DETAILS · VERBOSE ON','evidence',mode,extra))
        navigation=('0 overview · 1 identity · 2 stages · 3 events · 4 output · 5 evidence')
        shortcuts=('j/k select · Enter details · [ prev  ] next · l verified logs · r refresh · q detach'
                   if kind!='overlay' else 'Native apply runs unmodified · prompts remain native')
        blocks=[title,_row('PAGE',page_labels[focus],style='bold cyan'),page_info,
                _segment(page_labels[focus],
                         {'identity':'repo','stages':'stage','events':'events',
                          'output':'logs','evidence':'evidence'}[focus],mode,content)]
        if feedback and focus!='output':
            blocks.append(_row('UI',feedback,style='cyan'))
        blocks.extend((_row(navigation),_row(shortcuts)))
    elif kind != 'overlay':
        # Keep all section shortcuts discoverable in the full-width overview.
        blocks.append(_row('0 overview · 1 identity · 2 stages · 3 events · 4 output · 5 evidence'))

    color='red' if str(state or '').casefold() in _FAILURE else 'cyan' 
    return Group(shared_panel('M6 · NATIVE LIVE',blocks,color=color,width=width))


class OverlayLiveProjection:
    """Read-only live projection of canonical artifact lifecycle callbacks.

    The native overlay runner owns all execution, output, rollback, transactions,
    source validation, outcomes and logs. UI failures NEVER change its verdict.
    """
    def __init__(self, *, source: Mapping[str, Any], enabled: bool | None = None,
                 console: Console | None = None, env: Mapping[str,str] | None = None):
        from .delivery_rich import terminal_rich_enabled
        self.enabled = terminal_rich_enabled(env=env) if enabled is None else bool(enabled)
        self.console = console or Console()
        self.data = dict(source)
        self.data.setdefault('completion_status', 'in_progress')
        self.stages: deque[dict[str,Any]] = deque(maxlen=60)
        self.events: deque[dict[str,Any]] = deque(maxlen=30)
        self._live: Live | None = None
        self._broken = False

    def _render(self) -> BoundedLiveFrame:
        self.data['stages'] = list(self.stages)
        self.data['events'] = list(self.events)
        return BoundedLiveFrame(build_live_view('overlay',self.data,width=self.console.width))

    def start(self) -> None:
        if not self.enabled or self._broken:return
        try:
            self._live = Live(self._render(),console=self.console,screen=False,
                              transient=False,auto_refresh=False,
                              redirect_stdout=False,redirect_stderr=False,
                              vertical_overflow='ellipsis')
            self._live.start()
        except Exception as exc:
            self._warn(exc)

    def stage(self, kind: str, phase: str, result: Any = None) -> None:
        if not self.enabled or self._broken:return
        state = ('in_progress' if result is None else
                 'skipped' if bool(getattr(result,'skipped',False)) else
                 'success' if getattr(result,'returncode',1)==0 else 'failure')
        item={'name':f'{safe(kind)} · {safe(phase)}','status':state}
        self.stages.append(item)
        meta=getattr(result,'metadata',None)
        moment=(meta.get('timestamp') or meta.get('at')) if isinstance(meta,Mapping) else None
        self.events.append({'name':item['name'], 'status':state,
                            **({'at':moment} if moment is not None else {})})
        self._refresh()

    def finish(self, result: Any) -> None:
        if not self.enabled or self._broken:return
        code = getattr(result,'returncode',None)
        self.data['completion_status'] = ('success' if code==0 and not getattr(result,'skipped',False)
                                          else 'skipped' if getattr(result,'skipped',False)
                                          else 'failure')
        self.events.append({'name':safe(getattr(result,'name','native operation')),
                            'status':self.data['completion_status']})
        metadata = getattr(result,'metadata',None)
        if isinstance(metadata,Mapping):
            if metadata.get('transaction_id') is not None:
                self.data['transaction_id'] = metadata['transaction_id']
            if metadata.get('native_delivery_session_id') is not None:
                self.data['session_id'] = metadata['native_delivery_session_id']
        self._refresh()

    def _refresh(self) -> None:
        if self._live is None or self._broken:return
        try:
            # A native overlay may continue through a Termux zoom/resize.
            # Read the PTY directly, not potentially stale COLUMNS/LINES.
            try:
                geometry=os.get_terminal_size(sys.stdout.fileno())
                self.console.width=max(40,min(180,geometry.columns))
                self.console.height=max(8,min(120,geometry.lines))
            except (OSError,AttributeError,ValueError):
                pass
            self._live.update(self._render(),refresh=True)
        except Exception as exc:
            self._warn(exc)

    def _warn(self, exc: BaseException) -> None:
        self._broken = True
        try:
            self.close()
        finally:
            print('M6_PRESENTATION_WARNING '+type(exc).__name__+
                  '; native execution/result unaffected',file=sys.stderr,flush=True)

    def close(self) -> None:
        live,self._live=self._live,None
        if live is not None:
            try:live.stop()
            except Exception as exc:
                print('M6_PRESENTATION_WARNING '+type(exc).__name__+
                      '; native execution/result unaffected',file=sys.stderr,flush=True)


__all__ = ['safe','icon_mode','log_page_size','shared_panel','build_live_view','BoundedLiveFrame','OverlayLiveProjection']
