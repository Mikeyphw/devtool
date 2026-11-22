"""Canonical M7 credential command handler; no separate command authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .m7_credentials import (
    CredentialDoctorError, compare_baseline, inspect_app,
    inspect_ssh, inspect_user_token, load_baseline,
    postrecreate_acceptance, predelete_feasibility,
    repair_guidance, write_baseline,
)


def run(args: argparse.Namespace) -> int:
    """Only canonical `credentials` parser calls this; all source pins stay native."""
    repo = str(getattr(args,'credential_repository','') or '').strip()
    mode = str(getattr(args,'credential_auth','app') or 'app')
    operation = str(getattr(args,'credential_action','doctor') or 'doctor')
    read_probe = bool(getattr(args,'credential_probe_read',False))
    head_repo = str(getattr(args,'credential_head_repository','') or '').strip()
    try:
        if mode == 'app':
            health=inspect_app(repo,probe=read_probe)
        elif mode == 'ssh':
            health=inspect_ssh(repo,probe=read_probe)
        else:
            health=inspect_user_token(repo,mode=mode)
        if head_repo:
            if head_repo.casefold() == repo.casefold():
                health['fork_head_access'] = 'SAME_REPOSITORY_BASE_IDENTITY'
            else:
                if mode == 'app':
                    head_health = inspect_app(head_repo,role='head',probe=read_probe)
                elif mode == 'ssh':
                    head_health = inspect_ssh(head_repo,probe=read_probe)
                else:
                    head_health = inspect_user_token(head_repo,mode=mode)
                health['fork_head_health'] = head_health
                if head_health.get('status')!='VALID':
                    health['status'] = 'UNVERIFIED'
                    health['reason'] = 'FORK_HEAD_CREDENTIAL_NOT_INDEPENDENTLY_VERIFIED'
                else:
                    health['fork_head_access']='INDEPENDENTLY_READ_VERIFIED'
        if operation=='doctor':
            outcome=health
        elif operation=='repair':
            outcome={'health':health,'repair':repair_guidance(health),
                     'schema':'devtool.m7.repair-report.v1',
                     'remote_repository_mutation':False}
        elif operation=='drift':
            baseline=load_baseline(Path(args.credential_baseline))
            outcome={'health':health,'drift':compare_baseline(health,baseline),
                     'schema':'devtool.m7.drift-report.v1',
                     'remote_repository_mutation':False}
        elif operation=='predelete':
            outcome={'health':health,'feasibility':predelete_feasibility(
                health,backup_sha256=str(getattr(args,'credential_backup_sha256','') or '')),
                     'schema':'devtool.m7.predelete-report.v1',
                     'remote_repository_mutation':False}
        elif operation=='postrecreate':
            outcome={'health':health,'acceptance':postrecreate_acceptance(
                health,previous_repository_id=args.credential_previous_repo_id),
                     'schema':'devtool.m7.postrecreate-report.v1',
                     'remote_repository_mutation':False}
        else:
            raise CredentialDoctorError('CREDENTIAL_OPERATION_UNKNOWN')
        baseline_to_save=str(getattr(args,'credential_save_baseline','') or '')
        if baseline_to_save:
            if operation!='doctor':
                raise CredentialDoctorError('BASELINE_SAVE_DOCTOR_ONLY')
            write_baseline(Path(baseline_to_save),health)
            outcome=dict(health)
            outcome['baseline_saved_locally']=True
        print(json.dumps(outcome,sort_keys=True,indent=2,ensure_ascii=False))
        # A diagnostic which only guides repair is not itself a credential success.
        return 0 if health['status']=='VALID' and operation in ('doctor','repair') else 2
    except (CredentialDoctorError,OSError,ValueError,TypeError) as exc:
        # Only known local refusal identifiers; never raw upstream/user payloads.
        code=str(exc)
        if not code.isupper() or len(code)>100 or not code.replace('_','').isalnum():
            code='UNCLASSIFIED_LOCAL_REFUSAL'
        print(json.dumps({'schema':'devtool.m7.refusal.v1',
                          'state':'BLOCKED','reason':code,
                          'remote_repository_mutation':False},sort_keys=True))
        return 2
