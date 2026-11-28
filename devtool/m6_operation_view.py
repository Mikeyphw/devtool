"""M6 launcher boundaries and foreground, read-only native receipt inspection.

No orchestration, subprocess ownership, persistence, network, recovery, or
qualification. A child always owns its terminal during native execution.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
import os
import json
import stat
from pathlib import Path
import re
import sys
from typing import Any, Callable

from rich.console import Console
from .inline_live import InlineLive, terminal_geometry
from .posix_input import PosixInput
from rich.text import Text

from .m6_live import BoundedLiveFrame, safe, shared_panel
from .delivery_rich import terminal_rich_enabled

# Fixed labels, never user-supplied argv, arbitrary environment or prompt text.
LAUNCHER_STAGES = {
    'runtime-source-import': 'Trusted runtime source probe',
    'native-scaffold': 'Native artifact scaffold',
    'native-validate-overlay': 'Native overlay validation',
    'native-atomic-apply-and-commit': 'Native atomic apply and commit',
    'native-committed-history-adoption': 'Native committed history adoption',
    'session-status': 'Native session identity readback',
    'native-required-ci-preflight': 'Native required CI policy preflight',
    'native-push': 'Approved native push', 'native-pr': 'Approved native PR',
    'native-source-bound-ci': 'Native source-bound CI observer',
    'native-ci-machine-receipt': 'Native CI machine receipt',
    'native-saved-report': 'Native saved report',
    'native-session-resume': 'Native read-only resume',
    'native-recovery-plan': 'Native read-only recovery plan',
    'native-dag-plan': 'Native workflow plan',
    'native-artifact-verify': 'Native artifact verification',
    'build-package': 'Source-bound package build',
    'source-preflight': 'Exact source preflight',
    'postcommit-proof': 'Native postcommit source proof',
}
_PAGE_NAMES = ('Overview', 'Identity', 'Stages / inventory', 'Events', 'Output references', 'Evidence')
_TX_ID = re.compile(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}\Z')


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[Mapping[str, Any]]:
    return [x for x in value if isinstance(x, Mapping)] if isinstance(value, (list, tuple)) else []


def _public(value: object) -> str:
    # No raw provider output, arbitrary nested mappings, URL credentials or
    # multiline key material. A view cannot attest the content of a log.
    if not isinstance(value, (str, int, bool, float)) and value is not None:
        return 'structured evidence retained in native receipt'
    if isinstance(value, str) and ('-----BEGIN' in value or re.search(r'(?i)https?://[^/\s]+@|[?&](?:token|sig|signature|key|auth)=', value)):
        return '[redacted]'
    return safe(value, limit=512)


def boundary_enabled(*, args: Any = None, argv: Sequence[str] = ()) -> bool:
    """No stage chrome in machine, redirected, quiet or incompatible modes."""
    if os.environ.get('DEVTOOL_RICH_LIVE') == '0':
        return False
    if args is not None:
        if any(bool(getattr(args, k, False)) for k in ('json', '_devtool_json', 'quiet', 'trace', 'machine', 'machine_file')):
            return False
        if str(getattr(args, 'format', '') or '').lower() == 'json':
            return False
        if str(getattr(args, 'stdout_payload', 'auto') or 'auto').lower() != 'auto':
            return False
    # Conservatively decline any machine-like option, including passthrough.
    for token in argv:
        if str(token).split('=', 1)[0] in {'--json','--machine','--machine-file','--quiet','-q','--trace','--format','--stdout-payload'}:
            return False
    return terminal_rich_enabled() and terminal_rich_enabled(stdout=sys.stderr)


def emit_boundary(label: str, state: str, *, enabled: bool, rc: int | None = None) -> None:
    """Synchronous static line: never hold Live or read child keystrokes."""
    if not enabled:
        return
    try:
        out = Console(file=sys.stderr, markup=False, highlight=False)
        line = Text('M6 · ' + _public(label) + ' · ' + _public(state), style='cyan')
        if rc is not None:
            line.append(' · native return code ' + str(rc), style='green' if rc == 0 else 'red')
        out.print(line)
    except Exception:
        # Native execution, exit status and failure transcript are independent.
        pass


@contextmanager
def launcher_boundary(stage: str):
    label = LAUNCHER_STAGES.get(stage)
    try:
        enabled = bool(label) and boundary_enabled()
    except Exception:
        enabled = False
    emit_boundary(label or '', 'started', enabled=enabled)
    try:
        yield
    except BaseException:
        emit_boundary(label or '', 'interrupted or refused; inspect native diagnostics', enabled=enabled)
        raise
    else:
        emit_boundary(label or '', 'returned; native evidence determines verdict', enabled=enabled)


def wrapper_boundary(command: str, argv: Sequence[str], invoke: Callable[[], int]) -> int:
    try:
        enabled = boundary_enabled(argv=argv)
    except Exception:
        enabled = False
    # Canonical command token only. Do not render user option values or path.
    label = 'Wrapper → ' + (command if re.fullmatch(r'[a-z][a-z0-9-]{0,60}', command) else 'canonical command')
    emit_boundary(label, 'resolved; native command owns terminal', enabled=enabled)
    try:
        code = invoke()
    except BaseException:
        emit_boundary(label, 'interrupted or refused', enabled=enabled)
        raise
    emit_boundary(label, 'returned', enabled=enabled, rc=code)
    return code


def _fields(data: Mapping[str, Any], keys: Sequence[str], prefix: str = '') -> list[tuple[str, str]]:
    return [(prefix + k.replace('_', ' '), _public(data[k])) for k in keys if k in data and data[k] is not None]


def _records(value: object, keys: Sequence[str], prefix: str) -> list[tuple[str, str]]:
    result = []
    for index, item in enumerate(_rows(value), 1):
        result.extend(_fields(item, keys, f'{prefix} {index} · '))
    return result


def read_bound_transaction(repo: Path, identity: str) -> tuple[str, Mapping[str, Any]]:
    """Use native transaction paths/readers, not discovery or recovery routines.

    Absence/ambiguity is explicit. Never call active marker lookup (may prune),
    transaction finalization, archive writing, or recovery.
    """
    if not _TX_ID.fullmatch(identity):
        return 'invalid transaction identity', {}
    try:
        from .transactions import transaction_root, read_transaction, read_transaction_summary
        root = transaction_root(repo)
        active = root / identity / 'transaction.json'
        archive = root / 'history' / (identity + '.json')
        if any(p.is_symlink() for p in (root, active.parent, active, archive.parent, archive)):
            return 'unsafe transaction path', {}
        present = [p for p in (active, archive) if p.exists()]
        if len(present) != 1 or not present[0].is_file():
            return ('ambiguous transaction records' if len(present) > 1 else 'transaction record not available'), {}
        if present[0].stat().st_size > 16 * 1024 * 1024:
            return 'transaction record exceeds inspection limit', {}
        data = read_transaction(active) if present[0] == active else read_transaction_summary(repo, identity)
        if not isinstance(data, Mapping) or data.get('transaction_id') != identity or data.get('repo_root') != str(repo.resolve()):
            return 'transaction identity mismatch', {}
        return ('active' if present[0] == active else 'archived'), data
    except (OSError, ValueError, RuntimeError, ImportError):
        return 'transaction record unavailable', {}


def read_bound_history(repo: Path, native: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """Read the exact native operation receipt, not legacy history guesses."""
    identity = native.get('history_id')
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
        return 'history identity not recorded', {}
    try:
        from .artifacts import operation_history_entries_dir
        directory = operation_history_entries_dir(repo)
        path = directory / (identity + '.json')
        if directory.is_symlink() or path.is_symlink():
            return 'unsafe runtime history path', {}
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 16*1024*1024:
                return 'runtime history is not a bounded regular file', {}
            raw = handle.read(16*1024*1024+1)
        if len(raw)>16*1024*1024:
            return 'runtime history exceeds inspection limit', {}
        item = json.loads(raw)
        if not isinstance(item, Mapping):
            return 'runtime history malformed', {}
        required = {'history_id': identity, 'operation_id':native.get('operation_id'),
                    'sha256':native.get('artifact_sha256'), 'commit_hash':native.get('commit')}
        if any(not isinstance(v,str) or not v or item.get(k)!=v for k,v in required.items()):
            return 'runtime history identity mismatch', {}
        if _mapping(item.get('transaction')).get('id')!=native.get('transaction_id'):
            return 'runtime history transaction mismatch', {}
        return 'exact native runtime history', item
    except (OSError, ValueError, RuntimeError, ImportError):
        return 'runtime history unavailable', {}


def history_pages(record: Mapping[str, Any]) -> dict[int, list[tuple[str, str]]]:
    validation = _mapping(record.get('validation_plan'))
    return {
        1: _fields(record, ('history_id','operation_id','artifact_name','sha256','commit_hash','applied_at')),
        2: _records(record.get('child_outcomes'), ('name','returncode','skipped','failure_kind','overlay_id','target'), 'Native child')
           + _records(validation.get('validation_step_outcomes'), ('name','outcome','returncode','skipped','warnings','failure_kind','step_index','step_total'), 'Native validation'),
        3: _records(record.get('hook_events'), ('kind','phase','status','returncode','skipped','timestamp','at','name'), 'Native hook'),
        4: _fields(record, ('artifact','sha256','validation_session_id','validation_report_path'), 'Native reference · '),
        5: _fields(record, ('status','validation_status','rollback_status','validation_final_verdict'))
           + _fields(_mapping(record.get('transaction')), ('id','status'), 'Native history transaction · '),
    }


def transaction_pages(tx: Mapping[str, Any]) -> dict[int, list[tuple[str, str]]]:
    # Whitelist only existing semantic fields; no raw backup content, env,
    # command argv, credentials, arbitrary stdout/stderr or host process data.
    git = _mapping(tx.get('git_state'))
    isolation = _mapping(tx.get('isolation'))
    pages = {
        1: _fields(tx, ('transaction_id','repo_root','artifact','artifact_type','run_id','started_at','updated_at')),
        2: _records(tx.get('affected_paths'), ('path','action','kind','existed','restore_state','backup_sha256','restored_sha256','verified','status'), 'Path'),
        3: _records(tx.get('events'), ('kind','phase','status','at','timestamp'), 'Event'),
        4: _fields(tx, ('run_id','artifact','path')),
        5: _fields(tx, ('status','commit','commit_mode','rollback_status','rollback_verified','validation_status'))
           + _fields(git, ('initial_head','applied_commit','head_ref','rollback_scope','preserve_dirty_requested','restored','dirty_tree_restored'), 'Git · ')
           + _fields(isolation, ('requested','effective','state','execution_root','fallback_reason'), 'Isolation · ')
           + _fields(_mapping(tx.get('rollback_receipt')), ('state','verified','phase','restored_paths','removed_added_paths','repository_scope','git_scope','hook_scope'), 'Rollback · '),
    }
    return pages


def session_pages(payload: Mapping[str, Any], *, tx_state: str = 'not inspected', tx: Mapping[str, Any] | None = None) -> dict[int, list[tuple[str, str]]]:
    op = _mapping(payload.get('operator_status'))
    target = _mapping(op.get('target'))
    package = _mapping(op.get('package'))
    native = _mapping(payload.get('native_apply'))
    session = _mapping(op.get('session'))
    ci = _mapping(op.get('ci'))
    required = _mapping(ci.get('required'))
    regression = _mapping(ci.get('regression'))
    pages = {
        1: _fields(target, ('repository','repo_root','branch','base','head_repository','base_repository','head_sha','source_binding'))
           + _fields(package, ('path','sha256','verified'), 'Package · '),
        2: _records(native.get('validation_steps'), ('name','outcome','returncode','skipped'), 'Validation')
           + [('Changed path', _public(x)) for x in (native.get('changed_paths') or []) if isinstance(x, str)]
           + _records(required.get('runs'), ('workflow','workflow_id','run_id','attempt','state','conclusion'), 'CI run'),
        3: _records(op.get('events'), ('kind','stage','at','timestamp','state','operation','number','source_sha'), 'Event')
           + _records(session.get('pending_intents'), ('id','operation','state'), 'Pending intent'),
        4: _fields(package, ('path','sha256'), 'Retained artifact · ')
           + _fields(native, ('history_id','operation_id','transaction_id','artifact_sha256'), 'Native reference · ')
           + [('Output', 'Raw logs remain native; this snapshot does not fetch or open log files.')],
        5: [('Transaction record', tx_state)]
           + _fields(native, ('state','transaction_id','transaction_status','validation_status','source_commit','commit','source_tree','result_tree','validation_evidence_source'))
           + _fields(session, ('id','stage','resumable'), 'Session · ')
           + _fields(_mapping(op.get('pr')), ('number','state','base_sha'), 'PR · ')
           + _fields(required, ('state','passed','recorded_qualified','policy_sha256'), 'Required CI · ')
           + _fields(regression, ('state','passed','run_id','attempt','shards_passed','summary_passed'), 'CI24 · ')
           + _fields(ci, ('overall_acceptance','device_qualification','release_qualification'))
           + _records(op.get('blockers'), ('code','reason'), 'Blocker'),
    }
    # Only native public credential receipts, never credentials or inferred auth.
    credential = _mapping(payload.get('credential'))
    if credential:
        pages[5] += credential_rows(credential)
    else:
        pages[5].append(('Credential health', 'Not recorded in this session; no provider probe performed'))
    current = payload.get('credential_health')
    if not isinstance(current, Mapping):
        current = op.get('credential_health')
    if isinstance(current, Mapping):
        pages[5].extend(credential_health_rows(current))
    else:
        pages[5].append(('Current credential authorization', 'UNVERIFIED; no live M4 diagnostic supplied'))
    if tx:
        for page, rows in transaction_pages(tx).items():
            pages[page].extend(rows)
    pages[0] = _fields(op, ('state','next_safe_action')) + _fields(session, ('id','stage','resumable')) + [('Transaction record', tx_state), ('Mode', 'Saved native evidence; no refresh of GitHub or release qualification')]
    return pages


def credential_rows(receipt: Mapping[str, Any]) -> list[tuple[str, str]]:
    rows = _fields(receipt, ('mode','app_id','installation_id','account','repository','repository_id','capability','expires_at'), 'M4 recorded · ')
    permissions = _mapping(receipt.get('granted_permissions'))
    for key in ('contents','pull_requests','actions','checks','metadata','workflows'):
        if permissions.get(key) in ('read','write','none'):
            rows.append(('Recorded permission · '+key, permissions[key]))
    rows.append(('Credential authority', 'Historical receipt only; current expiry, permission drift and authorization unverified'))
    return rows



def credential_health_rows(health: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Project native M4 diagnostics only, never infer approval from token presence."""
    fields = ('auth_mode', 'app_name', 'installation_id', 'repository_coverage',
              'required_capability', 'granted_capability', 'expiry_state',
              'permission_drift', 'fork_access', 'base_access', 'authenticated',
              'authorized_for_operation', 'remote_verified', 'required_ci_qualified')
    rows = _fields(health, fields, 'M4 observed · ')
    for key in ('authenticated', 'authorized_for_operation', 'remote_verified', 'required_ci_qualified'):
        if key not in health:
            rows.append(('M4 ' + key, 'UNVERIFIED; no evidence'))
    rows.append(('Credential authority', 'Display only; use native provider for current approval and access'))
    return rows


