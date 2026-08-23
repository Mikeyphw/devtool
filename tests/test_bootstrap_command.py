from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool import android_build
from devtool import bootstrap
from devtool.bootstrap import BootstrapDestination, BootstrapRequest
from devtool.cli import build_parser, main as cli_main
from devtool.models import ProjectConfig


def test_parse_destination_supports_termux_linux_and_arch_specific_chroot(monkeypatch: pytest.MonkeyPatch) -> None:
    assert bootstrap.parse_destination("termux") == BootstrapDestination("termux", "termux", expected_arch="arm64")
    assert bootstrap.parse_destination("linux-aarch64") == BootstrapDestination("linux-arm64", "linux", expected_arch="arm64")
    assert bootstrap.parse_destination("linux-x86_64") == BootstrapDestination("linux-amd64", "linux", expected_arch="amd64")
    assert bootstrap.parse_destination("chroot:androidos") == BootstrapDestination("chroot:androidos", "chroot", name="androidos")
    assert bootstrap.parse_destination("chroot-arm64:androidos") == BootstrapDestination(
        "chroot:androidos", "chroot", name="androidos", expected_arch="arm64"
    )

    monkeypatch.setattr(bootstrap, "_running_under_termux", lambda: False)
    monkeypatch.setattr(bootstrap, "normalize_machine", lambda machine=None: "amd64")
    assert bootstrap.parse_destination("current").canonical == "linux-amd64"


def test_parse_destination_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="Unsupported bootstrap destination"):
        bootstrap.parse_destination("windows-amd64")
    with pytest.raises(ValueError, match="Invalid chroot destination"):
        bootstrap.parse_destination("chroot:bad name")


def test_builder_argv_carries_provisioning_contract(tmp_path: Path) -> None:
    request = BootstrapRequest(
        java_version=21,
        gradle_version="9.7.1",
        android_platform="36",
        build_tools="36.0.0",
        ndk_version="28.2.13676358",
        cmake_version="3.22.1",
        sdk_provider="managed",
        aapt2_provider="sdk",
        native_provider="ndk",
        gradle_provider="download",
        java_provider="system",
        sdk_root="/opt/android-sdk",
        gradle_user_home="/var/cache/gradle",
        cache_dir="/var/cache/devtool/android",
    )
    argv = bootstrap.build_builder_argv(
        tmp_path / "build-apk",
        tmp_path / "project",
        tmp_path / "report.json",
        request,
        force=True,
        downloads="force",
    )
    joined = " ".join(argv)
    assert "--bootstrap-only" in argv
    assert "--bootstrap-report" in argv
    assert "--no-chroot" in argv
    assert "--toolchain-downloads force" in joined
    assert "--min-java 21" in joined
    assert "--gradle-version 9.7.1" in joined
    assert "--android-platform 36" in joined
    assert "--build-tools 36.0.0" in joined
    assert "--ndk-version 28.2.13676358" in joined
    assert "--cmake-version 3.22.1" in joined
    assert "--sdk-root /opt/android-sdk" in joined
    assert "--gradle-user-home /var/cache/gradle" in joined
    assert "--force-setup" in argv


def test_synthetic_bootstrap_project_does_not_pin_auto_sdk_components() -> None:
    _settings, build = bootstrap._synthetic_project_text(
        BootstrapRequest(build_tools="auto", ndk_version="auto", cmake_version="auto")
    )

    assert "compileSdk 36" in build
    assert "externalNativeBuild" in build
    assert "buildToolsVersion" not in build
    assert "ndkVersion" not in build
    assert 'version "36.0.0"' not in build
    assert 'version "28.2.13676358"' not in build
    assert 'version "3.22.1"' not in build


def test_synthetic_bootstrap_project_keeps_explicit_sdk_component_pins() -> None:
    _settings, build = bootstrap._synthetic_project_text(
        BootstrapRequest(
            build_tools="36.0.0",
            ndk_version="28.2.13676358",
            cmake_version="3.22.1",
        )
    )

    assert 'buildToolsVersion "36.0.0"' in build
    assert 'ndkVersion "28.2.13676358"' in build
    assert 'version "3.22.1"' in build


def _ready_build_tools(sdk: Path, version: str) -> None:
    directory = sdk / "build-tools" / version
    (directory / "lib").mkdir(parents=True, exist_ok=True)
    (directory / "source.properties").write_text(f"Pkg.Revision = {version}\n", encoding="utf-8")
    (directory / "lib" / "d8.jar").write_bytes(b"jar")


