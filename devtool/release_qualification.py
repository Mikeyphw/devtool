from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Iterable, Mapping, Sequence


@dataclass(slots=True)
class GateResult:
    gate: str
    status: str
    required: bool
    command: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    detail: str = ""
    log_path: str = ""


@dataclass(slots=True)
class QualificationReport:
    release: str
    started_at: str
    finished_at: str = ""
    repository: str = ""
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(result.status == "passed" or (result.status == "skipped" and not result.required)
                   for result in self.results)

    def to_dict(self) -> dict[str, object]:
        return {
            "release": self.release,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "repository": self.repository,
            "passed": self.passed,
            "results": [asdict(result) for result in self.results],
        }


def repository_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "release" / "versions.json").is_file():
            return candidate
    raise FileNotFoundError("Could not locate the devtool repository root")


def load_versions(root: Path) -> dict[str, object]:
    return json.loads((root / "release" / "versions.json").read_text(encoding="utf-8"))


def _extract(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise ValueError(f"Could not find {label}")
    return match.group(1)


def version_inventory(root: Path) -> dict[str, str | int]:
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    init = (root / "devtool" / "__init__.py").read_text(encoding="utf-8")
    gtk_control = (root / "desktop" / "gtk-control-center" / "packaging" / "debian" / "control").read_text(encoding="utf-8")
    gradle = (root / "android" / "devtool-live-hud" / "app" / "build.gradle").read_text(encoding="utf-8")
    return {
        "pyproject": _extract(r'^version\s*=\s*"([^"]+)"', pyproject, "pyproject version"),
        "python": _extract(r'__version__\s*=\s*"([^"]+)"', init, "Python version"),
        "desktop_debian": _extract(r'^Version:\s*([^\s]+)', gtk_control, "GTK4 Debian version"),
        "android_name": _extract(r"versionName\s+'([^']+)'", gradle, "Android version name"),
        "android_code": int(_extract(r'versionCode\s+(\d+)', gradle, "Android version code")),
    }


def validate_version_consistency(root: Path) -> list[str]:
    versions = load_versions(root)
    actual = version_inventory(root)
    expected = {
        "pyproject": versions["devtool"],
        "python": versions["devtool"],
        "desktop_debian": versions["desktop"],
        "android_name": versions["android"]["version_name"],
        "android_code": versions["android"]["version_code"],
    }
    return [f"{key}: expected {expected[key]!r}, found {actual[key]!r}"
            for key in expected if actual[key] != expected[key]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def android_sdk_available(environment: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environment is None else environment
    sdk = env.get("ANDROID_SDK_ROOT") or env.get("ANDROID_HOME")
    if not sdk:
        return False
    root = Path(sdk)
    return (root / "platforms" / "android-36").is_dir() and (root / "build-tools" / "36.0.0").is_dir()


def gtk4_available() -> bool:
    from .desktop.gtk_smoke import gtk_runtime_import_probe

    ok, _detail = gtk_runtime_import_probe(sys.executable)
    return ok


def desktop_available() -> bool:
    from .desktop.gtk_smoke import has_display, has_xvfb

    return gtk4_available() and (has_display() or has_xvfb())


def qt_available() -> bool:
    # Backward-compatible API alias. It no longer probes Qt; desktop is GTK4-only.
    return desktop_available()


def _run(gate: str, command: Sequence[str], root: Path, logs: Path, *, required: bool,
         timeout: int = 1800, environment: Mapping[str, str] | None = None) -> GateResult:
    import signal
    import time

    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{gate}.log"
    started = time.monotonic()
    env = os.environ.copy()
    if environment:
        env.update(environment)
    status = "failed"
    detail = "gate did not run"
    print(f"[release] starting {gate}", flush=True)
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            list(command), cwd=root, env=env, text=True,
            stdout=log_handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
            status = "passed" if returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            status = "failed"
            detail = f"timed out after {timeout} seconds"
    output = log_path.read_text(encoding="utf-8", errors="replace")
    if not detail.startswith("timed out"):
        detail = output.strip().splitlines()[-1] if output.strip() else ("passed" if status == "passed" else "command failed")
    print(f"[release] {gate}: {status} ({round(time.monotonic() - started, 3)}s)", flush=True)
    return GateResult(
        gate=gate,
        status=status,
        required=required,
        command=list(command),
        duration_seconds=round(time.monotonic() - started, 3),
        detail=detail,
        log_path=str(log_path),
    )


def _skip(gate: str, detail: str, *, required: bool = False) -> GateResult:
    return GateResult(gate=gate, status="failed" if required else "skipped", required=required, detail=detail)


def shell_entry_points(root: Path) -> list[Path]:
    paths = [
        root / "install.sh", root / "uninstall.sh", root / "build-android-hud.sh",
        root / "build-root-bridge-module.sh", root / "build-apk",
        root / "android" / "devtool-live-hud" / "build.sh",
        root / "android" / "devtool-live-hud" / "gradlew",
        root / "desktop" / "gtk-control-center" / "build.sh",
        root / "desktop" / "gtk-control-center" / "package-deb.sh",
        root / "tools" / "make-release-candidate.sh",
    ]
    return [path for path in paths if path.is_file()]


def qualify(root: Path, *, include_qt: bool = True, include_android: bool = True,
            require_qt: bool = False, require_android: bool = False,
            run_device_tests: bool = False, logs_dir: Path | None = None) -> QualificationReport:
    root = root.resolve()
    versions = load_versions(root)
    logs = (logs_dir or root / "dist" / "qualification-logs").resolve()
    report = QualificationReport(
        release=str(versions["release"]),
        started_at=datetime.now(timezone.utc).isoformat(),
        repository=str(root),
    )

    errors = validate_version_consistency(root)
    report.results.append(GateResult(
        gate="versions", status="passed" if not errors else "failed", required=True,
        detail="versions are consistent" if not errors else "; ".join(errors),
    ))
    report.results.append(_run("python-compile", [sys.executable, "-m", "compileall", "-q", "devtool", "tools"], root, logs, required=True))
    for group in (3, 1, 2, 4):
        report.results.append(_run(
            f"python-tests-{group}",
            [sys.executable, "tools/run-pytest-split.py", "--groups", "4", "--group", str(group), "--group-timeout", "180", "--retries", "1"],
            root, logs, required=True, timeout=420,
        ))
    report.results.append(_run(
        "validation-release",
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_validation_reports_phase32.py",
         "tests/test_phase33_validation_release_seal.py",
         "tests/test_validation_sessions.py",
         "tests/test_validation_phase25_27.py",
         "tests/test_validation_control_api.py",
         "tests/test_validation_phase28.py",
         "tests/test_phase31_1_validation_completion.py"],
        root, logs, required=True,
    ))
    report.results.append(_run(
        "android-validation-source",
        [sys.executable, "-m", "pytest", "-q",
         "tests/test_android_validation_workbench_phase29_31.py",
         "tests/test_phase31_1_validation_completion.py",
         "tests/test_android_notification_mock.py",
         "tests/test_android_app_source.py"],
        root, logs, required=True,
    ))
    shell_command = ["bash", "-n", *[str(path.relative_to(root)) for path in shell_entry_points(root)]]
    report.results.append(_run("shell-syntax", shell_command, root, logs, required=True))

    gtk_package = root / "desktop" / "gtk-control-center" / "package-deb.sh"
    if include_qt:
        from .desktop.gtk_smoke import (
            control_center_startup_command,
            gtk_runtime_import_probe,
            hud_startup_command,
            run_gtk_constrained_layout_test,
            run_gtk_startup_test,
        )

        gtk_import_ok, gtk_import_detail = gtk_runtime_import_probe(sys.executable)
        report.results.append(GateResult(
            gate="desktop-gtk-imports",
            status="passed" if gtk_import_ok else ("failed" if require_qt else "skipped"),
            required=require_qt,
            detail="GTK4/libadwaita/Rich imports are available" if gtk_import_ok else gtk_import_detail,
        ))
        if gtk_import_ok:
            report.results.append(_run(
                "desktop-gtk-model-snapshot",
                [sys.executable, "-m", "devtool", "control-center", "--backend", "gtk4", "--once"],
                root, logs, required=require_qt, timeout=60,
            ))
            for probe in (
                run_gtk_startup_test(
                    "desktop-gtk-control-center-startup",
                    control_center_startup_command(repo=root, python=sys.executable),
                    root=root,
                    logs_dir=logs,
                    required=require_qt,
                    timeout=45,
                    geometry="1280x720x24",
                ),
                run_gtk_startup_test(
                    "desktop-gtk-hud-startup",
                    hud_startup_command(state_dir=root / ".devtool" / "gtk-startup-test", python=sys.executable),
                    root=root,
                    logs_dir=logs,
                    required=require_qt,
                    timeout=45,
                    geometry="800x720x24",
                ),
                run_gtk_constrained_layout_test(
                    "desktop-gtk-control-center-constrained-layout",
                    control_center_startup_command(repo=root, python=sys.executable),
                    root=root,
                    logs_dir=logs,
                    required=require_qt,
                    timeout=45,
                ),
                run_gtk_constrained_layout_test(
                    "desktop-gtk-hud-constrained-layout",
                    hud_startup_command(state_dir=root / ".devtool" / "gtk-startup-test", python=sys.executable),
                    root=root,
                    logs_dir=logs,
                    required=require_qt,
                    timeout=45,
                ),
            ):
                report.results.append(GateResult(
                    gate=probe.name,
                    status=probe.status,
                    required=probe.required,
                    command=probe.wrapped_command or probe.command,
                    detail=probe.detail,
                    log_path=probe.log_path,
                ))
        else:
            for gate in ("desktop-gtk-model-snapshot", "desktop-gtk-control-center-startup", "desktop-gtk-hud-startup", "desktop-gtk-control-center-constrained-layout", "desktop-gtk-hud-constrained-layout"):
                report.results.append(_skip(gate, "GTK4/libadwaita Python bindings were not detected", required=require_qt))
        if gtk_import_ok or require_qt:
            report.results.append(_run(
                "desktop-gtk-package",
                ["bash", str(gtk_package.relative_to(root)), "--verify"],
                root, logs, required=require_qt, timeout=180,
            ))
        else:
            report.results.append(_skip(
                "desktop-gtk-package",
                "GTK4/libadwaita Python bindings were not detected; package import verification skipped",
                required=require_qt,
            ))

    android_root = root / "android" / "devtool-live-hud"
    if include_android and android_sdk_available():
        gradle = str(android_root / "gradlew")
        report.results.append(_run("android-lint", [gradle, "--no-daemon", "lintRelease"], android_root, logs, required=require_android))
        report.results.append(_run("android-unit", [gradle, "--no-daemon", "testReleaseUnitTest"], android_root, logs, required=require_android))
        signing_ready = all(os.environ.get(name) for name in (
            "DEVTOOL_RELEASE_STORE_FILE", "DEVTOOL_RELEASE_STORE_PASSWORD",
            "DEVTOOL_RELEASE_KEY_ALIAS", "DEVTOOL_RELEASE_KEY_PASSWORD",
        ))
        if signing_ready:
            report.results.append(_run("android-release", [gradle, "--no-daemon", "assembleRelease", "verifyReleaseApk"], android_root, logs, required=require_android))
        else:
            report.results.append(_skip("android-release", "release signing environment is incomplete", required=require_android))
        if run_device_tests:
            adb = shutil.which("adb")
            if adb:
                report.results.append(_run("android-instrumentation", [gradle, "--no-daemon", "connectedDebugAndroidTest"], android_root, logs, required=require_android))
            else:
                report.results.append(_skip("android-instrumentation", "adb is unavailable", required=require_android))
    elif include_android:
        detail = "Android SDK platform 36 and build-tools 36.0.0 were not detected"
        for gate in ("android-lint", "android-unit", "android-release"):
            report.results.append(_skip(gate, detail, required=require_android))
        if run_device_tests:
            report.results.append(_skip("android-instrumentation", detail, required=require_android))

    report.results.append(_run(
        "source-archive",
        [sys.executable, "tools/build-release.py", "--root", str(root), "--out-dir", "dist/release", "--verify-reproducible"],
        root, logs, required=True,
    ))
    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report


def write_report(report: QualificationReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
