from __future__ import annotations

"""Structured project/environment health checks used by ``devtool doctor``."""

from dataclasses import asdict, dataclass
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .models import ProjectConfig
from .features import resolve_feature_flags

@dataclass(slots=True)
class DoctorCheck:
    section: str
    name: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version(executable: str, *args: str) -> str:
    try:
        proc = subprocess.run([executable, *args], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    text = (proc.stdout + "\n" + proc.stderr).strip()
    return text.splitlines()[0] if text else f"exit {proc.returncode}"


def _package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


def _latest_dir(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    dirs = [item for item in path.iterdir() if item.is_dir()]
    return sorted(dirs, key=lambda p: p.name)[-1] if dirs else None


def collect_doctor_checks(config: ProjectConfig) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    python = shutil.which(config.python_executable) or shutil.which("python3")
    checks.append(DoctorCheck("runtime", "python", "ok" if python else "missing", _version(python, "--version") if python else config.python_executable))
    for package in ("pip", "setuptools", "wheel"):
        value = _package_version(package)
        checks.append(DoctorCheck("runtime", package, "ok" if value != "missing" else "missing", value))

    java = shutil.which("java")
    gradle = shutil.which("gradle")
    cmake = shutil.which("cmake")
    checks.append(DoctorCheck("android", "java", "ok" if java else "missing", _version(java, "-version") if java else "not on PATH"))
    checks.append(DoctorCheck("android", "gradle", "ok" if gradle else "optional", _version(gradle, "--version") if gradle else "wrapper/project provider may be used"))
    checks.append(DoctorCheck("android", "cmake", "ok" if cmake else "optional", _version(cmake, "--version") if cmake else "not on PATH"))

    sdk_text = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME") or ""
    sdk = Path(sdk_text).expanduser() if sdk_text else None
    sdk_ok = bool(sdk and sdk.is_dir())
    checks.append(DoctorCheck("android", "sdk", "ok" if sdk_ok else "optional", str(sdk) if sdk else "ANDROID_SDK_ROOT/ANDROID_HOME unset"))
    build_tools = _latest_dir(sdk / "build-tools") if sdk_ok and sdk else None
    ndk = _latest_dir(sdk / "ndk") if sdk_ok and sdk else None
    checks.append(DoctorCheck("android", "build-tools", "ok" if build_tools else "optional", str(build_tools) if build_tools else "not discovered"))
    checks.append(DoctorCheck("android", "ndk", "ok" if ndk else "optional", str(ndk) if ndk else "not discovered"))

    adb = shutil.which("adb")
    devices = ""
    device_status = "optional"
    if adb:
        try:
            proc = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5, check=False)
            lines = [line.strip() for line in proc.stdout.splitlines()[1:] if line.strip()]
            devices = ", ".join(lines) if lines else "no devices"
            device_status = "ok" if lines else "optional"
        except (OSError, subprocess.TimeoutExpired) as exc:
            devices = str(exc)
    else:
        devices = "adb not on PATH"
    checks.append(DoctorCheck("device", "adb", "ok" if adb else "optional", adb or "not on PATH"))
    checks.append(DoctorCheck("device", "devices", device_status, devices))

    su = shutil.which("su")
    root_detail = "su unavailable"
    root_status = "optional"
    if su:
        try:
            proc = subprocess.run([su, "-c", "id -u"], capture_output=True, text=True, timeout=3, check=False)
            root_detail = (proc.stdout or proc.stderr).strip() or f"exit {proc.returncode}"
            root_status = "ok" if proc.returncode == 0 and proc.stdout.strip() == "0" else "optional"
        except (OSError, subprocess.TimeoutExpired) as exc:
            root_detail = str(exc)
    checks.append(DoctorCheck("device", "root", root_status, root_detail))

    checks.append(DoctorCheck("project", "git", "ok" if (config.repo_root / ".git").exists() else "missing", str(config.repo_root / ".git")))
    checks.append(DoctorCheck("project", "config", "ok" if config.config_path else "optional", str(config.config_path) if config.config_path else "using detected defaults"))
    target = config.build_target or config.project_root or str(config.repo_root)
    checks.append(DoctorCheck("project", "build-target", "ok" if target else "optional", target or "not configured"))
    return checks


def doctor_payload(config: ProjectConfig) -> dict[str, Any]:
    checks = collect_doctor_checks(config)
    features = resolve_feature_flags(config.repo_root)
    return {
        "schema_version": 1,
        "repository": str(config.repo_root),
        "kind": config.kind,
        "healthy": not any(item.status == "missing" for item in checks),
        "features": features.to_dict(),
        "checks": [item.as_dict() for item in checks],
    }
