"""Read-only native recovery health, plan and report surfaces."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
from . import engine as rr
from . import credentials
from . import external_dependencies

def _emit(payload: dict) -> int:
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get('ok') else 2

def doctor(args) -> int:
    required = ('git','gh','python3')
    tools = {name:bool(shutil.which(name)) for name in required}
    return _emit({'schema':'devtool.recovery.health.v1', 'ok':all(tools.values()),
                  'status':'READY_FOR_PREFLIGHT' if all(tools.values()) else 'BLOCKED',
                  'tools':tools, 'credential_authorization_verified':False,
                  'repository_mutated':False})

def plan(args) -> int:
    repo = str(getattr(args,'recovery_repository','') or '')
    if not rr.REPO_RE.fullmatch(repo):
        return _emit({'schema':'devtool.recovery.plan.v1','ok':False,'status':'BLOCKED',
                      'reason':'Expected explicit OWNER/REPO'})
    # 'inspect_repo' prints current inventory and can use GitHub API.
    rr.inspect_repo(repo)
    return _emit({'schema':'devtool.recovery.plan.v1','ok':True,'repository':repo,
                  'state':'OBSERVED_ONLY', 'backups_verified':False,
                  'credential_policy_required':True,
                  'external_dependency_scope':'known profile plus explicit operator declaration; not exhaustive inbound discovery',
                  'known_external_dependencies':external_dependencies.profile(repo),
                  'credentials_verified':False,'deletion_authorized':False,
                  'delete_permitted':False})

def report(args) -> int:
    raw=getattr(args,'session',None)
    if not raw:
        return _emit({'schema':'devtool.recovery.report.v1','ok':False,
                      'status':'BLOCKED','reason':'Session path required'})
    session = Path(raw).expanduser().resolve()
    state=rr.load_state(session)
    observed = credential_receipt(session,state)
    result={'schema':'devtool.recovery.report.v1','session':str(session),
            'repository':state.get('repo'),'source_stage':state.get('stage'),
            'replacement_repository_id':state.get('created_new_id'),
            'credentials':observed,
            'known_external_dependencies':external_dependencies.profile(state.get('repo')),
            'credential_proof_live':False,
            'qualified':False,'release_qualified':False,
            'not_a_live_ci_verdict':True}
    return _emit({'ok':True,**result})


def credential_receipt(session, state):
    """Report historical evidence, verifying its journal anchor, not live access."""
    saved_status=state.get('credential_recovery_status')
    expected=state.get('credential_acceptance_sha256')
    path=session/'credential-acceptance.json'
    if not path.is_file() or path.is_symlink():
        return {'status':'NOT_ATTESTED' if not saved_status else 'EVIDENCE_MISSING',
                'recorded_status':saved_status,'provenance_verified':False,'live_verified':False}
    try:
        row=rr.read_json(path)
        valid=(isinstance(expected,str) and len(expected)==64 and rr.sha256(path)==expected and
               row.get('repo')==state.get('repo') and row.get('replacement_id')==state.get('created_new_id') and
               row.get('policy_sha256')==state.get('credential_policy_sha256') and row.get('status')==saved_status)
        if not valid:
            return {'status':'EVIDENCE_UNANCHORED_OR_MISMATCHED','recorded_status':saved_status,
                    'provenance_verified':False,'live_verified':False}
        federation=row.get('federation') or {}
        if external_dependencies.applies(state.get('repo')) and saved_status=='verified' and federation.get('qualified') is not True:
            return {'status':'EVIDENCE_UNANCHORED_OR_MISMATCHED','recorded_status':saved_status,
                    'provenance_verified':False,'live_verified':False}
        return {'status':saved_status, 'provenance_verified':True, 'live_verified':False,
                'replacement_id':row.get('replacement_id'),'policy_sha256':row.get('policy_sha256'),
                'federation':federation,'unclassified':row.get('unclassified',[]),
                'receipt_sha256':expected}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, rr.Refused):
        return {'status':'EVIDENCE_UNREADABLE','recorded_status':saved_status,
                'provenance_verified':False,'live_verified':False}
