"""Portable, fail-closed .NET TRX / optional Cobertura CI receipt qualification.

Standard-library only. Local uploaded evidence is NOT remotely authenticated,
attested, device-qualified or release-signed. Reuse across consumer repositories.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

SHA40 = re.compile(r"[0-9a-f]{40}\Z")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
PROJECT = re.compile(r"[A-Za-z0-9_.+/-]+\.csproj\Z")
MAX_FILE = 8 * 1024 * 1024


class QualificationRefused(ValueError):
    """Evidence failed a source, provenance, test or coverage obligation."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise QualificationRefused(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_file(path: Path) -> bytes:
    require(path.is_file() and not path.is_symlink(), 'missing or linked evidence file')
    require(0 < path.stat().st_size <= MAX_FILE, 'empty or oversized evidence file')
    data = path.read_bytes()
    require(0 < len(data) <= MAX_FILE, 'empty or oversized evidence bytes')
    require(not any(marker in data.upper() for marker in (b'<!DOCTYPE', b'<!ENTITY')),
            'unsafe XML declaration')
    return data


def local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def trx_report(raw: bytes, expected_tests: int) -> dict:
    require(type(expected_tests) is int and 0 < expected_tests <= 100_000, 'invalid test target')
    require(len(raw) <= MAX_FILE and b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(),
            'unsafe or oversized TRX')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise QualificationRefused('invalid TRX XML') from exc
    require(local_name(root.tag) == 'TestRun', 'invalid TRX root')
    summary = [el for el in root.iter() if local_name(el.tag) == 'ResultSummary']
    require(len(summary) == 1 and summary[0].attrib.get('outcome') in ('Completed', 'Passed'),
            'missing or failing result summary')
    counters = [el for el in summary[0] if local_name(el.tag) == 'Counters']
    require(len(counters) == 1, 'missing or duplicated TRX counters')
    try:
        c = {k: int(v) for k, v in counters[0].attrib.items()}
    except ValueError as exc:
        raise QualificationRefused('invalid TRX counters') from exc
    require(all(v >= 0 for v in c.values()), 'negative TRX counter')
    require(c.get('total') == expected_tests and c.get('executed') == expected_tests and
            c.get('passed') == expected_tests and c.get('failed') == 0 and
            c.get('notExecuted') == 0, 'unexpected TRX test counters')
    require(all(c.get(k, 0) == 0 for k in ('error', 'timeout', 'aborted', 'inconclusive',
                                            'notRunnable', 'disconnected', 'warning',
                                            'completed', 'inProgress', 'pending')),
            'TRX contains non-passing counter')
    results = [el for el in root.iter() if local_name(el.tag) == 'UnitTestResult']
    require(len(results) == expected_tests, 'TRX test result count mismatch')
    require(all(r.attrib.get('outcome') == 'Passed' for r in results), 'non-passing TRX result')
    require(all(r.attrib.get('testId') and r.attrib.get('executionId') for r in results),
            'TRX missing test or execution identity')
    require(len({r.attrib['executionId'] for r in results}) == expected_tests,
            'duplicate TRX execution identity')
    return {'total': expected_tests, 'passed': expected_tests, 'failed': 0, 'skipped': 0,
            'report_sha256': sha(raw), 'report_format': 'trx'}


def cobertura_report(raw: bytes, min_line_rate: float = 0.0) -> dict:
    require(len(raw) <= MAX_FILE and b'<!DOCTYPE' not in raw.upper() and b'<!ENTITY' not in raw.upper(),
            'unsafe or oversized coverage XML')
    require(0 <= min_line_rate <= 1, 'invalid coverage threshold')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise QualificationRefused('invalid Cobertura XML') from exc
    require(local_name(root.tag) == 'coverage', 'invalid coverage root')
    try:
        rate = float(root.attrib['line-rate'])
        covered = int(root.attrib['lines-covered'])
        valid = int(root.attrib['lines-valid'])
    except (KeyError, ValueError, OverflowError) as exc:
        raise QualificationRefused('missing Cobertura counters') from exc
    require(valid > 0 and 0 <= covered <= valid and 0 <= rate <= 1,
            'invalid Cobertura line counters')
    require(abs(rate - covered / valid) <= 0.0002, 'Cobertura rate contradicts line counters')
    classes = [x for x in root.iter() if local_name(x.tag) == 'class']
    require(len(classes) > 0 and rate >= min_line_rate, 'coverage below threshold or no classes')
    return {'collected': True, 'line_rate': rate, 'lines_covered': covered,
            'lines_valid': valid, 'class_count': len(classes), 'report_sha256': sha(raw)}


