"""Current-contract regression for run 40517927121; real Windows gate stays open."""
from __future__ import annotations
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from devtool import host_file_lock as locks

ROOT = Path(__file__).resolve().parents[1]
# The native validator intentionally strips ambient Termux PREFIX. Resolve
# the verified real-interpreter helper from this repository, not the unrelated
# Hermes Agent package that also owns a top-level ``tools`` name.
_LOCAL_TOOLS = str(ROOT / 'tools')
if _LOCAL_TOOLS not in sys.path:
    sys.path.insert(0, _LOCAL_TOOLS)
import fc08_host_workloads as _host_fixture


class LockContract(unittest.TestCase):
    def test_native_posix_backend_unmodified(self):
        if os.name != 'posix':
            self.assertTrue(hasattr(locks, 'flock'))
            return
        import fcntl
        self.assertEqual(locks.LOCK_EX, fcntl.LOCK_EX)
        self.assertIs(locks._native.flock, fcntl.flock)

    def test_windows_shared_is_exclusive_not_a_noop(self):
        obj=types.SimpleNamespace(LK_UNLCK=0,LK_NBLCK=1,LK_LOCK=2,history=[])
        def execute(fd,op,n):obj.history.append((fd,op,n))
        obj.locking=execute
        with tempfile.TemporaryFile() as fp:
            with mock.patch.object(locks.os,'lseek',wraps=os.lseek) as seek:
                locks._windows_flock(fp.fileno(),locks.LOCK_SH,backend=obj)
                locks._windows_flock(fp.fileno(),locks.LOCK_EX|locks.LOCK_NB,backend=obj)
                locks._windows_flock(fp.fileno(),locks.LOCK_UN,backend=obj)
            self.assertEqual([x[1] for x in obj.history], [2,1,0])
            self.assertEqual(seek.call_count,3)
            self.assertTrue(all(x[2]==1 for x in obj.history))

    def test_windows_nonblocking_contention_is_not_silently_acquired(self):
        def blocked(*_):raise PermissionError(errno.EACCES,'locked')
        fake=types.SimpleNamespace(LK_UNLCK=0,LK_NBLCK=1,LK_LOCK=2,locking=blocked)
        with tempfile.TemporaryFile() as fp:
            with self.assertRaises(BlockingIOError):
                locks._windows_flock(fp.fileno(),locks.LOCK_EX|locks.LOCK_NB,backend=fake)
        with tempfile.TemporaryFile() as fp:
            with self.assertRaises(PermissionError):
                locks._windows_flock(fp.fileno(),locks.LOCK_EX,backend=fake)

    def test_resource_absence_does_not_block_cli_import(self):
        script='''import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name == "resource": raise ModuleNotFoundError("No module named resource")
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
import devtool.cli as cli
assert cli.resource is None
'''
        p=subprocess.run([sys.executable,'-c',script],cwd=ROOT,capture_output=True,text=True,timeout=40)
        self.assertEqual(p.returncode,0,p.stderr[-1300:])

    def test_real_lock_serializes_across_processes_posix(self):
        if os.name!='posix':
            self.assertTrue(callable(locks.flock));return
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'lock'
            with path.open('a+') as first:
                locks.flock(first.fileno(),locks.LOCK_EX|locks.LOCK_NB)
                src='from pathlib import Path;from devtool.host_file_lock import flock,LOCK_EX,LOCK_NB;\nwith Path(%r).open("a+") as f:\n flock(f.fileno(),LOCK_EX|LOCK_NB)' % str(path)
                run=subprocess.run([sys.executable,'-c',src],cwd=ROOT,capture_output=True,text=True,timeout=30)
                self.assertNotEqual(run.returncode,0)
                locks.flock(first.fileno(),locks.LOCK_UN)
            again=subprocess.run([sys.executable,'-c',src],cwd=ROOT,capture_output=True,text=True,timeout=30)
            self.assertEqual(again.returncode,0,again.stderr[-800:])

class NpmLifecycleContract(unittest.TestCase):
    def test_fixture_environment_comes_only_from_real_interpreter(self):
        # A fabricated path or foreign Python cannot grant Termux identity.
        fake = _host_fixture._fixture_termux_environment(
            python_executable='/usr/bin/not-the-real-termux-python',
            python_prefix='/data/data/com.termux/files/usr',
        )
        self.assertEqual(fake, {})
        real = _host_fixture._fixture_termux_environment()
        self.assertTrue(real == {} or real == {'PREFIX': '/data/data/com.termux/files/usr'})

    def test_pending_child_finalized_only_after_authoritative_status(self):
        source=(ROOT/'devtool/runners.py').read_text()
        self.assertIn('execution_adapter.finish(returncode)',source)
        self.assertLess(source.index('execution_adapter.finish(returncode)'),source.index('execution_adapter.process_exited('))
        self.assertIn('returncode == 0 and has_fatal_error_diagnostics',source)

    def test_actual_node_cli_returns_zero_and_seals(self):
        if shutil.which('node') is None or shutil.which('npm') is None:
            self.assertTrue((ROOT/'devtool/npm_progress.py').exists())
            return
        # Actual Devtool CLI, not mocked. Both process and EXO lifecycle must
        # reach terminal success for a no-dependency npm script.
        with tempfile.TemporaryDirectory(prefix='fc08-exo-') as tmp:
            base=Path(tmp)
            (base/'.devtool.toml').write_text('[project]\nrunner="node"\nplatform="server"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="node"\nroot="."\n')
            (base/'package.json').write_text('{"name":"fc08-exo","version":"1.0.0","private":true,"scripts":{"build":"node -e \\\"process.exit(0)\\\""}}')
            (base/'.gitignore').write_text('.devtool/\n')
            for args in [('init','-q'),('add','.'),('-c','user.name=FC08', '-c','user.email=fc08@example.invalid','commit','-qm','fixture')]:
                subprocess.run(['git',*args],cwd=base,check=True,capture_output=True,timeout=25)
            env=dict(os.environ,DEVTOOL_JOB_UI='off')
            env.update(_host_fixture._fixture_termux_environment())
            run=subprocess.run([sys.executable,'-m','devtool','--repo',str(base),'--target','app','build'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(run.returncode,0,run.stdout[-4000:]+run.stderr[-3000:])

if __name__=='__main__':unittest.main()

class CaptureAncestryContract(unittest.TestCase):
    def test_capture_requires_ancestry_not_stale_immediate_parent(self):
        s=(ROOT/'tools/fc08_host_capture.py').read_text()
        self.assertIn("git(root,'merge-base','--is-ancestor',BASE,sha)",s)
        self.assertNotIn("git(root,'rev-parse','HEAD^')!=BASE",s)
        self.assertIn('REFUSED_REMOTE_SOURCE_TREE_DRIFT',s)
        self.assertIn('REFUSED_LOCAL_BRANCH_NOT_PUSHED_TO_GITHUB',s)
        self.assertIn('REFUSED_MAIN_FEATURE_WORKFLOW_BYTES_DIFFER',s)
