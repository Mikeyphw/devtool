"""FC-02: source-bound Git object publication and non-destructive reconciliation.

The branch CAS is authoritative. Reconciliation never calls Git's destructive
read-tree -u/checkout/reset; displaced files are escrowed outside the checkout.
A blocked reconcile is not reported as a complete multi-repository adoption.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Callable


class GuardedGitError(ValueError):
    pass


def _run(repo: Path, *args: str, input: bytes | None = None, env: dict | None = None) -> bytes:
    proc = subprocess.run(['git', '-C', str(repo), *args], input=input, env=env,
                          capture_output=True, timeout=40)
    if proc.returncode:
        raise GuardedGitError('git ' + args[0] + ' refused: ' + proc.stderr.decode(errors='replace')[-240:])
    return proc.stdout.strip()


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def pinned_commit(repo: Path, source: str, changes: dict[str, bytes | None], message: bytes,
                  *, mode_for: Callable[[str], str] | None = None) -> tuple[str, str]:
    """Construct a commit in an isolated index; HEAD and worktree stay untouched."""
    fd, idx = tempfile.mkstemp(prefix='devtool-fc02-index-')
    os.close(fd)
    Path(idx).unlink()
    env = {**os.environ, 'GIT_INDEX_FILE': idx}
    try:
        _run(repo, 'read-tree', source, env=env)
        for rel, value in sorted(changes.items()):
            if value is None:
                _run(repo, 'update-index', '--force-remove', '--', rel, env=env)
                continue
            blob = _run(repo, 'hash-object', '-w', '--stdin', input=value).decode()
            old = _run(repo, 'ls-tree', source, '--', rel).decode()
            mode = old.split()[0] if old else mode_for(rel) if mode_for else '100644'
            if mode not in ('100644', '100755'):
                raise GuardedGitError('unsupported file mode for ' + rel)
            _run(repo, 'update-index', '--add', '--cacheinfo', mode, blob, rel, env=env)
        tree = _run(repo, 'write-tree', env=env).decode()
        try:
            _run(repo, 'var', 'GIT_AUTHOR_IDENT')
            _run(repo, 'var', 'GIT_COMMITTER_IDENT')
        except GuardedGitError as exc:
            raise GuardedGitError('Author identity unknown: '+str(exc)) from exc
        commit = _run(repo, 'commit-tree', tree, '-p', source, input=message).decode()
        return commit, tree
    finally:
        Path(idx).unlink(missing_ok=True)


def verify_candidate(repo: Path, source: str, candidate: str, tree: str,
                     hashes: dict[str, str | None], preimages: dict[str, str | None]) -> None:
    """Require exact parent/tree, exact changed paths, modes and SHA-256 bytes."""
    if _run(repo, 'rev-parse', candidate+'^').decode() != source:
        raise GuardedGitError('prepared commit parent differs from reviewed source')
    if _run(repo, 'rev-parse', candidate+'^{tree}').decode() != tree:
        raise GuardedGitError('prepared commit tree differs from reviewed tree')
    changed = set(_run(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', candidate).decode().splitlines())
    if changed != set(hashes):
        raise GuardedGitError('prepared commit changed unapproved or omitted paths')
    for rel, expected in hashes.items():
        if not rel or rel.startswith('/') or '..' in Path(rel).parts or '.git' in Path(rel).parts:
            raise GuardedGitError('unsafe approved path')
        previous = subprocess.run(['git','-C',str(repo),'show', source+':'+rel], capture_output=True,timeout=30)
        old = previous.stdout if previous.returncode == 0 else None
        if digest(old) != preimages.get(rel):
            raise GuardedGitError('reviewed preimage mismatch: '+rel)
        current = subprocess.run(['git','-C',str(repo),'show',candidate+':'+rel], capture_output=True,timeout=30)
        new = current.stdout if current.returncode == 0 else None
        if digest(new) != expected:
            raise GuardedGitError('prepared bytes differ from approved path: '+rel)


def cas_branch(repo: Path, branch: str, new: str, old: str) -> None:
    if not branch.startswith('refs/heads/') or not branch.removeprefix('refs/heads/'):
        raise GuardedGitError('expected an explicit branch ref')
    _run(repo, 'update-ref', branch, new, old)


@contextmanager
def _anchored_parent(root: Path, relative: str):
    """Pin every ancestor by O_DIRECTORY|O_NOFOLLOW before touching a file."""
    pieces = relative.split('/')
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, 'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    handles=[]
    try:
        fd=os.open(root,flags)
        handles.append(fd)
        for part in pieces[:-1]:
            if not part or part in ('.','..'):
                raise GuardedGitError('unsafe path component')
            try:
                nextfd=os.open(part,flags,dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part,0o700,dir_fd=fd)
                nextfd=os.open(part,flags,dir_fd=fd)
            handles.append(nextfd)
            fd=nextfd
        yield fd, pieces[-1]
    finally:
        for fd in reversed(handles):
            os.close(fd)


def _read_regular(dirfd: int, name: str) -> bytes | None:
    flags = os.O_RDONLY
    if hasattr(os,'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    try:
        fd=os.open(name,flags,dir_fd=dirfd)
    except FileNotFoundError:
        return None
    try:
        meta=os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise GuardedGitError('nonregular or multiply linked file refused: '+name)
        with os.fdopen(fd,'rb',closefd=False) as input:
            return input.read()
    finally:
        os.close(fd)


def reconcile_index_locked(repo: Path, target: str) -> None:
    """Install reviewed index under conventional Git index.lock exclusion.

    Cooperating Git writers cannot stage concurrently. Do not use a normal
    read-tree that can silently overwrite an index mutation after preflight.
    """
    index_name = _run(repo, 'rev-parse', '--git-path', 'index').decode()
    index = Path(index_name)
    if not index.is_absolute():
        index = repo / index
    index = index.absolute()
    if index.is_symlink():
        raise GuardedGitError('Git index symlink refused')
    lock = Path(str(index)+'.lock')
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os,'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock, flags, 0o600)
    except FileExistsError as exc:
        raise GuardedGitError('Git index locked by another writer') from exc
    os.close(descriptor)
    try:
        prior = index.read_bytes() if index.exists() else None
        # Use the leased index.lock as the alternate index output. Git's own
        # internal lock will be index.lock.lock, leaving our exclusion intact.
        env = {**os.environ,'GIT_INDEX_FILE':str(lock)}
        _run(repo,'read-tree',target,env=env)
        if (index.read_bytes() if index.exists() else None) != prior:
            raise GuardedGitError('Git index changed despite exclusive lease')
        if not lock.is_file() or lock.is_symlink():
            raise GuardedGitError('leased index unexpectedly replaced')
        os.replace(lock,index)
        dirfd = os.open(index.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dirfd)
        finally:os.close(dirfd)
    finally:
        # Only remove our own lock when it still contains a valid temp index;
        # an unexpected lock replacement is preserved for manual review.
        if lock.is_file() and not lock.is_symlink():
            lock.unlink()


def _restore_saved_exclusively(parentfd: int, name: str, escrowfd: int, backup: str,
                               expected_bytes: bytes) -> bool:
    """Restore displaced foreign bytes without relying on hard links.

    Android/Termux Python or filesystems may not provide os.link().  Open the
    pinned escrow file, verify regular/singly-linked provenance, then create
    the worktree entry exclusively through its pinned parent directory.  A
    competing newly created entry wins; in that case only the escrow copy is
    kept.  Keep the escrow entry in all cases for crash/manual recovery.

    A crash during the copy still leaves the full escrow original.  Partial
    destination writes are intentionally left for manual reconciliation, not
    unlinked by a racy pathname cleanup.
    """
    read_flags = os.O_RDONLY
    write_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, 'O_NOFOLLOW'):
        read_flags |= os.O_NOFOLLOW
        write_flags |= os.O_NOFOLLOW
    sourcefd = os.open(backup, read_flags, dir_fd=escrowfd)
    try:
        meta = os.fstat(sourcefd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
            raise GuardedGitError('escrow original is no longer a singly-linked regular file')
        # Check the open descriptor's bytes before creating any replacement.
        with os.fdopen(os.dup(sourcefd), 'rb') as source_stream:
            actual = source_stream.read()
        if actual != expected_bytes:
            raise GuardedGitError('escrow original bytes changed during recovery')
        try:
            destfd = os.open(name, write_flags, stat.S_IMODE(meta.st_mode), dir_fd=parentfd)
        except FileExistsError:
            return False  # Another writer owns the new path; do not overwrite.
        try:
            with os.fdopen(destfd, 'wb', closefd=False) as destination:
                destination.write(actual)
                destination.flush()
                os.fsync(destination.fileno())
            os.fchmod(destfd, stat.S_IMODE(meta.st_mode))
        finally:
            os.close(destfd)
        os.fsync(parentfd)
        return True
    finally:
        os.close(sourcefd)


def reconcile(repo: Path, source: str, target: str,
              hashes: dict[str, str | None], preimages: dict[str, str | None], escrow: Path,
              path_guard: Callable[[Path, str], Path]) -> None:
    """Reconcile without overwriting newly arrived bytes.

    Every existing file is first MOVED into exclusive escrow, not overwritten.
    If it differs from the reviewed source, we restore using an exclusively
    created copy (never clobbering another writer), and retain escrow even on
    failure. No filesystem hard-link capability is required.
    A successful update keeps preimage backups for explicit later cleanup.
    """
    escrow.mkdir(parents=True, exist_ok=True, mode=0o700)
    if escrow.is_symlink():
        raise GuardedGitError('escrow symlink refused')
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os,'O_NOFOLLOW'): flags |= os.O_NOFOLLOW
    escrowfd=os.open(escrow,flags)
    try:
        for index, rel in enumerate(sorted(hashes)):
            path_guard(repo, rel)
            after, old = hashes[rel], preimages[rel]
            with _anchored_parent(repo,rel) as (parentfd,name):
                current = _read_regular(parentfd,name)
                if digest(current) == after:
                    continue
                backup = f'{index:04d}.original'
                saved = _read_regular(escrowfd,backup)
                resuming_escrow = (current is None and old is not None and
                                   saved is not None and digest(saved) == old)
                if digest(current) != old and not resuming_escrow:
                    raise GuardedGitError('worktree changed during reconciliation: '+rel)
                if saved is not None:
                    # Previously interrupted after escrowing original bytes.
                    if not resuming_escrow:
                        raise GuardedGitError('unreconciled escrow record requires manual review: '+str(escrow/backup))
                if old is not None and saved is None:
                    # Move the *current* directory entry to escrow. A foreign
                    # late edit stays byte-for-byte intact even if checks raced.
                    os.rename(name, backup, src_dir_fd=parentfd,dst_dir_fd=escrowfd)
                    moved = _read_regular(escrowfd,backup)
                    if digest(moved) != old:
                        restored = _restore_saved_exclusively(parentfd,name,escrowfd,backup,moved)
                        raise GuardedGitError('late worktree edit preserved in escrow'
                                              + (' and restored exclusively' if restored else '; competing path retained')
                                              + ': ' + str(escrow/backup))
                else:
                    if _read_regular(parentfd,name) is not None:
                        raise GuardedGitError('concurrent new file refused: '+rel)
                if after is not None:
                    proc=subprocess.run(['git','-C',str(repo),'show',target+':'+rel],
                                        capture_output=True,timeout=40)
                    if proc.returncode or digest(proc.stdout) != after:
                        raise GuardedGitError('candidate Git blob differs: '+rel)
                    mode_line=_run(repo,'ls-tree',target,'--',rel).decode()
                    mode=0o755 if mode_line.startswith('100755') else 0o644
                    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL
                    if hasattr(os,'O_NOFOLLOW'): flags|=os.O_NOFOLLOW
                    try:
                        fd=os.open(name,flags,mode,dir_fd=parentfd)
                    except FileExistsError as exc:
                        raise GuardedGitError('concurrent worktree creation refused: '+rel) from exc
                    with os.fdopen(fd,'wb') as stream:
                        stream.write(proc.stdout);stream.flush();os.fsync(stream.fileno())
        reconcile_index_locked(repo, target)  # index only; no checkout writes
        if _run(repo, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise GuardedGitError('worktree/index differs after guarded reconciliation')
    finally:
        os.close(escrowfd)