def _ready_ndk(sdk: Path, version: str) -> None:
    directory = sdk / "ndk" / version
    (directory / "toolchains" / "llvm" / "prebuilt").mkdir(parents=True, exist_ok=True)
    (directory / "build" / "cmake").mkdir(parents=True, exist_ok=True)
    (directory / "source.properties").write_text(f"Pkg.Revision = {version}\n", encoding="utf-8")
    (directory / "build" / "cmake" / "android.toolchain.cmake").write_text("# ready\n", encoding="utf-8")


def _ready_cmake(sdk: Path, version: str) -> None:
    directory = sdk / "cmake" / version
    (directory / "bin").mkdir(parents=True, exist_ok=True)
    (directory / "source.properties").write_text(f"Pkg.Revision = {version}\n", encoding="utf-8")
    for name in ("cmake", "ninja"):
        executable = directory / "bin" / name
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)


def test_bootstrap_auto_reuses_complete_installed_sdk_components(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    _ready_build_tools(sdk, "36.1.0")
    _ready_ndk(sdk, "29.0.14206865")
    _ready_cmake(sdk, "4.1.2")
    project = android_build.ProjectInfo(
        root=tmp_path / "project",
        name="bootstrap",
        android=True,
        android_application=True,
        compile_sdk=36,
        build_tools=None,
        agp_major=9,
        native_build=True,
        ndk_version=None,
        cmake_version=None,
    )

    assert android_build.select_required_build_tools_version(
        project, sdk, "auto", prefer_installed=True
    ) == "36.1.0"
    assert android_build.select_required_ndk_version(
        project, sdk, "auto", prefer_installed=True
    ) == "29.0.14206865"
    assert android_build.select_required_cmake_version(
        project, sdk, "auto", prefer_installed=True
    ) == "4.1.2"


def test_bootstrap_ensure_android_sdk_does_not_download_when_auto_components_are_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sdk = tmp_path / "sdk"
    (sdk / "platforms" / "android-36").mkdir(parents=True, exist_ok=True)
    (sdk / "platforms" / "android-36" / "android.jar").write_bytes(b"jar")
    _ready_build_tools(sdk, "36.1.0")
    _ready_ndk(sdk, "29.0.14206865")
    _ready_cmake(sdk, "4.1.2")
    project = android_build.ProjectInfo(
        root=tmp_path / "project",
        name="bootstrap",
        android=True,
        android_application=True,
        compile_sdk=36,
        build_tools=None,
        agp_major=9,
        native_build=True,
        ndk_version=None,
        cmake_version=None,
    )
    project.root.mkdir()

    monkeypatch.setattr(
        android_build,
        "install_sdk_packages_with_repair",
        lambda *a, **k: pytest.fail("sdkmanager install must not run for ready auto components"),
    )
    monkeypatch.setattr(android_build, "ensure_arm64_build_tools_compatible", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "ensure_ndk_host_compatible", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "update_local_properties", lambda *a, **k: None)

    resolved = android_build.ensure_android_sdk(
        project,
        sdk,
        tmp_path / "cache",
        force_setup=False,
        platform_override="auto",
        build_tools_override="auto",
        ndk_override="auto",
        cmake_override="auto",
        skip_install=False,
        offline=False,
        env={},
        dry_run=False,
        arm_build_tools_provider="none",
        ndk_host_provider="none",
        prefer_installed=True,
    )

    assert resolved[1:] == ("android-36", "36.1.0", "29.0.14206865", "4.1.2")


def test_normal_project_auto_keeps_strict_agp_build_tools_default(tmp_path: Path) -> None:
    sdk = tmp_path / "sdk"
    _ready_build_tools(sdk, "36.1.0")
    project = android_build.ProjectInfo(
        root=tmp_path / "project",
        name="real-project",
        android=True,
        android_application=True,
        compile_sdk=None,
        build_tools=None,
        agp_major=9,
        native_build=True,
        ndk_version=None,
        cmake_version=None,
        agp_version="9.3.1",
    )

    build_tools, build_tools_source = android_build.resolve_build_tools_requirement(project, sdk, "auto")
    ndk, ndk_source = android_build.resolve_ndk_requirement(project, sdk, "auto")

    assert build_tools == "36.0.0"
    assert build_tools_source == "agp-default"
    assert ndk == "28.2.13676358"
    assert ndk_source == "agp-default"


def test_real_project_explicit_sdk_component_pins_beat_agp_defaults(tmp_path: Path) -> None:
    project = android_build.ProjectInfo(
        root=tmp_path / "project",
        name="real-project",
        android=True,
        android_application=True,
        compile_sdk=None,
        build_tools="35.0.1",
        agp_major=9,
        native_build=True,
        ndk_version="27.2.12479018",
        cmake_version="3.22.1",
        agp_version="9.3.1",
    )

    build_tools, build_tools_source = android_build.resolve_build_tools_requirement(project, tmp_path / "sdk", "auto")
    ndk, ndk_source = android_build.resolve_ndk_requirement(project, tmp_path / "sdk", "auto")
    cmake, cmake_source = android_build.resolve_cmake_requirement(project, tmp_path / "sdk", "auto")

    assert (build_tools, build_tools_source) == ("35.0.1", "project-explicit")
    assert (ndk, ndk_source) == ("27.2.12479018", "project-explicit")
    assert (cmake, cmake_source) == ("3.22.1", "project-explicit")


def test_install_is_idempotent_when_matching_receipt_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    request = BootstrapRequest()
    receipt = {
        "schema": bootstrap.BOOTSTRAP_RECEIPT_SCHEMA,
        "destination": "termux",
        "status": "ready",
        "request": bootstrap.asdict(request),
        "resolved": {"architecture": "arm64", "sdk_root": "/sdk"},
    }
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: receipt)
    monkeypatch.setattr(bootstrap, "probe_receipt", lambda _destination, _receipt=None: (True, "ready"))
    monkeypatch.setattr(bootstrap, "receipt_path", lambda _destination: Path("/state/termux.json"))
    monkeypatch.setattr(bootstrap, "_run_local_install", lambda *a, **k: pytest.fail("provisioning must not run"))

    payload = bootstrap.install_bootstrap(destination, request)

    assert payload["ok"] is True
    assert payload["changed"] is False
    assert payload["status"] == "ready"
    assert payload["resolved"]["sdk_root"] == "/sdk"


