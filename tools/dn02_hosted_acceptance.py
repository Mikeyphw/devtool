#!/usr/bin/env python3
"""DN02: fail-closed real-host .NET 6 acceptance; never claims release qualification."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

CONSUMER_PATHS = (
    '.github/r05-sdk10/global.json',
    'Tests/RoosterControl.PureTests/RoosterControl.PureTests.csproj',
    'Tests/RoosterControl.PureTests/packages.lock.json',
    'Tests/EggsInWild.AnimalNotes.Tests/EggsInWild.AnimalNotes.Tests.csproj',
    'Tests/EggsInWild.AnimalNotes.Tests/packages.lock.json',
    'scripts/verify_dotnet_trx.py',
)
TRX = {
    'rooster': 'RoosterControl.PureTests',
    'animalnotes': 'EggsInWild.AnimalNotes.Tests',
}
SHA = re.compile(r'[0-9a-f]{40}\Z')
SDK = '10.0.100'
RUNTIME = '6.0.36'


class AcceptanceError(ValueError):
    pass


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                                text=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcceptanceError('Cannot inspect pinned Git source') from exc
    if result.returncode:
        raise AcceptanceError('Required source is not tracked or Git is unavailable')
    return result.stdout.strip()


def _sha256(file: Path) -> str:
    h = hashlib.sha256()
    with file.open('rb') as source:
        for data in iter(lambda: source.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def _file(repo: Path, name: str) -> Path:
    cur = repo
    for part in name.split('/'):
        cur = cur / part
        if cur.is_symlink():
            raise AcceptanceError('Symlinked source is not permitted: ' + name)
    if not cur.is_file() or cur.stat().st_size > 2_000_000:
        raise AcceptanceError('Missing or oversized input: ' + name)
    _git(repo, 'ls-files', '--error-unmatch', '--', name)
    return cur


def _json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise AcceptanceError('Missing or unbounded evidence: ' + str(path))
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise AcceptanceError('Invalid JSON: ' + str(path)) from exc
    if not isinstance(value, dict):
        raise AcceptanceError('Expected JSON object: ' + str(path))
    return value


def _write_json(path: Path, value: dict) -> None:
    if path.is_symlink() or path.exists():
        raise AcceptanceError('Evidence output already exists: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    if temporary.exists() or temporary.is_symlink():
        raise AcceptanceError('Temporary evidence output already exists')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _check_context(env: dict[str, str]) -> dict:
    expected = {
        'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'Mikeyphw/devtool',
        'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
    }
    for key, value in expected.items():
        if env.get(key) != value:
            raise AcceptanceError('DN02 hosted context mismatch: ' + key)
    run_id, attempt = env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT', '')
    if not (run_id.isdecimal() and attempt.isdecimal() and int(run_id) > 0 and int(attempt) > 0):
        raise AcceptanceError('Missing real GitHub run identity')
    return {
        'repository': env['GITHUB_REPOSITORY'],
        'run_id': int(run_id), 'run_attempt': int(attempt),
        'workflow_sha': env.get('GITHUB_SHA', ''),
        'event_name': env.get('GITHUB_EVENT_NAME', ''),
        'ref': env.get('GITHUB_REF', ''),
    }


def inspect_inputs(devtool: Path, consumer: Path, devtool_sha: str,
                   consumer_sha: str, env: dict[str, str]) -> dict:
    if not (SHA.fullmatch(devtool_sha) and SHA.fullmatch(consumer_sha)):
        raise AcceptanceError('Expected full 40-character immutable Git SHAs')
    if _git(devtool, 'rev-parse', 'HEAD') != devtool_sha:
        raise AcceptanceError('Devtool checkout changed from event source')
    if _git(consumer, 'rev-parse', 'HEAD') != consumer_sha:
        raise AcceptanceError('Consumer checkout changed from pinned source')
    context = _check_context(env)
    checks = {name: _sha256(_file(consumer, name)) for name in CONSUMER_PATHS}
    pin = _json(_file(consumer, '.github/r05-sdk10/global.json'))
    if pin.get('sdk', {}).get('version') != SDK:
        raise AcceptanceError('Consumer SDK pin changed')
    for name in TRX.values():
        csproj = _file(consumer, f'Tests/{name}/{name}.csproj')
        try:
            project = ET.fromstring(csproj.read_bytes())
        except ET.ParseError as exc:
            raise AcceptanceError('Invalid actual test project XML') from exc
        values = [n.text.strip() for n in project.iter()
                  if n.tag.rsplit('}', 1)[-1] == 'TargetFramework' and n.text]
        if values != ['net6.0']:
            raise AcceptanceError('Actual test project target framework has changed: ' + name)
        lock = _json(_file(consumer, f'Tests/{name}/packages.lock.json'))
        if (lock.get('version') != 1 or
            not isinstance(lock.get('dependencies', {}).get('net6.0'), dict) or
            len(lock['dependencies']['net6.0']) < 1):
            raise AcceptanceError('Test project has no valid net6 locked dependencies: ' + name)
    if str(devtool) not in sys.path:
        sys.path.insert(0, str(devtool))
    from devtool.actions_rm09_dotnet import verify_dotnet_host
    try:
        host = verify_dotnet_host(consumer / '.github/r05-sdk10',
                                 sdk_version=SDK, legacy_runtime_version=RUNTIME)
    except ValueError as exc:
        raise AcceptanceError('Real hosted .NET verification failed: ' + str(exc)) from exc
    if host.get('verified_runtimes') != [{'family': 'Microsoft.NETCore.App', 'version': RUNTIME}]:
        raise AcceptanceError('Expected runtime family not independently observed')
    return {
        'schema_version': 1, 'kind': 'devtool.dn02-hosted-net6-preflight',
        'devtool_sha': devtool_sha, 'consumer_sha': consumer_sha,
        'host': host, 'source_sha256': checks,
        'github': context, 'sdk_version': SDK, 'legacy_runtime_version': RUNTIME,
        'release_qualified': False, 'game_dependent_tests': 'NOT_QUALIFIED',
    }


def verify_results(devtool: Path, consumer: Path, evidence: Path,
                   devtool_sha: str, consumer_sha: str,
                   env: dict[str, str]) -> dict:
    expected = inspect_inputs(devtool, consumer, devtool_sha, consumer_sha, env)
    preflight = _json(evidence / 'preflight.json')
    if preflight != expected:
        raise AcceptanceError('Host/source/run identity changed after preflight')
    script = _file(consumer, 'scripts/verify_dotnet_trx.py')
    spec = importlib.util.spec_from_file_location('pinned_consumer_trx', script)
    if spec is None or spec.loader is None:
        raise AcceptanceError('Cannot load immutable consumer TRX verifier')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    results = {}
    for key in TRX:
        path = evidence / key / 'results.trx'
        if path.is_symlink() or not path.is_file():
            raise AcceptanceError('Missing actual TRX: ' + key)
        try:
            parsed = module.inspect_trx(path, min_executed=1)
        except (ValueError, OSError) as exc:
            raise AcceptanceError('Failed or missing actual tests in ' + key + ': ' + str(exc)) from exc
        results[key] = {'summary': parsed, 'sha256': _sha256(path), 'bytes': path.stat().st_size}
    return {
        **expected, 'kind': 'devtool.dn02-hosted-net6-acceptance',
        'preflight_sha256': _sha256(evidence / 'preflight.json'),
        'actual_test_receipts': results,
        'locked_restore_required': True, 'real_hosted_execution': True,
        'release_qualified': False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['preflight', 'verify'])
    parser.add_argument('--devtool', type=Path, required=True)
    parser.add_argument('--consumer', type=Path, required=True)
    parser.add_argument('--devtool-sha', required=True)
    parser.add_argument('--consumer-sha', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        devtool, consumer, evidence = (args.devtool.resolve(), args.consumer.resolve(),
                                       args.evidence.resolve())
        if args.evidence.is_symlink():
            raise AcceptanceError('Symlinked evidence path')
        if args.mode == 'preflight':
            result = inspect_inputs(devtool, consumer, args.devtool_sha,
                                    args.consumer_sha, dict(os.environ))
            _write_json(evidence / 'preflight.json', result)
        else:
            result = verify_results(devtool, consumer, evidence, args.devtool_sha,
                                    args.consumer_sha, dict(os.environ))
            _write_json(evidence / 'acceptance.json', result)
    except (AcceptanceError, OSError, TypeError, KeyError, ValueError) as exc:
        print('DN02_HOSTED_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({'status': 'PASS', 'kind': result['kind'],
                      'run_id': result['github']['run_id']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
