#!/usr/bin/env python3
"""Opt-in LIVE GitHub disposable recreation acceptance.

NOT part of unittest discovery; never invoked by wheel installation or CI.
Requires a user-created private, standalone, freshly created disposable repo.
Will DELETE and recreate it, with a verified independent backup.  This test is
intentionally forbidden for normal project repositories or forks.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
from devtool.recovery import credentials as cr
from devtool.recovery import health
from pathlib import Path

from devtool.recovery import engine as rr


def checked_name(repo, ack):
    rr.require(ack == repo, 'exact --ack-disposable-delete OWNER/REPO required')
    rr.require(rr.REPO_RE.fullmatch(repo), 'invalid repository')
    owner, name = repo.split('/')
    rr.require(re.fullmatch(r'repo-rebuild-e2e-[a-z0-9]{8,32}',name),
               'live test limited to a uniquely named repo-rebuild-e2e-<id> repository')
    return owner


def checked_disposable(repo, ack):
    owner=checked_name(repo,ack)
    actor = rr.api('user')['login']
    rr.require(owner.lower() == actor.lower(), 'live test requires your own personal GitHub repository')
    current = rr.repo_info(repo)
    rr.require(current and current.get('fork') is False and current.get('private') is True and
               current.get('owner',{}).get('type')=='User' and
               current.get('full_name','').lower()==repo.lower(),
               'live test only supports an existing private standalone disposable repository')
    rr.require(not current.get('archived') and current.get('permissions',{}).get('admin'),
               'disposable test repository is archived or lacks admin permissions')
    age=(dt.datetime.now(dt.timezone.utc) -
         dt.datetime.fromisoformat(current['created_at'].replace('Z','+00:00')))
    rr.require(dt.timedelta(0) <= age < dt.timedelta(days=2),
               'disposable repo must be less than 48 hours old')
    refs = rr.live_refs(repo)
    rr.require(0 < len(refs) <= 8, 'disposable repo must have 1–8 refs')
    rr.require(not rr.collect(f'repos/{repo}/pulls?state=all') and
               not rr.collect(f'repos/{repo}/issues?state=all'),
               'disposable repo must not have existing issues or PRs')
    rr.require(not rr.collect(f'repos/{repo}/actions/secrets',key='secrets') and
               not rr.collect(f'repos/{repo}/dependabot/secrets',key='secrets'),
               'disposable repo must have no stored repository/Dependabot secrets')
    rr.require(not rr.api(f'repos/{repo}/rulesets?includes_parents=true'),
               'disposable repo has rulesets; do not delete')
    return current, refs


def checked_policy(path, repo):
    """A fixture may not provision secrets or installation access in another repo."""
    if path is None:
        return {'schema':1,'repository':repo,'credentials':[]}
    policy=cr.load_policy(path,repo)
    for item in policy['credentials']:
        rr.require(item['repositories']==[repo] and set(item.get('secret_hosts',[])) <= {repo},
                   'disposable credential policy may target only the exact disposable repository')
    return policy


def current_source(expected):
    rr.require(isinstance(expected,str) and re.fullmatch(r'[0-9a-f]{40}',expected),
               'exact --expected-devtool-head required')
    root=Path(__file__).resolve().parents[1]
    actual=subprocess.run(['git','-C',str(root),'rev-parse','HEAD'],check=True,
                          capture_output=True,text=True).stdout.strip()
    changes=subprocess.run(['git','-C',str(root),'status','--porcelain=v1','--untracked-files=all'],
                           check=True,capture_output=True,text=True).stdout
    rr.require(actual==expected and not changes,'disposable test requires exact clean reviewed Devtool source')
    return actual


def verify_fixture(session, repository, source_sha, *, apply_app=False, apply_pat=False):
    with rr.locked(session):
        state=rr.load_state(session)
        rr.require(state.get('repo')==repository and state.get('stage') in
                   ('done','done-with-action-required','done-manual-attested'),
                   'disposable session not a completed restoration')
        rr.require(state.get('disposable_test_devtool_head')==source_sha,
                   'disposable session is not bound to this Devtool test source')
        rr.verify_pair(session,state)
        rr.require_independent_backup(session,state)
        before=rr.read_json(session/'primary'/'source-refs.json')
        original=rr.read_json(session/'primary'/'repo.json')
        current=rr.get_new_repo(state)
        rr.require(original.get('full_name')==repository and original.get('private') is True and
                   original.get('fork') is False and current.get('full_name')==repository and
                   current.get('private') is True and current.get('fork') is False and
                   current['id']!=original['id'] and current['id']==state['created_new_id'],
                   'disposable original/replacement identity or privacy mismatch')
        rr.require(rr.live_refs(repository)==before,'disposable restored refs differ from exact original inventory')
        ready=rr.read_json(session/'credential-predelete-recheck.json')
        rr.require(ready.get('status')=='CURRENT_FEASIBILITY_REVERIFIED' and
                   ready.get('original_repository_id')==original['id'] and
                   ready.get('policy_sha256')==state.get('credential_policy_sha256') and
                   ready.get('readiness_sha256')==state.get('credential_readiness_sha256'),
                   'disposable pre-deletion credential evidence missing')
        cr.verify_preparation_anchor(session,state)
        checked_policy(session/'credential-policy.json',repository)
        admin=cr._admin_observation(repository,current['id'])
        proof=cr.post_restore(session,state,apply_apps=apply_app,apply_pats=apply_pat,record=True)
        anchored=health.credential_receipt(session,state)
        rr.require(anchored.get('provenance_verified') is True,'disposable credential receipt lacks provenance')
        # A credential-free fixture proves lifecycle, not PAT or App recovery.
        rows=proof.get('credential_results',[])
        kinds=sorted({x['kind'] for x in rows if x['status']=='verified'})
        report={'schema':'devtool.recovery.disposable-live.v1','devtool_head':source_sha,
                'repository':repository,'original_id':original['id'],'replacement_id':current['id'],
                'exact_refs_verified':True,'independent_backup_verified':True,
                'predelete_credential_recheck_verified':True,'restored_admin_verified':admin['admin'],
                'credential_status':proof['status'],'credential_kinds_verified':kinds,
                'credential_acceptance_sha256':state['credential_acceptance_sha256'],
                'live_lifecycle_passed':True,'live_pat_qualified':'fine_grained_pat' in kinds,
                'live_app_qualified':'github_app' in kinds,
                'hosted_workload_qualified':False,'release_qualified':False}
        rr.write_json(session/'disposable-live-acceptance.json',report)
        print('DISPOSABLE_LIVE_LIFECYCLE_PASS')
        print('DISPOSABLE_CREDENTIAL_STATUS='+proof['status'])
        print('DISPOSABLE_PAT_VERIFIED='+str(report['live_pat_qualified']).lower())
        print('DISPOSABLE_APP_VERIFIED='+str(report['live_app_qualified']).lower())
        print('DISPOSABLE_HOSTED_WORKLOAD=NOT_PERFORMED')
        print('DISPOSABLE_EVIDENCE='+str(session/'disposable-live-acceptance.json'))
        rr.require(proof['status']=='verified',
                   'disposable credential access pending; repair selection and use --verify-session, never delete again')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repository',help='OWNER/repo-rebuild-e2e-UNIQUE')
    p.add_argument('--independent-copy-dir',type=Path)
    p.add_argument('--ack-disposable-delete',required=True)
    p.add_argument('--expected-devtool-head',required=True)
    p.add_argument('--root',type=Path,default=rr.DEFAULT_ROOT)
    p.add_argument('--credentials-policy',type=Path)
    p.add_argument('--accept-credential-manual-risk',action='store_true')
    p.add_argument('--verify-session',type=Path,help='Verify an existing completed fixture; no new deletion')
    p.add_argument('--ack-app-repair',help='Exact disposable OWNER/REPO; only in verify-session')
    p.add_argument('--ack-pat-provision',help='Exact disposable OWNER/REPO; only in verify-session')
    args=p.parse_args(argv)
    session=None
    try:
        checked_name(args.repository,args.ack_disposable_delete)
        source_sha=current_source(args.expected_devtool_head)
        if args.verify_session is not None:
            rr.require(args.credentials_policy is None and args.independent_copy_dir is None and
                       not args.accept_credential_manual_risk,
                       'verify-session cannot replace original policy, archive or risk decision')
            rr.require(args.ack_app_repair in (None,args.repository) and
                       args.ack_pat_provision in (None,args.repository),
                       'credential mutation must match exact disposable repository')
            session=args.verify_session.expanduser().resolve()
            verify_fixture(session,args.repository,source_sha,apply_app=bool(args.ack_app_repair),
                           apply_pat=bool(args.ack_pat_provision))
            return 0
        rr.require(not args.ack_app_repair and not args.ack_pat_provision,
                   'credential mutation requires separate --verify-session stage')
        rr.require(args.independent_copy_dir is not None,'verified independent filesystem backup is mandatory')
        policy=checked_policy(args.credentials_policy,args.repository)
        old, refs = checked_disposable(args.repository,args.ack_disposable_delete)
        rr.log('Disposable test: verified private, recently created, empty-metadata repository',style='yellow')
        session=rr.new_session(args.repository,base=args.root.expanduser())
        print('DISPOSABLE_SESSION='+str(session))
        state=rr.load_state(session)
        state['disposable_test_devtool_head']=source_sha
        rr.save_state(session,state)
        declared=session/'disposable-policy-input.json'
        rr.write_json(declared,policy)
        # No archive gap, local-only risk, missing-secret or generic manual waiver.
        rr.prepare(session,independent_copy_dir=args.independent_copy_dir,
                   credentials_policy=declared,
                   accept_credential_manual_risk=args.accept_credential_manual_risk)
        state=rr.load_state(session)
        rr.require(state['original_repo_id']==old['id'] and
                   rr.read_json(session/'primary'/'source-refs.json')==refs,
                   'disposable identity or source moved before backup')
        rr.verify_pair(session,state)
        rr.require_independent_backup(session,state)
        rr.resume(session,ack_delete=args.repository)
        verify_fixture(session,args.repository,source_sha)
        return 0
    except (rr.Refused, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        rr.log('LIVE DISPOSABLE TEST BLOCKED/FAILED: '+str(exc),style='bold red')
        if session is not None:
            rr.log('Preserved disposable session: '+str(session),style='yellow')
        rr.log('Preserve the journal and snapshot; do not delete another repo or repeat the transaction',style='yellow')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
