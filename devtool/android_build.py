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
import copy
import hashlib
import importlib
import importlib.util
import json
import os
import platform
import posixpath
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
ARM_BUILD_TOOLS_PUBLISHED_RELEASES = {"35.0.1", "36.0.0", "36.1.0", "37.0.0"}
ARM_BUILD_TOOLS_NATIVE_NAMES = ("aapt2", "aidl", "zipalign", "split-select")
NDK_HOST_PROVIDERS = {"auto", "google", "arm64", "qemu", "none"}
QEMU_INSTALL_POLICIES = {"auto", "never"}
QEMU_X86_64_NAMES = ("qemu-x86_64", "qemu-x86_64-static", "qemu-user-x86_64")
DEFAULT_QEMU_X86_64_SYSROOT_URL = "https://cdimages.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.4-base-amd64.tar.gz"
DEFAULT_QEMU_X86_64_SYSROOT_SHA256 = "c1e67ef7b17a6300e136118bd1dc04725009cb376c1aad10abcf8cd453628d58"
ANDROID_CMDLINE_TOOLS_URL = "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip"
AAPT2_OVERRIDE_PROPERTY = "android.aapt2FromMavenOverride"
UNSUPPORTED_OPTION_WARNINGS_PROPERTY = "android.suppressUnsupportedOptionWarnings"
AGP_WARNING_SUPPRESSIONS_PROPERTY = "android.sync.suppressAgpWarnings"
AGP_UNSUPPORTED_PROJECT_OPTION_WARNING = "UNSUPPORTED_PROJECT_OPTION_USE"
BOOTSTRAP_DOWNLOAD_POLICIES = {"never", "missing", "auto", "force"}
BOOTSTRAP_NETWORK_POLICIES = {"deny", "allow"}
JAVA_PROVIDERS = {"auto", "termux", "system"}
GRADLE_PROVIDERS = {"auto", "wrapper", "termux", "system", "download"}
NATIVE_PROVIDERS = {"auto", "termux-sysroot", "ndk", "system"}
NATIVE_CACHE_PROVIDERS = {"auto", "ccache", "off"}
ANDROID_BUILD_TOOLS_PROVIDERS = {"auto", "termux", "sdk", "commit451", "none"}
ANDROID_SDK_PROVIDERS = {"auto", "termuxvoid", "homuhomu", "managed"}
TERMUXVOID_REPOSITORY_URL = "https://termuxvoid.github.io/repo"
TERMUXVOID_REPOSITORY_LINE = f"deb [arch=all] {TERMUXVOID_REPOSITORY_URL} termuxvoid main"
TERMUXVOID_KEY_URL = "https://raw.githubusercontent.com/termuxvoid/repo/main/assets/termuxvoid.gpg"
TERMUXVOID_SDK_REPOSITORY = "termuxvoid/android-sdk-termux"
HOMUHOMU_SDK_REPOSITORY = "HomuHomu833/android-sdk-custom"

# sdkmanager needs both download and unpack headroom. These are deliberately
# conservative working-space estimates, not claims about exact package sizes.
# The preflight only protects writable SDK provisioning paths and can reclaim
# Devtool-owned caches before deciding that the filesystem is too full.
ANDROID_SDK_INSTALL_RESERVE_BYTES = 512 * 1024 * 1024
ANDROID_SDK_PACKAGE_WORKING_SPACE_BYTES = {
    "platforms": 384 * 1024 * 1024,
    "build-tools": 512 * 1024 * 1024,
    "platform-tools": 256 * 1024 * 1024,
    "cmdline-tools": 384 * 1024 * 1024,
    "cmake": 1536 * 1024 * 1024,
    "ndk": 4096 * 1024 * 1024,
    "system-images": 4096 * 1024 * 1024,
}
ANDROID_SDK_UNKNOWN_PACKAGE_WORKING_SPACE_BYTES = 512 * 1024 * 1024
SPACE_CLEANUP_POLICIES = {"off", "safe"}
DEFAULT_ANDROID_SPACE_MIN_FREE_MB = 4096
DEFAULT_ANDROID_SPACE_TARGET_FREE_MB = 6144
DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION = 2


@dataclass(frozen=True)
class SpaceReclaimCandidate:
    path: Path
    category: str
    reason: str
    size_bytes: int
    priority: int = 100


@dataclass(frozen=True)
class SpaceCleanupReport:
    free_before: int | None
    free_after: int | None
    reclaimed_bytes: int
    removed: tuple[SpaceReclaimCandidate, ...]
    candidates: tuple[SpaceReclaimCandidate, ...]
    dry_run: bool = False


class BuildError(RuntimeError):
    """User-actionable build failure."""


_TERMUX_TOOLCHAINS_RUNTIME_MODULE = None


def _termux_toolchains_runtime_module():
    """Load toolchain helpers in package or staged-standalone execution.

    ``android_build.py`` is copied to a chroot-visible ``build-apk`` path and
    executed directly. Direct script execution has no package context, so
    explicit relative imports fail. The runtime stager places the canonical
    ``termux_toolchains.py`` beside ``build-apk``; load that companion directly
    when ``__package__`` is empty while retaining normal package imports when
    Devtool itself imports this module.
    """

    global _TERMUX_TOOLCHAINS_RUNTIME_MODULE
    if _TERMUX_TOOLCHAINS_RUNTIME_MODULE is not None:
        return _TERMUX_TOOLCHAINS_RUNTIME_MODULE

    if __package__:
        module = importlib.import_module(f"{__package__}.termux_toolchains")
    else:
        companion = Path(__file__).resolve().with_name("termux_toolchains.py")
        if not companion.is_file():
            fail(
                "Android builder runtime is incomplete: missing toolchain companion "
                f"{companion}. Re-run Devtool so the Android runtime bundle is refreshed."
            )

        module_name = "_devtool_android_runtime_termux_toolchains"
        module = sys.modules.get(module_name)

        if module is None:
            spec = importlib.util.spec_from_file_location(module_name, companion)

            if spec is None or spec.loader is None:
                fail(
                    f"Could not load Android builder toolchain companion: {companion}"
                )

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module

            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise

    _TERMUX_TOOLCHAINS_RUNTIME_MODULE = module
    return module


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


def normalize_android_sdk_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    aliases = {
        "termux-void": "termuxvoid",
        "void": "termuxvoid",
        "homu": "homuhomu",
        "android-sdk-custom": "homuhomu",
        "google": "managed",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in ANDROID_SDK_PROVIDERS:
        valid = ", ".join(sorted(ANDROID_SDK_PROVIDERS))
        fail(f"Unsupported Android SDK provider: {value}. Expected one of: {valid}")
    return normalized


def _termux_prefix_path() -> Path:
    raw = str(os.environ.get("PREFIX") or "/data/data/com.termux/files/usr").strip()
    return Path(raw).expanduser().resolve(strict=False)


def _termux_package_installed(name: str) -> bool:
    if not running_under_termux_python():
        return False
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return False
    result = run(
        [dpkg_query, "-W", "-f=${Status}", name],
        check=False,
        capture=True,
        quiet=True,
    )
    return result.returncode == 0 and "install ok installed" in (result.stdout or "")


def _ensure_termux_packages(packages: Sequence[str], *, dry_run: bool) -> None:
    missing = [name for name in packages if not _termux_package_installed(name)]
    if not missing:
        return
    package_manager = shutil.which("pkg") or shutil.which("apt")
    if not package_manager:
        fail("Native Termux package bootstrap requires pkg or apt in PATH")
    heading("Installing Termux SDK provider prerequisites")
    info(", ".join(missing))
    run([package_manager, "install", "-y", *missing], dry_run=dry_run)


def ensure_termuxvoid_repository(*, cache_root: Path, dry_run: bool) -> None:
    """Idempotently configure the minimal repositories required by TermuxVoid.

    We intentionally do not reproduce TermuxVoid's optional X11-repo setup: the
    Android SDK package does not require it. The official glibc repo is enabled
    because TermuxVoid's own installer treats it as a baseline dependency.
    """

    if not running_under_termux_python():
        fail("The TermuxVoid Android SDK provider requires native Termux execution")
    prefix = _termux_prefix_path()
    sources_dir = prefix / "etc" / "apt" / "sources.list.d"
    trusted_dir = prefix / "etc" / "apt" / "trusted.gpg.d"
    glibc_list = sources_dir / "glibc.list"
    termuxvoid_list = sources_dir / "termuxvoid.list"
    termuxvoid_key = trusted_dir / "termuxvoid.gpg"

    changed = False
    if not glibc_list.is_file():
        _ensure_termux_packages(["glibc-repo"], dry_run=dry_run)
        changed = True

    if dry_run:
        if not termuxvoid_list.is_file():
            info(f"Would configure TermuxVoid APT source: {termuxvoid_list}")
        if not termuxvoid_key.is_file():
            info(f"Would install TermuxVoid signing key: {termuxvoid_key}")
        return

    sources_dir.mkdir(parents=True, exist_ok=True)
    trusted_dir.mkdir(parents=True, exist_ok=True)
    desired_line = TERMUXVOID_REPOSITORY_LINE + "\n"
    current_line = ""
    try:
        current_line = termuxvoid_list.read_text(encoding="utf-8")
    except OSError:
        pass
    if current_line != desired_line:
        temporary = termuxvoid_list.with_suffix(".list.tmp")
        temporary.write_text(desired_line, encoding="utf-8")
        temporary.replace(termuxvoid_list)
        changed = True

    if not termuxvoid_key.is_file() or termuxvoid_key.stat().st_size == 0:
        key_cache = cache_root / "android-sdk-providers" / "termuxvoid" / "termuxvoid.gpg"
        download(TERMUXVOID_KEY_URL, key_cache, offline=False, force=False)
        temporary = termuxvoid_key.with_suffix(".gpg.tmp")
        shutil.copy2(key_cache, temporary)
        temporary.replace(termuxvoid_key)
        changed = True

    if changed:
        apt = shutil.which("apt") or shutil.which("pkg")
        if not apt:
            fail("TermuxVoid repository setup requires apt or pkg in PATH")
        heading("Refreshing Termux package repositories")
        run([apt, "update", "-y"] if Path(apt).name == "apt" else [apt, "update", "-y"])


def _provider_cache_root(cache_root: Path, provider: str) -> Path:
    return cache_root / "android-sdk-providers" / provider


def _provider_sdk_root(cache_root: Path, provider: str) -> Path:
    return _provider_cache_root(cache_root, provider) / "sdk"


def _provider_archive_working_space_bytes(archive: Path) -> int:
    try:
        archive_bytes = int(archive.stat().st_size)
    except OSError:
        archive_bytes = 0
    # Extraction can transiently coexist with the compressed archive and SDK
    # tree. Three archive sizes plus 512 MiB is conservative enough to avoid
    # the common ENOSPC failure without pretending to know the exact unpacked
    # size of third-party bundles.
    return max(768 * 1024 * 1024, archive_bytes * 3 + 512 * 1024 * 1024)


def _ensure_provider_extraction_space(provider: str, archive: Path, provider_root: Path) -> None:
    free = android_sdk_free_bytes(provider_root)
    if free is None:
        return
    required = _provider_archive_working_space_bytes(archive)
    if free < required:
        fail(
            f"Insufficient free space for {provider} Android SDK extraction: "
            f"{format_transfer_size(free)} free, approximately "
            f"{format_transfer_size(required)} working space reserved for "
            f"{archive.name}."
        )


def _clear_failed_provider_cache(cache_root: Path, provider: str) -> None:
    provider_root = _provider_cache_root(cache_root, provider)
    for staging in provider_root.glob(".sdk-*.tmp"):
        shutil.rmtree(staging, ignore_errors=True)
    downloads = provider_root / "downloads"
    if downloads.exists():
        shutil.rmtree(downloads, ignore_errors=True)
        info(f"Cleared failed Android SDK provider download cache: {downloads}")


def clear_selected_provider_download(
    cache_root: Path,
    sdk_root: Path | None,
    *,
    dry_run: bool,
) -> None:
    if sdk_root is None:
        return
    selected = sdk_root.expanduser().resolve(strict=False)
    for provider in ("termuxvoid", "homuhomu"):
        provider_sdk = _provider_sdk_root(cache_root, provider).resolve(strict=False)
        if selected != provider_sdk and provider_sdk not in selected.parents:
            continue
        downloads = _provider_cache_root(cache_root, provider) / "downloads"
        if not downloads.exists():
            return
        size = _path_size_bytes(downloads)
        if dry_run:
            info(f"Would clear installed {provider} provider download cache: {downloads}")
        else:
            shutil.rmtree(downloads, ignore_errors=True)
            suffix = f" ({format_transfer_size(size)})" if size else ""
            info(f"Cleared installed {provider} provider download cache{suffix}: {downloads}")
        return


def _sdk_tree_candidate(root: Path) -> Path | None:
    candidates = [root, root / "android-sdk", root / "sdk"]
    try:
        candidates.extend(path for path in root.iterdir() if path.is_dir())
    except OSError:
        pass
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        if (candidate / "build-tools").is_dir() and (
            (candidate / "platform-tools").is_dir()
            or (candidate / "cmdline-tools").is_dir()
        ):
            return candidate
    return None


def _provider_cached_sdk(cache_root: Path, provider: str) -> Path | None:
    root = _provider_sdk_root(cache_root, provider)
    candidate = _sdk_tree_candidate(root)
    if candidate is not None and sdk_host_compatible(candidate):
        return candidate.resolve()
    return None


def _github_release_asset(release: Mapping[str, object], predicate, *, label: str) -> Mapping[str, object]:
    assets = release.get("assets") if isinstance(release, Mapping) else None
    if not isinstance(assets, list):
        fail(f"{label} release metadata contains no assets")
    matches = [asset for asset in assets if isinstance(asset, Mapping) and predicate(str(asset.get("name") or ""))]
    if not matches:
        names = ", ".join(str(asset.get("name") or "") for asset in assets if isinstance(asset, Mapping))
        fail(f"No {label} release asset matched this Termux host. Assets: {names or 'none'}")
    matches.sort(key=lambda asset: str(asset.get("name") or ""))
    return matches[0]


def _release_asset_sha256(release: Mapping[str, object], asset: Mapping[str, object]) -> str | None:
    digest = str(asset.get("digest") or "").strip().lower()
    if digest.startswith("sha256:") and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        return digest.split(":", 1)[1]
    name = re.escape(str(asset.get("name") or ""))
    body = str(release.get("body") or "")
    patterns = (
        rf"(?i){name}[^0-9a-f]{{0,80}}([0-9a-f]{{64}})",
        rf"(?i)([0-9a-f]{{64}})[^\n]{{0,80}}{name}",
    )
    for pattern in patterns:
        match = re.search(pattern, body)
        if match:
            return match.group(1).lower()
    return None


def _provider_archive_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(
        item.strip()
        for item in (result.stdout, result.stderr)
        if item and item.strip()
    )


def _validate_provider_symlinks(root: Path) -> None:
    """Reject provider-archive symlinks that escape the staging directory.

    Recent 7-Zip releases deliberately reject some chained symlinks. Android
    NDK archives legitimately contain chains such as ``clang++ -> clang``.
    When we opt into 7-Zip's ``-snld`` compatibility mode we compensate by
    validating every extracted link before the staged tree can replace a live
    SDK.
    """

    base = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            raw_target = os.readlink(path)
        except OSError as exc:
            fail(f"Could not inspect Android SDK provider symlink: {path}\n{exc}")
        if os.path.isabs(raw_target):
            fail(
                "Android SDK provider archive contains an absolute symlink: "
                f"{path} -> {raw_target}"
            )
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(base)
        except (OSError, RuntimeError, ValueError):
            fail(
                "Android SDK provider archive contains a symlink escaping its "
                f"staging directory: {path} -> {raw_target}"
            )


def _extract_sdk_provider_archive(archive: Path, staging: Path, *, dry_run: bool) -> None:
    if dry_run:
        info(f"Would extract Android SDK provider archive: {archive} -> {staging}")
        return
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    if archive.name.lower().endswith(".7z"):
        seven = shutil.which("7z") or shutil.which("7zz")
        if not seven:
            fail("7z is required to extract the TermuxVoid Android SDK bundle")
        run([seven, "t", archive])
        command = [seven, "x", archive, f"-o{staging}", "-y"]
        result = run(command, check=False, capture=True, quiet=True)
        if result.returncode != 0:
            output = _provider_archive_output(result)
            dangerous_link_markers = (
                "dangerous link via another link was ignored",
                "dangerous symbolic link path was ignored",
                "dangerous link path was ignored",
            )
            if not any(marker in output.lower() for marker in dangerous_link_markers):
                fail(
                    f"Command failed ({result.returncode}): {shell_join(command)}"
                    + (f"\n{output}" if output else "")
                )

            # 7-Zip 25+ tightened symlink extraction and can reject legitimate
            # Android NDK link chains. -snld is the upstream CLI escape hatch.
            # Retry only for that specific diagnostic, inside a fresh staging
            # tree, then reject any link that resolves outside that tree.
            warn(
                "7z rejected Android SDK/NDK symlink chains; retrying in isolated "
                "staging with -snld and validating all extracted symlink targets"
            )
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True, exist_ok=True)
            retry = [seven, "x", "-snld", archive, f"-o{staging}", "-y"]
            result = run(retry, check=False, capture=True, quiet=True)
            if result.returncode != 0:
                output = _provider_archive_output(result)
                if any(marker in output.lower() for marker in dangerous_link_markers):
                    bsdtar = shutil.which("bsdtar")
                    if bsdtar:
                        warn(
                            "7z still rejected Android SDK/NDK symlink chains; "
                            "retrying with Termux libarchive/bsdtar in isolated staging"
                        )
                        shutil.rmtree(staging, ignore_errors=True)
                        staging.mkdir(parents=True, exist_ok=True)
                        fallback = [bsdtar, "-xf", archive, "-C", staging]
                        result = run(fallback, check=False, capture=True, quiet=True)
                        if result.returncode != 0:
                            fallback_output = _provider_archive_output(result)
                            fail(
                                f"Command failed ({result.returncode}): {shell_join(fallback)}"
                                + (f"\n{fallback_output}" if fallback_output else "")
                            )
                    else:
                        fail(
                            f"Command failed ({result.returncode}): {shell_join(retry)}"
                            + (f"\n{output}" if output else "")
                        )
                else:
                    fail(
                        f"Command failed ({result.returncode}): {shell_join(retry)}"
                        + (f"\n{output}" if output else "")
                    )
        _validate_provider_symlinks(staging)
    else:
        extract_archive(archive, staging)
        _validate_provider_symlinks(staging)


