"""FC-01 mandatory adversarial RM01 acceptance (Linux/Termux)."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtool.output_scopes import (
    cleanup_owned_temp, hash_owned_output, provision_node_temp,
    recover_orphaned_temps, OutputScopeError,
)


@pytest.fixture()
def owned(tmp_path):
    state = tmp_path / 'state'
    repo = tmp_path / 'repo'
    repo.mkdir()
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(state)}):
        path, identity = provision_node_temp(repo, 'fc01-run', 'node')
        yield repo, path, identity
        cleanup_owned_temp(path, identity)


def test_hash_descriptor_anchor_refuses_swapped_intermediate(owned, tmp_path):
    _, root, identity = owned
    nested = root / 'nested'
    nested.mkdir()
    (nested / 'proof').write_bytes(b'owned')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'proof').write_bytes(b'FOREIGN SECRET')
    original_open = os.open
    swapped = False

    def injected_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == 'nested' and kwargs.get('dir_fd') is not None and not swapped:
            swapped = True
            nested.rename(root / 'moved')
            nested.symlink_to(foreign, target_is_directory=True)
        return original_open(name, flags, *args, **kwargs)

    with patch('devtool.output_scopes.os.open', side_effect=injected_open):
        result = hash_owned_output(root, 'nested/proof', identity=identity)
    assert swapped and result['status'] != 'hashed', result
    assert (foreign / 'proof').read_bytes() == b'FOREIGN SECRET'


def test_hash_refuses_multiple_links_even_without_hardlink_creation_api(owned):
    """Exercise the security decision even on Python/Android without os.link.

    Android Python builds may expose no working hardlink-creation API, so
    pathlib.Path.hardlink_to() can raise UnsupportedOperation before the
    verifier is called. A deterministic descriptor-stat probe verifies the
    actual fail-closed st_nlink > 1 branch on *every* supported host.
    """
    _, root, identity = owned
    regular = root / 'regular'
    regular.write_bytes(b'owned')
    expected = regular.stat()
    original_fstat = os.fstat
    checked = []

    def report_multiple_links(fd):
        info = original_fstat(fd)
        if (stat.S_ISREG(info.st_mode)
                and (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)):
            checked.append(fd)
            # Only nlink changes; this models a real multiply-linked inode.
            return SimpleNamespace(st_mode=info.st_mode, st_nlink=2)
        return info

    with patch('devtool.output_scopes.os.fstat', side_effect=report_multiple_links):
        proof = hash_owned_output(root, 'regular', identity=identity)
    assert checked, 'hash verifier did not inspect the owned regular-file fd'
    assert proof == {'path': 'regular', 'status': 'unsupported',
                     'reason': 'not_private_regular_file'}, proof
    assert regular.read_bytes() == b'owned'

    # Also exercise a physical hardlink where the OS provides one. This is
    # additive, not the mandatory proof: some Termux builds omit os.link.
    link = getattr(os, 'link', None)
    if callable(link):
        try:
            link(regular, root / 'physical-hardlink')
        except (OSError, NotImplementedError):
            pass  # The mandatory descriptor-stat refusal above still ran.
        else:
            assert hash_owned_output(root, 'regular', identity=identity) == {
                'path': 'regular', 'status': 'unsupported',
                'reason': 'not_private_regular_file',
            }


def test_hash_rejects_root_replacement(owned, tmp_path):
    _, root, identity = owned
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'proof').write_bytes(b'protected')
    root.rename(root.with_name('moved'))
    root.symlink_to(foreign, target_is_directory=True)
    result = hash_owned_output(root, 'proof', identity=identity)
    assert result['status'] == 'unavailable'
    assert (foreign / 'proof').read_bytes() == b'protected'


def test_cleanup_refuses_replaced_root_without_foreign_unlink(owned, tmp_path):
    _, root, identity = owned
    (root / 'owned').write_bytes(b'owned')
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'sentinel').write_bytes(b'FOREIGN')
    from devtool import output_scopes as scopes
    original = scopes._owned_recursive_remove
    touched = False

    def race(fd):
        nonlocal touched
        if not touched:
            touched = True
            root.rename(root.with_name('moved'))
            root.mkdir()
            (root / 'sentinel').write_bytes(b'FOREIGN')
        return original(fd)

    with patch.object(scopes, '_owned_recursive_remove', side_effect=race):
        assert not cleanup_owned_temp(root, identity)
    assert touched
    assert (root / 'sentinel').read_bytes() == b'FOREIGN'
    assert (foreign / 'sentinel').read_bytes() == b'FOREIGN'


def test_cleanup_symlink_and_sibling_protected(owned, tmp_path):
    _, root, identity = owned
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (foreign / 'file').write_text('do not remove')
    (root / 'link').symlink_to(foreign, target_is_directory=True)
    (root / 'owned').mkdir()
    (root / 'owned' / 'nested').write_text('yes')
    sibling = root.parent / 'other-node'
    sibling.mkdir()
    (sibling / 'sentinel').write_text('sibling')
    assert cleanup_owned_temp(root, identity)
    assert (foreign / 'file').read_text() == 'do not remove'
    assert (sibling / 'sentinel').read_text() == 'sibling'


def test_journal_lock_prevents_reclaim_of_live_owner(owned):
    repo, root, _ = owned
    records = recover_orphaned_temps(repo, dry_run=False)
    assert any(r['status'] == 'preserved' and r['reason'] == 'owner_active' for r in records), records
    assert root.is_dir()


def test_crash_recovery_sigkill_preserves_foreign_and_reclaims_verified_root(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    script = '''import os,sys,pathlib,signal
from devtool.output_scopes import provision_node_temp
root,ident=provision_node_temp(pathlib.Path(sys.argv[1]),'sigkill','node')
(root/'nested').mkdir()
(root/'nested'/'owned').write_text('owned')
print(root,flush=True)
os.kill(os.getpid(),signal.SIGKILL)
'''
    env = dict(os.environ, XDG_STATE_HOME=str(state), PYTHONPATH=str(Path(__file__).parents[1]))
    p = subprocess.run([sys.executable, '-c', script, str(repo)], env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == -signal.SIGKILL, p.stderr
    root = Path(p.stdout.strip())
    assert (root / 'nested' / 'owned').exists()
    foreign = tmp_path / 'unrelated'
    foreign.mkdir()
    (foreign / 'keep').write_bytes(b'forever')
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(state)}):
        dry = recover_orphaned_temps(repo, dry_run=True)
        assert any(x['status'] == 'orphan_candidate' for x in dry), dry
        assert root.is_dir()
        conservative = recover_orphaned_temps(repo, dry_run=False)
        assert any(x['status'] == 'orphan_candidate' for x in conservative), conservative
        assert root.exists()  # A supervisor can die while its child still runs.
        done = recover_orphaned_temps(repo, dry_run=False, approve_same_boot=True)
        assert any(x['status'] == 'recovered' for x in done), done
        assert not root.exists()
        assert recover_orphaned_temps(repo, dry_run=False) == []
    assert (foreign / 'keep').read_bytes() == b'forever'


def test_unregistered_and_forged_journal_fail_closed(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(tmp_path / 'state')}):
        root, identity = provision_node_temp(repo, 'run-foreign', 'node')
        journal = root.parent / f'.owner-{root.name}.json'
        assert journal.exists()
        journal.write_text('{"fake":true}')
        # Current lease remains active; it is never adopted by recovery.
        assert recover_orphaned_temps(repo, dry_run=False)[0]['status'] == 'preserved'
        # Simulate process release; malformed record cannot authorize deletion.
        from devtool.output_scopes import _LEASES
        fd, _, _ = _LEASES.pop((str(root), identity))
        os.close(fd)
        found = recover_orphaned_temps(repo, dry_run=False)
        assert any(r['status'] == 'refused' for r in found), found
        assert root.exists()
        journal.unlink()
        found = recover_orphaned_temps(repo, dry_run=False)
        assert any(r['status'] == 'orphan_candidate' for r in found), found
        assert root.exists()
        assert cleanup_owned_temp(root, identity)


def test_hash_regular_owned_file_unchanged(owned):
    _, root, identity = owned
    (root / 'regular').write_bytes(b'expected')
    proof = hash_owned_output(root, 'regular', identity=identity)
    assert proof['status'] == 'hashed'
    assert proof['sha256'] == hashlib.sha256(b'expected').hexdigest()


def test_sigkill_during_partial_nested_cleanup_is_recoverable(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    state = tmp_path / 'state'
    script = '''import os,sys,pathlib,signal
from unittest.mock import patch
from devtool import output_scopes as scopes
root,identity=scopes.provision_node_temp(pathlib.Path(sys.argv[1]),'mid-cleanup','node')
(root/'deep').mkdir()
(root/'deep'/'one').write_text('one')
(root/'deep'/'two').write_text('two')
print(root,flush=True)
def interrupted(fd):
    os.unlink('deep/one',dir_fd=fd)
    os.kill(os.getpid(),signal.SIGKILL)
with patch.object(scopes,'_owned_recursive_remove',side_effect=interrupted):
    scopes.cleanup_owned_temp(root,identity)
'''
    env = dict(os.environ, XDG_STATE_HOME=str(state), PYTHONPATH=str(Path(__file__).parents[1]))
    p = subprocess.run([sys.executable, '-c', script, str(repo)], env=env, capture_output=True, text=True, timeout=20)
    assert p.returncode == -signal.SIGKILL, p.stderr
    root = Path(p.stdout.strip())
    assert root.exists()
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(state)}):
        candidate = recover_orphaned_temps(repo, dry_run=False)
        assert any(x['status'] == 'orphan_candidate' for x in candidate), candidate
        records = recover_orphaned_temps(repo, dry_run=False, approve_same_boot=True)
        assert any(x['status'] == 'recovered' for x in records), records
    assert not root.exists()


def test_reboot_verified_orphan_can_be_recovered_without_same_boot_approval(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    from devtool import output_scopes as scopes
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(tmp_path / 'state')}):
        path, identity = provision_node_temp(repo, 'boot-owned', 'node')
        lease_fd, _, _ = scopes._LEASES.pop((str(path), identity))
        os.close(lease_fd)
        with patch.object(scopes, '_boot_id', return_value='another-boot-id-12345678901234567890'):
            receipt = recover_orphaned_temps(repo, dry_run=False)
        assert any(x['status'] == 'recovered' for x in receipt), receipt
        assert not path.exists()


def test_walk_uses_search_only_descriptors_for_ancestor_directories(tmp_path):
    """Android may refuse O_RDONLY on / and /data despite granting traversal."""
    from devtool import output_scopes as scopes
    if not hasattr(os, 'O_PATH'):
        pytest.skip('O_PATH descriptor API unavailable')
    target = tmp_path / 'first' / 'second'
    original = os.open
    observations = []

    def deny_read_on_ancestors(path, flags, *args, **kwargs):
        # The final private node must be opened for reading, but ancestors
        # must never request read permission. Simulate Android's policy even
        # on a permissive developer workstation.
        is_ancestor = path == '/' or path in target.parts[1:-1]
        if is_ancestor:
            observations.append((path, flags))
            if not flags & os.O_PATH:
                raise PermissionError(13, 'read access denied on Android ancestor', path)
        return original(path, flags, *args, **kwargs)

    with patch.object(scopes.os, 'open', side_effect=deny_read_on_ancestors):
        fd = scopes._open_dir(target, create=True)
    try:
        assert target.is_dir()
        assert os.fstat(fd).st_ino == target.stat().st_ino
        assert os.listdir(fd) == []  # the returned owned descriptor is readable
    finally:
        os.close(fd)
    assert observations and all(flags & os.O_PATH for _, flags in observations)


def test_walk_refuses_symlink_even_when_ancestor_is_search_only(tmp_path):
    from devtool import output_scopes as scopes
    if not hasattr(os, 'O_PATH'):
        pytest.skip('O_PATH descriptor API unavailable')
    outside = tmp_path / 'outside'
    outside.mkdir()
    (tmp_path / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        scopes._open_dir(tmp_path / 'link' / 'child', create=True)
    assert not (outside / 'child').exists()


def test_recovery_and_hash_work_with_android_traverse_only_ancestors(owned):
    """Production scope paths still work when ancestors have no read access."""
    if not hasattr(os, 'O_PATH'):
        pytest.skip('O_PATH descriptor API unavailable')
    from devtool import output_scopes as scopes
    repo, root, identity = owned
    (root / 'proof').write_bytes(b'actual evidence')
    real_open = os.open

    def denied_ancestor(path, flags, *args, **kwargs):
        if path == '/' and not flags & os.O_PATH:
            raise PermissionError(13, 'Android / is search-only', path)
        return real_open(path, flags, *args, **kwargs)

    with patch.object(scopes.os, 'open', side_effect=denied_ancestor):
        assert scopes.hash_owned_output(root, 'proof', identity=identity)['status'] == 'hashed'
        assert any(x['status'] == 'preserved' for x in scopes.recover_orphaned_temps(repo))
        assert scopes.cleanup_owned_temp(root, identity)
    assert not root.exists()
