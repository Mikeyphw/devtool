"""Post-recreation, source-bound recovery of a conflicting default branch.

No force-push, hard reset, journal edits, release, merge, or external CI dispatch.
This module is deliberately separate from the original destructive replay path.
"""
from __future__ import annotations

from pathlib import Path
import urllib.parse


def _core():
    from . import engine as cli
    return cli


def _name_ref(branch: str) -> str:
    return 'refs/heads/' + branch


def _precheck(session: Path, state: dict) -> dict:
    rr = _core()
    rr.require(state.get('stage') == 'done-with-action-required',
               'post-recreation recovery requires a completed, action-required session')
    rr.require(state.get('approved') is True and state.get('actions_suspended') is True,
               'original transaction approval or Actions suspension checkpoint missing')
    rr.require(state.get('topology') == 'fork' and state.get('created_new_id') is not None,
               'default branch reconciliation is only available for verified replacement forks')
    rr.verify_pair(session, state)
    rr.require_independent_backup(session, state)
    actual = rr.get_new_repo(state)
    backup = session / 'primary'
    saved_repo = rr.read_json(backup / 'repo.json')
    branch = saved_repo.get('default_branch')
    rr.require(isinstance(branch, str) and branch and '/' not in branch and
               branch not in ('.', '..'),
               'original default branch has an unsupported name')
    original_ref = _name_ref(branch)
    old_sha = rr.read_json(backup / 'source-refs.json').get(original_ref)
    seed_sha = state.get('fork_seed_refs', {}).get(original_ref)
    rr.require(rr.re.fullmatch('[0-9a-f]{40}', old_sha or '') is not None,
               'original default branch SHA missing from verified backup')
    rr.require(rr.re.fullmatch('[0-9a-f]{40}', seed_sha or '') is not None and old_sha != seed_sha,
               'upstream default branch does not have a verified source conflict')
    expected, archived = rr.expected_ref_mapping(state, backup, {})
    rr.require(archived is not None and state.get('master_archive') == archived and
               expected.get(archived) == old_sha,
               'original default archive provenance does not match journal')
    preserved_branch = (f'archive/upstream-pre-reconcile-{state["created_new_id"]}-'
                        f'{seed_sha[:12]}/{branch}')
    rr.require(preserved_branch != branch and
               _name_ref(preserved_branch) not in rr.read_json(backup / 'source-refs.json') and
               _name_ref(preserved_branch) not in state.get('fork_seed_refs', {}),
               'preservation branch name collides with backed-up source')
    plan = {
        'original_branch': branch, 'original_ref': original_ref,
        'original_sha': old_sha, 'original_archive': archived,
        'seed_sha': seed_sha, 'preserved_branch': preserved_branch,
        'preserved_ref': _name_ref(preserved_branch),
        'replacement_id': int(state['created_new_id']),
    }
    recorded = state.get('default_reconciliation')
    if recorded is not None:
        rr.require(all(recorded.get(k) == v for k, v in plan.items()),
                   'journaled default-branch reconciliation plan differs from immutable backup')
    rr.require(actual.get('id') == state['created_new_id'],
               'GitHub replacement identity changed')
    rr.require(rr.api(f'repos/{state["repo"]}/actions/permissions').get('enabled') is False,
               'Actions must remain disabled until the original default branch is restored')
    return plan


def _protection_preflight(repo: str, branch: str) -> None:
    rr = _core()
    q = urllib.parse.quote(branch, safe='')
    actual = rr.api(f'repos/{repo}/branches/{q}')
    rr.require(actual.get('name') == branch and actual.get('protected') is False,
               'current default branch is missing or protected; no branch rename permitted')
    rules = rr.api(f'repos/{repo}/rulesets?includes_parents=true')
    rr.require(isinstance(rules, list) and not rules,
               'branch rulesets exist or cannot be audited; manual review required')
    open_pulls = rr.collect(f'repos/{repo}/pulls?state=open', key=None)
    rr.require(all((p.get('base') or {}).get('ref') != branch for p in open_pulls),
               'open PR targets current default; renaming could change its base')


def _remote_stage(state: dict, plan: dict):
    rr = _core()
    refs = rr.live_refs(state['repo'])
    origin = refs.get(plan['original_ref'])
    preserved = refs.get(plan['preserved_ref'])
    archived = refs.get(plan['original_archive'])
    rr.require(archived == plan['original_sha'],
               'original archived default branch changed; refuse recovery')
    rr.require(origin in (None, plan['seed_sha'], plan['original_sha']),
               'current default branch changed to an unrecognized commit')
    rr.require(preserved in (None, plan['seed_sha']),
               'upstream preservation ref has unexpected content')
    rr.require(not (origin == plan['seed_sha'] and preserved is not None),
               'both upstream default names present; ambiguous partial rename')
    rr.require(not (origin == plan['original_sha'] and preserved is None),
               'original master present without a preserved upstream commit')
    return refs


