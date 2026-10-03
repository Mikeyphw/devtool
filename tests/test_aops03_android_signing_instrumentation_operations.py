from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from devtool.android._standalone import BuildError, filter_unavailable_connected_android_tests
from devtool.android.capabilities import AndroidExecutionContext, AndroidToolResolution
from devtool.android.device_ops import AndroidDeviceOperations
from devtool.android.devices import AndroidDevice, AndroidDeviceManager
from devtool.android.instrumentation import normalize_instrumentation_policy
from devtool.android.signing import (
    AndroidSigningManager,
    SigningProfile,
    signing_environment_for_execution,
    signing_gradle_args_for_tasks,
    signing_plan_metadata,
    stage_gradle_signing_init_script,
    task_requires_release_signing,
    tasks_require_release_signing,
)
from devtool.android_integration import android_builder_arguments_for_tasks
from devtool.artifact_trust import SecretProviderRef, SigningIdentityId, SigningIdentityRecord, SigningIdentityRegistry
from devtool.cli import build_parser
from devtool.models import CommandSpec, ProjectConfig
from devtool.termux_toolchains import RuntimeIdentity


def _config(root: Path, android: dict, *, env: dict[str, str] | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind="android",
        runner="android",
        project_root=str(root),
        selected_target="app",
        variant="release",
        runner_options={"android": android, "gradle": {"structured_events": False}},
        env=dict(env or {}),
    )


def _register_android_signing_identity(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    keystore: Path,
    *,
    secret_kind: str = "local",
    secret_reference: str = "APP_STORE",
    identity_id: str = "android-release",
) -> str:
    registry = root / "signing-identities.json"
    monkeypatch.setenv("DEVTOOL_SIGNING_IDENTITY_REGISTRY", str(registry))
    SigningIdentityRegistry(registry).register(SigningIdentityRecord(
        identity_id=SigningIdentityId(identity_id),
        provider="android-pkcs12",
        key_reference=str(keystore),
        alias="release",
        certificate_sha256="a" * 64,
        purposes=("android-signing",),
        variants=("release",),
        secret_provider=SecretProviderRef(secret_kind, secret_reference),
    ))
    return identity_id



def _gradle_project(root: Path) -> None:
    (root / "settings.gradle").write_text("rootProject.name='android-g1-fixture'\n", encoding="utf-8")
    (root / "build.gradle").write_text("tasks.register('noop')\n", encoding="utf-8")

def _context(*, adb: str = "/fake/adb", apksigner: str = "/fake/apksigner") -> AndroidExecutionContext:
    runtime = RuntimeIdentity("linux", "host", "x86_64", "glibc")
    tools = {
        "adb": AndroidToolResolution("adb", "path", True, path=adb, compatible=True),
        "apksigner": AndroidToolResolution("apksigner", "sdk", True, path=apksigner, compatible=True),
        "java": AndroidToolResolution("java", "system", True, path="/fake/java", compatible=True),
    }
    return AndroidExecutionContext(runtime, ".", "app", "release", "", "", tools, "path")


def test_release_signing_is_task_scoped() -> None:
    assert task_requires_release_signing(":app:assembleRelease")
    assert task_requires_release_signing(":app:bundleRelease")
    assert task_requires_release_signing(":app:publishReleaseBundle")
    assert not task_requires_release_signing(":app:testReleaseUnitTest")
    assert not task_requires_release_signing(":app:lintRelease")
    assert not task_requires_release_signing(":app:assembleDebug")
    assert tasks_require_release_signing([":app:testDebugUnitTest", ":app:assembleRelease"])


def test_devtool_signing_init_script_never_persists_password_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", str(runtime))
    config = _config(tmp_path, {
        "signing": {"release": {
            "mode": "devtool", "policy": "release-required",
            "identity_id": "android-release", "key_password_env": "APP_KEY",
        }}
    })
    env = {"APP_STORE": "super-secret-store", "APP_KEY": "super-secret-key"}
    script = stage_gradle_signing_init_script(config, [":app:assembleRelease"], env=env)
    assert script is not None and script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "super-secret-store" not in text
    assert "super-secret-key" not in text
    assert "DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD" in text
    assert "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD" in text
    assert str(keystore.resolve()) in text
    assert "release" in text


