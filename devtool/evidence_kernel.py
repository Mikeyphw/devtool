"""NEXT-02: typed, read-only evidence projection and host admission.

This module never asserts independent provenance, signature, device or release
qualification.  Existing producer owners retain execution and trust authority.
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import stat
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

SCHEMA_VERSION = 1
STATES = frozenset({'NOT_RUN', 'UNKNOWN', 'WAITING', 'BLOCKED', 'PASS', 'FAIL'})
DOMAINS = ('source', 'ci_presence', 'host', 'build', 'test', 'artifact_integrity',
           'artifact_authenticity', 'instrumentation', 'native_device', 'consumer',
           'signature', 'release')
_SHA40 = re.compile('[a-f0-9]{40}\\Z')
_SHA64 = re.compile('[a-f0-9]{64}\\Z')
_SAFE = re.compile('[A-Za-z0-9][A-Za-z0-9_.:/@+\\-]{0,255}\\Z')
MAX_REPORT = 16 * 1024 * 1024
MAX_TESTS = 250_000

class EvidenceError(ValueError):
    pass


def _id(value: Any, name: str, *, required: bool = True) -> str:
    if value in ('', None) and not required:
        return ''
    if not isinstance(value, str) or not _SAFE.fullmatch(value):
        raise EvidenceError(f'{name} must be a bounded literal identifier')
    return value


def _sha(value: Any, name: str, size: int = 40) -> str:
    pattern = _SHA40 if size == 40 else _SHA64
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise EvidenceError(f'{name} must be an exact lowercase SHA-{size * 4}')
    return value


def _nonnegative(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TESTS:
        raise EvidenceError(f'{name} must be a bounded nonnegative integer')
    return value


def _domain_state(value: Any, domain: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) - {'state', 'reason', 'authority'}:
        raise EvidenceError(f'{domain} has unknown fields')
    state = value.get('state')
    reason = value.get('reason')
    authority = value.get('authority')
    if state not in STATES or not isinstance(reason, str) or len(reason) > 512:
        raise EvidenceError(f'{domain} has invalid state/reason')
    if not isinstance(authority, str) or len(authority) > 256 or (authority and not _SAFE.fullmatch(authority)):
        raise EvidenceError(f'{domain} has invalid authority')
    # A caller-provided label is not independently trusted evidence.
    return {'state': state, 'reason': reason, 'authority': authority}


def validate_evidence(record: Any, *, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Validate shape and exact identity, NOT claims' producer authority.

    PASS in a supplied record remains 'untrusted_reported_PASS' unless a domain
    has been independently verified by its own provider. No status promotion.
    """
    if not isinstance(record, dict) or set(record) - {'schema_version', 'kind', 'source', 'execution', 'host', 'artifacts', 'tests', 'gates'}:
        raise EvidenceError('evidence must be a versioned object with known fields')
    if record.get('schema_version') != SCHEMA_VERSION or record.get('kind') != 'devtool.typed-evidence':
        raise EvidenceError('unsupported typed evidence version/kind')
    src = record.get('source')
    if not isinstance(src, dict) or set(src) - {'repository', 'commit', 'tree', 'target', 'job', 'node'}:
        raise EvidenceError('source identity is ambiguous')
    source = {'repository': _id(src.get('repository'), 'repository'),
              'commit': _sha(src.get('commit'), 'commit'),
              'tree': _sha(src.get('tree'), 'tree')}
    for name in ('target', 'job', 'node'):
        source[name] = _id(src.get(name), name, required=False)
    execution = record.get('execution')
    if not isinstance(execution, dict) or set(execution) - {'request_id', 'workflow', 'run_id', 'attempt', 'exo_run_id'}:
        raise EvidenceError('execution identity is ambiguous')
    run, attempt = execution.get('run_id'), execution.get('attempt')
    if (run is None) != (attempt is None):
        raise EvidenceError('run_id and attempt must both be present or absent')
    if run is not None and (type(run) is not int or run <= 0 or type(attempt) is not int or attempt <= 0):
        raise EvidenceError('run_id/attempt must be positive integers')
    ex = {k: _id(execution.get(k), k, required=False) for k in ('request_id', 'workflow', 'exo_run_id')}
    ex.update({'run_id': run, 'attempt': attempt})
    host = record.get('host')
    if not isinstance(host, dict) or set(host) - {'os', 'arch', 'toolchain', 'provisioned', 'authority'}:
        raise EvidenceError('host identity is ambiguous')
    ht = {k: _id(host.get(k), 'host.' + k, required=False) for k in ('os', 'arch', 'toolchain', 'authority')}
    if type(host.get('provisioned')) is not bool:
        raise EvidenceError('host.provisioned must be a bool')
    ht['provisioned'] = host['provisioned']
    gates = record.get('gates')
    if not isinstance(gates, dict) or set(gates) != set(DOMAINS):
        raise EvidenceError('all typed gate domains must be supplied explicitly')
    normalized_gates = {d: _domain_state(gates[d], d) for d in DOMAINS}
    artifacts = record.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) > 1024:
        raise EvidenceError('artifacts must be a bounded array')
    normalized_artifacts = []
    for art in artifacts:
        if not isinstance(art, dict) or set(art) - {'role', 'type', 'path', 'sha256', 'signature_state', 'producer'}:
            raise EvidenceError('artifact has unknown fields')
        rel = art.get('path')
        if not isinstance(rel, str) or not rel or len(rel) > 512 or Path(rel).is_absolute() or '..' in Path(rel).parts or '\\' in rel:
            raise EvidenceError('artifact path must be relative and bounded')
        signature = art.get('signature_state')
        if signature not in ('UNKNOWN', 'UNSIGNED', 'SIGNED_UNVERIFIED', 'VERIFIED'):
            raise EvidenceError('artifact signature state is not explicit')
        normalized_artifacts.append({'role': _id(art.get('role'), 'artifact.role'),
            'type': _id(art.get('type'), 'artifact.type'), 'path': rel,
            'sha256': _sha(art.get('sha256'), 'artifact.sha256', 64),
            'signature_state': signature, 'producer': _id(art.get('producer'), 'artifact.producer')})
    tests = record.get('tests')
    if not isinstance(tests, dict) or set(tests) - {'format', 'total', 'failed', 'skipped', 'report_sha256', 'required'}:
        raise EvidenceError('tests are ambiguous')
    fmt = tests.get('format')
    if fmt not in ('junit', 'pytest-junit', 'trx', 'go-json', 'cargo-json', 'js-junit', 'none'):
        raise EvidenceError('unsupported test format')
    numbers = {n: _nonnegative(tests.get(n), 'tests.' + n) for n in ('total','failed','skipped')}
    if numbers['failed'] + numbers['skipped'] > numbers['total']:
        raise EvidenceError('failed + skipped exceeds total')
    if type(tests.get('required')) is not bool:
        raise EvidenceError('tests.required must be boolean')
    digest = tests.get('report_sha256')
    if fmt == 'none':
        if digest not in ('', None) or numbers['total']:
            raise EvidenceError('no report cannot claim test results')
        digest = ''
    else:
        digest = _sha(digest, 'tests.report_sha256', 64)
    if tests['required'] and (fmt == 'none' or numbers['total'] == 0):
        if normalized_gates['test']['state'] == 'PASS':
            raise EvidenceError('zero/missing mandatory tests cannot PASS')
    if numbers['failed'] and normalized_gates['test']['state'] == 'PASS':
        raise EvidenceError('failing test report cannot PASS')
    normalized = {'schema_version': SCHEMA_VERSION, 'kind': 'devtool.typed-evidence',
                  'source': source, 'execution': ex, 'host': ht,
                  'artifacts': normalized_artifacts,
                  'tests': {**numbers, 'format':fmt, 'report_sha256':digest, 'required': tests['required']},
                  'gates': normalized_gates}
    for field, wanted in (expected or {}).items():
        cursor: Any = normalized
        for part in field.split('.'):
            if not isinstance(cursor, dict) or part not in cursor:
                raise EvidenceError('unknown expected identity: ' + field)
            cursor = cursor[part]
        if cursor != wanted:
            raise EvidenceError('source/run/attempt identity mismatch: ' + field)
    return {'schema_version': SCHEMA_VERSION, 'structure_valid': True, 'identity_matched': True,
            'trusted_producer_verified': False, 'reported_gates': normalized_gates,
            'effective_gates': {d: {'state': 'UNKNOWN', 'reason': 'external report is not independently attested', 'authority': ''} for d in DOMAINS},
            'evidence': normalized, 'formal_release_qualified': False}


