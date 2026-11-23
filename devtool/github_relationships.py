"""GA-02 opt-in relationship registry: explicit, source-bound and read-only.

A declared relationship is NEVER an authority to write.  M4 scopes tokens,
M5 owns mutations/journals, and M7 owns installation credential diagnosis.
This registry records policy provenance, repository numeric identities and
individual directed edges for GA-03/05/06. No ambient fork inference.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from .github_app import _repository
from .github_installations import (RepositoryObservation, InstallationIdentityRefused,
                                    observe_installed, prewrite_snapshot)

SCHEMA='devtool.ga02.relationships.v1'
_KINDS=frozenset({'fork_of','upstream','library_provider','consumer','submodule','package_dependency'})
_SHA=re.compile(r'[a-f0-9]{64}\Z')
_SHA1=re.compile(r'[a-f0-9]{40}\Z')
_POLICY_ID=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._-]{2,95}\Z')
_ROLE=frozenset({'base','head','public'})


class RelationshipRefused(RuntimeError):
    """Use fixed, token-free refusal codes; do not dump policy or credentials."""


def _id(raw: Any) -> int:
    if type(raw) is not int or raw<1:
        raise RelationshipRefused('GA02_POLICY_NUMERIC_ID_INVALID')
    return raw


def _clean_slug(raw: Any) -> str:
    if not isinstance(raw,str):
        raise RelationshipRefused('GA02_POLICY_REPOSITORY_INVALID')
    try:
        return _repository(raw)
    except Exception:
        raise RelationshipRefused('GA02_POLICY_REPOSITORY_INVALID') from None


def _relative(raw: Any) -> str:
    if (not isinstance(raw,str) or not 1<=len(raw)<=200 or '\\' in raw
            or raw.startswith('/') or ':' in raw or '\x00' in raw
            or '//' in raw or any(p in ('','.', '..') for p in PurePosixPath(raw).parts)):
        raise RelationshipRefused('GA02_POLICY_PROVENANCE_PATH_UNSAFE')
    return raw


def _unique_json(pairs):
    output={}
    for k,v in pairs:
        if k in output:
            raise RelationshipRefused('GA02_POLICY_DUPLICATE_KEY')
        output[k]=v
    return output


@dataclass(frozen=True,slots=True)
class RegistryNode:
    repository: str
    repository_id: int
    owner_id: int
    role: str
    installation_id: int | None
    visibility: str


@dataclass(frozen=True,slots=True)
class RegistryEdge:
    kind: str
    source: str
    target: str
    policy_ref: str


@dataclass(frozen=True,slots=True)
class RelationshipRegistry:
    policy_id: str
    source_sha: str
    sha256: str
    nodes: tuple[RegistryNode,...]
    edges: tuple[RegistryEdge,...]
    path: str

    def receipt(self) -> dict[str,Any]:
        return {'schema':SCHEMA,'policy_id':self.policy_id,'source_sha':self.source_sha,
                'policy_sha256':self.sha256,'node_count':len(self.nodes),
                'edge_count':len(self.edges),'read_only':True,
                'write_authorized':False,'tokens_present':False}

    def node(self, repository: str) -> RegistryNode:
        slug=_clean_slug(repository)
        found=[x for x in self.nodes if x.repository.casefold()==slug.casefold()]
        if len(found)!=1:
            raise RelationshipRefused('GA02_REPOSITORY_NOT_EXPLICITLY_ENROLLED')
        return found[0]

    def edge(self, kind:str, source:str,target:str) -> RegistryEdge:
        s=_clean_slug(source); t=_clean_slug(target)
        found=[e for e in self.edges if e.kind==kind and
               e.source.casefold()==s.casefold() and e.target.casefold()==t.casefold()]
        if len(found)!=1:
            raise RelationshipRefused('GA02_RELATION_NOT_EXPLICITLY_ENROLLED')
        return found[0]


def load_registry(path: str|Path, *, expected_sha256: str,
                  expected_source_sha: str|None=None) -> RelationshipRegistry:
    if not isinstance(expected_sha256,str) or not _SHA.fullmatch(expected_sha256):
        raise RelationshipRefused('GA02_POLICY_DIGEST_REQUIRED')
    p=Path(path).expanduser().absolute()
    if p.is_symlink() or not p.is_file() or p.stat().st_size>128*1024:
        raise RelationshipRefused('GA02_POLICY_UNAVAILABLE_OR_UNSAFE')
    # Repository-controlled and local policy files may be readable by others;
    # PINNED SHA-256 is the authority, not filesystem owner or a file name.
    try:
        data=p.read_bytes()
    except OSError:
        raise RelationshipRefused('GA02_POLICY_READ_FAILED') from None
    digest=hashlib.sha256(data).hexdigest()
    if digest!=expected_sha256:
        raise RelationshipRefused('GA02_POLICY_DIGEST_DRIFT')
    try:
        obj=json.loads(data.decode('utf8'),object_pairs_hook=_unique_json)
    except (ValueError,UnicodeError):
        raise RelationshipRefused('GA02_POLICY_INVALID_JSON') from None
    if not isinstance(obj,dict) or set(obj)!= {'schema','policy_id','source_sha','repositories','relations'} or obj['schema']!=SCHEMA:
        raise RelationshipRefused('GA02_POLICY_SCHEMA_UNSUPPORTED')
    policy_id=obj['policy_id']
    if not isinstance(policy_id,str) or not _POLICY_ID.fullmatch(policy_id):
        raise RelationshipRefused('GA02_POLICY_ID_INVALID')
    source=obj['source_sha']
    if not isinstance(source,str) or not _SHA1.fullmatch(source):
        raise RelationshipRefused('GA02_POLICY_SOURCE_SHA_INVALID')
    if expected_source_sha is not None and source!=expected_source_sha:
        raise RelationshipRefused('GA02_POLICY_SOURCE_SHA_DRIFT')
    raw_nodes=obj['repositories']; raw_edges=obj['relations']
    if (not isinstance(raw_nodes,list) or not 1<=len(raw_nodes)<=64 or
            not isinstance(raw_edges,list) or len(raw_edges)>256):
        raise RelationshipRefused('GA02_POLICY_CARDINALITY_INVALID')
    nodes=[]; identities=set(); roles=set()
    for row in raw_nodes:
        if not isinstance(row,dict) or set(row)!={'repository','repository_id','owner_id','role','installation_id','visibility'}:
            raise RelationshipRefused('GA02_POLICY_NODE_SHAPE_INVALID')
        slug=_clean_slug(row['repository'])
        key=slug.casefold()
        if key in identities:
            raise RelationshipRefused('GA02_POLICY_DUPLICATE_REPOSITORY')
        identities.add(key)
        role=row['role'];access=row['installation_id'];visibility=row['visibility']
        if role not in _ROLE or visibility not in ('public','private','internal'):
            raise RelationshipRefused('GA02_POLICY_NODE_ROLE_OR_VISIBILITY_INVALID')
        if role=='public':
            if access is not None or visibility!='public':
                raise RelationshipRefused('GA02_PUBLIC_NODE_MUST_BE_ANONYMOUS')
        else:
            _id(access)
        nodes.append(RegistryNode(slug,_id(row['repository_id']),_id(row['owner_id']),role,
                                  access,visibility))
    edges=[]; pairs=set()
    for row in raw_edges:
        if not isinstance(row,dict) or set(row)!={'kind','source','target','policy_ref'}:
            raise RelationshipRefused('GA02_POLICY_EDGE_SHAPE_INVALID')
        kind=row['kind'];src=_clean_slug(row['source']);target=_clean_slug(row['target'])
        if kind not in _KINDS or src.casefold()==target.casefold():
            raise RelationshipRefused('GA02_POLICY_EDGE_KIND_OR_SELF_INVALID')
        if src.casefold() not in identities or target.casefold() not in identities:
            raise RelationshipRefused('GA02_POLICY_EDGE_NODE_UNENROLLED')
        key=(kind,src.casefold(),target.casefold())
        if key in pairs:raise RelationshipRefused('GA02_POLICY_DUPLICATE_EDGE')
        pairs.add(key)
        provenance=_relative(row['policy_ref'])
        edges.append(RegistryEdge(kind,src,target,provenance))
    return RelationshipRegistry(policy_id,source,digest,tuple(nodes),tuple(edges),str(p))


def assert_observation(registry:RelationshipRegistry, observed:RepositoryObservation) -> None:
    node=registry.node(observed.repository)
    if node.repository_id!=observed.repository_id:
        raise RelationshipRefused('GA02_REPOSITORY_RECREATED_OR_RENAMED')
    if node.owner_id!=observed.owner_id:
        raise RelationshipRefused('GA02_OWNER_TRANSFER_OR_ID_DRIFT')
    if node.role!=observed.role:
        raise RelationshipRefused('GA02_INSTALLATION_ROLE_DRIFT')
    if (node.visibility!=observed.visibility or
            node.installation_id!=observed.installation_id or observed.archived or observed.suspended):
        raise RelationshipRefused('GA02_INSTALLATION_SELECTION_VISIBILITY_OR_ARCHIVE_DRIFT')
    if node.role=='public' and observed.evidence!='PUBLIC_ANONYMOUS_METADATA_ONLY':
        raise RelationshipRefused('GA02_PUBLIC_ACCESS_SCOPE_ELEVATED')
    if node.role!='public' and observed.evidence!='SCOPED_APP_READ_VERIFIED':
        raise RelationshipRefused('GA02_APP_SCOPE_NOT_VERIFIED')


def assert_relation(registry:RelationshipRegistry, kind:str,
                    source:RepositoryObservation,target:RepositoryObservation) -> dict[str,Any]:
    edge=registry.edge(kind,source.repository,target.repository)
    assert_observation(registry,source)
    assert_observation(registry,target)
    # A cross-owner fork needs proof of the actual upstream numerical identity,
    # not an assumed relationship based on similar names or public metadata.
    if kind in ('fork_of','upstream'):
        if (not source.fork or source.parent_repository is None or
                source.parent_repository.casefold()!=target.repository.casefold() or
                source.parent_repository_id!=target.repository_id or
                source.parent_owner_id!=target.owner_id):
            raise RelationshipRefused('GA02_FORK_PARENT_REMOTE_IDENTITY_UNVERIFIED')
    if kind in ('submodule','package_dependency') and target.role=='public':
        # Public dependency metadata can be read but cannot be used for writes.
        pass
    return {'schema':'devtool.ga02.relation-observation.v1',
            'policy_id':registry.policy_id,'policy_sha256':registry.sha256,
            'kind':edge.kind,'source':source.repository,'source_repository_id':source.repository_id,
            'target':target.repository,'target_repository_id':target.repository_id,
            'policy_ref':edge.policy_ref,'read_only':True,'write_authorized':False,
            'remote_fork_identity_verified':kind in ('fork_of','upstream')}


def authorize_federation_source(registry:RelationshipRegistry, *,owner_repository:str,
                                target:RepositoryObservation,kind:str='submodule') -> dict[str,Any]:
    """Admit one opt-in federation target, without conferring write authority."""
    if kind not in ('submodule','package_dependency'):
        raise RelationshipRefused('GA02_FEDERATION_KIND_NOT_ADMITTED')
    owner=registry.node(owner_repository)
    destination=registry.node(target.repository)
    if owner.role=='public' or destination.role=='public':
        raise RelationshipRefused('GA02_FEDERATION_REQUIRES_APP_ENROLLED_TARGET')
    assert_observation(registry,target)
    edge=registry.edge(kind,owner.repository,target.repository)
    return {'schema':'devtool.ga02.federation-source-admission.v1',
            'policy_sha256':registry.sha256,'source_repository':owner.repository,
            'target_repository':target.repository,'target_repository_id':target.repository_id,
            'target_installation_id':target.installation_id,'kind':kind,
            'policy_ref':edge.policy_ref,'fetch_admitted':True,'write_authorized':False}


def opted_in_registry(environ:Mapping[str,str]|None=None, *, source_sha:str|None=None) -> RelationshipRegistry|None:
    env=os.environ if environ is None else environ
    path=env.get('DEVTOOL_GA02_RELATIONSHIPS_FILE','')
    digest=env.get('DEVTOOL_GA02_RELATIONSHIPS_SHA256','')
    if not path and not digest:return None
    if not path or not digest:
        raise RelationshipRefused('GA02_RELATIONSHIP_POLICY_PARTIAL_ENV')
    return load_registry(path,expected_sha256=digest,expected_source_sha=source_sha)


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description='Read-only GA-02 identity and policy inspector; never writes')
    p.add_argument('action',choices=('inspect','verify-observation','live-catalog','qualify-operation'))
    p.add_argument('--policy',required=True)
    p.add_argument('--sha256',required=True)
    p.add_argument('--source-sha',default=None)
    p.add_argument('--repository',default=None)
    p.add_argument('--operation',default=None)
    p.add_argument('--account-auth',choices=('none','env','gh'),default='none')
    p.add_argument('--account-limit',type=int,default=100)
    p.add_argument('--checkout',action='append',default=[])
    p.add_argument('--host',default='termux')
    p.add_argument('--alias',action='append',default=[])
    args=p.parse_args(argv)
    try:
        from .ga02_live_registry import (LiveRegistryRefused, qualify_operation, catalog,
            account_visible_repositories, local_checkouts)
        reg=load_registry(args.policy,expected_sha256=args.sha256,expected_source_sha=args.source_sha)
        if args.action=='inspect':
            report=reg.receipt()
        elif args.action=='qualify-operation':
            if not args.repository or not args.operation:
                raise RelationshipRefused('GA02_LIVE_REPO_AND_OPERATION_REQUIRED')
            report=qualify_operation(reg,args.repository,args.operation,source_sha=args.source_sha)
        elif args.action=='live-catalog':
            account=(account_visible_repositories(mode=args.account_auth,limit=args.account_limit)
                     if args.account_auth!='none' else None)
            checkouts=local_checkouts(args.checkout,host=args.host)
            report=catalog(reg,source_sha=args.source_sha,account=account,checkouts=checkouts,aliases=args.alias)
        else:
            if not args.repository:
                raise RelationshipRefused('GA02_REPOSITORY_ARG_REQUIRED')
            node=reg.node(args.repository)
            if node.role=='public':
                raise RelationshipRefused('GA02_PUBLIC_METADATA_CANNOT_PROBE_INSTALLED_APP')
            observed=observe_installed(node.repository,role=node.role)
            assert_observation(reg,observed)
            report={'registry':reg.receipt(),'observation':observed.receipt(),
                    'result':'PASS_LIVE_APP_READ_NO_WRITE'}
        print(json.dumps(report,indent=2,sort_keys=True))
        return 0
    except (RelationshipRefused,InstallationIdentityRefused,LiveRegistryRefused) as exc:
        print(json.dumps({'schema':'devtool.ga02.refusal.v1','status':'BLOCKED',
                          'reason':str(exc),'remote_mutation':False},sort_keys=True))
        return 2

if __name__=='__main__':
    raise SystemExit(main())


def guard_delivery_plan(*, head_repository: str, base_repository: str,
                        head_branch: str, base_branch: str,
                        head_sha: str, base_sha: str | None,
                        environ: Mapping[str,str] | None = None) -> dict[str,Any] | None:
    """Opt-in native M5 delivery preflight with actual remote source observations.

    No fallback to PAT, untrusted fork heuristics, or mutable base SHA. Existing
    non-enrolled Devtool delivery workflows remain unchanged. Guard is checked
    BEFORE starting a native session or writing a GitHub mutation intent.
    """
    registry=opted_in_registry(environ,source_sha=head_sha)
    if registry is None:return None
    if not isinstance(base_sha,str) or not _SHA1.fullmatch(base_sha):
        raise RelationshipRefused('GA02_DELIVERY_REQUIRES_EXACT_BASE_SHA')
    head_node=registry.node(head_repository)
    base_node=registry.node(base_repository)
    if head_node.role=='public':
        raise RelationshipRefused('GA02_DELIVERY_HEAD_REQUIRES_APP_INSTALLATION')
    from .github_installations import (AnonymousGitHubMetadata,
        InstallationIdentityRefused, _branch, observe_public_upstream)
    from .github_app import AppInstallationProvider, _ProviderHTTP
    try:
        head=_branch(head_branch)
        base=_branch(base_branch)
        env=os.environ if environ is None else environ
        head_obs=observe_installed(head_repository,role=head_node.role,
             environ=(env if head_node.role=='head' else None))
        assert_observation(registry,head_obs)
        if head_node.role=='base':
            from .federation_app_auth import installed_app_environment
            head_env=installed_app_environment(env)
        else:
            head_env=env
        credential=AppInstallationProvider.from_environment(head_env,
                         role=head_node.role).issue(head_repository,'repository_read')
        if credential.repository_id!=head_obs.repository_id:
            raise RelationshipRefused('GA02_DELIVERY_HEAD_GRANT_DRIFT')
        head_snapshot=prewrite_snapshot(head_obs,http=_ProviderHTTP(),
             token=credential.token,branch=head,expected_sha=head_sha)
        if head_repository.casefold()==base_repository.casefold():
            if base_node.repository_id!=head_node.repository_id:
                raise RelationshipRefused('GA02_DELIVERY_SAME_REPOSITORY_CONTRADICTION')
            # Same-repository head and base are still distinct branch refs. A
            # matching head SHA is not evidence for a separately pinned base.
            base_snapshot=prewrite_snapshot(head_obs,http=_ProviderHTTP(),
                token=credential.token,branch=base,expected_sha=base_sha)
            base_protected=base_snapshot['protected']
        else:
            if base_node.role=='public':
                anonymous=AnonymousGitHubMetadata()
                base_obs=observe_public_upstream(base_repository,http=anonymous)
                assert_observation(registry,base_obs)
                remote_commit=anonymous.request('GET','/repos/'+base_repository+'/commits/'+base)
                remote_branch=anonymous.request('GET','/repos/'+base_repository+'/branches/'+base)
                if (remote_commit.get('sha')!=base_sha or
                        type(remote_branch.get('protected')) is not bool):
                    raise RelationshipRefused('GA02_PUBLIC_UPSTREAM_SHA_OR_POLICY_DRIFT')
                base_protected=remote_branch['protected']
            else:
                base_obs=observe_installed(base_repository,role=base_node.role,
                    environ=(env if base_node.role=='head' else None))
                assert_observation(registry,base_obs)
                base_env=(env if base_node.role=='head' else
                     __import__('devtool.federation_app_auth',fromlist=['installed_app_environment'])
                        .installed_app_environment(env))
                access=AppInstallationProvider.from_environment(base_env,
                    role=base_node.role).issue(base_repository,'repository_read')
                if access.repository_id!=base_obs.repository_id:
                    raise RelationshipRefused('GA02_DELIVERY_BASE_GRANT_DRIFT')
                base_snapshot=prewrite_snapshot(base_obs,http=_ProviderHTTP(),
                        token=access.token,branch=base,expected_sha=base_sha)
                base_protected=base_snapshot['protected']
            # No permission inferred from the fork relationship. Every
            # different-repository delivery requires explicit pinned provenance.
            assert_relation(registry,'fork_of',head_obs,base_obs)
        return {'schema':'devtool.ga02.delivery-preflight.v1',
                'policy_id':registry.policy_id,'policy_sha256':registry.sha256,
                'head_repository':head_repository,'head_repository_id':head_obs.repository_id,
                'head_sha':head_sha,'head_branch_protected':head_snapshot['protected'],
                'base_repository':base_repository,'base_repository_id':base_node.repository_id,
                'base_sha':base_sha,'base_branch_protected':base_protected,
                'write_authorized':False,'remote_mutation':False}
    except InstallationIdentityRefused as exc:
        raise RelationshipRefused(str(exc)) from None
