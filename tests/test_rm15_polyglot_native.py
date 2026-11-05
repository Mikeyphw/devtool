"""RM15 source-level native Node/JVM project-scope contracts (no Gradle/npm)."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devtool.actions_rm11_node import NodeError, inspect_node_job
from devtool.actions_rm12_jvm import JvmError, inspect_jvm_job


def tracked_repo(directory: Path, files: dict[str, str]) -> Path:
    subprocess.run(['git','init','-q',str(directory)],check=True,capture_output=True)
    for rel, body in files.items():
        item=directory/rel
        item.parent.mkdir(parents=True,exist_ok=True)
        item.write_text(body,encoding='utf-8')
    subprocess.run(['git','-C',str(directory),'add','-A'],check=True,capture_output=True)
    return directory


def node_inputs() -> dict[str,str]:
    return {
        'web/package.json':json.dumps({
            'name':'portable-web','version':'1.0.0','packageManager':'npm@10.9.4',
            'scripts':{'test':'node --test --test-reporter=junit --test-reporter-destination=dist/test-results.xml'}
        }),
        'web/package-lock.json':json.dumps({'name':'portable-web','lockfileVersion':3,
                                             'packages':{'':{'name':'portable-web','version':'1.0.0'}}}),
    }


def jvm_inputs() -> dict[str,str]:
    return {
        'settings.gradle.kts':'rootProject.name = "Portable"\ninclude(\n    ":app",\n    ":desktop",\n)\n',
        'build.gradle.kts':'plugins { id("java") apply false }\n',
        'app/build.gradle.kts':'plugins { id("com.android.application") }\n',
        'desktop/build.gradle.kts':'plugins { id("java") }\njava { toolchain { languageVersion.set(JavaLanguageVersion.of(21)) } }\n',
        'gradle/verification-metadata.xml':'<verification-metadata/>\n',
    }


class NativePolyglotScopes(unittest.TestCase):
    def test_nested_node_project_first_class_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),node_inputs())
            row=inspect_node_job(root,['npm','run','test'],{'ci_node_project_dir':'web','ci_node_version':'22'})
            self.assertEqual(row['provider'],'node')
            self.assertEqual(row['project_dir'],'web')
            self.assertEqual(row['kind'],'test')
            self.assertIn('package.json',row['lockfiles'])
            self.assertEqual(row['workspace']['packages']['']['name'],'portable-web')

    def test_node_project_escape_and_untracked_source_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),node_inputs())
            for value in ('../web','/web','web/../web','web//src'):
                with self.subTest(value=value),self.assertRaises(NodeError):
                    inspect_node_job(root,['npm','run','test'],{'ci_node_project_dir':value})
            (root/'web/package-lock.json').unlink()
            with self.assertRaises(NodeError):
                inspect_node_job(root,['npm','run','test'],{'ci_node_project_dir':'web'})

    def test_node_project_symlink_and_gitlink_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Construct a parent with a separate nested Git repo. Root tracks
            # the gitlink only, not web/package.json and web/package-lock.json.
            parent=Path(tmp)/'parent'
            parent.mkdir()
            subprocess.run(['git','init','-q',str(parent)],check=True,capture_output=True)
            inner=parent/'web'
            inner.mkdir()
            subprocess.run(['git','init','-q',str(inner)],check=True,capture_output=True)
            for rel,body in node_inputs().items():
                (inner/Path(rel).name).write_text(body,encoding='utf-8')
            subprocess.run(['git','-C',str(inner),'add','-A'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(inner),'-c','user.name=CI','-c','user.email=ci@example.invalid',
                            'commit','-qm','nested'],check=True,capture_output=True)
            subprocess.run(['git','-C',str(parent),'add','web'],check=True,capture_output=True)
            with self.assertRaises(NodeError):
                inspect_node_job(parent,['npm','run','test'],{'ci_node_project_dir':'web'})

    def test_mixed_gradle_jvm_only_closure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),jvm_inputs())
            args=['gradle','--offline','--no-daemon','--console=plain',':desktop:test']
            row=inspect_jvm_job(root,args,{'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21',
                                          'ci_jvm_tool_version':'9.1.0'})
            self.assertEqual(row['provider'],'jvm')
            self.assertEqual(row['mixed_module'],':desktop')
            self.assertEqual(set(row['project']['modules']),{':desktop'})
            self.assertEqual(row['selected'],':desktop')
            self.assertEqual(row['project']['modules'][':desktop']['jdk_pin'],'21')

    def test_mixed_gradle_refuses_android_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),jvm_inputs())
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':app:test'],
                                {'ci_jvm_mixed_module':':app','ci_jdk_version':'21',
                                 'ci_jvm_tool_version':'9.1.0'})

    def test_mixed_gradle_refuses_mismatched_task_or_dynamic_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),jvm_inputs())
            args=['gradle','--offline','--no-daemon','--console=plain',':app:test']
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,args,{'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21',
                                           'ci_jvm_tool_version':'9.1.0'})
            (root/'settings.gradle.kts').write_text('include(someVariable)\n')
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':desktop:test'],
                                {'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21',
                                 'ci_jvm_tool_version':'9.1.0'})

    def test_mixed_gradle_android_dependency_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=tracked_repo(Path(tmp),jvm_inputs())
            (root/'desktop/build.gradle.kts').write_text('plugins { id("java") }\ndependencies { implementation(project(":app")) }\n')
            with self.assertRaises(JvmError):
                inspect_jvm_job(root,['gradle','--offline','--no-daemon','--console=plain',':desktop:test'],
                                {'ci_jvm_mixed_module':':desktop','ci_jdk_version':'21',
                                 'ci_jvm_tool_version':'9.1.0'})


if __name__ == '__main__':
    unittest.main()
