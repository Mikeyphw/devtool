"""Real unsigned-JAR verification must accept jarsigner's documented success code."""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

from devtool.actions_aab import APP, _unsigned_bundle
from devtool.github_actions import GitHubActionsError


def _candidate(tmp_path: Path, *, signature=False) -> Path:
    bundle = tmp_path / 'candidate.aab'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('BundleConfig.pb', b'fake protobuf')
        archive.writestr('base/manifest/AndroidManifest.xml', b'fake protobuf')
        archive.writestr('base/dex/classes.dex', b'fake dex')
        if signature:
            archive.writestr('META-INF/SIGNER.SF', b'fake signature')
    return bundle


def _bundletool_stub(tmp_path, monkeypatch):
    jar = tmp_path / 'bundletool.jar'
    jar.write_bytes(b'fake')
    monkeypatch.setenv('DEVTOOL_BUNDLETOOL_JAR', str(jar))
    def run(cmd, purpose, timeout=45):
        if cmd[-1] == '/manifest/@package':
            return APP
        if cmd[-1] == '/manifest/@android:versionCode':
            return '55'
        return ''
    monkeypatch.setattr('devtool.actions_aab._run', run)


def test_real_jarsigner_returns_success_for_unsigned_archive(tmp_path, monkeypatch):
    """Reproduce run 38445139596's misleading success exit on ordinary unsigned ZIP."""
    if not shutil.which('jarsigner'):
        pytest.skip('JDK jarsigner unavailable on this host')
    aab = _candidate(tmp_path)
    raw = subprocess.run(['jarsigner', '-verify', '-strict', str(aab)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         check=False, timeout=45)
    assert raw.returncode == 0
    assert b'jar is unsigned.' in (raw.stdout + raw.stderr).lower()
    _bundletool_stub(tmp_path, monkeypatch)
    result = _unsigned_bundle(aab)
    assert result['signing_state'] == 'unsigned'
    assert result['signature_verified'] is False
    assert result['qualified_release'] is False
    assert result['package'] == APP
    assert result['version_code'] == 55


@pytest.mark.parametrize(('code', 'output'), [
    (0, b'jar verified.\n'),
    (0, b''),
    (0, b'no manifest.\n'),
    (16, b'jar is unsigned.\n'),
    (1, b'jarsigner: java.util.zip.ZipException\n'),
    (0, b'jar is unsigned.\njar verified.\n'),
    (0, b'jar is unsigned.\njar is unsigned.\n'),
])
def test_unsigned_status_fail_closed_on_ambiguous_or_bad_tool_result(tmp_path, monkeypatch, code, output):
    _bundletool_stub(tmp_path, monkeypatch)
    monkeypatch.setattr('devtool.actions_aab.subprocess.run', lambda *a, **k:
                        SimpleNamespace(returncode=code, stdout=output, stderr=b''))
    with pytest.raises(GitHubActionsError, match='not demonstrably unsigned'):
        _unsigned_bundle(_candidate(tmp_path))


def test_unsigned_marker_with_success_does_not_override_zip_signature_rejection(tmp_path, monkeypatch):
    _bundletool_stub(tmp_path, monkeypatch)
    monkeypatch.setattr('devtool.actions_aab.subprocess.run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=b'jar is unsigned.\n', stderr=b''))
    with pytest.raises(GitHubActionsError, match='contains a JAR signature'):
        _unsigned_bundle(_candidate(tmp_path, signature=True))


def test_explicit_unsigned_status_allows_only_unqualified_import(tmp_path, monkeypatch):
    _bundletool_stub(tmp_path, monkeypatch)
    monkeypatch.setattr('devtool.actions_aab.subprocess.run', lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout=b'no manifest.\njar is unsigned.\n', stderr=b''))
    status = _unsigned_bundle(_candidate(tmp_path))
    assert status['qualified_release'] is False
    assert status['signature_verified'] is False
    assert status['signing_state'] == 'unsigned'
