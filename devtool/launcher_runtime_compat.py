"""UX-RICH01: fail-closed, versioned *runtime* compatibility only.

Immutable package target HEAD/tree/blobs and payload are never relaxed.
Runtime Git origin, current HEAD/tree, committed module/schema blobs, current
worktree bytes and launcher ABI are re-attested.  Exact build-time runtime
pins remain recorded for provenance, not silently replaced.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Any, Mapping

SEMVER = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')


class IncompatibleLauncherRuntime(ValueError):
    pass


def parse_version(raw: Any) -> tuple[int, int, int]:
    if not isinstance(raw, str) or len(raw) > 32:
        raise IncompatibleLauncherRuntime('Launcher version must be an explicit three-component version')
    match = SEMVER.fullmatch(raw)
    if match is None:
        raise IncompatibleLauncherRuntime('Launcher version is missing or malformed')
    return tuple(int(x) for x in match.groups())


def version_compatible(actual: Any, minimum: Any, major: Any) -> bool:
    """Allow >= minimum within one *exact* major; never accept major+1."""
    a, m = parse_version(actual), parse_version(minimum)
    if type(major) is not int or major < 1 or major != m[0]:
        raise IncompatibleLauncherRuntime('Invalid immutable major-version compatibility bound')
    return a[0] == major and a >= m


def require_version(actual: Any, requirement: Mapping[str, Any]) -> tuple[int,int,int]:
    if not isinstance(requirement, Mapping) or set(requirement) != {'minimum', 'major'}:
        raise IncompatibleLauncherRuntime('Missing or unknown immutable launcher compatibility contract')
    if not version_compatible(actual, requirement['minimum'], requirement['major']):
        raise IncompatibleLauncherRuntime(
            'DEVTOOL_LAUNCHER_INCOMPATIBLE expected >= '+str(requirement['minimum'])+
            ' within major '+str(requirement['major'])+'.x, actual='+str(actual)+
            '; no module imported or native operation attempted')
    return parse_version(actual)


def declared_version(module_path: Path) -> str:
    """Read a literal VERSION marker; do not execute unverified source code."""
    path = Path(module_path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise IncompatibleLauncherRuntime('Unsafe launcher module version source')
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    except (UnicodeError, OSError, SyntaxError) as exc:
        raise IncompatibleLauncherRuntime('Cannot parse committed launcher module version') from exc
    markers = [node.value for node in tree.body
               if isinstance(node, ast.Assign) and
               any(isinstance(x,ast.Name) and x.id == 'LAUNCHER_CONTRACT_VERSION'
                   for x in node.targets)]
    if len(markers) != 1:
        raise IncompatibleLauncherRuntime('Exactly one literal launcher version marker required')
    try:
        value = ast.literal_eval(markers[0])
    except (ValueError, TypeError, RecursionError, SyntaxError) as exc:
        raise IncompatibleLauncherRuntime('Dynamic/nonliteral launcher version marker refused') from exc
    parse_version(value)
    return value


def declared_major(module_path: Path) -> int:
    return parse_version(declared_version(module_path))[0]


__all__ = ['parse_version','version_compatible','require_version',
           'declared_version','declared_major','IncompatibleLauncherRuntime']
