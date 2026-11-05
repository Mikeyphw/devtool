"""RM17 first-class RM-11/RM-12 provider acceptance, no local Gradle/npm builds."""
from __future__ import annotations
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devtool.actions_rm11_node import inspect_node_job, NodeError
from devtool.actions_rm12_jvm import inspect_jvm_job, JvmError
from test_rm17_generic_polyglot import checkout, npm_sources, gradle_sources


class ProviderIntegration(unittest.TestCase):
    def test_nested_node_pipeline_admitted_and_repo_sources_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=checkout(Path(tmp),npm_sources())
            meta={'ci_node_project_dir':'web','ci_node_script_policy':'reviewed-composite',
                  'ci_node_version':'22','ci_python_version':'3.13'}
            row=inspect_node_job(root,['npm','run','test'],meta)
            self.assertEqual(row['script_policy'],'reviewed-composite')
            self.assertEqual(row['python_version'],'3.13')
            self.assertIn('scripts/lint.py',row['repo_script_sources'])
            source=row['workspace']['source_digest']
            (root/'scripts/lint.py').write_text('print("changed")\n')
            changed=inspect_node_job(root,['npm','run','test'],meta)
            self.assertNotEqual(source,changed['workspace']['source_digest'])

    def test_nested_node_strict_default_refuses_composite_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=checkout(Path(tmp),npm_sources())
            with self.assertRaises(NodeError):
                inspect_node_job(root,['npm','run','test'],{'ci_node_project_dir':'web'})

    def test_nested_node_unreviewed_policy_and_python_versions_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=checkout(Path(tmp),npm_sources())
            for config in ({'ci_node_script_policy':'any'},
                           {'ci_node_script_policy':'reviewed-composite','ci_python_version':'3.8'}):
                with self.subTest(config=config), self.assertRaises(NodeError):
                    inspect_node_job(root,['npm','run','test'],{'ci_node_project_dir':'web',**config})

    def test_convention_module_gradle_admitted_without_android_sibling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=checkout(Path(tmp),gradle_sources())
            row=inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':desktop:test'],
                  {'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21','ci_jvm_tool_version':'9.1.0'})
            self.assertEqual(row['selected'],':desktop')
            self.assertEqual(set(row['project']['modules']),{':desktop',':core'})
            self.assertIn('build-logic/convention/src/main/kotlin/DesktopConventionPlugin.kt',row['project']['source_sha256'])

    def test_android_module_in_convention_closure_denied(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=checkout(Path(tmp),gradle_sources())
            (root/'desktop/build.gradle.kts').write_text('plugins { id("acme.desktop.application") }\ndependencies { implementation(project(":app")) }\n')
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':desktop:test'],
                     {'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21','ci_jvm_tool_version':'9.1.0'})

if __name__=='__main__':unittest.main()
