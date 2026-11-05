"""RM17 generic Node/JVM static acceptance and hostile input regressions."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devtool.actions_rm17_source_review import SourceReviewError, review_node_pipeline, review_jvm_conventions


def checkout(path: Path, entries: dict[str, str]) -> Path:
    subprocess.run(['git','init','-q',str(path)], check=True)
    for name, data in entries.items():
        file = path/name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(data, encoding='utf8')
    subprocess.run(['git','-C',str(path),'add','-A'], check=True)
    return path


def npm_sources() -> dict[str, str]:
    return {
        'web/package.json': json.dumps({'name':'reusable-web','version':'1.0.0','packageManager':'npm@10.9.4',
           'scripts': {'test': "npm run validate && node scripts/check.mjs && python3 -m unittest discover -s tests -p 'test_*.py'",
                       'validate':'python3 ../scripts/lint.py --all',
                       'build':'npm run validate && node scripts/build.mjs'}}),
        'web/package-lock.json': json.dumps({'name':'reusable-web','lockfileVersion':3,'packages':{'':{'name':'reusable-web','version':'1.0.0'}}}),
        'web/scripts/check.mjs': "console.log('check')\n",
        'web/scripts/build.mjs': "console.log('build')\n",
        'web/tests/test_a.py': 'def test_something(): pass\n',
        'scripts/lint.py': 'print("ok")\n',
    }


def npm_packages():
    return {'': {'scripts': {
        'test': "npm run validate && node scripts/check.mjs && python3 -m unittest discover -s tests -p 'test_*.py'",
        'validate': 'python3 ../scripts/lint.py --all',
        'build': 'npm run validate && node scripts/build.mjs'}}}


def gradle_sources():
    return {
       'settings.gradle.kts': 'pluginManagement { includeBuild("build-logic") }\ninclude(":app", ":desktop", ":core")\n',
       'gradle/verification-metadata.xml': '<verification-metadata/>\n',
       'app/build.gradle.kts': 'plugins { id("com.android.application") }\n',
       'desktop/build.gradle.kts': 'plugins { id("acme.desktop.application") }\ndependencies { implementation(project(":core")) }\n',
       'core/build.gradle.kts': 'plugins { id("acme.kotlin.library") }\n',
       'build-logic/settings.gradle.kts': 'rootProject.name = "logic"\n',
       'build-logic/convention/build.gradle.kts': '''plugins { `kotlin-dsl` }
         gradlePlugin { plugins {
           register("desktop") { id = "acme.desktop.application"; implementationClass = "DesktopConventionPlugin" }
           register("library") { id = "acme.kotlin.library"; implementationClass = "LibraryConventionPlugin" }
           register("android") { id = "acme.android.application"; implementationClass = "AndroidConventionPlugin" }
         } }\n''',
       'build-logic/convention/src/main/kotlin/DesktopConventionPlugin.kt': '''class DesktopConventionPlugin {
         fun apply(target: Project) { pluginManager.apply("org.jetbrains.kotlin.jvm")
           pluginManager.apply("org.jetbrains.compose") } }\n''',
       'build-logic/convention/src/main/kotlin/LibraryConventionPlugin.kt': '''class LibraryConventionPlugin {
         fun apply(target: Project) { pluginManager.apply("org.jetbrains.kotlin.jvm") } }\n''',
       'build-logic/convention/src/main/kotlin/AndroidConventionPlugin.kt': '''class AndroidConventionPlugin {
         fun apply(target: Project) { pluginManager.apply("com.android.application") } }\n''',
    }


class GenericReview(unittest.TestCase):
    def test_composed_js_python_source_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),npm_sources())
            row=review_node_pipeline(root,root/'web',npm_packages(),manager='npm')
            self.assertIn('scripts/lint.py',row['source_sha256'])
            self.assertIn('web/scripts/build.mjs',row['source_sha256'])
            self.assertIn('web/tests/test_a.py',row['source_sha256'])
            self.assertFalse(row['release_qualified'])

    def test_composed_script_injection_and_cycles_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),npm_sources())
            for value in ('npm run validate; rm -rf /', 'npm run validate || true',
                          'node scripts/check.mjs > ignored', 'node -e "bad"',
                          'python3 -c "print(1)"', 'npm run test'):
                with self.subTest(value=value):
                    p=npm_packages()
                    p['']['scripts']['test']=value
                    with self.assertRaises(SourceReviewError):
                        review_node_pipeline(root,root/'web',p,manager='npm')

    def test_composed_outside_checkout_and_untracked_sources_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),npm_sources())
            p=npm_packages();p['']['scripts']['validate']='python3 ../../../outside.py'
            with self.assertRaises(SourceReviewError):
                review_node_pipeline(root,root/'web',p,manager='npm')
            p=npm_packages();p['']['scripts']['validate']='python3 scripts/new.py'
            (root/'web/scripts/new.py').write_text('print(1)\n')
            with self.assertRaises(SourceReviewError):
                review_node_pipeline(root,root/'web',p,manager='npm')

    def test_literal_gradle_convention_resolves_jvm_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),gradle_sources())
            a=review_jvm_conventions(root,'desktop/build.gradle.kts')
            self.assertEqual(a['identity'],'jvm')
            self.assertIn('build-logic/convention/src/main/kotlin/DesktopConventionPlugin.kt',a['source_sha256'])
            self.assertFalse(a['android_lint_adjacent'])
            self.assertFalse(a['release_qualified'])

    def test_gradle_convention_unknown_android_and_dynamic_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),gradle_sources())
            dest=root/'desktop/build.gradle.kts'
            for data in ('plugins { id("com.android.application") }',
                         'plugins { id("acme.missing") }',
                         'plugins { id(variable) }',
                         'plugins { alias(libs.plugins.unknown) }'):
                dest.write_text(data)
                with self.subTest(data=data),self.assertRaises(SourceReviewError):
                    review_jvm_conventions(root,'desktop/build.gradle.kts')

    def test_gradle_convention_impl_untracked_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),gradle_sources())
            (root/'build-logic/convention/src/main/kotlin/DesktopConventionPlugin.kt').unlink()
            with self.assertRaises(SourceReviewError):
                review_jvm_conventions(root,'desktop/build.gradle.kts')

    def test_gradle_convention_recursive_plugin_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),gradle_sources())
            impl=root/'build-logic/convention/src/main/kotlin/DesktopConventionPlugin.kt'
            impl.write_text('class DesktopConventionPlugin { fun apply(target: Project) { pluginManager.apply("acme.desktop.application") } }')
            with self.assertRaises(SourceReviewError):
                review_jvm_conventions(root,'desktop/build.gradle.kts')

    def test_android_sibling_not_treated_as_jvm_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root=checkout(Path(temp),gradle_sources())
            with self.assertRaises(SourceReviewError):
                review_jvm_conventions(root,'app/build.gradle.kts')

if __name__=='__main__':unittest.main()
