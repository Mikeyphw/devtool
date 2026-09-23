from __future__ import annotations

import json
import os
import platform
import re
import shlex
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .models import CommandSpec, ProjectConfig
from .xdg import state_dir

BOOTSTRAP_RECEIPT_SCHEMA = 3
DEFAULT_JAVA_VERSION = 21
DEFAULT_GRADLE_VERSION = "9.7.1"
DEFAULT_ANDROID_PLATFORM = "36"
DEFAULT_BUILD_TOOLS = "auto"
DEFAULT_NDK_VERSION = "auto"
DEFAULT_CMAKE_VERSION = "auto"
_ARCH_ALIASES = {
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv8l": "arm64",
    "x86_64": "amd64",
    "amd64": "amd64",
}


@dataclass(frozen=True, slots=True)
class BootstrapDestination:
    canonical: str
    kind: str
    name: str = ""
    expected_arch: str = ""


@dataclass(frozen=True, slots=True)
class BootstrapRequest:
    java_version: int = DEFAULT_JAVA_VERSION
    gradle_version: str = DEFAULT_GRADLE_VERSION
    android_platform: str = DEFAULT_ANDROID_PLATFORM
    build_tools: str = DEFAULT_BUILD_TOOLS
    ndk_version: str = DEFAULT_NDK_VERSION
    cmake_version: str = DEFAULT_CMAKE_VERSION
    sdk_provider: str = "auto"
    aapt2_provider: str = "auto"
    arm_build_tools_provider: str = "auto"
    native_provider: str = "auto"
    gradle_provider: str = "auto"
    java_provider: str = "auto"
    sdk_root: str = ""
    gradle_user_home: str = ""
    cache_dir: str = ""


def normalize_machine(machine: str | None = None) -> str:
    raw = (machine or platform.machine()).strip().lower().replace("-", "_")
    return _ARCH_ALIASES.get(raw, raw)


def _running_under_termux() -> bool:
    try:
        from .android.providers.termux import running_under_termux_python

        return bool(running_under_termux_python())
    except Exception:
        prefix = str(os.environ.get("PREFIX") or "")
        return prefix.startswith("/data/data/com.termux/")


def _termux_expected_arch() -> str:
    for key in ("TERMUX_ARCH", "ANDROID_CPU_ABI", "ANDROID_ABI", "DEVTOOL_TERMUX_ARCH"):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            normalized = normalize_machine(raw)
            if normalized in {"arm64", "amd64"}:
                return normalized
    # Native Termux most commonly runs on Android arm64.  Do not let a host
    # Python/PRoot shim reporting x86_64 make the Termux bootstrap receipt
    # claim linux-amd64 architecture.
    host_arch = normalize_machine()
    return host_arch if host_arch not in {"amd64"} else "arm64"


def current_destination() -> BootstrapDestination:
    arch = normalize_machine()
    if _running_under_termux():
        return BootstrapDestination("termux", "termux", expected_arch=_termux_expected_arch())
    if arch == "arm64":
        return BootstrapDestination("linux-arm64", "linux", expected_arch="arm64")
    if arch == "amd64":
        return BootstrapDestination("linux-amd64", "linux", expected_arch="amd64")
    return BootstrapDestination(f"linux-{arch}", "linux", expected_arch=arch)


def parse_destination(value: str | None) -> BootstrapDestination:
    raw = str(value or "current").strip().lower()
    if not raw or raw == "current":
        return current_destination()
    aliases = {
        "termux-arm64": "termux",
        "linux-x86_64": "linux-amd64",
        "linux-aarch64": "linux-arm64",
    }
    raw = aliases.get(raw, raw)
    if raw == "termux":
        return BootstrapDestination("termux", "termux", expected_arch="arm64")
    if raw in {"linux-arm64", "linux-amd64"}:
        return BootstrapDestination(raw, "linux", expected_arch=raw.removeprefix("linux-"))

    match = re.fullmatch(r"chroot(?:(?:-|:)(arm64|amd64))?:(.+)", raw)
    if match:
        expected_arch = str(match.group(1) or "")
        name = str(match.group(2) or "").strip()
        if not name or not re.fullmatch(r"[a-z0-9._-]+", name):
            raise ValueError(f"Invalid chroot destination: {value!r}")
        return BootstrapDestination(f"chroot:{name}", "chroot", name=name, expected_arch=expected_arch)
    raise ValueError(
        "Unsupported bootstrap destination. Use current, termux, linux-arm64, "
        "linux-amd64, chroot:<name>, chroot-arm64:<name>, or chroot-amd64:<name>."
    )