def overlay_pages(payload: Mapping[str, Any]) -> dict[int, list[tuple[str, str]]]:
    """Render only existing overlay plan/check/status/recovery facts."""
    manifest = _mapping(payload.get('manifest'))
    stages = _records(payload.get('validation_steps'), ('name','outcome','returncode','skipped'), 'Validation')
    for key in ('inventory','affected_paths','changes','conflicts','active_transactions'):
        stages += _records(payload.get(key), ('path','action','kind','status','classification','transaction_id','detail'), key)
    for key in ('inventory','changes'):
        stages += _records(manifest.get(key), ('path','action','sha256','source_blob_sha','payload_sha256'), key)
    return {
        0: _fields(payload, ('decision','status','ok','read_only','next_step','error')),
        1: _fields(payload, ('artifact','repository','repo_root','source_sha','source_tree','overlay_patch_id','current_patch_id')) + _fields(manifest, ('schema_version','base_commit','base_tree')),
        2: stages,
        3: _records(payload.get('events'), ('kind','phase','status','at'), 'Event'),
        4: [('Native message', _public(v)) for v in (payload.get('messages') or []) if isinstance(v, str)] + [('Output', 'Native output remains in its original command and logs; no implicit file reads.')],
        5: _fields(payload, ('integrity_ok','source_compatible','structural_ok','source_state_ok','git_worktree_clean','rebaseable','rollback_verified')) + _fields(_mapping(payload.get('patch_equivalence')), ('status',)) + _records(payload.get('marker_issues'), ('kind','status','reason'), 'Marker issue'),
    }


