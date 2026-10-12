"""Private, local Android release execution; never a substitute for ACTIONS-08 CI provenance.

GitHub Free/private repositories do not have to weaken protected-ref rules to
produce a locally signed Android package. This lane reuses the LIFE signing
identity and Devtool's Android Gradle injection, and reports local inspections
without granting CI/publishing qualification.
"""
from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
import getpass
import sys
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any

from .actions_android import _project_root, _MODULE, _MAX_PACKAGE
from .actions_setup import _signing_profile, _signer_material, _keystore, _application
from .github_actions import GitHubActionsError


def _tracked_state(root: Path) -> str:
    """Allow only a clean, committed tracked source tree for local builds."""
    try:
        proc = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                              capture_output=True, text=True, timeout=10)
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('Cannot verify clean local Git source') from None
    if (proc.returncode or proc.stdout.strip() or head.returncode or
            not re.fullmatch(r'[0-9a-f]{40}', head.stdout.strip())):
        raise GitHubActionsError('Local release requires a clean tracked Git worktree and committed HEAD')
    return head.stdout.strip()


def _artifact(path: str, root: Path, fmt: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute() or p.is_symlink():
        raise GitHubActionsError('Local artifact must be an absolute, non-symlink path')
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_size < 1 or
                    st.st_size > _MAX_PACKAGE or p.suffix.lower() != '.' + fmt):
                raise GitHubActionsError('Local release artifact must be a regular, supported APK/AAB <=256 MiB')
        finally:
            os.close(fd)
    except OSError:
        raise GitHubActionsError('Cannot open local release artifact') from None
    return p


def local_verify(root: Path, *, artifact: str, fmt: str = 'apk',
                 identity_id: str = '', target: str = 'android_hud',
                 application_id: str = '', minimum_version_code: int = 1,
                 project_dir: str = 'android/devtool-live-hud', module: str = 'app') -> dict[str, Any]:
    """Inspect file bytes and registered certificate. No CI/source attestation claims."""
    root = root.resolve()
    if fmt not in ('apk', 'aab') or type(minimum_version_code) is not int or minimum_version_code < 1:
        raise GitHubActionsError('Local package format and minimum version code are invalid')
    if not _MODULE.fullmatch(module):
        raise GitHubActionsError('Local Android module name is invalid')
    profile = _signing_profile(root, identity_id=identity_id, target=target)
    if profile.mode != 'devtool' or not profile.expected_cert_sha256 or not profile.alias:
        raise GitHubActionsError('Local verification requires a registered LIFE release signing identity')
    _keystore(profile.keystore, root)  # validate private identity file without accessing its password
    path = _artifact(artifact, root, fmt)
    from .actions_android import _inspect_apk, _inspect_aab
    details = _inspect_apk(path) if fmt == 'apk' else _inspect_aab(path)
    expected_app = _application(root, project_dir, module, application_id)
    if details['package'] != expected_app:
        raise GitHubActionsError('Local release application ID does not match the selected project')
    if details['signer_sha256'].lower() != profile.expected_cert_sha256.lower():
        raise GitHubActionsError('Local release certificate differs from the registered LIFE signer')
    if details.get('debug_signing') is not False:
        raise GitHubActionsError('Local release cannot use an Android Debug or unknown signer')
    try:
        code = int(details['version_code'])
    except (KeyError, TypeError, ValueError):
        raise GitHubActionsError('Local release versionCode could not be verified') from None
    if code < minimum_version_code:
        raise GitHubActionsError('Local release versionCode is below the required minimum')
    digest = hashlib.sha256()
    with path.open('rb') as src:
        while data := src.read(1024 * 1024):
            digest.update(data)
    return {'phase': 'ACTIONS-LOCAL', 'operation': 'verify', 'local_signature_verified': True,
            'application_id': expected_app, 'signing_identity_id': profile.identity_id,
            'signer_sha256': profile.expected_cert_sha256.lower(), 'artifact': str(path),
            'artifact_sha256': digest.hexdigest(), 'version_code': code, 'format': fmt,
            'package_inspection': details, 'qualified_release': False,
            'github_provenance_verified': False, 'source_attested': False,
            'scope': 'local signature/manifest inspection only; not ACTIONS-08 CI qualification or publishing'}



