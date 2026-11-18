"""C2: one bounded, read-only delivery operator projection for all frontends.

Session, transaction, PR, CI and Actions proofs remain owned by their native
journals/provider verifiers.  This module never runs a mutation, observes a
provider, changes a journal, or elevates an unverified result to a pass.
"""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import re

from .delivery_session import DeliverySessionError, status as delivery_status

SCHEMA = 'devtool.operator-status.v1'
_SHA = re.compile(r'[0-9a-f]{40}\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[0-9a-f]{32}\Z')


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object, *, limit: int = 400) -> str:
    if value is None:
        return ''
    raw = str(value)
    return ''.join(' ' if ord(c) < 32 or ord(c) == 127 else c for c in raw)[:limit]


def _safe_sha(value: object, *, size: int = 40) -> str | None:
    candidate = _text(value).lower()
    if len(candidate) != size or not ( _SHA if size == 40 else _SHA256).fullmatch(candidate):
        return None
    return candidate


def _ci_runs(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw[:60]:
        if not isinstance(item, Mapping):
            continue
        rid = item.get('id') or item.get('run_id')
        attempt = item.get('attempt') or item.get('run_attempt')
        if type(rid) is not int or rid <= 0 or type(attempt) is not int or attempt < 1:
            continue
        result.append({
            'workflow': _text(item.get('name') or item.get('workflow'), limit=160),
            'workflow_id': item.get('workflow_id') if type(item.get('workflow_id')) is int else None,
            'run_id': rid, 'attempt': attempt,
            'state': _text(item.get('status') or item.get('state'), limit=70) or 'UNKNOWN',
            'conclusion': _text(item.get('conclusion'), limit=70) or None,
            'url': _text(item.get('url'), limit=320) or None,
        })
    return result


def empty_status(repo_root: str | Path, *, reason: str = 'No source-bound delivery session selected') -> dict[str, Any]:
    """Never pretend an undiscovered or unreadable session is qualified."""
    return {'schema': SCHEMA, 'read_only': True, 'authority': 'native-receipts',
            'state':'UNVERIFIED', 'target':{'repo_root':str(repo_root), 'repository':None,
                                          'branch':None,'head_sha':None,'source_binding':'unavailable'},
            'package':{'sha256':None,'path':None,'verified':'unknown'},
            'transaction':{'state':'UNKNOWN','id':None,'commit':None,'validation':None},
            'session':{'id':None,'stage':'NOT_FOUND','resumable':False,'pending_intents':[]},
            'pr':{'number':None,'url':None,'state':'NOT_OBSERVED'},
            'ci':{'required':{'state':'NOT_OBSERVED','runs':[],'passed':False},
                  'regression':{'state':'NOT_OBSERVED','run_id':None,'attempt':None,'passed':False},
                  'overall_acceptance':'BLOCKED_OR_PENDING',
                  'release_qualification':'NOT_PERFORMED', 'device_qualification':'NOT_PERFORMED'},
            'events':[], 'failures':[],
            'blockers':[{'code':'SESSION_UNAVAILABLE','reason':_text(reason)}],
            'next_safe_action':'Inspect the native session ID or source state; do not apply an overlay to resolve a status query',
            'remote_mutation_performed':False}


def project_delivery_status(record: Mapping[str, Any], *,
                            qualification: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Convert already-verified native observations into a versioned UI view.

    A saved CI record alone is not a live provider qualification; the optional
    B3 qualification carries that authority if it is bound to this exact head.
    """
    if not isinstance(record, Mapping):
        raise TypeError('Native session status must be a mapping')
    source = _safe_sha(record.get('head_sha'))
    sid = _text(record.get('id'), limit=40)
    if not source or not _ID.fullmatch(sid):
        raise ValueError('Invalid native session identity')

    native = _mapping(record.get('native_apply'))
    overlay = _mapping(record.get('overlay'))
    pr = _mapping(record.get('pr'))
    ci = _mapping(record.get('ci'))
    head_repo = str(record.get('head_repository') or record.get('repository') or '')
    base_repo = str(record.get('base_repository') or record.get('repository') or '')
    number = pr.get('number')
    pr_valid = bool(type(number) is int and number > 0
                    and pr.get('head_sha') == source
                    and str(pr.get('head_repository') or head_repo).casefold() == head_repo.casefold()
                    and str(pr.get('base_repository') or base_repo).casefold() == base_repo.casefold()
                    and pr.get('url') == f'https://github.com/{base_repo}/pull/{number}'
                    and pr.get('head',record.get('head')) == record.get('head')
                    and pr.get('base',record.get('base')) == record.get('base')
                    and (not record.get('base_sha') or pr.get('base_sha') == record.get('base_sha')))
    proof = _mapping(qualification)
    proof_bound = bool(proof.get('schema') == 'devtool.delivery-qualification.v1'
                       and proof.get('session_id') == sid
                       and proof.get('head_sha') == source
                       and proof.get('remote_mutation_performed') is False
                       and proof.get('read_only') is True)
    required_proof = _mapping(proof.get('required_delivery_ci')) if proof_bound else {}
    regression_proof = _mapping(proof.get('repository_regression')) if proof_bound else {}
    safe_source = (pr_valid and record.get('source_binding') == 'matching'
                   and record.get('resumable') is True)
    qualification_pass = bool(safe_source and proof_bound and required_proof.get('state')=='PASSED'
                              and required_proof.get('passed') is True)
    regression_pass = bool(safe_source and proof_bound and regression_proof.get('state')=='PASSED'
                           and regression_proof.get('passed') is True
                           and regression_proof.get('source_sha') == source
                           and regression_proof.get('shards_passed') == 24
                           and regression_proof.get('summary_passed') is True
                           and type(regression_proof.get('run_id')) is int
                           and regression_proof['run_id'] > 0
                           and type(regression_proof.get('attempt')) is int
                           and regression_proof['attempt'] > 0
                           and regression_proof.get('request_id')
                           and proof.get('pre_merge_qualification') == 'PASSED'
                           and regression_proof.get('jobs_passed') == 25
                           and regression_proof.get('artifacts_verified') == 25
                           and regression_proof.get('attribution_proven') is True
                           and regression_proof.get('workflow_path') == '.github/workflows/devtool-full-repository-pytest.yml')
    runs = _ci_runs(ci.get('runs'))
    stream = _mapping(record.get('event_stream'))
    events = [dict(item) for item in (stream.get('events') or [])[-30:]
              if isinstance(item,Mapping) and item.get('source_sha') == source] if (
              stream.get('schema') == 'devtool.delivery-event-stream.v1' and
              stream.get('source_sha') == source) else []
    failures = [
        {'reason':_text(f.get('classification') or f.get('message'),limit=160),
         'run_id':f.get('run_id'), 'attempt':f.get('attempt')}
        for f in (_mapping(ci.get('diagnosis')).get('failures') or [])[:20]
        if isinstance(f,Mapping)
    ]
    recorded_ci = bool(ci.get('state') == 'REQUIRED_CHECKS_PASSED'
                       and ci.get('checks_qualified') is True
                       and ci.get('source_sha') == source)
    if qualification_pass:
        required_state = 'PASSED'
    elif proof_bound:
        required_state = _text(required_proof.get('state')) or 'NOT_OBSERVED'
    elif recorded_ci:
        required_state = 'RECORDED_NEEDS_LIVE_RECHECK'
    else:
        required_state = _text(ci.get('state'), limit=100) or 'NOT_OBSERVED'

    regression_state = ('PASSED' if regression_pass else
                        _text(regression_proof.get('state'), limit=100)
                        if proof_bound else 'NOT_ATTESTED')
    if regression_state == 'PASSED' and not regression_pass:
        regression_state = 'UNVERIFIED'
    intent_rows = record.get('intents')
    pending_intents = [
        {'operation':_text(item.get('operation'),limit=60),
         'state':_text(item.get('state'),limit=60),
         'id':_text(item.get('id'),limit=40)}
        for item in intent_rows[:60] if isinstance(item, Mapping) and item.get('state') != 'confirmed'
    ] if isinstance(intent_rows,list) else []

    source_bound = record.get('source_binding') == 'matching'
    committed = bool(native.get('state') == 'COMMITTED' and native.get('commit') == source
                     and native.get('transaction_status') == 'completed'
                     and native.get('validation_status') == 'ok')
    pkg_sha = _safe_sha(overlay.get('sha256'),size=64)
    native_sha = _safe_sha(native.get('artifact_sha256'),size=64)
    package_verified = bool(pkg_sha and native_sha == pkg_sha and committed)
    blockers: list[dict[str,str]] = []
    def block(code: str, reason: str) -> None:
        blockers.append({'code':code,'reason':reason})
    if not source_bound:
        block('SOURCE_BINDING_UNVERIFIED', 'Native worktree, branch or commit differs from the recorded session')
    if pending_intents or record.get('resumable') is not True:
        block('MUTATION_RECONCILIATION_REQUIRED','An unresolved native intent or non-resumable session cannot authorize another mutation')
    if not committed:
        block('NATIVE_COMMIT_UNVERIFIED','No matching completed native transaction and validation receipt')
    if committed and not package_verified:
        block('PACKAGE_RECEIPT_UNVERIFIED','Native package SHA-256 does not match the recorded overlay')
    if not record.get('remote_verified'):
        block('REMOTE_HEAD_UNVERIFIED','Remote branch has not been independently confirmed in the native delivery session')
    if not pr_valid:
        block('PR_IDENTITY_UNVERIFIED','Matching upstream/base/fork PR evidence is not recorded')
    if not qualification_pass:
        block('REQUIRED_CI_NOT_LIVE_QUALIFIED','Required CI needs exact-attempt live qualification on the current source')
    if not regression_pass:
        block('REGRESSION_NOT_LIVE_QUALIFIED','24/24 shards, summary and artifacts need source-correlated B3 qualification')
    if qualification_pass and regression_pass:
        block('POST_MERGE_PENDING','Pre-merge proof cannot attest a different main or merge commit')

    next_action = {
        'SOURCE_BINDING_UNVERIFIED':'Inspect the native source/branch identity; never reapply to recover status',
        'MUTATION_RECONCILIATION_REQUIRED':'Use delivery session resume/status to reconcile unresolved intents before any approved mutation',
        'NATIVE_COMMIT_UNVERIFIED':'Inspect native overlay history/transaction and validation receipts before proceeding',
        'PACKAGE_RECEIPT_UNVERIFIED':'Compare the recorded package and native apply SHA-256; refuse unverified inventory',
        'REMOTE_HEAD_UNVERIFIED':'Read-only verify remote HEAD; use native approved delivery session push only after preflight',
        'PR_IDENTITY_UNVERIFIED':'Use native delivery session pr to reconcile or explicitly approve the correct PR',
        'REQUIRED_CI_NOT_LIVE_QUALIFIED':'Read-only verify required PR checks using the native B3 qualification authority',
        'REGRESSION_NOT_LIVE_QUALIFIED':'Use an existing correlated Actions request and native B3 qualification; do not dispatch automatically',
        'POST_MERGE_PENDING':'After an explicitly approved merge, qualify the actual main HEAD with a separate correlated regression run',
    }
    first = blockers[0]['code'] if blockers else None
    return {
        'schema':SCHEMA,'read_only':True,'authority':'native-receipts',
        'state': 'BLOCKED' if first and first not in ('POST_MERGE_PENDING',) else
                 'PRE_MERGE_QUALIFIED_PENDING_MAIN' if first else 'UNVERIFIED',
        'target':{'repo_root':_text(record.get('repo_root'),limit=1024),
                  'repository':_text(record.get('repository'),limit=140),
                  'head_repository':_text(record.get('head_repository'),limit=140),
                  'base_repository':_text(record.get('base_repository'),limit=140),
                  'base':_text(record.get('base'),limit=160),
                  'branch':_text(record.get('head'),limit=160),
                  'head_sha':source,
                  'source_binding':_text(record.get('source_binding')) or 'unavailable'},
        'package':{'path':_text(overlay.get('path'),limit=1024) or None,
                   'sha256':pkg_sha,'verified': 'recorded-matching' if package_verified else 'unknown'},
        'transaction':{'state':_text(native.get('state')) or 'UNKNOWN',
                       'id':_text(native.get('transaction_id')) or None,
                       'history_id':_text(native.get('history_id')) or None,
                       'operation_id':_text(native.get('operation_id')) or None,
                       'commit':_safe_sha(native.get('commit')),
                       'validation':_text(native.get('validation_status')) or None,
                       'status':_text(native.get('transaction_status')) or None},
        'session':{'id':sid,'stage':_text(record.get('stage')),
                   'resumable':record.get('resumable') is True,
                   'pending_intents':pending_intents},
        'pr':{'number':pr.get('number') if pr_valid else None,
              'url':_text(pr.get('url'),limit=320) if pr_valid else None,
              'state':'RECORDED_IDENTITY' if pr_valid else 'NOT_OBSERVED',
              'base_sha':_safe_sha(pr.get('base_sha'))},
        'ci':{
            'required':{'state':required_state,'runs':runs,'passed':qualification_pass,
                        'recorded_qualified':recorded_ci,
                        'policy_sha256':_safe_sha(ci.get('policy_sha256'),size=64)},
            'regression':{'state':regression_state, 'passed':regression_pass,
                          'run_id':regression_proof.get('run_id') if proof_bound else None,
                          'attempt':regression_proof.get('attempt') if proof_bound else None,
                          'shards_passed':24 if regression_pass else None,
                          'summary_passed':regression_pass,
                          'request_id':_text(regression_proof.get('request_id')) or None if proof_bound else None,
                          'url':_text(regression_proof.get('url')) or None if proof_bound else None},
            'overall_acceptance':'PENDING_POST_MERGE_MAIN_HEAD' if qualification_pass and regression_pass else 'BLOCKED_OR_PENDING',
            'release_qualification':'NOT_PERFORMED','device_qualification':'NOT_PERFORMED',
        },
        'events':events, 'failures':failures,
        'blockers':blockers,
        'next_safe_action':next_action.get(first,'Inspect the current native receipts and source-bound qualification'),
        'remote_mutation_performed':False,
    }


def render_status_text(projection: Mapping[str, Any], *, compact: bool = False) -> str:
    """One plain/GUI presentation contract, suitable for non-TTY or NO_COLOR."""
    target = _mapping(projection.get('target'))
    package = _mapping(projection.get('package'))
    tx = _mapping(projection.get('transaction'))
    session = _mapping(projection.get('session'))
    pr = _mapping(projection.get('pr'))
    ci = _mapping(projection.get('ci'))
    required = _mapping(ci.get('required'))
    regression = _mapping(ci.get('regression'))
    blockers = projection.get('blockers') or []
    lines = [
        f"Devtool operator status: {_text(projection.get('state'))}",
        f"Source: {_text(target.get('repository'))} {_text(target.get('branch'))} {_text(target.get('head_sha'))} ({_text(target.get('source_binding'))})",
        f"Package: {_text(package.get('sha256')) or 'unavailable'} ({_text(package.get('verified'))})",
        f"Transaction: {_text(tx.get('id')) or 'none'} {_text(tx.get('state'))} validation={_text(tx.get('validation')) or 'unknown'}",
        f"Session: {_text(session.get('id')) or 'none'} {_text(session.get('stage'))}",
        f"PR: {_text(pr.get('url')) or 'unverified'}",
        f"Required CI: {_text(required.get('state'))} ({len(required.get('runs') or [])} recorded run/attempt identities)",
        f"24-shard regression: {_text(regression.get('state'))} run={_text(regression.get('run_id')) or 'none'} attempt={_text(regression.get('attempt')) or 'none'}",
        f"Overall: {_text(ci.get('overall_acceptance'))}; release/device: NOT_PERFORMED",
    ]
    if compact:
        lines.extend(f"Blocker: {_text(item.get('code'))} — {_text(item.get('reason'),limit=200)}"
                     for item in blockers[:3] if isinstance(item,Mapping))
        if len(blockers)>3:
            lines.append(f"Blockers: {len(blockers)} total (full detail in native JSON)")
    else:
        lines.extend(f"Blocker: {_text(item.get('code'))} — {_text(item.get('reason'),limit=200)}"
                     for item in blockers if isinstance(item,Mapping))
        lines.extend(f"  CI run: {row['workflow']} #{row['run_id']} attempt {row['attempt']} {row['state']}/{row['conclusion'] or 'pending'}"
                     for row in (required.get('runs') or [])[:10])
    if not compact:
        lines.extend(f"  Failure: {_text(x.get('reason'))} run={_text(x.get('run_id'))} attempt={_text(x.get('attempt'))}"
                     for x in (projection.get('failures') or [])[:10])
        lines.append(f"Events: {len(projection.get('events') or [])} bounded native receipt rows")
    lines.append(f"Next: {_text(projection.get('next_safe_action'),limit=400)}")
    return '\n'.join(lines)


def latest_for_repo(repo_root: str | Path, *, state_root: Path | None = None,
                    max_candidates: int = 80) -> dict[str, Any]:
    """Bounded read-only discovery for the explicit Control Center dashboard.

    Never creates state or writes a session. A corrupt/unavailable latest
    candidate cannot become a success signal in the UI.
    """
    root = Path(repo_root).expanduser().resolve(strict=False)
    from .delivery_session import _state_root, _directory
    folder = _state_root(state_root)
    if not folder.exists():
        return empty_status(root)
    try:
        _directory(folder, create=False)
        rows = sorted((p for p in folder.glob('*.json') if p.is_file() and not p.is_symlink()
                      and _ID.fullmatch(p.stem)),
                      key=lambda p:p.stat().st_mtime_ns, reverse=True)
        for path in rows[:max(1,min(int(max_candidates),200))]:
            try:
                record = delivery_status(path.stem,state_root=state_root)
            except (DeliverySessionError, OSError, ValueError) as exc:
                # This read-only dashboard must not ignore potentially corrupt
                # native receipts and present an older one as current green.
                return empty_status(root,reason=f'Native session receipt unreadable ({type(exc).__name__})')
            if Path(str(record.get('repo_root',''))).resolve(strict=False)==root:
                return project_delivery_status(record)
    except (DeliverySessionError, OSError, ValueError) as exc:
        return empty_status(root,reason=f'Native session discovery unavailable ({type(exc).__name__})')
    return empty_status(root)