def reconcile_default(session: Path, *, ack_repo: str | None = None):
    """Resume one journaled rename/create/default-select transaction; dry-run by default."""
    rr = _core()
    with rr.locked(session):
        state = rr.load_state(session)
        plan = _precheck(session, state)
        repo = state['repo']
        refs = _remote_stage(state, plan)
        recorded = state.get('default_reconciliation')
        current = rr.get_new_repo(state)
        if recorded is None:
            rr.require(current.get('default_branch') == plan['original_branch'],
                       'default branch changed since snapshot; refuse new reconciliation')
            rr.require(refs.get(plan['original_ref']) == plan['seed_sha'] and
                       refs.get(plan['preserved_ref']) is None,
                       'branch tips do not match the original fork-seed checkpoint')
            expected, _ = rr.expected_ref_mapping(state, session/'primary', refs)
            rr.require(all(refs.get(k) == v for k,v in expected.items()),
                       'other restored references drifted; refuse default reconciliation')
            rr.require(set(refs) <= set(expected) | set(state.get('fork_seed_refs', {})),
                       'unreviewed Git references appeared after original restoration')
            _protection_preflight(repo, plan['original_branch'])
        if ack_repo != repo:
            rr.log('READ-ONLY DEFAULT BRANCH RECONCILIATION PLAN',style='bold yellow')
            rr.log(f'{repo}: preserve inherited {plan["seed_sha"]} as {plan["preserved_branch"]}')
            rr.log(f'Restore {plan["original_branch"]} to {plan["original_sha"]} and make it default')
            rr.log('No mutation made. To authorize: --ack-reconcile-default '+repo)
            return plan
        if recorded is None:
            recorded = dict(plan, phase='intent', started_at=rr.now())
            state['default_reconciliation'] = recorded
            rr.save_state(session,state)
        # Any error or process interruption is resumed from server-observed refs,
        # never from an assumed successful request or a regenerated session.
        if refs.get(plan['original_ref']) == plan['seed_sha']:
            rr.require(refs.get(plan['preserved_ref']) is None,
                       'preservation ref collision before rename')
            rr.require(current.get('default_branch') == plan['original_branch'],
                       'current default changed during reconciliation')
            _protection_preflight(repo,plan['original_branch'])
            rr.api(f'repos/{repo}/branches/{urllib.parse.quote(plan["original_branch"],safe="")}/rename',
                   method='POST',payload={'new_name':plan['preserved_branch']})
        # GitHub notes renames can propagate asynchronously. Never assume a
        # successful POST implies the old name has already disappeared.
        for _ in range(15):
            refs = _remote_stage(state,plan)
            if refs.get(plan['original_ref']) is None and refs.get(plan['preserved_ref']) == plan['seed_sha']:
                break
            if refs.get(plan['original_ref']) == plan['original_sha'] and refs.get(plan['preserved_ref']) == plan['seed_sha']:
                break
            rr.time.sleep(2)
        else:
            raise rr.Refused('branch rename not fully visible; journal retained for safe retry')
        recorded['phase'] = 'renamed'
        rr.save_state(session,state)
        if refs.get(plan['original_ref']) is None:
            rr.api(f'repos/{repo}/git/refs', method='POST', payload={
                'ref':plan['original_ref'],'sha':plan['original_sha']})
        refs = _remote_stage(state,plan)
        rr.require(refs.get(plan['original_ref']) == plan['original_sha'] and
                   refs.get(plan['preserved_ref']) == plan['seed_sha'],
                   'new default ref did not resolve to the exact original SHA')
        recorded['phase'] = 'original-ref-restored'
        rr.save_state(session,state)
        current = rr.get_new_repo(state)
        rr.require(current.get('default_branch') in (plan['original_branch'],plan['preserved_branch']),
                   'default branch changed unexpectedly after rename')
        if current.get('default_branch') != plan['original_branch']:
            rr.api(f'repos/{repo}',method='PATCH',payload={'default_branch':plan['original_branch']})
        rr.require(rr.get_new_repo(state).get('default_branch') == plan['original_branch'],
                   'GitHub did not confirm original default branch selection')
        refs = _remote_stage(state,plan)
        rr.require(refs.get(plan['original_ref']) == plan['original_sha'] and
                   refs.get(plan['preserved_ref']) == plan['seed_sha'] and
                   refs.get(plan['original_archive']) == plan['original_sha'],
                   'post-reconciliation refs failed identity verification')
        rr.require(rr.api(f'repos/{repo}/actions/permissions').get('enabled') is False,
                   'Actions unexpectedly enabled before reconciliation was completed')
        recorded['phase'] = 'verified'
        recorded['verified_at'] = rr.now()
        rr.save_state(session,state)
        report = rr.read_json(session/'report.json')
        report['default_branch_reconciliation'] = recorded.copy()
        report['conflicting_original_default_archived_at'] = plan['original_archive']
        report['actions_left_disabled_for_manual_recovery'] = True
        report['fully_operationally_verified'] = False
        rr.write_json(session/'report.json',report)
        rr.log('DEFAULT BRANCH RECONCILED; ACTIONS REMAIN DISABLED',style='green')
        return plan