def _xml_count(path: Path, *, trx: bool) -> tuple[int, int, int]:
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError, UnicodeError) as exc:
        raise EvidenceError('malformed or unreadable test XML: ' + type(exc).__name__) from None
    root = tree.getroot()
    def tag(element: ET.Element) -> str:
        return element.tag.rsplit('}', 1)[-1]
    if trx:
        if tag(root) != 'TestRun':
            raise EvidenceError('TRX root must be TestRun')
        results = [el for el in root.iter() if tag(el) == 'UnitTestResult']
        if not results:
            raise EvidenceError('TRX has no UnitTestResult rows')
        names = set()
        failures = skips = 0
        for el in results:
            key = (el.get('testId'), el.get('executionId'))
            if not all(key) or key in names:
                raise EvidenceError('TRX test identity is missing or duplicated')
            names.add(key)
            outcome = el.get('outcome')
            if outcome in ('Failed', 'Error', 'Timeout', 'Aborted'): failures += 1
            elif outcome in ('NotExecuted', 'Inconclusive', 'NotRunnable', 'Pending'): skips += 1
            elif outcome != 'Passed':
                raise EvidenceError('TRX has unknown outcome')
        return len(results), failures, skips
    if tag(root) not in ('testsuites', 'testsuite'):
        raise EvidenceError('JUnit XML must have testsuite/testsuites root')
    cases = [el for el in root.iter() if tag(el) == 'testcase']
    if not cases:
        raise EvidenceError('JUnit contains zero actual testcases')
    failed = skipped = 0
    for case in cases:
        tags = {tag(c) for c in case}
        if 'failure' in tags or 'error' in tags: failed += 1
        elif 'skipped' in tags: skipped += 1
    return len(cases), failed, skipped


