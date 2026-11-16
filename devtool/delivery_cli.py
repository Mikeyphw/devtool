"""Canonical `delivery session` adapter: Devtool owns GitHub orchestration."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .delivery_session import DeliverySessionError, plan, start, status, resume, report
from .delivery_rich import NativeRichMonitor, MonitorDetached


def run_delivery_command(args: Any) -> int:
    operation = str(getattr(args, 'delivery_session_command', '') or '')
    root = Path(str(getattr(args, 'repo', None) or Path.cwd())).expanduser().resolve(strict=False)
    rich_monitor = None
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
                rich_monitor = NativeRichMonitor(kind='ci',
                    enabled=False if bool(getattr(args,'json',False) or
                                          getattr(args,'_devtool_json',False)) else None)
                if rich_monitor.enabled:
                    from .github_actions import GitHubActionsClient, resolve_token
                    from .delivery_remote_logs import fetch_selected_run_logs
                    def _fetch_logs(run):
                        token,_=resolve_token(str(getattr(args,'auth','auto') or 'auto'))
                        return fetch_selected_run_logs(GitHubActionsClient(token),repo_name,run)
                    rich_monitor.log_fetcher=_fetch_logs
                def progress(s:dict[str,Any])->None:
                    if rich_monitor.enabled:
                        rich_monitor.observe(s)
                        return
                    if bool(getattr(args,'json',False) or getattr(args,'_devtool_json',False)):
                        return
                    print(f"DEVTOOL_DELIVERY_CI_CHECK poll={s['poll']} state={s['state']} "
                          f"matched={len(s['runs'])} missing={len(s['missing'])} "
                          f"policy={policy.sha256[:12]}",flush=True)
                    for row in (s.get('diagnosis') or {}).get('matrix', []):
                        print(f"  run={row['id']} attempt={row['attempt']} "
                              f"workflow={row['name']} status={row['status']} "
                              f"conclusion={row['conclusion']} {row['url']}", flush=True)
                with rich_monitor:
                    payload = observe_ci(identity,required=policy.names,
                                         workflow_ids=policy.workflow_ids, required_event="pull_request",
                                         policy_sha256=policy.sha256,policy_guard=guard,
                                         strict_registration=True,
                                         checks=int(getattr(args,'checks',1)),
                                         interval=float(getattr(args,'interval',5)),
                                         auth=str(getattr(args,'auth','auto') or 'auto'),
                                         progress=progress,
                                         sleep=rich_monitor.wait_interval)
                    rich_monitor.review_failed()
                guard()
                assert_observation(payload, policy)
        else:
            raise DeliverySessionError('Unsupported delivery session command')
    except MonitorDetached as exc:
        print(f'Devtool delivery: {exc}',file=sys.stderr,flush=True)
        return 2
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
        if operation == 'ci' and rich_monitor is not None and rich_monitor.enabled:
            print('Session: '+identity+' · source-bound native CI receipt retained',flush=True)
            print('Release qualification: NOT PERFORMED',flush=True)
            return 0 if (payload.get('state') == 'REQUIRED_CHECKS_PASSED' and
                         payload.get('checks_qualified') is True) else (1 if payload.get('state') == 'CHECKS_FAILED' else 2)
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
        diagnosis = payload.get('diagnosis') or {}
        if diagnosis:
            print(f"  S5 CI verdict: {diagnosis.get('classification', 'unknown')} " 
                  f"aggregate={diagnosis.get('aggregate', 'unverified')}", flush=True)
            for failed in diagnosis.get('failures', []):
                print(f"  Diagnostic: {failed.get('classification', 'unclassified')} "
                      f"run={failed.get('run_id')} attempt={failed.get('attempt')}", flush=True)
                for job in failed.get('jobs', []):
                    print(f"    Job: {job.get('name', '-')}: {job.get('classification', '-')}", flush=True)
                    for excerpt in job.get('excerpt', []):
                        print('      Evidence: ' + excerpt, flush=True)
        print('Release qualification: NOT PERFORMED',flush=True)
    elif operation == 'report' and isinstance(payload.get('receipt'), dict):
        if sys.stdout.isatty():
            from .delivery_receipt import render_rich
            render_rich(payload['receipt'])
        else:
            print(payload['human_report'],flush=True)
    else:
        print(f'Devtool delivery: {operation} — {payload["stage"]}',flush=True)
        print('Source: '+payload['head_sha'],flush=True)
        if payload.get('id'):
            print('Session: '+payload['id'],flush=True)
        if payload.get('disposition'):
            print(f'Next: {payload["disposition"]} — {payload["next_action"]}',flush=True)
        if operation == 'report' and isinstance(payload.get('ci'),dict):
            diagnosis = payload['ci'].get('diagnosis') or {}
            print(f"CI diagnostics: {diagnosis.get('classification', 'not-observed')} "
                  f"aggregate={diagnosis.get('aggregate', 'unverified')}", flush=True)
            print(f"Report run identities: {len(diagnosis.get('matrix', []))}", flush=True)
        print('Release qualification: NOT PERFORMED — not verified',flush=True)
    if operation=='ci':
        if payload['state']=='CHECKS_FAILED':
            return 1
        # An observed-only or unrecognized state is never a CI success.
        if payload['state']!='REQUIRED_CHECKS_PASSED' or payload.get('checks_qualified') is not True:
            return 2
    if operation in ('push','pr') and payload['state'].endswith('REQUIRES_APPROVAL'):
        return 2
    return 0


def run_adoption_command(args: Any) -> int:
    """S7 P02 canonical entrypoint for P01's source-bound adoption engine.

    Never calls remote providers, applies an overlay, or modifies a consumer
    worktree. Scaffold writes only a new, exact-tree-bound artifact outside it.
    """
    import subprocess
    from .delivery_adoption import AdoptionError, audit_consumer, scaffold_overlay

    op = str(getattr(args, "delivery_adoption_command", "") or "")
    repo_value = str(getattr(args, "repo", None) or Path.cwd())
    try:
        repo = Path(repo_value).expanduser()
        if op == "audit":
            result = audit_consumer(repo)
        elif op == "scaffold":
            result = scaffold_overlay(
                repo, Path(str(args.payload)).expanduser(),
                Path(str(args.plan)).expanduser(),
                Path(str(args.output)).expanduser(),
                expected_head=str(args.expected_head),
            )
        else:
            raise AdoptionError("Unsupported canonical adoption operation")
    except AdoptionError as exc:
        print("Devtool adoption: " + str(exc), file=sys.stderr, flush=True)
        return 2
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # Do not echo arbitrary provider/file content or command-line secrets.
        print("Devtool adoption: local filesystem or Git inspection unavailable ("+
              type(exc).__name__+")", file=sys.stderr, flush=True)
        return 2
    if bool(getattr(args, "json", False) or getattr(args, "_devtool_json", False)):
        print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=False))
    elif op == "audit":
        print("Devtool adoption: READ_ONLY_AUDIT", flush=True)
        print("Consumer: " + result["repository"] + " " + result["source_sha"], flush=True)
        print("Worktree: " + ("clean" if result["worktree_clean"] else "dirty"), flush=True)
        print("Candidate duplicate launchers: " + str(len(result["candidate_duplicate_launchers"])), flush=True)
        print("Qualification: NOT PERFORMED", flush=True)
    else:
        print("Devtool adoption: SCAFFOLD_CREATED_NOT_APPLIED", flush=True)
        print("Consumer: " + result["repository"] + " " + result["source_sha"], flush=True)
        print("Artifact: " + result["output"], flush=True)
        print("SHA-256: " + result["sha256"], flush=True)
        print("Qualification: NOT PERFORMED", flush=True)
    return 0