def receipt_root() -> Path:
    return state_dir() / "bootstrap"


def receipt_path(destination: BootstrapDestination | str) -> Path:
    parsed = parse_destination(destination) if isinstance(destination, str) else destination
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", parsed.canonical).strip("-") or "current"
    return receipt_root() / f"{safe}.json"


def load_receipt(destination: BootstrapDestination | str) -> dict[str, Any] | None:
    path = receipt_path(destination)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        schema = int(payload.get("schema") or 0)
    except (TypeError, ValueError):
        return None
    if schema != BOOTSTRAP_RECEIPT_SCHEMA:
        return None
    return payload


def write_receipt(destination: BootstrapDestination, request: BootstrapRequest, report: Mapping[str, Any]) -> Path:
    path = receipt_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": BOOTSTRAP_RECEIPT_SCHEMA,
        "destination": destination.canonical,
        "kind": destination.kind,
        "name": destination.name,
        "expected_arch": destination.expected_arch,
        "actual_arch": str(report.get("architecture") or ""),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "request": asdict(request),
        "resolved": dict(report),
        "status": "ready",
    }
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def remove_receipt(destination: BootstrapDestination | str) -> bool:
    path = receipt_path(destination)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def request_matches_receipt(receipt: Mapping[str, Any] | None, request: BootstrapRequest) -> bool:
    if not receipt:
        return False
    stored = receipt.get("request")
    return isinstance(stored, dict) and stored == asdict(request) and receipt.get("status") == "ready"


def _synthetic_project_text(request: BootstrapRequest) -> tuple[str, str]:
    """Create a probe project without turning ``auto`` into pinned SDK versions.

    The builder receives the requested overrides on its command line, so this
    project exists only to provide Android/AGP/native-build context.  Older
    bootstrap probes wrote concrete Build Tools, NDK, and CMake versions here
    even when the user selected ``auto``.  Project inspection then treated
    those synthetic values as real project requirements and downloaded them.
    """

    platform_value = request.android_platform
    try:
        compile_sdk = int(platform_value.removeprefix("android-")) if platform_value not in {"auto", "none"} else 36
    except ValueError:
        compile_sdk = 36

    android_lines = [f"    compileSdk {compile_sdk}"]
    if request.build_tools not in {"auto", "none"}:
        android_lines.append(f'    buildToolsVersion "{request.build_tools}"')
    if request.ndk_version not in {"auto", "none"}:
        android_lines.append(f'    ndkVersion "{request.ndk_version}"')

    # Keep a native-build marker so standalone bootstrap still provisions an
    # NDK when none is installed.  Omit the CMake version for ``auto`` so host
    # or already-installed CMake can satisfy the request without forcing an SDK
    # CMake package download.
    cmake_lines = ["    externalNativeBuild {", "        cmake {"]
    if request.cmake_version not in {"auto", "none"}:
        cmake_lines.append(f'            version "{request.cmake_version}"')
    cmake_lines.extend(["        }", "    }"])
    android_lines.extend(cmake_lines)

    settings = "rootProject.name = 'devtool-bootstrap-probe'\n"
    body = "\n".join(android_lines)
    build = (
        "plugins {\n"
        "    id 'com.android.application' version '9.0.0' apply false\n"
        "}\n\n"
        "android {\n"
        f"{body}\n"
        "}\n"
    )
    return settings, build


def prepare_bootstrap_workspace(request: BootstrapRequest) -> tuple[Path, Path, Path]:
    from .android_integration import stage_android_builder

    builder = stage_android_builder()
    workspace = builder.parent / "bootstrap-project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings, build = _synthetic_project_text(request)
    (workspace / "settings.gradle").write_text(settings, encoding="utf-8")
    (workspace / "build.gradle").write_text(build, encoding="utf-8")
    report = builder.parent / "bootstrap-report.json"
    return builder, workspace, report


