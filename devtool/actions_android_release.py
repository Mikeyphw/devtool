"""One operator-facing Android release workflow; two separate trust authorities.

Generic ACTIONS-05 builds are *not* equivalent to a registered unsigned
ACTIONS-10 artifact. Only registered consumer builds may advance to immutable
import and local LIFE signing. Never translate an unregistered APK into release
provenance, never produce a new signing identity, never qualify a release.
"""
from __future__ import annotations
from pathlib import Path
from typing import Any

from .github_actions import GitHubActionsClient, GitHubActionsError


def release_build(client: GitHubActionsClient, repository: str, root: Path, *,
                  consumer_id: str = '', module: str = 'app', variant: str = 'release',
                  fmt: str = 'apk', ref: str, execute: bool = False,
                  project_dir: str = '', discovery_mode: str = 'static',
                  expected_package: str = '') -> dict[str, Any]:
    if consumer_id:
        from .actions_android_contracts import select_consumer, contract_sha256
        from .actions_unsigned import unsigned_dispatch
        consumer=select_consumer(root, consumer_id=consumer_id, repository=repository)
        if fmt!='apk':raise GitHubActionsError('Registered LIFE APK release requires APK format')
        if variant and variant not in ('release',consumer['variant']):
            raise GitHubActionsError('Selected release variant differs from consumer contract')
        if expected_package and expected_package!=consumer['application_id']:
            raise GitHubActionsError('Selected application differs from registered consumer')
        result=unsigned_dispatch(client,repository,root,ref=ref,execute=execute,consumer_id=consumer_id)
        return {'phase':'ACTIONS-ANDROID-RELEASE','mode':'registered-unsigned-life',
                'consumer_id':consumer_id,'variant':consumer['variant'],
                'contract_sha256':contract_sha256(consumer),
                'build':result,'next_action':'release-import',
                'qualified_release':False}
    from .actions_android import android_plan
    result=android_plan(client,repository,root,module=module,variant=variant,fmt=fmt,
                        ref=ref,execute=execute,project_dir=project_dir,
                        expected_package=expected_package,signing_mode='local',
                        discovery_mode=discovery_mode)
    return {'phase':'ACTIONS-ANDROID-RELEASE','mode':'generic-unsigned-build',
            'build':result,'release_import_eligible':False,
            'next_action':'register-exact-unsigned-consumer-producer-before-import',
            'qualified_release':False}


def release_import(client: GitHubActionsClient, repository: str, root: Path, *,
                   consumer_id: str, run_id: int, artifact_id: int,
                   expected_sha: str, download: bool = False) -> dict[str,Any]:
    from .actions_unsigned import unsigned_import
    from .actions_android_contracts import select_consumer
    if not consumer_id:raise GitHubActionsError('Release import requires an explicit registered consumer')
    select_consumer(root,consumer_id=consumer_id,repository=repository)
    result=unsigned_import(client,repository,root,run_id=run_id,artifact_id=artifact_id,
                           expected_sha=expected_sha,download=download,materialize=True,
                           consumer_id=consumer_id)
    return {'phase':'ACTIONS-ANDROID-RELEASE','mode':'verified-unsigned-import',
            'import':result,'qualified_release':False,'next_action':'release-sign'}


def release_sign(root: Path, *, registry_id: str, consumer_id: str = '',
                 identity_id: str = '',execute: bool = False) -> dict[str,Any]:
    from .actions_unsigned import unsigned_local_sign, _imported
    from .actions_android_contracts import select_consumer
    if not registry_id:raise GitHubActionsError('Release signing requires exact immutable registry ID')
    _,meta=_imported(root,registry_id)
    actual=str(meta.get('consumer_id') or '')
    if consumer_id and actual!=consumer_id:
        raise GitHubActionsError('Signing consumer differs from imported candidate')
    consumer=select_consumer(root,consumer_id=actual)
    receipt=unsigned_local_sign(root,registry_id=registry_id,identity_id=identity_id,
                                target=consumer['target'],execute=execute)
    return {'phase':'ACTIONS-ANDROID-RELEASE','mode':'local-life-sign',
            'signing':receipt,'qualified_release':False}
