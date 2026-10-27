"""RM-04: guarded local native-node replay with content-bound durable records.

Never replays remote dispatch, signing, publication, uncertain execution or a
node for which the original terminal result cannot be authenticated locally.
"""
from __future__ import annotations

import copy
import inspect
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable, Mapping, Sequence

from .models import CommandSpec
from .execution_scheduler_truth import execution_history_root, run_projection_directory
from .execution_lineage_v2 import project_execution_lineage, LineageError

_ID = re.compile(r"[A-Za-z0-9_.-]{1,160}\Z")
_LOCK = threading.RLock()
_RISK = ('sign', 'publish', 'deploy', 'release', 'upload', 'push', 'dispatch', 'remote-run',
         'ksud', 'fastboot', 'adb install', 'rm -rf', 'git reset', 'git push')
_MAX = 4 * 1024 * 1024


class WorkflowRetryError(ValueError):
    pass


def _sha(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), default=str).encode()).hexdigest()


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise WorkflowRetryError('Git source identity unavailable or working tree changed')
    return result.stdout.strip()


def _source(root: Path) -> dict[str, str]:
    root = root.resolve()
    commit = _git(root, 'rev-parse', 'HEAD')
    tree = _git(root, 'rev-parse', 'HEAD^{tree}')
    if subprocess.run(['git', '-C', str(root), 'diff', '--quiet', 'HEAD', '--', '.'],
                      timeout=12).returncode:
        raise WorkflowRetryError('tracked source drift: retry must not rewrite or assume dirty input')
    return {'commit': commit, 'tree': tree}


def _host() -> str:
    return _sha({'platform': sys.platform, 'machine': platform.machine(),
                 'python': list(sys.version_info[:3]), 'system': platform.system(),
                 'release': platform.release()})