def inspect_enabled(args: Any = None) -> bool:
    """Opt-in foreground browser; status/report remain nonblocking by default."""
    return os.environ.get('DEVTOOL_UI_INSPECT') == '1' and boundary_enabled(args=args)


def render_inspection(pages: Mapping[int, list[tuple[str, str]]], section: int, page: int, *, width: int, height: int, title: str = 'M6 · SAVED NATIVE EVIDENCE') -> tuple[BoundedLiveFrame, int]:
    rows = pages.get(section) or [('Native evidence', 'not recorded in this snapshot')]
    # Fold using Rich cell widths: hashes, long paths and combining glyphs
    # remain reachable in a narrow portrait viewport, not silently truncated.
    console = Console(width=max(1, width), color_system=None)
    lines = []
    for key, value in rows:
        text = Text(_public(key) + ': ', style='dim')
        text.append(_public(value), style='default')
        lines.extend(text.wrap(console, max(12, width-4), overflow='fold'))
    count = max(1, height - 10)
    total = max(1, (len(lines) + count - 1) // count)
    index = min(max(0, page), total - 1)
    blocks = [Text(f'{_PAGE_NAMES[section]} · {index + 1}/{total}', style='bold cyan', no_wrap=True, overflow='ellipsis')]
    blocks.extend(lines[index*count:(index+1)*count])
    blocks += [Text('0 overview 1 identity 2 stages 3 events', style='dim', no_wrap=True, overflow='crop'),
               Text('4 output · 5 evidence · q exit', style='dim', no_wrap=True, overflow='crop'),
               Text('[ previous · ] next · l bound logs', style='dim', no_wrap=True, overflow='crop')]
    return BoundedLiveFrame(shared_panel(title, blocks, width=width)), index


def browse_inspection(pages: Mapping[int, list[tuple[str, str]]], *, args: Any = None, title: str = 'M6 · SAVED NATIVE EVIDENCE', repo: Path | None = None, log_refs: Sequence[str] = ()) -> bool:
    """Single-thread PTY input/render loop, no child, remote call or mutation.

    This operates only after a native read-only command completes. The static
    final summary survives in scrollback (no alternate screen or transient UI).
    """
    if not inspect_enabled(args) or os.name != 'posix':
        return False
    try:
        fd = sys.stdin.fileno()
        out = Console()
        section = page = 0
        log_index = 0
        log_page = 0
        log_open = False
        width,height=terminal_geometry(out,fd)
        out.width, out.height = width, height
        frame, page = render_inspection(pages, section, page, width=width, height=height, title=title)
        with PosixInput(fd) as keys, InlineLive(frame,console=out) as live:
            while True:
                key=keys.read(.2)
                size=terminal_geometry(out,fd)
                changed=size!=(width,height)
                if key is not None:
                    if key in ('','q','\x04'):
                        break
                    key={'up':'k','down':'j'}.get(key,key).encode('ascii','ignore')
                    if key in tuple(str(i).encode() for i in range(6)):
                        section, page = int(key), 0
                        log_open = False
                        changed = True
                    elif key == b'l' and log_refs and repo is not None:
                        log_open = not log_open
                        log_page = 0
                        changed = True
                    elif key == b'n' and log_open and log_refs:
                        log_index = (log_index + 1) % len(log_refs)
                        log_page = 0
                        changed = True
                    elif key in (b']', b'j', b'\r', b'\n'):
                        if log_open:
                            log_page += 1
                        else:
                            page += 1
                        changed = True
                    elif key in (b'[', b'k'):
                        if log_open:
                            log_page = max(0, log_page-1)
                        else:
                            page -= 1
                        changed = True
                if changed:
                    width, height = size
                    out.width, out.height = width, height
                    if log_open and log_refs and repo is not None:
                        try:
                            from .m6_receipt_logs import read_native_log_page
                            preview = read_native_log_page(repo, log_refs[log_index], log_page)
                            log_page = preview['page']
                            rows = [Text('Archived log ' + str(log_index+1) + '/' + str(len(log_refs)) + ' · ' + preview['name'], style='bold cyan'),
                                    Text('Read-only · redacted · ' + str(preview['page']+1) + '/' + str(preview['pages']) + ' pages')]
                            rows.extend(Text(line, overflow='fold') for line in preview['lines'][:max(1,height-8)])
                        except (OSError, ValueError, ImportError) as exc:
                            rows = [Text('Native log unavailable · ' + type(exc).__name__, style='yellow'),
                                    Text('No raw contents exposed; use canonical Devtool log commands.')]
                        rows.append(Text('[ prev  ] next · n next log · l return · q exit', style='dim'))
                        frame = BoundedLiveFrame(shared_panel('M6 · ARCHIVED NATIVE LOG', rows, width=width))
                    else:
                        frame, page = render_inspection(pages, section, page, width=width, height=height, title=title)
                    live.update(frame, refresh=True)
        return True
    except KeyboardInterrupt:
        return True
    except Exception as exc:
        print('M6 inspection unavailable: '+type(exc).__name__,file=sys.stderr,flush=True)
        return False


def _browse_session(payload: Mapping[str, Any], repo: Path, *, args: Any = None) -> bool:
    if not inspect_enabled(args):
        return False
    identity = _mapping(payload.get('native_apply')).get('transaction_id')
    state, record = read_bound_transaction(repo, identity) if isinstance(identity, str) else ('not recorded', {})
    pages=session_pages(payload, tx_state=state, tx=record)
    history_state, history=read_bound_history(repo, _mapping(payload.get('native_apply')))
    pages[5].append(('Runtime history', history_state))
    for section, rows in history_pages(history).items():
        pages[section].extend(rows)
    from .m6_receipt_logs import receipt_log_refs
    log_refs = receipt_log_refs(_mapping(payload.get('native_apply')), record, history)
    pages[4].append(('Archived log references', str(len(log_refs)) + (' · press l to inspect' if log_refs else ' · no bound raw logs; nothing fetched')))
    return browse_inspection(pages, args=args, title='M6 · SAVED SESSION', repo=repo, log_refs=log_refs)


def _browse_overlay(payload: Mapping[str, Any], repo: Path, *, args: Any = None) -> bool:
    if not inspect_enabled(args):
        return False
    pages = overlay_pages(payload)
    identity = getattr(args, 'transaction', None)
    record: Mapping[str, Any] = {}
    if identity and isinstance(identity, str):
        state, record = read_bound_transaction(repo, identity)
        pages[5].append(('Transaction record', state))
        for section, rows in transaction_pages(record).items():
            pages[section].extend(rows)
    from .m6_receipt_logs import receipt_log_refs
    log_refs = receipt_log_refs(_mapping(payload), record)
    pages[4].append(('Archived log references', str(len(log_refs)) + (' · press l to inspect' if log_refs else ' · no bound raw logs; nothing fetched')))
    return browse_inspection(pages, args=args, title='M6 · OVERLAY INSPECTION', repo=repo, log_refs=log_refs)


def _browse_transaction(payload: Mapping[str, Any], repo: Path, identity: str, *, args: Any = None) -> bool:
    if not inspect_enabled(args):
        return False
    state, record = read_bound_transaction(repo, identity)
    pages = transaction_pages(record)
    pages[0] = [('Saved record', state)] + _fields(payload, ('classification','eligible','retired','reason','read_only'))
    pages[5].extend(('Native blocker', _public(x)) for x in (payload.get('blockers') or []) if isinstance(x, str))
    from .m6_receipt_logs import receipt_log_refs
    log_refs = receipt_log_refs(record)
    pages[4].append(('Archived log references', str(len(log_refs)) + (' · press l to inspect' if log_refs else ' · no bound raw logs; nothing fetched')))
    return browse_inspection(pages, args=args, title='M6 · NATIVE TRANSACTION', repo=repo, log_refs=log_refs)


def _safe_browse(invoke: Callable[[], bool]) -> bool:
    try:
        return invoke()
    except Exception as exc:
        print('M6 inspection unavailable: '+type(exc).__name__,file=sys.stderr,flush=True)
        return False


def browse_session(payload: Mapping[str, Any], repo: Path, *, args: Any = None) -> bool:
    return _safe_browse(lambda: _browse_session(payload, repo, args=args))


def browse_overlay(payload: Mapping[str, Any], repo: Path, *, args: Any = None) -> bool:
    return _safe_browse(lambda: _browse_overlay(payload, repo, args=args))


def browse_transaction(payload: Mapping[str, Any], repo: Path, identity: str, *, args: Any = None) -> bool:
    return _safe_browse(lambda: _browse_transaction(payload, repo, identity, args=args))