def _exact_reconciled_refs(session, state):
    rr = _core()
    plan = state.get('default_reconciliation')
    if not state.get('master_archive'):
        # Standalone repositories and forks without an inherited default-ref
        # conflict never needed a rename transaction. Still prove source identity.
        saved = rr.read_json(session/'primary'/'repo.json')
        refs = rr.live_refs(state['repo'])
        branch = saved.get('default_branch')
        rr.require(rr.get_new_repo(state).get('default_branch') == branch,
                   'original default branch is not selected')
        expected, _ = rr.expected_ref_mapping(state,session/'primary',refs)
        rr.require(all(refs.get(k)==v for k,v in expected.items()),
                   'restored Git refs drifted; refuse Actions activation')
        rr.require(refs.get('refs/heads/'+branch) ==
                   rr.read_json(session/'primary'/'source-refs.json').get('refs/heads/'+branch),
                   'original default SHA is not restored')
        return refs
    rr.require(isinstance(plan,dict) and plan.get('phase')=='verified',
               'original default-branch recovery has not been verified')
    rr.require(rr.get_new_repo(state).get('default_branch') == plan['original_branch'],
               'default branch no longer matches reconciliation')
    refs = _remote_stage(state,plan)
    rr.require(refs.get(plan['original_ref'])==plan['original_sha'] and
               refs.get(plan['preserved_ref'])==plan['seed_sha'],
               'default branch or upstream preservation ref drifted')
    expected,_=rr.expected_ref_mapping(state,session/'primary',refs)
    rr.require(all(refs.get(k)==v for k,v in expected.items()),
               'restored reference inventory drifted after reconciliation')
    return refs


def activate_actions(session: Path, *, ack_repo: str | None = None):
    """Explicitly enable verified original policy; leave historical gaps visible."""
    rr = _core()
    with rr.locked(session):
        state = rr.load_state(session)
        rr.require(state.get('stage') in ('done-with-action-required','done'),
                   'Actions activation requires a completed post-recreation session')
        rr.verify_pair(session,state)
        rr.require_independent_backup(session,state)
        _exact_reconciled_refs(session,state)
        rr.verify_recovery_configuration(session,state,require_all_secrets=True)
        repo=state['repo']
        saved=rr.read_json(session/'primary'/'actions'/'permissions.json')
        rr.require(saved.get('enabled') is True,
                   'Actions were not originally enabled; refuse activation')
        rr.require(not state.get('missing_secret_names'),
                   'missing secret values require explicit recovery before Actions')
        endpoint=f'repos/{repo}/actions/permissions'
        policy=rr.api(endpoint)
        desired={k:saved[k] for k in ('allowed_actions','sha_pinning_required') if k in saved}
        rr.require(policy.get('enabled') is False or
                   (all(policy.get(k)==v for k,v in desired.items()) and
                    state.get('post_recovery_actions_activation') in ('intent','enabled-unqualified')),
                   'Actions enabled outside journal or permission policy differs')
        if ack_repo != repo:
            rr.log('READ-ONLY ACTIONS ACTIVATION PLAN',style='bold yellow')
            rr.log(f'{repo}: enable original policy {desired}')
            rr.log('Historical workflow registrations, run IDs and CI remain unqualified')
            rr.log('No mutation made. To authorize: --ack-enable-actions '+repo)
            return
        state['post_recovery_actions_activation']='intent'
        rr.save_state(session,state)
        rr.restore_actions_enabled_after_safety(session,state,unresolved=False)
        actual=rr.api(endpoint)
        rr.require(actual.get('enabled') is True and
                   all(actual.get(k)==v for k,v in desired.items()),
                   'Actions enabled but original policy was not verified')
        wf=rr.collect(f'repos/{repo}/actions/workflows',key='workflows')
        present={w['path']:w for w in wf}
        original=rr.read_json(session/'primary'/'actions'/'workflows.json')
        missing=[w['path'] for w in original if w.get('state')!='deleted' and w['path'] not in present]
        state['unregistered_workflows']={p:state.get('unregistered_workflows',{}).get(p,{
            'reason':'still-absent-after-actions-activation'}) for p in missing}
        state['post_recovery_actions_activation']='enabled-unqualified'
        rr.save_state(session,state)
        report=rr.read_json(session/'report.json')
        report.update({
            'actions_left_disabled_for_manual_recovery':False,
            'actions_policy_verified_after_activation':True,
            'actions_workflows_registered':sorted(present),
            'actions_workflows_not_registered':state['unregistered_workflows'],
            'actions_workflow_registration_complete':not bool(missing),
            'actions_activation_at':rr.now(),
            'fully_operationally_verified':False,
            'recovery_classification':'RESTORED_ACTIONS_ENABLED_CI_NOT_QUALIFIED',
            'ci_execution':'not performed; hosted checks, exact HEAD and workflow registration require review'
        })
        rr.write_json(session/'report.json',report)
        rr.log('ACTIONS ENABLED WITH ORIGINAL POLICY; CI NOT QUALIFIED',style='yellow')
        if missing:rr.log(f'{len(missing)} archived workflow registrations remain missing',style='yellow')


