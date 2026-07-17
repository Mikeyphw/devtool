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
    cmake = (root / "desktop" / "devtool-control-center" / "CMakeLists.txt").read_text(encoding="utf-8")
    gradle = (root / "android" / "devtool-live-hud" / "app" / "build.gradle").read_text(encoding="utf-8")
    control = (root / "desktop" / "devtool-control-center" / "packaging" / "debian" / "control").read_text(encoding="utf-8")
    return {
        "pyproject": _extract(r'^version\s*=\s*"([^"]+)"', pyproject, "pyproject version"),
        "python": _extract(r'__version__\s*=\s*"([^"]+)"', init, "Python version"),
        "desktop_cmake": _extract(r'project\(DevtoolControlCenter VERSION ([^ )]+)', cmake, "desktop CMake version"),
        "desktop_debian": _extract(r'^Version:\s*([^\s]+)', control, "Debian version"),
        "android_name": _extract(r"versionName\s+'([^']+)'", gradle, "Android version name"),
        "android_code": int(_extract(r'versionCode\s+(\d+)', gradle, "Android version code")),
    }


def validate_version_consistency(root: Path) -> list[str]:
    versions = load_versions(root)
    actual = version_inventory(root)
    expected = {
        "pyproject": versions["devtool"],
        "python": versions["devtool"],
        "desktop_cmake": versions["desktop"],
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


def qt_available() -> bool:
    if any(shutil.which(command) for command in ("qtpaths", "qmake", "qml", "qmlscene")):
        return True
    candidates = (
        Path("/usr/lib/x86_64-linux-gnu/cmake/Qt5/Qt5Config.cmake"),
        Path("/usr/lib/aarch64-linux-gnu/cmake/Qt5/Qt5Config.cmake"),
        Path("/usr/local/lib/cmake/Qt5/Qt5Config.cmake"),
        Path.home() / ".local/lib/cmake/Qt5/Qt5Config.cmake",
    )
    return any(path.is_file() for path in candidates)


def _run(gate: str, command: Sequence[str], root: Path, logs: Path, *, required: bool,
         timeout: int = 1800, environment: Mapping[str, str] | None = None) -> GateResult:
    import time

    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{gate}.log"
    started = time.monotonic()
    env = os.environ.copy()
    if environment:
        env.update(environment)
    try:
        completed = subprocess.run(
            list(command), cwd=root, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
        output = completed.stdout or ""
        log_path.write_text(output, encoding="utf-8")
        status = "passed" if completed.returncode == 0 else "failed"
        detail = output.strip().splitlines()[-1] if output.strip() else f"exit code {completed.returncode}"
    except subprocess.TimeoutExpired as error:
        output = (error.stdout or "") + (error.stderr or "")
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        log_path.write_text(str(output), encoding="utf-8")
        status = "failed"
        detail = f"timed out after {timeout} seconds"
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
        root / "desktop" / "devtool-control-center" / "build.sh",
        root / "desktop" / "devtool-control-center" / "package-deb.sh",
        root / "desktop" / "devtool-control-center" / "tests" / "run-headless-smoke.sh",
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
    report.results.append(_run("python-tests", [sys.executable, "-m", "pytest", "-q"], root, logs, required=True))
    shell_command = ["bash", "-n", *[str(path.relative_to(root)) for path in shell_entry_points(root)]]
    report.results.append(_run("shell-syntax", shell_command, root, logs, required=True))

    qt_build = root / "dist" / "qt-release-build"
    if include_qt and qt_available():
        report.results.append(_run(
            "qt-configure",
            ["cmake", "-S", "desktop/devtool-control-center", "-B", str(qt_build), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DDEVTOOL_ENABLE_TESTS=ON"],
            root, logs, required=require_qt,
        ))
        if report.results[-1].status == "passed":
            report.results.append(_run("qt-build", ["cmake", "--build", str(qt_build), "--parallel"], root, logs, required=require_qt))
            if report.results[-1].status == "passed":
                report.results.append(_run("qt-smoke", ["ctest", "--test-dir", str(qt_build), "--output-on-failure"], root, logs, required=require_qt))
            else:
                report.results.append(_skip("qt-smoke", "Qt build failed", required=require_qt))
        else:
            report.results.extend([
                _skip("qt-build", "Qt configuration failed", required=require_qt),
                _skip("qt-smoke", "Qt configuration failed", required=require_qt),
            ])
    elif include_qt:
        detail = "Qt 5 development files were not detected"
        report.results.extend([_skip("qt-configure", detail, required=require_qt), _skip("qt-build", detail, required=require_qt), _skip("qt-smoke", detail, required=require_qt)])

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
