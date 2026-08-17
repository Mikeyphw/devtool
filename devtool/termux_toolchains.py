from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

TERMUX_PREFIX_FALLBACK = Path('/data/data/com.termux/files/usr')
VALID_JAVA_PROVIDERS = {'auto', 'termux', 'system'}
VALID_GRADLE_PROVIDERS = {'auto', 'wrapper', 'termux', 'system', 'download'}
VALID_NATIVE_PROVIDERS = {'auto', 'termux-sysroot', 'ndk', 'system'}
VALID_NATIVE_CACHE_PROVIDERS = {'auto', 'ccache', 'off'}
VALID_ANDROID_BUILD_TOOLS_PROVIDERS = {'auto', 'termux', 'sdk', 'commit451', 'none'}

_GRADLE_SCAN_IGNORED_DIRS = frozenset({
    '.git', '.gradle', '.idea', '.devtool', '.venv', 'venv',
    'build', 'out', 'dist', 'node_modules', 'target', 'bin', 'obj',
})


def _iter_project_files(project_root: Path):
    """Yield source/config files without descending into generated trees.

    ``Path.rglob`` cannot prune directory recursion on Python 3.11, which made
    Android project inspection walk Gradle caches and build outputs before the
    validation HUD could render. ``os.walk`` lets us prune those directories
    in-place and keeps devtool's Python 3.11 compatibility.
    """

    root = Path(project_root).expanduser()
    if not root.is_dir():
        return
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [
            name for name in dirnames
            if name.lower() not in _GRADLE_SCAN_IGNORED_DIRS
        ]
        base = Path(current)
        for filename in filenames:
            yield base / filename


@dataclass(frozen=True)
class RuntimeIdentity:
    platform: str
    environment: str
    arch: str
    libc: str
    prefix: Path | None = None
    android_api: int | None = None

    @property
    def is_termux(self) -> bool:
        return self.environment == 'termux'


@dataclass(frozen=True)
class ProviderDecisionEvidence:
    runtime: RuntimeIdentity
    wrapper: object
    gradle: "ToolProvider"
    java: tuple["ToolProvider", ...]
    native: "ToolProvider"
    native_requirement: "NativeRequirement"
    cmake: "ToolProvider"
    ninja: "ToolProvider"
    ccache: "ToolProvider"
    android_tools: tuple["ToolProvider", ...]


@dataclass(frozen=True)
class PackageProbe:
    name: str
    installed: bool
    version: str = ''
    manager: str = ''
    reason: str = ''


@dataclass(frozen=True)
class ToolProvider:
    capability: str
    provider: str
    available: bool
    path: Path | None = None
    version: str = ''
    package: str = ''
    package_version: str = ''
    package_manager: str = ''
    runtime: RuntimeIdentity | None = None
    compatible: bool | None = None
    selection_reason: str = ''
    rejection_reason: str = ''
    capabilities: tuple[str, ...] = ()
    reason: str = ''


def _normalized_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower().replace('-', '_')
    return {
        'aarch64': 'arm64',
        'arm64': 'arm64',
        'armv8l': 'arm64',
        'armv7l': 'armv7',
        'armv7': 'armv7',
        'x86_64': 'x86_64',
        'amd64': 'x86_64',
        'i386': 'x86',
        'i686': 'x86',
    }.get(value, value)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def termux_prefix(env: Mapping[str, str] | None = None) -> Path:
    env = env or os.environ
    raw = str(env.get('PREFIX', '') or '').strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    return TERMUX_PREFIX_FALLBACK


