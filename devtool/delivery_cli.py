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
                from .delivery_ci_policy import (load_policy, check_requested,
                                                 assert_observation)
                repo_name = str(bound['repository'])
                base_branch = str(bound['base'])
                policy = load_policy(root, expected_repository=repo_name,
                                     expected_base=base_branch)
                check_requested(tuple(getattr(args,'require_workflow',[]) or ()), policy)
                def guard() -> None:
                    again = load_policy(root, expected_repository=repo_name,
                                        expected_base=base_branch)
                    if again.sha256 != policy.sha256:
                        raise DeliverySessionError('Committed CI policy changed during observation')
                def progress(s:dict[str,Any])->None:
                    if bool(getattr(args,'json',False) or getattr(args,'_devtool_json',False)):
                        return
                    print(f"DEVTOOL_DELIVERY_CI_CHECK poll={s['poll']} state={s['state']} "
                          f"matched={len(s['runs'])} missing={len(s['missing'])} "
                          f"policy={policy.sha256[:12]}",flush=True)
                payload = observe_ci(identity,required=policy.names,
                                     workflow_ids=policy.workflow_ids, required_event="pull_request",
                                     policy_sha256=policy.sha256,policy_guard=guard,
                                     strict_registration=True,
                                     checks=int(getattr(args,'checks',1)),
                                     interval=float(getattr(args,'interval',5)),
                                     auth=str(getattr(args,'auth','auto') or 'auto'),
                                     progress=progress)
                guard()
                assert_observation(payload, policy)
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
        registration = payload.get('registration') or {}
        if registration:
            print(f"  Registrar: event={registration.get('event')} "
                  f"deadline={registration.get('deadline_at')} "
                  f"scanned={registration.get('run_count')}",flush=True)
            for name in registration.get('ambiguous',[]):
                print(f'  Ambiguous concurrent run: {name}',flush=True)
            for name in registration.get('not_expected',[]):
                print(f'  Trigger excludes required workflow: {name}',flush=True)
            for name in registration.get('indeterminate',[]):
                print(f'  Conditional trigger needs explicit proof: {name}',flush=True)
        evidence = payload.get('exact_run_evidence') or {}
        for issue in evidence.get('issues', []):
            print(f"  Evidence pending: {issue['workflow']} — {issue['reason']}", flush=True)
        for item in payload.get('failures', []):
            for job in item.get('detailed_jobs', []):
                print(f"  Failed job: {job.get('job', '-')}: {job.get('classification', '-')} {job.get('url', '')}",flush=True)
                for step in job.get('steps', []):
                    print(f"    Step: {step.get('name', '-')} — {step.get('conclusion', '-')}",flush=True)
        print('Release qualification: NOT PERFORMED',flush=True)
    else:
        print(f'Devtool delivery: {operation} — {payload["stage"]}',flush=True)
        print('Source: '+payload['head_sha'],flush=True)
        if payload.get('id'):
            print('Session: '+payload['id'],flush=True)
        if payload.get('disposition'):
            print(f'Next: {payload["disposition"]} — {payload["next_action"]}',flush=True)
        print('Release qualification: NOT PERFORMED',flush=True)
    if operation=='ci':
        if payload['state']=='CHECKS_FAILED':
            return 1
        # An observed-only or unrecognized state is never a CI success.
        if payload['state']!='REQUIRED_CHECKS_PASSED' or payload.get('checks_qualified') is not True:
            return 2
    if operation in ('push','pr') and payload['state'].endswith('REQUIRES_APPROVAL'):
        return 2
    return 0