def recover_operational(session: Path, *, ack_reconcile: str | None = None,
                        ack_actions: str | None = None, pr: int | None = None,
                        required: tuple[str, ...] = (),
                        ack_retrigger: str | None = None, watch: bool = False,
                        timeout_seconds: int = 1800):
    """One resumable operator-facing recovery command; each mutation separately approved.

    Reuses the original native RepoRebuild journal.  Does not automatically
    accept historical identity losses or retrigger PRs.  Its default is a plan.
    """
    rr = _core()
    state = rr.load_state(session)
    rr.require(state.get('stage') in ('done','done-with-action-required','done-manual-attested'),
               'finish the original restoration transaction first')
    repo = state['repo']
    rr.require(not ack_reconcile or ack_reconcile == repo,
               'wrong explicit reconciliation repository acknowledgement')
    rr.require(not ack_actions or ack_actions == repo,
               'wrong explicit Actions repository acknowledgement')
    rr.require(pr is not None or (not required and ack_retrigger is None and not watch),
               'required CI selections, PR retrigger and watch need --pr NUMBER')
    if state.get('master_archive') and not (
            state.get('default_reconciliation') or {}).get('phase') == 'verified':
        reconcile_default(session, ack_repo=ack_reconcile)
        if not ack_reconcile:
            rr.log('STOP: approved default-branch reconciliation required before Actions',style='yellow')
            return None
    else:
        with rr.locked(session):
            state = rr.load_state(session)
            rr.verify_pair(session,state)
            rr.require_independent_backup(session,state)
            rr.get_new_repo(state)
            _exact_reconciled_refs(session,state)
    state = rr.load_state(session)
    saved = rr.read_json(session/'primary'/'actions'/'permissions.json')
    current = rr.api(f'repos/{repo}/actions/permissions')
    desired = {k:saved[k] for k in ('enabled','allowed_actions','sha_pinning_required')
               if k in saved}
    if current.get('enabled') is True:
        rr.require(all(current.get(k)==v for k,v in desired.items()),
                   'Actions are enabled with an unverified policy; refuse hosted qualification')
    elif saved.get('enabled') is False:
        rr.require(not pr, 'Actions were originally disabled; PR CI requires operator review')
        rr.log('Original repository had disabled Actions; no automatic activation',style='yellow')
    else:
        activate_actions(session,ack_repo=ack_actions)
        if not ack_actions:
            rr.log('STOP: explicit Actions activation required before hosted CI',style='yellow')
            return None
    from . import qualification
    # Discovery is observational and must never be confused with hosted CI.
    qualification.audit_workflows(session,record=True)
    if pr is not None:
        return qualification.qualify_pr(session,number=pr,required=required,
                                        ack_retrigger=ack_retrigger,watch=watch,
                                        timeout_seconds=timeout_seconds)
    rr.log('Source and Actions recovery inspected; no PR CI was qualified',style='yellow')
    return None
