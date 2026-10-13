"""ACTIONS-10 V6 signing verification compatibility and strict rejection."""
import re
import pytest
from pathlib import Path
from devtool.actions_android import _fingerprint, _inspect_apk
from devtool.github_actions import GitHubActionsError

CERT = "5601d49884487a5d8f378b7665e37fff8b6808e7a1a038a465de1d07c9a5ef8b"

@pytest.mark.parametrize("value", [
    f"Signer #1 certificate SHA-256 digest: {CERT}",
    f"Signer #1 certificate SHA256 digest: {CERT.upper()}",
    "Signer #1 certificate SHA-256 digest: " + ":".join(CERT[i:i+2] for i in range(0,64,2)),
    "Signer #1 certificate SHA-256 fingerprint: " + " ".join(CERT[i:i+2] for i in range(0,64,2)),
    f"certificate SHA-256: {CERT}",
])
def test_apk_certificate_supported_public_formats(value):
    assert _fingerprint("Verifies\n"+value+"\n", source="apksigner") == CERT

@pytest.mark.parametrize("value", [
    "SHA256: " + ":".join(CERT[i:i+2] for i in range(0,64,2)),
    f"SHA-256: {CERT}",
])
def test_keytool_certificate_supported_public_formats(value):
    assert _fingerprint(value, source="keytool") == CERT

@pytest.mark.parametrize("value,reason", [
    ("Signer #1 certificate SHA-256 digest: " + CERT + "ff", "malformed-sha256"),
    ("Signer #1 certificate SHA-256 digest: " + CERT[:-2], "malformed-sha256"),
    ("Signer #1 certificate SHA-256 digest: not-available", "malformed-sha256"),
    ("Signer #1 certificate SHA-1 digest: " + CERT, "missing-sha256-field"),
    ("Signer #2 certificate SHA-256 digest: " + CERT, "multiple-signers"),
    ("Signer #1 certificate SHA-256 digest: " + CERT + "\nSigner #2 certificate SHA-1 digest: aa", "multiple-signers"),
    ("Signer #1 certificate SHA-256 digest: " + CERT + "\nSigner #1 certificate SHA-256 digest: " + "a"*64, "conflicting-digests"),
    ("warning: SHA256: " + CERT, "missing-sha256-field"),
])
def test_untrusted_or_ambiguous_signing_output_is_rejected(value, reason):
    with pytest.raises(GitHubActionsError, match=reason):
        _fingerprint(value, source="apksigner")

def test_does_not_expose_untrusted_tool_output():
    private = "DO_NOT_ECHO_SECRET_MATERIAL"
    with pytest.raises(GitHubActionsError) as exc:
        _fingerprint(f"Signer #1 certificate SHA256 digest: {private}")
    assert private not in str(exc.value)

def test_apk_inspector_needs_verified_tools_and_keeps_certificate(monkeypatch, tmp_path):
    apk = tmp_path / "signed.apk"
    apk.write_bytes(b"representative signed artifact - tool calls mocked")
    def tool(name): return name
    def run(argv, *, purpose, **kwargs):
        if argv[0] == "apksigner":
            assert argv[1:3] == ["verify", "--print-certs"]
            return "Verifies\nSigner #1 certificate SHA-256 digest: " + ":".join(CERT[i:i+2] for i in range(0,64,2))
        return "package: name='com.example.app' versionCode='123' versionName='1.0'\n"
    monkeypatch.setattr("devtool.actions_android._tool", tool)
    monkeypatch.setattr("devtool.actions_android._run", run)
    details = _inspect_apk(apk)
    assert details["signer_sha256"] == CERT
    assert details["signature_verified"] and details["manifest_verified"]
