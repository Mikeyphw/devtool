"""GA-00: conservative, private, intent-before-write GitHub secret journal.

An Actions secret value is write-only. A successful upload can prove that the
provider acknowledged an operation and that a secret *name* exists. It cannot
prove the remote value equals the intended signer. Never automatically retry
an unresolved network write, even if the name appears in GitHub metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
from typing import Any, Iterator
from uuid import uuid4


class SecretJournalRefusal(ValueError):
    """Public, secret-free error message."""


_ALLOWED_EVENTS = frozenset({'INTENT', 'ACK', 'METADATA_PRESENT'})
_MAX_BYTES = 1024 * 1024


def journal_path(policy_path: Path) -> Path:
    return policy_path.with_name('.' + policy_path.name + '.devtool-secret-operations.jsonl')


class SecretJournal:
    def __init__(self, fd: int):
        self.fd = fd
        self.records = self._load()

    def _load(self) -> list[dict[str, Any]]:
        size = os.fstat(self.fd).st_size
        if size > _MAX_BYTES:
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_TOO_LARGE')
        os.lseek(self.fd, 0, os.SEEK_SET)
        data = os.read(self.fd, size)
        if not data:
            return []
        if not data.endswith(b'\n'):
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_TRUNCATED')
        try:
            records = [json.loads(line) for line in data.splitlines()]
        except (ValueError, UnicodeError):
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_MALFORMED') from None
        if any(not isinstance(x, dict) or x.get('schema') != 1
               or x.get('event') not in _ALLOWED_EVENTS for x in records):
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_INVALID')
        return records

    def unresolved(self) -> list[str]:
        """Return safe name-only diagnostics for operations lacking proof."""
        stages: dict[tuple[str, str], set[str]] = {}
        for record in self.records:
            key = (str(record.get('operation_id', '')), str(record.get('name', '')))
            stages.setdefault(key, set()).add(str(record['event']))
        return sorted({name for (_, name), seen in stages.items()
                       if 'INTENT' in seen and 'METADATA_PRESENT' not in seen})

    def new_operation(self) -> str:
        if self.unresolved():
            raise SecretJournalRefusal('GA00_SECRET_RECONCILIATION_REQUIRED')
        return uuid4().hex

    def record(self, *, event: str, operation_id: str, name: str,
               repository: str, source_sha: str, signer_sha256: str) -> None:
        if event not in _ALLOWED_EVENTS or len(operation_id) != 32 or not operation_id.isalnum():
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_EVENT_INVALID')
        if not name.startswith('DEVTOOL_RELEASE_') or any(x in name for x in '\r\n= '):
            raise SecretJournalRefusal('GA00_SECRET_NAME_INVALID')
        item = {'schema': 1, 'event': event, 'operation_id': operation_id, 'name': name,
                'repository': repository, 'source_sha': source_sha,
                'signer_sha256': signer_sha256}
        data = (json.dumps(item, sort_keys=True, separators=(',', ':')) + '\n').encode('utf-8')
        if os.fstat(self.fd).st_size + len(data) > _MAX_BYTES:
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_TOO_LARGE')
        os.lseek(self.fd, 0, os.SEEK_END)
        if os.write(self.fd, data) != len(data):
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_SHORT_WRITE')
        os.fsync(self.fd)
        self.records.append(item)


@contextmanager
def opened_journal(policy_path: Path) -> Iterator[SecretJournal]:
    """One owner-only lock held across the entire secret mutation sequence."""
    path = journal_path(policy_path)
    parent = path.parent
    if parent.is_symlink():
        raise SecretJournalRefusal('GA00_SECRET_JOURNAL_PARENT_SYMLINK')
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    owner = parent.stat()
    if not stat.S_ISDIR(owner.st_mode) or owner.st_uid != os.getuid() or owner.st_mode & 0o022:
        raise SecretJournalRefusal('GA00_SECRET_JOURNAL_PARENT_UNSAFE')
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except OSError:
        raise SecretJournalRefusal('GA00_SECRET_JOURNAL_UNAVAILABLE') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_UNSAFE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            raise SecretJournalRefusal('GA00_SECRET_JOURNAL_BUSY') from None
        yield SecretJournal(fd)
    finally:
        os.close(fd)