def build_builder_argv(
    builder: Path,
    workspace: Path,
    report: Path,
    request: BootstrapRequest,
    *,
    force: bool = False,
    dry_run: bool = False,
    downloads: str = "auto",
) -> list[str]:
    argv = [
        "python3",
        str(builder),
        str(workspace),
        "--no-chroot",
        "--bootstrap-only",
        "--bootstrap-report",
        str(report),
        "--toolchain-downloads",
        downloads,
        "--bootstrap-network",
        "allow",
        "--system-deps",
        "auto",
        "--min-java",
        str(request.java_version),
        "--gradle-version",
        request.gradle_version,
        "--android-platform",
        request.android_platform,
        "--build-tools",
        request.build_tools,
        "--ndk-version",
        request.ndk_version,
        "--cmake-version",
        request.cmake_version,
        "--sdk-provider",
        request.sdk_provider,
        "--aapt2-provider",
        request.aapt2_provider,
        "--arm-build-tools-provider",
        request.arm_build_tools_provider,
        "--native-provider",
        request.native_provider,
        "--gradle-provider",
        request.gradle_provider,
        "--java-provider",
        request.java_provider,
        "--no-artifact-scan",
    ]
    if request.sdk_root:
        argv.extend(["--sdk-root", request.sdk_root])
    if request.gradle_user_home:
        argv.extend(["--gradle-user-home", request.gradle_user_home])
    if request.cache_dir:
        argv.extend(["--cache-dir", request.cache_dir])
    if force:
        argv.append("--force-setup")
    if dry_run:
        argv.append("--dry-run")
    return argv


def _validate_local_destination(destination: BootstrapDestination) -> None:
    actual = current_destination()
    actual_arch = normalize_machine()
    if destination.kind == "termux" and not _running_under_termux():
        raise RuntimeError("Destination termux must be provisioned from native Termux.")
    if destination.kind == "linux" and _running_under_termux():
        raise RuntimeError(
            f"Destination {destination.canonical} is a Linux destination, but this process is native Termux. "
            "Use chroot:<name> for a Termux-hosted chroot."
        )
    if destination.expected_arch and actual_arch != destination.expected_arch:
        raise RuntimeError(
            f"Destination {destination.canonical} requires {destination.expected_arch}, but the current host is {actual_arch}."
        )
    if destination.kind not in {"termux", "linux"}:
        raise RuntimeError(f"Destination {destination.canonical} is not a local destination ({actual.canonical}).")


def _run_command(argv: list[str], *, env: Mapping[str, str] | None = None) -> int:
    completed = subprocess.run(argv, env=dict(env) if env is not None else None, check=False)
    return int(completed.returncode)


REQUIRED_ANDROID_BUILD_TOOLS = ("aapt2", "aidl", "zipalign", "split-select")


def _bootstrap_build_tool_paths(report: Mapping[str, Any]) -> dict[str, str] | None:
    build_tool_paths = report.get("build_tool_paths")
    if not isinstance(build_tool_paths, dict):
        return None
    return {str(name): str(path).strip() for name, path in build_tool_paths.items()}


def _missing_bootstrap_build_tools(report: Mapping[str, Any]) -> list[str]:
    build_tool_paths = _bootstrap_build_tool_paths(report)
    if build_tool_paths is None:
        return list(REQUIRED_ANDROID_BUILD_TOOLS)
    return [name for name in REQUIRED_ANDROID_BUILD_TOOLS if not build_tool_paths.get(name)]