def test_repair_bypasses_idempotent_shortcut(monkeypatch: pytest.MonkeyPatch) -> None:
    destination = BootstrapDestination("linux-amd64", "linux", expected_arch="amd64")
    request = BootstrapRequest()
    receipt = {
        "schema": bootstrap.BOOTSTRAP_RECEIPT_SCHEMA,
        "destination": destination.canonical,
        "status": "ready",
        "request": bootstrap.asdict(request),
        "resolved": {"architecture": "amd64"},
    }
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: receipt)
    calls: list[dict] = []

    def fake_run(_destination, _request, **kwargs):
        calls.append(kwargs)
        return {"architecture": "amd64", "gradle": "/gradle"}

    monkeypatch.setattr(bootstrap, "_run_local_install", fake_run)
    monkeypatch.setattr(bootstrap, "write_receipt", lambda *_args: Path("/state/linux-amd64.json"))

    payload = bootstrap.install_bootstrap(destination, request, repair=True)

    assert payload["changed"] is True
    assert calls == [{"force": True, "dry_run": False, "downloads": "auto"}]


def test_upgrade_uses_force_download_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    destination = BootstrapDestination("linux-amd64", "linux", expected_arch="amd64")
    request = BootstrapRequest()
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: None)
    calls: list[dict] = []

    def fake_run(_destination, _request, **kwargs):
        calls.append(kwargs)
        return {"architecture": "amd64"}

    monkeypatch.setattr(bootstrap, "_run_local_install", fake_run)
    monkeypatch.setattr(bootstrap, "write_receipt", lambda *_args: Path("/state/linux-amd64.json"))

    bootstrap.install_bootstrap(destination, request, upgrade=True)

    assert calls == [{"force": True, "dry_run": False, "downloads": "force"}]


def test_receipt_round_trip_uses_xdg_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    destination = BootstrapDestination("chroot:androidos", "chroot", name="androidos", expected_arch="arm64")
    request = BootstrapRequest()
    path = bootstrap.write_receipt(destination, request, {"architecture": "arm64", "sdk_root": "/opt/android-sdk"})

    assert path == tmp_path / "xdg" / "bootstrap" / "chroot-androidos.json"
    payload = bootstrap.load_receipt(destination)
    assert payload is not None
    assert payload["destination"] == "chroot:androidos"
    assert payload["request"]["gradle_version"] == "9.7.1"

    assert bootstrap.remove_receipt(destination) is True
    assert bootstrap.load_receipt(destination) is None


