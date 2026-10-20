"""ACTIONS-06: restart-safe foreground coordination over Devtool's native invoke DAG
and previously implemented authenticated GitHub Actions/verified archive contracts.

A graph node is not a second build scheduler: local-invoke delegates to the existing
Devtool execution engine; GitHub operations delegate to ACTIONS-02..04. Journal
states distinguish accepted remote requests from observed successful completion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from typing import Any

from .actions_artifacts import artifact_list, download_artifact, verify_download
from .actions_dispatch import _durable_record, dispatch_workflow, GitHubDispatchPreflightError
from .actions_runs import _normalize_run, run_id
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug

_IDENT = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
_REL = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_HEX = re.compile(r"[0-9a-f]{32}\Z")
_KINDS = {'local-git', 'local-invoke', 'github-dispatch', 'github-result', 'github-archive'}
_STATES = {'pending', 'running', 'waiting', 'success', 'failure', 'blocked', 'uncertain', 'cancelled'}
_MAX_NODES = 32
_MAX_REMOTE_WORKERS = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                              text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('Cannot establish pipeline local Git source identity') from None
    if proc.returncode:
        raise GitHubActionsError('Cannot establish pipeline local Git source identity')
    return proc.stdout.strip()


def _source(root: Path) -> str:
    # The Devtool frontend initializes its own run catalog BEFORE this handler.
    # Exclude only private Devtool-owned runtime material, not user manifests.
    if _git(root, 'status', '--porcelain', '--untracked-files=normal', '--', '.',
            ':(exclude).devtool/private', ':(exclude).devtool/artifacts'):
        raise GitHubActionsError('Pipeline requires clean local Git worktree')
    sha = _git(root, 'rev-parse', 'HEAD').lower()
    if not _SHA.fullmatch(sha):
        raise GitHubActionsError('Pipeline local Git HEAD is invalid')
    return sha


def _safe_file(root: Path, name: str) -> Path:
    if not _REL.fullmatch(name) or any(p in ('.', '..') for p in name.split('/')):
        raise GitHubActionsError('Pipeline file must be a repository-relative JSON path')
    path = root.resolve() / name
    current = root.resolve()
    for part in name.split('/'):
        current = current / part
        if current.is_symlink():
            raise GitHubActionsError('Pipeline file cannot traverse symlinks')
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise GitHubActionsError('Pipeline JSON file missing or larger than 64 KiB')
    return path


def _manifest(root: Path, name: str) -> tuple[dict[str, Any], str]:
    content = _safe_file(root, name).read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        obj = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        raise GitHubActionsError('Pipeline JSON is invalid') from None
    if not isinstance(obj, dict) or set(obj) != {'schema', 'name', 'nodes'} or type(obj['schema']) is not int or obj['schema'] != 1:
        raise GitHubActionsError('Pipeline must have schema=1, name and nodes')
    if not isinstance(obj['name'], str) or not _IDENT.fullmatch(obj['name']):
        raise GitHubActionsError('Invalid pipeline name')
    nodes = obj['nodes']
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= _MAX_NODES:
        raise GitHubActionsError('Pipeline must contain 1..32 nodes')
    seen = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise GitHubActionsError('Pipeline node must be an object')
        kind, ident, needs = node.get('kind'), node.get('id'), node.get('needs', [])
        if (not isinstance(ident, str) or not _IDENT.fullmatch(ident) or ident in seen
            or not isinstance(kind, str) or kind not in _KINDS or not isinstance(needs, list)
            or any(not isinstance(x, str) or x not in seen for x in needs)
            or len(set(needs)) != len(needs)):
            raise GitHubActionsError('Pipeline node identity/dependency invalid; nodes must be topologically ordered')
        allowed = {'id', 'kind', 'needs'}
        if kind == 'local-invoke':
            allowed |= {'action', 'target'}
            if (not isinstance(node.get('action'), str) or not _IDENT.fullmatch(node['action'])
                or ('target' in node and (not isinstance(node['target'], str) or not _IDENT.fullmatch(node['target'])))):
                raise GitHubActionsError('Local node must reference a safe declared Devtool workflow action/target')
        elif kind == 'github-dispatch':
            allowed |= {'workflow', 'ref', 'inputs'}
            from .actions_dispatch import _ref, _input_values
            from .github_actions import _WORKFLOW
            if not isinstance(node.get('workflow'), str) or not _WORKFLOW.fullmatch(node['workflow']):
                raise GitHubActionsError('Invalid GitHub workflow filename')
            if not isinstance(node.get('ref'), str):
                raise GitHubActionsError('Missing GitHub ref')
            _ref(node['ref'])
            inputs = node.get('inputs', {})
            if (not isinstance(inputs, dict) or len(inputs) > 25 or any(
                not isinstance(k, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,99}', k)
                or not isinstance(v, str) or len(v) > 1024 for k, v in inputs.items()
            )):
                raise GitHubActionsError('GitHub workflow inputs must be bounded string scalars')
            if any(re.search(r'(secret|token|password|credential|private[_-]?key)', k, re.I) for k in inputs):
                raise GitHubActionsError('Secret-like workflow inputs cannot be stored in pipeline source')
        elif kind in ('github-result', 'github-archive'):
            allowed |= {'from'}
            if (not isinstance(node.get('from'), str) or node['from'] not in seen
                or next(x for x in nodes if isinstance(x, dict) and x.get('id') == node['from']).get('kind') != 'github-dispatch'
                or node['from'] not in needs):
                raise GitHubActionsError('GitHub result/archive must directly depend on a prior dispatch node')
            if kind == 'github-archive':
                allowed |= {'artifact_name'}
                if not isinstance(node.get('artifact_name'), str) or not _IDENT.fullmatch(node['artifact_name']):
                    raise GitHubActionsError('Artifact node requires an exact safe artifact_name')
                if not any(x.get('kind') == 'github-result' and x.get('from') == node['from']
                           and x.get('id') in needs for x in nodes if isinstance(x, dict)):
                    raise GitHubActionsError('Archive acquisition must also depend on successful remote-result node')
        if set(node) - allowed:
            raise GitHubActionsError('Unknown pipeline node fields')
        seen.add(ident)
    return obj, digest


def _journal_root() -> Path:
    return Path(os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local/state')) / 'devtool/actions/pipelines'


def _journal_path(identity: str, *, state_root: Path | None = None) -> Path:
    if not _HEX.fullmatch(identity):
        raise GitHubActionsError('Pipeline ID must be a 32-character hex identifier')
    return (state_root if state_root is not None else _journal_root()) / (identity + '.json')


def _read(identity: str, *, state_root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    path = _journal_path(identity, state_root=state_root)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise GitHubActionsError('Pipeline journal missing or invalid')
    try:
        obj = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError, UnicodeError):
        raise GitHubActionsError('Pipeline journal is unreadable') from None
    if (not isinstance(obj, dict) or obj.get('schema') != 1 or obj.get('id') != identity
        or not isinstance(obj.get('nodes'), dict) or not _SHA.fullmatch(str(obj.get('head_sha', '')))):
        raise GitHubActionsError('Pipeline journal has invalid identity/contract')
    if (not isinstance(obj.get('node_order'), list) or
        len(obj['node_order']) != len(obj['nodes']) or
        any(not isinstance(x, str) for x in obj['node_order']) or
        set(obj['node_order']) != set(obj['nodes'])
        or any(not isinstance(x, dict) or x.get('state') not in _STATES for x in obj['nodes'].values())):
        raise GitHubActionsError('Pipeline journal has invalid node states')
    return path, obj


def _public(obj: dict[str, Any]) -> dict[str, Any]:
    nodes = [{'id': k, **{x: v for x, v in entry.items() if x in ('state', 'kind', 'detail', 'run_id', 'request_id', 'artifact_id', 'workflow_id')}}
             for k in obj['node_order'] for entry in (obj['nodes'][k],)]
    done = all(x['state'] == 'success' for x in nodes)
    return {'phase':'ACTIONS-06','pipeline_id': obj['id'],'name': obj['name'],
            'repository':obj['repository'],'source_sha':obj['head_sha'],
            'manifest_sha256':obj['manifest_sha256'],'nodes':nodes,
            'completed_successfully':done,'qualified_release':False,
            'cancel_requested':bool(obj.get('cancel_requested', False)),
            'state': 'success' if done else ('cancelled' if obj.get('cancel_requested') else
                     'blocked' if any(x['state'] in ('failure','blocked','uncertain','cancelled') for x in nodes) else 'incomplete')}


def pipeline_plan(client: GitHubActionsClient, slug: str, root: Path, filename: str) -> dict[str, Any]:
    manifest, digest = _manifest(root, filename)
    sha = _source(root)
    # Even the dry-run validates workflow source / inputs with the canonical ACTIONS-02 planner.
    remote = {}
    for node in manifest['nodes']:
        if node['kind'] == 'github-dispatch':
            result = dispatch_workflow(client, slug, node['workflow'], node['ref'], node.get('inputs', {}), execute=False)
            if result['pre_dispatch_sha'].lower() != sha:
                raise GitHubActionsError('Remote GitHub ref and local committed HEAD differ')
            remote[node['id']] = {'workflow_id':result['workflow_id'],'workflow_path':result['workflow_path'],
                                  'pre_dispatch_sha':result['pre_dispatch_sha']}
    return {'phase':'ACTIONS-06','validated':True,'dispatched':False,'name':manifest['name'],
            'repository':slug,'source_sha':sha,'file':filename,'manifest_sha256':digest,
            'graph':[{'id':x['id'],'kind':x['kind'],'needs':x.get('needs',[])} for x in manifest['nodes']],
            'remote_preflight':remote,'execution':'foreground-bounded-parallel-remote-delegating-native-DAG',
            'max_remote_workers':_MAX_REMOTE_WORKERS,
            'qualified_release':False}


def _local_invoke(root: Path, node: dict[str, Any]) -> str:
    """Launch one native workflow with a one-use immediate-parent RUN capability.

    Never infer the child from timestamps, ambient RUN_ID or latest history.
    A missing/unfinalized canonical catalog entry blocks lineage correlation.
    """
    from .invocation_origin import create_managed_run_handoff
    from .core_runtime import private_run_root
    from .run_store import RunCatalog

    child_run_id = uuid.uuid4().hex
    cmd = [sys.executable, '-m', 'devtool', '-r', str(root), '--no-hud', '--no-clipboard',
           'invoke', node['action']]
    if node.get('target'):
        cmd += ['--target', node['target']]
    env = os.environ.copy()
    # Only a fresh immediate-parent capability is valid. Inherited environment
    # values are deliberately not authority; subprocess does not use shell=True.
    env['DEVTOOL_MANAGED_RUN_HANDOFF'] = create_managed_run_handoff(child_run_id)
    handoff = Path(env['DEVTOOL_MANAGED_RUN_HANDOFF'])
    try:
        try:
            proc = subprocess.run(cmd, cwd=root, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, timeout=900, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise GitHubActionsError('Native Devtool invoke failed or exceeded 900-second bound') from None
    finally:
        # Child usually consumes the capability. If it could not start, remove
        # the orphaned secret file; never reuse the same preallocated identity.
        try:
            handoff.unlink(missing_ok=True)
            handoff.parent.rmdir()
        except OSError:
            pass
    if proc.returncode:
        raise GitHubActionsError(f'Native Devtool invoke exited with code {proc.returncode}; inspect Devtool run history')
    try:
        catalog = RunCatalog.open(private_run_root(root), create=False)
        entry = catalog.runs.get(child_run_id)
    except (OSError, ValueError, TypeError):
        entry = None
    if (not isinstance(entry, dict) or entry.get('run_id') != child_run_id
            or entry.get('command_id') != 'invoke' or entry.get('status') != 'succeeded'
            or type(entry.get('exit_code')) is not int or entry['exit_code'] != 0
            or not isinstance(entry.get('finished_at'), str) or not entry['finished_at']):
        raise GitHubActionsError('Native invoke returned success without finalized exact child RUN-V3 identity')
    return child_run_id


def _execute_node(client: GitHubActionsClient, slug: str, root: Path, node: dict[str, Any],
                  record: dict[str, Any], journal: dict[str, Any], head: str) -> str:
    kind = node['kind']
    if kind == 'local-git':
        if _source(root) != head:
            raise GitHubActionsError('Local Git source changed since pipeline creation')
        record['detail'] = 'clean Git HEAD verified'
        return 'success'
    if kind == 'local-invoke':
        child_id = _local_invoke(root, node)
        if _source(root) != head:
            raise GitHubActionsError('Local workflow altered committed source or worktree; remote dispatch blocked')
        record['exo_run_id'] = child_id
        record['detail'] = 'native Devtool workflow completed with verified RUN-V3 child'
        return 'success'
    dispatch = journal['nodes'][node.get('from', '')] if kind in ('github-result','github-archive') else record
    if kind == 'github-dispatch':
        if _source(root) != head:
            raise GitHubActionsError('Local source changed before remote dispatch')
        result = dispatch_workflow(client, slug, node['workflow'], node['ref'], node.get('inputs', {}),
                                   execute=True, expected_source_sha=head)
        # Never dispatch twice if the journal update is lost after an accepted POST.
        if result.get('pre_dispatch_sha', '').lower() != head:
            raise GitHubActionsError('GitHub ref changed during dispatch; outcome needs manual reconciliation')
        record.update(request_id=result.get('request_id'),run_id=result.get('run_id'),workflow_id=result.get('workflow_id'),
                      detail=result['state'])
        return 'success' if result.get('run_id') else 'uncertain'
    rid = run_id(dispatch.get('run_id'))
    if kind == 'github-result':
        remote = _normalize_run(client.get_run(slug, rid), slug, rid)
        if (remote.get('head_sha', '').lower() != head or
            remote.get('workflow_id') != dispatch.get('workflow_id') or
            remote.get('event') != 'workflow_dispatch'):
            raise GitHubActionsError('Remote run source/workflow/dispatch identity mismatch')
        if not remote['completed']:
            record['detail'] = 'GitHub execution not completed; resume later'
            return 'waiting'
        if remote['conclusion'] != 'success':
            raise GitHubActionsError('GitHub run completed with conclusion '+str(remote['conclusion']))
        record['detail'] = 'GitHub run succeeded for frozen source'
        return 'success'
    if kind == 'github-archive':
        listed = artifact_list(client, slug, rid)['artifacts']
        matches = [x for x in listed if x.get('name') == node['artifact_name']]
        if len(matches) != 1:
            raise GitHubActionsError('Expected exactly one matching, source-bound GitHub archive')
        identity = matches[0]['id']
        try:
            verified = verify_download(client, slug, rid, identity, root)
        except GitHubActionsError as exc:
            # Missing local archive may be downloaded. Integrity failures must
            # never be retried as if no archive existed.
            if 'no registered download' not in str(exc).lower():
                raise
            download_artifact(client, slug, rid, identity, root)
            verified = verify_download(client, slug, rid, identity, root)
        if not verified.get('archive_verified'):
            raise GitHubActionsError('GitHub archive SHA-256 verification not confirmed')
        record.update(artifact_id=identity,detail='SHA-256 verified ZIP registered; unqualified')
        return 'success'
    raise GitHubActionsError('Unsupported pipeline node')


def _cancel_path(path: Path) -> Path:
    return path.with_suffix('.cancel')


def _cancel_requested(path: Path) -> bool:
    marker = _cancel_path(path)
    if marker.is_symlink():
        raise GitHubActionsError('Pipeline cancellation marker is a symlink')
    return marker.is_file()


def _execute_isolated(client: GitHubActionsClient, slug: str, root: Path,
                      node: dict[str, Any], record: dict[str, Any],
                      journal: dict[str, Any], head: str) -> tuple[str, dict[str, Any]]:
    # Worker never writes the shared journal. Main thread owns every durable transition.
    private = dict(record)
    try:
        return _execute_node(client, slug, root, node, private, journal, head), private
    except GitHubDispatchPreflightError as exc:
        # This is a *definitive* pre-POST refusal, not an ambiguous POST outcome.
        private['detail'] = str(exc)
        return 'failure', private
    except GitHubActionsError as exc:
        private['detail'] = str(exc)
        return ('uncertain' if node['kind'] == 'github-dispatch' else 'failure'), private
    except Exception:
        # Do not treat an unknown dispatch error as permission to repeat a POST.
        private['detail'] = 'Unexpected node execution error; review retained evidence'
        return ('uncertain' if node['kind'] in ('github-dispatch', 'local-invoke') else 'failure'), private


def _recover_interrupted(path: Path, journal: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Only read-only observers can be safely reclassified after interruption."""
    changed = False
    for node in manifest['nodes']:
        record = journal['nodes'][node['id']]
        if record['state'] != 'running':
            continue
        if node['kind'] in ('github-result', 'local-git'):
            record['state'] = 'waiting' if node['kind'] == 'github-result' else 'pending'
            record['detail'] = 'Interrupted read-only observation; safe to inspect again'
        else:
            record['state'] = 'uncertain'
            record['detail'] = 'Interrupted side effect; explicit reconciliation required (not replayed)'
        record['updated_at'] = _now()
        changed = True
    if changed:
        _durable_record(path, journal)


