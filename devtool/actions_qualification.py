"""G8 read-only Actions qualification and historical obligation reconciliation.

A source hash, pytest pass, and user-reported observation are different kinds of
proof. This module deliberately never changes the frozen ledger or promotes an
unsigned/locally signed artifact into a protected release qualification.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

from .github_actions import GitHubActionsError

_PLAN = 'docs/actions/ACTIONS_G08_QUALIFICATION_MATRIX.json'
_PLAN_SHA256 = '12ca3595ffc59007b1b6a20bfd429a65fae2a40445f159094ff34b6a9b9d65bf'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VALID_STATES = {'LIVE_VERIFIED', 'SOURCE_TESTED', 'PARTIAL', 'OPEN', 'EXCLUDED'}


def _unique(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise ValueError('duplicate JSON key')
        out[key] = value
    return out


def _bounded_file(root: Path, relative: str, *, max_size: int) -> bytes:
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise GitHubActionsError('G8 source inventory has an unsafe path')
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ('', '.', '..') for part in relative.split('/')) or len(relative) > 350:
        raise GitHubActionsError('G8 source inventory has a noncanonical path')
    target = root
    for part in pure.parts:
        target = target / part
        if target.is_symlink():
            raise GitHubActionsError('G8 source inventory contains a symlink')
    if not target.is_file():
        raise GitHubActionsError('G8 source inventory is missing a required file')
    try:
        size = target.stat().st_size
        if size > max_size:
            raise GitHubActionsError('G8 source inventory file exceeds size bound')
        return target.read_bytes()
    except OSError:
        raise GitHubActionsError('G8 source inventory cannot be read') from None


def _document(root: Path, name: str) -> dict[str, Any]:
    try:
        result = json.loads(_bounded_file(root, name, max_size=1024*1024), object_pairs_hook=_unique)
    except (ValueError, UnicodeDecodeError):
        raise GitHubActionsError('G8 qualification JSON is malformed or ambiguous') from None
    if not isinstance(result, dict):
        raise GitHubActionsError('G8 qualification document must be a JSON object')
    return result


def _record_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# Explicit G1 QW02 amendment: safely reject mutable-ref mismatch *before* POST.
# The immutable G8 matrix/88-row ledger retain their historical G1 source pin.
_G1_QW02_AMENDMENT_ID = 'G1_CI24_DISPATCH_SOURCE_PREFLIGHT_QW02_20261016'
_G1_QW02_ANCHORS = {'devtool/actions_dispatch.py': '3bbcfb7155e6f69bb5528bbf41d172793b4e0040595a023d3dd483eb36d91c4c'}
_G1_QW02_EVIDENCE = {'tests/test_actions_00_01_github_provider.py': 'dde19fa9c0a6166c8915dfe3e02ded2308db718f9272d4b53839038d67f6ed45'}


def _g1_qw02_amendment_valid(root: Path) -> bool:
    for name, expected in {**_G1_QW02_ANCHORS, **_G1_QW02_EVIDENCE}.items():
        try:
            if _record_hash(_bounded_file(root, name, max_size=2 * 1024 * 1024)) != expected:
                return False
        except GitHubActionsError:
            return False
    return True


# Explicit post-G8 G4 amendment: original G8 matrix and 88-row historical ledger
# remain byte-for-byte immutable. Both changed anchors AND both new evidence
# paths must match before a hotfixed G4 source can be considered consistent.
# This is source implementation evidence, never a live-release qualification.
_G4_UNSIGNED_JAR_HOTFIX_ID = 'G4_REAL_AAB_JARSIGNER_UNSIGNED_STATUS_20261013'
_G4_UNSIGNED_JAR_HOTFIX_ANCHORS = {'devtool/actions_aab.py': 'ba9699959d93f1cbf281db0555e822af758dd23856bf7e683c9a9e6609bb8c37', 'tests/test_actions_g04_android_aab_candidate.py': '608732d54d1d63bc04e7d8fe36e1330b50771e7cff33701aa6c11da10337df73'}
_G4_UNSIGNED_JAR_HOTFIX_EVIDENCE = {'docs/actions/ACTIONS_G04_JARSIGNER_UNSIGNED_VERDICT_HOTFIX.md': 'a2839a24f2a1a3bb14a24d0f16b17072b5ee7b29b55b2419c58e6d824e4bb203', 'tests/test_actions_g04_unsigned_jarsigner_truth.py': 'd714c42bcc8d68952d8f4c253e7242b9c1a3bb0d0a79a88c66daaa43695cb60a'}


def _g4_unsigned_jar_amendment_valid(root: Path) -> bool:
    for name, expected in {**_G4_UNSIGNED_JAR_HOTFIX_ANCHORS,
                           **_G4_UNSIGNED_JAR_HOTFIX_EVIDENCE}.items():
        try:
            if _record_hash(_bounded_file(root, name, max_size=2 * 1024 * 1024)) != expected:
                return False
        except GitHubActionsError:
            return False
    return True


# Explicit post-G8 G3 compact Textual amendment. This is an exact-byte
# authentication of the accepted narrow-terminal source and its independent
# supporting records. The frozen matrix/ledger remain completely immutable,
# and this verifier does NOT independently qualify live device evidence.
_G3_TEXTUAL_HOTFIX_ID = 'G3_TERMUX_COMPACT_TEXTUAL_VISIBILITY_20261014'
_G3_TEXTUAL_HOTFIX_ANCHORS = {
    'devtool/actions_pipeline_textual.py': '5e5554118653c513213956597f84a80b87da3d208a3744a843e0fdb1a69f70b5',
    'tests/test_actions_g03_compact_narrow.py': '0fcfa57936b87968584f5cb8faddf1f7c45d9e12ebfa754e7ee78ae995b80be8',
    'docs/actions/ACTIONS_G03_NARROW_TEXTUAL_FIX.md': '903ebd71a2cf2865a62f9040dcbbfb6cbfc894a10787f07d43216a2b327f87b6',
}
_G3_TEXTUAL_HOTFIX_EVIDENCE = {
    'docs/actions/ACTIONS_G03_G8_SOURCE_AMENDMENT_20261014.md': '146d9b88d39c3c366d2b38f372605fdbc8bd6f9af7bea2db218fcaf36b402f13',
    'tests/test_actions_g08_g03_amendment.py': '453f24f05251313081228c85dea7ca2b6bcb8b1b750c4266843baa6eb050b96e',
}


def _g3_textual_amendment_valid(root: Path) -> bool:
    for name, expected in {**_G3_TEXTUAL_HOTFIX_ANCHORS,
                           **_G3_TEXTUAL_HOTFIX_EVIDENCE}.items():
        try:
            if _record_hash(_bounded_file(root, name, max_size=2 * 1024 * 1024)) != expected:
                return False
        except GitHubActionsError:
            return False
    return True


# A08 source-bound G6 correction: retain frozen G8 matrix and original G6 SHA,
# but accept precisely the parent-digest race fix with independent new proof.
# This never qualifies device operation, source attestation, or production release.
_A08_G6_AMENDMENT_ID = 'A08_PRIVATE_LIFE_HANDOFF_PARENT_DIGEST_20261019'
_A08_G6_ANCHORS = {'devtool/actions_local_evidence.py': 'ca001d28b44e74723019e526895b277c74c966ef369adb7815a9b7cddce5a8aa'}
_A08_G6_EVIDENCE = {'tests/test_actions_a08_local_handoff.py': 'acbde0be7c3426d8307c552e963d13e14441bb3d3ae7483f5636f877d6323c28', 'docs/actions/ACTIONS_A08_PRIVATE_HANDOFF.md': '83414a58395e167cc79a74d6b43151a744428f210381ba2c7c29bb16304ab6ee'}


def _a08_g6_amendment_valid(root: Path) -> bool:
    for name, expected in {**_A08_G6_ANCHORS, **_A08_G6_EVIDENCE}.items():
        try:
            if _record_hash(_bounded_file(root, name, max_size=2 * 1024 * 1024)) != expected:
                return False
        except GitHubActionsError:
            return False
    return True


def _git_head(root: Path) -> tuple[str, bool | None]:
    try:
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD'],
                              capture_output=True, text=True, timeout=5, check=False)
        status = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return '', None
    if head.returncode or not re.fullmatch('[0-9a-f]{40}', head.stdout.strip()):
        return '', None
    return head.stdout.strip(), (status.stdout.strip() == '' if status.returncode == 0 else None)


def qualification_report(root: Path, *, strict_source: bool = False) -> dict[str, Any]:
    """Verify G0 baseline and G0-G7 source pins; return truthful pending gates.

    `strict_source` stops on missing/mutated source contracts. Pending live gates
    are not failures of the source checker and cannot be marked passed here.
    """
    root = Path(root).absolute()
    if _record_hash(_bounded_file(root, _PLAN, max_size=1024*1024)) != _PLAN_SHA256:
        raise GitHubActionsError('G8 qualification matrix SHA-256 differs from installed immutable plan')
    plan = _document(root, _PLAN)
    if plan.get('schema_version') != 1 or plan.get('kind') != 'devtool.actions.g8.qualification-plan':
        raise GitHubActionsError('G8 qualification plan schema/kind mismatch')
    ledger_path = plan.get('original_ledger_path')
    if ledger_path != 'docs/actions/ACTIONS_BASELINE_OBLIGATIONS.json':
        raise GitHubActionsError('G8 original ledger location has changed')
    raw = _bounded_file(root, ledger_path, max_size=1024*1024)
    if not _SHA.fullmatch(str(plan.get('original_ledger_sha256', ''))) or _record_hash(raw) != plan['original_ledger_sha256']:
        raise GitHubActionsError('G8 frozen original obligation ledger SHA-256 mismatch')
    ledger = _document(root, ledger_path)
    obligations = ledger.get('obligations')
    if not isinstance(obligations, list) or len(obligations) != plan.get('original_requirement_count') or len(obligations) != 88:
        raise GitHubActionsError('G8 original obligation count mismatch')
    counts: Counter[str] = Counter()
    ids: set[str] = set()
    for obligation in obligations:
        if not isinstance(obligation, dict):
            raise GitHubActionsError('G8 original obligation row is invalid')
        key, status, wave = obligation.get('id'), obligation.get('status'), obligation.get('closure_wave')
        if not isinstance(key, str) or not key or key in ids or status not in _VALID_STATES:
            raise GitHubActionsError('G8 original obligation ID/status is ambiguous')
        if (status == 'EXCLUDED') != (wave == ''):
            raise GitHubActionsError('G8 excluded requirement was incorrectly assigned to a gate')
        if status != 'EXCLUDED' and wave not in {f'G{i}' for i in range(9)}:
            raise GitHubActionsError('G8 original requirement has no closure wave')
        ids.add(key)
        counts[status] += 1
    if dict(counts) != plan.get('historical_status_counts') or dict(counts) != ledger.get('counts'):
        raise GitHubActionsError('G8 historical requirement statistics changed')
    waves = plan.get('waves')
    if not isinstance(waves, list) or [w.get('id') for w in waves if isinstance(w, dict)] != [f'G{i}' for i in range(8)]:
        raise GitHubActionsError('G8 expected G0-G7 source wave inventory is incomplete')
    source_rows: list[dict[str, Any]] = []
    checked_files: set[str] = set()
    amended_g1 = _g1_qw02_amendment_valid(root)
    amended_g3 = _g3_textual_amendment_valid(root)
    amended_g4 = _g4_unsigned_jar_amendment_valid(root)
    amended_g6 = _a08_g6_amendment_valid(root)
    amendment_records: list[dict[str, Any]] = []
    g1_amendment_records: list[dict[str, Any]] = []
    for wave in waves:
        contracts = wave.get('source_contracts')
        if not isinstance(contracts, list) or len(contracts) < 2:
            raise GitHubActionsError('G8 wave source contract is incomplete')
        matching = True
        for entry in contracts:
            if not isinstance(entry, dict) or not _SHA.fullmatch(str(entry.get('sha256', ''))):
                raise GitHubActionsError('G8 source contract digest is malformed')
            name = entry.get('path')
            if name in checked_files:
                raise GitHubActionsError('G8 duplicated source contract path')
            checked_files.add(name)
            try:
                data = _bounded_file(root, name, max_size=2 * 1024 * 1024)
                ok = _record_hash(data) == entry['sha256']
            except GitHubActionsError:
                ok = False
            if not ok and wave['id'] == 'G1' and amended_g1 and name in _G1_QW02_ANCHORS:
                g1_amendment_records.append({'wave':'G1','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT',
                    'hotfix_id':_G1_QW02_AMENDMENT_ID,
                    'original_sha256':entry['sha256'],
                    'amended_sha256':_G1_QW02_ANCHORS[name]})
                source_rows.append({'wave':'G1','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT'})
            elif not ok and wave['id'] == 'G6' and amended_g6 and name in _A08_G6_ANCHORS:
                amendment_records.append({'wave':'G6','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT',
                    'hotfix_id':_A08_G6_AMENDMENT_ID,
                    'original_sha256':entry['sha256'],
                    'amended_sha256':_A08_G6_ANCHORS[name]})
                source_rows.append({'wave':'G6','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT'})
            elif not ok and wave['id'] == 'G3' and amended_g3 and name == 'devtool/actions_pipeline_textual.py':
                amendment_records.append({'wave':'G3','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT',
                    'hotfix_id':_G3_TEXTUAL_HOTFIX_ID,
                    'original_sha256':entry['sha256'],
                    'amended_sha256':_G3_TEXTUAL_HOTFIX_ANCHORS[name]})
                source_rows.append({'wave':'G3','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT'})
            elif not ok and wave['id'] == 'G4' and amended_g4 and name in _G4_UNSIGNED_JAR_HOTFIX_ANCHORS:
                # G8 still retains the original frozen SHA in its immutable
                # matrix. An explicit hotfix transition is separately verified.
                amendment_records.append({'wave':'G4','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT',
                    'hotfix_id':_G4_UNSIGNED_JAR_HOTFIX_ID,
                    'original_sha256':entry['sha256'],
                    'amended_sha256':_G4_UNSIGNED_JAR_HOTFIX_ANCHORS[name]})
                source_rows.append({'wave':'G4','path':name,
                    'status':'VERIFIED_POST_G08_AMENDMENT'})
            elif not ok:
                matching = False
                source_rows.append({'wave':wave['id'],'path':name,'status':'MISSING_OR_DIFFERENT'})
        source_rows.append({'wave':wave['id'],
                            'status':('SOURCE_PINS_AMENDED_VERIFIED' if matching and ((wave['id'] == 'G4' and amended_g4) or (wave['id'] == 'G3' and amended_g3) or (wave['id'] == 'G6' and amended_g6) or (wave['id'] == 'G1' and amended_g1))
                                      else 'SOURCE_PINS_MATCH' if matching else 'SOURCE_PINS_MISMATCH'),
                            'source_only':True,'live_qualified':False})
    gates = plan.get('gates')
    if not isinstance(gates, list) or len(gates) != 9:
        raise GitHubActionsError('G8 live gate inventory is incomplete')
    gate_ids: set[str] = set()
    pending = []
    for gate in gates:
        if not isinstance(gate, dict) or gate.get('live_status') != 'pending' or gate.get('evidence_receipt') != 'not_present_in_this_source_snapshot':
            raise GitHubActionsError('G8 plan tries to promote an unsupported live gate')
        identifier = gate.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in gate_ids or gate.get('wave') not in {f'G{i}' for i in range(9)}:
            raise GitHubActionsError('G8 live gate has invalid identity')
        if not isinstance(gate.get('required_for_final_claim'), bool) or not isinstance(gate.get('operator_deferred'), bool):
            raise GitHubActionsError('G8 live gate has ambiguous status')
        gate_ids.add(identifier)
        pending.append({'id':identifier, 'wave':gate['wave'], 'status':'PENDING_LIVE',
                        'operator_deferred':gate['operator_deferred'],
                        'required':gate['required_for_final_claim'],
                        'acceptance':gate.get('acceptance','')})
    if 'G8_BROAD_REGRESSIONS' not in gate_ids or 'G1_GITHUB_NEGATIVES' not in gate_ids:
        raise GitHubActionsError('G8 essential acceptance gates are missing')
    prior = plan.get('historical_dependencies',{}).get('V105_pre_actions',{})
    if not isinstance(prior, dict) or prior.get('status') != 'unresolved_historical_cross_campaign' or prior.get('required_reconciliation') is not True:
        raise GitHubActionsError('G8 historical V105 blockers were incorrectly promoted')
    rules = plan.get('qualification_rules',{})
    if rules.get('qualified_release') is not False or rules.get('source_attested') is not False:
        raise GitHubActionsError('G8 qualification policy cannot assert source attestation or release readiness')
    if plan.get('paid_private_github_branch_protection',{}).get('status') != 'excluded_by_user':
        raise GitHubActionsError('G8 paid private GitHub requirement was silently changed')
    source_consistent = all(row['status'] != 'SOURCE_PINS_MISMATCH' for row in source_rows)
    if strict_source and not source_consistent:
        raise GitHubActionsError('G8 source contract does not match the pinned post-G7 snapshot')
    current_head, clean = _git_head(root)
    return {'schema_version':1,'operation':'actions-qualification','mode':'read-only',
            'source_head':current_head,'working_tree_tracked_clean':clean,
            'source_reference_head':plan.get('reference_source_commit'),
            'source_contract_consistent':source_consistent,
            'source_contracts':source_rows,
            'verified_source_amendments':amendment_records,
            'verified_g1_dispatch_amendments':g1_amendment_records,
            'original_obligations':{'count':len(obligations),'historical_status_counts':dict(counts),
                                    'preserved_verbatim':True,'source_snapshot_only':True},
            'wave_implementation':[{ 'wave':w['id'],'source_pins_present':
                                    next(x['status'] in ('SOURCE_PINS_MATCH', 'SOURCE_PINS_AMENDED_VERIFIED') for x in source_rows if x.get('wave') == w['id'] and 'source_only' in x),
                                    'status':'SOURCE_IMPLEMENTED_LIVE_UNQUALIFIED'} for w in waves],
            'acceptance_gates':pending,'pending_required_gate_count':sum(g['required'] for g in pending),
            'historical_dependencies':plan['historical_dependencies'],
            'user_excluded_paid_github_gate':True,
            'qualified_release':False,'source_attested':False,'campaign_closed':False,
            'status':'BLOCKED_ON_LIVE_AND_HISTORICAL_EVIDENCE',
            'note':'This source-only report never runs tests or upgrades historical and external evidence.'}