def _android_api_level(env: Mapping[str, str]) -> int | None:
    raw_api = str(env.get('ANDROID_API', '') or env.get('ANDROID_PLATFORM_LEVEL', '') or '').strip()
    if raw_api.isdigit():
        return int(raw_api)
    getprop = shutil.which('getprop', path=str(env.get('PATH', '') or '')) or '/system/bin/getprop'
    try:
        result = subprocess.run(
            [getprop, 'ro.build.version.sdk'],
            text=True,
            capture_output=True,
            check=False,
            timeout=2,
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdigit() else None


def detect_runtime_identity(
    env: Mapping[str, str] | None = None,
    *,
    executable: str | os.PathLike[str] | None = None,
    machine: str | None = None,
) -> RuntimeIdentity:
    env = env or os.environ
    prefix = termux_prefix(env)
    active_executable = Path(executable or sys.executable).expanduser().resolve(strict=False)
    inherited_chroot = any(
        str(env.get(name, '') or '').strip() in {'1', 'true', 'yes', 'on'}
        for name in (
            'GRADLE_BUILD_CHROOT_ACTIVE',
            'DEVTOOL_INSIDE_TERMUX_CHROOT',
            'DEVTOOL_AUTO_CHROOT_ACTIVE',
        )
    )
    marker = bool(str(env.get('TERMUX_VERSION', '') or '').strip()) or '/com.termux/' in str(prefix)
    native_termux = not inherited_chroot and _path_is_within(active_executable, prefix) and (
        marker or _path_is_within(prefix, TERMUX_PREFIX_FALLBACK.parent)
    )
    if native_termux:
        android_api = _android_api_level(env)
        return RuntimeIdentity(
            platform='android',
            environment='termux',
            arch=_normalized_arch(machine),
            libc='bionic',
            prefix=prefix,
            android_api=android_api,
        )
    system = platform.system().lower() or 'unknown'
    return RuntimeIdentity(
        platform=system,
        environment='host',
        arch=_normalized_arch(machine),
        libc='unknown',
        prefix=None,
        android_api=None,
    )


def normalize_java_provider(value: str | None) -> str:
    normalized = str(value or 'auto').strip().lower() or 'auto'
    if normalized not in VALID_JAVA_PROVIDERS:
        valid = ', '.join(sorted(VALID_JAVA_PROVIDERS))
        raise ValueError(f'Unsupported Java provider: {value}. Expected one of: {valid}')
    return normalized


def normalize_gradle_provider(value: str | None) -> str:
    normalized = str(value or 'auto').strip().lower() or 'auto'
    aliases = {'managed': 'download', 'downloaded': 'download'}
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_GRADLE_PROVIDERS:
        valid = ', '.join(sorted(VALID_GRADLE_PROVIDERS))
        raise ValueError(f'Unsupported Gradle provider: {value}. Expected one of: {valid}')
    return normalized


def classify_executable_provider(
    path: str | os.PathLike[str],
    *,
    runtime: RuntimeIdentity | None = None,
) -> str:
    runtime = runtime or detect_runtime_identity()
    candidate = Path(path).expanduser().resolve(strict=False)
    if runtime.is_termux and runtime.prefix is not None and _path_is_within(candidate, runtime.prefix):
        return 'termux'
    return 'system'


def termux_package_probe(
    name: str,
    *,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> PackageProbe:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return PackageProbe(name=name, installed=False, reason='not running in native Termux')
    path = str(env.get('PATH', '') or '')
    dpkg_query = shutil.which('dpkg-query', path=path)
    if not dpkg_query:
        return PackageProbe(name=name, installed=False, reason='dpkg-query not available')
    try:
        result = subprocess.run(
            [dpkg_query, '-W', '-f=${Status}\t${Version}', name],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PackageProbe(name=name, installed=False, manager='dpkg', reason=str(exc))
    status, _, version = result.stdout.strip().partition('\t')
    installed = result.returncode == 0 and 'install ok installed' in status
    return PackageProbe(
        name=name,
        installed=installed,
        version=version.strip() if installed else '',
        manager='dpkg',
        reason='' if installed else (result.stderr.strip() or status.strip() or f'exit {result.returncode}'),
    )


def termux_jvm_roots(runtime: RuntimeIdentity | None = None) -> Sequence[Path]:
    runtime = runtime or detect_runtime_identity()
    if not runtime.is_termux or runtime.prefix is None:
        return ()
    root = runtime.prefix / 'lib' / 'jvm'
    if not root.is_dir():
        return ()
    try:
        return tuple(sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name))
    except OSError:
        return ()


def _tool_version(
    command: Sequence[str],
    pattern: str,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    try:
        result = subprocess.run(
            list(command),
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            env=dict(env or os.environ),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    match = re.search(pattern, f'{result.stdout}\n{result.stderr}', re.MULTILINE)
    return match.group(1).strip() if match else ''


def termux_gradle_probe(
    *,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> ToolProvider:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return ToolProvider(
            'gradle', 'termux', False, runtime=runtime, compatible=False,
            rejection_reason='not running in native Termux', reason='not running in native Termux',
        )
    found = shutil.which('gradle', path=str(env.get('PATH', '') or ''))
    if not found:
        return ToolProvider(
            'gradle', 'termux', False, runtime=runtime, package='gradle', compatible=False,
            rejection_reason='gradle not found on PATH', reason='gradle not found on PATH',
        )
    resolved = Path(found).expanduser().resolve(strict=False)
    if runtime.prefix is None or not _path_is_within(resolved, runtime.prefix):
        reason = f'gradle resolves outside Termux PREFIX: {resolved}'
        return ToolProvider(
            'gradle', 'termux', False, path=resolved, runtime=runtime, package='gradle', compatible=False,
            rejection_reason=reason, reason=reason,
        )
    package = termux_package_probe('gradle', env=env, runtime=runtime)
    tool_version = _tool_version([str(resolved), '--version'], r'^Gradle\s+([^\s]+)', env=env)
    detected_reason = 'Termux packaged Gradle detected' if package.installed else 'Gradle executable under Termux PREFIX'
    if not tool_version:
        rejection = f'{detected_reason}, but gradle --version could not be parsed'
        return ToolProvider(
            'gradle', 'termux', True, path=resolved, package='gradle',
            package_version=package.version, package_manager=package.manager, runtime=runtime,
            compatible=False, rejection_reason=rejection, capabilities=('gradle',), reason=rejection,
        )
    return ToolProvider(
        'gradle', 'termux', True, path=resolved, version=tool_version,
        package='gradle', package_version=package.version, package_manager=package.manager,
        runtime=runtime, compatible=True, selection_reason=detected_reason,
        capabilities=('gradle',), reason=detected_reason,
    )


def termux_java_providers(
    *,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> tuple[ToolProvider, ...]:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return ()
    packages = {
        item.name: item
        for item in termux_installed_packages('openjdk-*', env=env, runtime=runtime)
    }
    providers: list[ToolProvider] = []
    for root in termux_jvm_roots(runtime):
        java = root / 'bin' / 'java'
        javac = root / 'bin' / 'javac'
        if not java.is_file() or not javac.is_file():
            continue
        version = _tool_version([str(java), '-version'], r'(?:openjdk|java) version [\"\']([^\"\']+)', env=env)
        package_name = ''
        match = re.match(r'java-(\d+)-openjdk(?:$|-)', root.name)
        if match:
            package_name = f'openjdk-{match.group(1)}'
        package = packages.get(package_name)
        detected_reason = f'Termux OpenJDK detected at {root}'
        compatible = bool(version)
        rejection = '' if compatible else f'{detected_reason}, but java -version could not be parsed'
        providers.append(ToolProvider(
            'java', 'termux', True, path=root, version=version,
            package=package_name, package_version=package.version if package else '',
            package_manager=package.manager if package else '', runtime=runtime,
            compatible=compatible,
            selection_reason=detected_reason if compatible else '',
            rejection_reason=rejection, capabilities=('java', 'javac'),
            reason=detected_reason if compatible else rejection,
        ))
    return tuple(providers)


@dataclass(frozen=True)
class NativeRequirement:
    capability: str
    reason: str

    @property
    def requires_full_ndk(self) -> bool:
        return self.capability == 'android-ndk'


def normalize_native_provider(value: str | None) -> str:
    normalized = str(value or 'auto').strip().lower() or 'auto'
    aliases = {'termux': 'termux-sysroot', 'full-ndk': 'ndk', 'android-ndk': 'ndk'}
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_NATIVE_PROVIDERS:
        valid = ', '.join(sorted(VALID_NATIVE_PROVIDERS))
        raise ValueError(f'Unsupported native provider: {value}. Expected one of: {valid}')
    return normalized


def normalize_native_cache_provider(value: str | None) -> str:
    normalized = str(value or 'auto').strip().lower() or 'auto'
    if normalized in {'none', 'false', 'disabled'}:
        normalized = 'off'
    if normalized not in VALID_NATIVE_CACHE_PROVIDERS:
        valid = ', '.join(sorted(VALID_NATIVE_CACHE_PROVIDERS))
        raise ValueError(f'Unsupported native cache provider: {value}. Expected one of: {valid}')
    return normalized


def normalize_android_build_tools_provider(value: str | None) -> str:
    normalized = str(value or 'auto').strip().lower() or 'auto'
    aliases = {'download': 'commit451', 'downloaded': 'commit451', 'android-sdk': 'sdk'}
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_ANDROID_BUILD_TOOLS_PROVIDERS:
        valid = ', '.join(sorted(VALID_ANDROID_BUILD_TOOLS_PROVIDERS))
        raise ValueError(f'Unsupported Android build-tools provider: {value}. Expected one of: {valid}')
    return normalized


def _termux_binary_probe(
    capability: str,
    names: Sequence[str],
    *,
    package: str = '',
    version_args: Sequence[str] = ('--version',),
    version_pattern: str = r'([0-9]+(?:\.[0-9A-Za-z_-]+)+)',
    require_version: bool = True,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> ToolProvider:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return ToolProvider(capability, 'termux', False, runtime=runtime, compatible=False, rejection_reason='not running in native Termux', reason='not running in native Termux')
    path_value = str(env.get('PATH', '') or '')
    found = next((shutil.which(name, path=path_value) for name in names if shutil.which(name, path=path_value)), None)
    if not found:
        return ToolProvider(capability, 'termux', False, package=package, runtime=runtime, compatible=False, rejection_reason=f'{"/".join(names)} not found on PATH', reason=f'{"/".join(names)} not found on PATH')
    resolved = Path(found).expanduser().resolve(strict=False)
    if runtime.prefix is None or not _path_is_within(resolved, runtime.prefix):
        reason = f'{capability} resolves outside Termux PREFIX: {resolved}'
        return ToolProvider(capability, 'termux', False, path=resolved, package=package, runtime=runtime, compatible=False, rejection_reason=reason, reason=reason)
    package_probe = termux_package_probe(package, env=env, runtime=runtime) if package else PackageProbe('', False)
    version = _tool_version([str(resolved), *version_args], version_pattern, env=env) if version_args else ''
    if not require_version:
        probe_args = tuple(version_args) or ('--help',)
        try:
            probe = subprocess.run(
                [str(resolved), *probe_args], text=True, capture_output=True, check=False, timeout=5, env=dict(env),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            reason = f'Termux {capability} runtime probe failed: {exc}'
            return ToolProvider(capability, 'termux', True, path=resolved, package=package, package_version=package_probe.version, package_manager=package_probe.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=(capability,), reason=reason)
        if probe.returncode in {126, 127}:
            reason = f'Termux {capability} runtime probe exited {probe.returncode}'
            return ToolProvider(capability, 'termux', True, path=resolved, package=package, package_version=package_probe.version, package_manager=package_probe.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=(capability,), reason=reason)
        if not version:
            version = package_probe.version
    if not version and require_version:
        reason = f'Termux {capability} detected at {resolved}, but version probe could not be parsed'
        return ToolProvider(capability, 'termux', True, path=resolved, package=package, package_version=package_probe.version, package_manager=package_probe.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=(capability,), reason=reason)
    reason = f'Termux {capability} detected at {resolved}'
    return ToolProvider(capability, 'termux', True, path=resolved, version=version, package=package, package_version=package_probe.version, package_manager=package_probe.manager, runtime=runtime, compatible=True, selection_reason=reason, capabilities=(capability,), reason=reason)


def termux_cmake_probe(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> ToolProvider:
    return _termux_binary_probe('cmake', ('cmake',), package='cmake', version_pattern=r'cmake version\s+([^\s]+)', env=env, runtime=runtime)


def termux_ninja_probe(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> ToolProvider:
    return _termux_binary_probe('ninja', ('ninja', 'ninja-build'), package='ninja', version_pattern=r'^([^\s]+)', env=env, runtime=runtime)


def termux_ccache_probe(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> ToolProvider:
    return _termux_binary_probe('ccache', ('ccache',), package='ccache', version_pattern=r'ccache version\s+([^\s]+)', env=env, runtime=runtime)


def termux_native_provider(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> ToolProvider:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return ToolProvider('native-compiler', 'termux-sysroot', False, runtime=runtime, compatible=False, rejection_reason='not running in native Termux', reason='not running in native Termux')
    prefix = runtime.prefix or termux_prefix(env)
    required = ('clang', 'clang++', 'ld.lld', 'llvm-ar', 'llvm-ranlib')
    missing: list[str] = []
    paths: dict[str, Path] = {}
    path_value = str(env.get('PATH', '') or '')
    for name in required:
        found = shutil.which(name, path=path_value)
        if not found:
            missing.append(name)
            continue
        resolved = Path(found).expanduser().resolve(strict=False)
        if not _path_is_within(resolved, prefix):
            missing.append(f'{name} (outside PREFIX)')
            continue
        paths[name] = resolved
    sysroot = termux_package_probe('ndk-sysroot', env=env, runtime=runtime)
    clang_pkg = termux_package_probe('clang', env=env, runtime=runtime)
    if missing or not sysroot.installed:
        details = []
        if missing:
            details.append('missing ' + ', '.join(missing))
        if not sysroot.installed:
            details.append('ndk-sysroot package is not installed')
        reason = 'Termux native compiler provider incomplete: ' + '; '.join(details)
        return ToolProvider('native-compiler', 'termux-sysroot', bool(paths), path=paths.get('clang'), package='ndk-sysroot', package_version=sysroot.version, package_manager=sysroot.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=tuple(paths), reason=reason)
    version = _tool_version([str(paths['clang']), '--version'], r'(?:clang version|clang version\s*)([^\s]+)', env=env)
    if not version:
        reason = 'Termux clang --version could not be parsed'
        return ToolProvider('native-compiler', 'termux-sysroot', True, path=paths['clang'], package='ndk-sysroot', package_version=sysroot.version, package_manager=sysroot.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=required, reason=reason)
    unusable: list[str] = []
    for name in required:
        try:
            result = subprocess.run(
                [str(paths[name]), '--version'], text=True, capture_output=True, check=False, timeout=5, env=dict(env),
            )
        except (OSError, subprocess.TimeoutExpired):
            unusable.append(name)
            continue
        if result.returncode != 0:
            unusable.append(name)
    if unusable:
        reason = 'Termux native tools failed runtime probes: ' + ', '.join(unusable)
        return ToolProvider('native-compiler', 'termux-sysroot', True, path=paths['clang'], version=version, package='ndk-sysroot', package_version=sysroot.version, package_manager=sysroot.manager, runtime=runtime, compatible=False, rejection_reason=reason, capabilities=required, reason=reason)
    return ToolProvider('native-compiler', 'termux-sysroot', True, path=paths['clang'], version=version, package='ndk-sysroot', package_version=sysroot.version, package_manager=sysroot.manager, runtime=runtime, compatible=True, selection_reason='Termux clang/lld with ndk-sysroot is complete', capabilities=required, reason='Termux clang/lld with ndk-sysroot is complete')


_TERMUX_ANDROID_TOOL_PACKAGES = {
    'aapt': 'aapt',
    'aapt2': 'aapt',
    'apksigner': 'apksigner',
    'adb': 'android-tools',
    'aidl': 'aapt',
    'zipalign': 'aapt',
    'split-select': 'aapt',
}


def termux_android_tool_probe(name: str, *, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> ToolProvider:
    package = _TERMUX_ANDROID_TOOL_PACKAGES.get(name, '')
    patterns = {
        'aapt': r'v?([0-9]+(?:\.[0-9A-Za-z_-]+)+)',
        'aapt2': r'v?([0-9]+(?:\.[0-9A-Za-z_-]+)+)',
        'apksigner': r'([0-9]+(?:\.[0-9A-Za-z_-]+)+)',
        'adb': r'Version\s+([^\s]+)',
    }
    versioned = name in {'adb', 'aapt', 'aapt2', 'apksigner'}
    helper_args = {'aidl': ('--version',), 'zipalign': ('-h',), 'split-select': ('--help',)}
    args = ('version',) if name in {'adb', 'aapt', 'aapt2'} else ('--version',) if versioned else helper_args.get(name, ('--help',))
    return _termux_binary_probe(
        name,
        (name,),
        package=package,
        version_args=args,
        version_pattern=patterns.get(name, r'([0-9]+(?:\.[0-9A-Za-z_-]+)+)'),
        require_version=versioned,
        env=env,
        runtime=runtime,
    )


def termux_android_tool_providers(*, env: Mapping[str, str] | None = None, runtime: RuntimeIdentity | None = None) -> tuple[ToolProvider, ...]:
    return tuple(termux_android_tool_probe(name, env=env, runtime=runtime) for name in ('aapt', 'aapt2', 'apksigner', 'adb', 'aidl', 'zipalign', 'split-select'))


def active_gradle_config_text(project_root: Path) -> str:
    """Return Gradle/properties text with non-active comments removed.

    Provider classification and the Android build inspector must consume the
    same view; otherwise diagnostics can say "no NDK" while the real build
    provisions one because a marker only existed in a comment.
    """

    root = Path(project_root).expanduser()
    chunks: list[str] = []
    for path in _iter_project_files(root):
        name = path.name
        if name != 'gradle.properties' and not (name.endswith('.gradle') or name.endswith('.gradle.kts')):
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if name == 'gradle.properties':
            text = re.sub(r'(?m)^[ \t]*[#!].*$', '', text)
        else:
            text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
            text = re.sub(r'(?m)//.*$', '', text)
        chunks.append(text)
    return '\n'.join(chunks)


def classify_native_requirement(project_root: Path, *, android: bool = False) -> NativeRequirement:
    root = Path(project_root).expanduser()
    text = active_gradle_config_text(root)
    # A CMakeLists.txt by itself is not enough to prove Android NDK integration:
    # repositories often contain host-side helpers or third-party CMake projects.
    # Require an Android/Gradle NDK declaration or an ndk-build makefile instead.
    full_ndk_markers = ('externalNativeBuild', 'ndkVersion', 'ndk-build', 'android.toolchain.cmake')
    has_ndk_makefiles = False
    has_compiler_inputs = False
    for path in _iter_project_files(root):
        name = path.name
        if name in {'Android.mk', 'Application.mk'}:
            has_ndk_makefiles = True
        if name == 'CMakeLists.txt' or path.suffix.lower() in {'.c', '.cc', '.cpp'}:
            has_compiler_inputs = True
        if has_ndk_makefiles and has_compiler_inputs:
            break
    if android and (any(marker in text for marker in full_ndk_markers) or has_ndk_makefiles):
        return NativeRequirement('android-ndk', 'Android native build integration requires a complete Android NDK')
    if has_compiler_inputs:
        return NativeRequirement('compiler', 'project contains native compiler inputs without Android NDK integration')
    return NativeRequirement('none', 'no native compiler requirement detected')

def resolve_provider_decisions(
    project_root: Path,
    *,
    android: bool = False,
    env: Mapping[str, str] | None = None,
    probes: Mapping[str, object] | None = None,
) -> ProviderDecisionEvidence:
    """Resolve one immutable provider-decision snapshot.

    ``probes`` exists for deterministic tests/adapters; production callers use
    the exact same resolver and default provider probes as real builds.
    """
    env = env or os.environ
    probes = probes or {}
    runtime_probe = probes.get("runtime") or detect_runtime_identity
    wrapper_probe = probes.get("wrapper") or gradle_wrapper_probe
    gradle_probe = probes.get("gradle") or termux_gradle_probe
    java_probe = probes.get("java") or termux_java_providers
    native_probe = probes.get("native") or termux_native_provider
    requirement_probe = probes.get("native_requirement") or classify_native_requirement
    cmake_probe = probes.get("cmake") or termux_cmake_probe
    ninja_probe = probes.get("ninja") or termux_ninja_probe
    ccache_probe = probes.get("ccache") or termux_ccache_probe
    android_probe = probes.get("android_tools") or termux_android_tool_providers
    runtime = runtime_probe(env)
    root = Path(project_root).expanduser()
    return ProviderDecisionEvidence(
        runtime=runtime,
        wrapper=wrapper_probe(root),
        gradle=gradle_probe(env=env, runtime=runtime),
        java=java_probe(env=env, runtime=runtime),
        native=native_probe(env=env, runtime=runtime),
        native_requirement=requirement_probe(root, android=android),
        cmake=cmake_probe(env=env, runtime=runtime),
        ninja=ninja_probe(env=env, runtime=runtime),
        ccache=ccache_probe(env=env, runtime=runtime),
        android_tools=android_probe(env=env, runtime=runtime),
    )


@dataclass(frozen=True)
class GradleWrapperProbe:
    root: Path
    launcher: Path
    properties: Path
    jar: Path
    present: bool
    complete: bool
    missing: tuple[str, ...]


def gradle_wrapper_probe(project_root: Path) -> GradleWrapperProbe:
    root = Path(project_root).expanduser()
    launcher = root / 'gradlew'
    properties = root / 'gradle' / 'wrapper' / 'gradle-wrapper.properties'
    jar = root / 'gradle' / 'wrapper' / 'gradle-wrapper.jar'
    files = (
        ('gradlew', launcher),
        ('gradle/wrapper/gradle-wrapper.properties', properties),
        ('gradle/wrapper/gradle-wrapper.jar', jar),
    )
    present = any(path.exists() for _, path in files)
    missing = tuple(label for label, path in files if not path.is_file())
    return GradleWrapperProbe(
        root=root,
        launcher=launcher,
        properties=properties,
        jar=jar,
        present=present,
        complete=present and not missing,
        missing=missing,
    )


def termux_installed_packages(
    pattern: str,
    *,
    env: Mapping[str, str] | None = None,
    runtime: RuntimeIdentity | None = None,
) -> tuple[PackageProbe, ...]:
    env = env or os.environ
    runtime = runtime or detect_runtime_identity(env)
    if not runtime.is_termux:
        return ()
    path = str(env.get('PATH', '') or '')
    dpkg_query = shutil.which('dpkg-query', path=path)
    if not dpkg_query:
        return ()
    query = pattern if any(token in pattern for token in '*?[') else f'{pattern}*'
    try:
        result = subprocess.run(
            [dpkg_query, '-W', '-f=${binary:Package}\t${Status}\t${Version}\n', query],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
            env=dict(env),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    packages: list[PackageProbe] = []
    for line in result.stdout.splitlines():
        name, separator, remainder = line.partition('\t')
        if not separator:
            continue
        status, separator, version = remainder.partition('\t')
        if not separator or 'install ok installed' not in status:
            continue
        clean_name = name.split(':', 1)[0].strip()
        if clean_name:
            packages.append(PackageProbe(clean_name, True, version.strip(), 'dpkg'))
    return tuple(sorted(packages, key=lambda package: package.name))
