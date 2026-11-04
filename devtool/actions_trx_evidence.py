"""DN03-R6: source/run/graph-bound native .NET TRX verification.

All assertions are local/reviewed-byte assertions. GitHub-uploaded files are
not independently authenticated, and nothing is release qualified.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .actions_rm09_dotnet import DotNetError, merge_trx
from .evidence_kernel import EvidenceError, normalize_test_report


class TrxEvidenceError(ValueError):
    """A reviewed .NET test node has no trustworthy, parseable report bytes."""


def verified_native_trx(staging: Path | None, graph: Mapping[str, Any],
                        job: Mapping[str, Any], *, source: str,
                        run_id: int, attempt: int,
                        artifacts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Reparse an RM-06 handoff after verifying its source/run/attempt/byte digest.

    Non-test nodes retain test=NOT_RUN.  A .NET test node MUST have exactly one
    declared TRX and a staged handoff; an absent or failing report is fatal.
    The returned digest is exactly the one bound to typed node output bytes.
    """
    if job.get('provider') != 'dotnet' or job.get('dotnet_command') != 'test':
        return None
    paths = job.get('outputs')
    if not isinstance(paths, list) or len(set(paths)) != len(paths):
        raise TrxEvidenceError('R6 declared native outputs are ambiguous')
    trx = [p for p in paths if isinstance(p, str) and p.endswith('.trx')]
    if len(trx) != 1 or not trx[0].startswith('dist/'):
        raise TrxEvidenceError('R6 native .NET test needs exactly one owned dist TRX')
    expected_project = job.get('dotnet_selected')
    # RM-09 allows a statically reviewed project or solution.  All.sln is a
    # real native test entry point (tests/test_rm09_dotnet.py), not an unsafe
    # unbound dependency.  Keep expression, traversal and dynamic path checks.
    if (not isinstance(expected_project, str) or len(expected_project) > 220
            or not re.fullmatch(r'[A-Za-z0-9_.+/-]+', expected_project)
            or expected_project.startswith('/')
            or any(part in ('', '.', '..') for part in expected_project.split('/'))
            or not expected_project.endswith(('.csproj', '.sln', '.slnx'))):
        raise TrxEvidenceError('R6 native .NET test must bind a reviewed project or solution')
    if staging is None or not isinstance(staging, Path) or staging.is_symlink():
        raise TrxEvidenceError('R6 requires original native output staging for test verification')

    # RM-06 validates the manifest, source commit, graph, workflow, run,
    # attempt, relative names, SHA-256 and actual bytes before any XML parse.
    from .actions_graph_export import _load_manifest
    try:
        checked = _load_manifest(staging / job['id'], graph=graph, parent=job,
                                 source=source, run=str(run_id), attempt=attempt)
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise TrxEvidenceError('R6 native handoff identity/bytes refused: ' + str(exc)[:180]) from exc
    candidates = [p for name, p in checked if name == trx[0]]
    if len(candidates) != 1:
        raise TrxEvidenceError('R6 required TRX absent or duplicated in native handoff')
    path = candidates[0]
    # Verify the exact bytes are the typed artifact bytes, rather than a
    # secondary test report found elsewhere in a runner workspace.
    matches = [a for a in artifacts if a.get('path') == trx[0]]
    digest = sha256(path.read_bytes()).hexdigest() if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8_000_000 else ''
    if len(matches) != 1 or matches[0].get('sha256') != digest or not digest:
        raise TrxEvidenceError('R6 TRX digest differs from the frozen typed artifact')
    try:
        # RM-09 also validates TRX Counters against distinct test IDs and
        # refuses failed, skipped, empty or duplicate native test results.
        merged = merge_trx(staging / job['id'] / 'payload', trx, expected_project=expected_project)
        normalized = normalize_test_report(path, 'trx', required=True)
    except (DotNetError, EvidenceError, ValueError, OSError, TypeError) as exc:
        raise TrxEvidenceError('R6 native TRX was not independently accepted: ' + str(exc)[:180]) from exc
    total = normalized['total']
    if (total <= 0 or normalized['failed'] or normalized['skipped'] or
            normalized['passed'] != total or normalized['state'] != 'PASS' or
            merged.get('tests') != total or merged.get('passed') != total or
            normalized['report_sha256'] != digest):
        raise TrxEvidenceError('R6 TRX test counters or report digest disagree')
    # Detect a concurrent rewrite between independent parser reads.
    if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != digest:
        raise TrxEvidenceError('R6 TRX bytes changed during independent report parsing')
    coverage = None
    if job.get('dotnet_coverage') is True:
        coverage_rel = 'dist/coverage.cobertura.xml'
        covered = [p for name, p in checked if name == coverage_rel]
        typed = [a for a in artifacts if a.get('path') == coverage_rel]
        if len(covered) != 1 or len(typed) != 1:
            raise TrxEvidenceError('R6 requested Cobertura coverage is missing from owned outputs')
        coverage_file = covered[0]
        if (coverage_file.is_symlink() or not coverage_file.is_file() or
                not 0 < coverage_file.stat().st_size <= 8_000_000):
            raise TrxEvidenceError('R6 native coverage XML missing, symlinked or oversized')
        data = coverage_file.read_bytes()
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise TrxEvidenceError('R6 native Cobertura contains unsafe XML declarations')
        try:
            xml = ET.fromstring(data)
            if xml.tag != 'coverage':
                raise ValueError('invalid Cobertura root')
            rate = float(xml.attrib['line-rate'])
            classes = list(xml.iter('class'))
        except (ET.ParseError, KeyError, ValueError, OverflowError) as exc:
            raise TrxEvidenceError('R6 cannot independently parse coverage report') from exc
        if not 0 <= rate <= 1 or not classes:
            raise TrxEvidenceError('R6 native Cobertura has invalid rate or no classes')
        coverage_digest = sha256(data).hexdigest()
        if typed[0].get('sha256') != coverage_digest or sha256(coverage_file.read_bytes()).hexdigest() != coverage_digest:
            raise TrxEvidenceError('R6 coverage digest differs from frozen native artifact')
        coverage = {'path': coverage_rel, 'sha256': coverage_digest,
                    'line_rate': rate, 'class_count': len(classes)}
    return {'path': trx[0], 'sha256': digest, 'passed': total,
            'tests': {'format': 'trx', 'total': total, 'failed': 0,
                      'skipped': 0, 'report_sha256': digest, 'required': True},
            'coverage': coverage,
            'authority': 'local_staged_trx_reparsed',
            'remote_authenticated': False, 'release_qualified': False}
