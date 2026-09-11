from __future__ import annotations

"""On-device Android instrumentation runner used by ``devtool test --instrumented``.

The module intentionally uses Android's package manager and Activity Manager
interfaces directly, which makes it usable from rooted Termux without requiring
an emulator.  ADB is used only when an explicit external device serial is set.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any, Sequence

from . import android_build
from .android.capabilities import locate_adb_path, resolve_adb, resolve_android_execution_context
from .android.devices import AndroidDeviceError, AndroidDeviceManager


class InstrumentationError(RuntimeError):
    pass


class InstrumentationUnavailable(InstrumentationError):
    """No usable Android instrumentation execution target is available."""


def run(argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    rendered = " ".join(shlex.quote(value) for value in argv)
    try:
        result = subprocess.run(list(argv), text=True, capture_output=True, check=False)
    except OSError as exc:
        raise InstrumentationError(
            f"Could not execute command: {rendered}: {exc}"
        ) from exc
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and result.returncode != 0:
        raise InstrumentationError(
            f"Command failed ({result.returncode}): {rendered}"
        )
    return result


def _resolve_gradle(project: Path, configured: str) -> Path:
    raw = (configured or "./gradlew").strip() or "./gradlew"
    candidate = Path(raw).expanduser()
    project_candidate = candidate if candidate.is_absolute() else project / candidate

    if project_candidate.is_file() and os.access(project_candidate, os.X_OK):
        return project_candidate.resolve()

    # Explicit non-wrapper commands retain normal PATH semantics. The default
    # wrapper/system fallback is resolved through the canonical Android context.
    if raw not in {"./gradlew", "gradlew"}:
        resolved = shutil.which(raw)
        if resolved:
            return Path(resolved).resolve()
    else:
        context = resolve_android_execution_context(project_root=project)
        gradle = context.tool("gradle")
        if gradle.available and gradle.compatible is not False and gradle.path:
            return Path(gradle.path).resolve()

    if project_candidate.exists() and not os.access(project_candidate, os.X_OK):
        raise InstrumentationError(
            f"Gradle executable is not executable: {project_candidate}"
        )

    raise InstrumentationError(
        f"Gradle executable was not found: {raw}. "
        "Install Gradle, add it to PATH, or configure an executable Gradle command."
    )


def _variant_title(value: str) -> str:
    value = value.strip() or "debug"
    return value[:1].upper() + value[1:]


def _instrumentation_aapt2(
    project: Path,
    *,
    configured: str = "",
    provider: str = "auto",
) -> Path | None:
    """Resolve a host-runnable AAPT2 for instrumentation APK builds.

    Instrumentation historically invoked the project Gradle launcher directly,
    bypassing ``android_build``'s AAPT2 provider selection. On native Termux
    that lets AGP download the Maven linux/x86_64 AAPT2, which Android/Bionic
    cannot execute. Mirror the builder's host-safe selection here and fail
    before Gradle rather than ever falling through to an incompatible Maven
    executable.
    """

    requested = str(provider or "auto").strip().lower() or "auto"
    explicit = str(configured or "").strip()
    if explicit:
        candidate = Path(explicit).expanduser().resolve(strict=False)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise InstrumentationError(f"Configured AAPT2 is not executable: {candidate}")
        try:
            return android_build.validate_aapt2(candidate, dry_run=False)
        except Exception as exc:
            raise InstrumentationError(f"Configured AAPT2 cannot run: {candidate}: {exc}") from exc

    if not android_build.running_under_termux_python():
        return None
    if requested == "none":
        return None

    try:
        info = android_build.inspect_project(project)
        sdk_root = android_build.discover_sdk_root(None, project)
    except Exception:
        info = None
        sdk_root = None
    platform_id = f"android-{info.compile_sdk}" if info is not None and info.compile_sdk else None

    if requested in {"auto", "termux"}:
        candidate = android_build.termux_aapt2_path()
        if candidate is not None:
            compatible = sdk_root is None or android_build.termux_aapt2_supports_platform(
                candidate, sdk_root, platform_id, dry_run=False
            )
            if compatible:
                return candidate
        if requested == "termux":
            raise InstrumentationError(
                "Termux AAPT2 provider requested for instrumentation, but "
                "$PREFIX/bin/aapt2 is unavailable or incompatible with "
                f"{platform_id or 'the selected Android platform'}"
            )

    if requested in {"auto", "sdk"} and sdk_root is not None and info is not None:
        build_tools = android_build.select_required_build_tools_version(info, sdk_root, "auto")
        candidate = android_build.select_sdk_build_tools_aapt2(
            sdk_root, build_tools, platform_id, dry_run=False
        )
        if candidate is not None:
            return candidate
        if requested == "sdk":
            raise InstrumentationError(
                "SDK AAPT2 provider requested for instrumentation, but no "
                "host-compatible SDK AAPT2 is available"
            )

    raise InstrumentationError(
        "No host-compatible AAPT2 is available for the instrumentation setup build. "
        "On native Termux, install a compatible Termux AAPT2 or configure "
        "[android].aapt2 / [android.build_tools].aapt2_provider explicitly; "
        "Devtool will not fall back to AGP's linux/x86_64 Maven AAPT2."
    )


def _find_apks(project: Path, variant: str) -> tuple[Path, Path]:
    lower_variant = variant.lower()
    app_candidates: list[Path] = []
    test_candidates: list[Path] = []
    for path in project.glob("**/build/outputs/apk/**/*.apk"):
        if not path.is_file() or lower_variant not in path.as_posix().lower():
            continue
        name = path.name.lower()
        if "androidtest" in name or "-test" in name or "/androidtest/" in path.as_posix().lower():
            test_candidates.append(path.resolve())
        else:
            app_candidates.append(path.resolve())
    if not app_candidates:
        raise InstrumentationError(f"No {variant} application APK was found under {project}")
    if not test_candidates:
        raise InstrumentationError(f"No {variant} androidTest APK was found under {project}")
    app = max(app_candidates, key=lambda item: item.stat().st_mtime)
    test = max(test_candidates, key=lambda item: item.stat().st_mtime)
    return app, test


def _adb_resolution() -> object:
    provider = str(os.environ.get("DEVTOOL_ANDROID_ADB_PROVIDER") or "auto").strip() or "auto"
    explicit = str(os.environ.get("DEVTOOL_ANDROID_ADB_PATH") or "").strip() or None
    sdk_text = str(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or "").strip()
    sdk_root = Path(sdk_text).expanduser() if sdk_text else None
    return resolve_adb(sdk_root=sdk_root, provider=provider, explicit_path=explicit)


def _device_prefix(serial: str) -> list[str]:
    if not serial:
        return []
    provider = str(os.environ.get("DEVTOOL_ANDROID_ADB_PROVIDER") or "auto").strip() or "auto"
    explicit = str(os.environ.get("DEVTOOL_ANDROID_ADB_PATH") or "").strip() or None
    sdk_text = str(os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or "").strip()
    sdk_root = Path(sdk_text).expanduser() if sdk_text else None
    path = locate_adb_path(sdk_root=sdk_root, provider=provider, explicit_path=explicit)
    if path is None:
        raise InstrumentationError("ADB is required for --device-serial")
    return [str(path), "-s", serial]


def _connected_adb_devices() -> list[str]:
    resolution = _adb_resolution()
    if not getattr(resolution, "path", "") or getattr(resolution, "compatible", False) is False:
        return []
    manager = AndroidDeviceManager(resolution)
    try:
        return [device.serial for device in manager.list_devices() if device.usable]
    except AndroidDeviceError:
        return []


def _command_executable_available(command: str) -> bool:
    argv = shlex.split(command or "")

    if not argv:
        return False

    executable = argv[0]

    if "/" in executable:
        candidate = Path(executable)
        return candidate.is_file() and os.access(candidate, os.X_OK)

    return shutil.which(executable) is not None


def _resolve_execution_serial(
    configured_serial: str,
    *,
    install_command: str,
    am_command: str,
) -> str:
    configured = str(configured_serial or "").strip()

    if configured:
        return configured

    environment_serial = str(
        os.environ.get("ANDROID_SERIAL") or ""
    ).strip()

    if environment_serial:
        return environment_serial

    devices = _connected_adb_devices()

    if len(devices) == 1:
        return devices[0]

    if len(devices) > 1:
        raise InstrumentationError(
            "Multiple ADB devices are connected. "
            "Set android.device_serial or ANDROID_SERIAL."
        )

    # Permit genuinely available explicitly configured local commands.
    if (
        _command_executable_available(install_command)
        and _command_executable_available(am_command)
    ):
        return ""

    raise InstrumentationUnavailable(
        "No usable Android instrumentation target is available: "
        "no connected ADB device was found and no usable local "
        "privileged Android command is configured."
    )


def _resolve_local_command(command: Sequence[str]) -> list[str]:
    argv = [str(item) for item in command if str(item)]

    if not argv:
        raise InstrumentationUnavailable(
            "Local Android command is empty"
        )

    executable = argv[0]

    if "/" in executable:
        candidate = Path(executable)
        available = candidate.is_file() and os.access(candidate, os.X_OK)
    else:
        available = shutil.which(executable) is not None

    if available:
        return argv

    if Path(executable).name == "sudo":
        raise InstrumentationUnavailable(
            "Instrumentation is configured to use sudo, but sudo is "
            "unavailable. Devtool will not infer root access merely "
            "because Termux provides an su executable."
        )

    raise InstrumentationUnavailable(
        f"Configured Android command is unavailable: {executable}"
    )

def _install(apk: Path, *, serial: str, install_command: str) -> None:
    if serial:
        run([*_device_prefix(serial), "install", "-r", "-t", str(apk)])
        return
    command = shlex.split(install_command or "sudo pm install -r -t")
    run(_resolve_local_command([*command, str(apk)]))


def _local_pm_prefix(install_command: str) -> list[str]:
    command = shlex.split(install_command or "sudo pm install -r -t")
    try:
        index = command.index("install")
    except ValueError:
        return ["sudo", "pm"]
    prefix = command[:index]
    return prefix or ["sudo", "pm"]


def _pm_instrumentations(serial: str, *, install_command: str) -> str:
    if serial:
        result = run([*_device_prefix(serial), "shell", "pm", "list", "instrumentation"], check=False)
    else:
        result = run(
            _resolve_local_command(
                [*_local_pm_prefix(install_command), "list", "instrumentation"]
            ),
            check=False,
        )
    return result.stdout + result.stderr


def _choose_runner(text: str, *, configured: str, target_package: str) -> tuple[str, str]:
    entries = re.findall(r"instrumentation:([^\s]+)\s+\(target=([^\)]+)\)", text)
    if configured:
        configured_target = target_package
        if not configured_target:
            configured_target = next((target for runner, target in entries if runner == configured), "")
        return configured, configured_target
    if target_package:
        for runner, target in entries:
            if target == target_package:
                return runner, target
    if len(entries) == 1:
        return entries[0]
    if not entries:
        raise InstrumentationError("No installed instrumentation runner was reported by package manager")
    rendered = ", ".join(f"{runner} -> {target}" for runner, target in entries)
    raise InstrumentationError(f"Multiple instrumentation runners are installed; configure android.test_runner. Found: {rendered}")


def _instrument_command(serial: str, *, am_command: str) -> list[str]:
    if serial:
        return [*_device_prefix(serial), "shell", "am", "instrument"]
    # Availability is resolved once up front in _resolve_execution_serial.
    # Keep this helper as a pure argv builder so unit tests and diagnostics do
    # not depend on whether the host running the test happens to have sudo/pm.
    command = shlex.split(am_command or "sudo am instrument")
    return command or ["sudo", "am", "instrument"]


def _uninstall_test_package(package_name: str, *, serial: str, install_command: str) -> None:
    if not package_name:
        return
    if serial:
        run([*_device_prefix(serial), "uninstall", package_name], check=False)
    else:
        run(
            _resolve_local_command(
                [*_local_pm_prefix(install_command), "uninstall", package_name]
            ),
            check=False,
        )


def _parse_counts(text: str) -> dict[str, int]:
    # AndroidJUnitRunner normally exposes numtests and per-test status codes.
    numtests = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS:\s*numtests=(\d+)", text)]
    codes = [int(value) for value in re.findall(r"INSTRUMENTATION_STATUS_CODE:\s*(-?\d+)", text)]
    started = sum(1 for value in codes if value == 1)
    completed = sum(1 for value in codes if value == 0)
    failed = sum(1 for value in codes if value < 0)
    total = max(numtests or [started, completed + failed, 0])
    passed = max(0, completed - failed)
    if "FAILURES!!!" in text and failed == 0:
        failed = 1
        passed = max(0, completed - failed)
    return {
        "discovered": total,
        "selected": total,
        "executed": completed + failed,
        "passed": passed,
        "failed": failed,
        "skipped": 0,
        "total": total,
    }


def _parse_failed_ids(output: str) -> list[str]:
    current_class = ""
    current_test = ""
    failed: list[str] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if line.startswith("INSTRUMENTATION_STATUS: class="):
            current_class = line.split("=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_STATUS: test="):
            current_test = line.split("=", 1)[1].strip()
        elif line.startswith("INSTRUMENTATION_STATUS_CODE:"):
            try:
                code = int(line.split(":", 1)[1].strip())
            except ValueError:
                continue
            if code < 0 and current_test:
                value = f"{current_class}.{current_test}" if current_class else current_test
                failed.append(value)
    return list(dict.fromkeys(failed))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, install, and run Android instrumentation tests")
    parser.add_argument("project", type=Path)
    parser.add_argument("--variant", default="debug")
    parser.add_argument("--gradle", default="./gradlew")
    parser.add_argument("--aapt2", default="")
    parser.add_argument("--aapt2-provider", default="auto")
    parser.add_argument("--gradle-arg", action="append", default=[])
    parser.add_argument("--device-serial", default="")
    parser.add_argument("--adb-provider", choices=("auto", "sdk", "termux", "termux-adb", "path", "none"), default=os.environ.get("DEVTOOL_ANDROID_ADB_PROVIDER", "auto"))
    parser.add_argument("--adb-path", default=os.environ.get("DEVTOOL_ANDROID_ADB_PATH", ""))
    parser.add_argument("--install-command", default="sudo pm install -r -t")
    parser.add_argument("--am-command", default="sudo am instrument")
    parser.add_argument("--runner", default="")
    parser.add_argument("--target-package", default="")
    parser.add_argument("--class", dest="classes", action="append", default=[])
    parser.add_argument("--method", dest="methods", action="append", default=[])
    parser.add_argument("--annotation", action="append", default=[])
    parser.add_argument("--not-annotation", action="append", default=[])
    parser.add_argument("--keep-installed", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    return parser


def _write_skipped_result(
    project: Path,
    *,
    report: Path | None,
    runner: str,
    reason: str,
) -> int:
    report_path = report or (
        project
        / ".devtool"
        / "reports"
        / "android-instrumentation.json"
    )

    payload: dict[str, Any] = {
        "framework": "android-instrumentation",
        "reason": reason,
        "runner": runner,
        "test_package": (
            runner.split("/", 1)[0].strip()
            if "/" in runner
            else ""
        ),
        "returncode": 0,
        "discovered": 0,
        "selected": 0,
        "executed": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 1,
        "total": 1,
        "failed_ids": [],
    }

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(
            f"Instrumentation report write warning: {exc}",
            file=sys.stderr,
        )

    print(f"Instrumentation skipped: {reason}")
    print(
        "DEVTOOL_INSTRUMENTATION_RESULT "
        + json.dumps(payload, separators=(",", ":"))
    )

    return 0


def main(raw_args: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(raw_args)
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        print(f"Instrumentation project does not exist: {project}", file=sys.stderr)
        return 2
    variant_title = _variant_title(args.variant)
    installed_test_package = ""
    old_adb_provider = os.environ.get("DEVTOOL_ANDROID_ADB_PROVIDER")
    old_adb_path = os.environ.get("DEVTOOL_ANDROID_ADB_PATH")
    os.environ["DEVTOOL_ANDROID_ADB_PROVIDER"] = str(args.adb_provider or "auto")
    if args.adb_path:
        os.environ["DEVTOOL_ANDROID_ADB_PATH"] = str(args.adb_path)
    else:
        os.environ.pop("DEVTOOL_ANDROID_ADB_PATH", None)

    try:
        try:
            args.device_serial = _resolve_execution_serial(
                args.device_serial,
                install_command=args.install_command,
                am_command=args.am_command,
            )
        except InstrumentationUnavailable as exc:
            return _write_skipped_result(
                project,
                report=args.report,
                runner=args.runner,
                reason=str(exc),
            )

        if args.runner and "/" in args.runner:
            installed_test_package = args.runner.split("/", 1)[0].strip()

        if not args.skip_build:
            gradle = _resolve_gradle(project, args.gradle)
            aapt2 = _instrumentation_aapt2(
                project,
                configured=args.aapt2,
                provider=args.aapt2_provider,
            )
            gradle_arguments = android_build.gradle_arguments_with_aapt2_override(
                [
                    f"assemble{variant_title}",
                    f"assemble{variant_title}AndroidTest",
                    *args.gradle_arg,
                    "--console=plain",
                ],
                aapt2,
                plain_console=True,
            )
            run([str(gradle), *gradle_arguments], check=True)

        app_apk, test_apk = _find_apks(project, args.variant)
        print(f"DEVTOOL_EVENT {json.dumps({'kind': 'instrumentation-apks', 'app': str(app_apk), 'test': str(test_apk)})}")
        _install(app_apk, serial=args.device_serial, install_command=args.install_command)
        _install(test_apk, serial=args.device_serial, install_command=args.install_command)

        runner, resolved_target_package = _choose_runner(
            _pm_instrumentations(args.device_serial, install_command=args.install_command),
            configured=args.runner,
            target_package=args.target_package,
        )
        installed_test_package = runner.split("/", 1)[0]
        command = [*_instrument_command(args.device_serial, am_command=args.am_command), "-w", "-r"]
        selected_classes = list(args.classes)
        for method in args.methods:
            if "#" in method:
                selected_classes.append(method)
            elif "." in method:
                class_name, method_name = method.rsplit(".", 1)
                selected_classes.append(f"{class_name}#{method_name}")
            else:
                selected_classes.append(method)
        if selected_classes:
            command.extend(["-e", "class", ",".join(selected_classes)])
        if args.annotation:
            command.extend(["-e", "annotation", ",".join(args.annotation)])
        if args.not_annotation:
            command.extend(["-e", "notAnnotation", ",".join(args.not_annotation)])
        command.append(runner)
        result = run(command, check=False)
        output = result.stdout + "\n" + result.stderr
        counts = _parse_counts(output)
        payload: dict[str, Any] = {
            "framework": "android-instrumentation",
            "runner": runner,
            "target_package": resolved_target_package,
            "test_package": runner.split("/", 1)[0],
            "app_apk": str(app_apk),
            "test_apk": str(test_apk),
            **counts,
            "failed_ids": _parse_failed_ids(output),
            "returncode": result.returncode,
        }
        report = args.report or (project / ".devtool" / "reports" / "android-instrumentation.json")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"DEVTOOL_INSTRUMENTATION_RESULT {json.dumps(payload, separators=(',', ':'))}")
        if result.returncode != 0 or counts["failed"]:
            return result.returncode or 1
        return 0
    except InstrumentationError as exc:
        print(f"Instrumentation failed: {exc}", file=sys.stderr)
        report = args.report or (project / ".devtool" / "reports" / "android-instrumentation.json")
        payload = {
            "framework": "android-instrumentation",
            "error": str(exc),
            "runner": args.runner,
            "test_package": installed_test_package,
            "returncode": 1,
            "discovered": 0,
            "selected": 0,
            "executed": 0,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "total": 0,
            "failed_ids": [],
        }
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError as report_error:
            print(f"Instrumentation report write failed: {report_error}", file=sys.stderr)
        print(
            f"DEVTOOL_INSTRUMENTATION_RESULT "
            f"{json.dumps(payload, separators=(',', ':'))}"
        )
        return 1
    finally:
        if installed_test_package and not args.keep_installed:
            try:
                _uninstall_test_package(
                    installed_test_package,
                    serial=args.device_serial,
                    install_command=args.install_command,
                )
            except InstrumentationError as cleanup_error:
                print(
                    f"Instrumentation cleanup warning: {cleanup_error}",
                    file=sys.stderr,
                )
        if old_adb_provider is None:
            os.environ.pop("DEVTOOL_ANDROID_ADB_PROVIDER", None)
        else:
            os.environ["DEVTOOL_ANDROID_ADB_PROVIDER"] = old_adb_provider
        if old_adb_path is None:
            os.environ.pop("DEVTOOL_ANDROID_ADB_PATH", None)
        else:
            os.environ["DEVTOOL_ANDROID_ADB_PATH"] = old_adb_path


if __name__ == "__main__":
    raise SystemExit(main())
