from __future__ import annotations

import os
from pathlib import Path
import textwrap

import pytest

from devtool.android.capabilities import AndroidToolResolution, resolve_android_execution_context
from devtool.android.devices import AndroidDevice, AndroidDeviceError, AndroidDeviceManager, parse_adb_devices
from devtool.cli import build_parser
from devtool.models import ProjectConfig
from devtool.termux_toolchains import RuntimeIdentity, resolve_adb_provider


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nset -eu\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


def _project_config(root: Path, android: dict | None = None) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind="android",
        runner="android",
        project_root=str(root),
        selected_target="app",
        variant="debug",
        runner_options={"android": dict(android or {})},
    )


def test_parse_adb_devices_normalizes_states_metadata_and_transport() -> None:
    devices = parse_adb_devices(
        "List of devices attached\n"
        "ABC123 device product:foo model:Pixel_8 device:husky usb:1-1 transport_id:7\n"
        "emulator-5554 offline product:sdk_gphone model:sdk_gphone64_x86_64 transport_id:8\n"
        "10.0.0.2:5555 unauthorized product:bar model:Remote device:remote transport_id:9\n"
    )
    assert [(d.serial, d.state) for d in devices] == [
        ("ABC123", "device"), ("emulator-5554", "offline"), ("10.0.0.2:5555", "unauthorized")
    ]
    assert devices[0].model == "Pixel 8"
    assert devices[0].connection_type == "usb"
    assert devices[0].transport_id == "7"
    assert devices[1].emulator is True and devices[1].physical is False
    assert devices[2].connection_type == "tcp"


class _InventoryManager(AndroidDeviceManager):
    def __init__(self, devices: list[AndroidDevice], env: dict[str, str] | None = None) -> None:
        super().__init__(AndroidToolResolution("adb", "path", True, path="/fake/adb", compatible=True), env=env or {})
        self._devices = devices

    def list_devices(self, *, enrich: bool = False) -> list[AndroidDevice]:
        return list(self._devices)


def test_device_selection_policies_and_android_serial() -> None:
    physical = AndroidDevice("phone", "device", model="Pixel", physical=True)
    emulator = AndroidDevice("emulator-5554", "device", model="Emulator", emulator=True, physical=False)
    offline = AndroidDevice("spare", "offline", model="Spare")
    manager = _InventoryManager([physical, emulator, offline])
    with pytest.raises(AndroidDeviceError, match="Multiple usable"):
        manager.select("exactly-one")
    assert manager.select("first-physical")[0].serial == "phone"
    assert manager.select("first-emulator")[0].serial == "emulator-5554"
    assert [d.serial for d in manager.select("all")] == ["phone", "emulator-5554"]
    assert manager.select("preferred", preferred="Emulator")[0].serial == "emulator-5554"
    assert manager.select("serial", serial="spare", include_unusable=True)[0].state == "offline"
    with pytest.raises(AndroidDeviceError, match="offline"):
        manager.select("serial", serial="spare")
    env_manager = _InventoryManager([physical, emulator], env={"ANDROID_SERIAL": "phone"})
    assert env_manager.select("exactly-one")[0].serial == "phone"


def test_native_termux_auto_prefers_working_project_sdk_adb_before_ambient_termux(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    sdk = tmp_path / "sdk"
    sdk_adb = _exe(
        sdk / "platform-tools" / "adb",
        """
        if [ "${1:-}" = "version" ]; then
          printf "Android Debug Bridge version 1.0.41\\nVersion 36.0.0\\n"
        elif [ "${1:-}" = "devices" ]; then
          printf "List of devices attached\\nSDK device product:x model:Sdk device:sdk transport_id:1\\n"
        else
          exit 2
        fi
        """,
    )
    _exe(bindir / "pm", 'printf "package:/data/app/com.termux.api/base.apk\\n"\n')
    _exe(bindir / "termux-usb", 'if [ "${1:-}" = "-l" ]; then printf "[\\"/dev/bus/usb/001/002\\"]\\n"; else exit 2; fi\n')
    _exe(
        bindir / "termux-adb",
        """
        if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n";
        elif [ "${1:-}" = "devices" ]; then printf "List of devices attached\\nTERMUX device\\n"; else exit 2; fi
        """,
    )
    _exe(
        bindir / "adb",
        'if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n"; else printf "List of devices attached\\n"; fi\n',
    )
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix, android_api=35)
    env = {"PATH": str(bindir), "PREFIX": str(prefix)}
    result = resolve_adb_provider(sdk_root=sdk, provider="auto", env=env, runtime=runtime)
    assert result.compatible is True
    assert result.provider == "sdk"
    assert result.path == sdk_adb