# Do not return snippets from Gradle. Android/Gradle plugins can print secret
# environment values (even on otherwise ordinary build failures). Categorize
# captured child output by *constant* diagnostic IDs only.
_GRADLE_FAILURE_MARKERS: tuple[tuple[str, tuple[bytes, ...]], ...] = (
    ('gradle-bootstrap-checksum', (b'checksum mismatch', b'gradle distribution checksum')),
    ('java-toolchain-incompatible', (b'unsupported class file major version',
                                     b'incompatible java version', b'requires java 21',
                                     b'requires jdk 21', b'requires java 17')),
    ('android-sdk-path-missing', (b'sdk location not found', b'android sdk location not found',
                                  b'android_sdk_root or android_home is required',
                                  b'failed to find sdk', b'android sdk root is missing')),
    ('android-platform-missing', (b'failed to find platform sdk with path',
                                  b'failed to find target with hash string',
                                  b'platforms;android-36', b'android-36 was not found')),
    ('android-build-tools-missing', (b'build tools revision 36.0.0 is corrupted',
                                     b'failed to find build tools revision',
                                     b'failed to install the following sdk components')),
    ('android-aapt2-execution-failed', (b'aapt2 daemon startup failed',
                                        b'aapt2 daemon', b'error while executing aapt2',
                                        b'failed to start aapt2')),
    ('android-architecture-executable', (b'exec format error', b'cannot execute binary file',
                                         b'wrong elf class')),
    ('gradle-signing-password-rejected', (b'keystore password was incorrect',
                                          b'password was incorrect',
                                          b'cannot recover key', b'keystore was tampered with')),
    ('gradle-signing-material-missing', (b'keystore file not found',
                                         b'release keystore does not exist',
                                         b'release signing secrets are unavailable',
                                         b'release signing is required. set devtool_release_')),
    ('gradle-signing-keystore-invalid', (b'invalid keystore format',
                                         b'failed to read key', b'key was not found')),
    ('gradle-init-script-failed', (b'initialization script', b'could not compile initialization script',
                                   b'failed to apply plugin in init script')),
    ('gradle-network-unavailable', (b'unknownhostexception', b'connection timed out',
                                    b'could not get resource', b'read timed out')),
    ('gradle-dependency-resolution', (b'could not resolve all files',
                                      b'could not resolve all dependencies',
                                      b'plugin [id:', b'was not found in any of the following sources')),
    ('android-r8-minification', (b'missing classes detected while running r8',
                                 b'execution failed for task :app:minifyreleasewithr8',
                                 b'error: r8:')),
    ('gradle-out-of-memory', (b'java heap space', b'outofmemoryerror',
                              b'gradle build daemon disappeared unexpectedly')),
)


def _classify_gradle_failure(stdout: bytes | str | None, stderr: bytes | str | None) -> str:
    """Select a fixed, nonsecret failure ID without projecting Gradle text.

    The output may include passwords or injected strings. Never include any
    captured text in public results, exceptions, events or test assertions.
    """
    def bounded(value: bytes | str | None) -> bytes:
        if isinstance(value, str):
            value = value.encode('utf-8', errors='replace')
        if not isinstance(value, bytes):
            return b''
        # Both early boot errors and the final task failure can be relevant.
        return (value[:1024 * 1024] + b'\n' + value[-1024 * 1024:]).lower()

    output = bounded(stdout) + b'\n' + bounded(stderr)
    for category, markers in _GRADLE_FAILURE_MARKERS:
        if any(marker in output for marker in markers):
            return category
    return 'gradle-unclassified'


@contextmanager
def _local_signing_password(profile):
    """Ask only at a human-approved local execution boundary; clean process env."""
    name = profile.store_password_env
    inserted = False
    if name and not os.environ.get(name):
        if not sys.stdin.isatty():
            raise GitHubActionsError(f'Signing secret provider {name} is unavailable; use an interactive terminal or a private secret source')
        value = getpass.getpass('Local Android signing keystore password (hidden): ')
        if not value:
            raise GitHubActionsError('A nonempty release signing password is required')
        os.environ[name] = value
        inserted = True
        value = ''
    try:
        yield
    finally:
        if inserted:
            os.environ.pop(name, None)


