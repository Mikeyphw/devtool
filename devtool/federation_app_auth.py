"""Opt-in, repository-scoped GitHub App credentials for federation source fetch.

Reuses the existing Devtool M4 App provider and the operator's private Termux
configuration. No PAT, token, JWT or private key is serialized to a source lock,
journal, subprocess argv, origin, diagnostics, or clipboard.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
from typing import Mapping

from .github_app import AppInstallationProvider, GitHubAppError, run_git_authenticated
from .federation_lock import git_remote_locator

_NAMES = ('ID', 'INSTALLATION_ID', 'ACCOUNT', 'PRIVATE_KEY_PATH')
_KEYS = tuple('DEVTOOL_GITHUB_APP_' + key for key in _NAMES)
_LOCATOR = re.compile(
    r'https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/'
    r'([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?\Z', re.I,
)


def github_slug_for_locator(locator: str) -> str:
    """Refuse SSH, embedded credentials, rewrites, fragments and other hosts."""
    if not isinstance(locator, str) or len(locator) > 256:
        raise GitHubAppError('APP_FEDERATION_EXACT_HTTPS_GITHUB_ORIGIN_REQUIRED')
    found = _LOCATOR.fullmatch(locator)
    if not found or '..' in found.group(2).split('/') or any(c in locator for c in ('?', '#', '@', '\n', '\r', '\x00')):
        raise GitHubAppError('APP_FEDERATION_EXACT_HTTPS_GITHUB_ORIGIN_REQUIRED')
    return found.group(1) + '/' + found.group(2)


def _read_qualifed_local_env(path: Path) -> dict[str, str]:
    """Read only literal `export KEY=value` lines; never execute a shell file."""
    directory = path.parent
    if path.is_symlink() or directory.is_symlink():
        raise GitHubAppError('APP_FEDERATION_CONFIG_SYMLINK_REFUSED')
    try:
        ds = directory.stat()
        fs = path.stat()
    except OSError:
        raise GitHubAppError('APP_FEDERATION_CONFIG_UNAVAILABLE') from None
    if (not stat.S_ISDIR(ds.st_mode) or ds.st_uid != os.getuid()
            or ds.st_mode & 0o077
            or not stat.S_ISREG(fs.st_mode) or fs.st_uid != os.getuid()
            or fs.st_mode & 0o077 or not 1 <= fs.st_size <= 4096):
        raise GitHubAppError('APP_FEDERATION_CONFIG_UNSAFE')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise GitHubAppError('APP_FEDERATION_CONFIG_UNAVAILABLE') from None
    try:
        actual = os.fstat(fd)
        if (actual.st_ino != fs.st_ino or actual.st_dev != fs.st_dev
                or not stat.S_ISREG(actual.st_mode) or actual.st_uid != os.getuid()
                or actual.st_mode & 0o077 or actual.st_size != fs.st_size):
            raise GitHubAppError('APP_FEDERATION_CONFIG_CHANGED')
        data = os.read(fd, 4097)
    finally:
        os.close(fd)
    if len(data) != fs.st_size:
        raise GitHubAppError('APP_FEDERATION_CONFIG_CHANGED')
    try:
        lines = data.decode('utf-8').splitlines()
    except UnicodeError:
        raise GitHubAppError('APP_FEDERATION_CONFIG_UNSAFE') from None
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError:
            raise GitHubAppError('APP_FEDERATION_CONFIG_SYNTAX') from None
        if len(tokens) != 2 or tokens[0] != 'export' or '=' not in tokens[1]:
            raise GitHubAppError('APP_FEDERATION_CONFIG_SYNTAX')
        key, value = tokens[1].split('=', 1)
        if key not in _KEYS or key in result or not value:
            raise GitHubAppError('APP_FEDERATION_CONFIG_FIELDS_REFUSED')
        # shlex never executes substitutions. Reject shell metacharacters anyway
        # so this configuration can only contain static values.
        if any(c in value for c in ('$','`','\n','\r','\x00',';','|','&','<','>')):
            raise GitHubAppError('APP_FEDERATION_CONFIG_NONLITERAL_VALUE')
        result[key] = value
    if set(result) != set(_KEYS):
        raise GitHubAppError('APP_FEDERATION_CONFIG_INCOMPLETE')
    return result


def installed_app_environment(environ: Mapping[str, str] | None = None,
                              *, config: Path | None = None) -> dict[str, str]:
    """Prefer explicit complete vars; otherwise read existing private env.sh."""
    env = os.environ if environ is None else environ
    present = [key for key in _KEYS if env.get(key)]
    if present:
        if len(present) != len(_KEYS):
            raise GitHubAppError('APP_FEDERATION_ENV_PARTIAL')
        return {key: str(env[key]) for key in _KEYS}
    path = config or Path.home()/'.config/devtool/github-app/env.sh'
    return _read_qualifed_local_env(path)


def fetch_from_github_app(root: str | Path, locked_locator: str) -> subprocess.CompletedProcess[str]:
    """One exact source-lock-scoped Git fetch, no implicit PAT or SSH fallback."""
    root = Path(root).resolve()
    slug = github_slug_for_locator(locked_locator)
    if git_remote_locator(root) != locked_locator:
        raise GitHubAppError('APP_FEDERATION_REMOTE_MISMATCH')
    provider = AppInstallationProvider.from_environment(installed_app_environment())
    credential = provider.issue(slug, 'repository_read')
    return run_git_authenticated(root, ['fetch', '--prune', 'origin'], credential, timeout=120)
