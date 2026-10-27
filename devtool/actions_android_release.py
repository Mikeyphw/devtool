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


# RM16-BATCH: one hosted producer, independent immutable import and LIFE signing.
# This layer carries no signing credentials and never implements a build engine.

def _batch_contracts(root: Path, repository: str, consumer_ids: list[str]) -> list[dict[str, Any]]:
    from .actions_android_contracts import select_consumer
    if not isinstance(consumer_ids, list) or not 2 <= len(consumer_ids) <= 16 or any(
            not isinstance(x, str) or not x or len(x) > 100 for x in consumer_ids):
        raise GitHubActionsError('Batch requires 2–16 explicit registered consumer IDs')
    if len(set(consumer_ids)) != len(consumer_ids):
        raise GitHubActionsError('Batch has duplicate consumers')
    contracts = [select_consumer(root, consumer_id=name, repository=repository) for name in consumer_ids]
    if len({x['workflow'] for x in contracts}) != 1:
        raise GitHubActionsError('Batch consumers must share one reviewed GitHub workflow')
    for field in ('artifact','apk','variant'):
        if len({x[field] for x in contracts}) != len(contracts):
            raise GitHubActionsError('Batch consumers require independent artifact, APK and variant identities')
    # A release batch may use different package IDs, but the requested BKX pair
    # must be signed by the same locally authorized LIFE identity/certificate.
    if len({(x['signing_identity_id'],x['signer_sha256']) for x in contracts}) != 1:
        raise GitHubActionsError('Batch consumers must share the same authorized LIFE signer and certificate')
    return contracts


def release_batch_build(client: GitHubActionsClient, repository: str, root: Path, *,
                        consumer_ids: list[str], ref: str, execute: bool = False,
                        source_root: Path | None = None) -> dict[str, Any]:
    from .actions_android_contracts import contract_sha256
    from .actions_unsigned import unsigned_dispatch
    contracts = _batch_contracts(root, repository, consumer_ids)
    # Only the producer checkout supplies source identity. Devtool's registry
    # root is *not* the BKX Git repository and cannot stand in for its HEAD.
    result = unsigned_dispatch(client, repository, root, consumer_id=contracts[0]['id'],
                               ref=ref, execute=execute, source_root=source_root,
                               resume_if_existing=True)
    return {'phase':'ACTIONS-ANDROID-BATCH','operation':'dispatch-one-workflow',
            'workflow':contracts[0]['workflow'],'repository':repository,
            'consumers':[{'id':x['id'],'variant':x['variant'],'artifact':x['artifact'],
                          'contract_sha256':contract_sha256(x)} for x in contracts],
            'source_sha':result['source_sha'], 'dispatch':result,
            'dispatch_count':1 if result.get('dispatched') else 0,
            'next_action':'release-batch-check', 'qualified_release':False}


def _batch_artifacts(contracts: list[dict[str,Any]], mapping: dict[str,int]) -> None:
    required={x['id'] for x in contracts}
    if (not isinstance(mapping, dict) or set(mapping)!=required or
            any(type(x) is not int or x < 1 for x in mapping.values()) or
            len(set(mapping.values()))!=len(mapping)):
        raise GitHubActionsError('Batch requires one distinct positive artifact ID for every consumer')