def _json_count(path: Path, *, cargo: bool) -> tuple[int, int, int]:
    total = failed = skipped = 0
    suites = set()
    with path.open('r', encoding='utf-8') as handle:
        for i, line in enumerate(handle):
            if i > MAX_TESTS * 10:
                raise EvidenceError('too many test events')
            if not line.strip(): continue
            try: obj = json.loads(line)
            except json.JSONDecodeError: raise EvidenceError('invalid JSON event') from None
            if not isinstance(obj, dict): raise EvidenceError('invalid JSON event shape')
            if cargo:
                if obj.get('type') != 'test': continue
                event = obj.get('event')
                if event == 'ok': total += 1
                elif event == 'failed': total += 1; failed += 1
                elif event == 'ignored': total += 1; skipped += 1
                elif event in ('started', 'timeout'): continue
                else: raise EvidenceError('unknown Cargo test event')
            else:
                action = obj.get('Action')
                if action in ('pass','fail','skip') and isinstance(obj.get('Test'), str) and obj['Test']:
                    total += 1
                    if action == 'fail': failed += 1
                    if action == 'skip': skipped += 1
                elif action == 'fail' and not obj.get('Test'):
                    raise EvidenceError('Go package-level failure without test result')
                elif action not in ('start', 'run', 'output', 'pause', 'cont', 'pass', 'fail', 'skip'):
                    raise EvidenceError('unknown Go test event')
            if total > MAX_TESTS: raise EvidenceError('too many test cases')
    return total, failed, skipped