def validate_inputs(source: str, devtool_sha: str, run_id: int, attempt: int,
                    profile: str, project: str, test_id: str) -> None:
    require(bool(SHA40.fullmatch(source)) and bool(SHA40.fullmatch(devtool_sha)),
            'invalid source/runtime SHA')
    require(type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            'invalid run attempt identity')
    require(bool(PROFILE.fullmatch(profile)) and bool(PROFILE.fullmatch(test_id)),
            'invalid profile or test identity')
    require(bool(PROJECT.fullmatch(project)) and '..' not in project.split('/') and
            not project.startswith('/') and '//' not in project and '\\' not in project,
            'unsafe project path')


def review(*, source: str, devtool_sha: str, run_id: int, attempt: int,
           profile: str, project: str, test_id: str, trx: Path, expected_tests: int,
           sdk: str, coverage: Path | None = None,
           require_coverage: bool = False, min_line_rate: float = 0.0) -> dict:
    validate_inputs(source, devtool_sha, run_id, attempt, profile, project, test_id)
    require(bool(re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', sdk)), 'invalid SDK version')
    report = trx_report(checked_file(trx), expected_tests)
    require(not require_coverage or coverage is not None,
            'coverage required but no instrumented report supplied')
    coverage_result = (cobertura_report(checked_file(coverage), min_line_rate)
                       if coverage is not None else {'collected': False, 'status': 'NOT_COLLECTED'})
    return {'schema_version': 1, 'kind': 'devtool.ci-portable-test-receipt',
            'source_sha': source, 'devtool_sha': devtool_sha,
            'run_id': run_id, 'run_attempt': attempt, 'host_profile': profile,
            'project': project, 'test_id': test_id, 'sdk_version': sdk,
            'tests': report, 'coverage': coverage_result,
            'remote_authenticated': False, 'sigstore_attested': False,
            'device_qualified': False, 'release_qualified': False}


def aggregate(*, root: Path, source: str, devtool_sha: str, run_id: int,
              attempt: int, profiles: tuple[str, ...], tests: dict[str, tuple[str, int]],
              require_coverage: bool = False) -> dict:
    require(root.is_dir() and not root.is_symlink(), 'missing or linked receipt directory')
    require(len(set(profiles)) == len(profiles) > 0 and len(set(tests)) == len(tests) > 0,
            'ambiguous required matrix')
    expected = {f'{p}-{t}.json' for p in profiles for t in tests}
    found = {p.name for p in root.iterdir() if p.is_file() or p.is_symlink()}
    require(expected == found, 'missing, excess or ambiguous matrix receipt')
    received = []
    for p in profiles:
        for t, (project, count) in tests.items():
            f = root / f'{p}-{t}.json'
            raw = checked_file(f)
            try:
                value = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise QualificationRefused('invalid JSON receipt') from exc
            require(type(value) is dict and value.get('schema_version') == 1 and
                    value.get('kind') == 'devtool.ci-portable-test-receipt' and
                    value.get('source_sha') == source and value.get('devtool_sha') == devtool_sha and
                    value.get('run_id') == run_id and value.get('run_attempt') == attempt and
                    value.get('host_profile') == p and value.get('test_id') == t and
                    value.get('project') == project,
                    'receipt source/run/matrix mismatch')
            expected_report = {'total': count, 'passed': count, 'failed': 0, 'skipped': 0}
            result = value.get('tests')
            require(type(result) is dict and all(result.get(k) == v for k, v in expected_report.items())
                    and result.get('report_format') == 'trx' and
                    bool(re.fullmatch(r'[0-9a-f]{64}', str(result.get('report_sha256', '')))),
                    'receipt test counts/hash invalid')
            cover = value.get('coverage')
            require(type(cover) is dict and type(cover.get('collected')) is bool,
                    'coverage status missing')
            if require_coverage:
                require(cover.get('collected') is True, 'required coverage is not collected')
            if cover.get('collected'):
                require(0 <= cover.get('line_rate', -1) <= 1 and
                        bool(re.fullmatch(r'[0-9a-f]{64}',str(cover.get('report_sha256','')))),
                        'invalid coverage evidence')
            else:
                require(cover.get('status') == 'NOT_COLLECTED', 'uncollected coverage falsely claimed')
            require(all(value.get(k) is False for k in
                        ('remote_authenticated', 'sigstore_attested', 'device_qualified', 'release_qualified')),
                    'forbidden trust or release claim')
            received.append({'profile': p, 'test_id': t, 'project': project,
                             'passed': count, 'receipt_sha256': sha(raw),
                             'trx_sha256': result['report_sha256'],
                             'coverage_collected': cover['collected']})
    return {'schema_version': 1, 'kind': 'devtool.ci-cross-platform-matrix-review',
            'source_sha': source, 'devtool_sha': devtool_sha,
            'run_id': run_id, 'run_attempt': attempt, 'profiles': list(profiles),
            'test_projects': len(tests), 'matrix_entries_verified': len(received),
            'test_executions_verified': sum(e['passed'] for e in received),
            'coverage_all_collected': all(e['coverage_collected'] for e in received),
            'matrix': received, 'remote_authenticated': False,
            'sigstore_attested': False, 'device_qualified': False,
            'release_qualified': False}


def write_new(out: Path, obj: dict) -> None:
    require(out.parent.is_dir() and not out.parent.is_symlink() and
            not out.exists() and not out.is_symlink(), 'output already exists or parent invalid')
    with out.open('x', encoding='utf-8') as fp:
        json.dump(obj, fp, indent=2, sort_keys=True)
        fp.write('\n')


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='op', required=True)
    for name in ('review', 'aggregate'):
        s = sub.add_parser(name)
        s.add_argument('--source', required=True)
        s.add_argument('--devtool-sha', required=True)
        s.add_argument('--run-id', type=int, required=True)
        s.add_argument('--attempt', type=int, required=True)
        s.add_argument('--output', required=True, type=Path)
        s.add_argument('--require-coverage', action='store_true')
    a = sub.choices['review']
    a.add_argument('--profile', required=True)
    a.add_argument('--project', required=True)
    a.add_argument('--test-id', required=True)
    a.add_argument('--trx', type=Path, required=True)
    a.add_argument('--expected-tests', type=int, required=True)
    a.add_argument('--sdk', required=True)
    a.add_argument('--coverage', type=Path)
    a.add_argument('--min-line-rate', type=float, default=0.0)
    b = sub.choices['aggregate']
    b.add_argument('--receipts-dir', type=Path, required=True)
    b.add_argument('--profiles', nargs='+', required=True)
    b.add_argument('--test', nargs='+', action='append', required=True,
                   metavar='PART')
    try:
        args = p.parse_args(argv)
        if args.op == 'review':
            result = review(source=args.source, devtool_sha=args.devtool_sha,
                            run_id=args.run_id, attempt=args.attempt,
                            profile=args.profile, project=args.project, test_id=args.test_id,
                            trx=args.trx, expected_tests=args.expected_tests, sdk=args.sdk,
                            coverage=args.coverage, require_coverage=args.require_coverage,
                            min_line_rate=args.min_line_rate)
        else:
            tests = {}
            for value in args.test:
                require(len(value) == 3 and value[0] not in tests,
                        'test declaration must be ID PROJECT COUNT')
                tests[value[0]] = (value[1], int(value[2]))
            result = aggregate(root=args.receipts_dir, source=args.source,
                               devtool_sha=args.devtool_sha, run_id=args.run_id,
                               attempt=args.attempt, profiles=tuple(args.profiles), tests=tests,
                               require_coverage=args.require_coverage)
        write_new(args.output, result)
    except (QualificationRefused, OSError, ValueError, TypeError, OverflowError) as exc:
        print('DEVTOOL_FINAL_CI_REFUSED: ' + str(exc), file=sys.stderr)
        return 2
    print(('DEVTOOL_FINAL_MATRIX_PASS' if args.op == 'aggregate' else
           'DEVTOOL_FINAL_TEST_RECEIPT_PASS') +
          ' run=' + str(args.run_id) + ' source=' + args.source)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
