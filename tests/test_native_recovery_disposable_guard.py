"""Non-network tests proving the optional destructive E2E harness is opt-in."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from devtool.recovery import engine as rr

S = Path(__file__).with_name('native_recovery_e2e_disposable.py')
spec = importlib.util.spec_from_file_location('repo_rebuild_disposable_e2e', S)
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


class DisposableGuardTests(unittest.TestCase):
    def test_production_repo_never_enters_api_or_deletion(self):
        with patch.object(rr,'api',side_effect=AssertionError('no remote calls')):
            with self.assertRaisesRegex(rr.Refused,'uniquely named'):
                e2e.checked_disposable('Mikeyphw/MMRL','Mikeyphw/MMRL')

    def test_disposable_name_still_requires_exact_approval(self):
        with patch.object(rr,'api',side_effect=AssertionError('no remote calls')):
            with self.assertRaisesRegex(rr.Refused,'ack-disposable-delete'):
                e2e.checked_disposable('Mikeyphw/repo-rebuild-e2e-12345678','Mikeyphw/MMRL')

    def test_disposable_test_not_named_for_unittest_discovery(self):
        self.assertTrue(S.is_file())
        self.assertFalse(S.name.startswith('test_'))


if __name__=='__main__': unittest.main()