def local_build(root: Path, *, execute: bool = False, fmt: str = 'apk',
                identity_id: str = '', target: str = 'android_hud',
                application_id: str = '', minimum_version_code: int = 1,
                project_dir: str = 'android/devtool-live-hud', module: str = 'app') -> dict[str, Any]:
    root = root.resolve()
    if fmt not in ('apk', 'aab') or not _MODULE.fullmatch(module):
        raise GitHubActionsError('Unsupported local package format or module')
    if type(minimum_version_code) is not int or minimum_version_code < 1:
        raise GitHubActionsError('Minimum version code must be positive')
    sha = _tracked_state(root)
    project, relative = _project_root(root, project_dir)
    if not project.is_dir() or project.is_symlink():
        raise GitHubActionsError('Local Android Gradle project is unavailable')
    launcher = project / 'gradlew'
    if not launcher.is_file() or launcher.is_symlink():
        raise GitHubActionsError('Local Android release needs a regular Gradle wrapper')
    from .actions_android import _gradle_bootstrap
    _gradle_bootstrap(project)
    from .actions_android import android_discover
    info = android_discover(root, relative)
    if not any(x.get('module') == module for x in info['modules']):
        raise GitHubActionsError('Selected Gradle module is not a statically discovered Android application')
    app = _application(root, relative, module, application_id)
    profile = _signing_profile(root, identity_id=identity_id, target=target)
    if profile.mode != 'devtool' or not profile.expected_cert_sha256:
        raise GitHubActionsError('Local build requires a canonical registered LIFE release signer')
    _keystore(profile.keystore, root)
    task = ':' + module + (':assembleRelease' if fmt == 'apk' else ':bundleRelease')
    report = {'phase': 'ACTIONS-LOCAL', 'operation': 'build', 'mode': 'apply' if execute else 'plan',
              'source_sha_at_build_start': sha, 'project_dir': relative, 'module': module,
              'gradle_task': task, 'format': fmt, 'application_id': app,
              'signing_identity_id': profile.identity_id, 'signer_sha256': profile.expected_cert_sha256.lower(),
              'qualified_release': False, 'github_provenance_verified': False,
              'scope': 'private local Gradle execution; not protected CI release qualification'}
    if not execute:
        report['note'] = 'Read-only preview; use --yes to invoke local Gradle and inspect produced APK/AAB.'
        return report
    with _local_signing_password(profile):
        # Only materialize credentials at the actual local execution boundary; no secrets
        # are exported to gh, shell argv, manifests, or result objects.
        item, secrets, certificate = _signer_material(root, profile)
        from .android.signing import stage_gradle_signing_init_script, AndroidSigningError
        from .android.variants import activate_android_variant
        from .config import load_project_config
        try:
            cfg = activate_android_variant(load_project_config(str(root), None, target_name=target), 'release')
            opts = dict(cfg.runner_options)
            android = dict(opts.get('android') or {})
            signing = dict(android.get('signing') or {})
            signing['release'] = {'mode': 'devtool', 'policy': 'release-required',
                                  'identity_id': profile.identity_id}
            android['signing'] = signing
            opts['android'] = android
            cfg = replace(cfg, runner_options=opts, variant='release')
            init_script = stage_gradle_signing_init_script(cfg, [task], env={**os.environ, **secrets})
        except (AndroidSigningError, ValueError, OSError) as exc:
            raise GitHubActionsError('Local Android signing injection could not be prepared') from None
        if init_script is None or not init_script.is_file():
            raise GitHubActionsError('Canonical Gradle release signing init script was not generated')
        env = dict(os.environ)
        env.update(secrets)
        # Mirror the checked-in Actions build contract for projects that read these
        # variables, in addition to the canonical init-script signing injection.
        env.update({'DEVTOOL_RELEASE_STORE_FILE': str(item), 'DEVTOOL_RELEASE_STORE_PASSWORD': secrets['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD'],
                    'DEVTOOL_RELEASE_KEY_ALIAS': profile.alias, 'DEVTOOL_RELEASE_KEY_PASSWORD': secrets['DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD']})
        try:
            # Never echo subprocess output: arbitrary Gradle plugins may print secrets.
            proc = subprocess.run(['bash', str(launcher), '--no-daemon', '--init-script', str(init_script), task],
                                  cwd=str(project), env=env, capture_output=True, timeout=1800, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise GitHubActionsError('Local Gradle release build failed or timed out; inspect local Gradle privately') from None
        finally:
            env.clear()
            secrets.clear()
        if proc.returncode:
            category = _classify_gradle_failure(proc.stdout, proc.stderr)
            raise GitHubActionsError(
                f'Local Gradle release build failed (exit {proc.returncode}, reason={category}); '
                'captured build output withheld to protect credentials'
            ) from None
        if _tracked_state(root) != sha:
            raise GitHubActionsError('Source changed during local release build; refusing to endorse output')
        folder = project.joinpath(*module.split(':'), 'build', 'outputs', 'apk' if fmt == 'apk' else 'bundle', 'release')
        if not folder.is_dir() or folder.is_symlink():
            raise GitHubActionsError('Local release output directory was not produced')
        candidates = [p for p in folder.iterdir() if p.suffix.lower() == '.' + fmt and p.is_file() and not p.is_symlink()]
        if len(candidates) != 1:
            raise GitHubActionsError('Expected exactly one regular local release APK/AAB')
        result = local_verify(root, artifact=str(candidates[0].resolve()), fmt=fmt, identity_id=profile.identity_id,
                              target=target, application_id=app, minimum_version_code=minimum_version_code,
                              project_dir=relative, module=module)
        report['verification'] = result
        report['local_signature_verified'] = True
        report['source_attested'] = False
        return report
