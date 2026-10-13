#!/usr/bin/env python3
"""A tiny, offline JSON checklist for testing Devtool Actions through real work.

Creates local notes only. It never runs GitHub jobs, reads secrets or uploads data.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CASES = [
    ('G0_BASELINE', 'Run the normal Termux debug build or an everyday Devtool test job.'),
    ('G1_READONLY', 'Look up workflows, runs and an artifact for an actual project.'),
    ('G2_PIPELINE', 'Run a real pipeline, observe a waiting node, resume once; test stop on a separate run.'),
    ('G3_UI', 'Open the Textual pipeline inspector and inspect the Android HUD; GTK is optional.'),
    ('G4_AAB', 'Build a real AAB on GitHub, import with bundletool and locally sign.'),
    ('G5_MATRIX', 'Run the Python+Go Linux smoke, then all eight GitHub matrix cells.'),
    ('G6_EVIDENCE', 'Reverify a real locally signed APK or AAB from the registry.'),
    ('G7_METRICS', 'Inspect real CI metrics; runner/GitLab/draft publishing are optional.'),
    ('G8_REPORT', 'Re-run source qualification report and keep unfinished gates pending.'),
    ('NEGATIVE_WHEN_NATURAL', 'Save safe 401/403/429/expired-archive failure evidence only if naturally encountered.'),
]
STATUSES = ('not-run', 'passed', 'failed', 'blocked', 'skipped')

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def dump(path: Path, value: object) -> None:
    if path.is_symlink():
        raise ValueError('Refusing symlinked JSON output')
    raw = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + '\n'
    temp = path.with_name(path.name + '.new')
    if temp.is_symlink():
        raise ValueError('Refusing symlinked temporary JSON')
    with temp.open('x', encoding='utf-8') as f:
        os.chmod(temp, 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)

def load(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1000000:
        raise ValueError('Test tracker missing, symlinked or too large')
    result = json.loads(path.read_text('utf-8'))
    if result.get('schema_version') != 1 or not isinstance(result.get('cases'), dict):
        raise ValueError('Unrecognized result schema')
    return result

def head(repo: Path) -> str:
    try:
        proc = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=5, check=False)
        return proc.stdout.strip() if proc.returncode == 0 and re.fullmatch(r'[0-9a-f]{40}',proc.stdout.strip()) else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''

def main() -> int:
    ap=argparse.ArgumentParser(description='Offline JSON notes for real Devtool Actions testing')
    sub=ap.add_subparsers(dest='command',required=True)
    for name in ('init','mark','summary'):
        p=sub.add_parser(name)
        p.add_argument('--out',required=True,help='Folder for private JSON test notes')
        if name == 'init':p.add_argument('--repo',default='.')
        if name == 'mark':
            p.add_argument('--case',required=True,choices=[x[0] for x in CASES])
            p.add_argument('--status',required=True,choices=STATUSES[1:])
            p.add_argument('--note',default='',help='Short SECRET-FREE observation')
            p.add_argument('--run-id',type=int,default=0)
            p.add_argument('--artifact-sha',default='')
    args=ap.parse_args()
    folder=Path(args.out).expanduser()
    if args.command == 'init':
        if folder.is_symlink():raise ValueError('Output folder may not be a symlink')
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        path=folder/'results.json'
        if path.exists() or path.is_symlink():raise ValueError('Results already exist: use mark or choose a new output folder')
        report={'schema_version':1,'kind':'devtool.actions.real-work',
                'created_at':now(),'source_head':head(Path(args.repo).expanduser()),
                'campaign_closed':False,'qualified_release':False,
                'cases':{k:{'title':title,'status':'not-run','updated_at':None,
                    'note':'','run_id':None,'artifact_sha256':None} for k,title in CASES}}
        dump(path,report)
        print(path)
        return 0
    report=load(folder/'results.json')
    if args.command=='mark':
        if len(args.note)>400 or '\n' in args.note or '\r' in args.note:
            raise ValueError('Note must be a single line of at most 400 characters')
        if args.run_id < 0 or args.run_id > 2**63-1:
            raise ValueError('Invalid run ID')
        if args.artifact_sha and not re.fullmatch(r'[0-9a-f]{64}', args.artifact_sha):
            raise ValueError('Artifact SHA-256 must be 64 lowercase hex characters')
        row=report['cases'][args.case]
        row.update(status=args.status,updated_at=now(),note=args.note,
                   run_id=args.run_id or None,artifact_sha256=args.artifact_sha or None)
        dump(folder/'results.json',report)
        print(f'{args.case}: {args.status}')
        return 0
    for key,row in report['cases'].items():
        print(f"{key:<25} {row['status']:<9} {row['note'][:65]}")
    print('Qualification: incomplete unless separately proven; this tracker is personal test notes only.')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f'real-work tracker: {exc}',file=sys.stderr)
        raise SystemExit(2)
