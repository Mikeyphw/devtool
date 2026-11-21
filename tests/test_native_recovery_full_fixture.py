"""Offline planning and real local Git tests; not live GitHub acceptance."""
import importlib.util
from pathlib import Path
import json
import os
import re
import unittest
from unittest.mock import patch
import tempfile
import shutil
import subprocess

spec=importlib.util.spec_from_file_location('native_recovery_full_fixture',Path(__file__).with_name('native_recovery_full_fixture.py'))
f=importlib.util.module_from_spec(spec); spec.loader.exec_module(f)

class FullFixtureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.state=f.new_state(self.base/'campaign')
        self.plan=f.plan_data('fixture-user','a'*24,f.PARENT); self.raw=f.encoded(self.plan)
        f.write_new(self.state/'plan.json',self.raw); f.write_new(self.state/'plan.sha256',(f.digest(self.raw)+'\n').encode())
    def test_exact_roles_and_all_matrix_rows_initially_unqualified(self):
        self.assertEqual(set(self.plan['matrix']),set(f.MATRIX))
        self.assertEqual(len(set(self.plan['roles'].values())),8)
        self.assertTrue(all(re.fullmatch(r'fixture-user/repo-rebuild-e2e-[a-z0-9]{8,32}',x) for x in self.plan['roles'].values()))
        self.assertTrue(all(x['verdict']=='NOT_RUN' for x in self.plan['matrix'].values()))
        self.assertFalse(self.plan['remote_authorization'])
    def test_tamper_refused(self):
        (self.state/'plan.json').write_text('{}')
        with self.assertRaises(f.Refused): f.load_plan(self.state)
    def test_scope_change_even_with_digest_refused(self):
        self.plan['roles']['primary']='fixture-user/devtool'; raw=f.encoded(self.plan)
        (self.state/'plan.json').write_bytes(raw); (self.state/'plan.sha256').write_text(f.digest(raw))
        with self.assertRaises(f.Refused): f.load_plan(self.state)
    def test_symlink_and_insecure_storage_refused(self):
        p=self.base/'alias'; p.symlink_to(self.state,target_is_directory=True)
        with self.assertRaises(f.Refused): f.private_dir(p)
        self.state.chmod(0o755)
        with self.assertRaises(f.Refused): f.private_dir(self.state)
    def test_reused_state_refused(self):
        with self.assertRaises(FileExistsError): f.new_state(self.state)
    def test_create_requires_exact_plan_and_slug_before_github(self):
        with patch.object(f,'source_check',return_value=self.base), patch.object(f,'gh_get') as transport:
            with self.assertRaisesRegex(f.Refused,'EXACT_CREATE_APPROVAL_REQUIRED'):
                f.create_primary(self.state,self.base,'fixture-user/devtool',f.digest(self.raw))
            with self.assertRaisesRegex(f.Refused,'EXACT_CREATE_APPROVAL_REQUIRED'):
                f.create_primary(self.state,self.base,self.plan['roles']['primary'],'0'*64)
            transport.assert_not_called()
        self.assertFalse((self.state/'primary-create-intent.json').exists())
    def test_missing_preflight_refuses_before_github(self):
        with patch.object(f,'source_check',return_value=self.base), patch.object(f,'gh_get') as transport:
            with self.assertRaisesRegex(f.Refused,'PREFLIGHT_RECEIPT_REQUIRED'):
                f.create_primary(self.state,self.base,self.plan['roles']['primary'],f.digest(self.raw))
            transport.assert_not_called()
    def test_inert_workflow_and_pinned_action(self):
        w=f.workflow(); self.assertIn('permissions: {}',w); self.assertIn(f.UPLOAD,w); self.assertNotIn('pull_request_target',w); self.assertNotIn('gh api',w)
    @unittest.skipUnless(shutil.which('git'),'git unavailable')
    def test_real_git_smoke_seed(self):
        m=f.seed_local(self.state,with_lfs=False)
        self.assertEqual(len(m['refs']),7); self.assertEqual(m['seed_status'],'LOCAL_SMOKE_ONLY'); self.assertFalse(m['live_acceptance'])
        repo=self.state/'seed/repository'
        self.assertEqual(subprocess.check_output(['git','-C',str(repo),'cat-file','-t','fixture-annotated'],text=True).strip(),'tag')
        self.assertEqual(subprocess.check_output(['git','-C',str(repo),'remote'],text=True).strip(),'')
        with self.assertRaises(FileExistsError): f.seed_local(self.state,with_lfs=False)
    @unittest.skipUnless(shutil.which('git-lfs'),'git-lfs unavailable')
    def test_real_lfs_seed(self):
        m=f.seed_local(self.state); self.assertTrue(m['lfs']['verified_local']); self.assertFalse(m['live_acceptance'])
