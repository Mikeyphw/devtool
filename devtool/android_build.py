#!/usr/bin/env python3
"""Smart, reusable Gradle project builder.

Designed for ordinary Linux hosts and ARM64 Ubuntu chroots running through
Termux. It prefers a project's Gradle wrapper, reuses existing Java/Android SDK
installations, installs only missing SDK packages when sdkmanager is available,
can obtain a native AAPT2 from ReVanced releases on ARM hosts, and repairs the
remaining ARM64-incompatible SDK build-tools from Commit451 releases.

Examples:
    python3 gradle-build.py ~/Code/my-app
    python3 gradle-build.py . --aapt2 /sdcard/Download/aapt2-arm64-v8a.bin
    python3 gradle-build.py . -A revanced -o /sdcard/Download
    python3 gradle-build.py . --task test --task assembleDebug -- --info
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, MutableMapping, Sequence

APP_NAME = "gradle-build"
DEFAULT_GRADLE_VERSION = "8.13"
REVANCED_REPOSITORY = "ReVanced/aapt2"
REVANCED_API = f"https://api.github.com/repos/{REVANCED_REPOSITORY}"
ARM_BUILD_TOOLS_REPOSITORY = "Commit451/android-arm-build-tools"
ARM_BUILD_TOOLS_RELEASE_ALIASES = {"35.0.0": "35.0.1"}
ARM_BUILD_TOOLS_NATIVE_NAMES = ("aidl", "zipalign", "split-select")
NDK_HOST_PROVIDERS = {"auto", "google", "arm64", "qemu", "none"}
QEMU_INSTALL_POLICIES = {"auto", "never"}
QEMU_X86_64_NAMES = ("qemu-x86_64", "qemu-x86_64-static", "qemu-user-x86_64")
DEFAULT_QEMU_X86_64_SYSROOT_URL = "https://cdimages.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz"
DEFAULT_QEMU_X86_64_SYSROOT_SHA256 = "c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58"
ANDROID_CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-linux-14742923_latest.zip"
AAPT2_OVERRIDE_PROPERTY = "android.aapt2FromMavenOverride"
UNSUPPORTED_OPTION_WARNINGS_PROPERTY = "android.suppressUnsupportedOptionWarnings"
BOOTSTRAP_DOWNLOAD_POLICIES = {"never", "missing", "auto", "force"}
BOOTSTRAP_NETWORK_POLICIES = {"deny", "allow"}


class BuildError(RuntimeError):
    """User-actionable build failure."""


def heading(message: str) -> None:
    print(f"\n\033[1;34m==> {message}\033[0m", flush=True)


def info(message: str) -> None:
    print(f"    {message}", flush=True)


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr, flush=True)


def fail(message: str) -> "None":
    raise BuildError(message)


def normalize_bootstrap_downloads(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    if normalized in {"off", "false", "disabled", "disable", "no", "none"}:
        normalized = "never"
    if normalized not in BOOTSTRAP_DOWNLOAD_POLICIES:
        valid = ", ".join(sorted(BOOTSTRAP_DOWNLOAD_POLICIES))
        fail(f"Unsupported bootstrap download policy: {value}. Expected one of: {valid}")
    return normalized


def normalize_bootstrap_network(value: str | None) -> str:
    normalized = str(value or "allow").strip().lower() or "allow"
    if normalized in {"off", "false", "disabled", "disable", "no", "none"}:
        normalized = "deny"
    if normalized not in BOOTSTRAP_NETWORK_POLICIES:
        valid = ", ".join(sorted(BOOTSTRAP_NETWORK_POLICIES))
        fail(f"Unsupported bootstrap network policy: {value}. Expected one of: {valid}")
    return normalized


def bootstrap_provisioning_allowed(downloads: str, network: str) -> bool:
    return normalize_bootstrap_network(network) == "allow" and normalize_bootstrap_downloads(downloads) != "never"


def fail_bootstrap_blocked(component: str, *, downloads: str, network: str, checked: Sequence[str] = ()) -> "None":
    checked_lines = "\n".join(f"- {item}" for item in checked if item)
    checked_block = f"\n\nChecked:\n{checked_lines}" if checked_lines else ""
    fail(
        f"{component} location is unknown or incomplete.\n\n"
        f"Download policy: {normalize_bootstrap_downloads(downloads)}\n"
        f"Network policy: {normalize_bootstrap_network(network)}"
        f"{checked_block}\n\n"
        "Fix:\n"
        "- set [chroots.<name>.android] or [android] toolchain paths in ~/.config/devtool/config.toml\n"
        "- or pass --android-sdk-root, --gradle-command, or --java-home\n"
        "- or change [bootstrap].downloads to \"missing\" or \"auto\" with [bootstrap].network = \"allow\""
    )


def shell_join(parts: Sequence[str | os.PathLike[str]]) -> str:
    import shlex

    return " ".join(shlex.quote(str(part)) for part in parts)


def _memory_snapshot() -> tuple[int | None, int | None, int | None, float | None, float | None]:
    """Return MemAvailable, SwapFree, SwapTotal and memory PSI avg10 values."""

    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in {"MemAvailable", "SwapFree", "SwapTotal"}:
                continue
            values[key] = max(0, int(remainder.strip().split()[0]) // 1024)
    except (OSError, ValueError, IndexError):
        pass

    psi_some: float | None = None
    psi_full: float | None = None
    try:
        for line in Path("/proc/pressure/memory").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            kind, _, payload = line.partition(" ")
            fields = {
                key: value
                for token in payload.split()
                if "=" in token
                for key, value in [token.split("=", 1)]
            }
            if kind == "some" and "avg10" in fields:
                psi_some = float(fields["avg10"])
            elif kind == "full" and "avg10" in fields:
                psi_full = float(fields["avg10"])
    except (OSError, ValueError):
        pass

    return (
        values.get("MemAvailable"),
        values.get("SwapFree"),
        values.get("SwapTotal"),
        psi_some,
        psi_full,
    )


def _available_memory_mb() -> int | None:
    return _memory_snapshot()[0]


def _memory_guard_limits(memory_guard_mb: int) -> tuple[int, int]:
    """Return adaptive sustained-pressure and hard-emergency thresholds.

    ``memory_guard_mb`` is an early-warning/reclaim threshold, not an abort
    threshold. Kotlin/KSP can remain healthy below it while page cache and zram
    absorb the burst, so sustained aborts require corroborating swap, PSI, or
    downward-trend evidence.
    """

    sustained = max(320, min(memory_guard_mb - 96, int(memory_guard_mb * 0.70)))
    hard = max(192, min(sustained - 96, int(memory_guard_mb * 0.50)))
    return sustained, hard


def _critical_memory_pressure(
    snapshot: tuple[int | None, int | None, int | None, float | None, float | None],
    history: Sequence[int],
    sustained_limit: int,
) -> tuple[bool, str]:
    """Require corroborating pressure signals before a sustained abort."""

    available, swap_free, swap_total, psi_some, psi_full = snapshot
    if available is None or available >= sustained_limit:
        return False, ""

    swap_exhausted = False
    if swap_total is not None:
        swap_reserve = max(64, int(swap_total * 0.05))
        swap_exhausted = swap_total <= 0 or (swap_free or 0) <= swap_reserve

    worsening = len(history) >= 8 and (history[0] - history[-1]) >= 64
    psi_stalled = (psi_full is not None and psi_full >= 1.0) or (
        psi_some is not None and psi_some >= 25.0
    )
    telemetry_missing = swap_total is None and psi_some is None and psi_full is None
    critical = (
        (swap_exhausted and (psi_stalled or worsening))
        or (psi_stalled and worsening)
        or (telemetry_missing and worsening)
    )

    signals: list[str] = []
    if swap_total is not None:
        signals.append(f"swap {swap_free or 0}/{swap_total} MiB free")
    if psi_some is not None:
        signals.append(f"PSI some avg10={psi_some:.2f}")
    if psi_full is not None:
        signals.append(f"PSI full avg10={psi_full:.2f}")
    if len(history) >= 2:
        signals.append(f"trend {history[-1] - history[0]:+d} MiB")
    return critical, ", ".join(signals)


def _try_root_cache_reclaim() -> bool:
    """Drop clean filesystem caches once when root is available.

    This deliberately uses ``drop_caches=1`` only. It does not kill Android
    applications, alter swap, or drop inode/dentry caches.
    """

    if os.name != "posix":
        return False
    command = "sync; echo 1 > /proc/sys/vm/drop_caches"
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.sync()
            Path("/proc/sys/vm/drop_caches").write_text("1\n", encoding="ascii")
            return True
    except OSError:
        pass

    candidates: list[list[str]] = []
    su = shutil.which("su")
    if su:
        candidates.append([su, "-c", command])
    sudo = shutil.which("sudo")
    shell = shutil.which("sh") or "/bin/sh"
    if sudo:
        candidates.append([sudo, "-n", shell, "-c", command])
    for candidate in candidates:
        try:
            result = subprocess.run(
                candidate,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            return True
    return False


def _limit_cpu_command(command: list[str], cpu_limit: int) -> list[str]:
    if cpu_limit <= 0:
        return list(command)
    taskset = shutil.which("taskset")
    if not taskset:
        return list(command)
    try:
        available = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        available = list(range(max(1, os.cpu_count() or 1)))
    selected = available[: max(1, min(cpu_limit, len(available)))]
    cpu_list = ",".join(str(cpu) for cpu in selected)
    return [taskset, "--cpu-list", cpu_list, *command]


def _lower_priority_command(command: list[str]) -> list[str]:
    result = list(command)
    nice = shutil.which("nice")
    if nice:
        result = [nice, "-n", "10", *result]
    ionice = shutil.which("ionice")
    if ionice:
        result = [ionice, "-c", "3", *result]
    return result


def run(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = False,
    input_text: str | None = None,
    dry_run: bool = False,
    quiet: bool = False,
    memory_guard_mb: int = 0,
    low_priority: bool = False,
    cpu_limit: int = 0,
) -> subprocess.CompletedProcess[str]:
    rendered = [str(part) for part in command]
    if not quiet:
        print("+", shell_join(rendered), flush=True)
    if dry_run:
        return subprocess.CompletedProcess(rendered, 0, "", "")
    if low_priority:
        rendered = _lower_priority_command(rendered)
    if cpu_limit > 0:
        rendered = _limit_cpu_command(rendered, cpu_limit)
    try:
        if memory_guard_mb > 0 and not capture and input_text is None:
            proc = subprocess.Popen(
                rendered,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env else None,
                text=True,
                start_new_session=(os.name == "posix"),
            )
            sustained_limit, hard_limit = _memory_guard_limits(memory_guard_mb)
            pressure_samples = 0
            critical_samples = 0
            hard_samples = 0
            reclaim_attempted = False
            guard_tripped = False
            memory_history: deque[int] = deque(maxlen=12)
            pressure_details = ""
            try:
                while proc.poll() is None:
                    snapshot = _memory_snapshot()
                    available = snapshot[0]
                    if available is not None:
                        memory_history.append(available)
                    pressure_samples = (
                        pressure_samples + 1
                        if available is not None and available < memory_guard_mb
                        else 0
                    )
                    critical, pressure_details = _critical_memory_pressure(
                        snapshot, tuple(memory_history), sustained_limit
                    )
                    critical_samples = critical_samples + 1 if critical else 0
                    hard_samples = (
                        hard_samples + 1
                        if available is not None and available < hard_limit
                        else 0
                    )
                    if pressure_samples >= 4 and not reclaim_attempted:
                        reclaim_attempted = True
                        before = available
                        if _try_root_cache_reclaim():
                            time.sleep(0.25)
                            snapshot = _memory_snapshot()
                            available = snapshot[0]
                            warn(
                                "Memory pressure triggered one root cache reclaim "
                                f"({before} MiB before; {available} MiB after)."
                            )
                            if available is not None:
                                memory_history.append(available)
                            if available is not None and available >= memory_guard_mb:
                                pressure_samples = 0
                                critical_samples = 0
                                hard_samples = 0
                            elif available is not None and available >= sustained_limit:
                                critical_samples = 0
                                hard_samples = 0
                    if hard_samples >= 3 or critical_samples >= 10:
                        guard_tripped = True
                        reason = (
                            "hard emergency floor"
                            if hard_samples >= 3
                            else "corroborated sustained pressure"
                        )
                        detail_suffix = f"; {pressure_details}" if pressure_details else ""
                        warn(
                            f"Emergency memory guard stopped Gradle after {reason}: "
                            f"{available} MiB available; configured warning guard {memory_guard_mb} MiB; "
                            f"adaptive sustained threshold {sustained_limit} MiB; hard floor {hard_limit} MiB"
                            f"{detail_suffix}."
                        )
                        try:
                            if os.name == "posix":
                                os.killpg(proc.pid, 15)
                            else:
                                proc.terminate()
                        except (OSError, ProcessLookupError):
                            pass
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            try:
                                if os.name == "posix":
                                    os.killpg(proc.pid, 9)
                                else:
                                    proc.kill()
                            except (OSError, ProcessLookupError):
                                pass
                        break
                    time.sleep(1.0)
            except KeyboardInterrupt:
                try:
                    if os.name == "posix":
                        os.killpg(proc.pid, 15)
                    else:
                        proc.terminate()
                except (OSError, ProcessLookupError):
                    pass
                raise
            returncode = 75 if guard_tripped else int(proc.wait())
            result = subprocess.CompletedProcess(rendered, returncode, "", "")
        else:
            result = subprocess.run(
                rendered,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env else None,
                text=True,
                input=input_text,
                capture_output=capture,
                check=False,
            )
    except PermissionError as exc:
        fail(
            f"Permission denied while launching {rendered[0]}. The executable may "
            "be on a noexec mount such as /sdcard. Copy it under your Linux home "
            f"directory or use --aapt2 so the builder stages it automatically.\n{exc}"
        )
    except FileNotFoundError as exc:
        fail(f"Command not found: {rendered[0]}\n{exc}")

    if capture and not quiet:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and result.returncode != 0:
        details = ""
        if capture:
            combined = "\n".join(
                item.strip() for item in (result.stdout, result.stderr) if item and item.strip()
            )
            if combined:
                details = f"\n{combined}"
        fail(f"Command failed ({result.returncode}): {shell_join(rendered)}{details}")
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_executable(path: Path, label: str) -> Path:
    if not path.is_file():
        fail(f"{label} not found: {path}")
    mode = path.stat().st_mode
    bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if not mode & bits:
        try:
            path.chmod(mode | bits)
            info(f"Repaired executable permissions: {path}")
        except OSError as exc:
            fail(f"Could not make {label} executable: {path}\n{exc}")
    return path


def format_transfer_size(byte_count: int) -> str:
    value = float(max(0, byte_count))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1024.0 or candidate == units[-1]:
            break
        value /= 1024.0
    precision = 0 if unit == "B" else 1
    return f"{value:.{precision}f} {unit}"


def download(
    url: str,
    destination: Path,
    *,
    offline: bool,
    force: bool = False,
    headers: Mapping[str, str] | None = None,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0 and not force:
        info(f"Using cached download: {destination}")
        return destination
    if offline:
        fail(f"Required download is not cached and --offline was used:\n{url}")

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    request_headers = {"User-Agent": f"{APP_NAME}/1.0"}
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers)
    heading(f"Downloading {url}")
    info(f"Destination: {destination}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            raw_total = response.headers.get("Content-Length") if response.headers else None
            try:
                total = max(0, int(raw_total)) if raw_total else 0
            except (TypeError, ValueError):
                total = 0

            downloaded = 0
            started = time.monotonic()
            last_reported_bucket = -1
            last_reported_at = started
            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                elapsed = max(now - started, 0.001)
                rate = int(downloaded / elapsed)

                if total > 0:
                    percent = min(100, int(downloaded * 100 / total))
                    bucket = min(100, (percent // 5) * 5)
                    should_report = (
                        bucket > last_reported_bucket
                        or downloaded >= total
                        or now - last_reported_at >= 5.0
                    )
                    if should_report:
                        info(
                            "Download progress: "
                            f"{percent:3d}% "
                            f"({format_transfer_size(downloaded)} / {format_transfer_size(total)}, "
                            f"{format_transfer_size(rate)}/s)"
                        )
                        last_reported_bucket = bucket
                        last_reported_at = now
                elif now - last_reported_at >= 5.0:
                    info(
                        "Download progress: "
                        f"{format_transfer_size(downloaded)} "
                        f"({format_transfer_size(rate)}/s)"
                    )
                    last_reported_at = now

            elapsed = max(time.monotonic() - started, 0.001)
            if total <= 0:
                info(
                    "Download complete: "
                    f"{format_transfer_size(downloaded)} "
                    f"({format_transfer_size(int(downloaded / elapsed))}/s)"
                )
            elif downloaded < total:
                warn(
                    "Download server reported "
                    f"{format_transfer_size(total)}, but received {format_transfer_size(downloaded)}"
                )
    except (OSError, urllib.error.URLError) as exc:
        temporary.unlink(missing_ok=True)
        fail(f"Download failed: {url}\n{exc}")
    temporary.replace(destination)
    return destination


def github_json(url: str, *, offline: bool) -> dict:
    if offline:
        fail("GitHub release metadata is unavailable in --offline mode unless AAPT2 is cached.")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/1.0", **headers})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        fail(f"Could not query GitHub release metadata: {url}\n{exc}")


def extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for member in package.infolist():
            extracted = Path(package.extract(member, destination))
            archived_mode = (member.external_attr >> 16) & 0o7777
            if archived_mode and extracted.exists():
                try:
                    extracted.chmod(archived_mode)
                except OSError:
                    pass


def extract_archive(archive: Path, destination: Path) -> None:
    """Extract a zip/tar NDK-style archive while preserving executable bits."""

    destination.mkdir(parents=True, exist_ok=True)
    suffixes = "".join(archive.suffixes).lower()
    if archive.suffix.lower() == ".zip":
        extract_zip(archive, destination)
        return
    if suffixes.endswith((".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")) or archive.suffix.lower() == ".tar":
        with tarfile.open(archive) as package:
            package.extractall(destination)
        return
    fail(f"Unsupported archive type for Android NDK bootstrap: {archive}")


def root_command(command: Sequence[str]) -> list[str]:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return list(command)
    sudo = shutil.which("sudo")
    if sudo:
        return [sudo, *command]
    fail("This setup step needs root. Install sudo, run as root, or use --skip-system-deps after installing dependencies manually.")


def certificate_store_available() -> bool:
    paths = ssl.get_default_verify_paths()
    candidates = [
        os.environ.get("SSL_CERT_FILE"),
        paths.cafile,
        paths.openssl_cafile,
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/ssl/cert.pem",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return True
        except OSError:
            pass
    cert_dir = os.environ.get("SSL_CERT_DIR") or paths.capath or paths.openssl_capath
    if cert_dir:
        directory = Path(cert_dir)
        try:
            if directory.is_dir() and any(directory.iterdir()):
                return True
        except OSError:
            pass
    return False


def debian_package_installed(name: str) -> bool:
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return False
    result = subprocess.run(
        [dpkg_query, "-W", "-f=${Status}", name],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def install_missing_system_dependencies(*, need_java: bool, need_certificates: bool, offline: bool, java_version: int = 17) -> None:
    if not need_java and not need_certificates:
        info("System dependencies are already ready")
        return
    if offline:
        missing = []
        if need_java:
            missing.append("JDK")
        if need_certificates:
            missing.append("CA certificates")
        fail("Missing system dependencies in --offline mode: " + ", ".join(missing))

    if running_under_termux_python():
        packages: list[str] = []
        if need_java:
            packages.append(f"openjdk-{java_version}")
        if need_certificates:
            packages.append("ca-certificates")
        package_manager = shutil.which("pkg")
        if package_manager:
            heading("Installing missing Termux dependencies")
            run([package_manager, "install", "-y", *packages])
            return
        apt = shutil.which("apt-get") or shutil.which("apt")
        if apt:
            heading("Installing missing Termux dependencies")
            run([apt, "install", "-y", *packages])
            return
        fail("No Termux package manager was found. Install manually: " + " ".join(packages))

    packages = []
    if need_java:
        packages.append(f"openjdk-{java_version}-jdk-headless")
    if need_certificates and debian_package_installed("ca-certificates"):
        updater = shutil.which("update-ca-certificates")
        if updater:
            heading("Refreshing CA certificate store")
            run(root_command([updater]), check=False)
        need_certificates = not certificate_store_available()
    if need_certificates:
        packages.append("ca-certificates")
    if not packages:
        info("System dependencies are already ready")
        return
    apt_get = shutil.which("apt-get")
    if not apt_get:
        fail("apt-get was not found. Install manually: " + " ".join(packages))
    heading("Installing missing Linux dependencies")
    run(root_command([apt_get, "update"]))
    run(root_command([apt_get, "install", "-y", "--no-install-recommends", *packages]))


def normalized_machine() -> str:
    machine = platform.machine().lower().replace("-", "_")
    aliases = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv8l": "arm64",
        "armv7l": "armv7",
        "armv7": "armv7",
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "i386": "x86",
        "i686": "x86",
    }
    return aliases.get(machine, machine)


@dataclass(frozen=True)
class ChrootLaunchOptions:
    mode: str
    name: str
    termux_home: Path


def running_under_termux_python() -> bool:
    """Return True only when this interpreter is the Termux-host Python.

    Ubuntu chroots often inherit TERMUX_VERSION/PREFIX, so those variables alone
    are not enough. Requiring the active interpreter to live under the Termux
    prefix prevents a build already running inside a chroot from delegating
    recursively.
    """
    if os.environ.get("GRADLE_BUILD_CHROOT_ACTIVE") == "1":
        return False
    executable = Path(sys.executable).resolve(strict=False)
    prefixes: list[Path] = []
    if os.environ.get("PREFIX"):
        prefixes.append(Path(os.environ["PREFIX"]).expanduser().resolve(strict=False))
    prefixes.append(Path("/data/data/com.termux/files/usr"))
    for prefix in prefixes:
        try:
            executable.relative_to(prefix)
            return True
        except ValueError:
            continue
    return False


def parse_chroot_launch_options(raw_args: Sequence[str]) -> ChrootLaunchOptions:
    before_gradle = list(raw_args)
    if "--" in before_gradle:
        before_gradle = before_gradle[: before_gradle.index("--")]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-x", "--chroot",
        nargs="?",
        const="always",
        choices=("auto", "always", "never"),
        default="auto",
    )
    parser.add_argument("-X", "--no-chroot", dest="chroot", action="store_const", const="never")
    parser.add_argument(
        "-N", "--chroot-name",
        default=os.environ.get("GRADLE_BUILD_CHROOT_NAME", "androidos"),
    )
    parser.add_argument(
        "-H", "--chroot-termux-home",
        type=Path,
        default=Path(os.environ.get("GRADLE_BUILD_CHROOT_TERMUX_HOME", "/home/termux")),
    )
    options, _ = parser.parse_known_args(before_gradle)
    return ChrootLaunchOptions(
        mode=options.chroot,
        name=options.chroot_name,
        termux_home=options.chroot_termux_home.expanduser(),
    )


def devtool_cache_dir() -> Path:
    override = os.environ.get("DEVTOOL_XDG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    raw = os.environ.get("XDG_CACHE_HOME", "").strip()
    if raw:
        return Path(raw).expanduser() / "devtool"
    return Path.home() / ".cache" / "devtool"


def map_termux_path(value: str, termux_home: Path, chroot_termux_home: Path) -> str:
    """Map a Termux-host path to the path exposed inside the chroot."""
    if value == "~":
        return str(chroot_termux_home)
    if value.startswith("~/"):
        return str(chroot_termux_home / value[2:])

    source_prefixes = [termux_home, Path("/data/data/com.termux/files/home")]
    candidate = Path(value)
    if candidate.is_absolute():
        for source in source_prefixes:
            try:
                relative = candidate.relative_to(source)
                return str(chroot_termux_home / relative)
            except ValueError:
                continue
    return value


def map_termux_argument(argument: str, termux_home: Path, chroot_termux_home: Path) -> str:
    if argument.startswith("--") and "=" in argument:
        key, value = argument.split("=", 1)
        return f"{key}={map_termux_path(value, termux_home, chroot_termux_home)}"
    return map_termux_path(argument, termux_home, chroot_termux_home)


def stage_builder_for_chroot(termux_home: Path, chroot_termux_home: Path) -> tuple[Path, Path]:
    source = Path(__file__).resolve()
    host_target = devtool_cache_dir() / APP_NAME / "chroot" / "gradle-build.py"
    host_target.parent.mkdir(parents=True, exist_ok=True)
    needs_copy = not host_target.is_file()
    if not needs_copy:
        try:
            needs_copy = sha256(source) != sha256(host_target)
        except OSError:
            needs_copy = True
    if needs_copy:
        shutil.copy2(source, host_target)
        info(f"Staged builder for chroot: {host_target}")
    ensure_executable(host_target, "staged Gradle builder")
    try:
        relative = host_target.relative_to(termux_home)
    except ValueError:
        fail(f"Could not map staged builder into the chroot: {host_target}")
    return host_target, chroot_termux_home / relative


def chroot_probe_command(name: str) -> list[str]:
    return [
        "sudo",
        "chrootctl",
        "--name",
        name,
        "exec",
        "--",
        "zsh",
        "-ilc",
        "command -v python3 >/dev/null 2>&1",
    ]


def chroot_is_available(name: str) -> tuple[bool, str]:
    if shutil.which("sudo") is None:
        return False, "sudo is not available in Termux"
    command = chroot_probe_command(name)
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except FileNotFoundError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, f"timed out probing chroot {name}"
    if result.returncode == 0:
        return True, ""
    details = "\n".join(
        item.strip() for item in (result.stdout, result.stderr) if item and item.strip()
    )
    return False, details or f"chroot probe exited with code {result.returncode}"


def maybe_reexec_in_chroot(raw_args: Sequence[str]) -> int | None:
    """Delegate a Termux-host invocation into chrootctl when available."""
    options = parse_chroot_launch_options(raw_args)
    if options.mode == "never" or not running_under_termux_python():
        return None

    available, reason = chroot_is_available(options.name)
    if not available:
        message = f"Chroot '{options.name}' is unavailable: {reason}"
        if options.mode == "always":
            fail(message)
        warn(message + ". Continuing directly in Termux.")
        return None

    termux_home = Path(os.environ.get("HOME", "/data/data/com.termux/files/home")).resolve(strict=False)
    chroot_termux_home = options.termux_home
    _, chroot_script = stage_builder_for_chroot(termux_home, chroot_termux_home)
    mapped_cwd = map_termux_path(str(Path.cwd().resolve()), termux_home, chroot_termux_home)
    mapped_args = [
        map_termux_argument(argument, termux_home, chroot_termux_home)
        for argument in raw_args
    ]

    inner_parts = ["python3", str(chroot_script), *mapped_args]
    inner_command = (
        "export GRADLE_BUILD_CHROOT_ACTIVE=1; "
        f"cd {shell_join([mapped_cwd])} && exec {shell_join(inner_parts)}"
    )
    command = [
        "sudo",
        "chrootctl",
        "--name",
        options.name,
        "exec",
        "--",
        "zsh",
        "-ilc",
        inner_command,
    ]
    heading(f"Delegating build to chroot: {options.name}")
    info(f"Working directory: {mapped_cwd}")
    print("+", shell_join(command), flush=True)
    try:
        completed = subprocess.run(command, check=False)
    except FileNotFoundError as exc:
        fail(f"Could not launch chroot command: {exc}")
    return completed.returncode


def arch_asset_tokens(machine: str) -> list[str]:
    return {
        "arm64": ["arm64-v8a", "arm64_v8a", "aarch64", "arm64"],
        "armv7": ["armeabi-v7a", "armeabi_v7a", "armv7", "armeabi"],
        "x86_64": ["x86_64", "x86-64", "amd64"],
        "x86": ["x86", "i686", "i386"],
    }.get(machine, [machine])


def select_revanced_asset(release: dict, machine: str) -> dict:
    assets = release.get("assets") or []
    tokens = arch_asset_tokens(machine)
    scored: list[tuple[int, dict]] = []
    for asset in assets:
        name = str(asset.get("name", ""))
        lower = name.lower()
        if "aapt2" not in lower:
            continue
        if any(word in lower for word in ("sha256", "checksum", ".sig", "source")):
            continue
        score = 0
        for index, token in enumerate(tokens):
            if token in lower:
                score += 100 - index
                break
        if machine == "arm64" and "armeabi" in lower:
            score -= 80
        if lower.endswith(".bin"):
            score += 10
        elif lower.endswith(".zip"):
            score += 5
        if score > 0:
            scored.append((score, asset))
    if not scored:
        names = ", ".join(str(asset.get("name")) for asset in assets) or "no assets"
        fail(f"No ReVanced AAPT2 asset matched host architecture {machine}. Assets: {names}")
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def validate_aapt2(path: Path, *, dry_run: bool = False) -> Path:
    ensure_executable(path, "AAPT2")
    result = run([path, "version"], check=False, capture=True, dry_run=dry_run, quiet=True)
    if not dry_run and result.returncode != 0:
        details = "\n".join(item.strip() for item in (result.stdout, result.stderr) if item and item.strip())
        fail(f"AAPT2 could not run: {path}\n{details}")
    if not dry_run:
        version_text = (result.stdout or result.stderr).strip().splitlines()
        info(f"AAPT2 ready: {path}" + (f" ({version_text[0]})" if version_text else ""))
    return path


def stage_aapt2(source: Path, cache_root: Path, *, force: bool, dry_run: bool) -> Path:
    source = source.expanduser().resolve()
    if not source.is_file():
        fail(f"AAPT2 source not found: {source}")
    target_dir = cache_root / "aapt2" / "manual"
    target = target_dir / "aapt2"
    target_dir.mkdir(parents=True, exist_ok=True)
    if dry_run:
        info(f"Would stage AAPT2 outside shared storage: {source} -> {target}")
        return target
    same = target.is_file() and target.stat().st_size == source.stat().st_size
    if same and not force:
        try:
            same = sha256(target) == sha256(source)
        except OSError:
            same = False
    if not same or force:
        if not dry_run:
            shutil.copy2(source, target)
        info(f"Staged AAPT2 outside shared storage: {target}")
    return validate_aapt2(target, dry_run=dry_run)


def obtain_revanced_aapt2(
    cache_root: Path,
    *,
    release_name: str,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> Path:
    machine = normalized_machine()
    metadata_dir = cache_root / "aapt2" / "revanced"
    metadata_dir.mkdir(parents=True, exist_ok=True)

    # In offline mode, prefer the newest already staged binary.
    if offline and release_name == "latest":
        cached = sorted(metadata_dir.glob("*/aapt2"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cached:
            info(f"Using cached ReVanced AAPT2: {cached[0]}")
            return validate_aapt2(cached[0], dry_run=dry_run)

    endpoint = f"{REVANCED_API}/releases/latest" if release_name == "latest" else f"{REVANCED_API}/releases/tags/{release_name}"
    release = github_json(endpoint, offline=offline)
    tag = str(release.get("tag_name") or release_name)
    asset = select_revanced_asset(release, machine)
    asset_name = str(asset["name"])
    asset_url = str(asset["browser_download_url"])
    release_dir = metadata_dir / tag
    target = release_dir / "aapt2"
    if target.is_file() and not force:
        info(f"Using cached ReVanced AAPT2 {tag}: {target}")
        return validate_aapt2(target, dry_run=dry_run)

    archive = release_dir / asset_name
    download(asset_url, archive, offline=offline, force=force)
    release_dir.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return target

    if zipfile.is_zipfile(archive):
        with tempfile.TemporaryDirectory(prefix="gradle-build-aapt2-") as temporary_dir:
            temporary = Path(temporary_dir)
            extract_zip(archive, temporary)
            candidates = [
                path for path in temporary.rglob("*")
                if path.is_file() and "aapt2" in path.name.lower()
            ]
            if not candidates:
                fail(f"ReVanced asset contained no AAPT2 executable: {archive}")
            candidates.sort(key=lambda path: (path.name.lower() != "aapt2", len(path.parts)))
            shutil.copy2(candidates[0], target)
    else:
        shutil.copy2(archive, target)
    return validate_aapt2(target, dry_run=dry_run)


def _checksum_from_manifest(text: str, asset_name: str) -> str | None:
    """Return the SHA-256 recorded for ``asset_name`` in SHA256SUMS text."""

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        digest = parts[0].lower()
        name = parts[-1].lstrip("*")
        if name == asset_name and re.fullmatch(r"[0-9a-f]{64}", digest):
            return digest
    return None


def native_tool_matches_host(path: Path, machine: str | None = None) -> bool:
    """Return whether a native SDK executable can run on the current host.

    Script launchers and other non-ELF tools are accepted. ELF executables must
    declare the same machine as the host.
    """

    if not path.is_file():
        return False
    try:
        metadata = path.stat()
        header = path.read_bytes()[:4096]
    except OSError:
        return False
    executable_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    if metadata.st_size <= 0 or not metadata.st_mode & executable_bits:
        return False
    if not header.startswith(b"\x7fELF"):
        return True
    detected = _elf_machine(header)
    return detected is None or detected == (machine or normalized_machine())



def native_tool_arch(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        header = path.read_bytes()[:4096]
    except OSError:
        return None
    if not header.startswith(b"\x7fELF"):
        return "script"
    return _elf_machine(header)


def ndk_root_for_version(sdk_root: Path, version: str) -> Path:
    return sdk_root / "ndk" / version


def ndk_prebuilt_root(ndk_root: Path) -> Path:
    return ndk_root / "toolchains" / "llvm" / "prebuilt"


def ndk_google_host_tag() -> str:
    # Android Gradle Plugin and CMake's Android toolchain historically select
    # this host tag for Linux hosts.  Keep it stable even on ARM Linux so an
    # ARM64 unofficial NDK can expose a compatibility symlink here.
    return "linux-x86_64"


def ndk_google_tool_bin(sdk_root: Path, version: str) -> Path:
    return ndk_root_for_version(sdk_root, version) / "toolchains" / "llvm" / "prebuilt" / ndk_google_host_tag() / "bin"


def ndk_clangxx(sdk_root: Path, version: str) -> Path:
    return ndk_google_tool_bin(sdk_root, version) / "clang++"


def ndk_host_toolchain_runs(sdk_root: Path, version: str) -> bool:
    return executable_runs(ndk_clangxx(sdk_root, version), "--version")


def find_arm64_ndk_prebuilt(ndk_root: Path) -> Path | None:
    prebuilt = ndk_prebuilt_root(ndk_root)
    candidates = (
        "linux-aarch64",
        "linux-arm64",
        "linux-arm64-v8a",
        "linux-aarch64-musl",
    )
    for name in candidates:
        candidate = prebuilt / name
        if executable_runs(candidate / "bin" / "clang++", "--version"):
            return candidate
    for candidate in prebuilt.iterdir() if prebuilt.is_dir() else []:
        if candidate.name == ndk_google_host_tag():
            continue
        if native_tool_matches_host(candidate / "bin" / "clang++") and executable_runs(candidate / "bin" / "clang++", "--version"):
            return candidate
    return None


def expose_arm64_ndk_as_linux_x86_64(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    ndk_root = ndk_root_for_version(sdk_root, version)
    arm_prebuilt = find_arm64_ndk_prebuilt(ndk_root)
    if arm_prebuilt is None:
        return False
    google_tag = ndk_prebuilt_root(ndk_root) / ndk_google_host_tag()
    if google_tag.resolve() == arm_prebuilt.resolve():
        return True
    if google_tag.exists() or google_tag.is_symlink():
        backup = google_tag.with_name(google_tag.name + ".google-x86_64")
        if not backup.exists():
            if dry_run:
                info(f"Would backup Google NDK host prebuilt: {google_tag} -> {backup}")
            else:
                google_tag.rename(backup)
        else:
            if dry_run:
                info(f"Would replace existing Google host tag with ARM64 NDK alias: {google_tag}")
            else:
                if google_tag.is_symlink() or google_tag.is_file():
                    google_tag.unlink(missing_ok=True)
                elif google_tag.is_dir():
                    shutil.rmtree(google_tag)
    if dry_run:
        info(f"Would expose ARM64 NDK host prebuilt as {google_tag}: {arm_prebuilt}")
        return True
    google_tag.symlink_to(arm_prebuilt, target_is_directory=True)
    info(f"Exposed ARM64 NDK host prebuilt as {google_tag}: {arm_prebuilt}")
    return ndk_host_toolchain_runs(sdk_root, version)


def find_extracted_ndk_root(directory: Path) -> Path | None:
    candidates = [directory, *[p for p in directory.rglob("source.properties") if p.is_file()][:20]]
    for candidate in candidates:
        root = candidate.parent if candidate.is_file() else candidate
        if (root / "build" / "cmake" / "android.toolchain.cmake").is_file() and ndk_prebuilt_root(root).is_dir():
            return root
    return None


def obtain_unofficial_arm64_ndk(
    sdk_root: Path,
    version: str,
    cache_root: Path,
    *,
    url: str,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> bool:
    """Install a configured unofficial ARM64-host NDK archive.

    The URL is deliberately explicit.  There is no official Google Linux ARM64
    host NDK archive; different community archives use different names and host
    tags.  Devtool can download/cache/use one once the URL is supplied through
    config, CLI, or DEVTOOL_ARM64_NDK_URL.
    """

    if not url:
        return False
    if normalized_machine() not in {"arm64", "armv7"}:
        return False
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version)
    suffix = Path(urllib.parse.urlparse(url).path).name or f"ndk-{safe_version}.archive"
    archive = cache_root / "ndk" / "arm64" / safe_version / suffix
    download(url, archive, offline=offline, force=force)
    target = ndk_root_for_version(sdk_root, version)
    with tempfile.TemporaryDirectory(prefix="gradle-build-arm64-ndk-") as temporary_dir:
        temporary = Path(temporary_dir)
        extract_archive(archive, temporary)
        extracted = find_extracted_ndk_root(temporary)
        if extracted is None:
            fail(f"Configured ARM64 NDK archive did not contain a recognizable NDK layout: {archive}")
        revision = source_properties_revision(extracted)
        if revision and revision != version:
            warn(f"Configured ARM64 NDK revision is {revision}, but project requested {version}")
        if dry_run:
            info(f"Would install ARM64 NDK {version}: {extracted} -> {target}")
            return True
        if target.exists():
            backup = target.with_name(target.name + ".google-x86_64")
            if not backup.exists():
                target.rename(backup)
            else:
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                else:
                    shutil.rmtree(target)
        shutil.copytree(extracted, target, symlinks=True)
    if not expose_arm64_ndk_as_linux_x86_64(sdk_root, version, dry_run=dry_run):
        fail(f"Installed ARM64 NDK but clang++ is still not executable for this host: {target}")
    info(f"Installed configured ARM64-host NDK {version}: {target}")
    return True


def find_qemu_x86_64(explicit: str | None = None) -> Path | None:
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(QEMU_X86_64_NAMES)
    for candidate in candidates:
        path = Path(candidate).expanduser() if "/" in candidate else None
        resolved = path if path and path.is_file() else shutil.which(candidate)
        if resolved:
            return Path(resolved).resolve()
    return None


def install_qemu_x86_64_if_needed(*, offline: bool, dry_run: bool, install_policy: str) -> Path | None:
    found = find_qemu_x86_64()
    if found:
        return found
    if install_policy == "never" or offline:
        return None

    if running_under_termux_python():
        manager = shutil.which("pkg") or shutil.which("apt") or shutil.which("apt-get")
        if manager:
            heading("Installing Termux QEMU user-mode for x86_64 NDK fallback")
            run([manager, "install", "-y", "qemu-user-x86-64"], check=False, dry_run=dry_run)
            return find_qemu_x86_64()
        return None

    apt_get = shutil.which("apt-get")
    if apt_get:
        heading("Installing QEMU user-mode for x86_64 NDK fallback")
        run(root_command([apt_get, "update"]), check=False, dry_run=dry_run)
        for packages in (
            ["qemu-user", "qemu-user-binfmt", "libc6-amd64-cross"],
            ["qemu-user", "libc6-amd64-cross"],
            ["qemu-user"],
            ["qemu-user-static"],
        ):
            run(root_command([apt_get, "install", "-y", "--no-install-recommends", *packages]), check=False, dry_run=dry_run)
            found = find_qemu_x86_64()
            if found:
                return found
    return None


def qemu_prefix_has_x86_64_loader(prefix: Path) -> bool:
    """Return true when prefix looks usable as qemu-x86_64 -L root."""

    for relative in (
        "lib64/ld-linux-x86-64.so.2",
        "lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
        "usr/lib64/ld-linux-x86-64.so.2",
        "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
    ):
        if (prefix / relative).exists():
            return True
    return False


def verify_sha256(path: Path, expected: str | None, label: str) -> None:
    normalized = str(expected or "").strip().lower()
    if not normalized:
        return
    actual = sha256(path).lower()
    if actual != normalized:
        fail(f"{label} SHA256 mismatch for {path}\nexpected: {normalized}\nactual:   {actual}")


def qemu_sysroot_cache_dir(cache_root: Path, url: str) -> Path:
    stem = Path(urllib.parse.urlparse(url).path).name or "qemu-x86_64-sysroot.tar.gz"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    return cache_root / "qemu" / "x86_64-sysroot" / safe.replace(".tar.gz", "").replace(".tgz", "")


def obtain_qemu_x86_64_sysroot(
    cache_root: Path,
    *,
    url: str | None,
    expected_sha256: str | None,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> Path | None:
    """Download/extract a small amd64 Linux rootfs for qemu-x86_64 -L.

    Termux can install qemu-x86_64 but normally lacks the amd64 dynamic
    loader that the official Google linux-x86_64 NDK executables need.  Ubuntu
    Base is small enough for validation use and contains the loader/libc layout
    QEMU expects.  A custom URL can be supplied for air-gapped or mirrored
    setups.
    """

    selected_url = str(url or os.environ.get("DEVTOOL_QEMU_SYSROOT_URL", "").strip() or DEFAULT_QEMU_X86_64_SYSROOT_URL)
    selected_sha = str(expected_sha256 or os.environ.get("DEVTOOL_QEMU_SYSROOT_SHA256", "").strip() or DEFAULT_QEMU_X86_64_SYSROOT_SHA256)
    target = qemu_sysroot_cache_dir(cache_root, selected_url)
    if qemu_prefix_has_x86_64_loader(target) and not force:
        info(f"Using cached QEMU x86_64 sysroot: {target}")
        return target
    if dry_run:
        info(f"Would download/extract QEMU x86_64 sysroot: {selected_url} -> {target}")
        return target
    archive_name = Path(urllib.parse.urlparse(selected_url).path).name or "qemu-x86_64-sysroot.tar.gz"
    archive = cache_root / "qemu" / "downloads" / archive_name
    download(selected_url, archive, offline=offline, force=force)
    verify_sha256(archive, selected_sha, "QEMU x86_64 sysroot")
    staging = target.with_name(target.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    extract_archive(archive, staging)
    if not qemu_prefix_has_x86_64_loader(staging):
        shutil.rmtree(staging, ignore_errors=True)
        fail(
            "Downloaded QEMU x86_64 sysroot does not contain ld-linux-x86-64.so.2.\n"
            f"URL: {selected_url}\n"
            "Set --qemu-sysroot-url to a tar/zip rootfs containing an amd64 loader/libc."
        )
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    info(f"Installed QEMU x86_64 sysroot: {target}")
    return target


def resolve_qemu_ld_prefix(
    value: str | None,
    cache_root: Path,
    *,
    qemu_sysroot_url: str | None,
    qemu_sysroot_sha256: str | None,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> Path | None:
    normalized = str(value or "auto").strip()
    if normalized.lower() in {"none", "off", "false"}:
        return None
    if normalized.lower() not in {"", "auto"}:
        explicit = Path(normalized).expanduser()
        if not qemu_prefix_has_x86_64_loader(explicit):
            warn(f"Configured QEMU loader prefix does not contain an x86_64 loader: {explicit}")
        return explicit
    for candidate in (
        "/usr/x86_64-linux-gnu",
        "/usr/x86_64-linux-gnu/libc",
        "/usr/gnemul/qemu-x86_64",
        "/",
    ):
        path = Path(candidate)
        if path.exists() and qemu_prefix_has_x86_64_loader(path):
            return path
    if bootstrap_provisioning_allowed("auto", "allow"):
        return obtain_qemu_x86_64_sysroot(
            cache_root,
            url=qemu_sysroot_url,
            expected_sha256=qemu_sysroot_sha256,
            offline=offline,
            force=force,
            dry_run=dry_run,
        )
    return None


def ndk_wrapped_backup(path: Path) -> Path:
    return path.with_name(path.name + ".devtool-x86_64")


def wrapper_mentions_devtool(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:512]
    except OSError:
        return False
    return "devtool-x86_64" in head and "qemu" in head


def x86_64_binary_candidate(path: Path) -> Path | None:
    """Return an x86_64 ELF executable for an NDK bin entry.

    NDK tool names such as clang++ are often symlinks.  A wrapper must execute
    the real ELF binary through QEMU, not a symlink that may later resolve back
    to another wrapper.  This helper also recovers from the first-generation
    devtool wrappers where ``clang++.devtool-x86_64`` could be a symlink to the
    newly wrapped ``clang`` script.
    """

    if path.is_symlink():
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return None
        resolved_backup = ndk_wrapped_backup(resolved)
        if resolved_backup.is_file() and native_tool_arch(resolved_backup) == "x86_64":
            return resolved_backup
        if native_tool_arch(resolved) == "x86_64":
            return resolved
        return None

    backup = ndk_wrapped_backup(path)
    if backup.exists():
        if backup.is_symlink():
            try:
                resolved = backup.resolve(strict=True)
            except OSError:
                resolved = None
            if resolved is not None:
                resolved_backup = ndk_wrapped_backup(resolved)
                if resolved_backup.is_file() and native_tool_arch(resolved_backup) == "x86_64":
                    return resolved_backup
                if native_tool_arch(resolved) == "x86_64":
                    return resolved
        if native_tool_arch(backup) == "x86_64":
            return backup
    if native_tool_arch(path) == "x86_64":
        return path
    return None


def ndk_x86_64_real_binary(path: Path) -> Path | None:
    return x86_64_binary_candidate(path)


def snapshot_ndk_x86_64_targets(bin_dir: Path) -> dict[Path, Path]:
    """Map each visible NDK tool entry to the real x86_64 ELF it should run."""

    targets: dict[Path, Path] = {}
    entries = [p for p in sorted(bin_dir.iterdir() if bin_dir.is_dir() else []) if not p.name.endswith(".devtool-x86_64")]

    # First preserve every direct ELF executable.  Symlink wrappers can then
    # target the preserved ELF even if the original target path is replaced by
    # a wrapper later in this function.
    for path in entries:
        if path.is_symlink() or native_tool_arch(path) != "x86_64":
            continue
        backup = ndk_wrapped_backup(path)
        targets[path] = backup

    # Then resolve symlink entries to the preserved target ELF when possible.
    for path in entries:
        if not path.is_symlink():
            candidate = x86_64_binary_candidate(path)
            if candidate is not None:
                targets.setdefault(path, ndk_wrapped_backup(path) if candidate == path else candidate)
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        resolved_backup = ndk_wrapped_backup(resolved)
        if resolved_backup in targets.values() or native_tool_arch(resolved_backup) == "x86_64":
            targets[path] = resolved_backup
        elif native_tool_arch(resolved) == "x86_64":
            targets[path] = resolved
    return targets


def write_qemu_wrapper(wrapper: Path, real_binary: Path, qemu: Path, ld_prefix: Path | None, *, dry_run: bool) -> None:
    import shlex

    qemu_parts = [shlex.quote(str(qemu))]
    if ld_prefix is not None:
        qemu_parts.extend(["-L", shlex.quote(str(ld_prefix))])
    script = "#!/bin/sh\nexec " + " ".join(qemu_parts) + " " + shlex.quote(str(real_binary)) + " \"$@\"\n"
    if dry_run:
        info(f"Would install QEMU wrapper: {wrapper} -> {real_binary}")
        return
    if wrapper.exists() or wrapper.is_symlink():
        if wrapper.is_dir() and not wrapper.is_symlink():
            shutil.rmtree(wrapper)
        else:
            wrapper.unlink()
    wrapper.write_text(script, encoding="utf-8")
    wrapper.chmod(0o755)

def install_qemu_ndk_wrappers(
    sdk_root: Path,
    version: str,
    *,
    cache_root: Path,
    qemu_command: str | None,
    qemu_ld_prefix: str | None,
    qemu_sysroot_url: str | None,
    qemu_sysroot_sha256: str | None,
    offline: bool,
    force: bool,
    dry_run: bool,
    install_policy: str,
) -> bool:
    bin_dir = ndk_google_tool_bin(sdk_root, version)
    clang = bin_dir / "clang++"
    if not clang.is_file():
        return False
    if ndk_host_toolchain_runs(sdk_root, version):
        return True
    if normalized_machine() not in {"arm64", "armv7"}:
        return False
    if ndk_x86_64_real_binary(clang) is None:
        return False

    qemu = find_qemu_x86_64(qemu_command) or install_qemu_x86_64_if_needed(
        offline=offline,
        dry_run=dry_run,
        install_policy=install_policy,
    )
    if qemu is None:
        return False
    ld_prefix = resolve_qemu_ld_prefix(
        qemu_ld_prefix,
        cache_root,
        qemu_sysroot_url=qemu_sysroot_url,
        qemu_sysroot_sha256=qemu_sysroot_sha256,
        offline=offline,
        force=force,
        dry_run=dry_run,
    )
    heading(f"Wrapping x86_64 Android NDK {version} with QEMU")
    info(f"QEMU: {qemu}")
    if ld_prefix:
        info(f"QEMU loader prefix: {ld_prefix}")

    targets = snapshot_ndk_x86_64_targets(bin_dir)
    if not targets:
        return False

    # Preserve all direct ELF executables before writing any wrapper.  This is
    # critical for NDK symlinks such as clang++ -> clang: a wrapper must never
    # execute a backup symlink that later resolves to another wrapper.
    for path, real in sorted(targets.items()):
        backup = ndk_wrapped_backup(path)
        if path.is_symlink():
            continue
        if real == backup and not backup.exists():
            if dry_run:
                info(f"Would backup NDK host executable: {path} -> {backup}")
            else:
                path.rename(backup)

    for path, real in sorted(targets.items()):
        if not dry_run and (not real.is_file() or native_tool_arch(real) != "x86_64"):
            warn(f"Skipping NDK QEMU wrapper with non-ELF target: {path} -> {real}")
            continue
        write_qemu_wrapper(path, real, qemu, ld_prefix, dry_run=dry_run)
    if dry_run:
        return True
    if not ndk_host_toolchain_runs(sdk_root, version):
        fail(
            "QEMU NDK wrapper was installed but clang++ still cannot execute.\n"
            f"Checked: {clang}\n"
            f"QEMU: {qemu}\n"
            f"QEMU loader prefix: {ld_prefix or 'none'}\n"
            "Devtool tried to provision an x86_64 sysroot automatically. If this still fails, "
            "configure --qemu-sysroot-url to a trusted amd64 rootfs or configure an ARM64 NDK URL."
        )
    info(f"QEMU NDK wrapper ready: {clang}")
    return True


def ensure_ndk_host_compatible(
    sdk_root: Path,
    version: str | None,
    cache_root: Path,
    *,
    provider: str,
    arm64_ndk_url: str | None,
    qemu_command: str | None,
    qemu_ld_prefix: str | None,
    qemu_sysroot_url: str | None,
    qemu_sysroot_sha256: str | None,
    qemu_install: str,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> None:
    if not version:
        return
    if provider not in NDK_HOST_PROVIDERS:
        valid = ", ".join(sorted(NDK_HOST_PROVIDERS))
        fail(f"Unsupported NDK host provider: {provider}. Expected one of: {valid}")
    if qemu_install not in QEMU_INSTALL_POLICIES:
        valid = ", ".join(sorted(QEMU_INSTALL_POLICIES))
        fail(f"Unsupported QEMU install policy: {qemu_install}. Expected one of: {valid}")
    if not ndk_is_ready(sdk_root, version):
        return
    machine = normalized_machine()

    # On ARM hosts, auto mode intentionally prefers a native ARM64-host NDK
    # before accepting an existing QEMU wrapper or installing a new one. This
    # keeps Termux/chroot validation fast when the user has configured a
    # trusted unofficial NDK, while still falling back to QEMU when no native
    # toolchain is available.
    if machine in {"arm64", "armv7"} and provider in {"auto", "arm64"}:
        if expose_arm64_ndk_as_linux_x86_64(sdk_root, version, dry_run=dry_run):
            return
        configured_url = arm64_ndk_url or os.environ.get("DEVTOOL_ARM64_NDK_URL", "").strip()
        if configured_url and obtain_unofficial_arm64_ndk(
            sdk_root,
            version,
            cache_root,
            url=configured_url,
            offline=offline,
            force=force,
            dry_run=dry_run,
        ):
            return
        if provider == "arm64":
            fail(
                "An ARM64-host NDK was requested, but no runnable ARM64 prebuilt was found.\n"
                "Set --arm64-ndk-url or DEVTOOL_ARM64_NDK_URL to a trusted unofficial NDK archive."
            )

    if ndk_host_toolchain_runs(sdk_root, version):
        info(f"NDK host toolchain executable: {version}")
        return
    if machine not in {"arm64", "armv7"}:
        fail(f"NDK {version} is installed but clang++ cannot run on this host: {ndk_clangxx(sdk_root, version)}")
    if provider == "none":
        warn(f"NDK host compatibility repair disabled; native Gradle tasks may fail: {ndk_clangxx(sdk_root, version)}")
        return
    if provider in {"auto", "qemu"}:
        if install_qemu_ndk_wrappers(
            sdk_root,
            version,
            cache_root=cache_root,
            qemu_command=qemu_command,
            qemu_ld_prefix=qemu_ld_prefix,
            qemu_sysroot_url=qemu_sysroot_url,
            qemu_sysroot_sha256=qemu_sysroot_sha256,
            offline=offline,
            force=force,
            dry_run=dry_run,
            install_policy=qemu_install,
        ):
            return
        if provider == "qemu":
            fail(
                "QEMU NDK fallback was requested but qemu-x86_64 could not be installed or used.\n"
                "Install qemu-user-x86-64/qemu-user, or set --qemu-command."
            )
    if provider == "google":
        fail(
            f"The official Google NDK host compiler is not executable on {machine}: {ndk_clangxx(sdk_root, version)}\n"
            "Use --ndk-host-provider qemu or configure --arm64-ndk-url."
        )
    warn(
        f"NDK {version} host compiler is still not executable on {machine}. "
        "Native/CMake Gradle tasks may fail. Configure [android].ndk_host_provider='qemu' "
        "or set [android].arm64_ndk_url to a trusted ARM64 NDK archive."
    )


def obtain_commit451_arm64_tool(
    cache_root: Path,
    build_tools_version: str,
    tool_name: str,
    *,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> Path:
    """Download and verify one Commit451 linux-arm64 build-tools executable."""

    if tool_name not in ARM_BUILD_TOOLS_NATIVE_NAMES:
        fail(f"Unsupported ARM build-tools executable: {tool_name}")
    if normalized_machine() != "arm64":
        fail("Commit451 Android build-tools are only used on ARM64 Linux hosts")

    release_version = ARM_BUILD_TOOLS_RELEASE_ALIASES.get(
        build_tools_version, build_tools_version
    )
    if release_version != build_tools_version:
        warn(
            "Commit451 does not publish ARM64 binaries for Android Build Tools "
            f"{build_tools_version}; using the compatible {release_version} release."
        )
    tag = f"platform-tools-{release_version}"
    base_url = (
        f"https://github.com/{ARM_BUILD_TOOLS_REPOSITORY}/releases/download/{tag}"
    )
    release_dir = cache_root / "android-build-tools" / "commit451" / tag
    manifest = release_dir / "SHA256SUMS"
    target = release_dir / tool_name

    download(
        f"{base_url}/SHA256SUMS",
        manifest,
        offline=offline,
        force=force,
    )
    download(
        f"{base_url}/{tool_name}",
        target,
        offline=offline,
        force=force,
    )

    if dry_run:
        info(
            f"Would verify Commit451 ARM64 {tool_name} for Build Tools "
            f"{build_tools_version}"
        )
        return target

    try:
        checksum_text = manifest.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        fail(f"Could not read Commit451 checksum manifest: {manifest}\n{exc}")
    expected = _checksum_from_manifest(checksum_text, tool_name)
    if expected is None:
        fail(
            f"Commit451 checksum manifest does not contain {tool_name}: {manifest}"
        )
    actual = sha256(target)
    if actual != expected:
        target.unlink(missing_ok=True)
        fail(
            f"Checksum mismatch for Commit451 {tool_name}: expected {expected}, "
            f"received {actual}"
        )

    ensure_executable(target, f"Commit451 ARM64 {tool_name}")
    try:
        header = target.read_bytes()[:4096]
    except OSError as exc:
        fail(f"Could not inspect Commit451 {tool_name}: {target}\n{exc}")
    detected = _elf_machine(header)
    if detected != "arm64":
        fail(
            f"Commit451 {tool_name} is not an ARM64 ELF executable: "
            f"{target} ({detected or 'unknown architecture'})"
        )
    return target


def arm64_build_tools_replacements_needed(
    sdk_root: Path,
    build_tools_version: str | None,
) -> list[str]:
    """List SDK native tools missing or incompatible on an ARM64 host."""

    if normalized_machine() != "arm64" or not build_tools_version:
        return []
    directory = sdk_root / "build-tools" / build_tools_version
    if not directory.is_dir():
        return []
    return [
        name
        for name in ARM_BUILD_TOOLS_NATIVE_NAMES
        if not native_tool_matches_host(directory / name, "arm64")
    ]


def ensure_arm64_build_tools_compatible(
    sdk_root: Path,
    build_tools_version: str | None,
    cache_root: Path,
    *,
    provider: str,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> None:
    """Repair x86-only AIDL/zipalign/split-select in an ARM64 Android SDK."""

    if normalized_machine() != "arm64" or not build_tools_version:
        return
    if provider == "none":
        return
    if provider not in {"auto", "commit451"}:
        fail(f"Unsupported ARM build-tools provider: {provider}")

    directory = sdk_root / "build-tools" / build_tools_version
    if not directory.is_dir():
        return
    replacements = arm64_build_tools_replacements_needed(
        sdk_root,
        build_tools_version,
    )
    if not replacements and not force:
        info(
            f"ARM64 SDK build-tools ready: {build_tools_version} "
            f"({', '.join(ARM_BUILD_TOOLS_NATIVE_NAMES)})"
        )
        return
    if not sdk_root_is_writable_for_install(sdk_root):
        fail(
            "Android SDK contains incompatible native build-tools and cannot be "
            f"repaired because it is read-only: {sdk_root}"
        )

    heading(f"Repairing Android Build Tools {build_tools_version} for ARM64")
    selected = list(ARM_BUILD_TOOLS_NATIVE_NAMES) if force else replacements
    for tool_name in selected:
        source = obtain_commit451_arm64_tool(
            cache_root,
            build_tools_version,
            tool_name,
            offline=offline,
            force=force,
            dry_run=dry_run,
        )
        target = directory / tool_name
        if dry_run:
            info(f"Would install ARM64 {tool_name}: {source} -> {target}")
            continue

        backup = target.with_name(target.name + ".google-x86_64")
        if target.exists() or target.is_symlink():
            if not backup.exists() and not target.is_symlink():
                target.replace(backup)
            else:
                target.unlink(missing_ok=True)
        temporary = target.with_name(target.name + ".devtool.tmp")
        shutil.copy2(source, temporary)
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        temporary.replace(target)
        if not native_tool_matches_host(target, "arm64"):
            fail(f"Installed ARM64 {tool_name} failed architecture validation: {target}")
        info(f"Installed ARM64 {tool_name}: {target}")


@dataclass(frozen=True)
class ProjectInfo:
    root: Path
    name: str
    android: bool
    android_application: bool
    compile_sdk: int | None
    build_tools: str | None
    agp_major: int | None
    native_build: bool
    ndk_version: str | None
    cmake_version: str | None


@dataclass(frozen=True)
class JavaRequirement:
    minimum: int
    exact: int | None = None


def project_files(root: Path) -> Iterable[Path]:
    names = {
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
        "gradle.properties",
        "libs.versions.toml",
    }
    for name in names:
        path = root / name
        if path.is_file():
            yield path
    version_catalog = root / "gradle" / "libs.versions.toml"
    if version_catalog.is_file():
        yield version_catalog
    for depth in (1, 2):
        pattern = "*/" * depth + "build.gradle*"
        for path in root.glob(pattern):
            if path.is_file():
                yield path


def read_project_text(root: Path) -> str:
    chunks: list[str] = []
    seen: set[Path] = set()
    for path in project_files(root):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(chunks)


def resolve_project_root(value: Path) -> Path:
    candidate = value.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    if not candidate.is_dir():
        fail(f"Project directory not found: {candidate}")
    markers = ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts", "gradlew")
    if not any((candidate / marker).exists() for marker in markers):
        fail(f"No Gradle project markers were found in {candidate}")
    return candidate


def first_int(patterns: Sequence[str], text: str) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def project_display_name(root: Path, text: str) -> str:
    patterns = (
        r"rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]",
        r"rootProject\.name\s*=\s*providers\.gradleProperty\([^)]*\)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if match and match.lastindex:
            value = match.group(1).strip()
            if value:
                return value
    return root.name or "gradle-project"


def slugify_name(value: str) -> str:
    # Turn CamelCase, spaces and punctuation into a stable artifact-friendly name.
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value.strip())
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value)
    value = re.sub(r"[-_.]{2,}", "-", value).strip("-._")
    return value.lower() or "gradle-project"


def inspect_project(root: Path) -> ProjectInfo:
    text = read_project_text(root)
    name = project_display_name(root, text)
    android_application = bool(re.search(r"com\.android\.application|id\s*\(?\s*['\"]com\.android\.application", text))
    android_library = bool(re.search(r"com\.android\.library|id\s*\(?\s*['\"]com\.android\.library", text))
    android = android_application or android_library or any(root.rglob("src/main/AndroidManifest.xml"))
    compile_sdk = first_int(
        [
            r"\bcompileSdk(?:Version)?\s*(?:=|\s)\s*['\"]?(\d+)",
            r"\bcompileSdk\s*\(\s*(\d+)\s*\)",
        ],
        text,
    )
    build_tools_match = re.search(r"\bbuildToolsVersion\s*(?:=|\s)\s*['\"]([^'\"]+)", text)
    build_tools = build_tools_match.group(1) if build_tools_match else None
    agp_major = first_int(
        [
            r"com\.android\.tools\.build:gradle:([0-9]+)",
            r"com\.android\.(?:application|library)[^\n]*?version\s*(?:=\s*)?['\"]([0-9]+)",
            r"agp\s*=\s*['\"]([0-9]+)",
            r"androidGradlePlugin\s*=\s*['\"]([0-9]+)",
        ],
        text,
    )
    native_build = "externalNativeBuild" in text or any(root.rglob("CMakeLists.txt"))
    ndk_match = re.search(r"\bndkVersion\s*(?:=|\s)\s*['\"]([^'\"]+)", text)
    ndk_version = ndk_match.group(1).strip() if ndk_match else None
    cmake_match = re.search(
        r"externalNativeBuild\s*\{.*?cmake\s*\{.*?\bversion\s*=\s*['\"]([^'\"]+)",
        text,
        re.DOTALL,
    )
    cmake_version = cmake_match.group(1).strip() if cmake_match else None
    return ProjectInfo(
        root,
        name,
        android,
        android_application,
        compile_sdk,
        build_tools,
        agp_major,
        native_build,
        ndk_version,
        cmake_version,
    )


def infer_java_requirement(project: ProjectInfo) -> JavaRequirement:
    baseline = 8
    if project.agp_major is not None:
        if project.agp_major >= 8:
            baseline = 17
        elif project.agp_major >= 7:
            baseline = 11

    text = read_project_text(project.root)
    minimum = baseline
    exact_versions: list[int] = []

    minimum_patterns = (
        r"JavaVersion\.VERSION_(\d+)",
        r"JvmTarget\.JVM_(\d+)",
        r"jvmTarget\s*=\s*['\"]?(\d+)",
        r"sourceCompatibility\s*=\s*['\"]?(\d+)",
        r"targetCompatibility\s*=\s*['\"]?(\d+)",
    )
    exact_patterns = (
        r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)",
        r"jvmToolchain\(\s*(\d+)\s*\)",
    )

    for pattern in minimum_patterns:
        for match in re.finditer(pattern, text):
            try:
                minimum = max(minimum, int(match.group(1)))
            except (TypeError, ValueError):
                pass

    for pattern in exact_patterns:
        for match in re.finditer(pattern, text):
            try:
                value = int(match.group(1))
            except (TypeError, ValueError):
                continue
            minimum = max(minimum, value)
            exact_versions.append(value)

    return JavaRequirement(minimum=minimum, exact=max(exact_versions) if exact_versions else None)


def java_major(java: Path) -> int | None:
    result = subprocess.run([str(java), "-version"], text=True, capture_output=True, check=False)
    text = f"{result.stdout}\n{result.stderr}"
    match = re.search(r'(?:version\s+"|openjdk\s+)(\d+)(?:\.(\d+))?', text, re.IGNORECASE)
    if not match:
        return None
    first = int(match.group(1))
    second = int(match.group(2) or 0)
    return second if first == 1 else first


def inferred_min_java(project: ProjectInfo) -> int:
    return infer_java_requirement(project).minimum


def java_home_candidates(explicit: Path | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]).expanduser())
    for executable in ("javac", "java"):
        found = shutil.which(executable)
        if found:
            candidates.append(Path(found).resolve().parent.parent)
    jvm_root = Path("/usr/lib/jvm")
    if jvm_root.is_dir():
        candidates.extend(path for path in jvm_root.iterdir() if path.is_dir())
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        resolved = item.resolve(strict=False)
        if str(resolved) not in seen:
            seen.add(str(resolved))
            unique.append(resolved)
    return unique


def find_java_home(explicit: Path | None, minimum: int, *, exact: int | None = None) -> Path:
    rejected: list[str] = []
    accepted: list[tuple[int, int, Path]] = []
    for home in java_home_candidates(explicit):
        java = home / "bin" / "java"
        javac = home / "bin" / "javac"
        if not java.is_file() or not javac.is_file():
            continue
        major = java_major(java)
        if major is None:
            rejected.append(f"{home} (Java unknown)")
            continue
        if major < minimum:
            rejected.append(f"{home} (Java {major})")
            continue
        if exact is not None and major != exact:
            rejected.append(f"{home} (Java {major}, needs exact Java {exact})")
            continue
        # Prefer the narrowest matching toolchain so we select Java 17 when the
        # project requests an exact Java 17 toolchain and both 17 and 21 are
        # installed.
        accepted.append((0 if exact is not None and major == exact else 1, major, home))

    if accepted:
        accepted.sort(key=lambda item: (item[0], item[1], str(item[2])))
        return accepted[0][2]
    details = f" Candidates checked: {', '.join(rejected)}." if rejected else ""
    if exact is not None:
        fail(
            f"A JDK with Java {exact} is required for this project's declared toolchain.{details} "
            "Use --java-home to select one."
        )
    fail(f"A JDK with Java {minimum}+ is required.{details} Use --java-home to select one.")


def _sdk_root_from_local_properties(project_root: Path | None) -> Path | None:
    if project_root is None:
        return None
    target = (Path(project_root).expanduser() / "local.properties")
    if not target.is_file():
        return None
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("sdk.dir="):
            continue
        raw = stripped.split("=", 1)[1].strip()
        if not raw:
            return None
        path = Path(os.path.expandvars(os.path.expanduser(raw)))
        if path.is_dir():
            return path.resolve()
        return path.expanduser().resolve()
    return None


def sdk_host_compatible(sdk_root: Path) -> bool:
    machine = normalized_machine()
    if machine not in {"arm64", "armv7", "x86_64", "x86"}:
        return True

    build_tools = installed_build_tools_versions(sdk_root)
    if not build_tools:
        return True

    # AAPT2 is provided independently through the ReVanced override on ARM.
    # The remaining native SDK tools must match the host architecture.
    probe_names = ARM_BUILD_TOOLS_NATIVE_NAMES
    for version in build_tools[:2]:
        directory = sdk_root / "build-tools" / version
        for name in probe_names:
            candidate = directory / name
            if not candidate.is_file():
                continue
            try:
                header = candidate.read_bytes()[:4096]
            except OSError:
                continue
            if not header.startswith(b"\x7fELF"):
                continue
            detected = _elf_machine(header)
            if detected is not None and detected != machine:
                return False
    return True


def _elf_machine(header: bytes) -> str | None:
    if len(header) < 20 or not header.startswith(b"\x7fELF"):
        return None
    endian = "little" if header[5] == 1 else "big" if header[5] == 2 else "little"
    machine = int.from_bytes(header[18:20], endian, signed=False)
    return {
        3: "x86",
        40: "armv7",
        62: "x86_64",
        183: "arm64",
    }.get(machine)


def managed_sdk_root_for_host(cache_root: Path) -> Path:
    """Return a writable managed SDK location compatible with this host.

    The legacy ``android-sdk`` cache may have been populated by another host
    architecture. Preserve it rather than mutating it in place, and use an
    architecture-qualified cache when its build tools are incompatible.
    """

    base = (cache_root / "android-sdk").expanduser().resolve()
    if not base.exists() or sdk_host_compatible(base):
        return base
    return (cache_root / f"android-sdk-{normalized_machine()}").expanduser().resolve()


def sdk_root_is_writable_for_install(sdk_root: Path) -> bool:
    """Return whether SDK components can be created below ``sdk_root``."""

    candidate = sdk_root.expanduser()
    if candidate.exists():
        return candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK)

    parent = candidate.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)


def configure_android_sdk_env(env: MutableMapping[str, str], sdk_root: Path) -> None:
    """Point Android tooling at ``sdk_root`` and prepend its executable paths."""

    env["ANDROID_HOME"] = str(sdk_root)
    env["ANDROID_SDK_ROOT"] = str(sdk_root)
    additions = [
        str(sdk_root / "cmdline-tools" / "latest" / "bin"),
        str(sdk_root / "platform-tools"),
    ]
    current = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    env["PATH"] = os.pathsep.join([*additions, *[entry for entry in current if entry not in additions]])


def discover_sdk_root(explicit: Path | None, project_root: Path | None = None) -> Path | None:
    sdk_root, _source = discover_sdk_root_with_source(explicit, project_root)
    return sdk_root


def discover_sdk_root_with_source(
    explicit: Path | None,
    project_root: Path | None = None,
    *,
    include_managed_cache: bool = False,
    cache_root: Path | None = None,
) -> tuple[Path | None, str | None]:
    candidates: list[tuple[Path, str, bool]] = []
    if explicit:
        candidates.append((explicit.expanduser(), "explicit --sdk-root", True))
    local_properties = _sdk_root_from_local_properties(project_root)
    if local_properties is not None:
        candidates.append((local_properties, "local.properties sdk.dir", False))
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(variable):
            candidates.append((Path(os.environ[variable]).expanduser(), variable, False))
    if include_managed_cache and cache_root is not None:
        candidates.append(((cache_root / "android-sdk").expanduser(), "managed cache", False))
    candidates.extend(
        [
            (Path.home() / "Android" / "Sdk", "system install", False),
            (Path.home() / "android-sdk", "system install", False),
            (Path.home() / ".android" / "sdk", "system install", False),
            (Path("/opt/android-sdk"), "system install", False),
            (Path("/usr/lib/android-sdk"), "system install", False),
        ]
    )
    skipped_incompatible: set[Path] = set()
    for candidate, source, explicit_candidate in candidates:
        resolved = candidate.expanduser().resolve() if candidate.exists() else candidate.expanduser().resolve(strict=False)
        if resolved in skipped_incompatible:
            continue
        if candidate.is_dir():
            if not sdk_host_compatible(resolved):
                if explicit_candidate or sdk_root_is_writable_for_install(resolved):
                    warn(
                        "Android SDK contains incompatible native build-tools and "
                        f"will be repaired for this host: {resolved} ({source})"
                    )
                    return resolved, source
                skipped_incompatible.add(resolved)
                warn(f"Skipping Android SDK with incompatible host build tools: {resolved} ({source})")
                continue
            return resolved, source
    if explicit:
        return explicit.expanduser().resolve(), "explicit --sdk-root"
    if include_managed_cache and cache_root is not None:
        return (cache_root / "android-sdk").expanduser().resolve(), "managed cache"
    return None, None


def sdkmanager_candidates(sdk_root: Path) -> list[Path]:
    candidates = [
        sdk_root / "cmdline-tools" / "latest" / "bin" / "sdkmanager",
        sdk_root / "tools" / "bin" / "sdkmanager",
    ]
    cmdline_root = sdk_root / "cmdline-tools"
    if cmdline_root.is_dir():
        candidates.extend(sorted(cmdline_root.glob("*/bin/sdkmanager"), reverse=True))
    return candidates


def find_sdkmanager(sdk_root: Path) -> Path | None:
    for candidate in sdkmanager_candidates(sdk_root):
        if candidate.is_file():
            return ensure_executable(candidate, "sdkmanager")
    found = shutil.which("sdkmanager")
    return ensure_executable(Path(found), "sdkmanager") if found else None



def ensure_sdkmanager_available(
    sdk_root: Path,
    cache_root: Path,
    *,
    offline: bool,
    force: bool,
    dry_run: bool,
) -> Path:
    existing = find_sdkmanager(sdk_root)
    if existing is not None and not force:
        info(f"Reusing sdkmanager: {existing}")
        return existing

    if not sdk_root_is_writable_for_install(sdk_root):
        fail(
            "Android SDK command-line tools are missing and the selected SDK "
            f"is not writable: {sdk_root}. Use a writable --sdk-root or allow "
            "Devtool to use its managed SDK cache."
        )

    archive = cache_root / "android" / "commandlinetools-linux-latest.zip"
    download(ANDROID_CMDLINE_TOOLS_URL, archive, offline=offline, force=force)
    target = sdk_root / "cmdline-tools" / "latest"
    if dry_run:
        info(f"Would install Android command-line tools under {target}")
        return target / "bin" / "sdkmanager"

    with tempfile.TemporaryDirectory(prefix="gradle-build-cmdline-tools-") as temporary_dir:
        temporary = Path(temporary_dir)
        extract_zip(archive, temporary)
        source = temporary / "cmdline-tools"
        if not source.is_dir():
            candidates = [item for item in temporary.iterdir() if item.is_dir()]
            if len(candidates) == 1:
                source = candidates[0]
        if not (source / "bin" / "sdkmanager").is_file():
            fail(f"Unexpected Android command-line tools archive layout: {archive}")
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)

    manager = target / "bin" / "sdkmanager"
    return ensure_executable(manager, "sdkmanager")


def installed_build_tools_versions(sdk_root: Path) -> list[str]:
    root = sdk_root / "build-tools"
    if not root.is_dir():
        return []
    def key(value: str) -> tuple[int, ...]:
        return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.-]", value))
    return sorted((item.name for item in root.iterdir() if item.is_dir()), key=key, reverse=True)

def source_properties_revision(directory: Path) -> str | None:
    properties = directory / "source.properties"
    if not properties.is_file():
        return None
    try:
        text = properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^Pkg\.Revision\s*=\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def ndk_is_ready(sdk_root: Path, version: str) -> bool:
    directory = sdk_root / "ndk" / version
    return (
        source_properties_revision(directory) == version
        and (directory / "toolchains" / "llvm" / "prebuilt").is_dir()
        and (directory / "build" / "cmake" / "android.toolchain.cmake").is_file()
    )


def executable_runs(executable: Path, *args: str) -> bool:
    if not executable.is_file():
        return False
    try:
        result = subprocess.run(
            [str(executable), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def cmake_is_ready(sdk_root: Path, version: str) -> bool:
    directory = sdk_root / "cmake" / version
    return (
        source_properties_revision(directory) == version
        and executable_runs(directory / "bin" / "cmake", "--version")
        and executable_runs(directory / "bin" / "ninja", "--version")
    )


def ensure_host_cmake_compatible(
    sdk_root: Path,
    version: str,
    *,
    dry_run: bool,
) -> None:
    """Replace SDK x86-only CMake launchers with host-native tools on ARM."""
    directory = sdk_root / "cmake" / version
    sdk_cmake = directory / "bin" / "cmake"
    sdk_ninja = directory / "bin" / "ninja"
    if executable_runs(sdk_cmake, "--version") and executable_runs(sdk_ninja, "--version"):
        return

    machine = normalized_machine()
    if machine not in {"arm64", "armv7"}:
        return

    host_cmake = shutil.which("cmake")
    host_ninja = shutil.which("ninja") or shutil.which("ninja-build")
    if not host_cmake or not host_ninja:
        fail(
            f"Android SDK CMake {version} is not executable on {machine}. "
            "Install host-native tools first: sudo apt install cmake ninja-build"
        )

    replacements = {
        sdk_cmake: Path(host_cmake).resolve(),
        sdk_ninja: Path(host_ninja).resolve(),
    }
    for target, source in replacements.items():
        if dry_run:
            info(f"Would replace incompatible {target} with host-native {source}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = target.with_name(target.name + ".sdk-host")
        if target.exists() and not target.is_symlink() and not backup.exists():
            target.rename(backup)
        elif target.is_symlink() or target.exists():
            target.unlink(missing_ok=True)
        target.symlink_to(source)
        info(f"Using host-native {target.name}: {source}")

    if not dry_run and not cmake_is_ready(sdk_root, version):
        fail(f"Host-native CMake compatibility setup failed for SDK CMake {version}")


def remove_path(path: Path, *, dry_run: bool) -> None:
    if not path.exists():
        return
    if dry_run:
        info(f"Would remove incomplete SDK path: {path}")
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def clear_android_download_debris(sdk_root: Path, *, dry_run: bool) -> None:
    candidates = [sdk_root / ".temp", Path.home() / ".android" / "cache"]
    for candidate in candidates:
        if candidate.exists():
            if dry_run:
                info(f"Would clear Android download cache: {candidate}")
            else:
                shutil.rmtree(candidate, ignore_errors=True)
                info(f"Cleared Android download cache: {candidate}")


def clean_android_cxx_state(project_root: Path, *, dry_run: bool) -> None:
    """Remove stale AGP/CMake state after NDK host-toolchain rewriting."""

    candidates: set[Path] = {project_root / ".cxx"}
    try:
        for child in project_root.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                candidates.add(child / ".cxx")
                candidates.add(child / "build" / ".cxx")
    except OSError:
        return
    removed = False
    for candidate in sorted(candidates):
        if candidate.exists():
            if dry_run:
                info(f"Would clear stale Android native configuration cache: {candidate}")
            else:
                shutil.rmtree(candidate, ignore_errors=True)
                info(f"Cleared stale Android native configuration cache: {candidate}")
            removed = True
    if removed:
        info("Android .cxx native configuration caches were reset for NDK host fallback")


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_SDKMANAGER_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,3})%")


def run_sdkmanager_with_progress(
    command: Sequence[str | os.PathLike[str]],
    *,
    env: Mapping[str, str],
    input_text: str | None,
    dry_run: bool,
) -> subprocess.CompletedProcess[str]:
    """Run sdkmanager while turning CR-only updates into visible log lines."""

    rendered = [str(part) for part in command]
    print("+", shell_join(rendered), flush=True)
    if dry_run:
        return subprocess.CompletedProcess(rendered, 0, "", "")

    info("SDK installation progress: starting")
    try:
        process = subprocess.Popen(
            rendered,
            env=dict(env),
            stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except PermissionError as exc:
        fail(f"Permission denied while launching sdkmanager: {rendered[0]}\n{exc}")
    except FileNotFoundError as exc:
        fail(f"sdkmanager not found: {rendered[0]}\n{exc}")

    if input_text is not None and process.stdin is not None:
        try:
            process.stdin.write(input_text)
            process.stdin.close()
        except BrokenPipeError:
            pass

    captured: list[str] = []
    last_percent = -5
    last_progress_line = ""
    last_plain_line = ""
    assert process.stdout is not None
    for raw_line in process.stdout:
        # TextIO's universal-newline handling converts sdkmanager's carriage
        # returns into iterable lines, which keeps progress visible in pipes,
        # Devtool logs, notifications, and HUDs.
        line = _ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if not line:
            continue
        captured.append(line)
        match = _SDKMANAGER_PERCENT_RE.search(line)
        if match:
            percent = min(100, max(0, int(match.group(1))))
            should_report = (
                percent >= last_percent + 5
                or percent in {0, 100}
                or (line != last_progress_line and percent == last_percent)
            )
            if should_report:
                detail = re.sub(r"^[\[\]=#>.\-\s]*\d{1,3}%\s*", "", line).strip(" []")
                suffix = f" · {detail}" if detail and detail != line else ""
                info(f"SDK installation progress: {percent:3d}%{suffix}")
                last_percent = percent
                last_progress_line = line
            continue

        if line != last_plain_line:
            info(f"sdkmanager: {line}")
            last_plain_line = line

    returncode = int(process.wait())
    if returncode == 0 and last_percent < 100:
        info("SDK installation progress: 100% · complete")
    return subprocess.CompletedProcess(rendered, returncode, "\n".join(captured), "")


def install_sdk_packages_with_repair(
    manager: Path,
    sdk_root: Path,
    packages: Sequence[str],
    *,
    env: Mapping[str, str],
    dry_run: bool,
) -> None:
    command = [manager, f"--sdk_root={sdk_root}", *packages]
    result = run_sdkmanager_with_progress(
        command,
        env=env,
        input_text="y\n" * 200,
        dry_run=dry_run,
    )
    if dry_run or result.returncode == 0:
        return

    warn("Android SDK package installation failed; repairing partial downloads and retrying once")
    clear_android_download_debris(sdk_root, dry_run=False)
    for package in packages:
        kind, _, version = package.partition(";")
        if kind in {"ndk", "cmake", "build-tools"} and version:
            remove_path(sdk_root / kind / version, dry_run=False)
        elif kind == "platforms" and version:
            remove_path(sdk_root / "platforms" / version, dry_run=False)

    retry = run_sdkmanager_with_progress(
        command,
        env=env,
        input_text="y\n" * 200,
        dry_run=False,
    )
    if retry.returncode != 0:
        fail(
            "Android SDK package installation failed again after clearing partial "
            f"downloads: {', '.join(packages)}"
        )


def update_local_properties(project_root: Path, sdk_root: Path) -> None:
    target = project_root / "local.properties"
    lines: list[str] = []
    if target.is_file():
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    escaped = str(sdk_root).replace("\\", "\\\\")
    replacement = f"sdk.dir={escaped}"
    changed = False
    output: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith("sdk.dir="):
            found = True
            if line != replacement:
                output.append(replacement)
                changed = True
            else:
                output.append(line)
        else:
            output.append(line)
    if not found:
        output.append(replacement)
        changed = True
    if changed or not target.exists():
        target.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
        info(f"Updated {target}")
    else:
        info(f"Reusing SDK path from {target}")


def default_build_tools_version(project: ProjectInfo) -> str:
    """Choose a modern Build Tools release with published ARM64 binaries."""

    if project.compile_sdk is not None:
        if project.compile_sdk >= 37:
            return f"{project.compile_sdk}.0.0"
        if project.compile_sdk == 36:
            return "36.0.0"
        if project.compile_sdk == 35:
            return "35.0.1"
    return "35.0.1"


def ensure_android_sdk(
    project: ProjectInfo,
    sdk_root: Path | None,
    cache_root: Path,
    *,
    force_setup: bool,
    platform_override: str,
    build_tools_override: str,
    ndk_override: str,
    cmake_override: str,
    skip_install: bool,
    offline: bool,
    env: MutableMapping[str, str],
    dry_run: bool,
    arm_build_tools_provider: str = "auto",
    ndk_host_provider: str = "auto",
    arm64_ndk_url: str | None = None,
    qemu_command: str | None = None,
    qemu_ld_prefix: str | None = "auto",
    qemu_sysroot_url: str | None = None,
    qemu_sysroot_sha256: str | None = None,
    qemu_install: str = "auto",
    allow_managed_fallback: bool = True,
    bootstrap_downloads: str = "auto",
    bootstrap_network: str = "allow",
) -> tuple[Path, str | None, str | None, str | None, str | None]:
    if not project.android:
        fail("Internal error: ensure_android_sdk called for a non-Android project")
    if sdk_root is None:
        if skip_install:
            fail("Android SDK not found and --skip-sdk-install was used. Set ANDROID_HOME/ANDROID_SDK_ROOT or pass --sdk-root.")
        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            fail_bootstrap_blocked(
                "Android SDK",
                downloads=bootstrap_downloads,
                network=bootstrap_network,
                checked=[
                    "local.properties sdk.dir",
                    "$ANDROID_HOME",
                    "$ANDROID_SDK_ROOT",
                    str(Path.home() / "Android" / "Sdk"),
                    str(Path.home() / "android-sdk"),
                    "/opt/android-sdk",
                    "/usr/lib/android-sdk",
                ],
            )
        sdk_root = managed_sdk_root_for_host(cache_root)
        sdk_root.mkdir(parents=True, exist_ok=True)
        configure_android_sdk_env(env, sdk_root)
        info(f"Using managed Android SDK: {sdk_root}")

    platform_id: str | None
    if platform_override == "none":
        platform_id = None
    elif platform_override != "auto":
        platform_id = platform_override if platform_override.startswith("android-") else f"android-{platform_override}"
    else:
        platform_id = f"android-{project.compile_sdk}" if project.compile_sdk else None

    if build_tools_override == "none":
        build_tools = None
    elif build_tools_override != "auto":
        build_tools = build_tools_override
    else:
        if project.build_tools:
            build_tools = project.build_tools
        else:
            installed_versions = installed_build_tools_versions(sdk_root)
            build_tools = (
                installed_versions[0]
                if installed_versions
                else default_build_tools_version(project)
            )

    if ndk_override == "none":
        ndk_version = None
    elif ndk_override != "auto":
        ndk_version = ndk_override
    elif project.ndk_version:
        ndk_version = project.ndk_version
    elif project.native_build and project.agp_major is not None and project.agp_major >= 9:
        # AGP 9.0 defaults to NDK r28c. Installing it here avoids AGP's opaque
        # configuration-time auto-install path and lets us repair corrupt downloads.
        ndk_version = "28.2.13676358"
    else:
        ndk_version = None

    if cmake_override == "none":
        cmake_version = None
    elif cmake_override != "auto":
        cmake_version = cmake_override
    else:
        cmake_version = project.cmake_version

    def collect_missing(root: Path) -> tuple[list[str], list[Path]]:
        missing_packages: list[str] = []
        incomplete: list[Path] = []
        if platform_id and not (root / "platforms" / platform_id / "android.jar").is_file():
            missing_packages.append(f"platforms;{platform_id}")
            incomplete.append(root / "platforms" / platform_id)
        if build_tools and not (root / "build-tools" / build_tools).is_dir():
            missing_packages.append(f"build-tools;{build_tools}")
            incomplete.append(root / "build-tools" / build_tools)
        if ndk_version and not ndk_is_ready(root, ndk_version):
            missing_packages.append(f"ndk;{ndk_version}")
            incomplete.append(root / "ndk" / ndk_version)
        if cmake_version and not cmake_is_ready(root, cmake_version):
            missing_packages.append(f"cmake;{cmake_version}")
            incomplete.append(root / "cmake" / cmake_version)
        return missing_packages, incomplete

    missing, incomplete_paths = collect_missing(sdk_root)
    native_replacements = (
        arm64_build_tools_replacements_needed(sdk_root, build_tools)
        if arm_build_tools_provider != "none"
        else []
    )

    if (
        (missing or native_replacements)
        and not skip_install
        and not sdk_root_is_writable_for_install(sdk_root)
    ):
        if not allow_managed_fallback:
            fail(
                "The selected Android SDK requires additional packages but is not writable: "
                f"{sdk_root}. Choose a writable --sdk-root."
            )
        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            fail_bootstrap_blocked(
                "Android SDK packages",
                downloads=bootstrap_downloads,
                network=bootstrap_network,
                checked=[str(sdk_root), *missing, *[str(item) for item in native_replacements]],
            )

        previous_sdk_root = sdk_root
        sdk_root = managed_sdk_root_for_host(cache_root)
        if sdk_root == previous_sdk_root:
            fail(
                "The managed Android SDK requires additional packages but is not writable: "
                f"{sdk_root}. Check the cache directory permissions."
            )
        if sdk_root.name != "android-sdk":
            warn(
                "The default managed Android SDK cache contains incompatible host "
                f"build tools; using {sdk_root}"
            )
        warn(
            f"Selected Android SDK is read-only and needs additional packages: {previous_sdk_root}; "
            f"falling back to managed SDK: {sdk_root}"
        )
        sdk_root.mkdir(parents=True, exist_ok=True)
        if not sdk_root_is_writable_for_install(sdk_root):
            fail(f"Managed Android SDK path is not writable: {sdk_root}")
        configure_android_sdk_env(env, sdk_root)

        if build_tools_override == "auto" and not project.build_tools:
            installed_versions = installed_build_tools_versions(sdk_root)
            build_tools = (
                installed_versions[0]
                if installed_versions
                else default_build_tools_version(project)
            )
        missing, incomplete_paths = collect_missing(sdk_root)
        native_replacements = (
            arm64_build_tools_replacements_needed(sdk_root, build_tools)
            if arm_build_tools_provider != "none"
            else []
        )

    if missing:
        if skip_install:
            fail("Missing Android SDK packages: " + ", ".join(missing))
        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            fail_bootstrap_blocked(
                "Android SDK packages",
                downloads=bootstrap_downloads,
                network=bootstrap_network,
                checked=[str(sdk_root), *missing],
            )
        manager = find_sdkmanager(sdk_root)
        if manager is None:
            if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
                fail_bootstrap_blocked(
                    "Android SDK command-line tools",
                    downloads=bootstrap_downloads,
                    network=bootstrap_network,
                    checked=[str(candidate) for candidate in sdkmanager_candidates(sdk_root)] + ["sdkmanager in PATH"],
                )
            manager = ensure_sdkmanager_available(
                sdk_root,
                cache_root,
                offline=offline,
                force=force_setup,
                dry_run=dry_run,
            )
        if offline:
            fail("Missing Android SDK packages in offline mode: " + ", ".join(missing))
        heading("Installing missing Android SDK packages")
        info(", ".join(missing))
        run(
            [manager, f"--sdk_root={sdk_root}", "--licenses"],
            env=env,
            input_text="y\n" * 200,
            check=False,
            dry_run=dry_run,
        )
        for path in incomplete_paths:
            remove_path(path, dry_run=dry_run)
        install_sdk_packages_with_repair(
            manager,
            sdk_root,
            missing,
            env=env,
            dry_run=dry_run,
        )
        if cmake_version:
            ensure_host_cmake_compatible(sdk_root, cmake_version, dry_run=dry_run)
        if not dry_run:
            validation_failures: list[str] = []
            if ndk_version and not ndk_is_ready(sdk_root, ndk_version):
                validation_failures.append(f"NDK {ndk_version}")
            if cmake_version and not cmake_is_ready(sdk_root, cmake_version):
                validation_failures.append(f"CMake {cmake_version}")
            if validation_failures:
                fail("SDK package validation failed after installation: " + ", ".join(validation_failures))
    else:
        if cmake_version:
            ensure_host_cmake_compatible(sdk_root, cmake_version, dry_run=dry_run)
        heading("Android SDK is already ready")
        if platform_id:
            info(f"Platform: {platform_id}")
        if build_tools:
            info(f"Build Tools: {build_tools}")
        if ndk_version:
            info(f"NDK: {ndk_version}")
        if cmake_version:
            info(f"CMake: {cmake_version}")
    if native_replacements and not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
        fail_bootstrap_blocked(
            "Android SDK ARM build-tools repair",
            downloads=bootstrap_downloads,
            network=bootstrap_network,
            checked=[str(sdk_root / "build-tools" / str(build_tools or "")), *native_replacements],
        )
    ensure_arm64_build_tools_compatible(
        sdk_root,
        build_tools,
        cache_root,
        provider=arm_build_tools_provider,
        offline=offline or not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network),
        force=force_setup,
        dry_run=dry_run,
    )
    ensure_ndk_host_compatible(
        sdk_root,
        ndk_version,
        cache_root,
        provider=ndk_host_provider,
        arm64_ndk_url=arm64_ndk_url,
        qemu_command=qemu_command,
        qemu_ld_prefix=qemu_ld_prefix,
        qemu_sysroot_url=qemu_sysroot_url,
        qemu_sysroot_sha256=qemu_sysroot_sha256,
        qemu_install=qemu_install,
        offline=offline or not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network),
        force=force_setup,
        dry_run=dry_run,
    )
    update_local_properties(project.root, sdk_root)
    return sdk_root, platform_id, build_tools, ndk_version, cmake_version


def wrapper_distribution_version(project_root: Path) -> str | None:
    properties = project_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not properties.is_file():
        return None
    text = properties.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"gradle-([0-9][^-]*)-(?:bin|all)\.zip", text)
    return match.group(1) if match else None


def gradle_version(command: Path) -> str | None:
    result = subprocess.run([str(command), "--version"], text=True, capture_output=True, check=False)
    match = re.search(r"^Gradle\s+([^\s]+)", f"{result.stdout}\n{result.stderr}", re.MULTILINE)
    return match.group(1) if match else None


def managed_gradle(
    version: str,
    cache_root: Path,
    *,
    offline: bool,
    force: bool,
) -> Path:
    home = cache_root / "gradle" / f"gradle-{version}"
    launcher = home / "bin" / "gradle"
    if launcher.is_file() and not force:
        return ensure_executable(launcher, "Gradle launcher")
    archive = cache_root / "downloads" / f"gradle-{version}-bin.zip"
    download(
        f"https://services.gradle.org/distributions/gradle-{version}-bin.zip",
        archive,
        offline=offline,
        force=force,
    )
    if home.exists():
        shutil.rmtree(home)
    home.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gradle-build-gradle-") as temporary_dir:
        temporary = Path(temporary_dir)
        extract_zip(archive, temporary)
        extracted = temporary / f"gradle-{version}"
        if not extracted.is_dir():
            fail(f"Unexpected Gradle archive layout: {archive}")
        shutil.copytree(extracted, home)
    return ensure_executable(launcher, "Gradle launcher")


def version_key(value: str | None) -> tuple[int, ...]:
    """Return a numeric comparison key for versions such as 8.13 or 8.13.2."""
    if not value:
        return ()
    match = re.match(r"\s*(\d+(?:\.\d+)*)", value)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def gradle_version_matches(actual: str | None, requested: str) -> bool:
    """Require the requested Gradle feature version, allowing patch releases.

    For example, requested 8.13 accepts 8.13 and 8.13.1, but rejects 8.6 and
    8.14. This is intentionally conservative for Android Gradle Plugin builds.
    """
    actual_key = version_key(actual)
    requested_key = version_key(requested)
    if not actual_key or not requested_key:
        return False
    width = len(requested_key)
    return actual_key[:width] == requested_key


def gradle_wrapper_distribution_cached(
    project_root: Path,
    version: str,
    *,
    gradle_user_home: Path | None = None,
) -> bool:
    gradle_user_home = (gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")).expanduser()
    custom = gradle_user_home / "devtool-bootstrap" / f"gradle-{version}" / "bin" / "gradle"
    if custom.is_file():
        return True
    wrapper_root = gradle_user_home / "wrapper" / "dists"
    if wrapper_root.is_dir():
        pattern = f"gradle-{version}-*"
        for candidate in wrapper_root.glob(f"{pattern}/*/gradle-{version}/bin/gradle"):
            if candidate.is_file():
                return True
    properties = project_root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if properties.is_file():
        try:
            text = properties.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        match = re.search(r"distributionPath\s*=\s*(.+)$", text, re.MULTILINE)
        distribution_path = match.group(1).strip() if match else ""
        if distribution_path and (project_root / distribution_path).exists():
            return True
    return False


def select_gradle(
    project_root: Path,
    *,
    explicit: str | None,
    version: str | None,
    cache_root: Path,
    offline: bool,
    force: bool,
    bootstrap_downloads: str = "auto",
    bootstrap_network: str = "allow",
    gradle_user_home: Path | None = None,
) -> Path:
    wrapper = project_root / "gradlew"
    requested = version or wrapper_distribution_version(project_root) or DEFAULT_GRADLE_VERSION

    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            found = shutil.which(explicit)
            if not found:
                fail(f"Gradle command not found: {explicit}")
            candidate = Path(found)
        candidate = ensure_executable(candidate.resolve(), "Gradle launcher")
        actual = gradle_version(candidate)
        if not gradle_version_matches(actual, requested):
            fail(
                f"Explicit Gradle launcher is incompatible: {candidate} reports "
                f"{actual or 'an unknown version'}, but this build requires Gradle "
                f"{requested}. Choose another launcher or pass --gradle-version."
            )
        info(f"Using explicit compatible Gradle: {candidate} ({actual})")
        return candidate

    if wrapper.is_file() and not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
        wrapper_version = wrapper_distribution_version(project_root)
        if wrapper_version and gradle_wrapper_distribution_cached(project_root, wrapper_version, gradle_user_home=gradle_user_home):
            info(f"Using project Gradle wrapper with cached distribution: {wrapper} ({wrapper_version})")
            return ensure_executable(wrapper, "Gradle wrapper")
        system = shutil.which("gradle")
        if system:
            system_path = ensure_executable(Path(system), "Gradle launcher")
            actual = gradle_version(system_path)
            if gradle_version_matches(actual, requested):
                info(f"Using compatible Gradle from PATH: {system_path} ({actual})")
                return system_path
        fail_bootstrap_blocked(
            "Gradle wrapper distribution",
            downloads=bootstrap_downloads,
            network=bootstrap_network,
            checked=[
                str(wrapper),
                str((gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")).expanduser() / "wrapper" / "dists"),
                str((gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")).expanduser() / "devtool-bootstrap"),
                "gradle in PATH",
            ],
        )

    if wrapper.is_file():
        wrapper_version = wrapper_distribution_version(project_root)
        if wrapper_version and not gradle_version_matches(wrapper_version, requested):
            warn(
                f"Project wrapper declares Gradle {wrapper_version}, while Gradle "
                f"{requested} is requested. The wrapper remains authoritative."
            )
        info(
            f"Using project Gradle wrapper: {wrapper}"
            + (f" ({wrapper_version})" if wrapper_version else "")
        )
        return ensure_executable(wrapper, "Gradle wrapper")

    system = shutil.which("gradle")
    if system:
        system_path = ensure_executable(Path(system), "Gradle launcher")
        actual = gradle_version(system_path)
        if gradle_version_matches(actual, requested):
            info(f"Using compatible Gradle from PATH: {system_path} ({actual})")
            return system_path
        warn(
            f"Ignoring incompatible Gradle from PATH: {system_path} "
            f"({actual or 'unknown'}); Gradle {requested} is required."
        )

    if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
        fail_bootstrap_blocked(
            "Gradle",
            downloads=bootstrap_downloads,
            network=bootstrap_network,
            checked=[
                str(wrapper),
                "gradle in PATH",
                str(cache_root / "gradle" / f"gradle-{requested}" / "bin" / "gradle"),
            ],
        )
    info(f"Using managed Gradle {requested}")
    return managed_gradle(requested, cache_root, offline=offline, force=force)


def default_tasks(project: ProjectInfo) -> list[str]:
    if project.android_application:
        return ["assembleDebug"]
    if project.android:
        return ["assemble"]
    return ["build"]


def discover_artifacts(root: Path, since: float) -> list[Path]:
    patterns = (
        "**/build/outputs/**/*.apk",
        "**/build/outputs/**/*.aab",
        "**/build/outputs/**/*.aar",
        "**/build/libs/*.jar",
        "**/build/libs/*.war",
        "**/build/distributions/*.zip",
        "**/build/distributions/*.tar",
    )
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime + 2 < since:
                    continue
            except OSError:
                continue
            found[str(path.resolve())] = path.resolve()
    return sorted(found.values(), key=lambda path: (path.suffix, str(path)))


def artifact_module(source: Path, project_root: Path) -> str:
    try:
        relative = source.relative_to(project_root)
    except ValueError:
        return "artifact"
    parts = list(relative.parts)
    try:
        build_index = parts.index("build")
    except ValueError:
        return "artifact"
    if build_index == 0:
        return "app"
    return "-".join(parts[:build_index]) or "app"


def artifact_variant(source: Path) -> str:
    """Infer a useful build variant/classifier from standard Gradle output paths."""
    parts = source.parts
    stem = source.stem
    stem_lower = stem.lower()
    for marker in ("apk", "bundle", "aar"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts) - 1:
                candidate = parts[index + 1]
                if candidate not in {"outputs", "intermediates"}:
                    classifier = slugify_name(candidate)
                    if "unsigned" in stem_lower and "unsigned" not in classifier:
                        classifier += "-unsigned"
                    return classifier
    for prefix in ("app-", "application-", "bundle-"):
        if stem.lower().startswith(prefix):
            return slugify_name(stem[len(prefix):])
    if stem.lower() in {"app", "application", "bundle", "artifact"}:
        return ""
    return slugify_name(stem)


def planned_artifact_names(
    artifacts: Sequence[Path],
    project_root: Path,
    project_name: str,
    output_name: str | None,
) -> list[str]:
    project_slug = slugify_name(project_name)
    modules = [artifact_module(source, project_root) for source in artifacts]
    meaningful_modules = {module for module in modules if module not in {"app", "artifact"}}
    multiple_modules = len(meaningful_modules) > 1
    names: list[str] = []
    used: set[str] = set()

    for source, module in zip(artifacts, modules):
        suffix = source.suffix.lower()
        variant = artifact_variant(source)
        if output_name:
            requested = Path(output_name).name
            requested_path = Path(requested)
            base = requested_path.stem if requested_path.suffix else requested
            explicit_suffix = requested_path.suffix.lower()
            if explicit_suffix and len(artifacts) == 1:
                suffix = explicit_suffix
            base_slug = slugify_name(base)
        else:
            base_slug = project_slug

        descriptors: list[str] = []
        module_slug = slugify_name(module)
        if module not in {"app", "artifact"} and (multiple_modules or module_slug not in base_slug):
            descriptors.append(module_slug)
        if variant and variant not in base_slug and variant not in descriptors:
            descriptors.append(variant)

        candidate_base = "-".join([base_slug, *descriptors])
        candidate = f"{candidate_base}{suffix}"
        serial = 2
        while candidate in used:
            candidate = f"{candidate_base}-{serial}{suffix}"
            serial += 1
        used.add(candidate)
        names.append(candidate)
    return names


def copy_artifacts(
    artifacts: Sequence[Path],
    project_root: Path,
    project_name: str,
    output_dir: Path,
    output_name: str | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    names = planned_artifact_names(artifacts, project_root, project_name, output_name)
    for source, name in zip(artifacts, names):
        destination = output_dir / name
        if destination.resolve() != source:
            shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def normalize_gradle_arguments(arguments: Sequence[str], *, plain_console: bool = True) -> list[str]:
    """Return Gradle arguments with at most one console option.

    Devtool owns the default ``--console=plain`` policy. Integrations may add
    init scripts and users may pass an explicit console mode after ``--``;
    duplicate or conflicting console switches are collapsed so Gradle 9 does
    not reject the command. The last explicit console value wins.
    """
    normalized: list[str] = []
    console_value: str | None = None
    index = 0
    values = list(arguments)
    while index < len(values):
        value = str(values[index])
        if value.startswith("--console="):
            candidate = value.split("=", 1)[1].strip()
            if candidate:
                console_value = candidate
            index += 1
            continue
        if value == "--console":
            if index + 1 < len(values):
                candidate = str(values[index + 1]).strip()
                if candidate:
                    console_value = candidate
                index += 2
                continue
            index += 1
            continue
        normalized.append(value)
        index += 1

    if console_value is None and plain_console:
        console_value = "plain"
    if console_value is not None:
        normalized.append(f"--console={console_value}")
    return normalized


def gradle_arguments_with_aapt2_override(
    arguments: Sequence[str],
    aapt2: Path | None,
    *,
    plain_console: bool = True,
) -> list[str]:
    """Normalize Gradle arguments and silence the custom-AAPT2 AGP warning.

    Android Gradle Plugin labels ``android.aapt2FromMavenOverride`` as an
    experimental option. Whenever Devtool injects that property, also add it to
    ``android.suppressUnsupportedOptionWarnings``. Existing comma-separated
    suppressions from Gradle passthrough arguments are retained.
    """

    normalized = normalize_gradle_arguments(arguments, plain_console=plain_console)
    if aapt2 is None:
        return normalized

    suppression_prefix = f"-P{UNSUPPORTED_OPTION_WARNINGS_PROPERTY}="
    suppressions: list[str] = []
    remaining: list[str] = []
    for argument in normalized:
        value = str(argument)
        if value.startswith(suppression_prefix):
            configured = value[len(suppression_prefix):]
            for item in configured.split(","):
                item = item.strip()
                if item and item not in suppressions:
                    suppressions.append(item)
            continue
        remaining.append(value)

    if AAPT2_OVERRIDE_PROPERTY not in suppressions:
        suppressions.append(AAPT2_OVERRIDE_PROPERTY)

    properties = [
        f"-P{AAPT2_OVERRIDE_PROPERTY}={aapt2}",
        f"-P{UNSUPPORTED_OPTION_WARNINGS_PROPERTY}={','.join(suppressions)}",
    ]

    console = [item for item in remaining if item.startswith("--console=")]
    remaining = [item for item in remaining if not item.startswith("--console=")]
    return [*properties, *remaining, *console]


def split_gradle_passthrough_args(raw_args: Sequence[str]) -> tuple[list[str], list[str]]:
    raw: list[str] = []
    extra_gradle_args: list[str] = []
    iterator = iter(raw_args)
    for value in iterator:
        if value == "--":
            extra_gradle_args.extend(iterator)
            break
        if value.startswith("-P") and value != "-P":
            extra_gradle_args.append(value)
            continue
        raw.append(value)
    return raw, extra_gradle_args


def parse_args(raw_args: Sequence[str] | None = None) -> argparse.Namespace:
    raw, extra_gradle_args = split_gradle_passthrough_args(sys.argv[1:] if raw_args is None else raw_args)

    parser = argparse.ArgumentParser(
        description="Smart generic Gradle builder for JVM and Android projects.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("project", nargs="?", type=Path, default=Path.cwd(), help="Gradle project directory")
    parser.add_argument("-p", "--project-dir", type=Path, help="Gradle project directory (named form)")
    parser.add_argument(
        "-x", "--chroot",
        nargs="?",
        const="always",
        choices=("auto", "always", "never"),
        default="auto",
        help="When launched by Termux, automatically build inside chrootctl; omit the value to require it",
    )
    parser.add_argument("-X", "--no-chroot", dest="chroot", action="store_const", const="never", help="Never delegate from Termux to a chroot")
    parser.add_argument("-N", "--chroot-name", default=os.environ.get("GRADLE_BUILD_CHROOT_NAME", "androidos"), help="chrootctl chroot name")
    parser.add_argument(
        "-H", "--chroot-termux-home",
        type=Path,
        default=Path(os.environ.get("GRADLE_BUILD_CHROOT_TERMUX_HOME", "/home/termux")),
        help="Path where the Termux home is mounted inside the chroot",
    )
    parser.add_argument("-t", "--task", action="append", dest="tasks", help="Gradle task; repeat for multiple tasks")
    parser.add_argument("-c", "--clean", action="store_true", help="Run clean before requested/default tasks")
    parser.add_argument("-o", "--output-dir", type=Path, help="Copy newly produced artifacts here")
    parser.add_argument("-n", "--output-name", help="Base filename for copied artifacts; extension is inferred unless a single artifact is built")
    parser.add_argument("-Q", "--no-artifact-scan", action="store_true", help="Do not scan for produced APK/AAB/AAR/JAR artifacts")

    parser.add_argument("-a", "--aapt2", type=Path, help="Use this AAPT2 binary; shared-storage files are staged automatically")
    parser.add_argument(
        "-A", "--aapt2-provider",
        choices=("auto", "revanced", "none"),
        default="auto",
        help="AAPT2 source when --aapt2 is omitted; auto uses ReVanced on ARM Android builds",
    )
    parser.add_argument("-R", "--aapt2-release", default="latest", help="ReVanced AAPT2 tag or latest")
    parser.add_argument(
        "--arm-build-tools-provider",
        choices=("auto", "commit451", "none"),
        default="auto",
        help=(
            "ARM64 provider for SDK aidl/zipalign/split-select; auto repairs "
            "incompatible Google binaries from Commit451 releases"
        ),
    )

    parser.add_argument("-s", "--sdk-root", type=Path, help="Android SDK root")
    parser.add_argument("-P", "--android-platform", default="auto", help="android-36, 36, auto, or none")
    parser.add_argument("-B", "--build-tools", default="auto", help="Build Tools version, auto, or none")
    parser.add_argument("--ndk-version", default="auto", help="NDK version, auto, or none")
    parser.add_argument(
        "--ndk-host-provider",
        choices=sorted(NDK_HOST_PROVIDERS),
        default=os.environ.get("DEVTOOL_NDK_HOST_PROVIDER", "auto"),
        help=(
            "Host compatibility strategy for Android NDK on ARM Linux/Termux/chroot: "
            "auto prefers configured ARM64 NDK first, then existing runnable NDK/QEMU, then QEMU bootstrap; google requires official host binary, "
            "arm64 requires an ARM64-host NDK, qemu wraps Google x86_64 tools, none warns only"
        ),
    )
    parser.add_argument("--arm64-ndk-url", default=os.environ.get("DEVTOOL_ARM64_NDK_URL", ""), help="Trusted unofficial ARM64-host NDK archive URL")
    parser.add_argument("--qemu-command", default=os.environ.get("DEVTOOL_QEMU_X86_64", ""), help="qemu-x86_64 executable used for x86_64 NDK fallback")
    parser.add_argument("--qemu-ld-prefix", default=os.environ.get("DEVTOOL_QEMU_LD_PREFIX", "auto"), help="qemu -L loader prefix, auto, none, or a path")
    parser.add_argument("--qemu-sysroot-url", default=os.environ.get("DEVTOOL_QEMU_SYSROOT_URL", ""), help="amd64 rootfs archive used to bootstrap qemu -L when auto prefix is missing")
    parser.add_argument("--qemu-sysroot-sha256", default=os.environ.get("DEVTOOL_QEMU_SYSROOT_SHA256", ""), help="optional SHA256 for --qemu-sysroot-url; defaults to the built-in Ubuntu Base checksum")
    parser.add_argument("--qemu-install", choices=sorted(QEMU_INSTALL_POLICIES), default=os.environ.get("DEVTOOL_QEMU_INSTALL", "auto"), help="Whether devtool may install qemu-user when needed")
    parser.add_argument("--cmake-version", default="auto", help="CMake version, auto, or none")
    parser.add_argument("-I", "--skip-sdk-install", action="store_true", help="Never install missing SDK packages")
    parser.add_argument("--auto-sdk", action="store_true", help="Explicitly allow automatic SDK package downloads and managed SDK creation")
    parser.add_argument(
        "--toolchain-downloads",
        choices=sorted(BOOTSTRAP_DOWNLOAD_POLICIES),
        default="auto",
        help="Devtool-managed toolchain provisioning policy",
    )
    parser.add_argument(
        "--bootstrap-network",
        choices=sorted(BOOTSTRAP_NETWORK_POLICIES),
        default="allow",
        help="Whether Devtool-managed bootstrap downloads may use the network",
    )

    parser.add_argument("-j", "--java-home", type=Path, help="JDK home")
    parser.add_argument(
        "-D", "--system-deps",
        choices=("auto", "always", "never"),
        default="auto",
        help="Install missing Java/CA packages only when needed, always refresh them, or never install them",
    )
    parser.add_argument("-K", "--skip-system-deps", dest="system_deps", action="store_const", const="never", help="Alias for --system-deps never")
    parser.add_argument("-J", "--min-java", type=int, help="Minimum Java version; inferred when omitted")
    parser.add_argument("-g", "--gradle-command", help="Explicit Gradle or gradlew executable")
    parser.add_argument("-G", "--gradle-version", help="Managed Gradle version when no wrapper is available")
    parser.add_argument("--gradle-user-home", type=Path, help="GRADLE_USER_HOME for this build")
    parser.add_argument("-k", "--cache-dir", type=Path, default=devtool_cache_dir() / APP_NAME)
    parser.add_argument("-w", "--max-workers", type=int, default=max(1, min(4, os.cpu_count() or 2)))
    parser.add_argument("--gradle-jvm-heap-mb", type=int, default=0, help="Cap the Gradle JVM heap in MiB")
    parser.add_argument("--gradle-metaspace-mb", type=int, default=0, help="Cap the Gradle JVM metaspace in MiB")
    parser.add_argument("--memory-guard-mb", type=int, default=0, help="Stop Gradle before system free memory remains below this threshold")
    parser.add_argument("--cpu-limit", type=int, default=0, help="Limit validation to this many logical CPUs when taskset is available")
    parser.add_argument("--low-priority", action="store_true", help="Run Gradle with reduced CPU and I/O scheduler priority")
    parser.add_argument("--constrained", action="store_true", help="Use memory-saving Gradle and Kotlin settings for Android-hosted validation")
    parser.add_argument("-O", "--offline", action="store_true", help="Disable downloads and Gradle network access")
    parser.add_argument("-f", "--force-setup", action="store_true", help="Refresh managed Gradle/AAPT2 downloads")
    parser.add_argument("-m", "--no-daemon", action="store_true", help="Pass --no-daemon to Gradle")
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Enable parallel project execution for multi-module builds",
    )
    parser.add_argument(
        "--build-cache",
        action="store_true",
        help="Enable the persistent Gradle build cache",
    )
    parser.add_argument(
        "--daemon-idle-timeout-ms",
        type=int,
        default=900_000,
        help="Keep a reusable Gradle daemon alive for this many idle milliseconds",
    )
    parser.add_argument(
        "--gradle-lock-timeout-ms",
        type=int,
        default=int(os.environ.get("DEVTOOL_GRADLE_LOCK_TIMEOUT_MS", "600000") or "600000"),
        help="Gradle cache/file-lock timeout in milliseconds",
    )
    parser.add_argument(
        "--pre-stop-gradle",
        action="store_true",
        help="Stop stale Gradle daemons before probing or running Gradle",
    )
    parser.add_argument(
        "--stop-gradle",
        action="store_true",
        help="Stop this Gradle daemon after the build instead of retaining it for reuse",
    )
    parser.add_argument(
        "-S", "--stacktrace",
        dest="stacktrace",
        action="store_true",
        default=True,
        help="Show Gradle stacktraces (enabled by default)",
    )
    parser.add_argument(
        "--no-stacktrace",
        dest="stacktrace",
        action="store_false",
        help="Do not pass --stacktrace to Gradle",
    )
    parser.add_argument(
        "--fail-fast",
        dest="continue_on_failure",
        action="store_false",
        default=True,
        help="Stop after the first task failure instead of passing --continue",
    )
    parser.add_argument(
        "--rich-console",
        dest="plain_console",
        action="store_false",
        default=True,
        help="Use Gradle's rich console instead of --console=plain",
    )
    parser.add_argument("-y", "--dry-run", action="store_true", help="Print setup/build commands without running them")
    args = parser.parse_args(raw)
    if args.project_dir:
        args.project = args.project_dir
    if args.max_workers < 1:
        parser.error("--max-workers must be at least 1")
    if args.gradle_jvm_heap_mb < 0:
        parser.error("--gradle-jvm-heap-mb cannot be negative")
    if args.gradle_metaspace_mb < 0:
        parser.error("--gradle-metaspace-mb cannot be negative")
    if args.memory_guard_mb < 0:
        parser.error("--memory-guard-mb cannot be negative")
    if args.cpu_limit < 0:
        parser.error("--cpu-limit cannot be negative")
    if args.daemon_idle_timeout_ms < 0:
        parser.error("--daemon-idle-timeout-ms cannot be negative")
    if args.gradle_lock_timeout_ms < 0:
        parser.error("--gradle-lock-timeout-ms cannot be negative")
    args.toolchain_downloads = normalize_bootstrap_downloads(args.toolchain_downloads)
    args.bootstrap_network = normalize_bootstrap_network(args.bootstrap_network)
    if args.offline:
        args.toolchain_downloads = "never"
        args.bootstrap_network = "deny"
    args.gradle_args = extra_gradle_args
    return args


def gradle_process_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,uid=,args="],
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return []
    uid = os.getuid() if hasattr(os, "getuid") else None
    protected = {os.getpid(), os.getppid()}
    pids: list[int] = []
    signatures = (
        "org.gradle.launcher.daemon.bootstrap.GradleDaemon",
        "org.gradle.wrapper.GradleWrapperMain",
        "org.gradle.launcher.GradleMain",
    )
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid, process_uid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid in protected or (uid is not None and process_uid != uid):
            continue
        if any(signature in parts[2] for signature in signatures):
            pids.append(pid)
    return pids


def stop_gradle_processes(
    gradle: Path,
    project_root: Path,
    env: Mapping[str, str],
    *,
    dry_run: bool,
) -> None:
    heading("Gradle cleanup")
    run([gradle, "--stop"], cwd=project_root, env=env, check=False, dry_run=dry_run)
    if dry_run:
        return
    pids = gradle_process_pids()
    for pid in pids:
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.time() + 2.0
    while time.time() < deadline and gradle_process_pids():
        time.sleep(0.1)
    remaining = gradle_process_pids()
    for pid in remaining:
        try:
            os.kill(pid, 9)
        except (ProcessLookupError, PermissionError):
            pass
    info(f"Stopped {len(set(pids + remaining))} remaining Gradle process(es)")


def should_cleanup_gradle(args: argparse.Namespace) -> bool:
    """Return whether this run should release stale or single-use Gradle processes."""

    return bool(
        args.stop_gradle
        or args.pre_stop_gradle
        or (args.no_daemon and args.constrained)
        or os.environ.get("DEVTOOL_ANDROID_CLEAN_GRADLE", "").strip().lower() in {"1", "true", "yes", "on"}
    )


def gradle_jvmargs(args: argparse.Namespace) -> str | None:
    """Build one bounded ``org.gradle.jvmargs`` value for constrained runs."""

    explicit_heap = int(args.gradle_jvm_heap_mb or 0)
    explicit_metaspace = int(args.gradle_metaspace_mb or 0)
    explicit_cpu = int(args.cpu_limit or 0)
    if not args.constrained and explicit_heap <= 0 and explicit_metaspace <= 0 and explicit_cpu <= 0:
        return None

    heap = explicit_heap or int(os.environ.get("DEVTOOL_GRADLE_JVM_HEAP_MB", "") or "0") or 1024
    # ``memory_guard_mb`` is a system-pressure warning threshold, not a JVM
    # heap budget.  Comparing the two silently changed an explicitly requested
    # 768 MiB heap into 512 MiB and caused D8/R8 to fail while plenty of system
    # memory and swap remained available.  Resource safety is enforced by the
    # adaptive watchdog, worker cap, CPU cap and low-priority scheduling.
    metaspace = (
        explicit_metaspace
        or int(os.environ.get("DEVTOOL_GRADLE_METASPACE_MB", "") or "0")
        or min(1024, max(512 if args.constrained else 384, int(heap * 0.75)))
    )
    cpu_limit = explicit_cpu or int(os.environ.get("DEVTOOL_VALIDATION_CPU_LIMIT", "") or "0")
    parts = [
        f"-Xmx{heap}m",
        f"-XX:MaxMetaspaceSize={metaspace}m",
        "-XX:ReservedCodeCacheSize=128m",
        "-Dfile.encoding=UTF-8",
    ]
    if cpu_limit > 0:
        parts.append(f"-XX:ActiveProcessorCount={cpu_limit}")
    return " ".join(parts)


def apply_low_priority(args: argparse.Namespace) -> None:
    """Lower the builder process priority while retaining per-command ionice support."""

    if not args.low_priority:
        return
    try:
        os.nice(5)
    except (AttributeError, OSError):
        pass


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    delegated = maybe_reexec_in_chroot(raw_args)
    if delegated is not None:
        return delegated
    args = parse_args(raw_args)
    if args.toolchain_downloads == "force":
        args.force_setup = True
    provisioning_allowed = bootstrap_provisioning_allowed(
        args.toolchain_downloads,
        args.bootstrap_network,
    )
    apply_low_priority(args)
    project_root = resolve_project_root(args.project)
    project = inspect_project(project_root)
    cache_root = args.cache_dir.expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)

    heading("Project inspection")
    info(f"Project: {project_root}")
    info(f"Project name: {project.name}")
    info(f"Type: {'Android application' if project.android_application else 'Android Gradle project' if project.android else 'Gradle/JVM project'}")
    if project.compile_sdk:
        info(f"compileSdk: {project.compile_sdk}")
    if project.agp_major:
        info(f"Android Gradle Plugin major: {project.agp_major}")
    if project.native_build:
        info("Native build: yes")
    if project.ndk_version:
        info(f"Declared NDK: {project.ndk_version}")
    if project.cmake_version:
        info(f"Declared CMake: {project.cmake_version}")

    requirement = infer_java_requirement(project)
    minimum_java = args.min_java or requirement.minimum
    java_home: Path | None = None
    try:
        java_home = find_java_home(args.java_home, minimum_java, exact=requirement.exact if args.java_home is None else None)
    except BuildError:
        java_home = None
    need_java = java_home is None
    need_certificates = not certificate_store_available()
    if args.system_deps == "always":
        if not provisioning_allowed:
            fail_bootstrap_blocked(
                "System dependency installation",
                downloads=args.toolchain_downloads,
                network=args.bootstrap_network,
                checked=[f"java_home={args.java_home}" if args.java_home else "java in PATH", "system CA certificate store"],
            )
        install_missing_system_dependencies(
            need_java=True,
            need_certificates=need_certificates,
            offline=args.offline,
            java_version=minimum_java,
        )
        java_home = find_java_home(args.java_home, minimum_java, exact=requirement.exact if args.java_home is None else None)
    elif args.system_deps == "auto" and (need_java or need_certificates):
        if not provisioning_allowed:
            missing = []
            if need_java:
                missing.append(f"Java {requirement.exact or str(minimum_java) + '+'}")
            if need_certificates:
                missing.append("CA certificates")
            fail_bootstrap_blocked(
                "System dependencies",
                downloads=args.toolchain_downloads,
                network=args.bootstrap_network,
                checked=[*missing, f"java_home={args.java_home}" if args.java_home else "java in PATH"],
            )
        install_missing_system_dependencies(
            need_java=need_java,
            need_certificates=need_certificates,
            offline=args.offline,
            java_version=minimum_java,
        )
        java_home = find_java_home(args.java_home, minimum_java, exact=requirement.exact if args.java_home is None else None)
    elif need_java:
        if requirement.exact is not None:
            fail(
                f"A JDK with Java {requirement.exact} is required for this project's declared toolchain and system dependency installation is disabled. "
                "Use --java-home or install the matching JDK."
            )
        fail(f"A JDK with Java {minimum_java}+ is required and system dependency installation is disabled. Use --java-home or install a JDK.")
    assert java_home is not None
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = os.pathsep.join([str(java_home / "bin"), env.get("PATH", "")])
    if args.gradle_user_home:
        env["GRADLE_USER_HOME"] = str(args.gradle_user_home.expanduser())
    heading("Java")
    info(f"JAVA_HOME={java_home}")
    if requirement.exact is not None:
        info(f"Toolchain requirement: Java {requirement.exact}")
    elif minimum_java:
        info(f"Minimum Java: {minimum_java}+")
    run([java_home / "bin" / "java", "-version"], env=env, dry_run=args.dry_run)

    sdk_root: Path | None = None
    sdk_source: str | None = None
    if project.android:
        sdk_root, sdk_source = discover_sdk_root_with_source(args.sdk_root, project.root)
        if sdk_root is None and not args.skip_sdk_install:
            if not provisioning_allowed:
                fail_bootstrap_blocked(
                    "Android SDK",
                    downloads=args.toolchain_downloads,
                    network=args.bootstrap_network,
                    checked=[
                        str(args.sdk_root) if args.sdk_root else "--sdk-root not set",
                        "local.properties sdk.dir",
                        "$ANDROID_HOME",
                        "$ANDROID_SDK_ROOT",
                        str(Path.home() / "Android" / "Sdk"),
                        str(Path.home() / "android-sdk"),
                        "/opt/android-sdk",
                        "/usr/lib/android-sdk",
                    ],
                )
            sdk_root = managed_sdk_root_for_host(cache_root)
            sdk_source = "managed cache"
        if sdk_root:
            sdk_root.mkdir(parents=True, exist_ok=True)
            configure_android_sdk_env(env, sdk_root)
            if sdk_source:
                info(f"SDK path source: {sdk_source}")
            if args.auto_sdk and not args.skip_sdk_install:
                info("Auto SDK downloads: enabled")
        sdk_root, _, _, _, _ = ensure_android_sdk(
            project,
            sdk_root,
            cache_root,
            force_setup=args.force_setup,
            platform_override=args.android_platform,
            build_tools_override=args.build_tools,
            ndk_override=args.ndk_version,
            cmake_override=args.cmake_version,
            skip_install=args.skip_sdk_install,
            offline=args.offline,
            env=env,
            dry_run=args.dry_run,
            arm_build_tools_provider=args.arm_build_tools_provider,
            ndk_host_provider=args.ndk_host_provider,
            arm64_ndk_url=args.arm64_ndk_url,
            qemu_command=args.qemu_command,
            qemu_ld_prefix=args.qemu_ld_prefix,
            qemu_sysroot_url=args.qemu_sysroot_url,
            qemu_sysroot_sha256=args.qemu_sysroot_sha256,
            qemu_install=args.qemu_install,
            allow_managed_fallback=args.sdk_root is None,
            bootstrap_downloads=args.toolchain_downloads,
            bootstrap_network=args.bootstrap_network,
        )

        if project.native_build and normalized_machine() in {"arm64", "armv7"} and args.ndk_host_provider in {"auto", "arm64", "qemu"}:
            clean_android_cxx_state(project.root, dry_run=args.dry_run)

    aapt2: Path | None = None
    if project.android:
        if args.aapt2:
            aapt2 = stage_aapt2(args.aapt2, cache_root, force=args.force_setup, dry_run=args.dry_run)
        elif args.aapt2_provider == "revanced" or (
            args.aapt2_provider == "auto" and normalized_machine() in {"arm64", "armv7"}
        ):
            if not provisioning_allowed:
                cached_aapt2 = sorted(
                    (cache_root / "aapt2" / "revanced").glob("*/aapt2"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if not cached_aapt2:
                    fail_bootstrap_blocked(
                        "AAPT2",
                        downloads=args.toolchain_downloads,
                        network=args.bootstrap_network,
                        checked=[
                            "--aapt2 not set",
                            str(cache_root / "aapt2" / "revanced"),
                            "Android Gradle Plugin default AAPT2",
                        ],
                    )
            aapt2 = obtain_revanced_aapt2(
                cache_root,
                release_name=args.aapt2_release,
                offline=args.offline or not provisioning_allowed,
                force=args.force_setup,
                dry_run=args.dry_run,
            )
        elif args.aapt2_provider == "none":
            info("Using the AAPT2 selected by Android Gradle Plugin")

    gradle = select_gradle(
        project_root,
        explicit=args.gradle_command,
        version=args.gradle_version,
        cache_root=cache_root,
        offline=args.offline,
        force=args.force_setup,
        bootstrap_downloads=args.toolchain_downloads,
        bootstrap_network=args.bootstrap_network,
        gradle_user_home=args.gradle_user_home,
    )
    heading("Gradle")
    info(f"Launcher: {gradle}")
    if should_cleanup_gradle(args):
        stop_gradle_processes(gradle, project_root, env, dry_run=args.dry_run)
    run([gradle, "--version"], cwd=project_root, env=env, dry_run=args.dry_run)

    tasks = list(args.tasks or default_tasks(project))
    if args.clean and "clean" not in tasks:
        tasks.insert(0, "clean")
    command: list[str | os.PathLike[str]] = [
        gradle,
        *tasks,
        f"--max-workers={args.max_workers}",
        f"-Dorg.gradle.java.installations.paths={java_home}",
        "-Dorg.gradle.java.installations.auto-detect=false",
    ]
    jvmargs = gradle_jvmargs(args)
    if jvmargs:
        command.append(f"-Dorg.gradle.jvmargs={jvmargs}")
    if args.gradle_lock_timeout_ms:
        command.append(f"-Dorg.gradle.cache.internal.lockTimeout={args.gradle_lock_timeout_ms}")
    if args.constrained:
        command.extend([
            "-Dorg.gradle.parallel=false",
            "-Dorg.gradle.vfs.watch=false",
            "-Pkotlin.compiler.execution.strategy=in-process",
        ])
    # Diagnostics are enabled by default so independent tasks continue and the
    # resulting output is stable and suitable for error parsers and CI logs.
    if args.continue_on_failure:
        command.append("--continue")
    if args.stacktrace:
        command.append("--stacktrace")
    if args.offline:
        command.append("--offline")
    if args.no_daemon:
        command.append("--no-daemon")
    else:
        command.append(f"-Dorg.gradle.daemon.idletimeout={args.daemon_idle_timeout_ms}")
    if args.parallel and args.max_workers > 1:
        command.append("--parallel")
    if args.build_cache:
        command.append("--build-cache")
    command.extend(
        gradle_arguments_with_aapt2_override(
            args.gradle_args,
            aapt2,
            plain_console=args.plain_console,
        )
    )

    heading("Build")
    if jvmargs:
        failure_mode = "continue" if args.continue_on_failure else "fail-fast"
        info(
            f"Resource profile: workers={args.max_workers}, {jvmargs}, "
            f"failure-mode={failure_mode}"
        )
    build_started = time.time()
    try:
        run(
            command,
            cwd=project_root,
            env=env,
            dry_run=args.dry_run,
            memory_guard_mb=args.memory_guard_mb,
            low_priority=args.low_priority,
            cpu_limit=args.cpu_limit,
        )

        if args.no_artifact_scan or args.dry_run:
            return 0
        artifacts = discover_artifacts(project_root, build_started)
        heading("Artifacts")
        if not artifacts:
            info("Build succeeded; no new standard artifacts were detected.")
            return 0
        for artifact in artifacts:
            info(str(artifact))
        if args.output_dir:
            copied = copy_artifacts(
                artifacts,
                project_root,
                project.name,
                args.output_dir.expanduser().resolve(),
                args.output_name,
            )
            heading("Copied artifacts")
            for artifact in copied:
                info(str(artifact))
        return 0
    finally:
        if should_cleanup_gradle(args):
            stop_gradle_processes(
                gradle,
                project_root,
                env,
                dry_run=args.dry_run,
            )


def standalone_main(argv: Sequence[str] | None = None) -> int:
    try:
        return main(argv)
    except BuildError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(standalone_main())