def _drive(client: GitHubActionsClient, slug: str, root: Path, filename: str,
           manifest: dict[str, Any], path: Path, journal: dict[str, Any]) -> dict[str, Any]:
    head = journal['head_sha']
    by_id = {node['id']:node for node in manifest['nodes']}
    # A waiting node is observed once per invocation, not polled to exhaustion.
    visited: set[str] = set()
    while True:
        if _cancel_requested(path):
            journal['cancel_requested'] = True
            for node in manifest['nodes']:
                rec = journal['nodes'][node['id']]
                if rec['state'] in ('pending', 'waiting'):
                    rec.update(state='cancelled', detail='Pipeline cancellation requested; not scheduled', updated_at=_now())
            _durable_record(path, journal)
            return _public(journal)
        ready = [n for n in manifest['nodes']
                 if n['id'] not in visited
                 and journal['nodes'][n['id']]['state'] in ('pending','waiting')
                 and all(journal['nodes'][dep]['state']=='success' for dep in n.get('needs', []))]
        if not ready:
            break
        # Bounded concurrency only for independent remote operations. Native local
        # workflow mutations and archive registry writes remain serialized.
        remote = [n for n in ready if n['kind'] in ('github-dispatch','github-result')]
        batch = remote[:_MAX_REMOTE_WORKERS] if remote else [ready[0]]
        # Durably mark ALL selected nodes running before any remote HTTP POST.
        for node in batch:
            rec = journal['nodes'][node['id']]
            rec.update(state='running', updated_at=_now())
            visited.add(node['id'])
        _durable_record(path, journal)
        outcomes: dict[str, tuple[str, dict[str, Any]]] = {}
        if len(batch) == 1:
            n = batch[0]
            outcomes[n['id']] = _execute_isolated(client, slug, root, n, journal['nodes'][n['id']], journal, head)
        else:
            with ThreadPoolExecutor(max_workers=min(len(batch), _MAX_REMOTE_WORKERS),
                                    thread_name_prefix='devtool-actions-g2') as pool:
                futures = {pool.submit(_execute_isolated, client, slug, root, n,
                                       journal['nodes'][n['id']], journal, head):n['id'] for n in batch}
                for future in as_completed(futures):
                    outcomes[futures[future]] = future.result()
        # Persist individual outcomes; a crash preserves any earlier run receipt.
        for node in batch:
            status, delta = outcomes[node['id']]
            rec = journal['nodes'][node['id']]
            rec.update(delta)
            rec.update(state=status, updated_at=_now())
            _durable_record(path, journal)
    return _public(journal)


