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
from pathlib import Path
import re

from devtool.recovery import engine as rr


def checked_disposable(repo, ack):
    rr.require(ack == repo, 'exact --ack-disposable-delete OWNER/REPO required')
    rr.require(rr.REPO_RE.fullmatch(repo), 'invalid repository')
    owner, name = repo.split('/')
    rr.require(re.fullmatch(r'repo-rebuild-e2e-[a-z0-9]{8,32}',name),
               'live test limited to a uniquely named repo-rebuild-e2e-<id> repository')
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


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('repository',help='OWNER/repo-rebuild-e2e-UNIQUE')
    p.add_argument('--independent-copy-dir',type=Path,required=True)
    p.add_argument('--ack-disposable-delete',required=True)
    p.add_argument('--root',type=Path,default=rr.DEFAULT_ROOT)
    args=p.parse_args(argv)
    try:
        old, refs = checked_disposable(args.repository,args.ack_disposable_delete)
        rr.log('Disposable test: verified private, recently created, empty-metadata repository',style='yellow')
        session=rr.new_session(args.repository,base=args.root.expanduser())
        rr.prepare(session,independent_copy_dir=args.independent_copy_dir,
                   accept_archive_gaps=True,accept_manual_recovery=True)
        rr.verify_pair(session,rr.load_state(session))
        rr.require_independent_backup(session,rr.load_state(session))
        rr.resume(session,ack_delete=args.repository)
        current=rr.repo_info(args.repository)
        rr.require(current and current['id'] != old['id'], 'disposable identity unchanged')
        restored=rr.live_refs(args.repository)
        rr.require(all(restored.get(r)==sha for r,sha in refs.items()),
                   'disposable original refs were not restored')
        rr.log('LIVE DISPOSABLE RECOVERY SOURCE/ID VERIFIED; check workflow/CI separately',
               style='bold green')
        rr.log('Session: '+str(session),style='blue')
        return 0
    except (rr.Refused, OSError, ValueError, KeyError) as exc:
        rr.log('LIVE DISPOSABLE TEST BLOCKED/FAILED: '+str(exc),style='bold red')
        rr.log('Preserve the printed journal and snapshot; do not delete another repo',style='yellow')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
