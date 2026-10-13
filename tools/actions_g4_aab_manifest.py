#!/usr/bin/env python3
"""Generate G4 source-bound AAB and test/lint receipts after Gradle completes."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
from devtool.actions_aab import emit_aab_manifest
from devtool.github_actions import GitHubActionsError
import os


def aggregate_tests(source: Path, output: Path):
    if not source.is_dir() or source.is_symlink():
        raise GitHubActionsError('Android unit-test reports not produced')
    files = sorted(source.glob('TEST-*.xml'))
    if not files or len(files) > 256:
        raise GitHubActionsError('Android unit-test receipt set missing or excessive')
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    for path in files:
        if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
            raise GitHubActionsError('Android unit-test XML path invalid')
        data = path.read_bytes()
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            raise GitHubActionsError('Android test XML has external declarations')
        try: parsed = ET.fromstring(data)
        except ET.ParseError: raise GitHubActionsError('Android test XML invalid') from None
        if parsed.tag != 'testsuite':
            raise GitHubActionsError('Android test XML suite element invalid')
        for key in counts:
            value = parsed.get(key, '0')
            if not value.isdecimal(): raise GitHubActionsError('Android test suite has invalid counts')
            counts[key] += int(value)
        if counts['tests'] > 200000: raise GitHubActionsError('Android test count exceeds receipt bound')
    counts['gradle_task'] = ':app:testDebugUnitTest'
    output.write_text(json.dumps(counts, sort_keys=True, indent=2) + '\n')


def main(argv):
    if len(argv) != 6 or argv[1] != 'emit':
        raise GitHubActionsError('Usage: actions_g4_aab_manifest.py emit BUNDLE TEST_XML_DIR LINT_XML STAGING')
    _, _, bundle, reports, lint, stage = argv
    dest = Path(stage)
    if not dest.is_dir() or dest.is_symlink():
        raise GitHubActionsError('AAB stage directory invalid')
    from shutil import copyfile
    copyfile(bundle, dest/'unsigned-release.aab')
    copyfile(lint, dest/'lint-results-release.xml')
    aggregate_tests(Path(reports), dest/'unit-tests.json')
    emit_aab_manifest(dest/'unsigned-release.aab', dest/'unit-tests.json',
                      dest/'lint-results-release.xml', dest/'manifest.json', os.environ)

if __name__ == '__main__':
    try: main(sys.argv)
    except (GitHubActionsError, OSError, ValueError, KeyError) as exc:
        print('AAB candidate evidence generation failed:', str(exc), file=sys.stderr)
        sys.exit(1)
