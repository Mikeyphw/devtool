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


class InstrumentationError(RuntimeError):
    pass


def run(argv: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(list(argv), text=True, capture_output=True, check=False)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and result.returncode != 0:
        raise InstrumentationError(f"Command failed ({result.returncode}): {' '.join(shlex.quote(value) for value in argv)}")
    return result


def _variant_title(value: str) -> str:
    value = value.strip() or "debug"
    return value[:1].upper() + value[1:]


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


def _device_prefix(serial: str) -> list[str]:
    if serial:
        adb = shutil.which("adb")
        if not adb:
            raise InstrumentationError("adb is required for --device-serial")
        return [adb, "-s", serial]
    return []


def _install(apk: Path, *, serial: str, install_command: str) -> None:
    if serial:
        run([*_device_prefix(serial), "install", "-r", "-t", str(apk)])
        return
    command = shlex.split(install_command or "sudo pm install -r -t")
    run([*command, str(apk)])


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
        result = run([*_local_pm_prefix(install_command), "list", "instrumentation"], check=False)
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
    command = shlex.split(am_command or "sudo am instrument")
    return command or ["sudo", "am", "instrument"]


def _uninstall_test_package(package_name: str, *, serial: str, install_command: str) -> None:
    if not package_name:
        return
    if serial:
        run([*_device_prefix(serial), "uninstall", package_name], check=False)
    else:
        run([*_local_pm_prefix(install_command), "uninstall", package_name], check=False)


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
    parser.add_argument("--device-serial", default="")
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


def main(raw_args: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(raw_args)
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        print(f"Instrumentation project does not exist: {project}", file=sys.stderr)
        return 2
    variant_title = _variant_title(args.variant)
    installed_test_package = ""
    if args.runner and "/" in args.runner:
        installed_test_package = args.runner.split("/", 1)[0].strip()
    try:
        if not args.skip_build:
            gradle = Path(args.gradle)
            if not gradle.is_absolute():
                gradle = project / gradle
            if not gradle.exists():
                found = shutil.which(args.gradle)
                if found:
                    gradle = Path(found)
            run([str(gradle), f"assemble{variant_title}", f"assemble{variant_title}AndroidTest", "--console=plain"], check=True)

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
        return 1
    finally:
        if installed_test_package and not args.keep_installed:
            _uninstall_test_package(installed_test_package, serial=args.device_serial, install_command=args.install_command)


if __name__ == "__main__":
    raise SystemExit(main())