def test_native_termux_auto_falls_back_to_termux_adb_when_project_sdk_adb_is_unusable(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    sdk = tmp_path / "sdk"
    _exe(sdk / "platform-tools" / "adb", 'exit 126\n')
    _exe(bindir / "pm", 'printf "package:/data/app/com.termux.api/base.apk\\n"\n')
    _exe(bindir / "termux-usb", 'if [ "${1:-}" = "-l" ]; then printf "[\\"/dev/bus/usb/001/002\\"]\\n"; else exit 2; fi\n')
    termux_adb = _exe(
        bindir / "termux-adb",
        """
        if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n";
        elif [ "${1:-}" = "devices" ]; then printf "List of devices attached\\nABC device\\n"; else exit 2; fi
        """,
    )
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix, android_api=35)
    env = {"PATH": str(bindir), "PREFIX": str(prefix)}
    result = resolve_adb_provider(sdk_root=sdk, provider="auto", env=env, runtime=runtime)
    assert result.compatible is True
    assert result.provider == "termux-adb"
    assert result.path == termux_adb


def test_native_termux_auto_prefers_working_termux_adb(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    _exe(bindir / "pm", 'printf "package:/data/app/com.termux.api/base.apk\\n"\n')
    _exe(bindir / "termux-usb", 'if [ "${1:-}" = "-l" ]; then printf "[\\\"/dev/bus/usb/001/002\\\"]\\n"; else exit 2; fi\n')
    _exe(
        bindir / "termux-adb",
        '''
        if [ "${1:-}" = "version" ]; then
          printf "Android Debug Bridge version 1.0.41\\nVersion 35.0.2\\n"
        elif [ "${1:-}" = "devices" ]; then
          printf "List of devices attached\\nABC device product:x model:Y device:z transport_id:1\\n"
        else
          exit 2
        fi
        ''',
    )
    _exe(
        bindir / "adb",
        '''
        if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n";
        elif [ "${1:-}" = "devices" ]; then printf "List of devices attached\\n"; else exit 2; fi
        ''',
    )
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix, android_api=35)
    env = {"PATH": str(bindir), "PREFIX": str(prefix)}
    result = resolve_adb_provider(provider="auto", env=env, runtime=runtime)
    assert result.compatible is True
    assert result.provider == "termux-adb"
    assert result.path == bindir / "termux-adb"
    assert result.metadata["termux_usb"]["list_status"] == "ready"
    assert result.metadata["termux_usb"]["devices"] == ["/dev/bus/usb/001/002"]
    assert "Termux:API" in result.selection_reason


