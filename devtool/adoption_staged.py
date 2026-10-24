"""RM17/RM18: prepared Git-object adoption and lease-guarded multi-repo publication.

No target branch or checkout changes in prepare. Git cannot atomically update refs
across separate repositories; publish therefore writes durable, truthful partial receipts.
Legacy adopt-batch apply stays available; this module is the staged replacement.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from . import adoption_batch as ab
from . import guarded_git as gg

SCHEMA = 'devtool.multi-repo-adoption-staged.v2'
ZERO = '0' * 40


def _git(repo: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None) -> str:
    cmd = ['git', '-C', str(repo), *args]
    p = subprocess.run(cmd, input=input, capture_output=True, env=env, timeout=50)
    if p.returncode:
        raise ab.AdoptionBlocked(f'{repo.name}: git {args[0]} refused: {p.stderr.decode("utf-8", "replace")[-320:]}')
    return p.stdout.decode('utf-8', 'replace').strip()


def _identity(repo: Path, row: dict[str, Any]) -> dict[str, str]:
    identity = ab._source(repo, row)
    if identity['branch'] != row['branch'] or identity['remote'] != row['remote_url']:
        raise ab.AdoptionBlocked(f'{row["id"]}: branch or remote identity changed')
    if _git(repo, 'symbolic-ref', 'HEAD') != 'refs/heads/' + row['branch']:
        raise ab.AdoptionBlocked('HEAD is not the reviewed branch')
    return identity


def _safe_checkout(repo: Path, record: dict[str, Any]) -> None:
    """Before syncing after CAS, reject all unrelated or divergent working copies."""
    allowed = set(record['hashes'])
    changed = ab._changed_paths(repo)
    if changed - allowed or set(_git(repo, 'diff', '--cached', '--name-only').splitlines()) - allowed:
        raise ab.AdoptionBlocked('checkout advanced or has unrelated/staged changes; do not overwrite')
    for index, (rel, digest) in enumerate(sorted(record['hashes'].items())):
        path = ab._path(repo, rel)
        cur = path.read_bytes() if path.is_file() else None
        curhash = ab._sha(cur) if cur is not None else None
        # The worktree is allowed to be at its original preimage or its candidate;
        # never rewrite a third-party edit even to a touched path.
        if curhash not in (record['preimages'][rel], digest):
            escrow = Path(record.get('escrow','')) / 'publish' / f'{index:04d}.original'
            # Crash between moving the reviewed preimage into escrow and
            # exclusive creation of the candidate. Resumption is permissible
            # only when the immutable old bytes still exist and target is absent.
            if not (cur is None and record['preimages'][rel] is not None and
                    escrow.is_file() and not escrow.is_symlink() and
                    ab._sha(escrow.read_bytes()) == record['preimages'][rel]):
                raise ab.AdoptionBlocked(f'unexpected touched-file change during publish: {rel}')


def _materialize(repo: Path, record: dict[str, Any], target: str) -> None:
    """FC-02: no destructive Git worktree synchronization, even after branch CAS."""
    _safe_checkout(repo, record)
    reversed_direction = target == record['source']
    after = record['preimages'] if reversed_direction else record['hashes']
    before = record['hashes'] if reversed_direction else record['preimages']
    if target not in (record['source'], record['prepared_commit']):
        raise ab.AdoptionBlocked('unapproved reconciliation target')
    escrow_base = record.get('escrow')
    if not escrow_base:
        raise ab.AdoptionBlocked('durable reconciliation escrow missing')
    try:
        gg.reconcile(repo, record['source'], target, after, before,
                     Path(escrow_base) / ('compensate' if reversed_direction else 'publish'), ab._path)
    except gg.GuardedGitError as exc:
        raise ab.AdoptionBlocked(str(exc)) from exc
    if _git(repo, 'rev-parse', 'HEAD^{tree}') != _git(repo, 'rev-parse', target + '^{tree}'):
        raise ab.AdoptionBlocked('checkout tree verification mismatch')


def _rederive(repo: Path, row: dict[str, Any]) -> tuple[dict[str, bytes | None], dict[str, bytes | None]]:
    """Derive approved bytes from immutable source objects + pinned overlay, not journal."""
    import zipfile
    source = row['source_head']
    with tempfile.TemporaryDirectory(prefix='devtool-fc02-review-') as tmp:
        root = Path(tmp)
        with zipfile.ZipFile(row['overlay']) as archive:
            metadata = json.loads(archive.read('.devtool-artifact.json'))
        required = {str(e['path']) for e in metadata['inventory']['entries']}
        required.update(('.devtool.toml','.gitignore',*ab.WRAPPER))
        for rel in required:
            ab._path(root, rel)
            result = subprocess.run(['git','-C',str(repo),'show',source+':'+rel],
                                    capture_output=True, timeout=30)
            if result.returncode == 0:
                dest = ab._path(root, rel)
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(result.stdout)
                # Native launcher mode is relevant to reviewed wrapper migration.
                mode = _git(repo,'ls-tree',source,'--',rel)
                if mode.startswith('100755'):
                    dest.chmod(0o755)
        return ab._desired(root, row)


def _verify_record(repo: Path, row: dict[str, Any], record: dict[str, Any]) -> None:
    expected, before = _rederive(repo, row)
    hashes = {k: ab._sha(v) if v is not None else None for k,v in sorted(expected.items())}
    preimages = {k: ab._sha(before.get(k)) if before.get(k) is not None else None for k in sorted(expected)}
    if record.get('source') != row['source_head'] or record.get('source_tree') != row['source_tree']:
        raise ab.AdoptionBlocked('journal source identity differs from reviewed manifest')
    if record.get('hashes') != hashes or record.get('preimages') != preimages:
        raise ab.AdoptionBlocked('prepared journal bytes differ from independently reviewed overlay')
    if _git(repo,'rev-parse',row['source_head']+'^{tree}') != row['source_tree']:
        raise ab.AdoptionBlocked('source Git tree changed')
    try:
        gg.verify_candidate(repo, row['source_head'], record['prepared_commit'],
                            record['prepared_tree'], hashes, preimages)
    except gg.GuardedGitError as exc:
        raise ab.AdoptionBlocked(str(exc)) from exc



def _candidate(repo: Path, row: dict[str, Any]) -> dict[str, Any]:
    identity = _identity(repo, row)
    if identity['head'] != row['source_head'] or identity['tree'] != row['source_tree']:
        raise ab.AdoptionBlocked('source HEAD/tree drift: review a new source-bound overlay')
    if not ab._clean(repo):
        raise ab.AdoptionBlocked('dirty checkout: prepare requires clean tracked and untracked source')
    from .overlay.recovery import overlay_recovery_plan
    recovery = overlay_recovery_plan(repo, limit=25)
    if recovery.get('marker_issues') or recovery.get('active_transactions'):
        raise ab.AdoptionBlocked('stale or live overlay transaction requires explicit recovery')
    desired, before = ab._desired(repo, row)
    _, _, native = ab._archive(repo, row)
    ab._verify_head_preimages(repo, native)
    desired = {k: v for k, v in desired.items() if before.get(k) != v}
    if not desired:
        raise ab.AdoptionBlocked('no candidate changes: already applied or unreviewed source')
    candidate = dict(id=row['id'], repo=str(repo), source=identity['head'], source_tree=identity['tree'],
                branch=identity['branch'], remote=identity['remote'],
                hashes={k: ab._sha(v) if v is not None else None for k, v in sorted(desired.items())},
                preimages={k: ab._sha(before.get(k)) if before.get(k) is not None else None for k in sorted(desired)},
                desired=desired)
    _offline_hooks(repo, row, candidate)
    return candidate


def _prepare_commit(repo: Path, row: dict[str, Any], item: dict[str, Any], digest: str) -> dict[str, Any]:
    try:
        commit, tree = gg.pinned_commit(repo, item['source'], item['desired'],
                     (f'devtool: adopt {row["id"]}\n').encode(),
                     mode_for=lambda rel: '100755' if rel == 'devtoolw' else '100644')
    except gg.GuardedGitError as exc:
        raise ab.AdoptionBlocked(str(exc)) from exc
    if tree == item['source_tree']:
        raise ab.AdoptionBlocked('candidate tree equals source')
    ref = f'refs/devtool/adoption-prepared/{digest[:32]}/{row["id"]}'
    existing = ab._git(repo, 'rev-parse', '--verify', ref, check=False)
    if existing:
        old_tree = _git(repo, 'rev-parse', existing+'^{tree}')
        if old_tree != tree or _git(repo,'rev-parse',existing+'^') != item['source']:
            raise ab.AdoptionBlocked('prepared-ref collision: manual review required')
        commit = existing
    else:
        _git(repo, 'update-ref', ref, commit, ZERO)
    record = {k:v for k,v in item.items() if k != 'desired'} | {
        'prepared_commit':commit,'prepared_tree':tree,'prepared_ref':ref,'state':'PREPARED'}
    _verify_record(repo,row,record)
    return record



def _offline_hooks(repo: Path, row: dict[str, Any], item: dict[str, Any]) -> None:
    # Deliberately not a shell hook runner. No network, venv or package manager,
    # arbitrary argv, config-supplied Python, or downloaded executable code.
    allowed = {'git-clean', 'source-hashes', 'no-symlinks', 'toml-parse'}
    hooks = row.get('offline_hooks', ['git-clean', 'source-hashes', 'no-symlinks', 'toml-parse'])
    if not isinstance(hooks, list) or any(not isinstance(h, str) or h not in allowed for h in hooks):
        raise ab.AdoptionBlocked('offline_hooks must be reviewed, built-in offline preflight identifiers')
    for hook in hooks:
        if hook == 'git-clean' and not ab._clean(repo):
            raise ab.AdoptionBlocked('offline git-clean hook refused')
        if hook == 'no-symlinks':
            for rel in item['desired']:
                ab._path(repo, rel)
        if hook == 'source-hashes':
            for rel, digest in item['preimages'].items():
                file = ab._path(repo, rel)
                old = file.read_bytes() if file.is_file() else None
                if (ab._sha(old) if old is not None else None) != digest:
                    raise ab.AdoptionBlocked(f'offline preimage changed: {rel}')
        if hook == 'toml-parse' and '.devtool.toml' in item['desired']:
            import tomllib
            content = item['desired']['.devtool.toml']
            if content is not None:
                tomllib.loads(content.decode('utf-8'))


def upgrade_review(manifest: Path, state_dir: Path) -> dict[str, Any]:
    """No writes: a three-way *proposal* for drifted source, never an auto-merge.

    The manifest must be rebound to the reviewed new HEAD/tree and supplied
    with new exact overlay SHA-256 before it can be staged or published.
    """
    source, digest, _, _ = _get(manifest, state_dir)
    results = []
    for row in source['repositories']:
        repo = Path(row['path']); result: dict[str, Any] = {'id': row['id'], 'state': 'BLOCKED'}
        try:
            identity = _identity(repo, row)
            if not ab._clean(repo):
                raise ab.AdoptionBlocked('dirty source requires manual reconciliation')
            incoming, _, meta = ab._archive(repo, row)
            entries = meta.get('inventory', {}).get('entries', [])
            if _git(repo, 'rev-parse', row['source_head'] + '^{tree}') != row['source_tree']:
                raise ab.AdoptionBlocked('original source tree is unavailable or untrusted')
            for entry in entries:
                rel = str(entry['path'])
                ab._path(repo, rel)
                original = subprocess.run(['git', '-C', str(repo), 'show',
                                           f'{row["source_head"]}:{rel}'], capture_output=True, timeout=25)
                if entry.get('action') == 'add':
                    if original.returncode == 0:
                        raise ab.AdoptionBlocked(f'overlay add was tracked at source: {rel}')
                elif original.returncode or ab._sha(original.stdout) != entry.get('source_sha256'):
                    raise ab.AdoptionBlocked(f'original overlay preimage differs: {rel}')
            if identity['head'] == row['source_head'] and identity['tree'] == row['source_tree']:
                result = {'id': row['id'], 'state': 'SOURCE_UNCHANGED', 'next_action': 'adopt-batch prepare'}
            else:
                changed = []
                for entry in entries:
                    rel = str(entry['path']); ab._path(repo, rel)
                    old = subprocess.run(['git','-C',str(repo),'show',f'{row["source_head"]}:{rel}'],capture_output=True,timeout=25)
                    base = old.stdout if old.returncode == 0 else None
                    newer = subprocess.run(['git','-C',str(repo),'show',f'HEAD:{rel}'],capture_output=True,timeout=25)
                    current = newer.stdout if newer.returncode == 0 else None
                    incoming_bytes = incoming[rel]
                    if current == base or current == incoming_bytes:
                        status = 'CLEAN_MERGE_CANDIDATE'
                        proposed = incoming_bytes
                    elif any(v is None or len(v)>64000 or b'\0' in v for v in (base,current,incoming_bytes)):
                        status = 'REVIEW_REQUIRED'
                        proposed = None
                    else:
                        from .overlay.merge import _merge_text
                        code, proposal = _merge_text(current, base, incoming_bytes)
                        status = 'CLEAN_MERGE_CANDIDATE' if code == 0 else 'CONFLICT'
                        proposed = proposal if code == 0 else None
                    changed.append({'path': rel, 'state': status,
                                    'base_sha256': ab._sha(base) if base is not None else None,
                                    'current_sha256': ab._sha(current) if current is not None else None,
                                    'candidate_sha256': ab._sha(proposed) if proposed is not None else None})
                result = {'id': row['id'], 'state': 'REBASE_REVIEW_REQUIRED',
                          'current_head': identity['head'], 'current_tree': identity['tree'],
                          'paths': changed,
                          'next_action': 'Review `devtool overlay rebase`; create a new source-bound overlay and manifest; run staged prepare/publish'}
        except (ab.AdoptionBlocked, OSError, RuntimeError, ValueError) as exc:
            result['reason'] = str(exc)
        results.append(result)
    return {'schema': SCHEMA, 'read_only': True, 'ok': all(x['state'] != 'BLOCKED' for x in results),
            'review_required': any(x['state']=='REBASE_REVIEW_REQUIRED' for x in results),
            'repositories': results, 'manifest_sha256': digest, 'auto_rebase_applied': False,
            'cross_repository_atomic': False, 'formal_release_qualified': False}


def recovery_plan(manifest: Path, state_dir: Path) -> dict[str, Any]:
    source, digest, journal, data = _get(manifest, state_dir)
    from .overlay.recovery import overlay_recovery_plan
    rows = []
    for row in source['repositories']:
        repo=Path(row['path']); saved=data['repositories'].get(row['id'])
        native=overlay_recovery_plan(repo, limit=25)
        ref = _identity(repo,row)['head']
        blockers = list(native.get('marker_issues') or [])
        if native.get('active_transactions'):
            blockers.append('existing native Devtool transaction requires recovery')
        if saved and ref not in (saved['source'], saved['prepared_commit']):
            blockers.append('branch advanced outside staged transaction')
        action = ('review native recovery before any staged mutation' if blockers else
                  'adopt-batch recover --yes or resume-staged --yes after review' if data['overall']=='PARTIAL_COMMIT' else
                  'adopt-batch resume-staged --yes or recover --yes to abort' if data['overall']=='PREPARING' else
                  'adopt-batch resume-staged --yes' if data['overall'] in ('PREPARED','PUBLISHING') else
                  'no action')
        rows.append({'id':row['id'],'head':ref,'staged_state':saved.get('state') if saved else 'NONE',
                     'native_active_transactions':len(native.get('active_transactions') or []),
                     'blockers':blockers,'safe_next_action':action})
    return {'schema':SCHEMA,'read_only':True,'overall':data['overall'],
            'manifest_sha256':digest,'journal':str(journal),'repositories':rows,
            'ok':all(not r['blockers'] for r in rows),'formal_release_qualified':False}

def _get(manifest: Path, state_dir: Path) -> tuple[dict[str, Any], str, Path, dict[str, Any]]:
    source = ab._read_config(manifest)
    ab._checked_state_path(state_dir, source['repositories'])
    digest = ab._sha(ab._json(source))
    journal = state_dir / f'staged-{digest}.json'
    if journal.is_symlink() or (journal.exists() and journal.stat().st_size > 2_000_000):
        raise ab.AdoptionBlocked('unsafe staged adoption journal')
    data = json.loads(journal.read_text()) if journal.exists() else {
        'schema': SCHEMA, 'manifest_sha256': digest, 'overall': 'NEW', 'repositories': {}}
    if data.get('schema') != SCHEMA or data.get('manifest_sha256') != digest:
        raise ab.AdoptionBlocked('staged adoption journal identity differs')
    return source, digest, journal, data


def review(manifest: Path, state_dir: Path) -> dict[str, Any]:
    source, digest, journal, data = _get(manifest, state_dir)
    rows = []
    for row in source['repositories']:
        repo = Path(row['path']); saved = data['repositories'].get(row['id'])
        if saved:
            cur = _identity(repo, row)
            if cur['head'] == saved['prepared_commit']:
                rows.append({'id': row['id'], 'state': 'PUBLISHED', 'prepared_commit': saved['prepared_commit']})
            elif cur['head'] == saved['source']:
                rows.append({'id': row['id'], 'state': saved['state'], 'prepared_commit': saved['prepared_commit']})
            else:
                rows.append({'id': row['id'], 'state': 'DRIFTED', 'reason': 'HEAD no longer matches source or prepared lease'})
        else:
            try:
                candidate = _candidate(repo, row)
                rows.append({'id': row['id'], 'state': 'FRESH', 'paths': candidate['hashes']})
            except (ab.AdoptionBlocked, OSError) as exc:
                rows.append({'id': row['id'], 'state': 'BLOCKED', 'reason': str(exc)})
    safe = all(r['state'] in ('FRESH','PREPARED','PUBLISHED') for r in rows)
    return {'schema': SCHEMA, 'ok': safe, 'read_only': True, 'overall': data['overall'],
            'manifest_sha256': digest, 'repositories': rows, 'cross_repository_atomic': False,
            'formal_release_qualified': False, 'journal': str(journal)}


def prepare(manifest: Path, state_dir: Path, *, yes: bool = False) -> dict[str, Any]:
    if not yes:
        raise ab.AdoptionBlocked('prepare creates Git objects and refs: requires --yes')
    with ab._lock(state_dir):
        source, digest, journal, data = _get(manifest, state_dir)
        if data['overall'] in ('PREPARED','PUBLISHING','PARTIAL_COMMIT','COMPLETE'):
            # Re-examine HEAD/ref identities; never blindly trust a journal.
            for row in source['repositories']:
                saved = data['repositories'].get(row['id'])
                if not saved or _git(Path(row['path']), 'rev-parse', saved['prepared_ref']) != saved['prepared_commit']:
                    raise ab.AdoptionBlocked('missing/mismatched durable prepared commit')
                _verify_record(Path(row['path']), row, saved)
                head = _identity(Path(row['path']), row)['head']
                if head not in (saved['source'], saved['prepared_commit']):
                    raise ab.AdoptionBlocked('prepared repository HEAD advanced')
            return data
        if data['overall'] not in ('NEW','PREPARE_FAILED','PREPARING'):
            raise ab.AdoptionBlocked('staged transaction cannot be prepared in this state')
        # GLOBAL READ-ONLY PREFLIGHT: before the first object or prepared-ref write.
        candidates = [_candidate(Path(row['path']), row) for row in source['repositories']]
        data['overall'] = 'PREPARING'; data['repositories'] = {}
        ab._save(journal, data)
        try:
            for row, item in zip(source['repositories'], candidates):
                record = _prepare_commit(Path(row['path']), row, item, digest)
                data['repositories'][row['id']] = record
                ab._save(journal, data)
        except Exception as exc:
            data['overall'] = 'PREPARE_FAILED'
            data['failure'] = str(exc)
            ab._save(journal, data)
            raise
        data['overall'] = 'PREPARED'; data.pop('failure', None)
        ab._save(journal, data)
        return data


def _publication_preflight(source: dict[str, Any], data: dict[str, Any]) -> None:
    # Preflight every repository again before FIRST target-ref update.
    for row in source['repositories']:
        record = data['repositories'].get(row['id'])
        if not record:
            raise ab.AdoptionBlocked('global prepare is incomplete')
        repo = Path(row['path'])
        head = _identity(repo, row)['head']
        if head not in (record['source'], record['prepared_commit']):
            raise ab.AdoptionBlocked(f'{row["id"]}: HEAD moved since prepare')
        if _git(repo, 'rev-parse', record['prepared_ref']) != record['prepared_commit']:
            raise ab.AdoptionBlocked(f'{row["id"]}: prepared ref replaced')
        _verify_record(repo, row, record)
        if head == record['source'] and not ab._clean(repo):
            raise ab.AdoptionBlocked(f'{row["id"]}: checkout dirty after prepare')
        if head == record['prepared_commit']:
            _safe_checkout(repo, record)


def publish(manifest: Path, state_dir: Path, *, yes: bool = False) -> dict[str, Any]:
    if not yes:
        raise ab.AdoptionBlocked('publishing target branches requires --yes')
    # prepare obtains its own lock; it is idempotent if already prepared.
    prepare(manifest, state_dir, yes=True)
    with ab._lock(state_dir):
        source, digest, journal, data = _get(manifest, state_dir)
        if data['overall'] == 'ROLLED_BACK':
            raise ab.AdoptionBlocked('compensated transaction cannot be republished without new reviewed manifest')
        _publication_preflight(source, data)
        data['overall'] = 'PUBLISHING'; ab._save(journal, data)
        for row in source['repositories']:
            record = data['repositories'][row['id']]
            repo = Path(row['path'])
            try:
                cur = _identity(repo, row)['head']
                if cur == record['source']:
                    if not ab._clean(repo):
                        raise ab.AdoptionBlocked('checkout changed during publication')
                    # Compare-and-swap is the only operation that changes the branch.
                    try:
                        gg.cas_branch(repo, 'refs/heads/' + row['branch'], record['prepared_commit'], record['source'])
                    except gg.GuardedGitError as exc:
                        raise ab.AdoptionBlocked(str(exc)) from exc
                    record['state'] = 'REF_PUBLISHED'; ab._save(journal, data)
                record.setdefault('escrow',str(state_dir / 'fc02-escrow' / digest / row['id']))
                ab._save(journal, data)
                _materialize(repo, record, record['prepared_commit'])
                record['state'] = 'PUBLISHED'
                ab._save(journal, data)
            except Exception as exc:
                data['overall'] = 'PARTIAL_COMMIT'
                data['failure'] = {'id': row['id'], 'reason': str(exc)}
                ab._save(journal, data)
                raise ab.AdoptionBlocked(f'PARTIAL_COMMIT: {row["id"]}: {exc}; use recover or resume after review') from exc
        data['overall'] = 'COMPLETE'; data.pop('failure', None)
        ab._save(journal, data)
        return data


def recover(manifest: Path, state_dir: Path, *, yes: bool = False) -> dict[str, Any]:
    if not yes:
        raise ab.AdoptionBlocked('compensating branch rollback requires --yes')
    with ab._lock(state_dir):
        source, digest, journal, data = _get(manifest, state_dir)
        if data['overall'] in ('PREPARING','PREPARE_FAILED','PREPARED'):
            for row in source['repositories']:
                record = data['repositories'].get(row['id'])
                if _identity(Path(row['path']),row)['head'] != row['source_head']:
                    raise ab.AdoptionBlocked('branch advanced; cannot abort prepare')
                if record:
                    _verify_record(Path(row['path']), row, record)
            data['overall'] = 'ROLLED_BACK'
            data['recovery'] = 'aborted prepare without branch publication'
            ab._save(journal,data)
            return data
        if data['overall'] not in ('PARTIAL_COMMIT', 'PUBLISHING', 'COMPLETE'):
            raise ab.AdoptionBlocked('nothing published to recover')
        # GLOBAL PRECHECK prevents rolling back earlier repos if a later HEAD moved.
        _publication_preflight(source, data)
        for row in reversed(source['repositories']):
            repo = Path(row['path']); record = data['repositories'][row['id']]
            head = _identity(repo, row)['head']
            if head == record['source']:
                record['state'] = 'COMPENSATED'; continue
            try:
                gg.cas_branch(repo, 'refs/heads/' + row['branch'], record['source'], record['prepared_commit'])
                record['state'] = 'REF_COMPENSATED'; ab._save(journal, data)
                _materialize(repo, record, record['source'])
                record['state'] = 'COMPENSATED'; ab._save(journal, data)
            except Exception as exc:
                data['overall'] = 'PARTIAL_COMMIT'; data['failure'] = {'id': row['id'], 'reason': str(exc)}
                ab._save(journal, data)
                raise ab.AdoptionBlocked(f'PARTIAL_COMMIT: compensation blocked at {row["id"]}: {exc}') from exc
        data['overall'] = 'ROLLED_BACK'; data.pop('failure', None)
        ab._save(journal, data)
        return data
