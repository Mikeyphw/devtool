"""ACTIONS-09 guided release preparation and governance readiness.

Never print key material, overwrite existing protection, accept mutable SHA policy,
or claim a qualified release from setup/readiness. Remote changes require --yes.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Any
from urllib.parse import quote

from .github_actions import GitHubActionsClient, GitHubActionsError, _slug
from .actions_release import release_policy, _WORKFLOW, _SHA64
from .actions_android import android_discover

SECRETS = ('DEVTOOL_RELEASE_KEYSTORE_B64', 'DEVTOOL_RELEASE_STORE_PASSWORD',
           'DEVTOOL_RELEASE_KEY_ALIAS', 'DEVTOOL_RELEASE_KEY_PASSWORD')
_BRANCH = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./-]{0,180}\Z')


def _branch(value: str) -> str:
    if (not _BRANCH.fullmatch(value) or value.endswith('/') or value.endswith('.lock')
            or '..' in value or '//' in value or '@{' in value or value.startswith('refs/')):
        raise GitHubActionsError('Choose an exact safe branch name, e.g. main')
    return value


def _gh(arguments: list[str], *, input_text: str | None = None, timeout: int = 35) -> str:
    gh = shutil.which('gh')
    if not gh:
        raise GitHubActionsError('GitHub CLI missing: install gh and run gh auth login')
    try:
        p = subprocess.run([gh, *arguments], input=input_text, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('GitHub CLI failed or timed out (check gh auth status)') from None
    if p.returncode != 0:
        # stderr/stdout may contain sensitive provider or subprocess input; never print it.
        raise GitHubActionsError('GitHub rejected a setup operation; check permissions, settings and gh auth status')
    return p.stdout


def _secrets(slug: str) -> set[str]:
    try:
        obj = json.loads(_gh(['secret', 'list', '--repo', slug, '--app', 'actions', '--json', 'name']))
    except (ValueError, TypeError):
        raise GitHubActionsError('GitHub secret inventory is malformed') from None
    if not isinstance(obj, list):
        raise GitHubActionsError('GitHub secret inventory is malformed')
    return {entry['name'] for entry in obj if isinstance(entry, dict) and isinstance(entry.get('name'), str)}


def _protected(client: GitHubActionsClient, slug: str, branch: str) -> bool:
    obj = client._get('/repos/' + slug + '/branches/' + quote(branch, safe=''))
    if type(obj.get('protected')) is not bool:
        raise GitHubActionsError('GitHub did not report an authoritative branch protection status')
    return obj['protected']


def _ruleset(slug: str, branch: str) -> None:
    # A dedicated additive ruleset; never PUT/replace existing branch protection.
    # No bypass actors, no force-push or deletion. GitHub enforces GITHUB_REF_PROTECTED.
    obj = {'name': 'Devtool release safeguard: ' + branch,
           'target': 'branch', 'enforcement': 'active',
           'conditions': {'ref_name': {'include': ['refs/heads/' + branch], 'exclude': []}},
           'rules': [{'type': 'deletion'}, {'type': 'non_fast_forward'}]}
    _gh(['api', '--method', 'POST', 'repos/' + slug + '/rulesets', '--input', '-'],
        input_text=json.dumps(obj), timeout=45)


def _source(root: Path) -> str:
    try:
        proc = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        raise GitHubActionsError('Could not resolve local Git HEAD') from None
    sha = proc.stdout.strip()
    if proc.returncode or not re.fullmatch('[0-9a-f]{40}', sha):
        raise GitHubActionsError('Could not resolve local Git HEAD')
    return sha


def _keystore(path: str, root: Path) -> Path:
    item = Path(path).expanduser()
    if not item.is_absolute() or item.is_symlink() or item.resolve().is_relative_to(root.resolve()):
        raise GitHubActionsError('Keystore must be an absolute non-symlink path outside repository')
    try:
        fd = os.open(item, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                    st.st_mode & 0o077 or not 1 <= st.st_size <= 10 * 1024 * 1024):
                raise GitHubActionsError('Keystore must be user-owned, private (0600), 1B–10MiB')
        finally:
            os.close(fd)
    except OSError:
        raise GitHubActionsError('Cannot open the private release keystore') from None
    return item


def _signing_profile(root: Path, *, identity_id: str = '', target: str = 'android_hud'):
    """Select a canonical Devtool Android/LIFE signing identity, never raw key fields.

    Target policy wins. An unconfigured target may use a unique pre-existing
    LIFE registry identity. The registry is the *only* identity authority.
    """
    from dataclasses import replace
    from .config import load_project_config
    from .android.signing import signing_profile, AndroidSigningError
    from .artifact_trust import SigningIdentityRegistry
    try:
        config = load_project_config(str(root), None, target_name=target)
        configured = signing_profile(config)
        if configured.mode == 'devtool' and configured.identity_id:
            if identity_id and identity_id != configured.identity_id:
                raise GitHubActionsError('Selected signing identity conflicts with the target release policy')
            return configured
        records = SigningIdentityRegistry().list()
        suitable = [r for r in records if r.lifecycle == 'durable'
                    and (not r.purposes or set(r.purposes) & {'android','android-signing','release-signing'})
                    and (not r.variants or 'release' in r.variants or '*' in r.variants)]
        if identity_id:
            matches = [r for r in suitable if str(r.identity_id) == identity_id]
        else:
            matches = suitable
        if len(matches) != 1:
            if not matches:
                raise GitHubActionsError('No eligible Devtool LIFE release signer. Configure android signing / register an identity before release setup')
            raise GitHubActionsError('Multiple Devtool LIFE release signers; choose one with --signing-identity ID')
        selected = matches[0]
        # Derive the profile through the existing signing resolver, not an
        # ACTIONS-specific secret provider or parallel identity store.
        opts = dict(config.runner_options)
        android = dict(opts.get('android') or {})
        signing = dict(android.get('signing') or {})
        signing['release'] = {'mode': 'devtool', 'policy': 'release-required',
                              'identity_id': str(selected.identity_id)}
        android['signing'] = signing
        opts['android'] = android
        return signing_profile(replace(config, runner_options=opts))
    except GitHubActionsError:
        raise
    except (AndroidSigningError, ValueError, OSError, SystemExit) as exc:
        # Local configuration error; do not leak provider values.
        raise GitHubActionsError('Devtool Android signing configuration unavailable or invalid; check android signing inspect and the LIFE identity registry') from None


def _signer_material(root: Path, profile):
    """Bridge canonical signing provider to the GitHub workflow only on --yes."""
    from .android.signing import AndroidSigningManager, AndroidSigningError, signing_secret_environment
    if profile.mode != 'devtool' or not profile.identity_id:
        raise GitHubActionsError('A configured Devtool LIFE release signing identity is required')
    if not profile.keystore or not profile.alias or not profile.expected_cert_sha256:
        raise GitHubActionsError('Devtool signing identity must pin a keystore, alias and certificate SHA-256')
    item = _keystore(profile.keystore, root)
    try:
        secrets = signing_secret_environment(profile, allow_prompt=False)
        identity = AndroidSigningManager().inspect_profile(
            profile, store_password=secrets['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD'])
    except AndroidSigningError:
        raise GitHubActionsError('Devtool signing provider could not verify its configured release identity') from None
    if identity.expired is True or identity.sha256.lower() != profile.expected_cert_sha256.lower():
        raise GitHubActionsError('Devtool signer is expired or does not match the LIFE certificate fingerprint')
    return item, secrets, identity.sha256.lower()


def _application(root: Path, project_dir: str, module: str, explicit: str) -> str:
    from .actions_release import _PKG
    if explicit:
        if not _PKG.fullmatch(explicit):
            raise GitHubActionsError('Invalid application ID')
        return explicit
    items = android_discover(root, project_dir)['modules']
    matches = [item for item in items if item['module'] == module]
    if len(matches) != 1 or not matches[0].get('application_id_static'):
        raise GitHubActionsError('Application ID not statically known; specify --application-id')
    return str(matches[0]['application_id'])


def _policy_target(root: Path, path: str) -> Path:
    p = Path(path).expanduser().absolute()
    if p.is_symlink() or p == root.resolve() or p.resolve().is_relative_to(root.resolve()):
        raise GitHubActionsError('Private release policy must be outside repository and not a symlink')
    if p.exists() and not p.is_file():
        raise GitHubActionsError('Private release policy target is not a regular file')
    return p


def _write_policy(root: Path, path: Path, document: dict[str, Any]) -> bool:
    # Never replace an owner-controlled trust anchor. Identical policy is idempotent.
    if path.exists():
        current = release_policy(root, str(path))
        if {k: v for k, v in current.items() if not k.startswith('_')} != document:
            raise GitHubActionsError('Existing private policy differs; review and explicitly move it before setup')
        return False
    parent = path.parent
    if parent.is_symlink():
        raise GitHubActionsError('Private policy parent may not be symlinked')
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = parent.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise GitHubActionsError('Private policy directory must be user-owned and not group/world writable')
    data = (json.dumps(document, indent=2, sort_keys=True) + '\n').encode()
    fd = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'wb') as f:
            fd = None
            os.fchmod(f.fileno(), 0o600)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        dir_fd = os.open(parent, os.O_RDONLY)
        try: os.fsync(dir_fd)
        finally: os.close(dir_fd)
    except FileExistsError:
        raise GitHubActionsError('Release policy appeared concurrently; refusing overwrite') from None
    finally:
        if fd is not None: os.close(fd)
    return True


def release_readiness(client: GitHubActionsClient, slug: str, root: Path, *, ref: str,
                      policy_file: str, source_sha: str = '', signing_identity: str = '', target: str = 'android_hud') -> dict[str, Any]:
    slug = _slug(slug); branch = _branch(ref)
    repo = client.repository(slug)
    remote = client._get('/repos/' + slug + '/commits/' + quote(branch, safe=''))
    sha = remote.get('sha', '')
    if not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{40}', sha):
        raise GitHubActionsError('Cannot observe remote branch source commit')
    # GitHub accepts a workflow filename or numeric ID, not its repository path.
    # The returned full path is still checked below as a separate identity gate.
    workflow = client.workflow(slug, Path(_WORKFLOW).name)
    protected = _protected(client, slug, branch)
    installed_secrets = _secrets(slug)
    missing = [name for name in SECRETS if name not in installed_secrets]
    policy_path = _policy_target(root, policy_file)
    policy = None
    policy_problem = ''
    if policy_path.exists():
        try:
            policy = release_policy(root, str(policy_path))
            if (policy['repository'].lower() != slug.lower() or policy['ref'] != 'refs/heads/' + branch or
                    policy['source_sha'] != sha):
                policy_problem = 'Policy differs from current repository/ref/remote SHA'
        except GitHubActionsError as exc:
            policy_problem = str(exc)
    else:
        policy_problem = 'Private policy is not created yet'
    source_ok = (not source_sha or source_sha == sha)
    selected_signer = ''
    signer_problem = ''
    try:
        profile = _signing_profile(root, identity_id=signing_identity, target=target)
        selected_signer = profile.identity_id
        if (not profile.keystore or not profile.alias or not profile.expected_cert_sha256 or
                profile.mode != 'devtool'):
            signer_problem = 'Devtool LIFE release signer must pin a keystore, alias and certificate fingerprint'
        elif policy is not None and policy['signer_sha256'].lower() != profile.expected_cert_sha256.lower():
            signer_problem = 'Private policy signer differs from Devtool LIFE signing identity'
    except GitHubActionsError as exc:
        signer_problem = str(exc)
    gates = {'workflow_active': workflow.get('state') == 'active' and workflow.get('path') == _WORKFLOW,
             'branch_protected': protected, 'signing_secrets_present': not missing,
             'policy_current': not policy_problem and bool(policy),
             'signing_identity_configured': not signer_problem,
             'source_match': source_ok}
    return {'phase': 'ACTIONS-09', 'repository': slug, 'ref': 'refs/heads/' + branch,
            'source_sha': sha, 'local_source_sha': source_sha or None,
            'policy_file': str(policy_path), 'secret_names_missing': missing,
            'signing_identity_id': selected_signer or None, 'signing_identity_problem': signer_problem,
            'gates': gates, 'ready': all(gates.values()), 'qualified_release': False,
            'scope': 'preflight only; not a release qualification',
            'next_actions': [msg for condition, msg in (
                (not gates['source_match'], 'Push your intended source commit before setup'),
                (not gates['branch_protected'], 'Protect the branch (setup --yes can create an additive ruleset)'),
                (bool(missing), 'Provision workflow secrets from the existing Devtool signing identity with setup --yes'),
                (bool(signer_problem), signer_problem),
                (bool(policy_problem), policy_problem),
                (not gates['workflow_active'], 'Enable the pinned Android workflow')) if condition]}


def release_setup(client: GitHubActionsClient, slug: str, root: Path, *, ref: str = 'main',
                  policy_file: str = '', signing_identity: str = '', target: str = 'android_hud',
                  project_dir: str = 'android/devtool-live-hud', module: str = 'app',
                  application_id: str = '', fmt: str = 'apk', minimum_version_code: int = 1,
                  execute: bool = False, rotate_secrets: bool = False) -> dict[str, Any]:
    root = root.resolve(); slug = _slug(slug); branch = _branch(ref)
    path = policy_file or str(Path.home()/'.config/devtool/release-policy.json')
    plan = release_readiness(client, slug, root, ref=branch, policy_file=path, source_sha=_source(root),
                             signing_identity=signing_identity, target=target)
    plan['mode'] = 'apply' if execute else 'plan'
    plan['required_inputs'] = {'signing_identity_id': plan['signing_identity_id'], 'target': target,
                               'application_id': application_id or '(auto-discover)',
                               'format': fmt, 'minimum_version_code': minimum_version_code}
    if not execute:
        plan['note'] = 'Read-only. Add --yes to protect ref, upload secrets and create private policy.'
        return plan
    if fmt not in ('apk', 'aab') or type(minimum_version_code) is not int or minimum_version_code < 1:
        raise GitHubActionsError('Release format/version code are invalid')
    if not plan['gates']['source_match'] or not plan['gates']['workflow_active']:
        raise GitHubActionsError('Remote source and active workflow must match before setup')
    policy_target = _policy_target(root, path)
    if policy_target.exists():
        # First validate the independent trust anchor before touching secrets.
        release_policy(root, str(policy_target))
    profile = _signing_profile(root, identity_id=signing_identity, target=target)
    # Validate all signing material before any remote mutation. All secret
    # values come from AndroidSigningManager's existing secret providers.
    item, resolved_secrets, signer = _signer_material(root, profile)
    app = _application(root, project_dir, module, application_id)
    document = {'schema': 1, 'repository': slug, 'workflow': _WORKFLOW,
                'ref': 'refs/heads/' + branch, 'source_sha': plan['source_sha'],
                'application_id': app, 'signer_sha256': signer, 'format': fmt,
                'minimum_version_code': minimum_version_code}
    if policy_target.exists():
        # Detect conflicting trust anchor before sending secrets or modifying remote.
        release_policy(root, str(policy_target))
        existing = json.loads(policy_target.read_text())
        if existing != document:
            raise GitHubActionsError('Existing private policy differs; refusing remote mutation')
    observed = set(SECRETS) - set(plan['secret_names_missing'])
    if observed and observed != set(SECRETS) and not rotate_secrets:
        raise GitHubActionsError('Partial GitHub release secrets exist; refusing mixed signer material. Review them and use --rotate-secrets --yes only if intentionally replacing all four')
    # Protect before provisioning secrets. Creating a new ruleset is additive, not replacement.
    if not plan['gates']['branch_protected']:
        _ruleset(slug, branch)
        if not _protected(client, slug, branch):
            raise GitHubActionsError('Branch ruleset submitted but branch is not yet protected; no secrets uploaded')
    # Secrets travel to gh via stdin only (never process arguments, stdout or logs).
    # The four names exactly match the pinned checked-in Android workflow.
    payloads = (base64.b64encode(item.read_bytes()).decode('ascii'),
                resolved_secrets['DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD'], profile.alias,
                resolved_secrets['DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD'])
    uploaded = []
    if rotate_secrets or not observed:
        for name, value in zip(SECRETS, payloads):
            _gh(['secret', 'set', name, '--repo', slug, '--app', 'actions'], input_text=value)
            uploaded.append(name)
    # Existing complete sets are never overwritten by a generic --yes.
    # Name presence cannot attest their cryptographic correctness.
    if _secrets(slug).issuperset(SECRETS) is False:
        raise GitHubActionsError('GitHub did not confirm all secret names; private policy was not written')
    created = _write_policy(root, policy_target, document)
    return {'phase': 'ACTIONS-09', 'mode': 'apply', 'repository': slug,
            'ref': document['ref'], 'source_sha': document['source_sha'],
            'policy_file': str(policy_target), 'policy_created': created,
            'signer_sha256': signer, 'signing_identity_id': profile.identity_id,
            'application_id': app, 'secret_names_updated': uploaded,
            'existing_secret_values_unverified': bool(observed) and not uploaded,
            'branch_protected': True, 'ready': True, 'qualified_release': False,
            'scope': 'configuration only; a real attested signed release remains mandatory'}
