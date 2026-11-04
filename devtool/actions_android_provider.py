"""Reviewed, GitHub-hosted Android Gradle *debug* job admission.

Only static source review; never assumes a local Android SDK, signer, emulator,
root authority or authenticated package provenance. Native target-owned command
jobs remain the execution authority. No Termux-specific runner bridge is used.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
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
        """Admit a reviewable hosted-only Gradle command, never arbitrary shell code.

        R36 adds nested project roots, flavor debug variants and bounded groups
        of tasks. Every node still invokes the canonical native RM-06 job.
        """
        meta = job.metadata or {}
        project_dir = meta.get('ci_android_project_dir', '')
        if not isinstance(project_dir, str) or (project_dir and not re.fullmatch(
                r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){0,5}', project_dir)):
            raise AndroidProviderError('Android CI project_dir must be a bounded repository-relative directory')
        prefix = project_dir + '/' if project_dir else ''
        project = root / project_dir
        # Jobs with a target root use that root as cwd, never a caller-selected
        # wrapper in a different directory. No traversal or symlinks allowed.
        effective_root = job.root or meta.get('target_root') or str(root)
        if Path(effective_root).resolve(strict=False) != project.resolve(strict=False):
            raise AndroidProviderError('Android CI job/target root must equal its reviewed Gradle project_dir')
        if project.is_symlink() or any((root / '/'.join(project_dir.split('/')[:i])).is_symlink()
                                        for i in range(1, len(project_dir.split('/')) + 1)):
            raise AndroidProviderError('Android CI refuses symlinked project directory')
        if not 2 <= len(argv) <= 5 or argv[0] != './gradlew':
            raise AndroidProviderError('Android CI requires direct ./gradlew with reviewed debug tasks; extra arguments refused')
        tail = argv[1:]
        if tail and tail[0] == '--no-daemon':
            tail = tail[1:]
        if not 1 <= len(tail) <= 3 or len(set(tail)) != len(tail):
            raise AndroidProviderError('Android CI refuses extra flags, duplicate or unreviewed multi-task requests')
        variant = meta.get('ci_android_debug_variant', 'debug')
        if not isinstance(variant, str) or not re.fullmatch(r'[a-z][A-Za-z0-9]{0,39}Debug|debug', variant):
            raise AndroidProviderError('Android CI requires a bounded named debug variant ending in Debug')
        # `ci_android_debug_variant` is the actual Gradle variant, not a generic
        # release/flavor string. Only Debug build-type operations are eligible.
        cap_variant = variant[0].upper() + variant[1:]
        variants = ('assemble' + cap_variant, 'bundle' + cap_variant,
                    'lint' + cap_variant, 'test' + cap_variant + 'UnitTest')
        tasks = []
        module = ''
        operations = []
        for task in tail:
            match = re.fullmatch(r':([A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*){0,5}):([A-Za-z][A-Za-z0-9]*)', task)
            if match is None or match.group(2) not in variants:
                raise AndroidProviderError('Android CI requires reviewed debug tasks; rejects extra or unreviewed tasks and release/device/signing')
            if module and module != match.group(1):
                raise AndroidProviderError('Android CI multi-task group must use one module')
            module = match.group(1)
            tasks.append(task)
            operations.append(('assemble' if match.group(2) == variants[0] else
                               'bundle' if match.group(2) == variants[1] else
                               'lint' if match.group(2) == variants[2] else 'test'))
        if operations.count('assemble') + operations.count('bundle') > 1:
            raise AndroidProviderError('Android CI only permits one build producer per multi-task job')
        if meta.get('ci_toolchain') != 'android-gradle':
            raise AndroidProviderError('Android Gradle provider requires explicit ci_toolchain=android-gradle')
        java = meta.get('ci_android_java_version')
        if java not in ('17', '21'):
            raise AndroidProviderError('Android CI requires explicit reviewed JDK 17 or 21')
        sdk = meta.get('ci_android_sdk_packages')
        if (not isinstance(sdk, list) or not 2 <= len(sdk) <= 6 or
                len(set(map(str,sdk))) != len(sdk) or
                any(not isinstance(x,str) or not re.fullmatch(r'(?:platform-tools|platforms;android-(?:3[3-9]|[4-5][0-9])|build-tools;[0-9]{2}\.[0-9]\.[0-9]|ndk;29\.0\.14206865)',x) for x in sdk) or
                not any(x.startswith('platforms;') for x in sdk) or
                not any(x.startswith('build-tools;') for x in sdk)):
            raise AndroidProviderError('Android CI requires bounded reviewed platform/build-tools SDK package pins')
        ndk_version = meta.get('ci_android_ndk_version', '')
        if ndk_version and (ndk_version != '29.0.14206865' or
                            'ndk;' + ndk_version not in sdk):
            raise AndroidProviderError('SFDT05 hosted Android NDK must be exact, reviewed and installed')
        host_profile = meta.get('ci_android_host_profile', '')
        if host_profile not in ('', 'studyflow-ubuntu24'):
            raise AndroidProviderError('SFDT05 unknown Android host affinity/profile')
        if host_profile == 'studyflow-ubuntu24' and (
                java != '21' or ndk_version != '29.0.14206865' or
                'platforms;android-36' not in sdk or 'build-tools;36.0.0' not in sdk):
            raise AndroidProviderError('SFDT05 StudyFlow host requires JDK21, SDK36, build-tools36 and NDK29')
        module_dir = module.replace(':', '/')
        try:
            wrapper = _trusted(root, prefix + 'gradlew')
            props = _trusted(root, prefix + 'gradle/wrapper/gradle-wrapper.properties')
            jar = _trusted(root, prefix + 'gradle/wrapper/gradle-wrapper.jar')
            module_build = next((prefix + module_dir + suffix for suffix in ('/build.gradle.kts', '/build.gradle')
                                 if _trusted(root, prefix + module_dir + suffix, required=False) is not None), None)
            if module_build is None:
                raise AndroidProviderError('Android CI module build file must be tracked')
            settings = next((prefix + name for name in ('settings.gradle.kts','settings.gradle')
                             if _trusted(root, prefix + name, required=False) is not None), None)
            if settings is None:
                raise AndroidProviderError('Android CI requires tracked Gradle settings')
        except (ProjectReviewError, OSError, UnicodeError) as exc:
            raise AndroidProviderError('Android CI source review refused: '+str(exc)[:180]) from None
        # The official Gradle POSIX launcher has two reviewed generations:
        # older wrappers invoke GradleWrapperMain directly; Gradle 8+ uses
        # `-jar "$APP_HOME/gradle/wrapper/gradle-wrapper.jar"` and then the
        # canonical `exec "$JAVACMD" "$@"` tail. Reject quoted comments,
        # alternate JAR paths and launchers missing the real exec line.
        legacy_launcher = b'org.gradle.wrapper.GradleWrapperMain' in wrapper
        modern_launcher = bool(
            re.search(rb'(?m)^[ \t]*-jar[ \t]+"\$APP_HOME/gradle/wrapper/gradle-wrapper[.]jar"[ \t]*\\[ \t]*$', wrapper)
            and re.search(rb'(?m)^[ \t]*exec[ \t]+"\$JAVACMD"[ \t]+"\$@"[ \t]*$', wrapper)
        )
        if not (legacy_launcher or modern_launcher) or len(jar) < 16 or not (project/'gradlew').stat().st_mode & 0o111:
            raise AndroidProviderError('Android CI missing an executable Gradle wrapper contract')
        jar_pin = meta.get('ci_android_wrapper_jar_sha256')
        if (not isinstance(jar_pin,str) or not re.fullmatch(r'[a-f0-9]{64}',jar_pin)
                or jar_pin != hashlib.sha256(jar).hexdigest()):
            raise AndroidProviderError('Android CI requires reviewed ci_android_wrapper_jar_sha256 matching tracked wrapper bytes')
        properties = props.decode('utf-8')
        checksum = re.search(r'(?m)^distributionSha256Sum=([a-f0-9]{64})\s*$', properties)
        distribution = re.search(r'(?m)^distributionUrl=https\\?://services\.gradle\.org/distributions/gradle-([0-9]+\.[0-9]+(?:\.[0-9]+)?)-(?:bin|all)\.zip\s*$', properties)
        if not checksum or not distribution:
            raise AndroidProviderError('Android CI requires an official HTTPS Gradle distribution and distributionSha256Sum')
        if any(s in properties for s in ('distributionUrl=http\\:', 'distributionUrl=file\\:', 'distributionUrl=ftp\\:')):
            raise AndroidProviderError('Android CI refuses alternate Gradle distribution origins')
        if host_profile == 'studyflow-ubuntu24' and distribution.group(1) != '9.7.1':
            raise AndroidProviderError('SFDT05 StudyFlow host requires Gradle wrapper 9.7.1')
        outs = list(job.outputs or [])
        build_operation = next((v for v in operations if v in ('assemble', 'bundle')), None)
        if build_operation:
            ext = 'apk' if build_operation == 'assemble' else 'aab'
            # Android Gradle Plugin's default debug and single-flavor paths.
            # For multi-dimension flavors, require an exact reviewed override.
            flavor = variant[:-5] if variant != 'debug' else ''
            default_subdir = (flavor[0].lower() + flavor[1:] + '/debug') if flavor else 'debug'
            subdir = meta.get('ci_android_output_subdir', default_subdir if ext == 'apk' else variant)
            if not isinstance(subdir, str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+){0,3}', subdir):
                raise AndroidProviderError('Android CI requires bounded reviewed debug output subdir')
            if ext == 'apk' and not subdir.endswith('/debug') and subdir != 'debug':
                raise AndroidProviderError('Android APK output must belong to Debug build type')
            folder = 'apk' if ext == 'apk' else 'bundle'
            expected_prefix = prefix + module_dir + '/build/outputs/' + folder + '/' + subdir + '/'
            if len(outs) != 1 or not outs[0].startswith(expected_prefix) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.' + ext, outs[0][len(expected_prefix):]):
                raise AndroidProviderError('Android debug build must own exactly one declared APK/AAB path')
        elif outs:
            raise AndroidProviderError('Android source/unit checks may not assert unverified build outputs')
        pinned = meta.get('ci_android_expected_output')
        if pinned is not None and (len(outs)!=1 or pinned!=outs[0]):
            raise AndroidProviderError('Android CI expected output pin must equal actual declared output')
        if meta.get('read_only') is True and outs:
            raise AndroidProviderError('Android build cannot declare read_only=true')
        if any(meta.get(k) is True for k in ('signing_required', 'device_required', 'release_signed', 'root_required')):
            raise AndroidProviderError('Android CI refuses signing, device and root authority')
        return {
            'provider':self.provider_id, 'api_version':self.api_version,
            'toolchain':'android-gradle', 'version':java,'matrix_versions':[java],
            'version_source':'ci_android_java_version',
            'java_version':java, 'sdk_packages':sdk, 'ndk_version':ndk_version, 'host_profile':host_profile,
            'task':tasks[-1], 'tasks':tasks, 'operation':build_operation or operations[-1],
            'operations':operations, 'project_dir':project_dir, 'variant':variant,
            'wrapper_distribution_sha256':checksum.group(1),
            'wrapper_version':distribution.group(1),
            'wrapper_jar_sha256':hashlib.sha256(jar).hexdigest(),
            'lockfiles':[prefix+'gradlew',prefix+'gradle/wrapper/gradle-wrapper.properties',
                         prefix+'gradle/wrapper/gradle-wrapper.jar',settings,module_build],
            'cache_enabled':False, 'report_adapter':'none',
            'output_role':'local-bytes-only', 'release_qualified':False,
            'device_qualified':False, 'signer_authority':False,
        }


def hosted_semantic_spec(root: Path, job: Any, review: dict[str, Any]):
    """Lower admitted semantic Android jobs to the direct reviewed hosted wrapper.

    The normal local Android/Gradle runner builds through a Termux-oriented
    build-apk helper; it cannot be used as the GitHub Actions execution plan.
    This boundary is called only from RM-06 run-node after frozen graph/source
    and exact run/attempt admission. It NEVER runs or invokes Gradle itself.
    """
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            os.environ.get('RUNNER_OS') != 'Linux' or
            os.environ.get('RUNNER_ARCH') != 'X64'):
        raise AndroidProviderError('R36 semantic Gradle execution requires real Linux GitHub Actions runner')
    project_dir = review.get('project_dir')
    tasks = review.get('tasks')
    if (review.get('semantic_request') is not True or
            not isinstance(project_dir, str) or
            not isinstance(tasks, list) or
            not tasks or len(tasks)>3 or
            job.runner not in ('gradle','android') or
            list(job.gradle_tasks) != tasks or
            job.command or job.action or job.shell_command or job.env):
        raise AndroidProviderError('R36 semantic job differs from frozen reviewed Gradle tasks')
    # Re-admit the actual source-owned job, not just its frozen JSON summary.
    checked = AndroidGradleProvider().inspect(root, job, ['./gradlew','--no-daemon',*tasks])
    if checked['tasks'] != tasks or checked['project_dir'] != project_dir:
        raise AndroidProviderError('R36 semantic source and frozen review disagree')
    from .models import CommandSpec
    return CommandSpec(name='job:'+job.name,
                       argv=['./gradlew','--no-daemon',*tasks],
                       cwd=str((root/project_dir).resolve(strict=False)), env={},
                       metadata={'r36_reviewed_hosted_semantic': True,
                                 'gradle_semantic_request': True,
                                 'ci_toolchain': 'android-gradle'})