def test_builder_only_injects_signing_for_release_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    monkeypatch.setenv("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", str(tmp_path / "runtime"))
    config = _config(tmp_path, {
        "signing": {"release": {
            "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
            "key_password_env": "APP_KEY",
        }}
    })
    monkeypatch.setenv("APP_STORE", "one")
    monkeypatch.setenv("APP_KEY", "two")
    debug = android_builder_arguments_for_tasks(config, [":app:assembleDebug"], scan_artifacts=False)
    assert not any("signing" in item and item.endswith(".gradle") for item in debug)
    release = android_builder_arguments_for_tasks(config, [":app:assembleRelease"], scan_artifacts=False)
    assert any(item.endswith(".init.gradle") for item in release)


def test_missing_release_secret_fails_before_gradle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore, secret_reference="MISSING_STORE")
    monkeypatch.setenv("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", str(tmp_path / "runtime"))
    config = _config(tmp_path, {
        "signing": {"release": {
            "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
            "key_password_env": "MISSING_KEY",
        }}
    })
    with pytest.raises(Exception, match="MISSING_STORE"):
        signing_gradle_args_for_tasks(config, [":app:assembleRelease"], env={})
    assert signing_gradle_args_for_tasks(config, [":app:assembleDebug"], env={}) == []


def test_signing_verifier_extracts_and_compares_apk_certificate_sha256(tmp_path: Path) -> None:
    apk = tmp_path / "app-release.apk"
    apk.write_bytes(b"apk")
    manager = AndroidSigningManager(_context())
    output = "Signer #1 certificate DN: CN=Example\nSigner #1 certificate SHA-256 digest: AA:BB:CC\n"
    completed = mock.Mock(returncode=0, stdout=output, stderr="")
    with mock.patch("devtool.android.signing.subprocess.run", return_value=completed):
        result = manager.verify_artifact(apk, expected_sha256="aabbcc")
    assert result.ok
    assert result.identity is not None and result.identity.sha256 == "aabbcc"


class _OneDeviceManager(AndroidDeviceManager):
    def __init__(self) -> None:
        super().__init__(AndroidToolResolution("adb", "path", True, path="/fake/adb", compatible=True), env={})

    def list_devices(self, *, enrich: bool = False):
        return [AndroidDevice("serial-1", "device", model="Pixel")]


def test_device_operations_use_canonical_selected_adb_transport(tmp_path: Path) -> None:
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    operations = AndroidDeviceOperations(_OneDeviceManager())
    completed = mock.Mock(returncode=0, stdout="Success\n", stderr="")
    with mock.patch("devtool.android.device_ops.subprocess.run", return_value=completed) as run:
        result = operations.install(apk)
    assert result.ok and result.serial == "serial-1"
    argv = run.call_args.args[0]
    assert argv[:3] == ["/fake/adb", "-s", "serial-1"]
    assert argv[3:6] == ["install", "-r", "-t"]


def test_device_test_policy_required_fails_and_conditional_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "devtool.android._standalone.connected_android_devices",
        lambda *args, **kwargs: ([], "/sdk/adb", {"serial-1": "unauthorized"}),
    )
    with pytest.raises(BuildError, match="required"):
        filter_unavailable_connected_android_tests(
            [":app:connectedDebugAndroidTest"], sdk_root=None, device_test_policy="required"
        )
    retained, skipped, adb, states = filter_unavailable_connected_android_tests(
        [":app:assembleDebug", ":app:connectedDebugAndroidTest"], sdk_root=None, device_test_policy="when-device-present"
    )
    assert retained == [":app:assembleDebug"]
    assert skipped == [":app:connectedDebugAndroidTest"]
    assert states == {"serial-1": "unauthorized"}