def test_legacy_receipts_are_invalidated_by_auto_resolution_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    path = bootstrap.receipt_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)

    assert bootstrap.BOOTSTRAP_RECEIPT_SCHEMA == 3
    for legacy_schema in (1, 2):
        path.write_text(
            json.dumps(
                {
                    "schema": legacy_schema,
                    "status": "ready",
                    "destination": "termux",
                    "resolved": {"sdk_root": "/sdk"},
                }
            ),
            encoding="utf-8",
        )
        assert bootstrap.load_receipt(destination) is None


def test_termux_receipt_probe_requires_all_verified_native_build_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    monkeypatch.setattr(bootstrap, "_validate_local_destination", lambda _destination: None)
    monkeypatch.setattr(bootstrap, "normalize_machine", lambda machine=None: "arm64")

    receipt = {
        "actual_arch": "arm64",
        "resolved": {
            "build_tool_paths": {
                "aapt2": str(tmp_path / "aapt2"),
                "aidl": str(tmp_path / "aidl"),
                "zipalign": str(tmp_path / "zipalign"),
            }
        },
    }
    for path in receipt["resolved"]["build_tool_paths"].values():
        Path(path).write_text("tool", encoding="utf-8")

    ok, detail = bootstrap._probe_local_receipt(destination, receipt)

    assert ok is False
    assert "split-select" in detail


def test_registered_receipt_reuses_baseline_but_allows_missing_project_components(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={"bootstrap": {"downloads": "auto"}})
    args = SimpleNamespace(
        bootstrap_downloads="",
        no_bootstrap_downloads=False,
        offline=False,
        chroot="",
        execution_environment="native-termux",
    )
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    monkeypatch.setattr(bootstrap, "_requested_destination_for_config", lambda _config, _args: destination)
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: {"status": "ready"})
    monkeypatch.setattr(bootstrap, "probe_receipt", lambda _destination, _receipt=None: (True, "ready"))
    monkeypatch.setattr(bootstrap, "receipt_path", lambda _destination: Path("/state/termux.json"))

    assert bootstrap.apply_registered_bootstrap_policy(config, args) is True
    assert config.runner_options["bootstrap"]["downloads"] == "missing"
    assert config.runner_options["bootstrap"]["destination"] == "termux"


def test_stale_registered_receipt_does_not_disable_repair_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={"bootstrap": {"downloads": "auto"}})
    args = SimpleNamespace(
        bootstrap_downloads="",
        no_bootstrap_downloads=False,
        offline=False,
        chroot="",
        execution_environment="native-termux",
    )
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    monkeypatch.setattr(bootstrap, "_requested_destination_for_config", lambda _config, _args: destination)
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: {"status": "ready"})
    monkeypatch.setattr(
        bootstrap,
        "probe_receipt",
        lambda _destination, _receipt=None: (False, "zipalign failed runtime verification"),
    )

    assert bootstrap.apply_registered_bootstrap_policy(config, args) is False
    assert config.runner_options["bootstrap"]["downloads"] == "auto"


def test_termux_provisioning_report_requires_runtime_verified_build_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = BootstrapDestination("termux", "termux", expected_arch="arm64")
    paths = {}
    for name in ("aapt2", "aidl", "zipalign", "split-select"):
        path = tmp_path / name
        path.write_text("tool", encoding="utf-8")
        paths[name] = str(path)

    monkeypatch.setattr(
        android_build,
        "native_android_build_tool_compatible",
        lambda _path, name, _machine: name != "zipalign",
    )
    with pytest.raises(RuntimeError, match="zipalign"):
        bootstrap._validate_provisioning_report(
            destination,
            {"architecture": "arm64", "build_tool_paths": paths},
        )


def test_explicit_download_policy_wins_over_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = ProjectConfig(repo_root=tmp_path, kind="android", runner_options={"bootstrap": {"downloads": "auto"}})
    args = SimpleNamespace(bootstrap_downloads="force", no_bootstrap_downloads=False, offline=False)
    monkeypatch.setattr(bootstrap, "load_receipt", lambda _destination: pytest.fail("receipt must not be consulted"))

    assert bootstrap.apply_registered_bootstrap_policy(config, args) is False
    assert config.runner_options["bootstrap"]["downloads"] == "auto"


