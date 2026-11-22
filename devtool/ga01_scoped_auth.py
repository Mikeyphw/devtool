"""GA-01 — closed-world GitHub App operation admission.

M4 owns token minting, M7 owns credential diagnosis, Actions/Delivery own
remote writes and journals. This module binds ONE CLI request to ONE
repository and ONE minimum-permission capability; never supplies a PAT fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .github_app import AppInstallationProvider, GitHubAppError, _repository


class AppOperationRefused(RuntimeError):
    """A fixed-code, non-secret scope admission refusal."""


# This is NOT a grant. M4 independently checks both installation permissions
# and the repository included in the actual short-lived installation token.
_OPERATION_CAPABILITIES = {
    'actions.workflows.read': 'actions_read',
    'actions.runs.read': 'actions_read',
    'actions.artifacts.read': 'actions_read',
    'actions.dispatch': 'actions_dispatch',
    'actions.run.control': 'actions_control',
    'actions.pipeline.plan': 'actions_read',
    'actions.pipeline.dispatch': 'actions_dispatch',
    'actions.ci.plan': 'actions_read',
    'actions.ci.dispatch': 'actions_dispatch',
    'actions.extension.metrics': 'actions_read',
    'actions.extension.runners': 'runners_read',
    'actions.extension.release.plan': 'release_read',
    'actions.extension.release.publish': 'release_write',
    'actions.release.readiness': 'release_read',
    'actions.release.verify': 'release_read',
    'actions.android.plan': 'actions_read',
    'actions.android.dispatch': 'actions_dispatch',
    'actions.android.artifacts': 'actions_read',
    'delivery.pr.create': 'pr_create',
    'delivery.pr.read': 'pr_read',
    'delivery.checks.read': 'checks_read',
    'federation.source.fetch': 'repository_read',
}


@dataclass(frozen=True)
class AppClient:
    """Memory-only scope proof; token deliberately excluded from repr/JSON.

    It does not implement a generic GitHub HTTP write endpoint. Existing
    operation-specific native clients retain their request/write journals.
    """
    repository: str
    repository_id: int
    app_id: int
    installation_id: int
    operation: str
    capability: str
    expires_at: int
    token: str = field(repr=False)

    def public_receipt(self) -> dict[str, Any]:
        return {'schema':'devtool.ga01.app-scope.v1', 'repository':self.repository,
                'repository_id':self.repository_id, 'installation_id':self.installation_id,
                'app_id':self.app_id, 'operation':self.operation,
                'capability':self.capability, 'expires_at_unix':self.expires_at,
                'token_persisted':False, 'write_execution_verified':False}


def issue_for_operation(repository: str, operation: str, *,
                        environ: Mapping[str,str] | None = None,
                        expected_repository_id: int | None = None,
                        provider: AppInstallationProvider | None = None) -> AppClient:
    """Prove installation and real repo ID before supplying a scoped token.

    For production, reuse M7's validated private env.sh through its existing
    literal-only loader. Explicit injected environments are for constrained
    callers and tests, never an alternate fallback to GH_TOKEN.
    """
    capability = _OPERATION_CAPABILITIES.get(operation)
    if capability is None:
        raise AppOperationRefused('GA01_OPERATION_NOT_ADMITTED')
    try:
        slug = _repository(repository)
        if provider is None:
            if environ is None:
                from .federation_app_auth import installed_app_environment
                environ = installed_app_environment()
            provider = AppInstallationProvider.from_environment(environ)
        access = provider.issue(slug, capability)
        if (access.repository.casefold()!=slug.casefold() or
                type(access.repository_id) is not int or access.repository_id < 1 or
                type(access.installation_id) is not int or access.installation_id < 1 or
                access.capability != capability or not access.token):
            raise AppOperationRefused('GA01_APP_ISSUANCE_IDENTITY_MISMATCH')
        if expected_repository_id is not None and (
                type(expected_repository_id) is not int or expected_repository_id < 1 or
                access.repository_id != expected_repository_id):
            raise AppOperationRefused('GA01_REPOSITORY_NUMERIC_ID_DRIFT')
        return AppClient(access.repository, access.repository_id, access.app_id,
                         access.installation_id, operation, capability,
                         access.expires_at, access.token)
    except GitHubAppError as exc:
        # M4 returns only fixed diagnostic codes, never a response body/token.
        raise AppOperationRefused(str(exc)) from None


def actions_operation(args: Any) -> str:
    """Admit exactly the supported Actions command/subcommand combinations.

    Do NOT infer write permission from a word appearing somewhere in argv;
    action and subaction are canonical parser fields.
    """
    action = str(getattr(args,'actions_command','') or '')
    if action in ('workflows','workflow','plan','diagnose','lineage'):
        return 'actions.workflows.read'
    if action in ('runs','view','jobs','watch','resume','logs','artifacts',
                  'download','verify','review','collect'):
        return 'actions.runs.read'
    if action == 'run':
        return 'actions.dispatch'
    if action in ('rerun','cancel'):
        return 'actions.run.control'
    if action == 'ci':
        sub = str(getattr(args,'ci_command','') or '')
        if sub == 'run': return 'actions.ci.dispatch'
        if sub == 'plan': return 'actions.ci.plan'
        if sub in ('check','collect'): return 'actions.artifacts.read'
    if action == 'pipeline':
        sub = str(getattr(args,'pipeline_command','') or '')
        if sub == 'plan': return 'actions.pipeline.plan'
        if sub in ('run','resume'): return 'actions.pipeline.dispatch'
    if action == 'extensions':
        sub = str(getattr(args,'extensions_command','') or '')
        return {'metrics':'actions.extension.metrics',
                'runners':'actions.extension.runners',
                'publish-plan':'actions.extension.release.plan',
                'publish':'actions.extension.release.publish'}.get(sub) or _refused()
    if action == 'release':
        sub = str(getattr(args,'release_command','') or '')
        # setup uses the separate owner gh-secret operation. Never pass a
        # different user's gh credential under a misleading `--auth app` label.
        return {'readiness':'actions.release.readiness',
                'verify':'actions.release.verify'}.get(sub) or _refused()
    if action == 'android':
        sub = str(getattr(args,'android_command','') or '')
        if sub in ('run','unsigned-run','release-run','aab-run','release-batch-run'):
            return 'actions.android.dispatch'
        if sub in ('plan','unsigned-plan','release-plan','aab-plan','release-batch-plan'):
            return 'actions.android.plan'
        if sub in ('collect','verify','unsigned-check','unsigned-import',
                   'release-import','release-batch-check','release-batch-import',
                   'aab-check','aab-import'):
            return 'actions.android.artifacts'
    return _refused()


def _refused() -> str:
    raise AppOperationRefused('GA01_ACTIONS_OPERATION_NOT_SUPPORTED_BY_APP')
