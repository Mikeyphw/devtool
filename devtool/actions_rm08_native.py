"""RM08 manager-native provisioning with hard offline/refusal semantics.

`uv` offers its own offline resolver. Poetry/PDM have no reviewed equivalent
proving network impossibility on all hosts, so their execution requires a real
Linux user+network namespace. Absence of that capability refuses the node;
legacy exported-hash pip installs remain a separate, explicitly nonnative mode.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import venv
from typing import Any

from .actions_language_providers import ProviderError


_MANAGERS = {'uv': 'uv', 'poetry': 'poetry', 'pdm': 'pdm'}


def native_command(manager: str, version: str, python: str) -> list[str]:
    if manager == 'uv' and re.fullmatch(r'0\.\d{1,3}\.\d{1,3}', version):
        return ['uv', 'sync', '--frozen', '--offline', '--no-install-project', '--python', python]
    if manager == 'poetry' and re.fullmatch(r'2\.\d{1,3}\.\d{1,3}', version):
        return ['unshare','--user','--map-root-user','--net','--',
                'poetry','sync','--no-root','--no-interaction','--no-ansi']
    if manager == 'pdm' and re.fullmatch(r'2\.\d{1,3}\.\d{1,3}', version):
        return ['unshare','--user','--map-root-user','--net','--',
                'pdm','sync','--no-self','--frozen-lockfile','--no-isolation']
    raise ProviderError('RM-08 unsupported native manager/version combination')


def _native_namespace_host_supported() -> bool:
    """Gate native network namespaces; independently mockable on Android tests."""
    return sys.platform == 'linux' and os.name == 'posix'


def provision_python_manager(root: Path, selected: dict[str, Any], specs: list[Any]) -> list[Any]:
    from .actions_rm08_projects import python_project_manifest, ProjectReviewError

    project = selected.get('python_project') or {}
    manager = str(project.get('manager') or '')
    version = selected.get('python_native_version') or ''
    if (selected['provider'] != 'python-shell' or len(specs) != 1 or
            specs[0].argv[:2] not in (['python','-m'],['python3','-m'])):
        raise ProviderError('RM-08 native manager requires one reviewed Python module job')
    if manager not in _MANAGERS:
        raise ProviderError('RM-08 native manager identity is not reviewed')
    native_command(manager, version, sys.executable)  # Validate pair before invoking tools.
    if manager != 'uv' and not _native_namespace_host_supported():
        raise ProviderError('RM-08 Poetry/PDM require supported Linux network namespace')
    try:
        current = python_project_manifest(root, require_package=True, native_manager=True)
    except ProjectReviewError as exc:
        raise ProviderError(str(exc)) from None
    if current['source_sha256'] != project.get('source_sha256') or current['manager'] != manager:
        raise ProviderError('RM-08 native manager project/lock identity drifted')
    try:
        probe = subprocess.run([manager, '--version'], capture_output=True, text=True, timeout=12,
                               check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError('RM-08 reviewed Python manager executable unavailable') from None
    formats = {'uv': 'uv '+version, 'poetry': 'Poetry (version '+version+')',
               'pdm': 'PDM, version '+version}
    if probe.returncode or probe.stdout.strip() != formats[manager]:
        raise ProviderError('RM-08 installed Python manager version differs from reviewed pin')
    base = Path(os.environ.get('RUNNER_TEMP') or tempfile.gettempdir()).resolve()
    work = base / ('devtool-rm08-'+selected['id'])
    if work.exists() or work.is_symlink():
        raise ProviderError('RM-08 native environment already exists; refusing reuse')
    # Cache is content-only and fixed outside the project; user configuration,
    # index credentials, build scripts and virtualenvs never enter the cache.
    cache = base / 'devtool-python-wheel-store'
    controlled = dict(os.environ)
    controlled.update({'PIP_NO_INDEX':'1', 'PIP_DISABLE_PIP_VERSION_CHECK':'1',
                       'UV_OFFLINE':'true','UV_PYTHON_DOWNLOADS':'never','UV_NO_PROGRESS':'true',
                       'UV_CACHE_DIR':str(cache),'POETRY_CACHE_DIR':str(cache),
                       'PDM_CACHE_DIR':str(cache), 'POETRY_NO_INTERACTION':'1',
                       'POETRY_VIRTUALENVS_CREATE':'false','PDM_USE_VENV':'true',
                       'PDM_IGNORE_SAVED_PYTHON':'1','PDM_PYPI_IGNORE_STORED_INDEX':'true'})
    if manager == 'uv':
        controlled['UV_PROJECT_ENVIRONMENT'] = str(work)
    else:
        # Never run Poetry/PDM's installer outside a network namespace, even
        # in a supposedly offline runner. An unavailable `unshare` fails closed.
        try:
            venv.EnvBuilder(with_pip=True, clear=False).create(work)
        except (OSError, RuntimeError, subprocess.CalledProcessError):
            raise ProviderError('RM-08 could not create clean native manager venv') from None
        controlled['VIRTUAL_ENV'] = str(work)
        controlled['PATH'] = str(work/'bin') + os.pathsep + controlled.get('PATH','')
    command = native_command(manager,version,sys.executable)
    try:
        installed = subprocess.run(command, cwd=root, env=controlled,
                                   capture_output=True,text=True, timeout=240, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderError('RM-08 native manager offline provisioning unavailable/timed out') from None
    if installed.returncode:
        if manager == 'uv':
            raise ProviderError('RM-08 native frozen/offline uv sync failed; no owned output accepted')
        raise ProviderError('RM-08 native '+manager+' offline provisioning failed; job/output refused')
    # A manager may not rewrite tracked manifests or bypass the reviewed lock.
    try:
        after = python_project_manifest(root, require_package=True, native_manager=True)
    except ProjectReviewError as exc:
        raise ProviderError('RM-08 native project/lock drift after provisioning: '+str(exc)) from None
    if after['source_sha256'] != project['source_sha256']:
        raise ProviderError('RM-08 native manager changed reviewed project/lock source')
    exe = work / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not exe.is_file() or exe.is_symlink():
        raise ProviderError('RM-08 native manager environment missing interpreter')
    updated = dict(specs[0].env)
    updated.update({'PIP_NO_INDEX':'1','UV_OFFLINE':'true','UV_PYTHON_DOWNLOADS':'never',
                    'VIRTUAL_ENV':str(work), 'UV_PROJECT_ENVIRONMENT':str(work)})
    return [replace(specs[0],argv=[str(exe), *specs[0].argv[1:]],env=updated)]
