"""SFDT05 provider and hosted receipt acceptance (source-only; never invokes Gradle)."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from devtool.actions_android_provider import AndroidGradleProvider, AndroidProviderError
from devtool.actions_graph_export import _sfdt05_android_setup, _sfdt05_attempt_yaml


def git(repo: Path, *args: str) -> str:
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


class HostedAdmission(unittest.TestCase):
    def repo(self, *, gradle: str = '9.7.1'):
        ctx = tempfile.TemporaryDirectory(prefix='sfdt05-')
        root = Path(ctx.name)
        (root / 'gradle/wrapper').mkdir(parents=True)
        (root / 'app').mkdir()
        (root / 'settings.gradle.kts').write_text('include(":app")\n')
        (root / 'app/build.gradle.kts').write_text('plugins { id("com.android.application") }\n')
        wrapper = root / 'gradlew'
        wrapper.write_text('#!/bin/sh\nexec java -classpath \"$APP_HOME/gradle/wrapper/gradle-wrapper.jar\" org.gradle.wrapper.GradleWrapperMain \"$@\"\n')
        wrapper.chmod(0o755)
        jar = root / 'gradle/wrapper/gradle-wrapper.jar'
        jar.write_bytes(b'FROZEN-JAR-FIXTURE-DO-NOT-EXECUTE' * 2)
        props = root / 'gradle/wrapper/gradle-wrapper.properties'
        props.write_text('distributionSha256Sum=' + 'a' * 64 + '\n'
                         + f'distributionUrl=https\\://services.gradle.org/distributions/gradle-{gradle}-bin.zip\n')
        git(root, 'init', '-q')
        git(root, '-c', 'user.name=SFDT', '-c', 'user.email=test@invalid.example', 'add', '-A')
        git(root, '-c', 'user.name=SFDT', '-c', 'user.email=test@invalid.example', 'commit', '-qm', 'fixture')
        meta = {
            'ci_toolchain': 'android-gradle', 'ci_android_java_version': '21',
            'ci_android_sdk_packages': ['platforms;android-36', 'build-tools;36.0.0', 'ndk;29.0.14206865'],
            'ci_android_ndk_version': '29.0.14206865',
            'ci_android_host_profile': 'studyflow-ubuntu24',
            'ci_android_wrapper_jar_sha256': hashlib.sha256(jar.read_bytes()).hexdigest(),
        }
        job = SimpleNamespace(root=str(root), metadata=meta, outputs=[])
        return ctx, root, job

    def test_strict_studyflow_toolchain_admitted_without_gradle(self):
        ctx, root, job = self.repo()
        with ctx:
            proof = AndroidGradleProvider().inspect(root, job, ['./gradlew', '--no-daemon', ':app:testDebugUnitTest'])
            self.assertEqual(proof['java_version'], '21')
            self.assertEqual(proof['ndk_version'], '29.0.14206865')
            self.assertEqual(proof['host_profile'], 'studyflow-ubuntu24')
            self.assertEqual(proof['wrapper_version'], '9.7.1')
            self.assertEqual(proof['tasks'], [':app:testDebugUnitTest'])

    def test_refuses_missing_ndk_package(self):
        ctx, root, job = self.repo()
        with ctx:
            job.metadata['ci_android_sdk_packages'].remove('ndk;29.0.14206865')
            with self.assertRaisesRegex(AndroidProviderError, 'NDK'):
                AndroidGradleProvider().inspect(root, job, ['./gradlew', ':app:lintDebug'])

    def test_refuses_unreviewed_ndk_version(self):
        ctx, root, job = self.repo()
        with ctx:
            job.metadata['ci_android_ndk_version'] = '28.0.0'
            with self.assertRaises(AndroidProviderError):
                AndroidGradleProvider().inspect(root, job, ['./gradlew', ':app:lintDebug'])

    def test_refuses_wrong_jdk(self):
        ctx, root, job = self.repo()
        with ctx:
            job.metadata['ci_android_java_version'] = '17'
            with self.assertRaisesRegex(AndroidProviderError, 'JDK21'):
                AndroidGradleProvider().inspect(root, job, ['./gradlew', ':app:lintDebug'])

    def test_refuses_wrong_wrapper_version(self):
        ctx, root, job = self.repo(gradle='9.6.0')
        with ctx:
            with self.assertRaisesRegex(AndroidProviderError, '9.7.1'):
                AndroidGradleProvider().inspect(root, job, ['./gradlew', ':app:lintDebug'])

    def test_refuses_unknown_profile(self):
        ctx, root, job = self.repo()
        with ctx:
            job.metadata['ci_android_host_profile'] = 'termux-android'
            with self.assertRaisesRegex(AndroidProviderError, 'profile'):
                AndroidGradleProvider().inspect(root, job, ['./gradlew', ':app:lintDebug'])

    def test_generated_host_setup_is_pinned_and_non_gradle(self):
        yaml = _sfdt05_android_setup({'android_review': {
            'java_version': '21',
            'sdk_packages': ['platforms;android-36', 'build-tools;36.0.0', 'ndk;29.0.14206865'],
            'ndk_version': '29.0.14206865',
        }})
        for marker in ('java-version: "21"', '"$ANDROID_HOME/platforms/android-36"',
                       '"$ANDROID_HOME/build-tools/36.0.0"', '"$ANDROID_HOME/ndk/29.0.14206865"',
                       'cache-disabled: true', 'cmdline-tools-version: 15859902'):
            self.assertIn(marker, yaml)
        self.assertNotIn('./gradlew', yaml)
        self.assertNotIn('signing', yaml.lower())

    def attempt(self, root: Path, temp: Path, *, native: str, job_status: str,
                evidence: bool, source_drift: bool = False):
        sha = git(root, 'rev-parse', 'HEAD')
        destination = temp / 'rm06-evidence/node-01'
        destination.mkdir(parents=True, exist_ok=True)
        if evidence:
            (destination / 'node-evidence.json').write_text('{"mocked": true}\n')
        yaml = _sfdt05_attempt_yaml('node-01', 'a' * 40, 'b' * 64)
        # Strip YAML block indentation to reconstruct the exact runner-side script.
        command = '\n'.join(line[10:] if line.startswith(' ' * 10) else line
                            for line in yaml.splitlines()) + '\n'
        env = dict(os.environ, RUNNER_TEMP=str(temp), GITHUB_SHA=('0'*40 if source_drift else sha),
                   GITHUB_RUN_ID='51', GITHUB_RUN_ATTEMPT='2',
                   SFDT_NATIVE_OUTCOME=native, SFDT_JOB_STATUS=job_status)
        result = subprocess.run(['bash', '-e', '-o', 'pipefail'], input=command,
                                cwd=root, env=env, capture_output=True, text=True)
        payload = json.loads((destination / 'attempt-evidence.json').read_text())
        return result, payload

    def test_missing_native_receipt_never_promotes_success(self):
        ctx, root, _ = self.repo()
        with ctx, tempfile.TemporaryDirectory() as tmp:
            result, record = self.attempt(root, Path(tmp), native='success', job_status='success', evidence=False)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertEqual(record['status'], 'UNQUALIFIED')
            self.assertFalse(record['typed_node_evidence_present'])
            self.assertFalse(record['authenticated_remote'])

    def test_failed_native_step_records_unqualified_receipt(self):
        ctx, root, _ = self.repo()
        with ctx, tempfile.TemporaryDirectory() as tmp:
            result, record = self.attempt(root, Path(tmp), native='failure', job_status='failure', evidence=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(record['status'], 'UNQUALIFIED')
            self.assertEqual(record['source_checkout_matched'], True)
            self.assertFalse(record['release_qualified'])

    def test_source_drift_cannot_promote_success(self):
        ctx, root, _ = self.repo()
        with ctx, tempfile.TemporaryDirectory() as tmp:
            result, record = self.attempt(root, Path(tmp), native='success', job_status='success',
                                          evidence=True, source_drift=True)
            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertFalse(record['source_checkout_matched'])
            self.assertEqual(record['status'], 'UNQUALIFIED')

    def test_native_success_has_only_observed_local_evidence(self):
        ctx, root, _ = self.repo()
        with ctx, tempfile.TemporaryDirectory() as tmp:
            result, record = self.attempt(root, Path(tmp), native='success', job_status='success', evidence=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(record['status'], 'OBSERVED_SUCCESS')
            self.assertFalse(record['authenticated_remote'])
            self.assertFalse(record['release_qualified'])


if __name__ == '__main__':
    unittest.main()