def normalize_test_report(path: Path, format: str, *, required: bool = True) -> dict[str, Any]:
    """Parse owned test-report bytes; no green status from process exit alone."""
    if format not in ('junit', 'pytest-junit', 'js-junit', 'trx', 'go-json', 'cargo-json'):
        raise EvidenceError('unsupported report adapter')
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_REPORT:
        raise EvidenceError('test report absent, symlink, empty or oversized')
    data = path.read_bytes()
    try:
        total, failed, skipped = (_xml_count(path, trx=(format == 'trx')) if format in ('junit', 'pytest-junit', 'js-junit', 'trx')
                                  else _json_count(path, cargo=(format == 'cargo-json')))
    except (OSError, UnicodeError, ValueError) as exc:
        raise EvidenceError('cannot parse test report: ' + str(exc)) from None
    if total == 0 and required:
        raise EvidenceError('required report contains no actual completed tests')
    return {'schema_version': 1, 'format': format, 'report_sha256': hashlib.sha256(data).hexdigest(),
            'total': total, 'failed': failed, 'skipped': skipped, 'passed': total - failed - skipped,
            'state': 'FAIL' if failed else ('BLOCKED' if required and not total else 'PASS'),
            'scope': 'local_report_only', 'trusted_producer_verified': False,
            'device_qualified': False, 'release_qualified': False}


def host_eligibility(metadata: Mapping[str, Any], *, runner: str, host_os: str,
                     host_arch: str, toolchains: tuple[str, ...]) -> dict[str, Any]:
    """Explicit allowlist admission, never native/device/release inference."""
    reasons: list[str] = []
    if host_os not in ('linux','windows','macos') or host_arch not in ('x86_64','aarch64'):
        reasons.append('unknown host operating system or architecture')
    for key in ('device_required','root_required','release_signed','device_qualification',
                'signing_required','android_instrumentation','termux_native_required'):
        if metadata.get(key) is True: reasons.append(f'{key} cannot be satisfied by a generic hosted worker')
        if key in metadata and type(metadata[key]) is not bool:
            reasons.append(key + ' must be a boolean declaration')
    specified_os = metadata.get('ci_host_os')
    specified_arch = metadata.get('ci_host_arch')
    if specified_os is not None and specified_os != host_os:
        reasons.append('declared CI OS does not match host')
    if specified_arch is not None and specified_arch != host_arch:
        reasons.append('declared CI architecture does not match host')
    declared = metadata.get('ci_toolchain')
    if declared not in (None, *toolchains):
        reasons.append('CI toolchain not provisioned by host')
    if runner not in ('command','python','go'):
        reasons.append('runner not allowlisted for hosted DAG export')
    return {'schema_version': 1, 'host': {'os':host_os,'arch':host_arch,'toolchains':list(toolchains)},
            'runner':runner, 'state': 'BLOCKED' if reasons else 'PASS', 'eligible':not reasons,
            'reasons':reasons, 'authority':'declarative_static_host_policy',
            'execution_performed':False, 'qualified_release':False}


def graph_host_admission(config: Any, graph: Any, *, host_os: str,
                         host_arch: str, toolchains: tuple[str, ...]) -> dict[str, Any]:
    """Overlay explicit hosted eligibility on *each* resolved native DAG node.

    Does not change local scheduling, and is not itself dispatch authorization;
    exporters must require `all_eligible` before emitting hosted jobs.
    """
    rows = []
    for node in graph.topological_order():
        target = config.targets.get(node.target)
        metadata = dict(target.metadata if target else {})
        runner = node.runner
        if node.ref.kind == 'job':
            if node.ref.resolution_scope == 'target':
                key = node.ref.resolution_local_name or node.ref.name
                job = target.jobs.get(key) if target else None
            else:
                job = config.jobs.get(node.ref.name)
            if job is None:
                raise EvidenceError('DAG node job does not resolve to configured owner')
            runner = job.runner
            metadata.update(job.metadata or {})
        eligibility = host_eligibility(metadata, runner=runner,
                 host_os=host_os, host_arch=host_arch, toolchains=toolchains)
        rows.append({'node':node.key,'target':node.target,'workflow':node.workflow,
                     'runner':runner,'eligibility':eligibility})
    return {'schema_version':1,'kind':'devtool.hosted-dag-eligibility',
            'target':graph.target,'workflow':graph.workflow,
            'nodes':rows,'all_eligible':bool(rows) and all(r['eligibility']['eligible'] for r in rows),
            'execution_performed':False,'release_qualified':False}
