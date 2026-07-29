from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JAVA = ROOT / "android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud"
MANIFEST = ROOT / "android/devtool-live-hud/app/src/main/AndroidManifest.xml"


def source(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_support_bundle_redaction_self_test_avoids_literal_auth_leak_lint_bait() -> None:
    text = source("SupportBundle.java")
    assert "redactionSelfTest" in text
    assert "bearerValue = \"Bearer \" + \"abcdef\"" in text
    assert "credentialPair = \"user\" + \":\" + \"pass\"" in text
    assert "secretValue = \"hunter\" + \"2\"" in text
    assert "privateKeyMarker = \"BEGIN \" + \"PRIVATE KEY\"" in text
    forbidden_literals = (
        "Authorization: Bearer abcdef",
        "https://user:pass@example.invalid/repo.git",
        "password=hunter2",
        "-----BEGIN PRIVATE KEY-----",
    )
    for literal in forbidden_literals:
        assert literal not in text


def test_support_bundle_redaction_self_test_still_checks_all_secret_families() -> None:
    text = source("SupportBundle.java")
    for key in (
        '"bridge_token_redacted"',
        '"auth_header_redacted"',
        '"url_credentials_redacted"',
        '"secret_assignment_redacted"',
        '"private_key_redacted"',
        '"sample_output"',
    ):
        assert key in text
    assert "SupportRedactor.redact(sample, token)" in text
    assert "!redacted.contains(bearerValue)" in text
    assert "!redacted.contains(credentialPair)" in text
    assert "!redacted.contains(secretValue)" in text
    assert "!redacted.contains(privateKeyMarker)" in text


def test_exported_receivers_are_documented_and_lint_suppressed_because_runtime_auth_is_required() -> None:
    text = MANIFEST.read_text(encoding="utf-8")
    assert 'xmlns:tools="http://schemas.android.com/tools"' in text
    assert "runtime config authorization is enforced in TaskerPluginReceiver" in text
    assert "BridgeReceiver rejects invalid tokens and unexpected actions" in text
    assert text.count('tools:ignore="ExportedReceiver"') == 2
    tasker_block = text[text.index('android:name=".TaskerPluginReceiver"') : text.index('</receiver>', text.index('android:name=".TaskerPluginReceiver"'))]
    bridge_block = text[text.index('android:name=".BridgeReceiver"') : text.index('</receiver>', text.index('android:name=".BridgeReceiver"'))]
    assert 'android:exported="true"' in tasker_block
    assert 'tools:ignore="ExportedReceiver"' in tasker_block
    assert 'android:exported="true"' in bridge_block
    assert 'tools:ignore="ExportedReceiver"' in bridge_block
