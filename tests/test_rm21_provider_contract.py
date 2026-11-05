"""RM21 real native RM11/RM12 source admission; no Gradle/npm executed locally."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import tempfile
import unittest

from devtool.actions_rm11_node import inspect_node_job, NodeError
from devtool.actions_rm12_jvm import inspect_jvm_job, JvmError

PREPARE=Path(__file__).resolve().parent/'fixtures/rm21/prepare.py'
spec=importlib.util.spec_from_file_location('rm21_prepare_contract', PREPARE)
assert spec and spec.loader
prepared=importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepared)

NODE={'ci_node_project_dir':'web','ci_node_script_policy':'reviewed-composite',
      'ci_node_version':'22','ci_python_version':'3.13'}
JVM={'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21','ci_jvm_tool_version':'9.1.0'}
GRADLE=['gradle','--offline','--no-daemon','--console=plain',':desktop:jar']


class ActualProviders(unittest.TestCase):
    def repo(self,temp):
        root=Path(temp)/'consumer'
        prepared.prepare(root)
        return root

    def test_tracked_node_project_admitted_and_nested_sources_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.repo(temp)
            reviewed=inspect_node_job(root,['npm','run','build'],NODE)
            self.assertEqual(reviewed['kind'],'build')
            self.assertEqual(reviewed['workspace']['script_policy'],'reviewed-composite')
            self.assertIn('web/scripts/build.mjs',reviewed['repo_script_sources'])
            self.assertEqual(len(reviewed['workspace']['source_digest']),64)

    def test_tracked_jvm_only_closure_admitted(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.repo(temp)
            reviewed=inspect_jvm_job(root,GRADLE,JVM)
            self.assertEqual(reviewed['selected'],':desktop')
            self.assertEqual(set(reviewed['project']['modules']),{':desktop'})
            self.assertEqual(len(reviewed['project']['source_digest']),64)

    def test_android_module_and_changed_source_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=self.repo(temp)
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,GRADLE[:-1]+[':android:jar'],{**JVM,'ci_jvm_mixed_module':':android'})
            with self.assertRaises(NodeError):
                inspect_node_job(root,['npm','run','build'],{**NODE,'ci_node_script_policy':'strict'})
            (root/'web/scripts/build.mjs').unlink()
            with self.assertRaises(NodeError):
                inspect_node_job(root,['npm','run','build'],NODE)


if __name__=='__main__':unittest.main()
