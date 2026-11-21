"""Read-only native recovery health, plan and report surfaces."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
from . import engine as rr
from . import credentials

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
                  'credentials_verified':False,'deletion_authorized':False,
                  'delete_permitted':False})

def report(args) -> int:
    raw=getattr(args,'session',None)
    if not raw:
        return _emit({'schema':'devtool.recovery.report.v1','ok':False,
                      'status':'BLOCKED','reason':'Session path required'})
    session = Path(raw).expanduser().resolve()
    state=rr.load_state(session)
    result={'schema':'devtool.recovery.report.v1','session':str(session),
            'repository':state.get('repo'),'source_stage':state.get('stage'),
            'replacement_repository_id':state.get('created_new_id'),
            'credentials':state.get('credentials_post_restore'),
            'qualified':False,'release_qualified':False,
            'not_a_live_ci_verdict':True}
    return _emit({'ok':True,**result})
