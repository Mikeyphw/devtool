"""Read-only PackageManager package presence; never a broker identity authority.

AOSP `pm path` may exit 1 for absence, which must not be confused with
permission/service errors. Require an independent exact package enumeration
on the *same* explicit ADB transport and Android user.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from .github_actions import GitHubActionsError

_PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\Z")
_SERIAL = re.compile(r"[A-Za-z0-9_.~:-]{1,128}\Z")
_PATH = re.compile(r"package:(/[A-Za-z0-9_.~@+/=-]+)\Z")


def classify_pm_presence(package: str, path_code: int, path_out: str, path_err: str,
                         list_code: int, list_out: str, list_err: str) -> dict[str, Any]:
    if not _PACKAGE.fullmatch(package):
        raise GitHubActionsError("PackageManager package identifier is invalid")
    if (list_code != 0 or list_err.strip() or
            any(line.strip() not in ('', 'package:' + package) for line in list_out.splitlines())):
        raise GitHubActionsError("PackageManager independent package enumeration failed or is ambiguous")
    listed = [line.strip() for line in list_out.splitlines() if line.strip()]
    if len(listed) > 1:
        raise GitHubActionsError("PackageManager returned a duplicate package listing")
    paths = [line.strip() for line in path_out.splitlines() if line.strip()]
    if (path_code == 1 and not path_out.strip() and not path_err.strip() and not listed):
        return {"presence": "absent", "path_count": 0,
                "pm_path_exit": 1, "pm_list_exit": 0, "trusted_broker_identity": False}
    if path_code != 0 or path_err.strip() or not paths or not listed:
        raise GitHubActionsError("PackageManager path/list evidence is contradictory or incomplete")
    if len(set(paths)) != len(paths) or any(not _PATH.fullmatch(line) for line in paths):
        raise GitHubActionsError("PackageManager returned unsafe or duplicate package paths")
    return {"presence": "present", "path_count": len(paths),
            "pm_path_exit": 0, "pm_list_exit": 0, "trusted_broker_identity": False}


def query_package_presence(serial: str, package: str, *, android_user: int = 0,
                           adb: str = 'adb') -> dict[str, Any]:
    """Observe same-user/same-transport PM results without changing device state."""
    if not isinstance(serial, str) or not _SERIAL.fullmatch(serial) or not _PACKAGE.fullmatch(package):
        raise GitHubActionsError("Read-only PackageManager query requires exact ADB transport and package")
    if type(android_user) is not int or not 0 <= android_user <= 9999:
        raise GitHubActionsError("Android user ID is invalid")
    executable = shutil.which(adb)
    if not executable:
        raise GitHubActionsError("ADB executable unavailable for read-only PM observation")
    commands = ([executable, '-s', serial, 'shell', 'pm', 'path', '--user', str(android_user), package],
                [executable, '-s', serial, 'shell', 'pm', 'list', 'packages', '--user', str(android_user), package])
    observations = []
    for command in commands:
        try:
            proc = subprocess.run(command, capture_output=True, text=True, check=False, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            raise GitHubActionsError("Read-only PackageManager query failed or timed out") from None
        observations.append((proc.returncode, proc.stdout, proc.stderr))
    result = classify_pm_presence(package, *observations[0], *observations[1])
    result.update({"android_user": android_user, "adb_transport": serial,
                   "read_only": True, "device_qualified": False})
    return result