def _validate_chroot_android_build_tools(destination: BootstrapDestination, report: Mapping[str, Any]) -> None:
    build_tool_paths = _bootstrap_build_tool_paths(report)
    if build_tool_paths is None:
        raise RuntimeError("Chroot bootstrap report did not include verified Android build-tool paths")
    missing = _missing_bootstrap_build_tools(report)
    if missing:
        raise RuntimeError("Chroot bootstrap completed without required Android build tools: " + ", ".join(missing))
    if not _running_under_termux():
        raise RuntimeError("Chroot Android build-tool verification requires native Termux")
    from .execution import build_chroot_exec_argv

    checks = [f"test -x {shlex.quote(build_tool_paths[name])}" for name in REQUIRED_ANDROID_BUILD_TOOLS]
    completed = subprocess.run(
        build_chroot_exec_argv(" && ".join(checks), destination.name),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise RuntimeError("Chroot bootstrap completed but Android build tools are not executable in the chroot: " + detail)


def _validate_provisioning_report(destination: BootstrapDestination, report: Mapping[str, Any]) -> None:
    if bool(report.get("dry_run")):
        return
    if destination.kind == "chroot":
        _validate_chroot_android_build_tools(destination, report)
        return
    if destination.kind != "termux":
        return
    build_tool_paths = _bootstrap_build_tool_paths(report)
    if build_tool_paths is None:
        raise RuntimeError("Termux bootstrap report did not include verified Android build-tool paths")
    missing = _missing_bootstrap_build_tools(report)
    if missing:
        raise RuntimeError("Termux bootstrap completed without required Android build tools: " + ", ".join(missing))
    from .android.providers.termux import native_android_build_tool_compatible

    broken = [
        name
        for name in REQUIRED_ANDROID_BUILD_TOOLS
        if not native_android_build_tool_compatible(Path(build_tool_paths[name]), name, "arm64")
    ]
    if broken:
        raise RuntimeError(
            "Termux bootstrap completed but Android build tools failed Bionic runtime verification: "
            + ", ".join(broken)
        )


def _run_local_install(destination: BootstrapDestination, request: BootstrapRequest, *, force: bool, dry_run: bool, downloads: str) -> dict[str, Any]:
    _validate_local_destination(destination)
    builder, workspace, report = prepare_bootstrap_workspace(request)
    report.unlink(missing_ok=True)
    argv = build_builder_argv(builder, workspace, report, request, force=force, dry_run=dry_run, downloads=downloads)
    rc = _run_command(argv)
    if rc != 0:
        raise RuntimeError(f"Bootstrap provisioning failed with exit {rc}: {shlex.join(argv)}")
    if dry_run:
        return {"architecture": normalize_machine(), "dry_run": True, "command": argv}
    if not report.is_file():
        raise RuntimeError(f"Bootstrap provisioning completed without a report: {report}")
    payload = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Bootstrap report is malformed: {report}")
    _validate_provisioning_report(destination, payload)
    return payload


def _chroot_wrapped_argv(destination: BootstrapDestination, request: BootstrapRequest, *, force: bool, dry_run: bool, downloads: str) -> tuple[list[str], Path]:
    if not _running_under_termux():
        raise RuntimeError("Chroot destinations must be provisioned from native Termux.")
    from .termux_chroot_runner import default_path_map, repair_termux_home_mapping, wrap_specs_for_termux_host_chroot

    builder, workspace, report = prepare_bootstrap_workspace(request)
    report.unlink(missing_ok=True)
    mappings = repair_termux_home_mapping(
        default_path_map(os.environ),
        host_path=str(workspace),
        chroot_name=destination.name,
        env=os.environ,
    )
    spec = CommandSpec(
        name="bootstrap-install",
        argv=build_builder_argv(builder, workspace, report, request, force=force, dry_run=dry_run, downloads=downloads),
        cwd=workspace,
        env={"GRADLE_BUILD_CHROOT_ACTIVE": "1", "DEVTOOL_INSIDE_TERMUX_CHROOT": "1"},
        metadata={},
    )
    wrapped = wrap_specs_for_termux_host_chroot(
        [spec],
        destination.name,
        repo_root=workspace,
        env=os.environ,
        path_map=mappings,
    )
    if len(wrapped) != 1:
        raise RuntimeError("Failed to construct chroot bootstrap command")
    return list(wrapped[0].argv), report


def _run_chroot_install(destination: BootstrapDestination, request: BootstrapRequest, *, force: bool, dry_run: bool, downloads: str) -> dict[str, Any]:
    argv, report = _chroot_wrapped_argv(destination, request, force=force, dry_run=dry_run, downloads=downloads)
    rc = _run_command(argv)
    if rc != 0:
        raise RuntimeError(f"Bootstrap provisioning failed with exit {rc}: {shlex.join(argv)}")
    if dry_run:
        return {"architecture": destination.expected_arch or "", "dry_run": True, "command": argv}
    if not report.is_file():
        raise RuntimeError(f"Chroot bootstrap provisioning completed without a report: {report}")
    payload = json.loads(report.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Bootstrap report is malformed: {report}")
    _validate_provisioning_report(destination, payload)
    return payload


def _resolved_paths(receipt: Mapping[str, Any]) -> list[str]:
    resolved = receipt.get("resolved")
    if not isinstance(resolved, dict):
        return []
    result: list[str] = []
    for key in ("java_home", "gradle", "sdk_root", "aapt2"):
        value = str(resolved.get(key) or "").strip()
        if value:
            result.append(value)
    build_tool_paths = resolved.get("build_tool_paths")
    if isinstance(build_tool_paths, dict):
        for value in build_tool_paths.values():
            path = str(value or "").strip()
            if path:
                result.append(path)
    return result


def _probe_local_receipt(destination: BootstrapDestination, receipt: Mapping[str, Any]) -> tuple[bool, str]:
    try:
        _validate_local_destination(destination)
    except RuntimeError as exc:
        return False, str(exc)
    actual_arch = normalize_machine()
    expected = str(receipt.get("actual_arch") or destination.expected_arch or "")
    if expected and actual_arch != expected:
        return False, f"architecture changed: receipt={expected}, current={actual_arch}"
    missing = [value for value in _resolved_paths(receipt) if not Path(value).expanduser().exists()]
    if missing:
        return False, "missing provisioned paths: " + ", ".join(missing)
    if destination.kind == "termux":
        resolved = receipt.get("resolved")
        build_tool_paths = resolved.get("build_tool_paths") if isinstance(resolved, dict) else None
        required_tools = {"aapt2", "aidl", "zipalign", "split-select"}
        if not isinstance(build_tool_paths, dict):
            return False, "Termux receipt predates native Android build-tool verification; reinstall or repair bootstrap"
        missing_tools = sorted(required_tools - {str(name) for name in build_tool_paths})
        if missing_tools:
            return False, "Termux receipt is missing verified Android build tools: " + ", ".join(missing_tools)
        try:
            from .android.providers.termux import native_android_build_tool_compatible

            broken = [
                name
                for name in sorted(required_tools)
                if not native_android_build_tool_compatible(Path(str(build_tool_paths.get(name) or "")), name, "arm64")
            ]
        except Exception as exc:
            return False, f"could not verify Termux Android build tools: {exc}"
        if broken:
            return False, "Termux Android build tools failed runtime verification: " + ", ".join(broken)
    return True, "receipt and provisioned paths are present"


def _probe_chroot_receipt(destination: BootstrapDestination, receipt: Mapping[str, Any]) -> tuple[bool, str]:
    if not _running_under_termux():
        return False, "chroot status requires native Termux"
    from .execution import build_chroot_exec_argv

    resolved = receipt.get("resolved")
    if not isinstance(resolved, dict):
        return False, "chroot receipt has no resolved provisioning report"
    missing_tools = _missing_bootstrap_build_tools(resolved)
    if missing_tools:
        return False, "chroot receipt is missing verified Android build tools: " + ", ".join(missing_tools)
    build_tool_paths = _bootstrap_build_tool_paths(resolved) or {}

    expected = str(receipt.get("actual_arch") or destination.expected_arch or "")
    tests = []
    build_tool_values = {str(path) for path in build_tool_paths.values() if str(path).strip()}
    for value in _resolved_paths(receipt):
        quoted = shlex.quote(value)
        if value in build_tool_values:
            tests.append(f"test -x {quoted}")
        else:
            tests.append(f"test -e {quoted}")
    arch_test = ""
    if expected:
        accepted = "aarch64|arm64" if expected == "arm64" else "x86_64|amd64" if expected == "amd64" else re.escape(expected)
        arch_test = f"uname -m | grep -Eq {shlex.quote('^(' + accepted + ')$')}"
    command = " && ".join([part for part in [arch_test, *tests] if part]) or "true"
    completed = subprocess.run(build_chroot_exec_argv(command, destination.name), check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        return False, f"chroot receipt probe failed: {detail}"
    return True, "receipt and provisioned chroot paths are present"


def probe_receipt(destination: BootstrapDestination, receipt: Mapping[str, Any] | None = None) -> tuple[bool, str]:
    receipt = receipt or load_receipt(destination)
    if not receipt:
        return False, "no bootstrap receipt"
    if str(receipt.get("destination") or "") != destination.canonical:
        return False, "receipt destination mismatch"
    if destination.kind == "chroot":
        return _probe_chroot_receipt(destination, receipt)
    return _probe_local_receipt(destination, receipt)


def install_bootstrap(
    destination: BootstrapDestination,
    request: BootstrapRequest,
    *,
    repair: bool = False,
    upgrade: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    existing = load_receipt(destination)
    if not repair and not upgrade and request_matches_receipt(existing, request):
        ready, detail = probe_receipt(destination, existing)
        if ready:
            return {
                "ok": True,
                "changed": False,
                "destination": destination.canonical,
                "status": "ready",
                "detail": detail,
                "receipt": str(receipt_path(destination)),
                "resolved": dict(existing.get("resolved") or {}),
            }
    downloads = "force" if upgrade else "auto"
    if destination.kind == "chroot":
        report = _run_chroot_install(destination, request, force=bool(repair or upgrade), dry_run=dry_run, downloads=downloads)
    else:
        report = _run_local_install(destination, request, force=bool(repair or upgrade), dry_run=dry_run, downloads=downloads)
    actual_arch = str(report.get("architecture") or "")
    if destination.expected_arch and actual_arch and actual_arch != destination.expected_arch:
        raise RuntimeError(
            f"Destination {destination.canonical} expected {destination.expected_arch}, but provisioning reported {actual_arch}."
        )
    path = ""
    if not dry_run:
        path = str(write_receipt(destination, request, report))
    return {
        "ok": True,
        "changed": not dry_run,
        "destination": destination.canonical,
        "status": "dry-run" if dry_run else "ready",
        "detail": "provisioning completed" if not dry_run else "provisioning dry-run completed",
        "receipt": path,
        "resolved": report,
    }


def bootstrap_status(destination: BootstrapDestination) -> dict[str, Any]:
    receipt = load_receipt(destination)
    ready, detail = probe_receipt(destination, receipt)
    return {
        "ok": ready,
        "destination": destination.canonical,
        "status": "ready" if ready else "missing-or-stale",
        "detail": detail,
        "receipt": str(receipt_path(destination)),
        "request": dict(receipt.get("request") or {}) if receipt else {},
        "resolved": dict(receipt.get("resolved") or {}) if receipt else {},
    }


def _requested_destination_for_config(config: ProjectConfig, args: Any) -> BootstrapDestination:
    explicit_chroot = str(getattr(args, "chroot", "") or "").strip()
    requested_execution = str(getattr(args, "execution_environment", "") or config.execution_environment or "auto").strip().lower().replace("_", "-")
    if explicit_chroot:
        return BootstrapDestination(f"chroot:{explicit_chroot}", "chroot", name=explicit_chroot)
    if requested_execution == "chroot":
        name = str(config.chroot_name or "androidos")
        return BootstrapDestination(f"chroot:{name}", "chroot", name=name)
    if _running_under_termux() and requested_execution not in {"native-termux", "desktop-linux"}:
        try:
            from .android_integration import should_prefer_android_chroot

            if should_prefer_android_chroot(config):
                name = str(config.chroot_name or "androidos")
                return BootstrapDestination(f"chroot:{name}", "chroot", name=name)
        except Exception:
            pass
    return current_destination()


def apply_registered_bootstrap_policy(config: ProjectConfig, args: Any) -> bool:
    """Reuse a registered destination without blocking project-specific repair.

    A bootstrap receipt proves that a baseline toolchain was provisioned for the
    destination; it does *not* prove that every future project's explicit or AGP
    default SDK component is present. Explicit CLI policy still wins. A healthy
    receipt therefore selects ``missing`` rather than ``never`` so existing tools
    are reused while genuinely missing project requirements can still be installed.
    """

    if str(getattr(args, "bootstrap_downloads", "") or "").strip():
        return False
    if bool(getattr(args, "no_bootstrap_downloads", False)) or bool(getattr(args, "offline", False)):
        return False
    bootstrap = config.runner_options.get("bootstrap", {})
    if not isinstance(bootstrap, dict):
        bootstrap = {}
        config.runner_options["bootstrap"] = bootstrap
    configured = str(bootstrap.get("downloads") or "").strip().lower()
    if configured not in {"", "auto", "missing"}:
        return False
    destination = _requested_destination_for_config(config, args)
    receipt = load_receipt(destination)
    if not receipt or receipt.get("status") != "ready":
        return False
    ready, _detail = probe_receipt(destination, receipt)
    if not ready:
        return False
    bootstrap["downloads"] = "missing"
    bootstrap["receipt"] = str(receipt_path(destination))
    bootstrap["destination"] = destination.canonical
    return True


def request_from_args(args: Any) -> BootstrapRequest:
    return BootstrapRequest(
        java_version=int(getattr(args, "bootstrap_java_version", DEFAULT_JAVA_VERSION) or DEFAULT_JAVA_VERSION),
        gradle_version=str(getattr(args, "bootstrap_gradle_version", DEFAULT_GRADLE_VERSION) or DEFAULT_GRADLE_VERSION),
        android_platform=str(getattr(args, "bootstrap_android_platform", DEFAULT_ANDROID_PLATFORM) or DEFAULT_ANDROID_PLATFORM),
        build_tools=str(getattr(args, "bootstrap_build_tools", DEFAULT_BUILD_TOOLS) or DEFAULT_BUILD_TOOLS),
        ndk_version=str(getattr(args, "bootstrap_ndk_version", DEFAULT_NDK_VERSION) or DEFAULT_NDK_VERSION),
        cmake_version=str(getattr(args, "bootstrap_cmake_version", DEFAULT_CMAKE_VERSION) or DEFAULT_CMAKE_VERSION),
        sdk_provider=str(getattr(args, "bootstrap_sdk_provider", "auto") or "auto"),
        aapt2_provider=str(getattr(args, "bootstrap_aapt2_provider", "auto") or "auto"),
        arm_build_tools_provider=str(getattr(args, "bootstrap_arm_build_tools_provider", "auto") or "auto"),
        native_provider=str(getattr(args, "bootstrap_native_provider", "auto") or "auto"),
        gradle_provider=str(getattr(args, "bootstrap_gradle_provider", "auto") or "auto"),
        java_provider=str(getattr(args, "bootstrap_java_provider", "auto") or "auto"),
        sdk_root=str(getattr(args, "bootstrap_sdk_root", "") or ""),
        gradle_user_home=str(getattr(args, "bootstrap_gradle_user_home", "") or ""),
        cache_dir=str(getattr(args, "bootstrap_cache_dir", "") or ""),
    )


def render_bootstrap_result(payload: Mapping[str, Any]) -> str:
    lines = [
        f"Destination: {payload.get('destination', '')}",
        f"Status: {payload.get('status', '')}",
        f"Detail: {payload.get('detail', '')}",
    ]
    receipt = str(payload.get("receipt") or "")
    if receipt:
        lines.append(f"Receipt: {receipt}")
    resolved = payload.get("resolved")
    if isinstance(resolved, dict):
        labels = (
            ("architecture", "Architecture"),
            ("java_home", "Java"),
            ("gradle", "Gradle"),
            ("sdk_root", "Android SDK"),
            ("platform", "Platform"),
            ("build_tools", "Build Tools"),
            ("ndk", "NDK"),
            ("cmake", "CMake"),
            ("aapt2", "AAPT2"),
        )
        for key, label in labels:
            value = str(resolved.get(key) or "").strip()
            if value:
                lines.append(f"{label}: {value}")
    return "\n".join(lines)
