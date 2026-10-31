"""R35: exact-run R34 lab archive collection into the existing canonical registry.

This is an integration adapter, not a dispatcher, second artifact downloader,
Android signer or release qualification mechanism. All remote archive bytes are
obtained and reverified using ACTIONS-04; APK inspection uses ACTIONS-05.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile
from typing import Any

from .actions_artifacts import artifact_list, download_artifact, verify_download, check_zip
from .actions_android import android_collect
from .artifact_registry import load_registry, register_paths, verify_record
from .github_actions import GitHubActionsClient, GitHubActionsError, _slug, resolve_token

WORKFLOW = 'devtool-android-r34-generated-hosted-dag.yml'
PACKAGE = 'org.devtool.r33.fixture'
APK_NAME = 'app-debug.apk'
STAGED_PATH = 'app/build/outputs/apk/debug/app-debug.apk'
HEX40 = re.compile('[a-f0-9]{40}\\Z')
HEX64 = re.compile('[a-f0-9]{64}\\Z')
_MAX_EVIDENCE_JSON = 1024 * 1024
_ROLES = ('node-01-evidence', 'node-02-evidence', 'debug-apk')


def _deny(reason: str) -> None:
    raise GitHubActionsError('R35 refused: ' + reason)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _json_member(z: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        info = z.getinfo(name)
        if info.is_dir() or not 0 < info.file_size <= _MAX_EVIDENCE_JSON:
            _deny('missing or oversized ' + name)
        raw = json.loads(z.read(info))
        if not isinstance(raw, dict):
            _deny('invalid JSON object: ' + name)
        return raw
    except (KeyError, ValueError, UnicodeError, TypeError, RuntimeError):
        _deny('missing or invalid JSON: ' + name)


def _requires(data: dict, fields: dict, label: str) -> None:
    if any(type(data.get(k)) is not type(v) or data[k] != v for k, v in fields.items()):
        _deny(label + ' identity/status mismatch')


def select(client: GitHubActionsClient, repository: str, run_id: int, attempt: int,
           source_sha: str, signer_sha: str, package: str = PACKAGE) -> dict[str, Any]:
    """Read-only remote admission. Never infer latest attempt/source or a signer."""
    repository = _slug(repository)
    if (type(run_id) is not int or run_id < 1 or type(attempt) is not int or attempt < 1
            or not HEX40.fullmatch(source_sha) or not HEX64.fullmatch(signer_sha)
            or package != PACKAGE):
        _deny('explicit run, attempt, source SHA, debug package and signer SHA pins required')
    inv = artifact_list(client, repository, run_id)
    remote = inv['remote_run']
    workflow = client.workflow(repository, WORKFLOW)
    if (remote.get('completed') is not True or remote.get('conclusion') != 'success'
            or remote.get('event') != 'workflow_dispatch'
            or remote.get('workflow_id') != workflow.get('id')
            or remote.get('run_attempt') != attempt or remote.get('head_sha') != source_sha):
        _deny('remote run status, workflow, event, attempt or source differs from pins')
    names = {role: f'devtool-r34-{role}-attempt{attempt}' for role in _ROLES}
    selected: dict[str, dict[str, Any]] = {}
    for role, name in names.items():
        matches = [x for x in inv['artifacts'] if x['name'] == name]
        if len(matches) != 1:
            _deny('missing or ambiguous exact-attempt artifact role: ' + role)
        row = matches[0]
        if (row.get('run_id') != run_id or row.get('run_attempt') != attempt
                or row.get('head_sha') != source_sha or row.get('expired') is not False
                or not re.fullmatch('sha256:[a-fA-F0-9]{64}', str(row.get('digest') or ''))):
            _deny('invalid metadata/sha256 for exact-attempt artifact: ' + role)
        selected[role] = row
    if len({x['id'] for x in selected.values()}) != len(_ROLES):
        _deny('duplicate GitHub artifact identity')
    return {'schema': 'devtool.r35.plan.v1', 'repository': repository, 'run_id': run_id,
            'attempt': attempt, 'source_sha': source_sha, 'workflow_id': workflow['id'],
            'workflow': WORKFLOW, 'package': package, 'signer_sha256': signer_sha,
            'artifacts': selected, 'release_qualified': False, 'device_qualified': False}


def validate_chain(plan: dict[str, Any], archives: dict[str, Path]) -> dict[str, Any]:
    """Cross-check three authenticated ZIPs, native staged bytes and producer receipts."""
    if set(archives) != set(_ROLES):
        _deny('all three archive roles required')
    # Reuse the ACTIONS-04 bounded CRC/path/symlink/zip-bomb review even for
    # callers that supply archives without using the coordinator downloader.
    for source in archives.values():
        check_zip(source)
    head = plan['source_sha']; rid = str(plan['run_id']); attempt = plan['attempt']
    results: dict[str, dict[str, Any]] = {}
    bs: dict[str, dict[str, Any]] = {}
    sources: dict[str, dict[str, Any]] = {}
    plans: dict[str, dict[str, Any]] = {}
    with zipfile.ZipFile(archives['node-01-evidence']) as lint, zipfile.ZipFile(archives['node-02-evidence']) as build, zipfile.ZipFile(archives['debug-apk']) as az:
        for name, z, stage, node in (('node-01-evidence', lint, 'lint', 'node-01'),
                                      ('node-02-evidence', build, 'build', 'node-02')):
            names = z.namelist()
            mandatory = {'bootstrap.json', 'source.json', 'plan.json', 'result.json'}
            if name == 'node-02-evidence':
                mandatory |= {'registry-evidence.json', 'registry-snapshot.json',
                              'native-outputs/node-02/receipt.json'}
            if not mandatory.issubset(names) or len(names) > 32 or any(
                not (n in mandatory or n.startswith('native-outputs/') or n.startswith('native-inputs/'))
                for n in names):
                _deny('unexpected or missing bounded ' + stage + ' evidence members')
            b = _json_member(z, 'bootstrap.json')
            _requires(b, {'schema': 'devtool.r34.bootstrap.v1', 'source_sha': head,
                          'run_id': rid, 'run_attempt': attempt, 'job': stage,
                          'status': 'NOT_QUALIFIED', 'release_qualified': False}, stage + ' bootstrap')
            bs[stage] = b
            src = _json_member(z, 'source.json')
            if src.get('devtool_sha') != head or not HEX40.fullmatch(str(src.get('fixture_sha', ''))):
                _deny(stage + ' source binding mismatch')
            sources[stage] = src
            p = _json_member(z, 'plan.json')
            _requires(p, {'devtool_sha': head, 'fixture_sha': src['fixture_sha'],
                          'release_qualified': False, 'device_qualified': False}, stage + ' DAG plan')
            if not HEX64.fullmatch(str(p.get('graph_sha256', ''))):
                _deny('invalid native graph digest')
            plans[stage] = p
            result = _json_member(z, 'result.json')
            _requires(result, {'kind': 'devtool.r33.live-node.v1', 'node': stage,
                     'status': 'PASS', 'provider': 'android-gradle',
                     'devtool_sha': head, 'github_run_id': rid, 'github_run_attempt': attempt,
                     'fixture_sha': src['fixture_sha'], 'graph_sha256': p['graph_sha256'],
                     'actual_gradle_execution': True, 'native_dag_executed': True,
                     'release_qualified': False, 'device_qualified': False}, stage + ' native result')
            native = result.get('node_result')
            if not isinstance(native, dict):
                _deny('missing ' + stage + ' native node receipt')
            _requires(native, {'status': 'LOCAL_JOB_SUCCEEDED', 'job_id': node,
                       'github_source': src['fixture_sha'], 'graph_sha256': p['graph_sha256'],
                       'run_id': rid, 'attempt': attempt}, stage + ' RM-06 result')
            results[stage] = result
        if (sources['lint']['fixture_sha'] != sources['build']['fixture_sha'] or
                plans['lint']['graph_sha256'] != plans['build']['graph_sha256'] or
                plans['lint'].get('jobs') != plans['build'].get('jobs')):
            _deny('lint and build refer to different frozen native graphs')
        jobs = plans['build']['jobs']
        if (not isinstance(jobs, list) or len(jobs) != 2
                or [x.get('id') for x in jobs] != ['node-01', 'node-02']
                or jobs[0].get('needs') != [] or jobs[1].get('needs') != ['node-01']):
            _deny('producer DAG is not the reviewed lint-to-build contract')
        staged = _json_member(build, 'native-outputs/node-02/receipt.json')
        _requires(staged, {'source_commit': sources['build']['fixture_sha'],
                           'graph_sha256': plans['build']['graph_sha256'],
                           'run_id': rid, 'attempt': attempt}, 'native staging')
        expected = results['build'].get('apk')
        if not isinstance(expected, dict):
            _deny('build has no APK receipt')
        if az.namelist() != [APK_NAME]:
            _deny('standalone APK archive must contain exactly app-debug.apk')
        member = az.getinfo(APK_NAME)
        if member.file_size <= 0 or member.file_size > 150 * 1024 * 1024:
            _deny('APK member outside size policy')
        h = hashlib.sha256(); size = 0
        with az.open(member) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(chunk); size += len(chunk)
        digest = h.hexdigest()
        _requires(expected, {'package': plan['package'], 'variant': 'debug',
                   'path': STAGED_PATH, 'sha256': digest, 'size': size,
                   'signer_certificate_sha256': plan['signer_sha256'],
                   'debug_signed': True, 'signing_release_authorized': False}, 'APK receipt')
        files = staged.get('files')
        if (not isinstance(files, list) or len(files) != 1 or not isinstance(files[0], dict)
                or any(files[0].get(k) != v for k, v in {
                    'path': STAGED_PATH, 'sha256': digest, 'size': size}.items())):
            _deny('native staging receipt does not match independently hashed APK bytes')
        registry = _json_member(build, 'registry-evidence.json')
        _requires(registry, {'schema': 'devtool.r34.hosted-registry.v1',
                   'status': 'PASS_LOCAL_REGISTRY_VERIFIED', 'devtool_sha': head,
                   'github_run_id': rid, 'github_run_attempt': attempt,
                   'fixture_sha': sources['build']['fixture_sha'],
                   'graph_sha256': plans['build']['graph_sha256'],
                   'apk_sha256': digest, 'apk_size': size, 'package': plan['package'],
                   'signer_sha256': plan['signer_sha256'],
                   'local_registry_registered': True, 'local_registry_verified': True,
                   'remote_actions_artifact_authenticated': False,
                   'release_qualified': False, 'device_qualified': False}, 'hosted registry')
        snap = _json_member(build, 'registry-snapshot.json')
        if (not isinstance(snap.get('items'), list) or not any(
               isinstance(i, dict) and i.get('id') == registry.get('record_id')
               and i.get('sha256') == digest for i in snap['items'])):
            _deny('hosted local registry snapshot does not match producer record')
        if not HEX64.fullmatch(str(registry.get('registry_sha256', ''))):
            _deny('hosted registry digest missing')
        with build.open('registry-snapshot.json') as stream:
            snapshot_digest = hashlib.sha256(stream.read()).hexdigest()
        if snapshot_digest != registry['registry_sha256']:
            _deny('hosted registry snapshot bytes differ from registered digest')
    return {'schema': 'devtool.r35.chain.v1', 'apk_sha256': digest, 'apk_size': size,
            'fixture_sha': sources['build']['fixture_sha'],
            'graph_sha256': plans['build']['graph_sha256'], 'hosted_record_id': registry['record_id'],
            'hosted_registry_sha256': registry['registry_sha256'],
            'package': plan['package'], 'signer_sha256': plan['signer_sha256'],
            'release_qualified': False, 'device_qualified': False}


def _report_path(root: Path, plan: dict) -> Path:
    if (not HEX40.fullmatch(str(plan.get('source_sha', '')))
            or type(plan.get('run_id')) is not int or plan['run_id'] < 1
            or type(plan.get('attempt')) is not int or plan['attempt'] < 1):
        _deny('unsafe local provenance selector')
    _slug(plan['repository'])
    folder = root.resolve()/'.devtool'/'artifacts'/'r35-imports'
    for part in (root.resolve()/'.devtool', root.resolve()/'.devtool/artifacts', folder):
        if part.is_symlink():
            _deny('symlinked R35 storage boundary')
    return folder/f"{plan['repository'].replace('/', '_')}-run{plan['run_id']}-attempt{plan['attempt']}-{plan['source_sha'][:12]}.json"


def inspect_local(root: Path, plan: dict) -> dict:
    """Read-only local provenance query. Does not call a remote API."""
    dest = _report_path(root, plan)
    if not dest.is_file() or dest.is_symlink() or dest.stat().st_size > 65536:
        return {'status': 'NOT_IMPORTED', 'remote_actions_artifact_authenticated': False}
    try:
        receipt = json.loads(dest.read_text())
    except (ValueError, OSError, UnicodeError):
        _deny('local R35 provenance record malformed')
    if (receipt.get('schema') != 'devtool.r35.import.v1'
            or receipt.get('repository') != plan['repository']
            or receipt.get('run_id') != plan['run_id']
            or receipt.get('attempt') != plan['attempt']
            or receipt.get('source_sha') != plan['source_sha']
            or receipt.get('status') != 'IMPORTED_VERIFIED_DEBUG'
            or receipt.get('remote_actions_artifact_authenticated') is not True
            or receipt.get('release_qualified') is not False
            or receipt.get('device_qualified') is not False
            or (plan.get('signer_sha256') is not None and
                receipt.get('signer_sha256') != plan['signer_sha256'])):
        _deny('stored provenance source/run/qualification mismatch')
    # Do not call load_registry here: on malformed data it may create a backup;
    # this query must remain strictly read-only.
    from .artifact_registry import registry_path
    registry_file = registry_path(root)
    if registry_file.is_symlink() or not registry_file.is_file():
        _deny('canonical registry missing or unsafe')
    try:
        registry_data = json.loads(registry_file.read_text())
    except (ValueError, OSError, UnicodeError):
        _deny('canonical registry malformed')
    if not isinstance(registry_data, dict) or not isinstance(registry_data.get('items'), list):
        _deny('canonical registry invalid')
    record = next((x for x in registry_data['items']
                   if x.get('id') == receipt.get('registry_id')), None)
    if record is None or record.get('sha256') != receipt.get('apk_sha256'):
        _deny('canonical registry no longer contains expected imported record')
    meta = record.get('metadata')
    if (not isinstance(meta, dict) or meta.get('remote_repository') != plan['repository']
            or meta.get('remote_run_id') != plan['run_id']
            or meta.get('remote_run_attempt') != plan['attempt']
            or meta.get('remote_head_sha') != plan['source_sha']
            or meta.get('remote_actions_artifact_authenticated') is not True
            or meta.get('signer_sha256') != receipt.get('signer_sha256')):
        _deny('canonical registry remote provenance identity differs')
    if 'artifacts' in plan and receipt.get('artifacts') != {
            role: {'id': plan['artifacts'][role]['id'],
                   'digest': plan['artifacts'][role]['digest']} for role in _ROLES}:
        _deny('local archive identities differ from current GitHub metadata')
    raw_path = root.resolve()/str(record.get('path', ''))
    path = raw_path.resolve()
    if (raw_path.is_symlink() or path.parent != dest.parent.resolve()
            or not path.is_file() or _sha(path) != receipt['apk_sha256']):
        _deny('registered imported APK bytes have changed or disappeared')
    return {**receipt, 'local_bytes_reverified': True}


def _verified_or_download(client: GitHubActionsClient, plan: dict, role: str, root: Path) -> dict:
    ident = plan['artifacts'][role]['id']
    try:
        return verify_download(client, plan['repository'], plan['run_id'], ident, root)
    except GitHubActionsError as exc:
        if 'No registered download for this GitHub artifact' not in str(exc):
            raise
    download_artifact(client, plan['repository'], plan['run_id'], ident, root)
    return verify_download(client, plan['repository'], plan['run_id'], ident, root)


def import_exact_run(client: GitHubActionsClient, root: Path, plan: dict) -> dict:
    """Mutating only on explicit import. No dispatch, signing, device or promotion."""
    if inspect_local(root, plan).get('status') != 'NOT_IMPORTED':
        return inspect_local(root, plan)  # idempotent exact receipt; never download again
    verified = {role: _verified_or_download(client, plan, role, root) for role in _ROLES}
    archives = {role: Path(row['file']) for role, row in verified.items()}
    chain = validate_chain(plan, archives)
    apk_id = plan['artifacts']['debug-apk']['id']
    inspected = android_collect(client, plan['repository'], root, run_id=plan['run_id'],
                 artifact_id=apk_id, fmt='apk', variant='debug',
                 expected_package=plan['package'], expected_cert=plan['signer_sha256'],
                 member=APK_NAME, materialize=False, download=False,
                 workflow=WORKFLOW, expected_source_sha=plan['source_sha'],
                 expected_run_attempt=plan['attempt'])
    details = inspected['android']
    if (details.get('sha256') != chain['apk_sha256'] or details.get('size') != chain['apk_size']
            or details.get('package') != plan['package']
            or details.get('signer_sha256') != plan['signer_sha256']
            or details.get('signature_verified') is not True
            or details.get('manifest_verified') is not True):
        _deny('independently inspected Android package differs from exact-run evidence')
    report = _report_path(root, plan)
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = report.with_suffix('.apk')
    if target.exists() or target.is_symlink() or report.exists() or report.is_symlink():
        _deny('existing or interrupted R35 import requires manual reconciliation')
    with zipfile.ZipFile(archives['debug-apk']) as z, z.open(APK_NAME) as src, target.open('xb') as dest:
        os.fchmod(dest.fileno(), 0o600)
        h = hashlib.sha256(); size = 0
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk); size += len(chunk)
            if size > 150 * 1024 * 1024:
                _deny('APK extraction exceeds R35 size policy')
            dest.write(chunk)
        dest.flush(); os.fsync(dest.fileno())
    if h.hexdigest() != chain['apk_sha256'] or size != chain['apk_size']:
        _deny('privately materialized APK differs from verified archive')
    meta = {'producer': 'github-actions-r35-verified-import',
          'state': 'android-debug-exact-run-inspected-unqualified',
          'remote_repository': plan['repository'], 'remote_run_id': plan['run_id'],
          'remote_run_attempt': plan['attempt'], 'remote_head_sha': plan['source_sha'],
          'remote_workflow_id': plan['workflow_id'], 'workflow': WORKFLOW,
          'remote_artifact_ids': {role: plan['artifacts'][role]['id'] for role in _ROLES},
          'remote_archive_digests': {role: plan['artifacts'][role]['digest'] for role in _ROLES},
          'fixture_sha': chain['fixture_sha'], 'graph_sha256': chain['graph_sha256'],
          'hosted_record_id': chain['hosted_record_id'],
          'package': plan['package'], 'variant': 'debug', 'signer_sha256': plan['signer_sha256'],
          'remote_actions_artifact_authenticated': True,
          'signature_verified': True, 'manifest_verified': True,
          'release_qualified': False, 'device_qualified': False,
          'verification_status': 'exact-run-archives-and-android-inspected'}
    created = register_paths(root, [target], metadata=meta)
    if len(created) != 1 or created[0]['sha256'] != chain['apk_sha256']:
        _deny('canonical R35 registry record not created')
    check = verify_record(root, created[0]['id'])
    if check.get('valid') is not True or check.get('current_sha256') != chain['apk_sha256']:
        _deny('new registry record failed independent local verification')
    receipt = {**chain, 'schema': 'devtool.r35.import.v1', 'status': 'IMPORTED_VERIFIED_DEBUG',
               'repository': plan['repository'], 'run_id': plan['run_id'],
               'attempt': plan['attempt'], 'source_sha': plan['source_sha'],
               'workflow': WORKFLOW, 'workflow_id': plan['workflow_id'],
               'registry_id': created[0]['id'],
               'artifacts': {role: {'id': plan['artifacts'][role]['id'],
                       'digest': plan['artifacts'][role]['digest']} for role in _ROLES},
               'remote_actions_artifact_authenticated': True,
               'apk_signature_independently_verified': True,
               'release_qualified': False, 'device_qualified': False, 'consumer_qualified': False}
    with report.open('x', encoding='utf-8') as dest:
        os.fchmod(dest.fileno(), 0o600)
        json.dump(receipt, dest, indent=2, sort_keys=True)
        dest.write('\n'); dest.flush(); os.fsync(dest.fileno())
    return receipt


def main() -> None:
    p = argparse.ArgumentParser(description='R35 exact-run GitHub Android evidence import (non-release)')
    p.add_argument('command', choices=('plan', 'import', 'inspect'))
    p.add_argument('--repo', type=Path, default=Path.cwd())
    p.add_argument('--github-repo', default='Mikeyphw/devtool')
    p.add_argument('--run-id', required=True, type=int)
    p.add_argument('--attempt', required=True, type=int)
    p.add_argument('--source-sha', required=True)
    p.add_argument('--expected-signer-sha256', required=True)
    p.add_argument('--expected-package', default=PACKAGE)
    p.add_argument('--auth', choices=('auto', 'gh', 'env', 'none'), default='auto')
    args = p.parse_args()
    # Inspect is intentionally offline and does not consume GitHub credentials.
    root = args.repo.resolve()
    if args.command == 'inspect':
        stub = {'repository': _slug(args.github_repo), 'run_id': args.run_id,
                'attempt': args.attempt, 'source_sha': args.source_sha}
        print(json.dumps(inspect_local(root, stub), sort_keys=True, indent=2))
        return
    token, _ = resolve_token(args.auth)
    client = GitHubActionsClient(token=token)
    plan = select(client, args.github_repo, args.run_id, args.attempt,
                  args.source_sha, args.expected_signer_sha256.lower(), args.expected_package)
    if args.command == 'plan':
        result = {'status': 'READ_ONLY_PLAN', **plan,
                  'remote_actions_artifact_authenticated': False}
    else:
        result = import_exact_run(client, root, plan)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except GitHubActionsError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