def test_device_test_policy_disabled_never_probes_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = mock.Mock(side_effect=AssertionError("must not probe"))
    monkeypatch.setattr("devtool.android._standalone.connected_android_devices", probe)
    retained, skipped, adb, states = filter_unavailable_connected_android_tests(
        [":app:connectedDebugAndroidTest", ":app:assembleDebug"], sdk_root=None, device_test_policy="disabled"
    )
    assert retained == [":app:assembleDebug"] and skipped == [":app:connectedDebugAndroidTest"]
    assert not adb and states == {}
    probe.assert_not_called()


def test_instrumentation_policy_aliases_and_cli_surfaces() -> None:
    assert normalize_instrumentation_policy("never") == "disabled"
    assert normalize_instrumentation_policy("always") == "required"
    assert normalize_instrumentation_policy({"policy": "when-device-present"}) == "when-device-present"
    parser = build_parser()
    assert parser.parse_args(["android", "signing", "inspect"]).signing_action == "inspect"
    assert parser.parse_args(["android", "install", "app.apk", "--policy", "first-physical"]).android_command == "install"
    assert parser.parse_args(["android", "smoke", "install", "--apk", "app.apk"]).gate == "install"
    assert parser.parse_args(["instrumentation", "--class", "demo.UiTest"]).instrumentation_classes == ["demo.UiTest"]


def test_nested_signing_and_instrumentation_config_round_trip(tmp_path: Path) -> None:
    from devtool.config import load_project_config
    (tmp_path / "app").mkdir()
    (tmp_path / ".devtool.toml").write_text(
        '[project]\nrunner="android"\n'
        '[targets.app]\nrunner="android"\nroot="app"\n'
        '[targets.app.android.instrumentation]\npolicy="required"\ncapture_logcat_on_failure=true\n'
        '[targets.app.android.signing.release]\nmode="devtool"\npolicy="release-required"\nkeystore="~/.config/app/release.p12"\nalias="release"\nstore_password_env="APP_STORE"\n',
        encoding="utf-8",
    )
    config = load_project_config(str(tmp_path), None, target_name="app")
    assert config.runner_options["android"]["instrumentation"]["policy"] == "required"
    assert config.runner_options["android"]["signing"]["release"]["mode"] == "devtool"


def test_project_runner_uses_canonical_instrumentation_adapter(tmp_path: Path) -> None:
    from devtool.project_runners import AndroidGradleRunner
    _gradle_project(tmp_path)
    config = _config(tmp_path, {"instrumentation": {"policy": "required"}, "adb": {"device_policy": "first-physical"}})
    config.runner_options["test_selection"] = {"instrumented": True}
    specs = AndroidGradleRunner().build_specs(config, "test")
    spec = next(item for item in specs if "devtool.android_instrumentation" in item.argv)
    assert any(item.metadata.get("instrumentation_build_owned_by_gradle_planner") for item in specs)
    assert "devtool.android_instrumentation" in spec.argv
    assert "--canonical" in spec.argv
    assert "--policy" in spec.argv and spec.argv[spec.argv.index("--policy") + 1] == "required"
    assert "--device-policy" in spec.argv and spec.argv[spec.argv.index("--device-policy") + 1] == "first-physical"


def test_aops03_production_contracts_are_wired() -> None:
    root = Path(__file__).resolve().parents[1]
    integration = (root / "devtool" / "android_integration.py").read_text(encoding="utf-8")
    runners = (root / "devtool" / "project_runners.py").read_text(encoding="utf-8")
    standalone = (root / "devtool" / "android" / "_standalone.py").read_text(encoding="utf-8")
    assert "signing_gradle_args_for_tasks" in integration
    assert "--device-test-policy" in integration and "--device-test-policy" in standalone
    assert "--canonical" in runners and "resolve_instrumentation_policy" in runners
    assert 'shutil.which("adb")' not in runners


