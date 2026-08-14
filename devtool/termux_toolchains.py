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