def _lock(path: Path) -> Path:
    lock = path.with_suffix('.lock')
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise GitHubActionsError('Pipeline journal is locked or an interrupted invocation needs review') from None
    return lock


def pipeline_run(client: GitHubActionsClient, slug: str, root: Path, filename: str,
                 *, state_root: Path | None = None) -> dict[str, Any]:
    plan = pipeline_plan(client, slug, root, filename)
    manifest, digest = _manifest(root, filename)
    if digest != plan['manifest_sha256']:
        raise GitHubActionsError('Pipeline manifest changed during planning')
    identity = uuid.uuid4().hex
    path = _journal_path(identity, state_root=state_root)
    journal = {'schema':1,'id':identity,'name':manifest['name'],'repository':_slug(slug),
               'repo_root':str(root.resolve()),'head_sha':plan['source_sha'],
               'manifest_file':filename,'manifest_sha256':digest,'created_at':_now(),
               'node_order':[x['id'] for x in manifest['nodes']],
               'nodes':{x['id']:{'kind':x['kind'],'state':'pending','detail':''} for x in manifest['nodes']}}
    # Create a durable journal BEFORE any mutable operation.
    _durable_record(path, journal)
    lock = _lock(path)
    try:
        return _drive(client, slug, root, filename, manifest, path, journal)
    finally:
        lock.rmdir()