def test_signing_identity_reports_certificate_expiry() -> None:
    text = """Alias name: release
Owner: CN=Example
Issuer: CN=Example
Serial number: 01
Valid from: Fri Jan 01 00:00:00 UTC 2021 until: Sat Jan 01 00:00:00 UTC 2022
Certificate fingerprints:
         SHA1: 11:22
         SHA256: AA:BB:CC
Signature algorithm name: SHA256withRSA
"""
    identity = AndroidSigningManager._identity_from_keytool("demo.p12", text)
    assert identity.sha256 == "aabbcc"
    assert identity.expired is True
    assert identity.expires_in_days is not None and identity.expires_in_days < 0


def test_signing_profile_report_redacts_external_secret_command(tmp_path: Path) -> None:
    profile = SigningProfile(
        keystore=str(tmp_path / "release.p12"), alias="release",
        store_password_command="secret-tool lookup service android token raw-secret",
        key_password_command="printf raw-key-secret",
    )
    payload = profile.as_dict()
    assert payload["store_password_command"] == "<configured>"
    assert payload["key_password_command"] == "<configured>"
    assert "raw-secret" not in repr(payload)
    assert "raw-key-secret" not in repr(payload)


def test_restore_restart_smoke_preserves_package_state() -> None:
    from devtool.android.device_ops import DeviceOperationResult
    ops = AndroidDeviceOperations(_OneDeviceManager())
    passed = lambda operation: DeviceOperationResult(operation, "passed", "serial-1")
    with mock.patch.object(ops, "package_info", side_effect=[passed("package-info"), passed("package-info")]), mock.patch.object(ops, "restart", return_value=[passed("stop"), passed("launch")]):
        result = ops.restore_restart_smoke("com.example.app", serial="serial-1", policy="serial")
    assert result.ok
    assert result.gate == "restore-restart-smoke"


def test_required_device_wait_rejects_unauthorized_immediately() -> None:
    class UnauthorizedManager(_OneDeviceManager):
        def list_devices(self, *, enrich: bool = False):
            return [AndroidDevice("serial-1", "unauthorized", model="Pixel")]

    ops = AndroidDeviceOperations(UnauthorizedManager())
    with pytest.raises(Exception, match="authorization is required"):
        ops.wait_for_device(serial="serial-1", policy="serial", timeout=5, poll_interval=0.01)


@pytest.mark.requires_tool("keytool")
@pytest.mark.slow_external
def test_real_keytool_create_inspect_export_when_available(tmp_path: Path) -> None:
    import shutil
    runtime = RuntimeIdentity("linux", "host", "x86_64", "glibc")
    context = AndroidExecutionContext(runtime, ".", "app", "release", "", "", {
        "java": AndroidToolResolution("java", "system", True, path=shutil.which("java") or "/usr/bin/java", compatible=True),
    }, "none")
    manager = AndroidSigningManager(context)
    store = tmp_path / "release.p12"
    identity = manager.create(store, alias="release", store_password="devtool-test-password", key_password="devtool-test-password", validity_days=30)
    assert identity.alias == "release"
    assert len(identity.sha256) == 64
    assert identity.expired is False
    pem = manager.export_certificate(SigningProfile(keystore=str(store), alias="release"), tmp_path / "release.pem", store_password="devtool-test-password")
    assert pem.is_file() and "BEGIN CERTIFICATE" in pem.read_text(encoding="utf-8")


def test_fd_secret_is_not_consumed_by_signing_init_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.signing import signing_environment_for_tasks
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"one-shot-secret\n")
        os.close(write_fd)
        write_fd = -1
        monkeypatch.setenv("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", str(tmp_path / "runtime"))
        _register_android_signing_identity(tmp_path, monkeypatch, keystore, secret_kind="fd", secret_reference=str(read_fd))
        config = _config(tmp_path, {"signing": {"release": {
            "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
        }}})
        # Staging validates only the fd descriptor; it must not consume the pipe.
        assert stage_gradle_signing_init_script(config, [":app:assembleRelease"], env={}) is not None
        child_env = signing_environment_for_tasks(config, [":app:assembleRelease"], env={})
        assert child_env["DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD"] == "one-shot-secret"
        assert child_env["DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD"] == "one-shot-secret"
    finally:
        if write_fd >= 0:
            os.close(write_fd)
        os.close(read_fd)