_TOOLCHAIN_CACHE: dict[tuple[str,int,int,int],str]={}
def _toolchain(specs: Sequence[CommandSpec]) -> str | None:
    entries=[]
    for spec in specs:
        if not spec.argv: return None
        raw=str(spec.argv[0])
        command = shutil.which(raw, path=spec.env.get('PATH') or os.environ.get('PATH'))
        if command is None and Path(raw).is_absolute(): command=raw
        if not command: return None
        binary=Path(command).resolve(strict=False)
        if not binary.is_file() or not os.access(binary,os.X_OK): return None
        stat=binary.stat()
        if stat.st_size>256*1024*1024: return None
        key=(str(binary),stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
        digest=_TOOLCHAIN_CACHE.get(key)
        if digest is None:
            sha=hashlib.sha256()
            with binary.open('rb') as reader:
                for chunk in iter(lambda:reader.read(1024*1024),b''):
                    sha.update(chunk)
            digest=sha.hexdigest()
            _TOOLCHAIN_CACHE[key]=digest
        entries.append({'executable':str(binary),'sha256':digest,
                        'host_policy':spec.metadata.get('host_eligibility'),
                        'toolchain_requirements':spec.metadata.get('toolchain_requirements')})
    return _sha(entries)


def _path_snapshot(root: Path, raws: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Merkle-style hashes of bounded repository-relative files and globs."""
    root = root.resolve()
    result: dict[str, dict[str, Any]] = {}
    for raw in raws:
        name = str(raw)
        path = Path(name)
        if not name or path.is_absolute() or '..' in path.parts or '\\' in name:
            raise WorkflowRetryError('retry requires safe repository-relative paths')
        prefix=[]
        for part in path.parts:
            if any(char in part for char in '*?['):
                break
            prefix.append(part)
        current=root
        for part in prefix:
            current=current/part
            if current.is_symlink():
                raise WorkflowRetryError('symlink in retry input/output: '+name)
        if any(char in name for char in '*?['):
            try: matches=list(root.glob(name))
            except (OSError,ValueError) as exc:
                raise WorkflowRetryError('invalid retry glob') from exc
        else:
            matches=[root/name] if (root/name).exists() else []
        files=[]
        for item in matches:
            if item.is_symlink():
                raise WorkflowRetryError('symlink matched by retry input/output')
            if item.is_dir():
                files.extend(v for v in item.rglob('*') if v.is_file() or v.is_symlink())
            elif item.is_file():
                files.append(item)
        paths=sorted(set(files))
        if len(paths)>4096:
            raise WorkflowRetryError('retry glob file count exceeds bound')
        entries=[]
        for file in paths:
            if file.is_symlink() or not file.resolve().is_relative_to(root):
                raise WorkflowRetryError('unsafe symlink/escaped retry path')
            if any(part.is_symlink() for part in list(file.parents)[:-1] if part.is_relative_to(root)):
                raise WorkflowRetryError('unsafe parent symlink in retry files')
            if file.stat().st_size>256*1024*1024:
                raise WorkflowRetryError('retry input/output exceeds bounded hash capacity')
            h=hashlib.sha256()
            with file.open('rb') as reader:
                for chunk in iter(lambda:reader.read(1024*1024),b''):
                    h.update(chunk)
            entries.append((file.relative_to(root).as_posix(),h.hexdigest()))
        result[name]={'present':bool(entries),'sha256':_sha(entries) if entries else None,
                      'file_count':len(entries)}
    return result


def _groups(specs: Sequence[CommandSpec]) -> dict[str, list[CommandSpec]]:
    groups: dict[str, list[CommandSpec]] = {}
    for spec in specs:
        meta = spec.metadata
        key = str(meta.get('workflow_node_key') or '')
        if not key or not meta.get('workflow_graph') or not _ID.fullmatch(key):
            raise WorkflowRetryError('native workflow node identities required for RM-04')
        groups.setdefault(key, []).append(spec)
    return groups


def _paths(specs: Sequence[CommandSpec], field: str) -> list[str]:
    return list(dict.fromkeys(str(item) for spec in specs
                              for item in (spec.metadata.get(field) or ())))


def _safe(specs: Sequence[CommandSpec]) -> tuple[bool, str]:
    for spec in specs:
        meta = spec.metadata
        command = ' '.join(spec.argv).lower()
        if spec.destructive or any(token in command for token in _RISK):
            return False, 'irreversible or remote-like command'
        if meta.get('remote_dispatch_allowed') is True or meta.get('workflow_device_serial'):
            return False, 'remote or device operation'
        if meta.get('workflow_retry_safe') is True or meta.get('retry_safe') is True:
            continue
        if meta.get('read_only') is True or meta.get('workflow_read_only') is True:
            continue
        return False, 'missing explicit retry_safe/read_only declaration'
    return True, 'declared safe local operation'


def _signature(specs: Sequence[CommandSpec]) -> str:
    return _sha([{'argv': x.argv, 'cwd': x.cwd, 'env': x.env,
                  'config': x.metadata.get('workflow_config_fingerprint'),
                  'execution_environment': x.metadata.get('workflow_execution_environment'),
                  'runner': x.metadata.get('workflow_runner'),
                  'device': x.metadata.get('workflow_device_serial'),
                  'runner_options':x.metadata.get('workflow_runner_options'),
                  'toolchains':x.metadata.get('toolchain_requirements'),
                  'deps': x.metadata.get('workflow_depends_on'),
                  'inputs': x.metadata.get('workflow_inputs'),
                  'outputs': x.metadata.get('workflow_outputs')} for x in specs])


def _record_path(run_id: str, *, writable: bool = False) -> Path:
    if not _ID.fullmatch(run_id):
        raise WorkflowRetryError('invalid workflow run ID')
    live = run_projection_directory(run_id)
    history = execution_history_root() / run_id
    if live.is_symlink() or history.is_symlink():
        raise WorkflowRetryError('unsafe execution projection directory')
    if (live / 'workflow-retry.json').exists():
        return live / 'workflow-retry.json'
    if (history / 'workflow-retry.json').exists():
        return history / 'workflow-retry.json'
    return live / 'workflow-retry.json' if writable else history / 'workflow-retry.json'


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX:
        raise WorkflowRetryError('missing/unsafe native workflow retry receipt')
    try:
        data = json.loads(path.read_text('utf-8'))
    except (ValueError, OSError, UnicodeError) as exc:
        raise WorkflowRetryError('malformed native workflow retry receipt') from exc
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise WorkflowRetryError('unsupported native workflow retry receipt schema')
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    with _LOCK:
        if path.is_symlink() or path.parent.is_symlink():
            raise WorkflowRetryError('unsafe retry receipt destination')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        b = (json.dumps(data, sort_keys=True, indent=2, default=str)+'\n').encode()
        if len(b) > _MAX:
            raise WorkflowRetryError('native retry receipt exceeds bound')
        fd, name = tempfile.mkstemp(prefix='.workflow-retry-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as out:
                os.chmod(name, 0o600)
                out.write(b); out.flush(); os.fsync(out.fileno())
            os.replace(name, path)
            folder_fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(folder_fd)
            finally: os.close(folder_fd)
        finally:
            if os.path.exists(name): os.unlink(name)


def capture_start(root: Path, run_id: str, specs: Sequence[CommandSpec]) -> dict[str, Any]:
    """Durably freeze a normal workflow's spec/input identities before spawn."""
    grouped = _groups(specs)
    identity = _source(root)
    nodes = {}
    for key, parts in grouped.items():
        deps = list(parts[0].metadata.get('workflow_depends_on') or ())
        ins = _paths(parts, 'workflow_inputs')
        outs = _paths(parts, 'workflow_outputs')
        # Unknown/glob paths do not disable the workflow; they disable reuse.
        try: input_state = _path_snapshot(root, ins)
        except WorkflowRetryError: input_state = None
        nodes[key] = {'depends_on': deps, 'signature': _signature(parts),
                      'inputs': input_state, 'outputs': outs, 'output_state': None,
                      'toolchain': _toolchain(parts),
                      'status': 'unknown', 'attempt': 1,
                      'retry_safe': _safe(parts)[0], 'reason': _safe(parts)[1]}
    meta = specs[0].metadata
    data = {'schema_version': 1, 'kind': 'devtool.workflow-retry.source', 'run_id': run_id,
            'source': identity, 'host': _host(),
            'target': str(meta.get('workflow_target') or ''),
            'workflow': str(meta.get('workflow_name') or ''),
            'nodes': nodes, 'terminal': False}
    _write(_record_path(run_id, writable=True), data)
    return data


def capture_finish(root: Path, record: dict[str, Any],
                   results: Sequence[Any], *, terminal: bool = True) -> None:
    by_key: dict[str, list[Any]] = {}
    for result in results:
        key = str(result.metadata.get('workflow_node_key') or '')
        if key in record['nodes']:
            by_key.setdefault(key, []).append(result)
    for key, node in record['nodes'].items():
        observed = by_key.get(key, ())
        if not observed: continue
        statuses = [str(item.metadata.get('workflow_node_state') or '') for item in observed]
        node['status'] = 'passed' if all(item.returncode == 0 and not item.skipped and s not in ('failed','cancelled','blocked','skipped') for item,s in zip(observed,statuses)) else (
            'blocked' if any(s == 'blocked' for s in statuses) else 'failed')
        if node['status'] == 'passed':
            try: node['output_state'] = _path_snapshot(root, node['outputs']) if node['outputs'] else None
            except WorkflowRetryError: node['output_state'] = None
    record['terminal'] = terminal
    _write(_record_path(record['run_id'], writable=True), record)


def plan_retry(root: Path, old_run_id: str, specs: Sequence[CommandSpec], *,
               projection_root: Path | None = None, require_journal: bool = True) -> dict[str, Any]:
    """Read-only plan. Never promotes local evidence to remote authority."""
    record = _read(_record_path(old_run_id))
    if record.get('run_id') != old_run_id or not isinstance(record.get('nodes'), dict):
        raise WorkflowRetryError('retry source receipt identity invalid')
    if record.get('terminal') is not True:
        raise WorkflowRetryError('original workflow has no finalized retry record; status is uncertain')
    source = _source(root)
    if record['source'] != source:
        raise WorkflowRetryError('frozen source differs; retry refused')
    if record['host'] != _host():
        raise WorkflowRetryError('host/toolchain context differs; retry refused')
    groups = _groups(specs)
    if set(groups) != set(record['nodes']):
        raise WorkflowRetryError('native DAG changed; retry refused')
    meta = specs[0].metadata
    if (str(meta.get('workflow_target') or '') != record['target'] or
        str(meta.get('workflow_name') or '') != record['workflow']):
        raise WorkflowRetryError('workflow target/action changed')
    if require_journal:
        try:
            lineage = project_execution_lineage(root, old_run_id, projection_root=projection_root)
        except LineageError as exc:
            raise WorkflowRetryError('EXO lineage unverified: '+str(exc)) from exc
        if not lineage['journal_current']:
            raise WorkflowRetryError('EXO event journal is incomplete; node reuse forbidden')
        live = {node['node_id']: node for node in lineage['nodes']}
        if set(live) != set(groups):
            raise WorkflowRetryError('original EXO graph identity differs')
    else:
        live = {}
    reuse, rerun, blocked = [], [], []
    decisions = {}
    for key, parts in groups.items():
        old = record['nodes'][key]
        if old['signature'] != _signature(parts) or old['depends_on'] != list(parts[0].metadata.get('workflow_depends_on') or ()):
            raise WorkflowRetryError('workflow node fingerprint/dependencies changed: '+key)
        safe, reason = _safe(parts)
        if not old.get('toolchain') or old['toolchain'] != _toolchain(parts):
            raise WorkflowRetryError('toolchain executable content/host policy changed: '+key)
        if require_journal:
            node = live[key]
            if node['lifecycle_evidence'] != 'OBSERVED_TERMINAL':
                raise WorkflowRetryError('uncertain node terminal; manual recovery required: '+key)
            if node['attempt'] != int(old['attempt']):
                raise WorkflowRetryError('node attempt identity differs: '+key)
            expected = {'passed':'succeeded','failed':'failed','blocked':'blocked','skipped':'skipped','cancelled':'cancelled'}.get(old['status'])
            if node['status'] != expected:
                raise WorkflowRetryError('EXO and retry receipt disagree: '+key)
        try:
            input_state = _path_snapshot(root, _paths(parts,'workflow_inputs'))
            inputs_match = bool(old['inputs']) and input_state == old['inputs']
        except WorkflowRetryError:
            inputs_match = False
        output_match = False
        if old['status'] == 'passed' and old['output_state'] and old['outputs']:
            try:
                observed = _path_snapshot(root, old['outputs'])
                output_match = (observed == old['output_state'] and
                                all(v['present'] for v in observed.values()))
            except WorkflowRetryError: pass
        if old['status'] == 'passed' and inputs_match and output_match:
            decision, cause = 'reuse','input and output digests match original'
            reuse.append(key)
        elif safe and inputs_match:
            decision, cause = 'rerun', ('original '+old['status']+' or output proof invalid')
            rerun.append(key)
        else:
            decision, cause = 'blocked', (reason if not safe else 'input fingerprint absent/drifted')
            blocked.append(key)
        decisions[key] = {'disposition':decision,'reason':cause,
                          'original_status':old['status'],'original_attempt':old['attempt']}
    # Any parent rerun invalidates downstream reuse, regardless of old hashes.
    changed=True
    while changed:
        changed=False
        for key, old in record['nodes'].items():
            if key in reuse and any(parent in rerun or parent in blocked for parent in old['depends_on']):
                reuse.remove(key)
                if key in blocked or not _safe(groups[key])[0]:
                    if key not in blocked: blocked.append(key)
                    decision='blocked'
                else:
                    rerun.append(key); decision='rerun'
                decisions[key]={'disposition':decision, 'reason':'upstream invalidation',
                                'original_status':old['status'],'original_attempt':old['attempt']}
                changed=True
    if blocked:
        status = 'BLOCKED'
    else:
        status = 'READY'
    return {'schema_version':1,'kind':'devtool.workflow-retry.plan','read_only':True,
            'original_run_id':old_run_id,'source':source,'host':record['host'],
            'target':record['target'],'workflow':record['workflow'],
            'disposition':status,'reuse':reuse,'rerun':rerun,'blocked':blocked,
            'nodes':decisions, 'plan_digest':_sha([old_run_id,source,record['host'],decisions]),
            'remote_dispatch_allowed':False, 'original_record':str(_record_path(old_run_id))}


def execute_retry(root: Path, plan: Mapping[str, Any], specs: Sequence[CommandSpec],
                  *, approval: bool, original_run_id: str, resume: bool = False,
                  on_execute: Callable[..., Sequence[Any]]) -> dict[str, Any]:
    """Replay guarded nodes with durable per-node start/finish checkpoints.

    A resumed *successful* node is omitted only after its retained outputs
    still match bytes observed at completion. Any unknown in-flight node is
    reexecuted only with explicit approval and a retry-safe job declaration.
    """
    if not approval or plan['disposition'] != 'READY' or plan['original_run_id'] != original_run_id:
        raise WorkflowRetryError('explicit approval and an unblocked plan required')
    fresh = plan_retry(root,original_run_id,specs)
    if fresh['plan_digest'] != plan['plan_digest']:
        raise WorkflowRetryError('plan changed before execution; retry refused')
    path = _record_path(original_run_id).parent / 'workflow-retry-attempt.json'
    if path.exists():
        state = _read(path)
        if state.get('plan_digest') != plan['plan_digest'] or state.get('original_run_id') != original_run_id:
            raise WorkflowRetryError('retry attempt provenance mismatch')
        if state.get('state') == 'COMPLETED':
            raise WorkflowRetryError('retry attempt already completed; duplicate execution refused')
        if not resume or state.get('state') not in {'RUNNING','PREPARED'}:
            raise WorkflowRetryError('unfinished attempt requires explicit --resume and renewed --yes')
    else:
        if resume:
            raise WorkflowRetryError('no interrupted attempt exists to resume')
        state = {'schema_version':1,'kind':'devtool.workflow-retry.attempt',
                 'original_run_id':original_run_id,'plan_digest':plan['plan_digest'],
                 'state':'PREPARED','decisions':plan['nodes'], 'nodes':{},
                 'execution_verified':False}
        _write(path,state)
    selected = set(plan['rerun'])
    grouped = _groups(specs)
    for key in tuple(selected):
        existing=state['nodes'].get(key) if isinstance(state.get('nodes'),dict) else None
        if not isinstance(existing,dict) or existing.get('state') != 'PASSED':
            continue
        saved=existing.get('verified_outputs')
        if not saved or not isinstance(saved,dict) or not all(x.get('present') for x in saved.values()):
            raise WorkflowRetryError('completed retry node has no verifiable outputs: '+key)
        try: actual=_path_snapshot(root,_paths(grouped[key],'workflow_outputs'))
        except WorkflowRetryError as exc:
            raise WorkflowRetryError('completed retry output unsafe: '+key) from exc
        if actual != saved:
            raise WorkflowRetryError('completed retry output changed: '+key)
        selected.remove(key)
    cloned=[]
    for spec in specs:
        key=spec.metadata['workflow_node_key']
        if key not in selected: continue
        s=copy.deepcopy(spec)
        s.metadata['workflow_depends_on']=[d for d in s.metadata.get('workflow_depends_on',()) if d in selected]
        s.metadata['workflow_retry_parent_run_id']=original_run_id
        attempt=int(plan['nodes'][key]['original_attempt'])+1
        s.metadata['workflow_attempt']=attempt
        s.metadata['workflow_canonical_identity']=_sha([
            plan['source']['commit'],str(s.metadata.get('workflow_target') or ''),
            str(s.metadata.get('workflow_name') or ''),key,attempt])
        cloned.append(s)
    state['state']='RUNNING'
    for key in selected:
        state['nodes'][key]={'state':'UNKNOWN_IF_INTERRUPTED'}
    _write(path,state)

    def observer(key: str, phase: str, results: Sequence[Any]) -> None:
        if key not in selected or phase not in {'started','terminal'}:
            raise WorkflowRetryError('unexpected workflow retry checkpoint')
        if phase=='started':
            state['nodes'][key]={'state':'RUNNING'}
        else:
            success=bool(results) and all(r.returncode==0 and not r.skipped and
                      str(r.metadata.get('workflow_node_state') or '') not in
                      ('blocked','failed','cancelled','skipped') for r in results)
            try:
                outputs=_path_snapshot(root,_paths(grouped[key],'workflow_outputs')) if success else None
            except WorkflowRetryError: outputs=None
            if success and _paths(grouped[key],'workflow_outputs'):
                success=bool(outputs) and all(v.get('present') for v in outputs.values())
            state['nodes'][key]={'state':'PASSED' if success else 'FAILED',
                                  'verified_outputs':outputs,
                                  'returncodes':[r.returncode for r in results]}
        _write(path,state)
    try:
        if cloned:
            if len(inspect.signature(on_execute).parameters)>=2:
                results=list(on_execute(cloned,observer))
            else:
                results=list(on_execute(cloned))
        else:
            results=[]
        actual={key:[] for key in selected}
        for result in results:
            key=str(result.metadata.get('workflow_node_key') or '')
            if key in actual: actual[key].append(result)
        for key in selected:
            rows=actual[key]
            observer(key,'terminal',rows)
        state['state']='COMPLETED'
        state['execution_verified']=all(state['nodes'].get(k,{}).get('state')=='PASSED'
                                        for k in plan['rerun'])
        _write(path,state)
        return state
    except BaseException:
        # Persisted RUNNING and checkpoints survive crash; never silently
        # execute another attempt without --resume + explicit --yes.
        raise
