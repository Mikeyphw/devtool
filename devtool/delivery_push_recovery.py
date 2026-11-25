"""DT-19 native Devtool push-intent reconciliation; no journal rewrites by operators."""
from __future__ import annotations
import argparse
import json
import sys
from .delivery_github import push
from .delivery_session import DeliverySessionError
from .github_app import GitHubAppError

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--session-id',required=True)
    ap.add_argument('--prior-intent-id',required=True)
    ap.add_argument('--auth',choices=('app',),default='app')
    ap.add_argument('--prior-process-finished',action='store_true')
    ap.add_argument('--prior-branch-absent',action='store_true',help='Explicitly acknowledge prior preflight evidence of absent branch (legacy journal)')
    ap.add_argument('--yes',action='store_true')
    ap.add_argument('--reconcile-only',action='store_true')
    args=ap.parse_args(argv)
    try:
        if args.reconcile_only:
            if args.yes:
                raise DeliverySessionError('Read-only remote reconciliation forbids --yes')
            result=push(args.session_id,auth=args.auth,yes=False)
        else:
            result=push(args.session_id,auth=args.auth,yes=args.yes,
                        recovery_intent_id=args.prior_intent_id,
                        prior_process_finished=args.prior_process_finished,
                        prior_branch_absent=args.prior_branch_absent)
    except (DeliverySessionError,GitHubAppError,OSError,ValueError) as exc:
        print('DT19_NATIVE_RECOVERY_REFUSED '+str(exc),file=sys.stderr,flush=True)
        return 2
    print(json.dumps(result,sort_keys=True,indent=2),flush=True)
    return 0 if result.get('state')=='PUSH_VERIFIED' else 3

if __name__=='__main__':
    raise SystemExit(main())
