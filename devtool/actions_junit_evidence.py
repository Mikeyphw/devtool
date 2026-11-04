"""Source-reviewed, local JUnit byte evidence for native Devtool DAG nodes.

This is a *local report verification*, never remote provenance, signing, device
qualification or proof that a runner executed the assertions. Source-owned job
metadata opts in to an exact relative report directory. Every file is parsed
from bytes by the existing evidence kernel and bound to its absolute digest.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

from .evidence_kernel import EvidenceError, normalize_test_report


class JUnitEvidenceError(ValueError):
    pass


_PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,95}\Z')
_XML = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.$-]{0,180}\.xml\Z')
_MAX_FILES = 192
_MAX_TOTAL = 250_000
_MAX_GROUP_BYTES = 48 * 1024 * 1024
_ALLOWED_PROVIDERS = frozenset({'android-gradle', 'python-shell', 'jvm', 'node'})


def reviewed_junit_directory(metadata: Mapping[str, Any], provider: str) -> str:
    """Validate a literal, deterministic, target-owned JUnit output directory."""
    path = metadata.get('ci_junit_report_dir', '')
    if path == '':
        return ''
    if (provider not in _ALLOWED_PROVIDERS or not isinstance(path, str) or
            len(path) > 240 or path.startswith('/') or '\\' in path):
        raise JUnitEvidenceError('SFDT08 unreviewed provider or JUnit directory')
    parts = path.split('/')
    if (not 3 <= len(parts) <= 8 or any(not _PART.fullmatch(x) or x in ('.','..') for x in parts)
            or not any(parts[i:i+2] == ['build','test-results']
                       for i in range(len(parts)-1))):
        raise JUnitEvidenceError('SFDT08 JUnit path must be a bounded build/test-results directory')
    if metadata.get('ci_junit_required', True) is not True:
        raise JUnitEvidenceError('SFDT08 verified JUnit opt-in must be required')
    return path


def _directory(root: Path, relative: str) -> Path:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise JUnitEvidenceError('SFDT08 JUnit root must be a directory')
    reviewed_junit_directory({'ci_junit_report_dir':relative}, 'python-shell')
    current = root
    for part in relative.split('/'):
        current = current / part
        if current.is_symlink():
            raise JUnitEvidenceError('SFDT08 symlinked JUnit path refused')
    if not current.resolve(strict=False).is_relative_to(root):
        raise JUnitEvidenceError('SFDT08 JUnit directory escapes repository')
    return current


def require_fresh_junit_directory(root: Path, relative: str) -> None:
    """A pre-existing report directory could turn a stale XML into fake success."""
    directory = _directory(root, relative)
    if directory.exists() or directory.is_symlink():
        raise JUnitEvidenceError('SFDT08 stale or pre-existing JUnit report directory')


def _verify_suite_counts(path: Path) -> None:
    """Do not accept XML headers that contradict actual testcase children."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError, UnicodeError) as exc:
        raise JUnitEvidenceError('SFDT08 unreadable JUnit XML') from exc
    root = tree.getroot()
    def tag(node):
        return node.tag.rsplit('}',1)[-1]
    suites = [e for e in root.iter() if tag(e)=='testsuite']
    if not suites:
        raise JUnitEvidenceError('SFDT08 JUnit contains no testsuite')
    for suite in suites:
        cases = [e for e in suite.iter() if tag(e)=='testcase']
        count = len(cases)
        failed = sum(any(tag(c) in ('failure','error') for c in case) for case in cases)
        skipped = sum(any(tag(c)=='skipped' for c in case) for case in cases)
        for name, actual in (('tests', count),('failures',failed),('skipped',skipped)):
            val = suite.get(name)
            if val is not None and (not val.isdecimal() or int(val) != actual):
                raise JUnitEvidenceError('SFDT08 JUnit suite header disagrees with testcase rows: '+name)
        errors = suite.get('errors')
        if errors is not None and (not errors.isdecimal() or int(errors)>failed):
            raise JUnitEvidenceError('SFDT08 contradictory JUnit errors header')


def scan_junit_bundle(root: Path, relative: str) -> dict[str, Any]:
    """Independently parse a fresh native result folder; deterministic group SHA."""
    directory = _directory(root, relative)
    if not directory.is_dir():
        raise JUnitEvidenceError('SFDT08 required JUnit report directory missing')
    names = sorted(directory.iterdir(),key=lambda p:p.name)
    if len(names) > _MAX_FILES + 3:
        raise JUnitEvidenceError('SFDT08 too many JUnit members')
    xml = []
    for item in names:
        if item.is_symlink():
            raise JUnitEvidenceError('SFDT08 symlinked JUnit report member')
        if item.is_file() and item.suffix == '.xml':
            if not _XML.fullmatch(item.name):
                raise JUnitEvidenceError('SFDT08 unsafe JUnit XML filename')
            xml.append(item)
        elif item.is_dir() and item.name == 'binary':
            # Gradle owns this auxiliary binary report. Never use it as evidence.
            if item.is_symlink():
                raise JUnitEvidenceError('SFDT08 symlinked Gradle binary report')
        else:
            raise JUnitEvidenceError('SFDT08 unreviewed file inside JUnit report directory')
    if not 1 <= len(xml) <= _MAX_FILES:
        raise JUnitEvidenceError('SFDT08 JUnit report set must contain 1..192 XML files')
    files=[]
    total=failed=skipped=aggregate_bytes=0
    for f in xml:
        size=f.stat().st_size
        aggregate_bytes += size
        if aggregate_bytes > _MAX_GROUP_BYTES:
            raise JUnitEvidenceError('SFDT08 JUnit bundle exceeds byte cap')
        try:
            parsed = normalize_test_report(f, 'junit', required=True)
        except EvidenceError as exc:
            raise JUnitEvidenceError('SFDT08 JUnit normalization failed: '+str(exc)) from None
        _verify_suite_counts(f)
        total += parsed['total']; failed += parsed['failed']; skipped += parsed['skipped']
        if total > _MAX_TOTAL:
            raise JUnitEvidenceError('SFDT08 JUnit case cap exceeded')
        files.append({'path': relative+'/'+f.name,'size':size,
                      'sha256':parsed['report_sha256'], 'total':parsed['total'],
                      'failed':parsed['failed'],'skipped':parsed['skipped']})
    if failed or total-skipped < 1:
        raise JUnitEvidenceError('SFDT08 failing or entirely skipped JUnit bundle cannot PASS')
    checksum=hashlib.sha256(json.dumps(files,sort_keys=True,separators=(',',':')).encode('ascii')).hexdigest()
    return {'tests':{'format':'junit','total':total,'failed':failed,'skipped':skipped,
                     'report_sha256':checksum,'required':True},
            'files': files,'local_verified':True,
            'authenticated_remote':False,'release_qualified':False}
