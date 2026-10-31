"""Reviewed, GitHub-hosted Android Gradle *debug* job admission.

Only static source review; never assumes a local Android SDK, signer, emulator,
root authority or authenticated package provenance. Native target-owned command
jobs remain the execution authority. No Termux-specific runner bridge is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from .actions_rm08_projects import _trusted, ProjectReviewError


class AndroidProviderError(ValueError):
    pass


@dataclass(frozen=True)
class AndroidGradleProvider:
    api_version: int = 1
    provider_id: str = 'android-gradle'

    def inspect(self, root: Path, job: Any, argv: list[str]) -> dict[str, Any]:
        meta = job.metadata or {}
        # A Gradle release task can consume signing configuration from the
        # project; it must never be accepted by a credential-free CI provider.
        # Restrict execution to an exact wrapper and bounded debug tasks.
        if not 2 <= len(argv) <= 3 or argv[0] != './gradlew':
            raise AndroidProviderError('Android CI requires direct ./gradlew with one reviewed debug task')
        tail = argv[1:]
        if tail[0] == '--no-daemon':
            tail = tail[1:]
        if len(tail) != 1:
            raise AndroidProviderError('Android CI rejects extra Gradle flags and injected tasks')
        task = tail[0]
        match = re.fullmatch(r':([A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*){0,5}):(assembleDebug|bundleDebug|lintDebug|testDebugUnitTest)', task)
        if match is None:
            raise AndroidProviderError('Android CI accepts only module-scoped debug build/lint/unit tests; no release/device/signing')
        operation = match.group(2)
        module = match.group(1).split(':')
        if meta.get('ci_toolchain') != 'android-gradle':
            raise AndroidProviderError('Android Gradle provider requires explicit ci_toolchain=android-gradle')
        java = meta.get('ci_android_java_version')
        if java not in ('17', '21'):
            raise AndroidProviderError('Android CI requires explicit reviewed JDK 17 or 21')
        sdk = meta.get('ci_android_sdk_packages')
        if (not isinstance(sdk, list) or not 2 <= len(sdk) <= 6 or
                len(set(map(str,sdk))) != len(sdk) or
                any(not isinstance(x,str) or not re.fullmatch(r'(?:platform-tools|platforms;android-(?:3[3-9]|[4-5][0-9])|build-tools;[0-9]{2}\.[0-9]\.[0-9])',x) for x in sdk) or
                not any(x.startswith('platforms;') for x in sdk) or
                not any(x.startswith('build-tools;') for x in sdk)):
            raise AndroidProviderError('Android CI requires bounded reviewed platform/build-tools SDK package pins')
        try:
            wrapper = _trusted(root, 'gradlew')
            props = _trusted(root, 'gradle/wrapper/gradle-wrapper.properties')
            jar = _trusted(root, 'gradle/wrapper/gradle-wrapper.jar')
            # A Gradle module build file must exist and be tracked. Requiring
            # it does not claim its contents are independently trustworthy.
            module_dir = '/'.join(module)
            if not any(_trusted(root, module_dir + suffix, required=False) is not None
                       for suffix in ('/build.gradle.kts', '/build.gradle')):
                raise AndroidProviderError('Android CI module build file must be tracked')
            settings = next((name for name in ('settings.gradle.kts','settings.gradle')
                             if _trusted(root,name,required=False) is not None),None)
            if settings is None:
                raise AndroidProviderError('Android CI requires tracked Gradle settings')
        except (ProjectReviewError,OSError,UnicodeError) as exc:
            raise AndroidProviderError('Android CI source review refused: '+str(exc)[:180]) from None
        if b'GradleWrapperMain' not in wrapper or len(jar) < 16 or not (root/'gradlew').stat().st_mode & 0o111:
            raise AndroidProviderError('Android CI missing an executable Gradle wrapper contract')
        jar_pin = meta.get('ci_android_wrapper_jar_sha256')
        if (not isinstance(jar_pin,str) or not re.fullmatch(r'[a-f0-9]{64}',jar_pin)
                or jar_pin != hashlib.sha256(jar).hexdigest()):
            raise AndroidProviderError('Android CI requires reviewed ci_android_wrapper_jar_sha256 matching tracked wrapper bytes')
        properties = props.decode('utf-8')
        # Requiring a distribution hash prevents blind wrapper distribution
        # changes, but does not attest Gradle code or Android packages.
        checksum = re.search(r'(?m)^distributionSha256Sum=([a-f0-9]{64})\s*$', properties)
        distribution = re.search(r'(?m)^distributionUrl=https\\?://services\.gradle\.org/distributions/gradle-([0-9]+\.[0-9]+(?:\.[0-9]+)?)-(?:bin|all)\.zip\s*$', properties)
        if not checksum or not distribution:
            raise AndroidProviderError('Android CI requires an official HTTPS Gradle distribution and distributionSha256Sum')
        # Do not allow external URL redirects or unreviewed wrapper options.
        if any(s in properties for s in ('distributionUrl=http\\:', 'distributionUrl=file\\:', 'distributionUrl=ftp\\:')):
            raise AndroidProviderError('Android CI refuses alternate Gradle distribution origins')
        outputs = list(job.outputs or [])
        if operation in ('assembleDebug', 'bundleDebug'):
            ext = 'apk' if operation == 'assembleDebug' else 'aab'
            folder = 'apk/debug' if ext == 'apk' else 'bundle/debug'
            expected_prefix = module_dir + '/build/outputs/' + folder + '/'
            if len(outputs) != 1 or not outputs[0].startswith(expected_prefix) or not re.fullmatch(r'[A-Za-z0-9_-]+\.'+ext, outputs[0][len(expected_prefix):]):
                raise AndroidProviderError('Android debug build must own exactly one declared APK/AAB path')
        elif outputs:
            raise AndroidProviderError('Android source/unit checks may not assert unverified build outputs')
        if meta.get('read_only') is True and outputs:
            raise AndroidProviderError('Android build cannot declare read_only=true')
        return {
            'provider':self.provider_id, 'api_version':self.api_version,
            'toolchain':'android-gradle', 'version':java,'matrix_versions':[java],
            'version_source':'ci_android_java_version',
            'java_version':java, 'sdk_packages':sdk,
            'task':task, 'operation':operation,
            'wrapper_distribution_sha256':checksum.group(1),
            'wrapper_version':distribution.group(1),
            'wrapper_jar_sha256':hashlib.sha256(jar).hexdigest(),
            'lockfiles':['gradlew','gradle/wrapper/gradle-wrapper.properties',
                         'gradle/wrapper/gradle-wrapper.jar',settings,
                         module_dir+('/build.gradle.kts' if _trusted(root,module_dir+'/build.gradle.kts',required=False) is not None else '/build.gradle')],
            'cache_enabled':False, 'report_adapter':'none',
            'output_role':'local-bytes-only', 'release_qualified':False,
            'device_qualified':False, 'signer_authority':False,
        }
