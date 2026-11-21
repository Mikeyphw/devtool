"""Native canonical Devtool CLI dispatcher for remote recovery operations.

No shell/subprocess proxy to standalone RepoRebuild. Uses the integrated engine.
"""
from __future__ import annotations
import argparse
from pathlib import Path
from .engine import *
import subprocess
import urllib.error
import zipfile
from rich.panel import Panel
from . import engine as _engine
from .provider import RecoveryTransportError

_NATIVE_ALIASES = {
    'execute': 'run',
    'workflows': 'workflow-audit',
    'qualify': 'qualify-pr',
}
_PATH_FIELDS = ('session','root','independent_copy_dir','secrets_vault','age_identity','policy','credentials_policy')


def run_native(args: argparse.Namespace) -> int:
    args = argparse.Namespace(**vars(args))
    try:
        requested = str(getattr(args, 'recovery_command', '') or '')
        if not requested:
            raise ValueError('Recovery subcommand is required')
        args.command = _NATIVE_ALIASES.get(requested, requested)
        if hasattr(args, 'recovery_repository'):
            args.repo = args.recovery_repository
        for key in _PATH_FIELDS:
            raw = getattr(args, key, None)
            if key == 'root' and raw is None:
                raw = DEFAULT_ROOT
                setattr(args, key, raw)
            if raw is not None and not isinstance(raw, Path):
                setattr(args, key, Path(raw))
        if args.command == 'doctor':
            from .health import doctor
            return doctor(args)
        if args.command == 'plan':
            from .health import plan
            return plan(args)
        if args.command == 'report':
            from .health import report
            return report(args)
        if args.command=='inspect':inspect_repo(args.repo)
        elif args.command=='wizard':
            require(REPO_RE.fullmatch(args.repo),'repo must be OWNER/REPO')
            guided_rebuild(args.repo,root=args.root.expanduser(),
                independent_copy_dir=args.independent_copy_dir,
                accept_local_only_risk=args.accept_local_only_risk,
                secrets_vault=args.secrets_vault,age_identity=args.age_identity,
                accept_missing_secrets=args.accept_missing_secrets,
                accept_manual_recovery=args.accept_manual_recovery,
                credentials_policy=args.credentials_policy,
                accept_credential_manual_risk=args.accept_credential_manual_risk)
        elif args.command=='sessions':list_sessions(args.repo,args.root.expanduser())
        elif args.command=='prepare':
            require(REPO_RE.fullmatch(args.repo),'repo must be OWNER/REPO')
            if args.session:session=args.session.expanduser().resolve()
            else:session=new_session(args.repo,base=args.root.expanduser())
            state=load_state(session)
            require(state['repo'].lower()==args.repo.lower(),'session repository mismatch')
            prepare(session,accept_archive_gaps=args.accept_archive_gaps,
                skip_logs=args.skip_logs,max_artifact_mb=args.max_artifact_mb,
                skip_action_artifacts=args.skip_action_artifacts,
                independent_copy_dir=args.independent_copy_dir,
                accept_local_only_risk=args.accept_local_only_risk,
                secrets_vault=args.secrets_vault,age_identity=args.age_identity,
                accept_missing_secrets=args.accept_missing_secrets,
                accept_manual_recovery=args.accept_manual_recovery,
                credentials_policy=args.credentials_policy,
                accept_credential_manual_risk=args.accept_credential_manual_risk)
        elif args.command=='run':
            session=args.session.expanduser().resolve()
            require(args.post_recovery or not (args.ack_reconcile_default or
                args.ack_enable_actions or args.pr is not None or
                args.required_workflow or args.ack_retrigger or args.watch),
                'post-restoration options require --post-recovery')
            resume(session,ack_delete=args.ack_delete,
                   accept_base_drift=args.accept_base_drift,
                   secrets_vault=args.secrets_vault,age_identity=args.age_identity,
                   adopt_created_repo_id=args.adopt_created_repo_id)
            if args.post_recovery:
                from .operational import recover_operational
                recover_operational(session,
                    ack_reconcile=args.ack_reconcile_default,
                    ack_actions=args.ack_enable_actions,pr=args.pr,
                    required=tuple(args.required_workflow),
                    ack_retrigger=args.ack_retrigger,
                    watch=args.watch,timeout_seconds=args.timeout_seconds)
        elif args.command=='reconcile-default':
            from .operational import reconcile_default
            reconcile_default(args.session.expanduser().resolve(),ack_repo=args.ack_reconcile_default)
        elif args.command=='activate-actions':
            from .operational import activate_actions
            activate_actions(args.session.expanduser().resolve(),ack_repo=args.ack_enable_actions)
        elif args.command=='workflow-audit':
            from .qualification import audit_workflows
            audit_workflows(args.session.expanduser().resolve(),record=args.record)
        elif args.command=='qualify-pr':
            from .qualification import qualify_pr
            qualify_pr(args.session.expanduser().resolve(),number=args.pr,
                       required=tuple(args.required_workflow),ack_retrigger=args.ack_retrigger,
                       watch=args.watch,timeout_seconds=args.timeout_seconds)
        elif args.command=='recover-operational':
            from .operational import recover_operational
            recover_operational(args.session.expanduser().resolve(),
                ack_reconcile=args.ack_reconcile_default,ack_actions=args.ack_enable_actions,
                pr=args.pr,required=tuple(args.required_workflow),
                ack_retrigger=args.ack_retrigger,watch=args.watch,
                timeout_seconds=args.timeout_seconds)
        elif args.command=='credentials':
            from . import credentials
            session=args.session.expanduser().resolve()
            require(not args.apply_app or args.ack_app_repair is not None,
                    'App installation mutation requires --ack-app-repair OWNER/REPO')
            require(not args.apply_pat or args.ack_pat_provision is not None,
                    'PAT secret reprovision requires --ack-pat-provision OWNER/REPO')
            with locked(session):
                state=load_state(session)
                require(state['stage'] in (DONE,'done-with-action-required','done-manual-attested'),
                        'credential audit after recreation requires a completed restore')
                verify_pair(session,state)
                require_independent_backup(session,state)
                get_new_repo(state)
                if args.policy is not None:
                    credentials.bind_post_restore_policy(session,state,args.policy)
                    state=load_state(session)
                require(not args.apply_app or args.ack_app_repair==state['repo'],
                        'App repair approval must match exact OWNER/REPO')
                require(not args.apply_pat or args.ack_pat_provision==state['repo'],
                        'PAT secret provision approval must match exact OWNER/REPO')
                result=credentials.post_restore(session,state,apply_apps=args.apply_app,
                    apply_pats=args.apply_pat,
                    record=args.record or args.apply_app or args.apply_pat)
                log(credentials.status_summary(result),style='green' if result['status']=='verified' else 'yellow')
                if result['status']!='verified':
                    raise Refused('credential access not verified; see guided repair requirements')
        elif args.command=='status':
            session=args.session.expanduser().resolve();render_status(session,load_state(session))
        elif args.command=='attest-manual':
            attest_manual_recovery(args.session.expanduser().resolve(),
                ack_external_recovery=args.ack_external_recovery)
        elif args.command=='ci-check':
            require(60<=args.timeout_seconds<=3600,'CI timeout must be 60–3600 seconds')
            verified_ci_dispatch(args.session.expanduser().resolve(),workflow=args.workflow,
                ref=args.ref,ack_dispatch=args.ack_dispatch,
                timeout_seconds=args.timeout_seconds)
        elif args.command=='verify':
            session=args.session.expanduser().resolve()
            state=load_state(session)
            verify_pair(session,state)
            require_independent_backup(session,state)
            log('BACKUPS VERIFIED (does not prove operational CI)',style='bold green')
        return 0
    except (Refused,RecoveryTransportError,safety.SafetyError,ValueError,KeyError,UnicodeError,OSError,subprocess.SubprocessError,
            urllib.error.URLError,zipfile.BadZipFile) as e:
        CONSOLE.print(Panel(str(e),title='Recovery stopped — no unsafe retry',style='red'))
        if getattr(args,'session',None):
            CONSOLE.print(f'[yellow]Journal preserved:[/yellow] {args.session}')
            CONSOLE.print('Inspect: devtool recovery status --session '+
                          repr(str(args.session)))
            CONSOLE.print('Resume only after resolving the reported error; '
                          'do not delete the repository manually.')
        return 2