def test_termux_adb_reports_missing_termux_usb_instead_of_falling_through(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    bindir = prefix / "bin"
    _exe(
        bindir / "termux-adb",
        'if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n"; else printf "List of devices attached\\n"; fi\n',
    )
    runtime = RuntimeIdentity("android", "termux", "arm64", "bionic", prefix=prefix, android_api=35)
    result = resolve_adb_provider(provider="termux-adb", env={"PATH": str(bindir), "PREFIX": str(prefix)}, runtime=runtime)
    assert result.available is True
    assert result.compatible is False
    assert "termux-usb" in result.rejection_reason


def test_explicit_path_provider_and_none_provider(tmp_path: Path) -> None:
    adb = _exe(
        tmp_path / "adb",
        'if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n"; else printf "List of devices attached\\n"; fi\n',
    )
    runtime = RuntimeIdentity("linux", "host", "x86_64", "glibc")
    chosen = resolve_adb_provider(provider="path", explicit_path=adb, env={"PATH": ""}, runtime=runtime)
    assert chosen.compatible is True and chosen.path == adb and chosen.provenance == "android.adb.path"
    disabled = resolve_adb_provider(provider="none", env={"PATH": ""}, runtime=runtime)
    assert disabled.available is False and "disabled" in disabled.rejection_reason.lower()


def test_execution_context_resolves_sdk_build_tools_and_configured_adb(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    bt = sdk / "build-tools" / "36.0.0"
    for name, invocation in {
        "aapt2": 'printf "Android Asset Packaging Tool (aapt) 2:8.0.0\\n"',
        "apksigner": 'printf "0.9\\n"',
        "zipalign": 'printf "Zip alignment utility\\n"',
    }.items():
        _exe(bt / name, invocation + "\n")
    adb = _exe(
        sdk / "platform-tools" / "adb",
        'if [ "${1:-}" = "version" ]; then printf "Android Debug Bridge version 1.0.41\\n"; else printf "List of devices attached\\n"; fi\n',
    )
    config = _project_config(tmp_path, {"sdk_root": str(sdk), "adb": {"provider": "sdk"}})
    context = resolve_android_execution_context(config, env={"PATH": os.environ.get("PATH", ""), "ANDROID_SDK_ROOT": str(sdk)})
    assert context.sdk_root == str(sdk)
    assert context.tool("build-tools").version == "36.0.0"
    assert context.tool("apksigner").provider == "sdk"
    assert context.adb.provider == "sdk" and context.adb.path == str(adb)
    assert context.as_dict()["device_transport"] == "adb"


def test_doctor_distinguishes_permission_unauthorized_and_offline() -> None:
    permission = AndroidToolResolution(
        "adb", "termux-adb", True, path="/fake/termux-adb", compatible=False,
        rejection_reason="USB permission not granted",
        metadata={"termux_usb": {"termux_usb": "/fake/termux-usb", "termux_api_app": True, "list_status": "ready", "devices": ["/dev/usb"]}},
    )
    assert AndroidDeviceManager(permission).doctor().code == "usb-permission-not-granted"
    assert _InventoryManager([AndroidDevice("a", "unauthorized")]).doctor().code == "device-unauthorized"
    assert _InventoryManager([AndroidDevice("a", "offline")]).doctor().code == "device-offline"


def test_android_cli_exposes_provider_doctor_and_selection_policies() -> None:
    parser = build_parser()
    args = parser.parse_args(["android", "devices", "doctor", "--provider", "termux-adb"])
    assert args.android_command == "devices"
    assert args.device_action == "doctor"
    assert args.provider == "termux-adb"
    args = parser.parse_args(["android", "devices", "--policy", "first-physical", "--serial", "ABC"])
    assert args.policy == "first-physical"
    assert args.serial == "ABC"


def test_builder_translation_carries_adb_provider_and_path(tmp_path: Path) -> None:
    from devtool.android_integration import android_builder_arguments_for_tasks

    config = _project_config(tmp_path, {"adb": {"provider": "termux-adb", "path": "/prefix/bin/termux-adb"}})
    argv = android_builder_arguments_for_tasks(config, [":app:connectedDebugAndroidTest"], scan_artifacts=False)
    assert "--adb-provider" in argv and argv[argv.index("--adb-provider") + 1] == "termux-adb"
    assert "--adb-path" in argv and argv[argv.index("--adb-path") + 1] == "/prefix/bin/termux-adb"



def test_target_android_adb_config_round_trips_into_runner_options(tmp_path: Path) -> None:
    from devtool.config import load_project_config

    (tmp_path / "app").mkdir()
    (tmp_path / ".devtool.toml").write_text(
        '[project]\nrunner="android"\n'
        '\n[targets.app]\nrunner="android"\nroot="app"\n'
        '\n[targets.app.android]\nvariant="debug"\n'
        '\n[targets.app.android.adb]\nprovider="termux-adb"\ndevice_policy="first-physical"\nserial="ABC"\n',
        encoding="utf-8",
    )
    config = load_project_config(str(tmp_path), None, target_name="app")
    adb = config.runner_options["android"]["adb"]
    assert adb == {"provider": "termux-adb", "device_policy": "first-physical", "serial": "ABC"}

def test_android_consumers_use_canonical_resolver_contracts() -> None:
    root = Path(__file__).resolve().parents[1]
    instrumentation = (root / "devtool" / "android_instrumentation.py").read_text(encoding="utf-8")
    artifacts = (root / "devtool" / "android_artifacts.py").read_text(encoding="utf-8")
    qualification = (root / "devtool" / "release_qualification.py").read_text(encoding="utf-8")
    doctor = (root / "devtool" / "doctor.py").read_text(encoding="utf-8")
    standalone = (root / "devtool" / "android" / "_standalone.py").read_text(encoding="utf-8")
    assert "resolve_adb" in instrumentation and "AndroidDeviceManager" in instrumentation
    assert "resolved_android_tool" in artifacts
    assert "AndroidDeviceManager" in qualification and "resolve_android_execution_context" in qualification
    assert "resolve_android_execution_context" in doctor
    assert ".resolve_adb_provider(" in standalone
    assert 'shutil.which("adb")' not in instrumentation
    assert 'shutil.which("adb")' not in qualification
    assert 'shutil.which("adb")' not in doctor
