"""Canonical `delivery session` adapter: Devtool owns GitHub orchestration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .delivery_session import DeliverySessionError, plan, start, status, resume, report


def run_delivery_command(args: Any) -> int:
    operation = str(getattr(args, 'delivery_session_command', '') or '')
    root = Path(str(getattr(args, 'repo', None) or Path.cwd())).expanduser().resolve(strict=False)
    try:
        if operation in ('plan', 'start'):
            options = {'base':str(getattr(args,'base','main') or 'main'),
                       'head':getattr(args,'head',None),
                       'overlay':Path(args.overlay) if getattr(args,'overlay',None) else None}
            payload = plan(root,**options) if operation=='plan' else start(root,**options)
        elif operation in ('status','resume','report'):
            identity = str(getattr(args,'delivery_id','') or '')
            action = {'status':status,'resume':resume,'report':report}[operation]
            payload = action(identity)
            if Path(payload['repo_root']).resolve() != root:
                raise DeliverySessionError('Session belongs to another repository/worktree')
        elif operation in ('push','pr','ci'):
            from .delivery_github import push, pr, observe_ci
            identity = str(getattr(args,'delivery_id','') or '')
            bound = status(identity)
            if Path(bound['repo_root']).resolve() != root:
                raise DeliverySessionError('Session belongs to another repository/worktree')
            if operation == 'push':
                payload = push(identity,yes=bool(getattr(args,'yes',False)))
            elif operation == 'pr':
                payload = pr(identity,yes=bool(getattr(args,'yes',False)),
                             title=str(getattr(args,'title','') or ''),
                             body=str(getattr(args,'body','') or ''),
                             auth=str(getattr(args,'auth','auto') or 'auto'))
            else:
                def progress(s:dict[str,Any])->None:
                    if bool(getattr(args,'json',False) or getattr(args,'_devtool_json',False)):
                        return
                    print(f"DEVTOOL_DELIVERY_CI_CHECK poll={s['poll']} state={s['state']} "
                          f"matched={len(s['runs'])} missing={len(s['missing'])}",flush=True)
                payload = observe_ci(identity,required=tuple(getattr(args,'require_workflow',[]) or []),
                                     checks=int(getattr(args,'checks',1)),
                                     interval=float(getattr(args,'interval',5)),
                                     auth=str(getattr(args,'auth','auto') or 'auto'),
                                     progress=progress)
        else:
            raise DeliverySessionError('Unsupported delivery session command')
    except (DeliverySessionError, OSError, ValueError) as exc:
        print(f'Devtool delivery: {exc}',file=sys.stderr,flush=True)
        return 2
    except Exception as exc:
        from .github_actions import GitHubActionsError
        if isinstance(exc,GitHubActionsError):
            print(f'Devtool delivery: GitHub observation failed ({exc})',file=sys.stderr,flush=True)
            return 2
        raise
    if bool(getattr(args,'json',False) or getattr(args,'_devtool_json',False)):
        print(json.dumps(payload,indent=2,sort_keys=True,ensure_ascii=False))
    elif operation in ('push','pr','ci'):
        print(f'Devtool delivery {operation}: {payload["state"]}',flush=True)
        if payload.get('url'):
            print('PR: '+payload['url'],flush=True)
        for run in payload.get('runs',[]):
            print(f"  {run['name']}: {run['conclusion'] or run['status']} {run['url']}",flush=True)
        for missing in payload.get('missing',[]):
            print(f'  Waiting registration: {missing}',flush=True)
        print('Release qualification: NOT PERFORMED',flush=True)
    else:
        print(f'Devtool delivery: {operation} — {payload["stage"]}',flush=True)
        print('Source: '+payload['head_sha'],flush=True)
        if payload.get('id'):
            print('Session: '+payload['id'],flush=True)
        if payload.get('disposition'):
            print(f'Next: {payload["disposition"]} — {payload["next_action"]}',flush=True)
        print('Release qualification: NOT PERFORMED',flush=True)
    if operation=='ci' and payload['state'] in ('WAITING_REGISTRATION','CHECKS_RUNNING','BLOCKED_AMBIGUOUS'):
        return 2
    if operation=='ci' and payload['state']=='CHECKS_FAILED':
        return 1
    if operation in ('push','pr') and payload['state'].endswith('REQUIRES_APPROVAL'):
        return 2
    return 0