def test_cli_parser_accepts_bootstrap_install_defaults() -> None:
    args = build_parser().parse_args(["bootstrap", "install", "--destination", "chroot-arm64:androidos"])
    assert args.command == "bootstrap"
    assert args.bootstrap_command == "install"
    assert args.destination == "chroot-arm64:androidos"
    assert args.bootstrap_java_version == 21
    assert args.bootstrap_gradle_version == "9.7.1"
    assert args.bootstrap_android_platform == "36"
    assert args.bootstrap_build_tools == "auto"
    assert args.bootstrap_ndk_version == "auto"
    assert args.bootstrap_cmake_version == "auto"


def test_cli_bootstrap_status_does_not_require_repository(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("devtool.cli.bootstrap_status", lambda destination: {
        "ok": True,
        "destination": destination.canonical,
        "status": "ready",
        "detail": "ready",
        "receipt": "/state/termux.json",
        "resolved": {"architecture": "arm64"},
    })
    rc = cli_main(["bootstrap", "status", "--destination", "termux", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["destination"] == "termux"
    assert payload["status"] == "ready"


def test_android_builder_parser_supports_bootstrap_only(tmp_path: Path) -> None:
    args = android_build.parse_args([
        str(tmp_path),
        "--bootstrap-only",
        "--bootstrap-report",
        str(tmp_path / "report.json"),
    ])
    assert args.bootstrap_only is True
    assert args.bootstrap_report == tmp_path / "report.json"


def test_android_bootstrap_only_returns_before_gradle_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "settings.gradle").write_text("rootProject.name='probe'\n", encoding="utf-8")
    (project / "build.gradle").write_text(
        "plugins { id 'com.android.application' version '9.0.0' apply false }\n"
        "android { compileSdk 36; buildToolsVersion '36.0.0'; ndkVersion '28.2.13676358'; externalNativeBuild { cmake { version '3.22.1' } } }\n",
        encoding="utf-8",
    )
    fake_java = tmp_path / "jdk"
    (fake_java / "bin").mkdir(parents=True)
    (fake_java / "bin" / "java").write_text("", encoding="utf-8")
    (fake_java / "bin" / "javac").write_text("", encoding="utf-8")
    fake_gradle = tmp_path / "gradle"
    fake_gradle.write_text("", encoding="utf-8")
    fake_sdk = tmp_path / "sdk"
    fake_sdk.mkdir()
    report = tmp_path / "report.json"

    monkeypatch.setattr(android_build, "maybe_reexec_in_chroot", lambda _argv: None)
    monkeypatch.setattr(android_build, "auto_manage_android_space", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "running_under_termux_python", lambda: False)
    monkeypatch.setattr(android_build, "find_java_home", lambda *a, **k: fake_java)
    monkeypatch.setattr(android_build, "certificate_store_available", lambda: True)
    monkeypatch.setattr(android_build, "select_native_provider", lambda *a, **k: "ndk")
    monkeypatch.setattr(android_build, "configure_native_cache", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "discover_sdk_root_with_source", lambda *a, **k: (fake_sdk, "test"))
    monkeypatch.setattr(android_build, "configure_android_sdk_env", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "clear_selected_provider_download", lambda *a, **k: None)
    monkeypatch.setattr(
        android_build,
        "ensure_android_sdk",
        lambda *a, **k: (fake_sdk, "android-36", "36.0.0", "28.2.13676358", "3.22.1"),
    )
    monkeypatch.setattr(android_build, "select_sdk_build_tools_aapt2", lambda *a, **k: tmp_path / "aapt2")
    monkeypatch.setattr(android_build, "select_cached_revanced_aapt2", lambda *a, **k: None)
    monkeypatch.setattr(android_build, "select_gradle", lambda *a, **k: fake_gradle)
    monkeypatch.setattr(android_build, "executable_provider", lambda path: "system")
    monkeypatch.setattr(android_build, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(
        android_build,
        "filter_unavailable_connected_android_tests",
        lambda *a, **k: pytest.fail("bootstrap-only must return before Gradle task/device handling"),
    )

    rc = android_build.main([
        str(project),
        "--no-chroot",
        "--bootstrap-only",
        "--bootstrap-report",
        str(report),
        "--dry-run",
        "--min-java",
        "21",
        "--gradle-version",
        "9.7.1",
        "--ndk-version",
        "28.2.13676358",
        "--cmake-version",
        "3.22.1",
    ])

    assert rc == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["platform"] == "android-36"
    assert payload["build_tools"] == "36.0.0"
    assert payload["ndk"] == "28.2.13676358"
    assert payload["cmake"] == "3.22.1"
    assert set(payload["build_tool_paths"]) == {"aapt2", "aidl", "zipalign", "split-select"}
    assert payload["dry_run"] is True