def test_planned_android_spec_never_contains_signing_secret_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.project_runners import AndroidGradleRunner
    _gradle_project(tmp_path)
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    monkeypatch.setenv("DEVTOOL_ANDROID_SIGNING_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("APP_STORE", "planned-store-secret")
    monkeypatch.setenv("APP_KEY", "planned-key-secret")
    config = _config(tmp_path, {
        "instrumentation": {"policy": "required"},
        "signing": {"release": {
            "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
            "key_password_env": "APP_KEY",
        }},
    })
    config.runner_options["test_selection"] = {"instrumented": True}
    specs = AndroidGradleRunner().build_specs(config, "test")
    spec = next(item for item in specs if "devtool.android_instrumentation" in item.argv)
    rendered = repr(specs)
    assert "planned-store-secret" not in rendered
    assert "planned-key-secret" not in rendered
    assert spec.metadata["android_signing_required"] is True
    assert spec.metadata["android_signing_secret_transport"] == "execution-boundary"
    assert "DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD" not in spec.env
    assert "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD" not in spec.env


def test_signing_secrets_materialize_only_at_native_execution_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    monkeypatch.setenv("APP_STORE", "runtime-store")
    monkeypatch.setenv("APP_KEY", "runtime-key")
    config = _config(tmp_path, {"signing": {"release": {
        "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
        "key_password_env": "APP_KEY",
    }}})
    metadata = signing_plan_metadata(config, [":app:assembleRelease"], env=os.environ)
    planned = {"SAFE": "value"}
    assert "runtime-store" not in repr(metadata)
    child = signing_environment_for_execution(config, metadata, env={**os.environ, **planned})
    assert child["DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD"] == "runtime-store"
    assert child["DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD"] == "runtime-key"
    assert planned == {"SAFE": "value"}


def test_signing_secret_transport_fails_closed_for_shell_wrapped_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    monkeypatch.setenv("APP_STORE", "runtime-store")
    config = _config(tmp_path, {"signing": {"release": {
        "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
    }}})
    metadata = signing_plan_metadata(config, [":app:assembleRelease"], env=os.environ)
    metadata["execution_mode"] = "termux-host-chroot"
    with pytest.raises(Exception, match="refuses the selected execution backend"):
        signing_environment_for_execution(config, metadata, env=os.environ)


def test_validation_session_never_persists_internal_signing_passwords() -> None:
    from devtool.validation_sessions import serialize_spec
    spec = CommandSpec(
        "release",
        ["gradle", "assembleRelease"],
        env={
            "SAFE": "yes",
            "DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD": "store-secret",
            "DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD": "key-secret",
        },
    )
    payload = serialize_spec(spec)
    rendered = repr(payload)
    assert payload["env"] == {"SAFE": "yes"}
    assert "store-secret" not in rendered and "key-secret" not in rendered


def test_project_env_cannot_be_used_as_signing_secret_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    config = _config(tmp_path, {"signing": {"release": {
        "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
    }}}, env={"APP_STORE": "must-not-live-in-toml"})
    with pytest.raises(Exception, match="process environment, not project"):
        signing_plan_metadata(config, [":app:assembleRelease"], env={"APP_STORE": "must-not-live-in-toml"})


def test_instrumentation_orchestrator_builds_with_staged_devtool_builder(tmp_path: Path) -> None:
    from devtool.android.instrumentation import AndroidInstrumentationOrchestrator, InstrumentationRequest
    from devtool.android_integration import AndroidCommandPlan
    ops = AndroidDeviceOperations(_OneDeviceManager())
    orchestrator = AndroidInstrumentationOrchestrator(ops, env={})
    request = InstrumentationRequest(project=tmp_path, variant="debug")
    plan = AndroidCommandPlan(tmp_path, tmp_path / "build-apk", ("python3", str(tmp_path / "build-apk")), ("assembleDebug",))
    completed = mock.Mock(returncode=0, stdout="ok", stderr="")
    with mock.patch("devtool.android_integration.plan_android_command", return_value=plan), \
         mock.patch("devtool.android_integration.android_builder_arguments_for_tasks", return_value=[str(tmp_path), "--no-chroot", "--task", "assembleDebug", "--task", "assembleDebugAndroidTest"]), \
         mock.patch("devtool.android.instrumentation.subprocess.run", return_value=completed) as run:
        orchestrator._build_with_devtool_builder(request)
    argv = run.call_args.args[0]
    assert argv[:2] == ["python3", str(tmp_path / "build-apk")]
    assert "assembleDebugAndroidTest" in argv
    assert "gradle" not in Path(argv[0]).name.lower()


def test_signing_list_cli_surface_and_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.cli import android_signing_payload
    parser = build_parser()
    parsed = parser.parse_args(["--repo", str(tmp_path), "android", "signing", "list", "--keystore", str(tmp_path / "release.p12")])
    assert parsed.signing_action == "list"
    config = _config(tmp_path, {"signing": {"release": {"keystore": str(tmp_path / "release.p12")}}})
    identity = mock.Mock()
    identity.as_dict.return_value = {"alias": "release", "sha256": "AA", "valid_until": "2099-01-01"}
    with mock.patch.object(AndroidSigningManager, "list_aliases", return_value=[identity]) as listed:
        payload = android_signing_payload(config, action="list", keystore=str(tmp_path / "release.p12"))
    assert payload["identities"][0]["alias"] == "release"
    listed.assert_called_once()


def test_aops03_public_surfaces_document_signing_list_and_secret_boundary() -> None:
    root = Path(__file__).resolve().parents[1]
    completion = (root / "completions" / "_devtool").read_text(encoding="utf-8")
    generated = (root / "devtool" / "completion.py").read_text(encoding="utf-8")
    manpage = (root / "share" / "man" / "man1" / "devtool.1").read_text(encoding="utf-8")
    signing = (root / "devtool" / "android" / "signing.py").read_text(encoding="utf-8")
    assert "create list inspect verify compare import export-certificate" in completion
    assert "create list inspect verify compare import export-certificate" in generated
    assert "signing list" in manpage
    assert "execution-boundary" in signing
    assert "signing_environment_for_execution" in signing


def test_life_a04_signing_plan_carries_full_non_secret_transport_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.android.signing import (
        PROTECTED_SIGNING_BACKENDS,
        signing_environment_for_protected_backend,
        signing_transport_matrix,
    )
    keystore = tmp_path / "release.p12"
    keystore.write_bytes(b"placeholder")
    _register_android_signing_identity(tmp_path, monkeypatch, keystore)
    monkeypatch.setenv("APP_STORE", "runtime-store")
    monkeypatch.setenv("APP_KEY", "runtime-key")
    config = _config(tmp_path, {"signing": {"release": {
        "mode": "devtool", "policy": "release-required", "identity_id": "android-release",
        "key_password_env": "APP_KEY",
    }}})

    metadata = signing_plan_metadata(config, [":app:assembleRelease"], env=os.environ)
    matrix = metadata["android_signing_transport_matrix"]

    assert matrix == signing_transport_matrix()
    assert tuple(matrix["all_supported_backends"]) == PROTECTED_SIGNING_BACKENDS
    assert set(matrix["protected_destination_backends"]) == {
        "ssh-command", "ssh-proot-oci", "termux-host-chroot", "proot-distro"
    }
    rendered = repr(metadata)
    assert "runtime-store" not in rendered and "runtime-key" not in rendered
    for backend in matrix["protected_destination_backends"]:
        resolved = signing_environment_for_protected_backend(config, metadata, env=os.environ, backend=backend)
        assert resolved["DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD"] == "runtime-store"
        assert resolved["DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD"] == "runtime-key"