def release_batch_import(client: GitHubActionsClient, repository: str, root: Path, *,
                         consumer_ids: list[str], run_id: int, artifact_ids: dict[str,int],
                         expected_sha: str, download: bool = False,
                         materialize: bool = False) -> dict[str,Any]:
    from .actions_unsigned import unsigned_import
    contracts=_batch_contracts(root,repository,consumer_ids)
    _batch_artifacts(contracts,artifact_ids)
    if type(run_id) is not int or run_id<1:
        raise GitHubActionsError('Batch import needs exact positive run ID')
    import re
    if not isinstance(expected_sha,str) or not re.fullmatch(r'[0-9a-fA-F]{40}', expected_sha):
        raise GitHubActionsError('Batch import needs an exact source SHA')
    # Verify *all* remote artifacts before saving any local APK. Artifact IDs
    # are explicit; never choose by newest matching name or mutable branch.
    previews=[]
    for x in contracts:
        p=unsigned_import(client,repository,root,run_id=run_id,artifact_id=artifact_ids[x['id']],
                          expected_sha=expected_sha,download=download,materialize=False,
                          consumer_id=x['id'])
        previews.append(p)
    attempts={p['run_attempt'] for p in previews}
    if len(attempts)!=1 or any(p['source_sha'].lower()!=expected_sha.lower() or
                               p['run_id']!=run_id for p in previews):
        raise GitHubActionsError('Batch artifacts have inconsistent GitHub source/run/attempt identity')
    response={'phase':'ACTIONS-ANDROID-BATCH','operation':'import' if materialize else 'verify',
              'repository':repository,'run_id':run_id,'run_attempt':previews[0]['run_attempt'],
              'source_sha':expected_sha.lower(),'consumers':consumer_ids,
              'preflight':previews,'qualified_release':False,'locally_signed':False,
              'complete':False,'next_action':'release-batch-sign'}
    if not materialize:return response
    imports=[]
    try:
        for x in contracts:
            imports.append(unsigned_import(client,repository,root,run_id=run_id,
                artifact_id=artifact_ids[x['id']],expected_sha=expected_sha,
                download=False,materialize=True,consumer_id=x['id']))
    except Exception as exc:
        # Never silently delete a successfully registered immutable candidate:
        # recovery uses its exact registry ID, not an implicit repeat/import.
        response.update({'imports':imports,'complete':False,
                         'next_action':'inspect imported registry IDs; recover individual missing consumer'})
        raise GitHubActionsError('Batch import partially completed; inspect exact unsigned registry IDs; '
                                 f'{len(imports)} candidates imported before refusal') from exc
    response.update({'imports':imports,'complete':True})
    return response


def release_batch_sign(root: Path, *, consumer_ids: list[str],
                       registry_ids: dict[str,str], execute: bool = False) -> dict[str,Any]:
    from .actions_android_contracts import select_consumer
    from .actions_unsigned import _imported
    if (not isinstance(registry_ids,dict) or set(registry_ids)!=set(consumer_ids) or
            len(registry_ids)!=len(consumer_ids) or
            any(not isinstance(v,str) or not v for v in registry_ids.values()) or
            len(set(registry_ids.values()))!=len(registry_ids)):
        raise GitHubActionsError('Batch signing requires one distinct immutable registry ID per consumer')
    sources=[]
    for cid in consumer_ids:
        _,meta=_imported(root,registry_ids[cid])
        if meta['consumer_id']!=cid:
            raise GitHubActionsError('Batch registry ID belongs to a different consumer')
        sources.append(meta)
    contracts=_batch_contracts(root,sources[0]['remote_repository'],consumer_ids)
    for field in ('remote_repository','remote_run_id','remote_run_attempt','remote_head_sha','remote_workflow_path'):
        if len({meta[field] for meta in sources})!=1:
            raise GitHubActionsError('Batch signing requires a single identical producer run and attempt')
    plans=[release_sign(root,registry_id=registry_ids[cid],consumer_id=cid,execute=False)
           for cid in consumer_ids]
    if len({p['signing']['expected_signer_sha256'] for p in plans})!=1:
        raise GitHubActionsError('Batch LIFE certificate plans do not agree')
    response={'phase':'ACTIONS-ANDROID-BATCH','operation':'local-life-sign',
              'consumers':consumer_ids,'plans':plans,'qualified_release':False,
              'complete':False,'next_action':'release-batch-sign --yes'}
    if not execute:return response
    signed=[]
    try:
        for cid in consumer_ids:
            signed.append(release_sign(root,registry_id=registry_ids[cid],consumer_id=cid,execute=True))
    except Exception as exc:
        raise GitHubActionsError('Batch signing partially completed; inspect signed registry for exact immutable receipts; '
                                 f'{len(signed)} candidates signed before refusal') from exc
    response.update({'signed':signed,'complete':True,'next_action':'external release/device qualification'})
    return response
