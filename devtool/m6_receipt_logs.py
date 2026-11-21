"""Read-only, explicitly requested, bounded native log paging for M6.

This is a display helper, never a log discovery, rotation, or execution authority.
Native receipt references are advisory until checked against a managed log root.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import os
import stat
from typing import Any

_LOG_KEYS = ('log_path', 'log_file', 'evidence_log_file')
_NESTED_ROWS = ('validation_steps', 'child_outcomes', 'hook_events', 'events', 'outputs', 'results')
_MAX_BYTES = 16 * 1024 * 1024
_PAGE_LINES = 28


def receipt_log_refs(*records: Mapping[str, Any]) -> tuple[str, ...]:
    """Only paths explicitly present in bound native receipts, never grep/discover."""
    found: list[str] = []
    def add_fields(record: Mapping[str, Any]) -> None:
        for key in _LOG_KEYS:
            value = record.get(key)
            if isinstance(value, str) and value.startswith('/') and '\x00' not in value and value not in found:
                found.append(value)
    for record in records:
        if not isinstance(record, Mapping):
            continue
        add_fields(record)
        for name in _NESTED_ROWS:
            rows = record.get(name)
            if isinstance(rows, (list, tuple)):
                for row in rows[:100]:
                    if isinstance(row, Mapping):
                        add_fields(row)
        plan = record.get('validation_plan')
        if isinstance(plan, Mapping):
            outcomes = plan.get('validation_step_outcomes')
            if isinstance(outcomes, (list, tuple)):
                for row in outcomes[:100]:
                    if isinstance(row, Mapping):
                        add_fields(row)
        if len(found) >= 16:
            break
    return tuple(found[:16])


def _managed_roots(repo: Path) -> tuple[Path, ...]:
    # Only log directories, not arbitrary source files or journal contents.
    roots = [repo / '.devtool' / 'logs', repo / '.devtool' / 'validation' / 'logs']
    home_state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state')
    roots.append(home_state / 'devtool' / 'logs')
    xdg = os.environ.get('DEVTOOL_XDG_DIR')
    if xdg and Path(xdg).is_absolute():
        roots.append(Path(xdg) / 'logs')
    override = os.environ.get('DEVTOOL_LOG_DIR')
    if override and Path(override).is_absolute():
        roots.append(Path(override))
    return tuple(roots)


def _open_managed_file(repo: Path, path: str) -> int:
    supplied = Path(path)
    if not supplied.is_absolute() or any(p in ('.', '..') for p in supplied.parts):
        raise ValueError('Invalid native log reference')
    # Resolve the managed *directory*, not a supplied file or symlink.
    # Then traverse every component through O_NOFOLLOW directory FDs.
    for proposed in _managed_roots(repo):
        try:
            root = proposed.resolve(strict=True)
            relative = supplied.relative_to(Path(os.path.abspath(proposed)))
        except (OSError, ValueError):
            continue
        if len(relative.parts) < 1 or any(p in ('', '.', '..') for p in relative.parts):
            continue
        base = os.open(root, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        fd = base
        try:
            for name in relative.parts[:-1]:
                nextfd = os.open(name, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) |
                                 getattr(os, 'O_NOFOLLOW', 0), dir_fd=fd)
                os.close(fd)
                fd = nextfd
            result = os.open(relative.parts[-1], os.O_RDONLY | os.O_NONBLOCK |
                             getattr(os, 'O_NOFOLLOW', 0), dir_fd=fd)
        finally:
            os.close(fd)
        return result
    raise ValueError('Reference is outside managed log directories')


def read_native_log_page(repo: Path, path: str, page: int = 0) -> dict[str, Any]:
    """Open only a bound file; redact the entire bounded record before slicing.

    O_NOFOLLOW prevents symlink reads. No directories are created, and the
    existing native logstore owns all actual log writing and retention.
    """
    fd = _open_managed_file(repo, path)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise ValueError('Native log is not a regular file')
        if meta.st_size > _MAX_BYTES:
            raise ValueError('Native log exceeds safe redaction limit')
        with os.fdopen(fd, 'rb') as handle:
            fd = -1
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError('Native log exceeds safe redaction limit')
    finally:
        if fd >= 0:
            os.close(fd)
    # Mandatory canonical redactor. If unavailable, fail closed; never display
    # unredacted raw journal output. Private-key blocks are handled there.
    from .logstore import redact_sensitive_text
    redacted = redact_sensitive_text(raw.decode('utf-8', errors='replace'))
    if not isinstance(redacted, str):
        raise ValueError('Native redaction did not return text')
    from .m6_live import safe
    lines = redacted.splitlines()
    total = max(1, (len(lines) + _PAGE_LINES - 1) // _PAGE_LINES)
    index = max(0, min(int(page), total - 1))
    return {
        'name': Path(path).name,
        'page': index,
        'pages': total,
        'lines': tuple(safe(line, limit=500) for line in lines[index*_PAGE_LINES:(index+1)*_PAGE_LINES]),
        'bytes': len(raw),
        'source': 'native log reference; explicit local read; redacted preview',
    }


__all__ = ['receipt_log_refs', 'read_native_log_page']