def _install_provider_sdk_tree(
    *,
    cache_root: Path,
    provider: str,
    release: Mapping[str, object],
    asset: Mapping[str, object],
    force: bool,
    dry_run: bool,
) -> Path:
    tag = str(release.get("tag_name") or "latest").strip() or "latest"
    asset_name = str(asset.get("name") or "").strip()
    asset_url = str(asset.get("browser_download_url") or "").strip()
    if not asset_name or not asset_url:
        fail(f"{provider} release asset metadata is incomplete")
    expected = _release_asset_sha256(release, asset)
    if not expected:
        fail(f"{provider} release asset has no published SHA-256 digest: {asset_name}")

    provider_root = _provider_cache_root(cache_root, provider)
    archive = provider_root / "downloads" / tag / asset_name
    download(asset_url, archive, offline=False, force=force)
    verify_sha256(archive, expected, f"{provider} Android SDK")
    _ensure_provider_extraction_space(provider, archive, provider_root)
    target = _provider_sdk_root(cache_root, provider)
    if dry_run:
        return target

    staging = provider_root / f".sdk-{os.getpid()}.tmp"
    try:
        _extract_sdk_provider_archive(archive, staging, dry_run=False)
        extracted = _sdk_tree_candidate(staging)
        if extracted is None:
            fail(f"{provider} archive did not contain a recognizable Android SDK tree")
        if not sdk_host_compatible(extracted):
            fail(f"{provider} Android SDK contains host-incompatible native build tools")
    except Exception:
        # Provider archives can be hundreds of MiB and a failed extraction can
        # consume substantially more. Never leave a partial .sdk-*.tmp tree
        # behind when auto falls through to another provider.
        shutil.rmtree(staging, ignore_errors=True)
        raise

    backup = provider_root / ".sdk.previous"
    shutil.rmtree(backup, ignore_errors=True)
    if target.exists():
        target.replace(backup)
    try:
        if extracted == staging:
            staging.replace(target)
        else:
            extracted.replace(target)
            shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        if backup.exists():
            backup.replace(target)
        raise
    shutil.rmtree(backup, ignore_errors=True)
    provenance = {
        "provider": provider,
        "tag": tag,
        "asset": asset_name,
        "asset_url": asset_url,
        "sha256": expected,
    }
    (target / ".devtool-sdk-provider.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target.resolve()


def provision_termuxvoid_sdk(cache_root: Path, *, force: bool, dry_run: bool) -> Path:
    if not running_under_termux_python() or normalized_machine() != "arm64":
        fail("TermuxVoid Android SDK auto-provisioning requires native ARM64 Termux")
    existing = _termux_prefix_path() / "opt" / "android-sdk"
    if existing.is_dir() and _sdk_tree_candidate(existing) is not None and sdk_host_compatible(existing):
        info(f"Using installed TermuxVoid Android SDK: {existing}")
        return existing.resolve()
    cached = _provider_cached_sdk(cache_root, "termuxvoid")
    if cached is not None and not force:
        info(f"Using cached TermuxVoid Android SDK: {cached}")
        return cached

    ensure_termuxvoid_repository(cache_root=cache_root, dry_run=dry_run)
    _ensure_termux_packages(["7zip", "bsdtar"], dry_run=dry_run)
    if dry_run:
        target = _provider_sdk_root(cache_root, "termuxvoid")
        info(f"Would query TermuxVoid release metadata and provision SDK at: {target}")
        return target
    endpoint = f"https://api.github.com/repos/{TERMUXVOID_SDK_REPOSITORY}/releases/latest"
    release = github_json(endpoint, offline=False)
    asset = _github_release_asset(
        release,
        lambda name: name.lower() == "android-sdk.7z",
        label="TermuxVoid Android SDK",
    )
    return _install_provider_sdk_tree(
        cache_root=cache_root,
        provider="termuxvoid",
        release=release,
        asset=asset,
        force=force,
        dry_run=dry_run,
    )


def provision_homuhomu_sdk(cache_root: Path, *, force: bool, dry_run: bool) -> Path:
    if not running_under_termux_python() or normalized_machine() != "arm64":
        fail("HomuHomu Android-host SDK auto-provisioning requires native ARM64 Termux")
    cached = _provider_cached_sdk(cache_root, "homuhomu")
    if cached is not None and not force:
        info(f"Using cached HomuHomu Android SDK: {cached}")
        return cached
    if dry_run:
        target = _provider_sdk_root(cache_root, "homuhomu")
        info(f"Would query HomuHomu release metadata and provision SDK at: {target}")
        return target
    endpoint = f"https://api.github.com/repos/{HOMUHOMU_SDK_REPOSITORY}/releases/latest"
    release = github_json(endpoint, offline=False)
    asset = _github_release_asset(
        release,
        lambda name: (
            "android-sdk" in name.lower()
            and "aarch64-linux-android" in name.lower()
            and name.lower().endswith((".tar.xz", ".txz", ".tar.gz", ".tgz"))
        ),
        label="HomuHomu ARM64 Android SDK",
    )
    return _install_provider_sdk_tree(
        cache_root=cache_root,
        provider="homuhomu",
        release=release,
        asset=asset,
        force=force,
        dry_run=dry_run,
    )


def provision_android_sdk_provider(
    provider: str,
    cache_root: Path,
    *,
    force: bool,
    dry_run: bool,
    downloads: str,
    network: str,
) -> tuple[Path, str]:
    provider = normalize_android_sdk_provider(provider)
    if provider == "managed":
        return managed_sdk_root_for_host(cache_root), "managed Google SDK"
    if not bootstrap_provisioning_allowed(downloads, network):
        fail_bootstrap_blocked(
            f"Android SDK provider {provider}",
            downloads=downloads,
            network=network,
            checked=[str(_provider_sdk_root(cache_root, provider))],
        )
    if provider == "termuxvoid":
        return provision_termuxvoid_sdk(cache_root, force=force, dry_run=dry_run), "TermuxVoid preassembled SDK"
    if provider == "homuhomu":
        return provision_homuhomu_sdk(cache_root, force=force, dry_run=dry_run), "HomuHomu ARM64 Android SDK"

    # Auto is deliberately Termux-specific. On ordinary hosts retain Devtool's
    # existing managed Google SDK behavior without introducing third-party repos.
    if running_under_termux_python() and normalized_machine() == "arm64":
        for candidate_provider, label in (
            ("termuxvoid", "TermuxVoid preassembled SDK"),
            ("homuhomu", "HomuHomu ARM64 Android SDK"),
        ):
            cached = _provider_cached_sdk(cache_root, candidate_provider)
            if cached is not None and not force:
                info(f"Android SDK provider auto: {label} (cached)")
                return cached, label
            try:
                root = (
                    provision_termuxvoid_sdk(cache_root, force=force, dry_run=dry_run)
                    if candidate_provider == "termuxvoid"
                    else provision_homuhomu_sdk(cache_root, force=force, dry_run=dry_run)
                )
                info(f"Android SDK provider auto: {label}")
                return root, label
            except BuildError as exc:
                warn(f"Android SDK provider {candidate_provider} unavailable: {exc}")
                if not dry_run:
                    _clear_failed_provider_cache(cache_root, candidate_provider)
    return managed_sdk_root_for_host(cache_root), "managed Google SDK"


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


def _rootfs_tar_members(package: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Return rootfs members with absolute links made extraction-root relative.

    Linux root filesystem archives legitimately contain absolute symbolic
    links, for example ``etc/alternatives/awk -> /usr/bin/mawk``. Python 3.14
    defaults tar extraction to the ``data`` filter, which correctly rejects
    such links for ordinary archives. For a verified rootfs, preserve the
    rootfs meaning without weakening extraction globally: rewrite absolute
    symlink targets to equivalent relative targets inside the extraction root.
    Absolute hard-link names are rewritten to archive-root-relative names.
    """

    members: list[tarfile.TarInfo] = []

    for member in package.getmembers():
        candidate = member

        if member.linkname and posixpath.isabs(member.linkname):
            candidate = copy.copy(member)
            target = posixpath.normpath(member.linkname.lstrip("/"))

            if member.issym():
                parent = posixpath.dirname(posixpath.normpath(member.name)) or "."
                candidate.linkname = posixpath.relpath(target, start=parent)
            elif member.islnk():
                candidate.linkname = target

        members.append(candidate)

    return members


def extract_archive(
    archive: Path,
    destination: Path,
    *,
    rootfs_links: bool = False,
) -> None:
    """Extract a zip/tar Android bootstrap archive.

    Ordinary tar extraction keeps Python's safe default extraction policy.
    ``rootfs_links=True`` is reserved for checksum-verified Linux rootfs
    archives used as QEMU loader prefixes. It rewrites rootfs-absolute links
    before applying Python's ``data`` extraction filter when available.
    """

    destination.mkdir(parents=True, exist_ok=True)
    suffixes = "".join(archive.suffixes).lower()

    if archive.suffix.lower() == ".zip":
        extract_zip(archive, destination)
        return

    if (
        suffixes.endswith(
            (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2")
        )
        or archive.suffix.lower() == ".tar"
    ):
        with tarfile.open(archive) as package:
            if rootfs_links:
                members = _rootfs_tar_members(package)

                if hasattr(tarfile, "data_filter"):
                    package.extractall(
                        destination,
                        members=members,
                        filter="data",
                    )
                else:
                    package.extractall(destination, members=members)
            else:
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


def node_runtime_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Return a Node.js runtime executable visible in the effective environment."""

    env = env or os.environ
    search_path = str(env.get("PATH", ""))

    for name in ("node", "nodejs"):
        resolved = shutil.which(name, path=search_path)
        if resolved:
            candidate = Path(resolved)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate

    # Linuxbrew versioned formulae are not necessarily linked into bin/.
    for candidate in (
        Path("/home/linuxbrew/.linuxbrew/opt/node@22/bin/node"),
        Path("/home/linuxbrew/.linuxbrew/opt/node/bin/node"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    return None



def is_connected_android_test_task(task: str) -> bool:
    """Return True for Gradle connected-device Android-test tasks.

    Examples:
      :app:connectedDebugAndroidTest
      connectedReleaseAndroidTest

    These tasks require at least one ADB device in state ``device``.
    """

    leaf = str(task or "").strip().rsplit(":", 1)[-1].lower()

    return (
        leaf.startswith("connected")
        and leaf.endswith("androidtest")
    )


def _parse_adb_device_states(output: str) -> dict[str, str]:
    """Parse ``adb devices`` output into serial -> state."""

    devices: dict[str, str] = {}

    for raw in str(output or "").splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("List of devices attached")
            or line.startswith("* daemon")
        ):
            continue

        fields = line.split()

        if len(fields) >= 2:
            devices[fields[0]] = fields[1]

    return devices


def _adb_candidates_for_sdk(
    sdk_root: Path | None,
    env: Mapping[str, str] | None = None,
) -> list[Path]:
    """Return usable ADB candidates, preferring the selected SDK."""

    env = env or os.environ
    candidates: list[Path] = []

    if sdk_root is not None:
        candidates.append(
            Path(sdk_root) / "platform-tools" / "adb"
        )

    resolved = shutil.which(
        "adb",
        path=str(env.get("PATH", "")),
    )

    if resolved:
        candidates.append(Path(resolved))

    unique: list[Path] = []
    seen: set[str] = set()

    for candidate in candidates:
        key = str(candidate)

        if key in seen:
            continue

        seen.add(key)

        if (
            candidate.is_file()
            and os.access(candidate, os.X_OK)
        ):
            unique.append(candidate)

    return unique


def connected_android_devices(
    sdk_root: Path | None,
    env: Mapping[str, str] | None = None,
    *,
    timeout: float = 8.0,
) -> tuple[list[str], str, dict[str, str]]:
    """Return usable ADB devices for connected Android tests.

    Only devices in the exact ADB state ``device`` are considered usable.
    Offline, unauthorized and bootloader entries do not make
    connected*AndroidTest runnable.
    """

    env = dict(env or os.environ)
    last_adb = ""
    last_states: dict[str, str] = {}

    for adb in _adb_candidates_for_sdk(sdk_root, env):
        last_adb = str(adb)

        try:
            result = subprocess.run(
                [str(adb), "devices"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
                env=env,
            )
        except (
            OSError,
            subprocess.TimeoutExpired,
        ):
            continue

        states = _parse_adb_device_states(result.stdout)
        last_states = states

        if result.returncode != 0:
            continue

        usable = [
            serial
            for serial, state in states.items()
            if state == "device"
        ]

        # A successful adb invocation is authoritative. Don't fall through
        # to another adb binary just because there are zero devices.
        return usable, str(adb), states

    return [], last_adb, last_states



def _force_gradle_configuration_cache_off(
    arguments: list[str],
) -> list[str]:
    """Force configuration cache off for an invocation.

    Devtool's generated no-device Gradle init script modifies task policy
    during configuration. Third-party Android plugins such as Chaquopy also
    retain Gradle configuration objects which are not configuration-cache
    serializable in this execution path.

    Command-line --no-configuration-cache is authoritative over a project
    gradle.properties setting.
    """

    result = [
        str(argument)
        for argument in arguments
        if str(argument) not in {
            "--configuration-cache",
            "--no-configuration-cache",
        }
    ]

    result.append("--no-configuration-cache")
    return result


def _write_no_device_connected_test_init_script(
    cache_root: Path,
) -> Path:
    """Create the Gradle policy used when no ADB device is available.

    The policy disables every connected*AndroidTest task lazily, so it also
    catches tasks which enter the graph transitively through release seals,
    qualification tasks, aggregate checks, or other custom Gradle tasks.
    """

    path = (
        Path(cache_root)
        / "android-connected-tests-no-device.init.gradle"
    )

    content = """// Generated by Devtool.
// No usable Android device was available when this invocation started.
//
// Do not remove connected Android tests from the project: merely disable
// them for this invocation. When a device is connected, Devtool does not
// load this init script and the tests run normally.

gradle.beforeProject { project ->
    project.tasks.configureEach { task ->
        def devtoolTaskName =
            task.name.toLowerCase(java.util.Locale.ROOT)

        if (
            devtoolTaskName.startsWith("connected") &&
            devtoolTaskName.endsWith("androidtest")
        ) {
            task.enabled = false
        }
    }
}
"""

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if (
        not path.exists()
        or path.read_text(
            encoding="utf-8",
            errors="replace",
        ) != content
    ):
        path.write_text(
            content,
            encoding="utf-8",
        )

    return path


def filter_unavailable_connected_android_tests(
    tasks: Sequence[str],
    *,
    sdk_root: Path | None,
    env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> tuple[list[str], list[str], str, dict[str, str]]:
    """Skip connected*AndroidTest tasks when no usable device exists."""

    requested = [
        str(task)
        for task in tasks
        if str(task).strip()
    ]

    connected = [
        task
        for task in requested
        if is_connected_android_test_task(task)
    ]

    if dry_run:
        return requested, [], "", {}

    # Probe even when no connected*AndroidTest task was requested directly.
    # A release/qualification/seal task may depend on a connected test
    # transitively, so explicit-task filtering alone is insufficient.
    devices, adb, states = connected_android_devices(
        sdk_root,
        env,
    )

    if devices:
        return requested, [], adb, states

    # There may be no explicitly requested connected task, while one of
    # the requested seal/qualification tasks pulls it into Gradle's task
    # graph transitively. Preserve the requested task list in that case;
    # the no-device Gradle init script below disables the transitive task.
    if not connected:
        return requested, [], adb, states

    skipped_set = set(connected)

    retained = [
        task
        for task in requested
        if task not in skipped_set
    ]

    return retained, connected, adb, states


def gradle_tasks_require_node(tasks: Sequence[str]) -> bool:
    """Recognize requested Gradle tasks that explicitly execute JS/Node tests."""

    for task in tasks:
        leaf = str(task).strip().rsplit(":", 1)[-1].lower()
        if leaf.startswith(("jstest", "nodetest", "nodejstest", "javascripttest")):
            return True

    return False


def install_missing_system_dependencies(
    *,
    need_java: bool,
    need_certificates: bool,
    need_node: bool = False,
    need_cmake: bool = False,
    need_ninja: bool = False,
    offline: bool,
    java_version: int = 17,
    java_provider: str = "auto",
) -> None:
    if not need_java and not need_certificates and not need_node and not need_cmake and not need_ninja:
        info("System dependencies are already ready")
        return
    if offline:
        missing = []
        if need_java:
            missing.append("JDK")
        if need_certificates:
            missing.append("CA certificates")
        if need_node:
            missing.append("Node.js")
        if need_cmake:
            missing.append("CMake")
        if need_ninja:
            missing.append("Ninja")
        fail("Missing system dependencies in --offline mode: " + ", ".join(missing))

    if running_under_termux_python():
        provider = normalize_java_provider(java_provider)
        if need_java and provider == "system":
            fail(
                "Java provider system was requested on native Termux, but automatic Termux package "
                "installation only supplies Termux JDKs. Install/provide an external JDK with "
                "--java-home, or use --java-provider termux/auto."
            )
        packages: list[str] = []
        if need_java:
            packages.append(f"openjdk-{java_version}")
        if need_certificates:
            packages.append("ca-certificates")
        if need_node:
            packages.append("nodejs")
        if need_cmake:
            packages.append("cmake")
        if need_ninja:
            packages.append("ninja")
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
    if need_node:
        packages.append("nodejs")
    if need_cmake:
        packages.append("cmake")
    if need_ninja:
        packages.append("ninja-build")
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
    termux_home: Path | None


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


def _native_termux_sanitize_guest_path(
    value: Path | None,
    *,
    label: str,
    fallback: Path | None = None,
) -> Path | None:
    """Keep chroot-only /home and /root paths out of native Termux."""

    if value is None:
        return fallback

    candidate = value.expanduser()

    if not running_under_termux_python():
        return candidate

    text = str(candidate)
    guest_only = (
        text == "/home"
        or text.startswith("/home/")
        or text == "/root"
        or text.startswith("/root/")
    )

    if not guest_only:
        return candidate

    replacement = fallback.expanduser() if fallback is not None else None

    if replacement is None:
        info(
            f"Ignoring chroot-only {label} during native Termux execution: "
            f"{candidate}"
        )
    else:
        info(
            f"Replacing chroot-only {label} during native Termux execution: "
            f"{candidate} -> {replacement}"
        )

    return replacement


def termux_prefix_path() -> Path:
    raw = str(os.environ.get("PREFIX", "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    return Path("/data/data/com.termux/files/usr")


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def normalize_java_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    if normalized not in JAVA_PROVIDERS:
        valid = ", ".join(sorted(JAVA_PROVIDERS))
        fail(f"Unsupported Java provider: {value}. Expected one of: {valid}")
    return normalized


def normalize_gradle_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    if normalized in {"managed", "downloaded"}:
        normalized = "download"
    if normalized not in GRADLE_PROVIDERS:
        valid = ", ".join(sorted(GRADLE_PROVIDERS))
        fail(f"Unsupported Gradle provider: {value}. Expected one of: {valid}")
    return normalized


def executable_provider(path: Path) -> str:
    if running_under_termux_python() and _path_is_within(path, termux_prefix_path()):
        return "termux"
    return "system"


def normalize_native_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    aliases = {"termux": "termux-sysroot", "full-ndk": "ndk", "android-ndk": "ndk"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in NATIVE_PROVIDERS:
        valid = ", ".join(sorted(NATIVE_PROVIDERS))
        fail(f"Unsupported native provider: {value}. Expected one of: {valid}")
    return normalized


def normalize_native_cache_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    if normalized in {"none", "false", "disabled"}:
        normalized = "off"
    if normalized not in NATIVE_CACHE_PROVIDERS:
        valid = ", ".join(sorted(NATIVE_CACHE_PROVIDERS))
        fail(f"Unsupported native cache provider: {value}. Expected one of: {valid}")
    return normalized


def normalize_android_build_tools_provider(value: str | None) -> str:
    normalized = str(value or "auto").strip().lower() or "auto"
    aliases = {"download": "commit451", "downloaded": "commit451", "android-sdk": "sdk"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in ANDROID_BUILD_TOOLS_PROVIDERS:
        valid = ", ".join(sorted(ANDROID_BUILD_TOOLS_PROVIDERS))
        fail(f"Unsupported Android build-tools provider: {value}. Expected one of: {valid}")
    return normalized


def termux_tool_path(name: str) -> Path | None:
    if not running_under_termux_python():
        return None
    found = shutil.which(name)
    if not found:
        return None
    resolved = Path(found).expanduser().resolve(strict=False)
    return resolved if _path_is_within(resolved, termux_prefix_path()) else None


def termux_ndk_sysroot_ready(provider_evidence=None) -> tuple[bool, str]:
    if not running_under_termux_python():
        return False, "not running in native Termux"
    if provider_evidence is not None:
        provider = provider_evidence.native
    else:
        provider = _termux_toolchains_runtime_module().termux_native_provider(env=os.environ)
    if not provider.compatible:
        return False, provider.rejection_reason or provider.reason or "Termux native provider is not ready"
    return True, provider.selection_reason or provider.reason or "runtime-verified Termux native provider"


def native_requirement(project: "ProjectInfo") -> str:
    if project.android and project.native_build:
        return "android-ndk"
    return "none"


def select_native_provider(project: "ProjectInfo", requested: str, *, provider_evidence=None) -> str:
    provider = normalize_native_provider(requested)
    requirement = (
        provider_evidence.native_requirement.capability
        if provider_evidence is not None and getattr(provider_evidence, "native_requirement", None) is not None
        else native_requirement(project)
    )
    if requirement == "android-ndk":
        if provider not in {"auto", "ndk"}:
            fail("This Android native build requires a complete Android NDK; termux-sysroot/system compiler providers are insufficient")
        return "ndk"
    if provider in {"auto", "termux-sysroot"}:
        ready, reason = termux_ndk_sysroot_ready(provider_evidence)
        if ready:
            info(f"Native provider: Termux sysroot ({reason})")
            return "termux-sysroot"
        if provider == "termux-sysroot":
            fail(f"Termux native provider requested but unavailable: {reason}")
    return "system" if provider == "auto" else provider


def configure_native_cache(env: MutableMapping[str, str], requested: str, *, provider_evidence=None) -> str:
    provider = normalize_native_cache_provider(requested)
    if running_under_termux_python():
        if provider_evidence is not None:
            ccache_probe = provider_evidence.ccache
        else:
            ccache_probe = _termux_toolchains_runtime_module().termux_ccache_probe(env=os.environ)
        ccache = ccache_probe.path if ccache_probe.compatible else None
    else:
        ccache = Path(shutil.which("ccache")).resolve() if shutil.which("ccache") else None
    if provider == "ccache" and ccache is None:
        fail("ccache provider requested but ccache is not available")
    if provider != "off" and ccache is not None:
        selected = str(ccache)
        owners = {
            key: str(env.get(key) or "").strip()
            for key in ("CMAKE_C_COMPILER_LAUNCHER", "CMAKE_CXX_COMPILER_LAUNCHER")
        }
        conflicts = [f"{key}={value}" for key, value in owners.items() if value and Path(value).expanduser() != ccache]
        if conflicts:
            if provider == "ccache":
                fail("Explicit ccache cannot own compiler launchers because they are already configured: " + ", ".join(conflicts))
            return "off"
        env.setdefault("CCACHE_DIR", str(Path.home() / ".cache" / "devtool" / "ccache"))
        env["CMAKE_C_COMPILER_LAUNCHER"] = selected
        env["CMAKE_CXX_COMPILER_LAUNCHER"] = selected
        info(f"Native compiler cache: {ccache}")
        return "ccache"
    return "off"


def termux_package_version(name: str) -> str | None:
    if not running_under_termux_python():
        return None
    dpkg_query = shutil.which("dpkg-query")
    if not dpkg_query:
        return None
    try:
        result = subprocess.run(
            [dpkg_query, "-W", "-f=${Status}\t${Version}", name],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    status, _, version = result.stdout.strip().partition("\t")
    if result.returncode != 0 or "install ok installed" not in status:
        return None
    return version.strip() or None


def termux_jvm_candidates() -> list[Path]:
    if not running_under_termux_python():
        return []
    root = termux_prefix_path() / "lib" / "jvm"
    if not root.is_dir():
        return []
    try:
        return sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name)
    except OSError:
        return []


@dataclass(frozen=True)
class GradleWrapperState:
    present: bool
    complete: bool
    missing: tuple[str, ...]


def standalone_gradle_wrapper_version(project_root: Path) -> str | None:
    """Recognize Devtool-style self-bootstrap Gradle launchers.

    Some projects intentionally ship a small ``gradlew`` shell launcher that
    pins a Gradle version and downloads the official distribution directly,
    instead of using Gradle's ``gradle-wrapper.jar`` bootstrap.  Treat that
    format as complete only when the launcher contains all of the contract
    markers we rely on: a pinned VERSION, the official Gradle distribution
    endpoint, and an execution path into the downloaded Gradle distribution.
    A random/incomplete ``gradlew`` therefore remains fail-closed.
    """
    launcher = project_root / "gradlew"
    if not launcher.is_file():
        return None
    try:
        text = launcher.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) > 256 * 1024:
        return None
    match = re.search(r"(?m)^\s*VERSION\s*=\s*['\"]?([0-9][0-9A-Za-z._-]*)['\"]?\s*$", text)
    if not match:
        return None
    version = match.group(1)
    distribution_markers = (
        "services.gradle.org/distributions/gradle-$VERSION-bin.zip",
        "services.gradle.org/distributions/gradle-${VERSION}-bin.zip",
    )
    if not any(marker in text for marker in distribution_markers):
        return None
    if "bin/gradle" not in text or "exec " not in text:
        return None
    return version


def gradle_wrapper_state(project_root: Path) -> GradleWrapperState:
    files = (
        ("gradlew", project_root / "gradlew"),
        ("gradle/wrapper/gradle-wrapper.properties", project_root / "gradle" / "wrapper" / "gradle-wrapper.properties"),
        ("gradle/wrapper/gradle-wrapper.jar", project_root / "gradle" / "wrapper" / "gradle-wrapper.jar"),
    )
    present = any(path.exists() for _, path in files)
    if standalone_gradle_wrapper_version(project_root):
        return GradleWrapperState(present=True, complete=True, missing=())
    missing = tuple(label for label, path in files if not path.is_file())
    return GradleWrapperState(present=present, complete=present and not missing, missing=missing)


def broken_wrapper_message(project_root: Path, state: GradleWrapperState) -> str:
    missing = "\n".join(f"- {item}" for item in state.missing) or "- unknown wrapper component"
    termux_gradle = shutil.which("gradle") if running_under_termux_python() else None
    fallback = ""
    if termux_gradle:
        fallback = (
            f"\n\nTermux Gradle is available at {termux_gradle}, but it was not selected "
            "automatically because replacing an incomplete project wrapper can violate the project's Gradle/AGP version contract."
        )
    return (
        f"Project Gradle wrapper is incomplete in {project_root}.\n\nMissing:\n{missing}"
        f"{fallback}\n\nRestore the wrapper files from version control, regenerate them with a compatible Gradle version, "
        "or explicitly select another provider with --gradle-provider."
    )


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
        default=(Path(os.environ["GRADLE_BUILD_CHROOT_TERMUX_HOME"]) if os.environ.get("GRADLE_BUILD_CHROOT_TERMUX_HOME") else None),
    )
    options, _ = parser.parse_known_args(before_gradle)
    return ChrootLaunchOptions(
        mode=options.chroot,
        name=options.chroot_name,
        termux_home=options.chroot_termux_home.expanduser() if options.chroot_termux_home is not None else None,
    )


def devtool_cache_dir() -> Path:
    override = str(os.environ.get("DEVTOOL_XDG_DIR", "") or "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("DEVTOOL_XDG_DIR must be an absolute path")
        return path.resolve(strict=False)
    raw = str(os.environ.get("XDG_CACHE_HOME", "") or "").strip()
    if raw:
        path = Path(raw).expanduser()
        if path.is_absolute():
            return path.resolve(strict=False) / "devtool"
        # Relative XDG values are invalid and must not become cwd-relative.
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
    if chroot_termux_home is None:
        message = (
            "Chroot Termux-home mapping is unknown. Set GRADLE_BUILD_CHROOT_TERMUX_HOME "
            "or pass --chroot-termux-home with the verified mount path inside the selected chroot."
        )
        if options.mode == "always":
            fail(message)
        warn(message + " Continuing directly in Termux.")
        return None
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

    def provenance_path(release_dir: Path) -> Path:
        return release_dir / "provenance.json"

    def verified_cached(release_dir: Path) -> Path | None:
        target = release_dir / "aapt2"
        meta = provenance_path(release_dir)
        if not target.is_file() or not meta.is_file():
            return None
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            recorded = str(data.get("binary_sha256") or "").lower()
            archive_recorded = str(data.get("archive_sha256") or "").lower()
            asset_name = str(data.get("asset_name") or "").strip()
        except (OSError, json.JSONDecodeError):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", recorded) or not re.fullmatch(r"[0-9a-f]{64}", archive_recorded) or not asset_name:
            return None
        archive = release_dir / asset_name
        if not archive.is_file():
            return None
        try:
            if sha256(target) != recorded or sha256(archive) != archive_recorded:
                return None
        except OSError:
            return None
        return validate_aapt2(target, dry_run=dry_run)

    if offline:
        if release_name != "latest":
            cached = verified_cached(metadata_dir / release_name)
            if cached is not None:
                info(f"Using cached ReVanced AAPT2 {release_name}: {cached}")
                return cached
            fail(f"Cached ReVanced AAPT2 tag {release_name!r} is unavailable or failed digest verification in offline mode")
        candidates: list[Path] = []
        for release_dir in metadata_dir.iterdir() if metadata_dir.is_dir() else ():
            if not release_dir.is_dir():
                continue
            candidate = verified_cached(release_dir)
            if candidate is not None:
                candidates.append(candidate)
        if candidates:
            candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
            info(f"Using verified cached ReVanced AAPT2: {candidates[0]}")
            return candidates[0]
        fail("No verified cached ReVanced AAPT2 release is available in offline mode")

    endpoint = f"{REVANCED_API}/releases/latest" if release_name == "latest" else f"{REVANCED_API}/releases/tags/{release_name}"
    release = github_json(endpoint, offline=False)
    tag = str(release.get("tag_name") or release_name)
    asset = select_revanced_asset(release, machine)
    asset_name = str(asset["name"])
    asset_url = str(asset["browser_download_url"])
    release_dir = metadata_dir / tag
    target = release_dir / "aapt2"
    if target.is_file() and not force:
        cached = verified_cached(release_dir)
        if cached is not None:
            info(f"Using verified cached ReVanced AAPT2 {tag}: {target}")
            return cached

    archive = release_dir / asset_name
    download(asset_url, archive, offline=False, force=force)
    release_dir.mkdir(parents=True, exist_ok=True)
    archive_digest = sha256(archive)
    if dry_run:
        return target

    temporary_target = release_dir / "aapt2.devtool.tmp"
    temporary_target.unlink(missing_ok=True)
    if zipfile.is_zipfile(archive):
        with tempfile.TemporaryDirectory(prefix="gradle-build-aapt2-") as temporary_dir:
            temporary = Path(temporary_dir)
            extract_zip(archive, temporary)
            candidates = [path for path in temporary.rglob("*") if path.is_file() and "aapt2" in path.name.lower()]
            if not candidates:
                fail(f"ReVanced asset contained no AAPT2 executable: {archive}")
            candidates.sort(key=lambda path: (path.name.lower() != "aapt2", len(path.parts)))
            shutil.copy2(candidates[0], temporary_target)
    else:
        shutil.copy2(archive, temporary_target)
    validate_aapt2(temporary_target, dry_run=False)
    binary_digest = sha256(temporary_target)
    temporary_target.replace(target)
    payload = {
        "tag": tag,
        "asset_name": asset_name,
        "asset_url": asset_url,
        "archive_sha256": archive_digest,
        "binary_sha256": binary_digest,
    }
    meta_tmp = provenance_path(release_dir).with_suffix(".json.tmp")
    meta_tmp.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    meta_tmp.replace(provenance_path(release_dir))
    return validate_aapt2(target, dry_run=False)


def termux_aapt2_path() -> Path | None:
    path = termux_tool_path("aapt2")
    if path is None or not executable_runs(path, "version"):
        return None
    return path


def termux_aapt2_supports_platform(
    aapt2: Path,
    sdk_root: Path,
    platform_id: str | None,
    *,
    dry_run: bool = False,
) -> bool:
    if dry_run:
        return True
    if not platform_id:
        return executable_runs(aapt2, "version")
    android_jar = sdk_root / "platforms" / platform_id / "android.jar"
    if not android_jar.is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="devtool-aapt2-probe-") as temporary_dir:
            root = Path(temporary_dir)
            values = root / "res" / "values"
            values.mkdir(parents=True)
            (values / "strings.xml").write_text('<resources><string name="app_name">probe</string></resources>\n', encoding="utf-8")
            manifest = root / "AndroidManifest.xml"
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="devtool.probe"><uses-sdk android:minSdkVersion="21"/><application android:label="@string/app_name"/></manifest>\n', encoding="utf-8")
            compiled = root / "compiled.zip"
            linked = root / "probe.apk"
            compile_result = subprocess.run([str(aapt2), "compile", "--dir", str(root / "res"), "-o", str(compiled)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
            if compile_result.returncode != 0:
                return False
            link_result = subprocess.run([str(aapt2), "link", "-I", str(android_jar), "--manifest", str(manifest), "-o", str(linked), str(compiled)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False)
            return link_result.returncode == 0 and linked.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def select_termux_aapt2(
    sdk_root: Path | None,
    platform_id: str | None,
    *,
    required: bool,
    dry_run: bool = False,
    provider_evidence=None,
) -> Path | None:
    candidate = None
    if provider_evidence is not None:
        provider = next((item for item in provider_evidence.android_tools if item.capability == "aapt2"), None)
        if provider is not None and provider.compatible and provider.path is not None:
            candidate = provider.path
    else:
        candidate = termux_aapt2_path()
    if candidate is None:
        if required:
            fail("Termux AAPT2 provider requested but $PREFIX/bin/aapt2 is unavailable or cannot run")
        return None
    if sdk_root is None or not termux_aapt2_supports_platform(candidate, sdk_root, platform_id, dry_run=dry_run):
        if required:
            fail(f"Termux AAPT2 is not compatible with selected Android platform {platform_id or 'unknown'}: {candidate}")
        warn(f"Termux AAPT2 failed capability probe for Android platform {platform_id or 'unknown'}; using another provider")
        return None
    info(f"AAPT2 provider: Termux ({candidate})")
    return candidate



def select_sdk_build_tools_aapt2(
    sdk_root: Path | None,
    build_tools_version: str | None,
    platform_id: str | None,
    *,
    dry_run: bool = False,
) -> Path | None:
    """Return a runnable SDK build-tools AAPT2 binary when one is installed.

    The auto provider must be offline-first.  A local SDK AAPT2 is safe to use
    only when it can execute on the current host and link against the selected
    Android platform.  If either probe fails, callers can fall back to AGP's
    default Maven/SDK resolution without contacting unrelated remote metadata
    services.
    """

    if sdk_root is None or not build_tools_version:
        return None
    candidate = sdk_root / "build-tools" / build_tools_version / "aapt2"
    if not candidate.is_file():
        return None
    if not termux_aapt2_supports_platform(candidate, sdk_root, platform_id, dry_run=dry_run):
        warn(
            "Installed Android SDK AAPT2 failed capability probe for "
            f"platform {platform_id or 'unknown'}; using another provider"
        )
        return None
    info(f"AAPT2 provider: Android SDK build-tools ({candidate})")
    return candidate


def select_cached_revanced_aapt2(cache_root: Path, *, dry_run: bool = False) -> Path | None:
    """Return a verified cached ReVanced AAPT2 without any network access.

    This intentionally does not query GitHub release metadata.  AAPT2 provider
    ``auto`` may use a previously verified local cache entry, but a live remote
    ReVanced lookup is reserved for the explicit ``revanced`` provider so
    Android validation cannot fail merely because GitHub metadata is slow or
    unavailable.
    """

    root = cache_root / "aapt2" / "revanced"
    if not root.is_dir():
        return None
    candidates: list[Path] = []
    for release_dir in root.iterdir():
        if not release_dir.is_dir():
            continue
        target = release_dir / "aapt2"
        meta = release_dir / "provenance.json"
        if not target.is_file() or not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            recorded = str(data.get("binary_sha256") or "").lower()
            archive_recorded = str(data.get("archive_sha256") or "").lower()
            asset_name = str(data.get("asset_name") or "").strip()
            archive = release_dir / asset_name
            if not re.fullmatch(r"[0-9a-f]{64}", recorded):
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", archive_recorded):
                continue
            if not asset_name or not archive.is_file():
                continue
            if sha256(target) != recorded or sha256(archive) != archive_recorded:
                continue
        except (OSError, json.JSONDecodeError):
            continue
        candidates.append(target)
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    selected = validate_aapt2(candidates[0], dry_run=dry_run)
    info(f"AAPT2 provider: cached ReVanced ({selected})")
    return selected

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



def android_build_tool_executes(path: Path, tool_name: str, *, timeout: float = 5.0) -> bool:
    """Return whether an Android build-tool executable really runs here.

    Matching ELF architecture is not enough on Android/Termux: an AArch64
    glibc executable still cannot run under Bionic. In addition, termux-exec
    or a launcher shell can turn a loader/exec failure into a normal non-zero
    process result. Capture the harmless probe and reject those results rather
    than treating every successfully spawned process as compatible.
    """

    probes = {
        "aidl": ("--help",),
        "zipalign": ("-h",),
        "split-select": ("--help",),
        "aapt2": ("version",),
    }
    fatal_markers = (
        "syntax error",
        "exec format error",
        "cannot execute",
        "not executable",
        "bad elf",
        "wrong elf class",
        "no such file or directory",
        "cannot link executable",
        "linker:",
        "could not load",
    )
    args = probes.get(tool_name, ("--help",))
    try:
        result = subprocess.run(
            [str(path), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    output = (
        bytes(result.stdout or b"")
        + b"\n"
        + bytes(result.stderr or b"")
    ).decode("utf-8", errors="replace").lower()
    if any(marker in output for marker in fatal_markers):
        return False
    # Host compatibility asks whether the binary can *start*, not whether a
    # harmless help/usage probe is semantically successful. Different Android
    # SDK builds return 0, 1, 2, or even 255 for these probes. Negative values
    # mean termination by signal; 126/127 are conventional launcher failures.
    return result.returncode >= 0 and result.returncode not in {126, 127}

def native_android_build_tool_compatible(path: Path, tool_name: str, machine: str | None = None) -> bool:
    if not native_tool_matches_host(path, machine):
        return False
    # Architecture is only a static hint. Every execution host must prove that
    # the selected helper can actually start under its current loader/runtime.
    return android_build_tool_executes(path, tool_name)


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


def ndk_legacy_cmake_arm_host_tag() -> str:
    # CMake 3.22 (and several later releases) maps any Linux host processor
    # other than x86_64 to the historical ``linux-x86`` Android NDK host tag.
    # On AArch64 hosts that makes CMAKE_ANDROID_NDK_TOOLCHAIN_UNIFIED empty
    # when a modern NDK only exposes linux-x86_64 (or an ARM64 compatibility
    # prebuilt), and CMake then constructs broken paths such as
    # ``toolchains/llvm/prebuilt/bin/clang++``.  Devtool keeps the canonical
    # compatibility toolchain under linux-x86_64 and exposes this legacy alias
    # for ARM-hosted CMake discovery.
    return "linux-x86"


def ensure_ndk_legacy_cmake_arm_alias(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    if normalized_machine() not in {"arm64", "armv7"}:
        return True
    prebuilt = ndk_prebuilt_root(ndk_root_for_version(sdk_root, version))
    canonical = prebuilt / ndk_google_host_tag()
    if not canonical.is_dir():
        return False
    legacy = prebuilt / ndk_legacy_cmake_arm_host_tag()
    try:
        if legacy.exists() or legacy.is_symlink():
            if legacy.resolve(strict=True) == canonical.resolve(strict=True):
                return True
    except OSError:
        pass

    backup = legacy.with_name(legacy.name + ".devtool-original")
    if dry_run:
        info(f"Would expose NDK host toolchain for ARM-hosted CMake as {legacy}: {canonical}")
        return True

    if legacy.exists() or legacy.is_symlink():
        if not backup.exists() and not backup.is_symlink():
            legacy.rename(backup)
        elif legacy.is_dir() and not legacy.is_symlink():
            shutil.rmtree(legacy)
        else:
            legacy.unlink(missing_ok=True)
    # Use a relative alias so cached SDK trees remain movable.
    legacy.symlink_to(canonical.name, target_is_directory=True)
    info(f"Exposed NDK host toolchain for ARM-hosted CMake as {legacy}: {canonical}")
    return legacy.is_dir() and (legacy / "bin" / "clang++").is_file()


def ensure_ndk_empty_host_tag_compat(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    """Make an empty NDK host tag resolve to Devtool's normalized toolchain.

    Some CMake/NDK combinations running natively under Termux can still reach
    the legacy toolchain with ``ANDROID_HOST_TAG`` unset.  The NDK then forms
    ``toolchains/llvm/prebuilt/bin`` and ``.../prebuilt/sysroot`` rather than
    using a host-tagged directory.  Expose only those two compatibility
    entries at the prebuilt root so that even this empty-tag path resolves to
    the canonical ``linux-x86_64`` tree that Devtool has already made runnable.

    Existing real entries are preserved once as ``.devtool-original``.
    Relative symlinks keep cached SDK trees relocatable.
    """

    if not running_under_termux_python():
        return True
    prebuilt = ndk_prebuilt_root(ndk_root_for_version(sdk_root, version))
    canonical = prebuilt / ndk_google_host_tag()
    if not canonical.is_dir():
        return False

    aliases = (("bin", canonical / "bin"), ("sysroot", canonical / "sysroot"))
    for name, target in aliases:
        if not target.exists():
            return False
        alias = prebuilt / name
        try:
            if alias.is_symlink() and alias.resolve(strict=True) == target.resolve(strict=True):
                continue
        except OSError:
            pass

        backup = alias.with_name(alias.name + ".devtool-original")
        if dry_run:
            info(f"Would expose empty NDK host-tag compatibility path {alias}: {target}")
            continue

        if alias.exists() or alias.is_symlink():
            if not backup.exists() and not backup.is_symlink():
                alias.rename(backup)
            elif alias.is_dir() and not alias.is_symlink():
                shutil.rmtree(alias)
            else:
                alias.unlink(missing_ok=True)
        alias.symlink_to(Path(ndk_google_host_tag()) / name, target_is_directory=True)
        info(f"Exposed empty NDK host-tag compatibility path {alias}: {target}")

    if dry_run:
        return True
    return (prebuilt / "bin" / "clang++").is_file() and (prebuilt / "sysroot").is_dir()


def ensure_ndk_cmake_termux_host_support(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    """Teach the NDK CMake toolchain that native Termux is a supported host.

    CMake reports ``CMAKE_HOST_SYSTEM_NAME=Android`` when its executable runs
    natively under Termux.  The Android NDK's recommended legacy toolchain
    historically assigns ``ANDROID_HOST_TAG`` only for Linux, Darwin, and
    Windows.  On Termux that leaves the tag empty and produces paths such as
    ``toolchains/llvm/prebuilt/bin/clang++``.

    Devtool already normalizes a runnable host toolchain under
    ``linux-x86_64`` (native ARM64 or QEMU-wrapped), so extend the NDK's Linux
    host branch to Android as well.  Preserve the pristine file once and make
    the edit idempotent.
    """

    if not running_under_termux_python():
        return True

    cmake_dir = ndk_root_for_version(sdk_root, version) / "build" / "cmake"
    candidates = (
        cmake_dir / "android-legacy.toolchain.cmake",
        cmake_dir / "android.toolchain.cmake",
    )
    required = candidates[0] if candidates[0].is_file() else candidates[1]
    if not required.is_file():
        return False

    pattern = re.compile(
        r'(?m)^(?P<indent>[ \t]*)if\(CMAKE_HOST_SYSTEM_NAME STREQUAL ["\']?Linux["\']?\)'
        r'(?P<trailing>[ \t]*)\r?\n'
        r'(?P<body>[ \t]+)set\(ANDROID_HOST_TAG[ \t]+linux-x86_64\)'
    )

    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except OSError:
            if path == required:
                return False
            continue

        # Already patched (by Devtool or an upstream that gained Android-host
        # support) as long as the Android branch selects the normalized tag.
        host_window = re.search(
            r'(?ms)if\([^\n]*CMAKE_HOST_SYSTEM_NAME[^\n]*Android[^\n]*\)'
            r'.{0,240}?set\(ANDROID_HOST_TAG[ \t]+linux-x86_64\)',
            text,
        )
        if host_window:
            continue

        match = pattern.search(text)
        if match is None:
            if path == required:
                return False
            continue

        replacement = (
            f"{match.group('indent')}if(CMAKE_HOST_SYSTEM_NAME STREQUAL Linux OR "
            "CMAKE_HOST_SYSTEM_NAME STREQUAL Android)"
            f"{match.group('trailing')}\n{match.group('body')}set(ANDROID_HOST_TAG linux-x86_64)"
        )
        patched = text[: match.start()] + replacement + text[match.end() :]
        if dry_run:
            info(f"Would enable native Termux/Android host support in NDK CMake toolchain: {path}")
            continue

        backup = path.with_name(path.name + ".devtool-original")
        try:
            if not backup.exists():
                shutil.copy2(path, backup)
            mode = path.stat().st_mode
            temporary = path.with_name(path.name + f".devtool-{os.getpid()}.tmp")
            temporary.write_text(patched)
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            if path == required:
                return False
            continue
        info(f"Enabled native Termux/Android host support in NDK CMake toolchain: {path}")

    try:
        final = required.read_text()
    except OSError:
        return False
    return bool(
        re.search(
            r'(?ms)if\([^\n]*CMAKE_HOST_SYSTEM_NAME[^\n]*Android[^\n]*\)'
            r'.{0,240}?set\(ANDROID_HOST_TAG[ \t]+linux-x86_64\)',
            final,
        )
    )


def ndk_clangxx(sdk_root: Path, version: str) -> Path:
    return ndk_google_tool_bin(sdk_root, version) / "clang++"


def ndk_host_toolchain_runs(sdk_root: Path, version: str) -> bool:
    return executable_runs(ndk_clangxx(sdk_root, version), "--version")


def ndk_host_linker_runs(sdk_root: Path, version: str) -> bool:
    """Return whether the NDK ELF linker entry point selects a real LLD flavor.

    LLVM's ``lld`` executable is a multi-call driver.  Android NDKs normally
    expose ``ld.lld`` as a symlink/hardlink to it, and the invoked basename is
    what selects the ELF linker flavor.  A host shim or QEMU wrapper that loses
    that argv[0] identity can make clang compilation succeed but linking fail
    with "lld is a generic driver".
    """

    linker = ndk_google_tool_bin(sdk_root, version) / "ld.lld"
    return executable_runs(linker, "--version")


def ndk_clang_resource_dir(sdk_root: Path, version: str) -> Path | None:
    """Locate the Clang resource directory shipped by the selected NDK.

    The NDK keeps compiler-rt, libunwind, libatomic compatibility archives,
    and Clang builtin headers below the host prebuilt's lib/clang tree. QEMU
    wrappers cannot rely on Clang inferring this location from a wrapper or
    backup executable name, so Devtool resolves it directly from the NDK.
    """

    host_root = ndk_google_tool_bin(sdk_root, version).parent
    candidates: list[Path] = []
    for clang_root in (host_root / "lib" / "clang", host_root / "lib64" / "clang"):
        if not clang_root.is_dir():
            continue
        for child in clang_root.iterdir():
            try:
                if child.is_dir() and (child / "lib" / "linux").is_dir():
                    candidates.append(child)
            except OSError:
                continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: version_key(item.name))


def ndk_host_android_link_runs(sdk_root: Path, version: str) -> bool:
    """Smoke-link minimal Android programs for arm64 and x86_64.

    ``clang++ --version`` and ``-print-resource-dir`` can both succeed while
    real Android links still use Termux's compiler runtime or miss the NDK's
    libunwind/libatomic. This probe exercises the exact runtime/linker pieces
    CMake needs before Devtool reports the NDK as host-compatible.
    """

    compiler = ndk_clangxx(sdk_root, version)
    sysroot = compiler.parent.parent / "sysroot"
    if not compiler.is_file() or not sysroot.is_dir():
        return False
    targets = ("aarch64-none-linux-android21", "x86_64-none-linux-android21")
    try:
        with tempfile.TemporaryDirectory(prefix="devtool-ndk-link-") as temporary:
            root = Path(temporary)
            source = root / "main.cpp"
            source.write_text("int main() { return 0; }\n", encoding="utf-8")
            for index, target in enumerate(targets):
                result = subprocess.run(
                    [
                        str(compiler),
                        f"--target={target}",
                        f"--sysroot={sysroot}",
                        "-static-libstdc++",
                        "-Wl,--no-undefined",
                        str(source),
                        "-o",
                        str(root / f"probe-{index}"),
                        "-latomic",
                        "-lm",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    return False
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def ndk_host_resource_dir_ready(sdk_root: Path, version: str) -> bool:
    """Return whether clang resolves its runtime resource dir inside the NDK.

    QEMU's ``-0`` controls guest argv[0].  Passing only ``clang++`` preserves
    driver mode, but it drops the absolute NDK installation path that Clang
    uses to locate ``../lib/clang/<version>``.  On Termux that can make the NDK
    compiler silently use ``$PREFIX/lib/clang`` and then fail to find Android
    compiler-rt, libunwind, or libatomic at link time.
    """

    compiler = ndk_clangxx(sdk_root, version)
    if not compiler.is_file():
        return False
    try:
        result = subprocess.run(
            [str(compiler), "-print-resource-dir"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return False
    resource = Path(lines[-1]).expanduser()
    if not resource.is_absolute() or not resource.is_dir():
        return False
    ndk_root = ndk_root_for_version(sdk_root, version)
    try:
        resource.resolve(strict=True).relative_to(ndk_root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def ensure_ndk_termux_multicall_aliases(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    """Materialize executable NDK bin symlinks as hardlinks on native Termux.

    Termux's exec interposition can resolve an executable symlink before the
    target program observes argv[0].  LLVM deliberately uses argv[0] for
    multi-call entry points such as clang++/clang and ld.lld/lld.  Replacing
    same-directory executable symlinks with hardlinks preserves the visible
    entry-point name without duplicating the large LLVM binaries.
    """

    if not running_under_termux_python():
        return True
    bin_dir = ndk_google_tool_bin(sdk_root, version)
    if not bin_dir.is_dir():
        return False
    try:
        resolved_bin = bin_dir.resolve(strict=True)
    except OSError:
        return False

    for alias in sorted(bin_dir.iterdir()):
        if not alias.is_symlink():
            continue
        try:
            target = alias.resolve(strict=True)
            if target.parent.resolve(strict=True) != resolved_bin or not target.is_file():
                continue
            if not os.access(target, os.X_OK):
                continue
        except OSError:
            continue

        backup = alias.with_name(alias.name + ".devtool-symlink")
        if dry_run:
            info(f"Would materialize NDK multi-call alias as hardlink: {alias} -> {target}")
            continue
        try:
            if not backup.exists() and not backup.is_symlink():
                alias.rename(backup)
            else:
                alias.unlink(missing_ok=True)
            temporary = alias.with_name(alias.name + f".devtool-{os.getpid()}.tmp")
            temporary.unlink(missing_ok=True)
            os.link(target, temporary)
            os.replace(temporary, alias)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            if not alias.exists() and backup.is_symlink():
                try:
                    backup.rename(alias)
                except OSError:
                    pass
            return False
        info(f"Materialized NDK multi-call alias as hardlink: {alias} -> {target}")

    return True


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


def finalize_arm_ndk_host_layout(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    """Finish every compatibility view required by ARM-hosted Android builds.

    A native ARM NDK may already expose its runnable prebuilt through the
    canonical ``linux-x86_64`` name.  That does not mean CMake on Termux can
    discover it: older CMake can request ``linux-x86`` and the NDK toolchain
    can leave ``ANDROID_HOST_TAG`` empty on an Android host.  Finalization must
    therefore run even when the canonical alias itself needed no changes.
    """

    if not ensure_ndk_termux_multicall_aliases(sdk_root, version, dry_run=dry_run):
        return False
    if not ensure_ndk_legacy_cmake_arm_alias(sdk_root, version, dry_run=dry_run):
        return False
    if not ensure_ndk_cmake_termux_host_support(sdk_root, version, dry_run=dry_run):
        return False
    if not ensure_ndk_empty_host_tag_compat(sdk_root, version, dry_run=dry_run):
        return False
    return dry_run or (
        ndk_host_toolchain_runs(sdk_root, version)
        and ndk_host_linker_runs(sdk_root, version)
    )


def restore_google_ndk_x86_64_prebuilt_for_qemu(
    sdk_root: Path, version: str, *, dry_run: bool
) -> bool:
    """Restore the pristine Google linux-x86_64 prebuilt before QEMU wrapping.

    Native ARM probing temporarily exposes an ARM-host prebuilt through the
    canonical ``linux-x86_64`` path so AGP/CMake can discover it.  If that
    candidate later fails the real Android link probes, QEMU fallback must not
    wrap that mixed/native view: the x86_64 compiler, resource directory,
    sysroot, and runtime libraries must all come from the same original Google
    host prebuilt.  ``expose_arm64_ndk_as_linux_x86_64`` preserves that tree as
    ``linux-x86_64.google-x86_64``; restore it atomically here.
    """

    ndk_root = ndk_root_for_version(sdk_root, version)
    prebuilt_root = ndk_prebuilt_root(ndk_root)
    google_tag = prebuilt_root / ndk_google_host_tag()
    backup = google_tag.with_name(google_tag.name + ".google-x86_64")
    if not backup.exists() or not backup.is_dir():
        return True

    # A pristine backup is only useful when it actually contains the expected
    # x86_64 Google host compiler.  Refuse to replace the active tree with an
    # unrelated or already-damaged directory.
    backup_clang = backup / "bin" / "clang++"
    candidate = x86_64_binary_candidate(backup_clang)
    if candidate is None and native_tool_arch(backup_clang) != "x86_64":
        warn(f"Ignoring non-x86_64 Google NDK backup during QEMU fallback: {backup}")
        return False

    if dry_run:
        info(f"Would restore pristine Google NDK host prebuilt for QEMU: {backup} -> {google_tag}")
        return True

    displaced = google_tag.with_name(google_tag.name + ".devtool-arm-host")
    try:
        if displaced.exists() or displaced.is_symlink():
            if displaced.is_symlink() or displaced.is_file():
                displaced.unlink(missing_ok=True)
            else:
                shutil.rmtree(displaced)
        if google_tag.exists() or google_tag.is_symlink():
            google_tag.rename(displaced)
        backup.rename(google_tag)
    except OSError as exc:
        # Best-effort rollback: never leave the canonical host tag absent.
        try:
            if not google_tag.exists() and not google_tag.is_symlink() and displaced.exists():
                displaced.rename(google_tag)
        except OSError:
            pass
        warn(f"Could not restore pristine Google NDK host prebuilt for QEMU: {exc}")
        return False

    info(f"Restored pristine Google NDK host prebuilt for QEMU: {google_tag}")
    return True


def expose_arm64_ndk_as_linux_x86_64(sdk_root: Path, version: str, *, dry_run: bool) -> bool:
    ndk_root = ndk_root_for_version(sdk_root, version)
    arm_prebuilt = find_arm64_ndk_prebuilt(ndk_root)
    if arm_prebuilt is None:
        return False
    google_tag = ndk_prebuilt_root(ndk_root) / ndk_google_host_tag()
    try:
        already_exposed = google_tag.resolve(strict=True) == arm_prebuilt.resolve(strict=True)
    except OSError:
        already_exposed = False
    if already_exposed:
        return finalize_arm_ndk_host_layout(sdk_root, version, dry_run=dry_run)
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
    return finalize_arm_ndk_host_layout(sdk_root, version, dry_run=dry_run)


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


def qemu_sysroot_cache_dir(cache_root: Path, url: str, expected_sha256: str | None = None) -> Path:
    stem = Path(urllib.parse.urlparse(url).path).name or "qemu-x86_64-sysroot.tar.gz"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem)
    safe = safe.replace(".tar.gz", "").replace(".tgz", "")
    identity = hashlib.sha256(f"{url}\n{expected_sha256 or ''}".encode("utf-8")).hexdigest()[:16]
    return cache_root / "qemu" / "x86_64-sysroot" / f"{safe}-{identity}"


def _qemu_sysroot_identity_file(prefix: Path) -> Path:
    return prefix / ".devtool-qemu-sysroot.json"


def _qemu_sysroot_identity_matches(prefix: Path, *, url: str, expected_sha256: str) -> bool:
    marker = _qemu_sysroot_identity_file(prefix)
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        str(payload.get("url") or "") == url
        and str(payload.get("sha256") or "").lower() == expected_sha256.lower()
    )


def _write_qemu_sysroot_identity(prefix: Path, *, url: str, expected_sha256: str) -> None:
    payload = {
        "schema_version": 1,
        "url": url,
        "sha256": expected_sha256.lower(),
        "purpose": "qemu-x86_64-loader-prefix",
    }
    _qemu_sysroot_identity_file(prefix).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    target = qemu_sysroot_cache_dir(cache_root, selected_url, selected_sha)
    if (
        qemu_prefix_has_x86_64_loader(target)
        and _qemu_sysroot_identity_matches(target, url=selected_url, expected_sha256=selected_sha)
        and not force
    ):
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
    extract_archive(archive, staging, rootfs_links=True)
    if not qemu_prefix_has_x86_64_loader(staging):
        shutil.rmtree(staging, ignore_errors=True)
        fail(
            "Downloaded QEMU x86_64 sysroot does not contain ld-linux-x86-64.so.2.\n"
            f"URL: {selected_url}\n"
            "Set --qemu-sysroot-url to a tar/zip rootfs containing an amd64 loader/libc."
        )
    _write_qemu_sysroot_identity(staging, url=selected_url, expected_sha256=selected_sha)
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


def qemu_wrapper_real_binary(path: Path) -> Path | None:
    """Recover the preserved x86_64 ELF referenced by a Devtool wrapper."""

    if not wrapper_mentions_devtool(path):
        return None
    import shlex

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.startswith("exec "):
            continue
        try:
            tokens = shlex.split(line[5:])
        except ValueError:
            continue
        for token in tokens:
            candidate = Path(token)
            if candidate.name.endswith(".devtool-x86_64") and native_tool_arch(candidate) == "x86_64":
                return candidate
    return None


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

    wrapped_real = qemu_wrapper_real_binary(path)
    if wrapped_real is not None:
        return wrapped_real

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


def write_qemu_wrapper(
    wrapper: Path,
    real_binary: Path,
    qemu: Path,
    ld_prefix: Path | None,
    *,
    dry_run: bool,
    clang_resource_dir: Path | None = None,
) -> None:
    import shlex

    qemu_parts = [shlex.quote(str(qemu))]
    if ld_prefix is not None:
        qemu_parts.extend(["-L", shlex.quote(str(ld_prefix))])
    # Do not pass Termux's host preload into the Linux/x86_64 guest.  The
    # preload object is built for the Android host and guest ld.so cannot use
    # it.  qemu-user's -U removes it from the target environment.
    qemu_parts.extend(["-U", "LD_PRELOAD"])
    # qemu-user's -0 option preserves the visible NDK entry-point name for
    # multi-call LLVM binaries (clang++/clang, ld.lld/lld, llvm-ranlib, ...).
    qemu_parts.extend(["-0", shlex.quote(str(wrapper))])
    guest_args: list[str] = []
    if clang_resource_dir is not None and wrapper.name in {"clang", "clang++"}:
        # Force the NDK's compiler runtime location. Under qemu-user Clang can
        # otherwise derive ResourceDir from the preserved backing ELF or host
        # installation and silently fall back to Termux's $PREFIX/lib/clang.
        guest_args.extend(["-resource-dir", shlex.quote(str(clang_resource_dir))])
    tail = " ".join(guest_args)
    if tail:
        tail += " "
    script = (
        "#!/bin/sh\nexec "
        + " ".join(qemu_parts)
        + " "
        + shlex.quote(str(real_binary))
        + " "
        + tail
        + '"$@"\n'
    )
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
    if (
        ndk_host_toolchain_runs(sdk_root, version)
        and ndk_host_linker_runs(sdk_root, version)
        and ndk_host_resource_dir_ready(sdk_root, version)
        and ndk_host_android_link_runs(sdk_root, version)
    ):
        return True
    if normalized_machine() not in {"arm64", "armv7"}:
        return False
    if ndk_x86_64_real_binary(clang) is None:
        info(
            f"QEMU NDK fallback is unavailable for {version}: {clang} has no preserved "
            "x86_64 Google compiler backing binary to wrap."
        )
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
    resource_dir = ndk_clang_resource_dir(sdk_root, version)
    if resource_dir is None:
        fail(
            f"Android NDK {version} does not contain a usable Clang resource directory under "
            f"{bin_dir.parent}. The NDK package is incomplete or malformed."
        )

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
        write_qemu_wrapper(
            path,
            real,
            qemu,
            ld_prefix,
            dry_run=dry_run,
            clang_resource_dir=resource_dir,
        )
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
    if not ndk_host_linker_runs(sdk_root, version):
        fail(
            "QEMU NDK wrappers can execute clang++, but the Android ELF linker entry point "
            "still does not select ld.lld mode. Devtool preserved argv[0] for LLVM multi-call "
            "tools, but ld.lld --version still failed."
        )
    if not ndk_host_resource_dir_ready(sdk_root, version):
        fail(
            "QEMU NDK wrappers can execute clang++ and ld.lld, but clang resolves its runtime "
            "resource directory outside the NDK."
        )
    if not ndk_host_android_link_runs(sdk_root, version):
        fail(
            "QEMU NDK wrappers can execute clang++ and ld.lld, but the compiler cannot link "
            "minimal Android arm64/x86_64 programs with the NDK compiler-rt/libunwind/libatomic "
            "runtime set. Devtool forced the NDK Clang resource directory, so this usually means "
            "the NDK package itself is incomplete."
        )
    if not ensure_ndk_legacy_cmake_arm_alias(sdk_root, version, dry_run=dry_run):
        fail(
            "QEMU NDK wrapper is executable, but Devtool could not expose the legacy linux-x86 "
            "host tag required by ARM-hosted CMake discovery."
        )
    if not ensure_ndk_cmake_termux_host_support(sdk_root, version, dry_run=dry_run):
        fail(
            "QEMU NDK wrapper is executable, but Devtool could not enable native Termux/Android "
            "host detection in the NDK CMake toolchain."
        )
    if not ensure_ndk_empty_host_tag_compat(sdk_root, version, dry_run=dry_run):
        fail(
            "QEMU NDK wrapper is executable, but Devtool could not expose the empty host-tag "
            "compatibility paths used by native Termux CMake."
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
        # Exposing or installing a native ARM prebuilt only proves that its host
        # executables can be reached under the canonical linux-x86_64 layout.
        # Do not return here: the NDK still has to pass the linker, resource-dir,
        # and real Android runtime-link probes below.  Returning early used to
        # let a Termux-host clang++ that sourced $PREFIX/lib/clang reach CMake
        # even though it could not link a trivial Android executable.
        native_candidate = expose_arm64_ndk_as_linux_x86_64(
            sdk_root, version, dry_run=dry_run
        )
        configured_url = arm64_ndk_url or os.environ.get("DEVTOOL_ARM64_NDK_URL", "").strip()
        if not native_candidate and configured_url:
            native_candidate = obtain_unofficial_arm64_ndk(
                sdk_root,
                version,
                cache_root,
                url=configured_url,
                offline=offline,
                force=force,
                dry_run=dry_run,
            )
        if provider == "arm64" and not native_candidate:
            fail(
                "An ARM64-host NDK was requested, but no runnable ARM64 prebuilt was found.\n"
                "Set --arm64-ndk-url or DEVTOOL_ARM64_NDK_URL to a trusted unofficial NDK archive."
            )

    if ndk_host_toolchain_runs(sdk_root, version):
        if machine in {"arm64", "armv7"} and not ensure_ndk_termux_multicall_aliases(
            sdk_root, version, dry_run=dry_run
        ):
            fail(
                "NDK host compiler is executable, but Devtool could not preserve LLVM "
                "multi-call tool identities for native Termux execution."
            )
        if machine in {"arm64", "armv7"} and not ensure_ndk_legacy_cmake_arm_alias(
            sdk_root, version, dry_run=dry_run
        ):
            fail(
                "NDK host compiler is executable, but the NDK cannot be exposed under the "
                "legacy linux-x86 tag used by ARM-hosted CMake."
            )
        if machine in {"arm64", "armv7"} and not ensure_ndk_cmake_termux_host_support(
            sdk_root, version, dry_run=dry_run
        ):
            fail(
                "NDK host compiler is executable, but Devtool could not enable native "
                "Termux/Android host detection in the NDK CMake toolchain."
            )
        if machine in {"arm64", "armv7"} and not ensure_ndk_empty_host_tag_compat(
            sdk_root, version, dry_run=dry_run
        ):
            fail(
                "NDK host compiler is executable, but Devtool could not expose the empty "
                "host-tag compatibility paths used by native Termux CMake."
            )
        linker_ready = ndk_host_linker_runs(sdk_root, version)
        resource_ready = ndk_host_resource_dir_ready(sdk_root, version)
        link_ready = ndk_host_android_link_runs(sdk_root, version) if linker_ready and resource_ready else False
        if linker_ready and resource_ready and link_ready:
            info(f"NDK host toolchain executable and Android link-ready: {version}")
            return
        if provider not in {"auto", "qemu"}:
            problems: list[str] = []
            if not linker_ready:
                problems.append("ld.lld does not select a usable ELF linker")
            if not resource_ready:
                problems.append("clang resource directory resolves outside the NDK")
            elif not link_ready:
                problems.append("Android compiler runtime link probe fails")
            fail(f"NDK {version} clang++ executes, but " + " and ".join(problems) + ".")
        problems: list[str] = []
        if not linker_ready:
            problems.append("ld.lld is not usable")
        if not resource_ready:
            problems.append("clang resource directory escapes the NDK")
        elif not link_ready:
            problems.append("Android compiler runtime link probe fails")
        if provider == "auto" and linker_ready and resource_ready and not link_ready:
            info(
                f"NDK {version} compiler, linker, and resource directory are usable, but the "
                "synthetic Android runtime link probe did not pass. Auto mode keeps the native "
                "NDK and defers final compatibility to the real Gradle/CMake build instead of "
                "forcing QEMU."
            )
            return
        warn(
            f"NDK {version} compiler executes but {' and '.join(problems)}; rebuilding QEMU "
            "wrappers with preserved LLVM argv[0] identity and installation path."
        )
    if machine not in {"arm64", "armv7"}:
        fail(f"NDK {version} is installed but clang++ cannot run on this host: {ndk_clangxx(sdk_root, version)}")
    if provider == "none":
        warn(f"NDK host compatibility repair disabled; native Gradle tasks may fail: {ndk_clangxx(sdk_root, version)}")
        return
    if provider in {"auto", "qemu"}:
        if not restore_google_ndk_x86_64_prebuilt_for_qemu(
            sdk_root, version, dry_run=dry_run
        ):
            if provider == "qemu":
                fail(
                    "QEMU NDK fallback was requested, but Devtool could not restore the "
                    "pristine Google linux-x86_64 NDK host prebuilt after native ARM probing."
                )
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
            clang = ndk_clangxx(sdk_root, version)
            if ndk_x86_64_real_binary(clang) is None:
                fail(
                    "QEMU NDK fallback was requested, but the active NDK has no preserved "
                    "x86_64 Google host compiler to wrap. This usually means a native ARM-host "
                    "NDK replaced the canonical linux-x86_64 prebuilt without retaining the "
                    "original Google prebuilt. Use --ndk-host-provider auto to keep the working "
                    "native NDK, or restore/reinstall the official Google NDK before forcing QEMU."
                )
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
        f"NDK {version} host toolchain is still not fully usable on {machine}. "
        "Native/CMake Gradle tasks may fail. Configure [android].ndk_host_provider='qemu' "
        "or set [android].arm64_ndk_url to a trusted ARM64 NDK archive."
    )


def commit451_release_available(
    cache_root: Path,
    release_version: str,
    *,
    offline: bool,
) -> bool:
    """Resolve whether Commit451 publishes the requested build-tools release."""

    tag = f"platform-tools-{release_version}"
    release_dir = cache_root / "android-build-tools" / "commit451" / tag
    if (release_dir / "SHA256SUMS").is_file() or release_version in ARM_BUILD_TOOLS_PUBLISHED_RELEASES:
        return True
    if offline:
        return False
    url = f"https://api.github.com/repos/{ARM_BUILD_TOOLS_REPOSITORY}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{APP_NAME}/1.0",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            if exc.code == 404:
                return False
            fail(f"Could not resolve Commit451 release availability: {url}\n{exc}")
        finally:
            # HTTPError owns the underlying response stream.  Close it even for
            # the expected 404 probe path so repeated release checks do not leak
            # descriptors or emit ResourceWarning under newer Python versions.
            exc.close()
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        fail(f"Could not resolve Commit451 release availability: {url}\n{exc}")
    return str(payload.get("tag_name") or "") == tag


def resolve_commit451_release_version(
    cache_root: Path,
    build_tools_version: str,
    *,
    offline: bool,
) -> str:
    release_version = ARM_BUILD_TOOLS_RELEASE_ALIASES.get(build_tools_version, build_tools_version)
    if release_version != build_tools_version:
        warn(
            "Commit451 does not publish ARM64 binaries for Android Build Tools "
            f"{build_tools_version}; using the compatible {release_version} release."
        )
    if commit451_release_available(cache_root, release_version, offline=offline):
        return release_version
    if offline:
        fail(
            f"Commit451 release platform-tools-{release_version} is not cached and availability "
            "cannot be resolved in --offline mode."
        )
    fail(
        f"Commit451 does not publish ARM64 Android Build Tools release platform-tools-{release_version}. "
        "Select a supported build-tools version, use the Termux-native provider, or use another execution backend."
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
    """Download and verify one Commit451 linux-glibc-arm64 build-tools executable."""

    if tool_name not in ARM_BUILD_TOOLS_NATIVE_NAMES:
        fail(f"Unsupported ARM build-tools executable: {tool_name}")
    if normalized_machine() != "arm64":
        fail("Commit451 Android build-tools are only used on ARM64 Linux hosts")
    if running_under_termux_python():
        fail(
            "Commit451 Android build-tools target linux-glibc-arm64 and cannot run in native "
            "Termux/Bionic. Use the Termux-native provider or a glibc chroot/PRoot/SSH backend."
        )

    release_version = resolve_commit451_release_version(
        cache_root, build_tools_version, offline=offline
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
        if not native_android_build_tool_compatible(directory / name, name, "arm64")
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
    provider_evidence=None,
) -> None:
    """Repair x86-only AIDL/zipalign/split-select in an ARM64 Android SDK."""

    if normalized_machine() != "arm64" or not build_tools_version:
        return
    provider = normalize_android_build_tools_provider(provider)
    if provider == "none":
        return

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

    if provider == "sdk":
        fail(
            "Android SDK build-tools provider was requested, but the selected SDK contains "
            "native tools that are incompatible with this ARM64 host: " + ", ".join(replacements)
        )

    heading(f"Repairing Android Build Tools {build_tools_version} for ARM64")
    selected = list(ARM_BUILD_TOOLS_NATIVE_NAMES) if force else replacements
    sources: dict[str, Path] = {}
    for tool_name in selected:
        source: Path | None = None
        if provider == "auto":
            donor = compatible_build_tools_donor(
                sdk_root,
                build_tools_version,
                tool_name,
                machine="arm64",
            )
            if donor is not None:
                source = donor
                info(
                    f"Using SDK-native {tool_name} donor from Build Tools "
                    f"{donor.parent.name}: {donor}"
                )
        if source is None and provider in {"auto", "termux"} and running_under_termux_python():
            if provider_evidence is not None:
                decision = next((item for item in provider_evidence.android_tools if item.capability == tool_name), None)
                candidate = decision.path if decision is not None and decision.compatible else None
            else:
                candidate = termux_tool_path(tool_name)
            if candidate is not None and native_android_build_tool_compatible(candidate, tool_name, "arm64"):
                source = candidate
                info(f"Using Termux-native {tool_name}: {candidate}")
            elif provider == "termux":
                fail(f"Termux build-tools provider requested but compatible {tool_name} is unavailable under {termux_prefix_path()}")
        if source is None:
            if provider == "termux":
                fail(f"Termux build-tools provider requested but no compatible {tool_name} was found")
            if running_under_termux_python():
                fail(
                    f"No compatible Termux-native {tool_name} is available. Commit451 linux-glibc-arm64 helpers "
                    "cannot run under native Termux/Bionic; install/update the required Termux Android tool package (for example `pkg install aidl`) or use a compatible Linux backend."
                )
            source = obtain_commit451_arm64_tool(
                cache_root, build_tools_version, tool_name, offline=offline, force=force, dry_run=dry_run,
            )
        sources[tool_name] = source

    if dry_run:
        for tool_name, source in sources.items():
            info(f"Would install ARM64 {tool_name}: {source} -> {directory / tool_name}")
        return

    # Stage and validate every replacement before touching the SDK. A broken
    # second helper therefore cannot leave the first helper half-installed.
    stage_dir = directory / f".devtool-build-tools-stage-{os.getpid()}"
    backup_dir = directory / f".devtool-build-tools-backup-{os.getpid()}"
    shutil.rmtree(stage_dir, ignore_errors=True)
    shutil.rmtree(backup_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=False)
    backup_dir.mkdir(parents=True, exist_ok=False)
    try:
        for tool_name, source in sources.items():
            staged = stage_dir / tool_name
            shutil.copy2(source, staged)
            staged.chmod(staged.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if not native_android_build_tool_compatible(staged, tool_name, "arm64"):
                fail(f"Staged ARM64 {tool_name} failed host/runtime validation: {staged}")

        installed: set[str] = set()
        originally_present: set[str] = set()
        try:
            for tool_name in selected:
                target = directory / tool_name
                backup = backup_dir / tool_name
                if target.exists() or target.is_symlink():
                    target.replace(backup)
                    # Record the backup move immediately.  If the following
                    # stage->target replace fails, this tool still requires
                    # rollback even though it was never fully installed.
                    originally_present.add(tool_name)
                (stage_dir / tool_name).replace(target)
                installed.add(tool_name)
            for tool_name in selected:
                target = directory / tool_name
                if not native_android_build_tool_compatible(target, tool_name, "arm64"):
                    fail(f"Installed ARM64 {tool_name} failed host/runtime validation: {target}")
                info(f"Installed ARM64 {tool_name}: {target}")
        except BaseException:
            # Restore every selected helper from the transaction's observable
            # state, not only helpers that reached the final installed list.
            for tool_name in reversed(selected):
                target = directory / tool_name
                backup = backup_dir / tool_name
                if tool_name in installed and (target.exists() or target.is_symlink()):
                    target.unlink()
                if tool_name in originally_present and (backup.exists() or backup.is_symlink()):
                    if target.exists() or target.is_symlink():
                        target.unlink()
                    backup.replace(target)
                elif tool_name in installed and tool_name not in originally_present:
                    target.unlink(missing_ok=True)
            raise
        else:
            # Persistent recovery copies are ancillary. Create them only after
            # the whole helper set is installed and verified; a backup-copy
            # failure must neither corrupt nor roll back a known-good SDK set.
            for tool_name in selected:
                transient_backup = backup_dir / tool_name
                persistent_backup = (directory / tool_name).with_name(tool_name + ".google-x86_64")
                if transient_backup.exists() and not persistent_backup.exists():
                    try:
                        if transient_backup.is_symlink():
                            persistent_backup.symlink_to(os.readlink(transient_backup))
                        else:
                            shutil.copy2(transient_backup, persistent_backup)
                    except OSError as exc:
                        warn(f"Could not preserve original {tool_name} helper backup at {persistent_backup}: {exc}")
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)


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
    agp_version: str | None = None


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


def _version_catalog_alias_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def version_catalog_versions(root: Path) -> dict[str, int]:
    """Return integer version aliases from ``gradle/libs.versions.toml``.

    Android projects commonly keep SDK levels in a Gradle version catalog and
    reference them as ``libs.versions.android.compileSdk.get().toInt()``.  The
    Android builder runs before Gradle, so it needs a conservative static view
    of those aliases when deciding which SDK platform to provision.
    """

    catalog = root / "gradle" / "libs.versions.toml"
    if not catalog.is_file():
        return {}
    try:
        import tomllib
        data = tomllib.loads(catalog.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, dict):
        return {}
    resolved: dict[str, int] = {}
    for alias, raw_value in versions.items():
        value: object = raw_value
        if isinstance(raw_value, dict):
            for key in ("strictly", "require", "prefer"):
                candidate = raw_value.get(key)
                if candidate is not None:
                    value = candidate
                    break
        match = re.fullmatch(r"\s*(\d+)\s*", str(value))
        if not match:
            continue
        resolved[_version_catalog_alias_key(str(alias))] = int(match.group(1))
    return resolved


def resolve_catalog_int_reference(root: Path, text: str, setting_name: str) -> int | None:
    versions = version_catalog_versions(root)
    if not versions:
        return None
    patterns = (
        rf"\b{re.escape(setting_name)}(?:Version)?\s*(?:=|\s)\s*libs\.versions\.([A-Za-z0-9_.-]+)\.get\(\)\.toInt\(\)",
        rf"\b{re.escape(setting_name)}(?:Version)?\s*(?:=|\s)\s*libs\.versions\.([A-Za-z0-9_.-]+)\.get\(\)\.toInteger\(\)",
        rf"\b{re.escape(setting_name)}\s*\(\s*libs\.versions\.([A-Za-z0-9_.-]+)\.get\(\)\.toInt\(\)\s*\)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            continue
        key = _version_catalog_alias_key(match.group(1))
        if key in versions:
            return versions[key]
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
    toolchains = _termux_toolchains_runtime_module()
    active_gradle_text = toolchains.active_gradle_config_text(root)
    name = project_display_name(root, text)
    android_application = bool(re.search(r"com\.android\.application|id\s*\(?\s*['\"]com\.android\.application", text))
    android_library = bool(re.search(r"com\.android\.library|id\s*\(?\s*['\"]com\.android\.library", text))
    android = android_application or android_library or any(root.rglob("src/main/AndroidManifest.xml"))
    compile_sdk = first_int(
        [
            r"\bcompileSdk(?:Version)?\s*(?:=|\s)\s*['\"]?(\d+)",
            r"\bcompileSdk\s*\(\s*(\d+)\s*\)",
        ],
        active_gradle_text,
    )
    if compile_sdk is None:
        reference = re.search(
            r"\bcompileSdk(?:Version)?\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\b",
            active_gradle_text,
        )
        if reference:
            alias = re.escape(reference.group(1))
            compile_sdk = first_int(
                [rf"\b(?:val|var)\s+{alias}\s*(?::\s*[A-Za-z0-9_.<>?]+)?\s*=\s*(\d+)"],
                active_gradle_text,
            )
    if compile_sdk is None:
        compile_sdk = resolve_catalog_int_reference(root, active_gradle_text, "compileSdk")
    build_tools_match = re.search(
        r"\bbuildToolsVersion\s*(?:=|\s)\s*['\"]([^'\"]+)",
        active_gradle_text,
    )
    build_tools = build_tools_match.group(1) if build_tools_match else None
    agp_version = None
    for pattern in (
        r"com\.android\.tools\.build:gradle:([0-9]+(?:\.[0-9]+){1,2})",
        r"com\.android\.(?:application|library)[^\n]*?version\s*(?:=\s*)?['\"]([0-9]+(?:\.[0-9]+){1,2})",
        r"\bagp\s*=\s*['\"]([0-9]+(?:\.[0-9]+){1,2})",
        r"androidGradlePlugin\s*=\s*['\"]([0-9]+(?:\.[0-9]+){1,2})",
    ):
        match = re.search(pattern, text)
        if match:
            agp_version = match.group(1)
            break
    agp_major = int(agp_version.split(".", 1)[0]) if agp_version else first_int(
        [r"com\.android\.tools\.build:gradle:([0-9]+)"], text
    )
    # Build-time NDK/CMake classification uses the same active Gradle view
    # as SDK inspection so comments and backup build files cannot affect it.
    ndk_match = re.search(r"\bndkVersion\s*(?:=|\s)\s*['\"]([^'\"]+)", active_gradle_text)
    ndk_version = ndk_match.group(1).strip() if ndk_match else None
    native_build = toolchains.classify_native_requirement(root, android=android).capability == "android-ndk"
    cmake_match = re.search(
        r"externalNativeBuild\s*\{.*?cmake\s*\{.*?\bversion\s*=\s*['\"]([^'\"]+)",
        active_gradle_text,
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
        agp_version,
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


def java_home_candidates(explicit: Path | None, provider: str = "auto", *, provider_evidence=None) -> list[Path]:
    provider = normalize_java_provider(provider)
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    if os.environ.get("JAVA_HOME"):
        candidates.append(Path(os.environ["JAVA_HOME"]).expanduser())
    for executable in ("javac", "java"):
        found = shutil.which(executable)
        if found:
            candidates.append(Path(found).resolve().parent.parent)
    if provider_evidence is not None and running_under_termux_python():
        candidates.extend(
            item.path for item in provider_evidence.java
            if item.compatible and item.path is not None
        )
    else:
        candidates.extend(termux_jvm_candidates())
    jvm_root = Path("/usr/lib/jvm")
    if jvm_root.is_dir():
        candidates.extend(path for path in jvm_root.iterdir() if path.is_dir())

    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        resolved = item.resolve(strict=False)
        if str(resolved) in seen:
            continue
        seen.add(str(resolved))
        if explicit is None and provider != "auto":
            candidate_provider = executable_provider(resolved / "bin" / "java")
            if candidate_provider != provider:
                continue
        unique.append(resolved)
    return unique


def find_java_home(
    explicit: Path | None,
    minimum: int,
    *,
    exact: int | None = None,
    provider: str = "auto",
    provider_evidence=None,
) -> Path:
    provider = normalize_java_provider(provider)
    if explicit is not None:
        home = explicit.expanduser().resolve(strict=False)
        java = home / "bin" / "java"
        javac = home / "bin" / "javac"
        if not java.is_file() or not javac.is_file():
            fail(f"Explicit Java home is incomplete: {home}. Expected bin/java and bin/javac.")
        major = java_major(java)
        if major is None:
            fail(f"Could not determine Java version from explicit Java home: {home}")
        if major < minimum:
            fail(f"Explicit Java home {home} provides Java {major}, but Java {minimum}+ is required.")
        if exact is not None and major != exact:
            fail(f"Explicit Java home {home} provides Java {major}, but exact Java {exact} is required.")
        info(f"Using explicit Java home: {home} (Java {major}); provider={executable_provider(java)}")
        return home
    if provider == "termux" and not running_under_termux_python():
        fail("Java provider termux requires native Termux execution or an explicit --java-home.")
    rejected: list[str] = []
    accepted: list[tuple[int, int, int, Path]] = []
    for home in java_home_candidates(None, provider, provider_evidence=provider_evidence):
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
        # On native Termux, prefer a compatible JDK from PREFIX over a foreign
        # system tree. Within the same provider, keep the narrowest compatible
        # Java version; exact project toolchains still win first.
        selected_provider = executable_provider(java)
        provider_rank = 0 if provider == "auto" and running_under_termux_python() and selected_provider == "termux" else 1
        accepted.append((0 if exact is not None and major == exact else 1, provider_rank, major, home))

    if accepted:
        accepted.sort(key=lambda item: (item[0], item[1], item[2], str(item[3])))
        return accepted[0][3]
    details = f" Candidates checked: {', '.join(rejected)}." if rejected else ""
    if exact is not None:
        fail(
            f"A JDK with Java {exact} is required for this project's declared toolchain "
            f"using Java provider {provider}.{details} Use --java-home or --java-provider to select one."
        )
    fail(
        f"A JDK with Java {minimum}+ is required using Java provider {provider}.{details} "
        "Use --java-home or --java-provider to select one."
    )


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
    # The remaining SDK helpers must both match the host architecture and
    # survive a runtime probe. This catches arm64/glibc helpers on arm64/Bionic,
    # which static ELF inspection alone cannot distinguish.
    for version in build_tools[:2]:
        directory = sdk_root / "build-tools" / version
        for name in ARM_BUILD_TOOLS_NATIVE_NAMES:
            candidate = directory / name
            if not candidate.is_file():
                continue
            if not native_android_build_tool_compatible(candidate, name, machine):
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


def termux_can_repair_sdk_build_tools(sdk_root: Path) -> bool:
    """Return whether native Termux can repair all incompatible ARM build tools.

    On ARM64 Termux an SDK may look repairable merely because it is writable,
    while one or more required helpers (most commonly ``zipalign`` or
    ``split-select``) are unavailable as Bionic-native executables. In that
    case ``sdk_provider=auto`` should prefer a host-compatible external SDK
    rather than selecting the SDK and failing later in the repair phase.
    """

    if not running_under_termux_python() or normalized_machine() != "arm64":
        return True
    versions = installed_build_tools_versions(sdk_root)
    if not versions:
        return True
    for version in versions[:2]:
        for tool_name in arm64_build_tools_replacements_needed(sdk_root, version):
            candidate = termux_tool_path(tool_name)
            if candidate is None or not native_android_build_tool_compatible(
                candidate, tool_name, "arm64"
            ):
                return False
    return True


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
    if running_under_termux_python():
        candidates.append((_termux_prefix_path() / "opt" / "android-sdk", "TermuxVoid android-sdk", False))
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


def canonical_build_tools_package_id(value: str) -> str | None:
    """Return the canonical SDK Build Tools package-id directory name.

    Android package IDs use X.Y.Z names (or X.Y.Z-rcN previews). Some Termux
    package metadata carries an additional packaging revision such as
    ``36.0.0-5``. That suffix must not become the SDK directory requested by
    AGP, so collapse it back to ``36.0.0``.
    """

    text = str(value or "").strip()
    if re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\d+)?", text):
        return text
    packaged = re.fullmatch(r"(\d+\.\d+\.\d+)-\d+", text)
    return packaged.group(1) if packaged else None


def _build_tools_version_key(value: str) -> tuple[int, ...]:
    return tuple(
        int(part) if part.isdigit() else 0
        for part in re.split(r"[.-]", value)
    )


def installed_build_tools_versions(sdk_root: Path) -> list[str]:
    root = sdk_root / "build-tools"
    if not root.is_dir():
        return []

    versions: set[str] = set()
    for item in root.iterdir():
        if not item.is_dir() or item.is_symlink():
            continue
        canonical = canonical_build_tools_package_id(item.name)
        if canonical is not None:
            versions.add(canonical)

    return sorted(versions, key=_build_tools_version_key, reverse=True)


def build_tools_is_ready(sdk_root: Path, version: str) -> bool:
    """Return whether an SDK Build Tools package is complete enough for AGP.

    A directory alone is not evidence that ``sdkmanager`` completed. Devtool's
    ARM64 repair path can also populate native executables into a partially
    created directory. AGP still rejects such a tree because the SDK package
    metadata and Java payload are missing. Require the canonical package
    revision plus the D8/R8 jar before treating the exact revision as present.
    Native host binaries are validated/repaired separately afterwards.
    """

    canonical = canonical_build_tools_package_id(version)
    if canonical is None:
        return False
    directory = sdk_root / "build-tools" / canonical
    if not directory.is_dir() or directory.is_symlink():
        return False

    revision = source_properties_revision(directory)
    if revision is None or canonical_build_tools_package_id(revision) != canonical:
        return False

    # Modern Android Build Tools (the versions Devtool manages) ship D8/R8 as
    # this jar. It is non-host-specific and therefore must come from a complete
    # SDK package, not from the ARM64 executable repair layer.
    if not (directory / "lib" / "d8.jar").is_file():
        return False
    return True


def select_required_build_tools_version(
    project: ProjectInfo,
    sdk_root: Path,
    override: str,
) -> str | None:
    """Resolve the Build Tools revision Gradle/AGP is expected to load.

    A provider SDK may contain a newer Build Tools directory than the revision
    expected by the project's AGP.  Treating "whatever is installed" as the
    requirement can therefore pass Devtool readiness while Gradle later aborts
    with e.g. ``Failed to find Build Tools revision 36.0.0``.
    """

    if override == "none":
        return None
    if override != "auto":
        return override
    if project.build_tools:
        return project.build_tools
    if project.compile_sdk is not None or project.agp_major is not None:
        return default_build_tools_version(project)
    installed = installed_build_tools_versions(sdk_root)
    return installed[0] if installed else default_build_tools_version(project)


def compatible_build_tools_donor(
    sdk_root: Path,
    target_version: str,
    tool_name: str,
    *,
    machine: str = "arm64",
) -> Path | None:
    """Return a host-native tool from another compatible Build Tools revision.

    External ARM64 SDKs sometimes ship a newer revision (for example 36.1.0)
    while AGP requires the exact 36.0.0 package directory.  After sdkmanager
    installs that exact package, reuse only the native executable from another
    revision in the same Build Tools major line.  The package metadata/JARs stay
    those of the exact requested revision.
    """

    target = canonical_build_tools_package_id(target_version)
    if target is None:
        return None
    target_major = target.split(".", 1)[0]
    for version in installed_build_tools_versions(sdk_root):
        if version == target or version.split(".", 1)[0] != target_major:
            continue
        candidate = sdk_root / "build-tools" / version / tool_name
        if native_android_build_tool_compatible(candidate, tool_name, machine):
            return candidate
    return None


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


def normalize_sdk_build_tools_locations(sdk_root: Path, *, dry_run: bool) -> None:
    """Repair legacy/noncanonical Build Tools directory names.

    Older Devtool versions could rename a canonical ``36.0.0`` directory to a
    package/build revision such as ``36.0.0-5`` by trusting Pkg.Revision as the
    SDK package ID. Repair those suffixed paths back to the canonical AGP path.
    """

    root = sdk_root / "build-tools"
    if not root.is_dir():
        return
    quarantine = sdk_root / ".devtool-noncanonical-build-tools"

    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or directory.is_symlink():
            continue

        # Canonical SDK package-id directories remain authoritative even when
        # source.properties carries a downstream package revision suffix.
        if re.fullmatch(r"\d+\.\d+\.\d+(?:-rc\d+)?", directory.name):
            continue

        canonical_name = canonical_build_tools_package_id(directory.name)
        if canonical_name is None:
            revision = source_properties_revision(directory)
            canonical_name = canonical_build_tools_package_id(revision or "")

        if canonical_name is not None:
            canonical = root / canonical_name
            if dry_run:
                info(f"Would normalize Android build-tools location: {directory} -> {canonical}")
                continue
            if not canonical.exists():
                directory.rename(canonical)
                info(f"Normalized Android build-tools location: {directory.name} -> {canonical_name}")
                continue

        if dry_run:
            info(f"Would quarantine duplicate noncanonical build-tools directory: {directory}")
            continue

        quarantine.mkdir(parents=True, exist_ok=True)
        destination = quarantine / directory.name
        if destination.exists():
            if destination.is_dir() and not destination.is_symlink():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        directory.rename(destination)
        info(f"Quarantined duplicate noncanonical build-tools directory: {directory.name}")


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
        install_hint = (
            "pkg install cmake ninja"
            if running_under_termux_python()
            else "sudo apt install cmake ninja-build"
        )
        fail(
            f"Android SDK CMake {version} is not executable on {machine}. "
            f"Install host-native tools first: {install_hint}"
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


def _nearest_existing_path(path: Path) -> Path:
    candidate = path.expanduser().resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def android_sdk_free_bytes(path: Path) -> int | None:
    try:
        return int(shutil.disk_usage(_nearest_existing_path(path)).free)
    except OSError:
        return None


def _path_size_bytes(path: Path) -> int:
    """Best-effort size for a file/tree without following symlink targets."""

    try:
        if path.is_symlink() or path.is_file():
            return int(path.lstat().st_size)
    except OSError:
        return 0
    if not path.is_dir():
        return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        # Symlinked directories are entries, not storage owned by this tree.
        kept_dirs: list[str] = []
        for name in dirs:
            candidate = root_path / name
            try:
                if candidate.is_symlink():
                    total += int(candidate.lstat().st_size)
                else:
                    kept_dirs.append(name)
            except OSError:
                continue
        dirs[:] = kept_dirs
        for name in files:
            candidate = root_path / name
            try:
                total += int(candidate.lstat().st_size)
            except OSError:
                continue
    return total


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _staging_is_abandoned(path: Path) -> bool:
    match = re.fullmatch(r"\.sdk-(\d+)\.tmp", path.name)
    if match and _pid_is_alive(int(match.group(1))):
        return False
    # Be conservative with unrecognized staging names that were touched very
    # recently; another Devtool process may still be using them.
    if not match:
        try:
            if time.time() - path.stat().st_mtime < 3600:
                return False
        except OSError:
            return False
    return True


def _space_candidate(path: Path, category: str, reason: str, *, priority: int) -> SpaceReclaimCandidate | None:
    if not path.exists() and not path.is_symlink():
        return None
    size = _path_size_bytes(path)
    return SpaceReclaimCandidate(
        path=Path(os.path.abspath(path.expanduser())),
        category=category,
        reason=reason,
        size_bytes=size,
        priority=priority,
    )


def _gradle_distribution_version_key(path: Path) -> tuple[int, ...]:
    raw = path.name.removeprefix("gradle-")
    values = [int(item) for item in re.findall(r"\d+", raw)]
    return tuple(values) if values else (0,)


def _managed_sdk_roots(cache_root: Path) -> list[Path]:
    roots: list[Path] = []
    for candidate in (cache_root / "android-sdk", cache_root / "android-sdk-arm64"):
        if candidate.is_dir():
            roots.append(candidate)
    providers = cache_root / "android-sdk-providers"
    if providers.is_dir():
        for provider_root in providers.iterdir():
            candidate = provider_root / "sdk"
            if candidate.is_dir():
                roots.append(candidate)
    return roots


def collect_android_space_candidates(
    cache_root: Path,
    *,
    managed_gradle_retention: int = DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION,
    include_gradle_user_cache: bool = False,
    gradle_user_home: Path | None = None,
) -> list[SpaceReclaimCandidate]:
    """Return safe, regenerable cache candidates ordered by cleanup value.

    Automatic cleanup intentionally stays inside Devtool-owned caches, plus the
    standard Android download cache. The user's Gradle cache is only included
    when explicitly requested. Active SDK/NDK/CMake trees and the extracted
    QEMU sysroot are never candidates.
    """

    cache_root = cache_root.expanduser().resolve(strict=False)
    candidates: list[SpaceReclaimCandidate] = []

    def add(path: Path, category: str, reason: str, priority: int) -> None:
        candidate = _space_candidate(path, category, reason, priority=priority)
        if candidate is not None:
            candidates.append(candidate)

    providers = cache_root / "android-sdk-providers"
    if providers.is_dir():
        for provider_root in providers.iterdir():
            if not provider_root.is_dir():
                continue
            current_sdk = _sdk_tree_candidate(provider_root / "sdk")
            current_sdk_usable = bool(current_sdk is not None and sdk_host_compatible(current_sdk))
            # Once a provider SDK is installed and host-usable, the original
            # release archive is purely a redownload cache. Keep it while the
            # SDK is absent/broken so cleanup cannot destroy the only local
            # recovery payload.
            if current_sdk_usable:
                add(
                    provider_root / "downloads",
                    "provider-download",
                    f"download archive for installed {provider_root.name} SDK",
                    10,
                )
                add(
                    provider_root / ".sdk.previous",
                    "provider-backup",
                    f"superseded {provider_root.name} SDK backup",
                    30,
                )
            for staging in provider_root.glob(".sdk-*.tmp"):
                if _staging_is_abandoned(staging):
                    add(staging, "provider-staging", "abandoned SDK provider staging", 0)

    add(cache_root / "downloads", "download-cache", "Devtool toolchain download cache", 10)
    add(cache_root / "android" / "commandlinetools-linux-latest.zip", "download-cache", "Android command-line tools archive", 10)

    qemu_root = cache_root / "qemu"
    if (qemu_root / "x86_64-sysroot").is_dir():
        add(qemu_root / "downloads", "download-cache", "QEMU sysroot source archive", 10)

    for sdk_root in _managed_sdk_roots(cache_root):
        add(sdk_root / ".devtool-old-cmdline-tools", "sdk-backup", "superseded Android command-line tools", 20)
        temp = sdk_root / ".temp"
        if temp.is_dir():
            try:
                stale = time.time() - temp.stat().st_mtime >= 3600
            except OSError:
                stale = False
            if stale:
                add(temp, "sdk-temp", "stale sdkmanager temporary files", 0)

        noncanonical = sdk_root / ".devtool-noncanonical-build-tools"
        if noncanonical.is_dir():
            for backup in noncanonical.iterdir():
                if not backup.is_dir():
                    continue
                version_match = re.match(r"(\d+(?:\.\d+)+)", backup.name)
                if version_match and build_tools_is_ready(sdk_root, version_match.group(1)):
                    add(backup, "sdk-backup", f"superseded Build Tools {version_match.group(1)} backup", 20)

    android_user_cache = Path.home() / ".android" / "cache"
    add(android_user_cache, "android-download-cache", "Android SDK download cache", 15)

    gradle_root = cache_root / "gradle"
    if gradle_root.is_dir():
        managed = [
            path for path in gradle_root.iterdir()
            if path.is_dir() and re.fullmatch(r"gradle-[0-9][0-9A-Za-z._-]*", path.name)
        ]
        managed.sort(key=_gradle_distribution_version_key, reverse=True)
        retain = max(0, int(managed_gradle_retention))
        for old in managed[retain:]:
            add(old, "managed-gradle", "old Devtool-managed Gradle distribution", 40)

    if include_gradle_user_cache:
        gradle_home = (gradle_user_home or Path.home() / ".gradle").expanduser().resolve(strict=False)
        caches = gradle_home / "caches"
        if caches.is_dir():
            versioned = [
                path for path in caches.iterdir()
                if path.is_dir() and re.fullmatch(r"\d+(?:\.\d+)+(?:[-.][0-9A-Za-z_-]+)?", path.name)
            ]
            versioned.sort(key=lambda path: tuple(int(x) for x in re.findall(r"\d+", path.name)), reverse=True)
            # Manual opt-in still keeps the newest version-specific cache.
            for old in versioned[1:]:
                add(old, "gradle-user-version-cache", "old Gradle version cache (explicit opt-in)", 60)
            add(caches / "build-cache-1", "gradle-build-cache", "Gradle local build cache (explicit opt-in)", 70)

    # Parent candidates subsume children. Keep only the highest-priority parent
    # so dry-run totals do not double-count storage.
    candidates.sort(key=lambda item: (item.priority, -item.size_bytes, str(item.path)))
    kept: list[SpaceReclaimCandidate] = []
    for candidate in candidates:
        if any(parent.path == candidate.path or parent.path in candidate.path.parents for parent in kept):
            continue
        kept.append(candidate)
    return kept


def _acquire_space_cleanup_lock(cache_root: Path) -> Path | None:
    lock = cache_root / ".space-cleanup.lock"
    cache_root.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                raw = lock.read_text(encoding="utf-8").strip()
                pid = int(raw) if raw.isdigit() else 0
            except (OSError, ValueError):
                pid = 0
            if pid and _pid_is_alive(pid):
                return None
            try:
                lock.unlink()
            except OSError:
                return None
            continue
        else:
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            return lock
    return None


def _cleanup_android_space_locked(
    cache_root: Path,
    *,
    target_free_bytes: int | None = None,
    dry_run: bool = False,
    managed_gradle_retention: int = DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION,
    include_gradle_user_cache: bool = False,
    gradle_user_home: Path | None = None,
) -> SpaceCleanupReport:
    """Prune safe caches until ``target_free_bytes`` is reached.

    ``target_free_bytes=None`` means remove every safe candidate, which powers
    the explicit ``devtool clean --space`` command. Automatic callers pass a
    target and stop as soon as enough headroom has been recovered.
    """

    cache_root = cache_root.expanduser().resolve(strict=False)
    free_before = android_sdk_free_bytes(cache_root)
    candidates = collect_android_space_candidates(
        cache_root,
        managed_gradle_retention=managed_gradle_retention,
        include_gradle_user_cache=include_gradle_user_cache,
        gradle_user_home=gradle_user_home,
    )
    removed: list[SpaceReclaimCandidate] = []
    estimated_free = free_before
    for candidate in candidates:
        if target_free_bytes is not None and estimated_free is not None and estimated_free >= target_free_bytes:
            break
        if dry_run:
            removed.append(candidate)
            if estimated_free is not None:
                estimated_free += candidate.size_bytes
            continue
        try:
            if candidate.path.is_dir() and not candidate.path.is_symlink():
                shutil.rmtree(candidate.path)
            else:
                candidate.path.unlink(missing_ok=True)
        except OSError as exc:
            warn(f"Could not reclaim {candidate.path}: {exc}")
            continue
        removed.append(candidate)
        if estimated_free is not None:
            estimated_free += candidate.size_bytes
        info(
            f"Reclaimed {format_transfer_size(candidate.size_bytes)}: "
            f"{candidate.reason} ({candidate.path})"
        )

    free_after = android_sdk_free_bytes(cache_root) if not dry_run else estimated_free
    reclaimed = sum(item.size_bytes for item in removed)
    report = SpaceCleanupReport(
        free_before=free_before,
        free_after=free_after,
        reclaimed_bytes=reclaimed,
        removed=tuple(removed),
        candidates=tuple(candidates),
        dry_run=dry_run,
    )
    return report


def cleanup_android_space(
    cache_root: Path,
    *,
    target_free_bytes: int | None = None,
    dry_run: bool = False,
    managed_gradle_retention: int = DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION,
    include_gradle_user_cache: bool = False,
    gradle_user_home: Path | None = None,
) -> SpaceCleanupReport:
    cache_root = cache_root.expanduser().resolve(strict=False)
    free_before = android_sdk_free_bytes(cache_root)
    lock = _acquire_space_cleanup_lock(cache_root)
    if lock is None:
        warn("Skipping Android space cleanup because another Devtool process is managing the shared toolchain cache")
        return SpaceCleanupReport(
            free_before=free_before,
            free_after=free_before,
            reclaimed_bytes=0,
            removed=(),
            candidates=(),
            dry_run=dry_run,
        )
    try:
        return _cleanup_android_space_locked(
            cache_root,
            target_free_bytes=target_free_bytes,
            dry_run=dry_run,
            managed_gradle_retention=managed_gradle_retention,
            include_gradle_user_cache=include_gradle_user_cache,
            gradle_user_home=gradle_user_home,
        )
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def auto_manage_android_space(
    cache_root: Path,
    *,
    policy: str = "safe",
    min_free_mb: int = DEFAULT_ANDROID_SPACE_MIN_FREE_MB,
    target_free_mb: int = DEFAULT_ANDROID_SPACE_TARGET_FREE_MB,
    managed_gradle_retention: int = DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION,
    include_gradle_user_cache: bool = False,
    gradle_user_home: Path | None = None,
    dry_run: bool = False,
    reason: str = "Android build",
) -> SpaceCleanupReport | None:
    normalized = str(policy or "safe").strip().lower()
    if normalized not in SPACE_CLEANUP_POLICIES:
        fail(
            f"Unsupported Android space cleanup policy: {policy}. "
            f"Expected one of: {', '.join(sorted(SPACE_CLEANUP_POLICIES))}"
        )
    if normalized == "off":
        return None
    minimum = max(0, int(min_free_mb)) * 1024 * 1024
    target = max(int(target_free_mb), int(min_free_mb), 0) * 1024 * 1024
    free = android_sdk_free_bytes(cache_root)
    if free is None or free >= minimum:
        return None
    warn(
        f"Low storage before {reason}: {format_transfer_size(free)} free. "
        f"Devtool will prune safe regenerable caches toward "
        f"{format_transfer_size(target)} free space."
    )
    report = cleanup_android_space(
        cache_root,
        target_free_bytes=target,
        dry_run=dry_run,
        managed_gradle_retention=managed_gradle_retention,
        include_gradle_user_cache=include_gradle_user_cache,
        gradle_user_home=gradle_user_home,
    )
    if report.removed:
        info(
            f"Storage cleanup {'would reclaim' if dry_run else 'reclaimed'} "
            f"{format_transfer_size(report.reclaimed_bytes)} across "
            f"{len(report.removed)} path(s)"
        )
    if not dry_run and report.free_after is not None and report.free_after < minimum:
        warn(
            f"Storage remains low after safe Devtool cleanup: "
            f"{format_transfer_size(report.free_after)} free. Active SDK/NDK/QEMU "
            "trees and user Gradle dependency caches were preserved."
        )
    return report


def android_sdk_install_working_space_bytes(packages: Sequence[str]) -> int:
    required = ANDROID_SDK_INSTALL_RESERVE_BYTES
    for package in packages:
        kind = str(package).split(";", 1)[0].strip()
        required += ANDROID_SDK_PACKAGE_WORKING_SPACE_BYTES.get(
            kind, ANDROID_SDK_UNKNOWN_PACKAGE_WORKING_SPACE_BYTES
        )
    return required


def clear_abandoned_android_provider_staging(cache_root: Path, *, dry_run: bool) -> None:
    providers = cache_root / "android-sdk-providers"
    if not providers.is_dir():
        return
    for provider_root in providers.iterdir():
        if not provider_root.is_dir():
            continue
        for candidate in provider_root.glob(".sdk-*.tmp"):
            if dry_run:
                info(f"Would clear abandoned Android SDK provider staging: {candidate}")
            else:
                shutil.rmtree(candidate, ignore_errors=True)
                info(f"Cleared abandoned Android SDK provider staging: {candidate}")


def clear_android_provider_download_cache(cache_root: Path, *, dry_run: bool) -> None:
    providers = cache_root / "android-sdk-providers"
    if not providers.is_dir():
        return
    for provider_root in providers.iterdir():
        downloads = provider_root / "downloads"
        if not downloads.exists():
            continue
        if dry_run:
            info(f"Would clear Android SDK provider download cache: {downloads}")
        else:
            shutil.rmtree(downloads, ignore_errors=True)
            info(f"Cleared Android SDK provider download cache: {downloads}")


def ensure_android_sdk_install_space(
    sdk_root: Path,
    cache_root: Path,
    packages: Sequence[str],
    *,
    dry_run: bool,
) -> None:
    """Ensure sdkmanager has enough scratch space before modifying the SDK.

    sdkmanager downloads and unpacks into the same filesystem and can leave a
    package half-installed on ENOSPC. Reclaim only disposable Android caches,
    then fail before installation if the filesystem still lacks conservative
    working headroom.
    """

    required = android_sdk_install_working_space_bytes(packages)
    free_before = android_sdk_free_bytes(sdk_root)
    if free_before is None or free_before >= required:
        return

    warn(
        "Low free space before Android SDK package installation: "
        f"{format_transfer_size(free_before)} free; approximately "
        f"{format_transfer_size(required)} working space reserved for "
        f"{', '.join(packages)}. Clearing safe Android caches first."
    )
    clear_android_download_debris(sdk_root, dry_run=dry_run)
    clear_abandoned_android_provider_staging(cache_root, dry_run=dry_run)
    cleanup_android_space(
        cache_root,
        target_free_bytes=required,
        dry_run=dry_run,
        managed_gradle_retention=DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION,
    )
    if dry_run:
        return

    free_after = android_sdk_free_bytes(sdk_root)
    if free_after is not None and free_after < required:
        reclaimed = max(0, free_after - free_before)
        fail(
            "Insufficient free space for Android SDK package installation. "
            f"Free after safe cache cleanup: {format_transfer_size(free_after)}; "
            f"reserved working space: {format_transfer_size(required)}; "
            f"reclaimed: {format_transfer_size(reclaimed)}. Packages: "
            f"{', '.join(packages)}. Free additional storage and rerun; "
            "Devtool intentionally did not start sdkmanager, so no new partial "
            "SDK package was created."
        )


def sdkmanager_failed_for_no_space(result: subprocess.CompletedProcess[str]) -> bool:
    output = "\n".join(str(item or "") for item in (result.stdout, result.stderr)).lower()
    return (
        "no space left on device" in output
        or "enospc" in output
        or "not enough space" in output
    )


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
    stdout = process.stdout
    try:
        for raw_line in stdout:
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
    finally:
        stdout.close()

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
    cache_root: Path | None = None,
) -> None:
    if cache_root is not None:
        ensure_android_sdk_install_space(
            sdk_root, cache_root, packages, dry_run=dry_run
        )

    command = [manager, f"--sdk_root={sdk_root}", *packages]
    result = run_sdkmanager_with_progress(
        command,
        env=env,
        input_text="y\n" * 200,
        dry_run=dry_run,
    )
    if dry_run or result.returncode == 0:
        return

    if sdkmanager_failed_for_no_space(result):
        # ENOSPC is not transient. Retrying immediately only destroys more of
        # the partially installed SDK and produces misleading secondary errors
        # such as PackageOperationXX disappearing underneath sdkmanager.
        clear_android_download_debris(sdk_root, dry_run=False)
        if cache_root is not None:
            clear_abandoned_android_provider_staging(cache_root, dry_run=False)
            clear_android_provider_download_cache(cache_root, dry_run=False)
        for package in packages:
            kind, _, version = package.partition(";")
            if kind in {"ndk", "cmake", "build-tools"} and version:
                remove_path(sdk_root / kind / version, dry_run=False)
            elif kind == "platforms" and version:
                remove_path(sdk_root / "platforms" / version, dry_run=False)
        free_after = android_sdk_free_bytes(sdk_root)
        suffix = (
            f" Free space after cleanup: {format_transfer_size(free_after)}."
            if free_after is not None
            else ""
        )
        fail(
            "Android SDK package installation stopped because the filesystem ran "
            "out of space. Devtool cleared partial SDK/download/provider cache "
            "state and will not retry an ENOSPC failure."
            f"{suffix} Packages: {', '.join(packages)}. Free additional storage "
            "and rerun."
        )

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
    provider_evidence=None,
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

    build_tools = select_required_build_tools_version(
        project,
        sdk_root,
        build_tools_override,
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

    normalize_sdk_build_tools_locations(sdk_root, dry_run=dry_run)

    if cmake_version:
        cmake_dir = sdk_root / "cmake" / cmake_version
        if source_properties_revision(cmake_dir) == cmake_version:
            ensure_host_cmake_compatible(
                sdk_root,
                cmake_version,
                dry_run=dry_run,
            )

    def collect_missing(root: Path) -> tuple[list[str], list[Path]]:
        missing_packages: list[str] = []
        incomplete: list[Path] = []
        if platform_id and not (root / "platforms" / platform_id / "android.jar").is_file():
            missing_packages.append(f"platforms;{platform_id}")
            incomplete.append(root / "platforms" / platform_id)
        if build_tools and not build_tools_is_ready(root, build_tools):
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

        build_tools = select_required_build_tools_version(
            project,
            sdk_root,
            build_tools_override,
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
            cache_root=cache_root,
        )
        # sdkmanager may leave packaging-revision-suffixed directories behind
        # on some Termux SDK distributions. Normalize again after installation
        # before validating the exact revision AGP will request.
        normalize_sdk_build_tools_locations(sdk_root, dry_run=dry_run)
        if cmake_version:
            ensure_host_cmake_compatible(sdk_root, cmake_version, dry_run=dry_run)
        if not dry_run:
            validation_failures: list[str] = []
            if platform_id and not (sdk_root / "platforms" / platform_id / "android.jar").is_file():
                validation_failures.append(f"Platform {platform_id}")
            if build_tools and not build_tools_is_ready(sdk_root, build_tools):
                validation_failures.append(f"Build Tools {build_tools}")
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
        provider_evidence=provider_evidence,
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
    if properties.is_file():
        text = properties.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"gradle-([0-9][^-]*)-(?:bin|all)\.zip", text)
        if match:
            return match.group(1)
    return standalone_gradle_wrapper_version(project_root)


def gradle_version(command: Path, *, timeout: float = 10.0) -> str | None:
    try:
        result = subprocess.run(
            [str(command), "--version"], text=True, capture_output=True, check=False, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
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
    provenance = home / ".devtool-gradle.json"
    archive = cache_root / "downloads" / f"gradle-{version}-bin.zip"
    checksum_file = cache_root / "downloads" / f"gradle-{version}-bin.zip.sha256"

    def expected_checksum() -> str:
        download(
            f"https://services.gradle.org/distributions/gradle-{version}-bin.zip.sha256",
            checksum_file, offline=offline, force=force,
        )
        try:
            value = checksum_file.read_text(encoding="utf-8").strip().split()[0].lower()
        except (OSError, IndexError):
            value = ""
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            fail(f"Gradle checksum metadata is invalid for {version}: {checksum_file}")
        return value

    if launcher.is_file() and provenance.is_file() and not force:
        try:
            data = json.loads(provenance.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        actual = gradle_version(launcher)
        if str(data.get("version") or "") == version and gradle_version_matches(actual, version):
            recorded = str(data.get("archive_sha256") or "").lower()
            if re.fullmatch(r"[0-9a-f]{64}", recorded) and archive.is_file():
                actual_archive = sha256(archive)
                official = expected_checksum()
                if actual_archive != recorded or actual_archive != official:
                    fail(
                        f"Cached Gradle archive digest verification failed for {version}: "
                        f"provenance={recorded}, official={official}, actual={actual_archive}"
                    )
                return ensure_executable(launcher, "Gradle launcher")
        if offline:
            fail(f"Cached Gradle {version} failed version/provenance verification: {launcher}")

    expected = expected_checksum()
    download(
        f"https://services.gradle.org/distributions/gradle-{version}-bin.zip",
        archive, offline=offline, force=force,
    )
    actual_digest = sha256(archive)
    if actual_digest != expected:
        fail(f"Gradle distribution SHA-256 mismatch for {version}: expected {expected}, got {actual_digest}")
    home.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gradle-build-gradle-") as temporary_dir:
        temporary = Path(temporary_dir)
        extract_zip(archive, temporary)
        extracted = temporary / f"gradle-{version}"
        if not extracted.is_dir():
            fail(f"Unexpected Gradle archive layout: {archive}")
        staged_launcher = ensure_executable(extracted / "bin" / "gradle", "Gradle launcher")
        staged_actual = gradle_version(staged_launcher)
        if not gradle_version_matches(staged_actual, version):
            fail(
                f"Downloaded Gradle launcher identity mismatch before install: requested {version}, "
                f"launcher reports {staged_actual or 'unknown'}"
            )
        staged_home = home.parent / f".{home.name}.devtool-stage-{os.getpid()}"
        shutil.rmtree(staged_home, ignore_errors=True)
        shutil.copytree(extracted, staged_home)
        staged_copy_launcher = ensure_executable(staged_home / "bin" / "gradle", "Gradle launcher")
        copied_actual = gradle_version(staged_copy_launcher)
        if not gradle_version_matches(copied_actual, version):
            shutil.rmtree(staged_home, ignore_errors=True)
            fail(
                f"Staged Gradle launcher identity mismatch before install: requested {version}, "
                f"launcher reports {copied_actual or 'unknown'}"
            )
        if home.exists():
            shutil.rmtree(home)
        staged_home.replace(home)
    launcher = ensure_executable(launcher, "Gradle launcher")
    actual = gradle_version(launcher)
    if not gradle_version_matches(actual, version):
        shutil.rmtree(home, ignore_errors=True)
        fail(f"Installed Gradle launcher identity mismatch: requested {version}, launcher reports {actual or 'unknown'}")
    provenance.write_text(
        json.dumps({"version": version, "archive_sha256": actual_digest}, sort_keys=True) + "\n", encoding="utf-8"
    )
    return launcher


def version_key(value: str | None) -> tuple[int, ...]:
    """Return a numeric comparison key for versions such as 8.13 or 8.13.2."""
    if not value:
        return ()
    match = re.match(r"\s*(\d+(?:\.\d+)*)", value)
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def minimum_gradle_for_agp(agp_version: str | None) -> str | None:
    """Return the documented minimum Gradle version for known AGP lines.

    Unknown newer lines fail closed rather than being guessed from the AGP
    major number. Patch releases inherit their minor-line requirement.
    """
    key = version_key(agp_version)
    if len(key) < 2:
        return None
    major, minor = key[:2]
    current = {
        (9, 4): "9.6.0", (9, 3): "9.5.0", (9, 2): "9.4.1", (9, 1): "9.3.1", (9, 0): "9.1.0",
        (8, 13): "8.13", (8, 12): "8.13", (8, 11): "8.13", (8, 10): "8.11.1", (8, 9): "8.11.1",
        (8, 8): "8.10.2", (8, 7): "8.9", (8, 6): "8.7", (8, 5): "8.7", (8, 4): "8.6",
        (8, 3): "8.4", (8, 2): "8.2", (8, 1): "8.0", (8, 0): "8.0",
        (7, 4): "7.5", (7, 3): "7.4", (7, 2): "7.3.3", (7, 1): "7.2", (7, 0): "7.0.2",
    }
    return current.get((major, minor))


def gradle_meets_agp_minimum(actual: str | None, project: ProjectInfo | None) -> bool:
    if project is None or not project.android or project.agp_major is None:
        return True
    if project.agp_version is None:
        # Compatibility shim for legacy callers constructing ProjectInfo by
        # hand. Real project inspection records the full AGP version; without
        # it, preserve the older conservative same-generation rule rather than
        # pretending a minor-specific compatibility mapping is known.
        actual_key = version_key(actual)
        return bool(actual_key) and actual_key[0] == project.agp_major
    minimum = minimum_gradle_for_agp(project.agp_version)
    if minimum is None:
        return False
    actual_key = version_key(actual)
    minimum_key = version_key(minimum)
    width = max(len(actual_key), len(minimum_key))
    return actual_key + (0,) * (width - len(actual_key)) >= minimum_key + (0,) * (width - len(minimum_key))


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
    if custom.is_file() and gradle_version_matches(gradle_version(custom), version):
        return True
    wrapper_root = gradle_user_home / "wrapper" / "dists"
    if wrapper_root.is_dir():
        pattern = f"gradle-{version}-*"
        for candidate in wrapper_root.glob(f"{pattern}/*/gradle-{version}/bin/gradle"):
            if candidate.is_file() and gradle_version_matches(gradle_version(candidate), version):
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


def _auto_installed_gradle_compatible(
    actual: str | None,
    *,
    selected_provider: str,
    project: ProjectInfo | None,
) -> bool:
    """Return whether an unpinned installed Gradle is safe for auto selection.

    Devtool's managed default is not a project version pin. On native Termux we
    may prefer the packaged Gradle for wrapperless projects, but Android builds
    still need a conservative AGP-generation check. Non-Termux/system behavior
    remains on the historical managed-default compatibility path.
    """
    key = version_key(actual)
    if not key:
        return False
    if selected_provider != "termux" or not running_under_termux_python():
        return gradle_version_matches(actual, DEFAULT_GRADLE_VERSION)
    if project is None or not project.android:
        return True
    return gradle_meets_agp_minimum(actual, project)


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
    provider: str = "auto",
    project: ProjectInfo | None = None,
    provider_evidence=None,
) -> Path:
    provider = normalize_gradle_provider(provider)
    if provider == "termux" and explicit is None and not running_under_termux_python():
        fail("Gradle provider termux requires native Termux execution or an explicit --gradle-command.")

    wrapper = project_root / "gradlew"
    wrapper_state = gradle_wrapper_state(project_root)
    wrapper_version = wrapper_distribution_version(project_root)
    pinned_version = version or wrapper_version
    requested = pinned_version or DEFAULT_GRADLE_VERSION
    if pinned_version and not gradle_meets_agp_minimum(pinned_version, project):
        minimum = minimum_gradle_for_agp(project.agp_version if project else None)
        fail(
            f"Gradle {pinned_version} is incompatible with Android Gradle Plugin "
            f"{project.agp_version if project else 'unknown'}; documented minimum is {minimum or 'unknown/fail-closed'}."
        )
    if not pinned_version and project is not None and project.android and project.agp_major is not None:
        minimum = minimum_gradle_for_agp(project.agp_version) or {9: "9.1.0", 8: "8.0", 7: "7.0.2"}.get(project.agp_major)
        if minimum is None:
            fail(f"Android Gradle Plugin {project.agp_version or project.agp_major} has no verified Gradle compatibility mapping")
        if not gradle_meets_agp_minimum(requested, project):
            requested = minimum

    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            found = shutil.which(explicit)
            if not found:
                fail(f"Gradle command not found: {explicit}")
            candidate = Path(found)
        candidate = ensure_executable(candidate.resolve(), "Gradle launcher")
        actual = gradle_version(candidate)
        if actual is None:
            fail(f"Could not determine Gradle version from explicit launcher: {candidate}")
        if pinned_version and not gradle_version_matches(actual, pinned_version):
            fail(
                f"Explicit Gradle launcher is incompatible: {candidate} reports "
                f"{actual}, but this build requires Gradle {pinned_version}. "
                "Choose another launcher or pass --gradle-version."
            )
        if not gradle_meets_agp_minimum(actual, project):
            minimum = minimum_gradle_for_agp(project.agp_version if project else None)
            fail(
                f"Gradle {actual} is incompatible with Android Gradle Plugin "
                f"{project.agp_version if project else 'unknown'}; documented minimum is {minimum or 'unknown/fail-closed'}."
            )
        info(
            f"Using explicit Gradle: {candidate} ({actual}); "
            f"provider={executable_provider(candidate)}"
        )
        return candidate

    def _warn_wrapper_version_override() -> None:
        if version and wrapper_version and not gradle_version_matches(wrapper_version, version):
            warn(
                f"Project wrapper declares Gradle {wrapper_version}, while Gradle {version} "
                "was requested. The wrapper remains authoritative."
            )

    if provider == "wrapper":
        if not wrapper_state.complete:
            fail(broken_wrapper_message(project_root, wrapper_state))
        _warn_wrapper_version_override()
        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            if wrapper_version and gradle_wrapper_distribution_cached(
                project_root, wrapper_version, gradle_user_home=gradle_user_home
            ):
                info(f"Using project Gradle wrapper with cached distribution: {wrapper} ({wrapper_version}); provider=wrapper")
                return ensure_executable(wrapper, "Gradle wrapper")
            fail_bootstrap_blocked(
                "Gradle wrapper distribution",
                downloads=bootstrap_downloads,
                network=bootstrap_network,
                checked=[
                    str(wrapper),
                    str((gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")).expanduser() / "wrapper" / "dists"),
                    str((gradle_user_home or Path(os.environ.get("GRADLE_USER_HOME") or Path.home() / ".gradle")).expanduser() / "devtool-bootstrap"),
                ],
            )
        info(
            f"Using project Gradle wrapper: {wrapper}"
            + (f" ({wrapper_version})" if wrapper_version else "")
            + "; provider=wrapper"
        )
        return ensure_executable(wrapper, "Gradle wrapper")

    if provider == "auto" and wrapper_state.present and not wrapper_state.complete:
        fail(broken_wrapper_message(project_root, wrapper_state))

    if provider == "auto" and wrapper_state.complete:
        _warn_wrapper_version_override()

        # Native Termux optimization:
        #
        # The wrapper remains authoritative for the required Gradle
        # version, but there is no benefit in launching/downloading its
        # distribution when Termux already provides exactly that Gradle
        # version.
        #
        # Only select Termux Gradle when:
        #   * Devtool itself is running natively under Termux;
        #   * PATH resolves Gradle to the Termux provider;
        #   * its version exactly matches the wrapper/requested version;
        #   * it satisfies the project's AGP requirement; and
        #   * the existing Termux capability probe accepts the binary.
        #
        # Any mismatch falls through to the existing wrapper path.
        if running_under_termux_python():
            system = shutil.which("gradle")

            if system:
                try:
                    system_path = ensure_executable(
                        Path(system),
                        "Gradle launcher",
                    )
                except BuildError:
                    # PATH may contain a stale/broken Gradle launcher. In
                    # auto mode this is only an optimization candidate; the
                    # complete project wrapper remains authoritative.
                    system_path = None

                if system_path is not None and executable_provider(system_path) == "termux":
                    actual = gradle_version(system_path)
                    required = pinned_version or wrapper_version

                    if (
                        actual
                        and required
                        and gradle_version_matches(actual, required)
                        and gradle_meets_agp_minimum(actual, project)
                    ):
                        if provider_evidence is not None:
                            gradle_evidence = provider_evidence.gradle
                        else:
                            gradle_evidence = (
                                _termux_toolchains_runtime_module()
                                .termux_gradle_probe(env=os.environ)
                            )

                        evidence_matches = (
                            gradle_evidence.compatible
                            and gradle_evidence.path is not None
                            and gradle_evidence.path.resolve(strict=False)
                            == system_path.resolve(strict=False)
                        )

                        if evidence_matches:
                            info(
                                "Using compatible termux Gradle instead "
                                "of wrapper launcher: "
                                f"{system_path} ({actual}); "
                                "wrapper version remains authoritative"
                            )
                            return system_path

        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            if wrapper_version and gradle_wrapper_distribution_cached(
                project_root, wrapper_version, gradle_user_home=gradle_user_home
            ):
                info(f"Using project Gradle wrapper with cached distribution: {wrapper} ({wrapper_version}); provider=wrapper")
                return ensure_executable(wrapper, "Gradle wrapper")
            system = shutil.which("gradle")
            if system:
                try:
                    system_path = ensure_executable(Path(system), "Gradle launcher")
                except BuildError:
                    system_path = None
                actual = gradle_version(system_path) if system_path is not None else None
                if system_path is not None and wrapper_version and gradle_version_matches(actual, wrapper_version):
                    selected_provider = executable_provider(system_path)
                    info(
                        f"Using compatible {selected_provider} Gradle because the wrapper distribution is not cached: "
                        f"{system_path} ({actual}); wrapper version remains authoritative"
                    )
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
        info(
            f"Using project Gradle wrapper: {wrapper}"
            + (f" ({wrapper_version})" if wrapper_version else "")
            + "; provider=wrapper"
        )
        return ensure_executable(wrapper, "Gradle wrapper")

    if provider == "download":
        if not bootstrap_provisioning_allowed(bootstrap_downloads, bootstrap_network):
            fail_bootstrap_blocked(
                "Gradle",
                downloads=bootstrap_downloads,
                network=bootstrap_network,
                checked=[str(cache_root / "gradle" / f"gradle-{requested}" / "bin" / "gradle")],
            )
        info(f"Using managed Gradle {requested}; provider=download")
        return managed_gradle(requested, cache_root, offline=offline, force=force)

    system = shutil.which("gradle")
    if system:
        system_path = ensure_executable(Path(system), "Gradle launcher")
        selected_provider = executable_provider(system_path)
        provider_matches = provider == "auto" or provider == selected_provider
        if provider_matches:
            actual = gradle_version(system_path)
            if pinned_version:
                compatible = gradle_version_matches(actual, pinned_version)
            elif provider in {"termux", "system"}:
                compatible = actual is not None and gradle_meets_agp_minimum(actual, project)
            else:
                compatible = _auto_installed_gradle_compatible(
                    actual,
                    selected_provider=selected_provider,
                    project=project,
                )
            if compatible and selected_provider == "termux" and running_under_termux_python():
                if provider_evidence is not None:
                    gradle_evidence = provider_evidence.gradle
                else:
                    gradle_evidence = _termux_toolchains_runtime_module().termux_gradle_probe(env=os.environ)
                compatible = bool(
                    gradle_evidence.compatible
                    and gradle_evidence.path is not None
                    and gradle_evidence.path.resolve(strict=False) == system_path.resolve(strict=False)
                )
            if compatible:
                info(
                    f"Using compatible {selected_provider} Gradle: {system_path} "
                    f"({actual or 'unknown'}); provider={selected_provider}"
                )
                return system_path
            if provider in {"termux", "system"}:
                requirement = pinned_version or "a project-compatible version"
                fail(
                    f"Requested Gradle provider {provider} is installed at {system_path} but reports "
                    f"{actual or 'an unknown version'}; {requirement} is required. "
                    "Use the project wrapper, select another provider, or pass --gradle-version if the project supports it."
                )
            warn(
                f"Ignoring incompatible {selected_provider} Gradle from PATH: {system_path} "
                f"({actual or 'unknown'}); managed Gradle {requested} will be considered instead."
            )
        elif provider in {"termux", "system"}:
            fail(
                f"Requested Gradle provider {provider}, but gradle on PATH resolves to the "
                f"{selected_provider} provider at {system_path}."
            )
    elif provider in {"termux", "system"}:
        detail = " Install the Termux gradle package with pkg install gradle." if provider == "termux" else ""
        fail(f"Requested Gradle provider {provider}, but no gradle executable was found on PATH.{detail}")

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
    info(f"Using managed Gradle {requested}; provider=download")
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

    AGP 9.x reports ``android.aapt2FromMavenOverride`` as an unsupported
    project option.  Older Devtool releases suppressed that warning with
    ``android.suppressUnsupportedOptionWarnings``; on current AGP that
    suppression property is itself experimental and therefore emits another
    warning.  Use AGP's current warning-category suppression property instead.

    Caller-provided current suppression categories are preserved.  Legacy
    ``android.suppressUnsupportedOptionWarnings`` arguments are consumed rather
    than forwarded so they cannot recreate the warning we are trying to hide.
    """

    normalized = normalize_gradle_arguments(arguments, plain_console=plain_console)
    if aapt2 is None:
        return normalized

    legacy_suppression_prefix = f"-P{UNSUPPORTED_OPTION_WARNINGS_PROPERTY}="
    warning_suppression_prefix = f"-P{AGP_WARNING_SUPPRESSIONS_PROPERTY}="
    aapt2_prefix = f"-P{AAPT2_OVERRIDE_PROPERTY}="

    warning_categories: list[str] = []
    remaining: list[str] = []
    explicit_aapt2: str | None = None

    for argument in normalized:
        value = str(argument)

        if value.startswith(legacy_suppression_prefix):
            # Do not forward the legacy suppression property: AGP 9.x warns
            # that this property is experimental.  The category suppression
            # below supersedes it for unsupported project options.
            continue

        if value.startswith(warning_suppression_prefix):
            configured = value[len(warning_suppression_prefix):]
            for item in configured.split(","):
                item = item.strip()
                if item and item not in warning_categories:
                    warning_categories.append(item)
            continue

        if value.startswith(aapt2_prefix):
            candidate = value[len(aapt2_prefix):].strip()
            if candidate:
                explicit_aapt2 = candidate
            continue

        remaining.append(value)

    if AGP_UNSUPPORTED_PROJECT_OPTION_WARNING not in warning_categories:
        warning_categories.append(AGP_UNSUPPORTED_PROJECT_OPTION_WARNING)

    selected_aapt2 = explicit_aapt2 or str(aapt2)

    properties = [
        f"-P{AAPT2_OVERRIDE_PROPERTY}={selected_aapt2}",
        f"-P{AGP_WARNING_SUPPRESSIONS_PROPERTY}={','.join(warning_categories)}",
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
        default=(Path(os.environ["GRADLE_BUILD_CHROOT_TERMUX_HOME"]) if os.environ.get("GRADLE_BUILD_CHROOT_TERMUX_HOME") else None),
        help="Verified path where the Termux home is mounted inside the chroot; no guest-home path is guessed",
    )
    parser.add_argument("-t", "--task", action="append", dest="tasks", help="Gradle task; repeat for multiple tasks")
    parser.add_argument("-c", "--clean", action="store_true", help="Run clean before requested/default tasks")
    parser.add_argument("-o", "--output-dir", type=Path, help="Copy newly produced artifacts here")
    parser.add_argument("-n", "--output-name", help="Base filename for copied artifacts; extension is inferred unless a single artifact is built")
    parser.add_argument("-Q", "--no-artifact-scan", action="store_true", help="Do not scan for produced APK/AAB/AAR/JAR artifacts")

    parser.add_argument("-a", "--aapt2", type=Path, help="Use this AAPT2 binary; shared-storage files are staged automatically")
    parser.add_argument(
        "-A", "--aapt2-provider",
        choices=("auto", "termux", "sdk", "revanced", "none"),
        default="auto",
        help="AAPT2 source when --aapt2 is omitted; auto is offline-first and never queries remote ReVanced metadata unless --aapt2-provider revanced is requested",
    )
    parser.add_argument("-R", "--aapt2-release", default="latest", help="ReVanced AAPT2 tag or latest")
    parser.add_argument(
        "--arm-build-tools-provider",
        choices=("auto", "termux", "sdk", "commit451", "none"),
        default="auto",
        help=(
            "ARM64 provider for SDK aidl/zipalign/split-select; auto prefers compatible "
            "Termux-native tools before Commit451 on native Termux"
        ),
    )

    parser.add_argument("-s", "--sdk-root", type=Path, help="Android SDK root")
    parser.add_argument("--sdk-provider", choices=sorted(ANDROID_SDK_PROVIDERS), default=os.environ.get("DEVTOOL_ANDROID_SDK_PROVIDER", "auto"), help="Android SDK provider: auto, termuxvoid, homuhomu, or managed")
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
    parser.add_argument("--native-provider", choices=sorted(NATIVE_PROVIDERS), default="auto", help="Native compiler provider: auto, termux-sysroot, ndk, or system")
    parser.add_argument("--native-cache", choices=sorted(NATIVE_CACHE_PROVIDERS), default="auto", help="Native compiler cache provider: auto, ccache, or off")
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
        "--java-provider",
        choices=sorted(JAVA_PROVIDERS),
        default=os.environ.get("DEVTOOL_JAVA_PROVIDER", "auto"),
        help="Java provider: auto, termux, or system; an explicit --java-home remains authoritative",
    )
    parser.add_argument(
        "-D", "--system-deps",
        choices=("auto", "always", "never"),
        default="auto",
        help="Install missing Java/CA/Node packages only when needed, always refresh them, or never install them",
    )
    parser.add_argument("-K", "--skip-system-deps", dest="system_deps", action="store_const", const="never", help="Alias for --system-deps never")
    parser.add_argument("-J", "--min-java", type=int, help="Minimum Java version; inferred when omitted")
    parser.add_argument("-g", "--gradle-command", help="Explicit Gradle or gradlew executable")
    parser.add_argument(
        "--gradle-provider",
        choices=sorted(GRADLE_PROVIDERS),
        default=os.environ.get("DEVTOOL_GRADLE_PROVIDER", "auto"),
        help="Gradle provider: auto, wrapper, termux, system, or download; an explicit --gradle-command remains authoritative",
    )
    parser.add_argument("-G", "--gradle-version", help="Managed Gradle version when no wrapper is available")
    parser.add_argument("--gradle-user-home", type=Path, help="GRADLE_USER_HOME for this build")
    parser.add_argument("-k", "--cache-dir", type=Path, default=devtool_cache_dir() / APP_NAME)
    parser.add_argument(
        "--space-cleanup",
        choices=sorted(SPACE_CLEANUP_POLICIES),
        default=os.environ.get("DEVTOOL_ANDROID_SPACE_CLEANUP", "safe"),
        help="Automatic Android/Devtool cache cleanup policy when storage is low",
    )
    parser.add_argument(
        "--space-min-free-mb",
        type=int,
        default=int(os.environ.get("DEVTOOL_ANDROID_MIN_FREE_MB", DEFAULT_ANDROID_SPACE_MIN_FREE_MB)),
        help="Trigger safe automatic cleanup below this free-space threshold",
    )
    parser.add_argument(
        "--space-target-free-mb",
        type=int,
        default=int(os.environ.get("DEVTOOL_ANDROID_TARGET_FREE_MB", DEFAULT_ANDROID_SPACE_TARGET_FREE_MB)),
        help="Stop automatic cleanup after reaching this free-space target",
    )
    parser.add_argument(
        "--managed-gradle-retention",
        type=int,
        default=int(os.environ.get("DEVTOOL_ANDROID_MANAGED_GRADLE_RETENTION", DEFAULT_ANDROID_MANAGED_GRADLE_RETENTION)),
        help="Number of newest Devtool-managed Gradle distributions to retain during space cleanup",
    )
    parser.add_argument(
        "--manage-gradle-user-cache",
        action="store_true",
        default=os.environ.get("DEVTOOL_ANDROID_MANAGE_GRADLE_USER_CACHE", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Allow low-space cleanup to prune old version-specific GRADLE_USER_HOME caches and build-cache-1; modules and wrapper distributions are preserved",
    )
    parser.add_argument(
        "--keep-provider-downloads",
        action="store_true",
        default=os.environ.get("DEVTOOL_ANDROID_KEEP_PROVIDER_DOWNLOADS", "").strip().lower() in {"1", "true", "yes", "on"},
        help="Keep TermuxVoid/HomuHomu provider release archives after an installed SDK is usable",
    )
    parser.add_argument(
        "--no-post-build-space-cleanup",
        dest="post_build_space_cleanup",
        action="store_false",
        default=True,
        help="Do not prune safe superseded Devtool caches after a successful Android build",
    )
    parser.add_argument("-w", "--max-workers", type=int, default=max(1, min(4, os.cpu_count() or 2)))
    parser.add_argument("--gradle-jvm-heap-mb", type=int, default=0, help="Cap the Gradle JVM heap in MiB")
    parser.add_argument("--gradle-metaspace-mb", type=int, default=0, help="Cap the Gradle JVM metaspace in MiB")
    parser.add_argument("--memory-guard-mb", type=int, default=0, help="Stop Gradle before system free memory remains below this threshold")
    parser.add_argument("--cpu-limit", type=int, default=0, help="Limit validation to this many logical CPUs when taskset is available")
    parser.add_argument(
        "--resource-resolution",
        action="append",
        default=[],
        help=argparse.SUPPRESS,
    )
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
    if project.agp_version:
        info(f"Android Gradle Plugin: {project.agp_version}")
    elif project.agp_major:
        info(f"Android Gradle Plugin major (legacy/partial detection): {project.agp_major}")
    if project.native_build:
        info("Native build: yes")
    if project.ndk_version:
        info(f"Declared NDK: {project.ndk_version}")
    if project.cmake_version:
        info(f"Declared CMake: {project.cmake_version}")

    if project.android:
        auto_manage_android_space(
            cache_root,
            policy=args.space_cleanup,
            min_free_mb=args.space_min_free_mb,
            target_free_mb=args.space_target_free_mb,
            managed_gradle_retention=args.managed_gradle_retention,
            include_gradle_user_cache=False,
            dry_run=args.dry_run,
            reason="Android toolchain setup",
        )

    provider_evidence = None
    if running_under_termux_python():
        provider_evidence = _termux_toolchains_runtime_module().resolve_provider_decisions(
            project_root, android=project.android, env=os.environ
        )

    requirement = infer_java_requirement(project)
    minimum_java = args.min_java or requirement.minimum
    if args.java_provider == "termux" and args.java_home is None and not running_under_termux_python():
        fail("Java provider termux requires native Termux execution or an explicit --java-home.")
    java_home: Path | None = None
    try:
        java_home = find_java_home(
            args.java_home,
            minimum_java,
            exact=requirement.exact if args.java_home is None else None,
            provider=args.java_provider,
            provider_evidence=provider_evidence,
        )
    except BuildError:
        java_home = None
    need_java = java_home is None
    need_certificates = not certificate_store_available()
    requested_tasks = list(args.tasks or default_tasks(project))
    node_required = gradle_tasks_require_node(requested_tasks)
    need_node = node_required and node_runtime_path() is None

    cmake_required = bool(project.native_build and project.cmake_version)
    termux_cmake = (
        _termux_toolchains_runtime_module().termux_cmake_probe(env=os.environ)
        if running_under_termux_python()
        else None
    )
    termux_ninja = (
        _termux_toolchains_runtime_module().termux_ninja_probe(env=os.environ)
        if running_under_termux_python()
        else None
    )
    need_cmake = bool(
        cmake_required
        and running_under_termux_python()
        and (termux_cmake is None or not termux_cmake.compatible)
    )
    need_ninja = bool(
        cmake_required
        and running_under_termux_python()
        and (termux_ninja is None or not termux_ninja.compatible)
    )

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
            need_node=need_node,
            need_cmake=need_cmake,
            need_ninja=need_ninja,
            offline=args.offline,
            java_version=minimum_java,
            java_provider=args.java_provider,
        )
        java_home = find_java_home(
            args.java_home,
            minimum_java,
            exact=requirement.exact if args.java_home is None else None,
            provider=args.java_provider,
            provider_evidence=None,
        )
    elif args.system_deps == "auto" and (
        need_java
        or need_certificates
        or need_node
        or need_cmake
        or need_ninja
    ):
        if not provisioning_allowed:
            missing = []
            if need_java:
                missing.append(f"Java {requirement.exact or str(minimum_java) + '+'}")
            if need_certificates:
                missing.append("CA certificates")
            if need_node:
                missing.append("Node.js")
            if need_cmake:
                missing.append("CMake")
            if need_ninja:
                missing.append("Ninja")
            fail_bootstrap_blocked(
                "System dependencies",
                downloads=args.toolchain_downloads,
                network=args.bootstrap_network,
                checked=[*missing, f"java_home={args.java_home}" if args.java_home else "java in PATH"],
            )
        install_missing_system_dependencies(
            need_java=need_java,
            need_certificates=need_certificates,
            need_node=need_node,
            need_cmake=need_cmake,
            need_ninja=need_ninja,
            offline=args.offline,
            java_version=minimum_java,
            java_provider=args.java_provider,
        )
        java_home = find_java_home(
            args.java_home,
            minimum_java,
            exact=requirement.exact if args.java_home is None else None,
            provider=args.java_provider,
            provider_evidence=None,
        )
    elif need_java:
        if requirement.exact is not None:
            fail(
                f"A JDK with Java {requirement.exact} is required for this project's declared toolchain and system dependency installation is disabled. "
                "Use --java-home or install the matching JDK."
            )
        fail(f"A JDK with Java {minimum_java}+ is required and system dependency installation is disabled. Use --java-home or install a JDK.")
    if node_required:
        node_runtime = node_runtime_path()
        if node_runtime is None:
            if args.system_deps == "never":
                fail(
                    "Node.js is required by requested Gradle JavaScript tasks but was not found in "
                    "the effective execution environment. Remove --system-deps never, install nodejs "
                    "inside the selected chroot, or provide a chroot-native node/nodejs on PATH."
                )
            fail(
                "Node.js provisioning completed but node/nodejs is still unavailable in the "
                "effective execution environment."
            )
        info(f"Node.js runtime: {node_runtime}")

    assert java_home is not None
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = os.pathsep.join([str(java_home / "bin"), env.get("PATH", "")])
    effective_gradle_user_home = _native_termux_sanitize_guest_path(
        args.gradle_user_home,
        label="Gradle user home",
        fallback=Path.home() / ".gradle" if args.gradle_user_home is not None else None,
    )
    if effective_gradle_user_home is not None:
        env["GRADLE_USER_HOME"] = str(effective_gradle_user_home)
    if project.android and args.manage_gradle_user_cache:
        auto_manage_android_space(
            cache_root,
            policy=args.space_cleanup,
            min_free_mb=args.space_min_free_mb,
            target_free_mb=args.space_target_free_mb,
            managed_gradle_retention=args.managed_gradle_retention,
            include_gradle_user_cache=True,
            gradle_user_home=effective_gradle_user_home or (Path.home() / ".gradle"),
            dry_run=args.dry_run,
            reason="Android build with Gradle cache management enabled",
        )
    heading("Java")
    selected_java_provider = executable_provider(java_home / "bin" / "java")
    info(f"JAVA_HOME={java_home}")
    info(f"Provider: {selected_java_provider}")
    if selected_java_provider == "termux":
        package_match = re.search(r"java-(\d+)-openjdk", java_home.name)
        package_name = f"openjdk-{package_match.group(1)}" if package_match else ""
        package_version = termux_package_version(package_name) if package_name else None
        if package_version:
            info(f"Termux package: {package_name} {package_version}")
    if requirement.exact is not None:
        info(f"Toolchain requirement: Java {requirement.exact}")
    elif minimum_java:
        info(f"Minimum Java: {minimum_java}+")
    run([java_home / "bin" / "java", "-version"], env=env, dry_run=args.dry_run)

    selected_native_provider = "none"
    if project.native_build or args.native_provider != "auto" or args.native_cache != "auto":
        selected_native_provider = select_native_provider(project, args.native_provider, provider_evidence=provider_evidence)
        configure_native_cache(env, args.native_cache, provider_evidence=provider_evidence)
        if project.native_build:
            info(f"Native provider: {selected_native_provider}")
        else:
            info(f"Native provider configured without detected native Android work: {selected_native_provider}")

    sdk_root: Path | None = None
    sdk_source: str | None = None
    platform_id: str | None = None
    if project.android:
        effective_sdk_root_arg = _native_termux_sanitize_guest_path(
            args.sdk_root,
            label="Android SDK root",
        )
        requested_sdk_provider = normalize_android_sdk_provider(args.sdk_provider)
        if effective_sdk_root_arg is not None:
            sdk_root, sdk_source = discover_sdk_root_with_source(
                effective_sdk_root_arg,
                project.root,
            )
        elif requested_sdk_provider == "auto":
            sdk_root, sdk_source = discover_sdk_root_with_source(None, project.root)
            if (
                sdk_root is not None
                and running_under_termux_python()
                and normalized_machine() == "arm64"
                and not sdk_host_compatible(sdk_root)
                and not termux_can_repair_sdk_build_tools(sdk_root)
            ):
                warn(
                    "Skipping discovered Android SDK because its ARM64 build-tools "
                    "cannot be repaired completely with native Termux helpers; "
                    f"trying external SDK providers instead: {sdk_root} "
                    f"({sdk_source or 'unknown source'})"
                )
                sdk_root, sdk_source = None, None
            if sdk_root is None:
                installed_termuxvoid = (
                    _termux_prefix_path() / "opt" / "android-sdk"
                    if running_under_termux_python()
                    else None
                )
                if (
                    installed_termuxvoid is not None
                    and installed_termuxvoid.is_dir()
                    and _sdk_tree_candidate(installed_termuxvoid) is not None
                    and sdk_host_compatible(installed_termuxvoid)
                ):
                    sdk_root, sdk_source = (
                        installed_termuxvoid.resolve(),
                        "TermuxVoid android-sdk",
                    )
            if sdk_root is None:
                for cached_provider, cached_label in (
                    ("termuxvoid", "TermuxVoid preassembled SDK cache"),
                    ("homuhomu", "HomuHomu ARM64 Android SDK cache"),
                ):
                    cached_sdk = _provider_cached_sdk(cache_root, cached_provider)
                    if cached_sdk is not None:
                        sdk_root, sdk_source = cached_sdk, cached_label
                        break
        elif requested_sdk_provider == "termuxvoid":
            installed_termuxvoid = _termux_prefix_path() / "opt" / "android-sdk" if running_under_termux_python() else None
            if installed_termuxvoid is not None and installed_termuxvoid.is_dir() and _sdk_tree_candidate(installed_termuxvoid) is not None:
                sdk_root, sdk_source = installed_termuxvoid.resolve(), "TermuxVoid android-sdk"
            else:
                sdk_root = _provider_cached_sdk(cache_root, "termuxvoid")
                sdk_source = "TermuxVoid preassembled SDK cache" if sdk_root is not None else None
        elif requested_sdk_provider == "homuhomu":
            sdk_root = _provider_cached_sdk(cache_root, "homuhomu")
            sdk_source = "HomuHomu ARM64 Android SDK cache" if sdk_root is not None else None
        else:
            sdk_root, sdk_source = None, None

        if sdk_root is None and not args.skip_sdk_install:
            if not provisioning_allowed:
                fail_bootstrap_blocked(
                    f"Android SDK provider {requested_sdk_provider}",
                    downloads=args.toolchain_downloads,
                    network=args.bootstrap_network,
                    checked=[
                        str(args.sdk_root) if args.sdk_root else "--sdk-root not set",
                        "local.properties sdk.dir",
                        "$ANDROID_HOME",
                        "$ANDROID_SDK_ROOT",
                        str(_termux_prefix_path() / "opt" / "android-sdk") if running_under_termux_python() else "",
                        str(_provider_sdk_root(cache_root, "termuxvoid")),
                        str(_provider_sdk_root(cache_root, "homuhomu")),
                        str(Path.home() / "Android" / "Sdk"),
                        str(Path.home() / "android-sdk"),
                        "/opt/android-sdk",
                        "/usr/lib/android-sdk",
                    ],
                )
            sdk_root, sdk_source = provision_android_sdk_provider(
                requested_sdk_provider,
                cache_root,
                force=args.force_setup,
                dry_run=args.dry_run,
                downloads=args.toolchain_downloads,
                network=args.bootstrap_network,
            )
        if sdk_root:
            sdk_root.mkdir(parents=True, exist_ok=True)
            configure_android_sdk_env(env, sdk_root)
            if sdk_source:
                info(f"SDK path source: {sdk_source}")
            if not args.keep_provider_downloads:
                clear_selected_provider_download(cache_root, sdk_root, dry_run=args.dry_run)
            if args.auto_sdk and not args.skip_sdk_install:
                info("Auto SDK downloads: enabled")
        sdk_root, platform_id, build_tools, _, _ = ensure_android_sdk(
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
            allow_managed_fallback=effective_sdk_root_arg is None,
            bootstrap_downloads=args.toolchain_downloads,
            bootstrap_network=args.bootstrap_network,
            provider_evidence=provider_evidence,
        )

        if project.native_build and normalized_machine() in {"arm64", "armv7"} and args.ndk_host_provider in {"auto", "arm64", "qemu"}:
            clean_android_cxx_state(project.root, dry_run=args.dry_run)

    aapt2: Path | None = None
    if project.android:
        if args.aapt2:
            aapt2 = stage_aapt2(args.aapt2, cache_root, force=args.force_setup, dry_run=args.dry_run)
        elif args.aapt2_provider in {"auto", "termux"} and running_under_termux_python():
            aapt2 = select_termux_aapt2(
                sdk_root,
                platform_id,
                required=args.aapt2_provider == "termux",
                dry_run=args.dry_run,
                provider_evidence=provider_evidence,
            )
        if aapt2 is None and args.aapt2_provider in {"auto", "sdk"}:
            aapt2 = select_sdk_build_tools_aapt2(
                sdk_root,
                build_tools,
                platform_id,
                dry_run=args.dry_run,
            )
            if aapt2 is None and args.aapt2_provider == "sdk":
                fail(
                    "Explicit Android SDK AAPT2 provider was requested, "
                    "but no compatible SDK build-tools AAPT2 is available "
                    f"for {platform_id or 'the selected platform'}."
                )
        if aapt2 is None and args.aapt2_provider == "auto" and normalized_machine() in {"arm64", "armv7"}:
            aapt2 = select_cached_revanced_aapt2(cache_root, dry_run=args.dry_run)
        if aapt2 is None and args.aapt2_provider == "revanced":
            if not provisioning_allowed:
                cached = select_cached_revanced_aapt2(cache_root, dry_run=args.dry_run)
                if cached is None:
                    fail_bootstrap_blocked(
                        "AAPT2",
                        downloads=args.toolchain_downloads,
                        network=args.bootstrap_network,
                        checked=[
                            "--aapt2 not set",
                            str(cache_root / "aapt2" / "revanced"),
                            "Termux AAPT2 capability probe",
                            "Android SDK build-tools AAPT2",
                            "Android Gradle Plugin default AAPT2",
                        ],
                    )
                aapt2 = cached
            else:
                aapt2 = obtain_revanced_aapt2(
                    cache_root,
                    release_name=args.aapt2_release,
                    offline=args.offline or not provisioning_allowed,
                    force=args.force_setup,
                    dry_run=args.dry_run,
                )
        elif aapt2 is None and args.aapt2_provider in {"auto", "none"}:
            if args.aapt2_provider == "auto":
                info(
                    "AAPT2 provider: Android Gradle Plugin default "
                    "(auto does not query remote ReVanced metadata; use --aapt2-provider revanced to provision it)"
                )
            else:
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
        provider=args.gradle_provider,
        project=project,
        provider_evidence=provider_evidence,
    )
    heading("Gradle")
    info(f"Launcher: {gradle}")
    if should_cleanup_gradle(args):
        stop_gradle_processes(gradle, project_root, env, dry_run=args.dry_run)
    run([gradle, "--version"], cwd=project_root, env=env, dry_run=args.dry_run)

    tasks = list(requested_tasks)

    (
        tasks,
        skipped_connected_tasks,
        connected_test_adb,
        connected_test_states,
    ) = filter_unavailable_connected_android_tests(
        tasks,
        sdk_root=sdk_root,
        env=env,
        dry_run=args.dry_run,
    )

    if skipped_connected_tasks:
        heading("Connected Android tests")

        state_summary = (
            ", ".join(
                f"{serial}={state}"
                for serial, state in sorted(
                    connected_test_states.items()
                )
            )
            or "none"
        )

        info(
            "No usable connected Android device is available; "
            "connected-device Gradle tests will be skipped."
        )

        if connected_test_adb:
            info(f"ADB: {connected_test_adb}")

        info(f"ADB device states: {state_summary}")

        for skipped_task in skipped_connected_tasks:
            info(
                "SKIPPED Gradle task "
                f"{skipped_task}: no connected Android device"
            )

        # Machine-readable marker for Devtool logs and future consumers.
        print(
            "DEVTOOL_ANDROID_CONNECTED_TEST_SKIP "
            + json.dumps(
                {
                    "reason": "no-connected-device",
                    "tasks": skipped_connected_tasks,
                    "adb": connected_test_adb,
                    "device_states": connected_test_states,
                },
                sort_keys=True,
            )
        )

    # A task such as bkxAndroidReleaseSeal may depend on
    # connectedDebugAndroidTest without that connected task appearing in
    # requested_tasks. Determine device policy independently of the explicit
    # task list so those transitive dependencies are handled too.
    no_usable_connected_device = (
        not args.dry_run
        and not any(
            state == "device"
            for state in connected_test_states.values()
        )
    )

    connected_test_skip_init_script: Path | None = None

    if no_usable_connected_device:
        connected_test_skip_init_script = (
            _write_no_device_connected_test_init_script(
                cache_root
            )
        )

        if not skipped_connected_tasks:
            heading("Connected Android tests")
            info(
                "No usable connected Android device is available. "
                "Any connected*AndroidTest task discovered transitively "
                "will be skipped."
            )

            if connected_test_adb:
                info(f"ADB: {connected_test_adb}")

            state_summary = (
                ", ".join(
                    f"{serial}={state}"
                    for serial, state in sorted(
                        connected_test_states.items()
                    )
                )
                or "none"
            )

            info(
                f"ADB device states: {state_summary}"
            )

            print(
                "DEVTOOL_ANDROID_CONNECTED_TEST_SKIP "
                + json.dumps(
                    {
                        "reason": "no-connected-device",
                        "tasks": [],
                        "transitive": True,
                        "adb": connected_test_adb,
                        "device_states": connected_test_states,
                    },
                    sort_keys=True,
                )
            )

        info(
            "Gradle connected-device policy: "
            "connected*AndroidTest tasks are disabled for this invocation"
        )

    if args.clean and "clean" not in tasks:
        tasks.insert(0, "clean")

    # An invocation consisting only of connected-device tests is a
    # successful skip, not a Gradle failure.
    if not tasks:
        heading("Gradle process summary")
        info(f"Project: {project_root}")
        info("Tasks (0): all requested tasks skipped")
        info(
            "Gradle result: skipped because no usable connected "
            "Android device is available"
        )
        return 0

    command: list[str | os.PathLike[str]] = [
        gradle,
        *tasks,
        f"--max-workers={args.max_workers}",
        f"-Dorg.gradle.java.installations.paths={java_home}",
        "-Dorg.gradle.java.installations.auto-detect=false",
    ]
    if connected_test_skip_init_script is not None:
        # A generated init script is active for this invocation. Force
        # configuration cache off even if the project enables it through
        # gradle.properties or the configured Gradle argument list.
        args.gradle_args = _force_gradle_configuration_cache_off(
            list(args.gradle_args)
        )

        command.extend(
            [
                "--init-script",
                str(connected_test_skip_init_script),
            ]
        )

    jvmargs = gradle_jvmargs(args)
    if jvmargs:
        command.append(f"-Dorg.gradle.jvmargs={jvmargs}")
    if args.gradle_lock_timeout_ms:
        command.append(f"-Dorg.gradle.cache.internal.lockTimeout={args.gradle_lock_timeout_ms}")
    if args.constrained:
        if not args.parallel:
            command.append("-Dorg.gradle.parallel=false")
        command.extend([
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

    heading("Gradle process summary")
    info(f"Project: {project_root}")
    info(f"Tasks ({len(tasks)}): {' '.join(tasks) if tasks else '(none)'}")
    info(f"Launcher: {gradle}")
    info(f"Java home: {java_home}")
    info(
        "Execution: "
        f"workers={args.max_workers}, "
        f"cpu-limit={args.cpu_limit or 'none'}, "
        f"parallel={'on' if args.parallel and args.max_workers > 1 else 'off'}, "
        f"daemon={'off/single-use' if args.no_daemon else 'reusable'}, "
        f"priority={'low' if args.low_priority else 'normal'}"
    )
    heap_summary = f"{args.gradle_jvm_heap_mb} MiB" if args.gradle_jvm_heap_mb else "auto"
    metaspace_summary = f"{args.gradle_metaspace_mb} MiB" if args.gradle_metaspace_mb else "auto"
    guard_summary = f"{args.memory_guard_mb} MiB" if args.memory_guard_mb else "disabled"
    info(
        "Memory: "
        f"heap={heap_summary}, "
        f"metaspace={metaspace_summary}, "
        f"guard={guard_summary}"
    )
    configuration_cache = "default"
    if "--no-configuration-cache" in args.gradle_args:
        configuration_cache = "off"
    elif "--configuration-cache" in args.gradle_args:
        configuration_cache = "on"
    info(
        "Caching: "
        f"build-cache={'on' if args.build_cache else 'off'}, "
        f"configuration-cache={configuration_cache}"
    )
    info(
        "Android tools: "
        f"aapt2={aapt2 or 'Gradle/default'}, "
        f"failure-mode={'continue' if args.continue_on_failure else 'fail-fast'}"
    )
    if jvmargs:
        info(f"Gradle JVM args: {jvmargs}")
    if args.resource_resolution:
        info("Flag resolution:")
        for note in args.resource_resolution:
            info(f"  - {note}")

    heading("Build")
    build_started = time.time()
    build_succeeded = False
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
        build_succeeded = True
        info(f"Gradle result: success in {time.time() - build_started:.1f}s")
    except Exception:
        info(f"Gradle result: failed in {time.time() - build_started:.1f}s")
        raise

    try:
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
        if build_succeeded and project.android and args.post_build_space_cleanup:
            auto_manage_android_space(
                cache_root,
                policy=args.space_cleanup,
                min_free_mb=args.space_min_free_mb,
                target_free_mb=args.space_target_free_mb,
                managed_gradle_retention=args.managed_gradle_retention,
                include_gradle_user_cache=args.manage_gradle_user_cache,
                gradle_user_home=effective_gradle_user_home or (Path.home() / ".gradle"),
                dry_run=args.dry_run,
                reason="successful Android build",
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
