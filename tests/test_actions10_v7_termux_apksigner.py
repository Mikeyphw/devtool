"""ACTIONS-10 V7: fail-closed signing certificate verification on Termux apksigner 0.9."""

import pytest

from devtool.actions_android import _fingerprint, _inspect_apk
from devtool.github_actions import GitHubActionsError

CERT = "5601d49884487a5d8f378b7665e37fff8b6808e7a1a038a465de1d07c9a5ef8b"
OTHER = "b" * 64


def test_termux_apksigner_09_actual_field_prefix():
    output = ("V3.0 Signer: certificate DN: CN=FDroid, OU=FDroid\n"
              f"V3.0 Signer: certificate SHA-256 digest: {CERT}\n"
              "V3.0 Signer: certificate SHA-1 digest: " + "c" * 40 + "\n"
              "V3.0 Signer: certificate MD5 digest: " + "c" * 32)
    assert _fingerprint(output) == CERT


@pytest.mark.parametrize("label", [
    "V1 Signer", "V2.0 Signer", "V3.0 Signer", "V3.1 Signer",
    "V3.2 Signer", "V4.0 Signer", "V3.0 Signer #1",
])
def test_versioned_signer_sha256_label(label):
    assert _fingerprint(f"{label}: certificate SHA-256 digest: {CERT.upper()}") == CERT


def test_cross_scheme_matching_certificates_are_accepted():
    output = ("Number of signers: 1\n"
              f"V2.0 Signer: certificate SHA-256 digest: {CERT}\n"
              f"V3.0 Signer: certificate SHA-256 digest: {CERT}\n")
    assert _fingerprint(output) == CERT


@pytest.mark.parametrize("output,reason", [
    ("V3.0 Signer: certificate SHA-1 digest: " + CERT, "missing-sha256-field"),
    ("V3.0 Signer: certificate SHA-256 digest: " + CERT[:-2], "malformed-sha256"),
    ("V3.0 Signer: certificate SHA-256 digest: " + CERT + "FF", "malformed-sha256"),
    ("V3.0 Signer: certificate SHA-256 digest: absent", "malformed-sha256"),
    (f"V2.0 Signer: certificate SHA-256 digest: {CERT}\nV3.0 Signer: certificate SHA-256 digest: {OTHER}", "conflicting-digests"),
    (f"V3.0 Signer #2: certificate SHA-256 digest: {CERT}", "multiple-signers"),
    (f"V3.0 Signer #2: certificate DN: CN=FDroid\nV3.0 Signer: certificate SHA-256 digest: {CERT}", "multiple-signers"),
    (f"Signer #2 certificate DN: CN=FDroid\nV3.0 Signer: certificate SHA-256 digest: {CERT}", "multiple-signers"),
    (f"Number of signers: 2\nV3.0 Signer: certificate SHA-256 digest: {CERT}", "multiple-signers"),
    (f"warning: V3.0 Signer: certificate SHA-256 digest: {CERT}", "missing-sha256-field"),
    (f"V3.0 Signer: non-certificate SHA-256 digest: {CERT}", "missing-sha256-field"),
    (f"V3.0 Signer: certificate SHA-256 digest: {CERT}\nV3.0 Signer: certificate SHA-256 digest: {OTHER}", "conflicting-digests"),
])
def test_termux_output_ambiguous_or_invalid_is_rejected(output, reason):
    with pytest.raises(GitHubActionsError, match=reason):
        _fingerprint(output)


def test_tool_output_not_disclosed_on_invalid_certificate():
    secret = "SECRET_NOT_TO_BE_ECHOED"
    with pytest.raises(GitHubActionsError) as exc:
        _fingerprint("V3.0 Signer: certificate SHA-256 digest: " + secret)
    assert secret not in str(exc.value)


def test_independent_android_apk_inspection_accepts_termux_format(monkeypatch, tmp_path):
    apk = tmp_path / "signed.apk"
    apk.write_bytes(b"test fixture: subprocesses mocked")
    calls = []

    def tool(name):
        return name

    def run(args, *, purpose, **kwargs):
        calls.append(args[:])
        if args[0] == "apksigner":
            assert args[1:3] == ["verify", "--print-certs"]
            return "V3.0 Signer: certificate SHA-256 digest: " + CERT
        return "package: name='com.example.app' versionCode='123' versionName='1.0'\n"

    monkeypatch.setattr("devtool.actions_android._tool", tool)
    monkeypatch.setattr("devtool.actions_android._run", run)
    result = _inspect_apk(apk)
    assert result["signer_sha256"] == CERT
    assert result["signature_verified"] is True
    assert result["manifest_verified"] is True
    assert len(calls) == 2
