#!/usr/bin/env python3
"""Fail-closed, source-bound full pytest matrix for GitHub-hosted Linux runners.

This runner has no publishing, signing, GitHub dispatch or source mutation path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_RE = re.compile(r'(tests/[^\s]+\.py::[^\s]+)')


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def modules(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in (root / 'tests').rglob('test_*.py')
                  if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts)


def inventory_digest(names: list[str]) -> str:
    return sha256_bytes(('\n'.join(names) + '\n').encode())


def partitions(root: Path, names: list[str], count: int) -> list[list[str]]:
    if count < 1:
        raise ValueError('count must be positive')
    groups = [[] for _ in range(count)]
    weights = [0] * count
    for name in sorted(names, key=lambda x: (-((root / x).stat().st_size), x)):
        weight = max(1, (root / name).stat().st_size)
        index = min(range(count), key=lambda x: (weights[x], x))
        groups[index].append(name)
        weights[index] += weight
    return [sorted(group) for group in groups]


def junit_summary(file: Path) -> dict[str, int]:
    ret = dict(tests=0, failures=0, errors=0, skipped=0)
    if not file.is_file() or not file.stat().st_size:
        return ret
    try:
        root = ET.parse(file).getroot()
        for case in root.iter('testcase'):
            ret['tests'] += 1
            if case.find('failure') is not None:
                ret['failures'] += 1
            elif case.find('error') is not None:
                ret['errors'] += 1
            elif case.find('skipped') is not None:
                ret['skipped'] += 1
    except ET.ParseError:
        return dict(tests=0, failures=0, errors=1, skipped=0)
    return ret


def repo_sha(root: Path) -> str:
    return subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()


def terminate_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def execute_module(root: Path, name: str, output: Path, position: int,
                   timeout: int, idle_timeout: int) -> dict:
    base = f'module-{position:03d}'
    log = output / 'logs' / (base + '.log')
    xml = output / 'junit' / (base + '.xml')
    log.parent.mkdir(parents=True, exist_ok=True)
    xml.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, '-m', 'pytest', '-vv', '--tb=short', '-ra',
               f'--junitxml={xml}', name]
    env = dict(os.environ)
    for token in ('GH_TOKEN', 'GITHUB_TOKEN', 'DEVTOOL_RELEASE_KEYSTORE_B64',
                  'DEVTOOL_RELEASE_STORE_PASSWORD', 'DEVTOOL_RELEASE_KEY_PASSWORD'):
        env.pop(token, None)
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONPATH'] = str(root)
    start = time.monotonic()
    last_activity = start
    reason = ''
    last_test = ''
    code = None
    pending = b''
    with log.open('wb') as stream:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=root, env=env, start_new_session=True, bufsize=0)
        assert proc.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        try:
            while True:
                now = time.monotonic()
                if now - start > timeout:
                    reason = 'module_timeout'
                    terminate_group(proc)
                    break
                if now - last_activity > idle_timeout:
                    reason = 'idle_timeout'
                    terminate_group(proc)
                    break
                events = selector.select(timeout=1)
                if events:
                    data = os.read(proc.stdout.fileno(), 65536)
                    if not data:
                        break
                    last_activity = time.monotonic()
                    stream.write(data)
                    stream.flush()
                    pending += data
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        decoded = line.decode('utf-8', 'replace')
                        match = NODE_RE.search(decoded)
                        if match:
                            last_test = match.group(1).rstrip(',:')
                        print(decoded, flush=True)
                    if len(pending) > 65536:
                        print(pending.decode('utf-8', 'replace'), flush=True)
                        pending = b''
                if proc.poll() is not None and not events:
                    # The pipe may still contain output; next iteration will reach EOF.
                    continue
            if pending:
                print(pending.decode('utf-8', 'replace'), flush=True)
            code = proc.wait(timeout=5)
        finally:
            selector.close()
            proc.stdout.close()
            terminate_group(proc)
    counts = junit_summary(xml)
    elapsed = round(time.monotonic() - start, 2)
    okay = reason == '' and code == 0 and counts['tests'] > 0 and counts['failures'] == 0 and counts['errors'] == 0
    return {
        'module': name, 'status': 'PASS' if okay else 'FAIL', 'exit_code': code,
        'reason': reason or ('pytest_failure' if not okay else ''),
        'seconds': elapsed, 'last_test': last_test, **counts,
        'log': str(log.relative_to(output)), 'log_sha256': hash_file(log),
        'junit': str(xml.relative_to(output)) if xml.is_file() else None,
        'junit_sha256': hash_file(xml) if xml.is_file() else None,
    }


def write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, sort_keys=True, indent=2) + '\n')
    os.replace(temp, path)


def run_shard(args) -> int:
    root = Path(args.root).resolve()
    names = modules(root)
    if not names:
        raise SystemExit('FAIL: zero test modules')
    if not 1 <= args.shard <= args.shards:
        raise SystemExit('invalid shard number')
    sha = repo_sha(root)
    expected = os.environ.get('GITHUB_SHA')
    if expected and sha != expected:
        raise SystemExit(f'Source SHA mismatch: checkout={sha}, GitHub={expected}')
    chosen = partitions(root, names, args.shards)[args.shard - 1]
    if not chosen:
        raise SystemExit('FAIL: empty shard')
    dest = Path(args.out).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'source_sha': sha, 'python_version': platform.python_version(), 'os': sys.platform,
              'repository': os.environ.get('GITHUB_REPOSITORY', ''),
              'shard': args.shard, 'shards': args.shards,
              'inventory_sha256': inventory_digest(names),
              'inventory_count': len(names), 'modules_expected': chosen,
              'modules': [], 'complete': False, 'success': False,
              'formal_release_qualified': False}
    try:
        for index, name in enumerate(chosen, 1):
            print(f'\n=== shard {args.shard}/{args.shards} module {index}/{len(chosen)}: {name} ===', flush=True)
            item = execute_module(root, name, dest, index, args.module_timeout, args.idle_timeout)
            # Tests execute in a separate process and must not change tracked checkout files.
            source_dirty = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--'], cwd=root).returncode != 0
            if source_dirty:
                item['status'] = 'FAIL'
                item['reason'] = 'tracked_source_modified_by_tests'
            report['modules'].append(item)
            write_json(dest / 'receipt.json', report)
            print(f'==> {item["status"]}: {item["tests"]} cases, {item["seconds"]}s', flush=True)
    finally:
        report['complete'] = len(report['modules']) == len(chosen)
        report['success'] = report['complete'] and all(item['status'] == 'PASS' for item in report['modules'])
        write_json(dest / 'receipt.json', report)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
                summary.write(f'### Pytest shard {args.shard}/{args.shards}\n\n')
                summary.write(f'Status: **{"PASS" if report["success"] else "FAIL"}**; modules {len(report["modules"])}/{len(chosen)}.\n\n')
                for item in report['modules']:
                    if item['status'] != 'PASS':
                        summary.write(f'- {item["module"]}: {item["reason"]} (last: {item["last_test"]})\n')
    return 0 if report['success'] else 1


def summarize(args) -> int:
    root = Path(args.root).resolve()
    names = modules(root)
    expected_sha = repo_sha(root)
    inventory = inventory_digest(names)
    found = sorted(Path(args.artifacts).glob('devtool-full-shard-*/receipt.json'))
    issues = []
    receipts = {}
    locations = {}
    if not names:
        issues.append('No test modules discovered')
    for path in found:
        try:
            d = json.loads(path.read_text())
        except (ValueError, OSError):
            issues.append(f'Unreadable receipt: {path}')
            continue
        shard = d.get('shard')
        if not isinstance(shard, int) or not 1 <= shard <= args.shards or shard in receipts:
            issues.append(f'Invalid or duplicate shard: {path}')
            continue
        if path.parent.name != f'devtool-full-shard-{shard}':
            issues.append(f'Artifact naming mismatch: {path}')
        receipts[shard] = d
        locations[shard] = path.parent
    expected_map = partitions(root, names, args.shards)
    total_tests = total_failed = total_errors = total_skipped = 0
    reported_mods = []
    failures = []
    for i in range(1, args.shards + 1):
        d = receipts.get(i)
        if d is None:
            issues.append(f'Missing shard {i:02d}')
            continue
        expected = expected_map[i - 1]
        if d.get('source_sha') != expected_sha or d.get('inventory_sha256') != inventory or d.get('inventory_count') != len(names):
            issues.append(f'Shard {i:02d}: source/inventory mismatch')
        expected_repo = os.environ.get('GITHUB_REPOSITORY', '')
        if expected_repo and d.get('repository') != expected_repo:
            issues.append(f'Shard {i:02d}: repository mismatch')
        if d.get('shards') != args.shards or d.get('modules_expected') != expected:
            issues.append(f'Shard {i:02d}: partition mismatch')
        actual = d.get('modules', [])
        if not isinstance(actual, list):
            issues.append(f'Shard {i:02d}: invalid module list')
            continue
        got = [m.get('module') for m in actual if isinstance(m, dict)]
        if got != expected:
            issues.append(f'Shard {i:02d}: missing/reordered/unexpected module')
        reported_mods.extend(got)
        if not d.get('complete') or not d.get('success'):
            issues.append(f'Shard {i:02d}: incomplete or failed')
        for m in actual:
            if not isinstance(m, dict):
                issues.append(f'Shard {i:02d}: invalid module entry')
                continue
            total_tests += m.get('tests', 0)
            total_failed += m.get('failures', 0)
            total_errors += m.get('errors', 0)
            total_skipped += m.get('skipped', 0)
            if m.get('status') != 'PASS':
                failures.append({'shard': i, 'module': m.get('module'),
                                 'reason': m.get('reason'), 'last_test': m.get('last_test')})
            for field, hash_field in [('log', 'log_sha256'), ('junit', 'junit_sha256')]:
                relative = m.get(field)
                if not relative or not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
                    issues.append(f'Shard {i:02d}: missing/invalid {field} for {m.get("module")}')
                    continue
                item = (locations[i] / relative)
                if not item.is_file() or hash_file(item) != m.get(hash_field):
                    issues.append(f'Shard {i:02d}: {field} integrity mismatch for {m.get("module")}')
    if sorted(reported_mods) != names:
        issues.append('Global coverage mismatch: not exactly one result per discovered module')
    qualified = len(issues) == 0 and len(failures) == 0
    result = {'schema_version': 1, 'status': 'PASS' if qualified else 'FAIL',
              'source_sha': expected_sha, 'total_modules': len(names),
              'covered_modules': len(reported_mods), 'expected_shards': args.shards,
              'present_shards': len(receipts), 'tests': total_tests,
              'failures': total_failed, 'errors': total_errors, 'skipped': total_skipped,
              'issues': issues, 'failed_modules': failures,
              'formal_release_qualified': False,
              'scope': 'GitHub hosted Linux Python regression only; not device/signing/V105 qualification'}
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / 'summary.json', result)
    lines = ['# Devtool hosted Python regression summary', '', f'**Result: {result["status"]}**',
             f'- Source: `{expected_sha}`', f'- Shards: {len(receipts)}/{args.shards}',
             f'- Modules: {len(reported_mods)}/{len(names)}',
             f'- Tests: {total_tests}; failures: {total_failed}; errors: {total_errors}; skips: {total_skipped}',
             '', '**Formal release: NOT QUALIFIED.** This matrix does not substitute for device, signing or historical evidence.', '']
    if issues:
        lines.extend(['## Evidence / coverage failures', *['- ' + item for item in issues], ''])
    if failures:
        lines.extend(['## Failing modules', *[f'- Shard {x["shard"]}: `{x["module"]}` — {x["reason"]} ({x["last_test"]})' for x in failures], ''])
    (out / 'SUMMARY.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines), flush=True)
    step = os.environ.get('GITHUB_STEP_SUMMARY')
    if step:
        with open(step, 'a') as f:
            f.write('\n'.join(lines) + '\n')
    return 0 if qualified else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--root', default=str(REPO_ROOT))
    run.add_argument('--shards', type=int, default=24)
    run.add_argument('--shard', type=int, required=True)
    run.add_argument('--out', required=True)
    run.add_argument('--module-timeout', type=int, default=600)
    run.add_argument('--idle-timeout', type=int, default=240)
    agg = sub.add_parser('summarize')
    agg.add_argument('--root', default=str(REPO_ROOT))
    agg.add_argument('--shards', type=int, default=24)
    agg.add_argument('--artifacts', required=True)
    agg.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    if args.command == 'run':
        if args.module_timeout < 2 or args.idle_timeout < 2:
            parser.error('timeouts must be >=2')
        return run_shard(args)
    return summarize(args)


if __name__ == '__main__':
    raise SystemExit(main())
