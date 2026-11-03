#!/usr/bin/env python3
"""DN03-R2/R06d: GitHub-hosted native Devtool DAG identity and evidence gate.

A PASS proves the five command-runner Devtool nodes and two real net6.0
xUnit receipts passed. It does NOT certify RM-09's generated per-node provider
workflow, the host publisher's identity, or a signed/released artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

DEVTOOL_REPO = 'Mikeyphw/devtool'
CONSUMER_SHA = '549d6cd0d08894dfac4950e362e437eb06010729'
CONSUMER_PATHS = (
    '.devtool.toml',
    '.github/r05-sdk10/global.json',
    'Tests/RoosterControl.PureTests/RoosterControl.PureTests.csproj',
    'Tests/RoosterControl.PureTests/packages.lock.json',
    'Tests/EggsInWild.AnimalNotes.Tests/EggsInWild.AnimalNotes.Tests.csproj',
    'Tests/EggsInWild.AnimalNotes.Tests/packages.lock.json',
    'scripts/verify_r06b_native_dotnet.py',
    'scripts/verify_dotnet_trx.py',
)
SDK = '10.0.100'
RUNTIME = '6.0.36'
SHA = re.compile(r'[0-9a-f]{40}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')
NODES = ('ci-source', 'r06b_restore_pure', 'r06b_test_pure',
         'r06b_restore_animal', 'r06b_test_animal')
TRX = ('r06b-pure.trx', 'r06b-animalnotes.trx')


class R06DError(ValueError):
    pass


def _git(root: Path, *args: str) -> str:
    try:
        run = subprocess.run(('git', '-C', str(root), *args), text=True,
                             capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise R06DError('Cannot inspect pinned Git source') from exc
    if run.returncode:
        raise R06DError('Git source identity is unavailable')
    return run.stdout.strip()


def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 3_000_000:
        raise R06DError('Missing/linked/oversized source or receipt: ' + path.name)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or not (1 <= path.stat().st_size <= 2_000_000):
        raise R06DError('Evidence absent or unsafe: ' + path.name)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError) as exc:
        raise R06DError('Malformed evidence JSON: ' + path.name) from exc
    if not isinstance(data, dict):
        raise R06DError('Evidence is not an object: ' + path.name)
    return data


def _context(env: dict[str, str]) -> dict:
    for name, value in [('GITHUB_ACTIONS','true'),('GITHUB_REPOSITORY',DEVTOOL_REPO),
                        ('RUNNER_OS','Linux'),('RUNNER_ARCH','X64')]:
        if env.get(name) != value:
            raise R06DError('Unreviewed hosted context: ' + name)
    expected = env.get('EXPECTED_DEVTOOL_SHA', '')
    if not SHA.fullmatch(expected):
        raise R06DError('Expected Devtool head must be immutable Git SHA')
    if not SHA.fullmatch(env.get('GITHUB_SHA','')):
        raise R06DError('GitHub event commit identity is invalid')
    run, attempt = env.get('GITHUB_RUN_ID', ''), env.get('GITHUB_RUN_ATTEMPT','')
    if not (run.isdecimal() and attempt.isdecimal() and int(run)>0 and int(attempt)>0):
        raise R06DError('Missing GitHub run/attempt identity')
    return {'repository': DEVTOOL_REPO, 'devtool_head':expected,
            'github_event_sha':env.get('GITHUB_SHA',''),
            'run_id':int(run), 'run_attempt':int(attempt),
            'event_name':env.get('GITHUB_EVENT_NAME','')}


def inspect(devtool: Path, consumer: Path, sdk_dir: Path, runtime_receipt: Path,
            env: dict[str, str]) -> dict:
    context=_context(env)
    if _git(devtool,'rev-parse','HEAD')!=context['devtool_head']:
        raise R06DError('Devtool source does not match GitHub PR/push head')
    if _git(consumer,'rev-parse','HEAD')!=CONSUMER_SHA:
        raise R06DError('Pinned Mike_SVMods source drift')
    files = {}
    for name in CONSUMER_PATHS:
        part=consumer
        for item in name.split('/'):
            part=part/item
            if part.is_symlink():
                raise R06DError('Symlinked consumer input')
        _git(consumer,'ls-files','--error-unmatch','--',name)
        files[name]=_sha(part)
    from devtool.actions_rm09_dotnet import verify_dotnet_host
    from devtool.dn03_runtime_only import SHA512, RUNTIME as PIN
    host=verify_dotnet_host(sdk_dir, sdk_version=SDK, legacy_runtime_version=RUNTIME)
    if host.get('verified_runtimes') != [{'family':'Microsoft.NETCore.App','version':RUNTIME}]:
        raise R06DError('Exact host runtime not observed')
    receipt=_read(runtime_receipt)
    if (receipt.get('kind')!='devtool.dn03-runtime-only' or
            receipt.get('archive_sha512')!=SHA512 or
            receipt.get('runtime')!=PIN or
            receipt.get('sdk_installed_by_this_provisioner') is not False):
        raise R06DError('Runtime-only payload has no reviewed Microsoft SHA-512')
    # Refuse an unreviewed SDK selected by the native consumer job directory.
    try:
        native=subprocess.run(['dotnet','--version'],cwd=consumer/'Tests',
                              capture_output=True,text=True,timeout=15,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise R06DError('Native consumer SDK unavailable') from exc
    if native.returncode or native.stdout.strip()!=SDK:
        raise R06DError('Native consumer SDK differs from isolated 10.0.100 pin')
    return {'schema_version':1, 'kind':'devtool.dn03-r06d-hosted-preflight',
            'devtool_sha':context['devtool_head'],'consumer_sha':CONSUMER_SHA,
            'github':context,'host':host,'native_sdk':native.stdout.strip(),
            'source_sha256':files,
            'runtime_receipt_sha256':_sha(runtime_receipt),
            'runtime_archive_sha512':SHA512,
            'rm09_native_provider_qualified':False,
            'release_qualified':False,'remote_authenticated':False}


def _verify(evidence: Path, devtool: Path, consumer: Path, sdk_dir: Path,
            runtime_receipt: Path, env: dict[str, str]) -> dict:
    expected=inspect(devtool, consumer, sdk_dir, runtime_receipt, env)
    if _read(evidence/'preflight.json')!=expected:
        raise R06DError('Preflight source/host/run identity changed during native DAG')
    native=evidence/'native'
    receipt=_read(native/'receipt.json')
    if (receipt.get('status')!='HOSTED_NATIVE_LOCKED_DOTNET_PASS' or
            receipt.get('consumer_sha')!=CONSUMER_SHA or
            receipt.get('devtool_sha')!=expected['devtool_sha'] or
            receipt.get('node_count')!=5 or
            receipt.get('native_nodes')!=list(NODES) or
            receipt.get('release_qualified') is not False):
        raise R06DError('Native 5-node command DAG receipt refused')
    tests=receipt.get('test_receipts')
    if not isinstance(tests,dict) or set(tests)!=set(TRX):
        raise R06DError('Expected two independently real TRX results')
    for name in TRX:
        row=tests[name]
        if not isinstance(row,dict) or row.get('failed')!=0 or row.get('notExecuted')!=0 or row.get('executed',0)<1 or row.get('passed')!=row.get('executed'):
            raise R06DError('Failed, skipped or missing actual test receipt')
        _sha(consumer/'.ci-results/r06b'/name)
    for name in ('plan.json','preflight.json','invoke.json'):
        if receipt.get(name.replace('.json','_sha256')) != _sha(native/name):
            raise R06DError('Pinned consumer native evidence SHA-256 differs')
    return {**expected,'kind':'devtool.dn03-r06d-hosted-native-acceptance',
            'native_nodes':list(NODES),'native_receipt_sha256':_sha(native/'receipt.json'),
            'native_command_dag_qualified':True,
            'rm09_generated_node_dag_qualified':False,
            'actual_test_counts':{name:tests[name]['passed'] for name in TRX},
            'release_qualified':False}


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('preflight','verify'))
    parser.add_argument('--devtool',type=Path,required=True)
    parser.add_argument('--consumer',type=Path,required=True)
    parser.add_argument('--sdk-dir',type=Path,required=True)
    parser.add_argument('--runtime-receipt',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.evidence.is_symlink():
            raise R06DError('Symlinked hosted evidence directory')
        evidence=args.evidence.resolve()
        value=(inspect(args.devtool.resolve(),args.consumer.resolve(),args.sdk_dir.resolve(),
                       args.runtime_receipt.resolve(),dict(os.environ)) if args.mode=='preflight'
               else _verify(evidence,args.devtool.resolve(),args.consumer.resolve(),
                            args.sdk_dir.resolve(),args.runtime_receipt.resolve(),dict(os.environ)))
        file=evidence/('preflight.json' if args.mode=='preflight' else 'acceptance.json')
        if file.exists() or file.is_symlink():
            raise R06DError('Refusing to overwrite existing hosted evidence')
        file.parent.mkdir(parents=True,exist_ok=True)
        with file.open('x',encoding='utf-8') as out:
            json.dump(value,out,sort_keys=True,indent=2)
            out.write('\n')
    except (R06DError, ValueError, OSError, TypeError, KeyError) as exc:
        print('DN03_R06D_REFUSED: '+str(exc),file=sys.stderr)
        return 2
    print(json.dumps({'status':'PASS','kind':value['kind'],
                      'github_run':value['github']['run_id']},sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
