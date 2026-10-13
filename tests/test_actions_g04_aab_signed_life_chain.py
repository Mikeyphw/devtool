"""G4 real LIFE AAB signing: self-signed chains are not broken signatures.

Negative tests use actual OpenJDK tools, no user keystore or credentials.
"""
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from devtool.actions_android import _verify_aab_jar_signature
from devtool.github_actions import GitHubActionsError


@pytest.fixture
def jars(tmp_path):
    if not all(shutil.which(t) for t in ('keytool', 'jarsigner')):
        pytest.skip('OpenJDK signing tools unavailable')
    original = tmp_path / 'unsigned.aab'
    with zipfile.ZipFile(original, 'w') as archive:
        archive.writestr('base/manifest/AndroidManifest.xml', b'manifest')
        archive.writestr('BundleConfig.pb', b'config')
        archive.writestr('base/dex/classes.dex', b'payload')
    keystore = tmp_path / 'test.p12'
    subprocess.run(['keytool', '-genkeypair', '-alias', 'life-test', '-keyalg', 'RSA',
                    '-keysize', '2048', '-validity', '7', '-dname', 'CN=Test Only',
                    '-keystore', str(keystore), '-storetype', 'PKCS12',
                    '-storepass', 'testpass', '-keypass', 'testpass'],
                   capture_output=True, check=True, timeout=45)
    signed = tmp_path / 'signed.aab'
    subprocess.run(['jarsigner', '-keystore', str(keystore), '-storepass', 'testpass',
                    '-keypass', 'testpass', '-signedjar', str(signed), str(original), 'life-test'],
                   capture_output=True, check=True, timeout=45)
    return original, signed


def test_g4_real_self_signed_jar_is_verified_despite_strict_chain_warning(jars):
    unsigned, signed = jars
    strict = subprocess.run(['jarsigner', '-verify', '-strict', str(signed)],
                            capture_output=True, text=True, timeout=45)
    assert strict.returncode != 0, 'Regression fixture must reproduce local LIFE chain warnings'
    assert 'jar verified, with signer errors.' in strict.stdout.lower()
    _verify_aab_jar_signature(signed)


def test_g4_unsigned_jar_is_never_accepted_for_local_signature(jars):
    unsigned, _ = jars
    with pytest.raises(GitHubActionsError, match='missing or ambiguous'):
        _verify_aab_jar_signature(unsigned)


def test_g4_appended_unsigned_entries_are_not_accepted(jars):
    _, signed = jars
    with zipfile.ZipFile(signed, 'a') as archive:
        archive.writestr('base/assets/unverified.txt', b'untrusted data')
    with pytest.raises(GitHubActionsError, match='partial or ambiguous'):
        _verify_aab_jar_signature(signed)


def test_g4_mutated_signed_payload_is_not_accepted(jars, tmp_path):
    _, signed = jars
    damaged = tmp_path / 'damaged.aab'
    damaged.write_bytes(signed.read_bytes())
    with zipfile.ZipFile(damaged, 'a') as archive:
        archive.writestr('base/dex/classes.dex', b'different')
    with pytest.raises(GitHubActionsError):
        _verify_aab_jar_signature(damaged)


def test_g4_signed_aab_verification_keeps_identity_and_bundletool_gates():
    from inspect import getsource
    from devtool.actions_android import _inspect_aab
    from devtool.actions_local_release import local_verify
    from devtool.actions_aab import aab_local_sign

    source = getsource(_inspect_aab)
    assert '_verify_aab_jar_signature(package_file)' in source
    assert "'jar', 'validate'" not in source  # no fake validation gate
    assert 'bundletool validation' in source.lower()
    assert '_fingerprint(cert, source="keytool")' in source
    local = getsource(local_verify)
    assert "profile.expected_cert_sha256.lower()" in local
    assert "details.get('debug_signing') is not False" in local
    sign = getsource(aab_local_sign)
    assert '_imported_bundle(root, registry_id)' in sign
    assert "'qualified_release':False" in sign