def pipeline_status(identity: str, *, state_root: Path | None = None) -> dict[str, Any]:
    path, journal = _read(identity, state_root=state_root)
    if _cancel_requested(path):
        journal['cancel_requested'] = True
    return _public(journal)


def pipeline_resume(client: GitHubActionsClient, slug: str, root: Path, identity: str,
                    *, state_root: Path | None = None) -> dict[str, Any]:
    path, journal = _read(identity, state_root=state_root)
    if _slug(slug).lower() != journal['repository'].lower() or str(root.resolve()) != journal['repo_root']:
        raise GitHubActionsError('Pipeline journal repository/worktree binding mismatch')
    manifest, digest = _manifest(root, journal['manifest_file'])
    if digest != journal['manifest_sha256'] or _source(root) != journal['head_sha']:
        raise GitHubActionsError('Pipeline source changed; cannot resume frozen run')
    if journal.get('node_order') != [x['id'] for x in manifest['nodes']] or set(journal['nodes']) != {x['id'] for x in manifest['nodes']}:
        raise GitHubActionsError('Pipeline journal graph has changed')
    lock = _lock(path)
    try:
        _recover_interrupted(path, journal, manifest)
        return _drive(client, slug, root, journal['manifest_file'], manifest, path, journal)
    finally:
        lock.rmdir()


def pipeline_cancel(identity: str, *, state_root: Path | None = None) -> dict[str, Any]:
    """Request pipeline-level stop, without cancelling an unrelated GitHub run.

    An active owner finishes the current bounded operation and then stops
    scheduling. No remote cancel API is called and no local process is killed.
    """
    path, journal = _read(identity, state_root=state_root)
    marker = _cancel_path(path)
    if all(item['state'] == 'success' for item in journal['nodes'].values()):
        out = _public(journal)
        out['cancel_note'] = 'Pipeline already succeeded; no cancellation performed'
        return out
    if marker.is_symlink():
        raise GitHubActionsError('Pipeline cancellation marker is a symlink')
    if not marker.exists():
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(identity + '\n')
                stream.flush()
                os.fsync(stream.fileno())
    journal['cancel_requested'] = True
    out = _public(journal)
    out['cancel_note'] = 'Stop scheduling requested; existing remote GitHub jobs were not cancelled'
    return out
