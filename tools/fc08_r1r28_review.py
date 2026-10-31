#!/usr/bin/env python3
"""Read-only, explicit run review for unresolved Devtool dispatch journal.

Never updates dispatch journal or attributes a candidate run to a request.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from devtool.github_actions import GitHubActionsClient, GitHubActionsError
from devtool.actions_dispatch import review_uncertain_dispatch

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--request-id',required=True)
    ap.add_argument('--run-id',type=int,required=True)
    ap.add_argument('--state-root',type=Path)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args()
    try:
        token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if not token:
            proc=subprocess.run(['gh','auth','token'],capture_output=True,text=True,timeout=8)
            if proc.returncode:raise GitHubActionsError('GITHUB_TOKEN_UNAVAILABLE')
            token=proc.stdout.strip()
        if not token:raise GitHubActionsError('GITHUB_TOKEN_UNAVAILABLE')
        folder=a.state_root.resolve() if a.state_root else None
        target=a.out.expanduser().absolute()
        if target.is_symlink() or (folder is not None and target.is_relative_to(folder)):
            raise GitHubActionsError('UNSAFE_REVIEW_OUTPUT')
        doc=review_uncertain_dispatch(GitHubActionsClient(token),a.request_id,a.run_id,state_root=folder)
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=target.parent,delete=False) as f:
            json.dump(doc,f,sort_keys=True,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());tmp=f.name
        os.replace(tmp,target)
        print('PASS_FC08_R1R28_READ_ONLY_COMPATIBILITY_REVIEW_NOT_ATTRIBUTED')
        return 0
    except (GitHubActionsError,ValueError,OSError,subprocess.SubprocessError):
        print('BLOCKED_FC08_R1R28_READ_ONLY_REVIEW',file=sys.stderr)
        return 2
if __name__=='__main__':sys.exit(main())
