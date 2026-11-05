from __future__ import annotations

"""Read-only, exact-source R39-R4 source/CI admission ledger.

The audit does NOT authenticate GitHub Actions receipts or authorize the
operator/device/signer/release lanes. These require independent evidence.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

SCHEMA = 'devtool.roadmap-gate-audit.v1'
_ACTIONS = {
    'actions/checkout': 'v6',
    'actions/setup-python': 'v6',
    'actions/setup-go': 'v6',
    'actions/setup-java': 'v5',
    'actions/setup-node': 'v6',
    'actions/setup-dotnet': 'v5',
    'actions/upload-artifact': 'v7',
    'actions/download-artifact': 'v8',
    'actions/attest': 'v4',
    'gradle/actions/setup-gradle': 'v5',
}
# Immutable GitHub tags resolved on 2026-11-01, not arbitrary commit IDs.
_PINNED_NODE24 = {
    'actions/checkout': 'd23441a48e516b6c34aea4fa41551a30e30af803',
    'actions/setup-python': 'ece7cb06caefa5fff74198d8649806c4678c61a1',
    'actions/setup-java': 'b6effb05e454b25005698d916606bdc6ffcbf961',
    'actions/setup-dotnet': '26b0ec14cb23fa6904739307f278c14f94c95bf1',
    'actions/upload-artifact': 'cf430e030ddbb5b0abf93d22962f4752f3646cd9',
    'actions/download-artifact': '9000827ccba6bdab643e8b6fd33ac0654aef8333',
    'gradle/actions/setup-gradle': '0723195856401067f7a2779048b490ace7a47d7c',
}

_USES = re.compile(r'^\s*(?:-\s*)?uses:\s*([^\s#]+)', re.MULTILINE)
_SOURCE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    'NEXT-02/03': ('devtool/workflow_scheduler.py', 'devtool/actions_language_providers.py'),
    'NEXT-04/05': ('devtool/actions_r35_verified_import.py', 'devtool/actions_r36_verified_import.py'),
    'NEXT-07/08': ('devtool/actions_rm09_dotnet.py', 'devtool/actions_rm10_cargo.py',
                   'devtool/actions_rm11_node.py', 'devtool/actions_rm12_jvm.py',
                   'devtool/actions_rm13_cpp.py', 'devtool/actions_rm14_shell.py'),
    'R38': ('devtool/workflow_write_audit.py', 'devtool/workflow_scratch.py',
            'devtool/workflow_benchmark.py', 'devtool/workflow_scheduler.py'),
    'R39': ('devtool/workflow_execution_policy.py', 'devtool/workflow_parity.py',
            'devtool/workflow_write_audit.py'),
}

# Historical SHA/run proof is NEVER converted to current-source acceptance.
HISTORICAL: tuple[dict[str, object], ...] = (
    {'kind': 'full_python_regression', 'commit': '6426e6e3d3145e0ce9181c1817352eb1f4f249f7',
     'run_id': 41361212330, 'result': 'PASS', 'scope': '7395 tests; 24 shards; source only'},
    {'kind': 'bounded_serial_parity', 'commit': '6426e6e3d3145e0ce9181c1817352eb1f4f249f7',
     'run_id': 41361215581, 'result': 'PASS', 'scope': '5 cases; legacy retirement NOT QUALIFIED'},
    {'kind': 'synthetic_dag_benchmark', 'commit': 'e3cc5473be04f21d7b807b3efc2a1617202fb328',
     'run_id': 41351437389, 'result': 'PASS', 'scope': 'synthetic workers only; non-release'},
)

_OPEN = {
    'full_24_shard_ci_this_sha': 'NOT_RUN',
    'hosted_source_gate_workflow_this_sha': 'NOT_RUN',
    'real_consumer_adoption_this_sha': 'NOT_RUN',
    'authenticated_operator_host_artifact_import': 'NOT_RUN',
    'actual_android_device_and_root_acceptance': 'NOT_RUN',
    'windows_linux_real_host_provider_parity_this_sha': 'NOT_RUN',
    'native_parallel_vs_legacy_equivalence': 'NOT_RUN',
    'interrupted_host_orphan_recovery': 'NOT_RUN',
    'actual_untracked_and_external_write_isolation': 'NOT_RUN',
    'real_build_cache_performance': 'NOT_RUN',
    'dynamically_generated_action_runtime_migration': 'NOT_RUN',
    'external_third_party_action_runtime_parity': 'NOT_RUN',
    'signing_and_formal_release_authorization': 'BLOCKED',
    'full_legacy_engine_retirement': 'BLOCKED',
}


class GateAuditError(RuntimeError):
    pass


def git_identity(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, ref in (('commit', 'HEAD'), ('tree', 'HEAD^{tree}')):
        try:
            proc = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', ref],
                                  check=True, text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateAuditError(f'R39_R4_SOURCE_IDENTITY_UNAVAILABLE:{name}') from exc
        value = proc.stdout.strip()
        if len(value) != 40 or not re.fullmatch('[a-f0-9]{40}', value):
            raise GateAuditError('R39_R4_INVALID_GIT_IDENTITY')
        values[name] = value
    return values


def audit_workflows(root: Path) -> dict[str, object]:
    """Audit versioned workflow source, not unrelated host-local YAML files.

    A native apply runs inside a detached Git worktree before ``git add``.
    Thus its newly introduced source-gate workflow is included explicitly,
    while unrelated untracked workflows must not affect a Git source receipt.
    In a fixture without Git, inspect the fixture's supplied YAML inventory.
    """
    folder = root / '.github' / 'workflows'
    if (root / '.git').exists():
        try:
            tracked = subprocess.run(
                ['git', '-C', str(root), 'ls-files', '--cached', '-z',
                 '--', '.github/workflows/'],
                check=True, capture_output=True, timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise GateAuditError('R39_R4_WORKFLOW_GIT_INVENTORY_UNAVAILABLE') from exc
        tracked_names = {Path(name.decode('utf-8')) for name in tracked.split(b'\x00') if name}
        files = {root / name for name in tracked_names
                 if name.parent == Path('.github/workflows') and name.suffix in ('.yml', '.yaml')}
        # The manually dispatched source-gate workflow is newly added by the
        # overlay; it is not staged when the artifact validator runs.
        candidate = folder / 'devtool-r39-consolidated-source-gates.yml'
        if candidate.is_file():
            files.add(candidate)
        files = sorted(files)
        missing = [str(p.relative_to(root)) for p in files if not p.is_file()]
        if missing:
            raise GateAuditError('R39_R4_TRACKED_WORKFLOW_MISSING:' + ','.join(missing[:8]))
    else:
        files = sorted(folder.glob('*.yml')) if folder.is_dir() else []
    if len(files) < 20:
        raise GateAuditError('R39_R4_WORKFLOW_INVENTORY_INCOMPLETE')
    actions: dict[str, int] = {}
    violations: list[str] = []
    unknown: set[str] = set()
    for file in files:
        for use in _USES.findall(file.read_text(encoding='utf-8')):
            path, sep, version = use.rpartition('@')
            if not sep:
                violations.append(f'{file.name}:{use}:missing_version')
                continue
            if path in _ACTIONS:
                expected = _ACTIONS[path]
                if version != expected and version != _PINNED_NODE24.get(path):
                    violations.append(f'{file.name}:{use}:expected_{expected}')
                actions[f'{path}@{version}'] = actions.get(f'{path}@{version}', 0) + 1
            else:
                # Third-party/composite/local actions are not certified here.
                unknown.add(use)
    return {'workflow_count': len(files), 'first_party_uses': actions,
            'first_party_violations': violations, 'other_actions_unqualified': sorted(unknown),
            'status': 'PASS' if not violations else 'FAIL'}


def audit(root: Path) -> dict[str, object]:
    root = root.resolve()
    source = git_identity(root)
    code = root / 'devtool/workflow_scheduler.py'
    source_gates = {
        name: {'status': 'SOURCE_PRESENT' if all((root / p).is_file() for p in paths) else 'FAIL',
               'paths': list(paths)}
        for name, paths in _SOURCE_REQUIREMENTS.items()
    }
    if not code.is_file():
        raise GateAuditError('R39_R4_SCHEDULER_MISSING')
    scheduler = code.read_text(encoding='utf-8')
    if 'from .workflow_write_audit import attest_node_transition' not in scheduler:
        source_gates['R39']['status'] = 'FAIL'
    if 'def run_workflow_specs(' not in scheduler or 'run_specs(' not in scheduler:
        source_gates['R39']['status'] = 'FAIL'
    from .actions_runtime_pins import audit as audit_generator_actions, verify as verify_generator_actions
    workflows = audit_workflows(root)
    generator_pins = audit_generator_actions()
    try:
        verify_generator_actions(generator_pins)
    except ValueError as exc:
        raise GateAuditError(str(exc)) from exc
    source_pass = workflows['status'] == 'PASS' and all(x['status'] == 'SOURCE_PRESENT' for x in source_gates.values())
    receipt: dict[str, object] = {
        'schema': SCHEMA,
        'repository': 'Mikeyphw/devtool',
        'source': source,
        'admission': {'status': 'PASS' if source_pass else 'FAIL',
                      'scope': 'read-only source inventory and first-party Action declarations only'},
        'source_gates': source_gates,
        'workflows': workflows,
        'generated_action_source_pins': generator_pins,
        'historical_exact_source_evidence': deepcopy(list(HISTORICAL)),
        'independent_gates_this_source': dict(_OPEN),
        'qualification': {
            'source_inventory_accepted': source_pass,
            'full_current_sha_ci_attested': False,
            'hosted_real_consumer_attested': False,
            'native_parallel_parity_attested': False,
            'device_attested': False,
            'signing_attested': False,
            'release_attested': False,
            'legacy_retirement_authorized': False,
        },
    }
    verify(receipt)
    return receipt


def verify(receipt: dict[str, object], *, source: dict[str, str] | None = None) -> None:
    if not isinstance(receipt, dict) or receipt.get('schema') != SCHEMA:
        raise GateAuditError('R39_R4_INVALID_SCHEMA')
    identity = receipt.get('source')
    if not isinstance(identity, dict) or any(not re.fullmatch(r'[0-9a-f]{40}', str(identity.get(x, '')))
                                                for x in ('commit', 'tree')):
        raise GateAuditError('R39_R4_UNBOUND_SOURCE')
    if source is not None and identity != source:
        raise GateAuditError('R39_R4_SOURCE_MISMATCH')
    wf = receipt.get('workflows')
    if not isinstance(wf, dict) or wf.get('status') != 'PASS' or wf.get('first_party_violations') != []:
        details = wf.get('first_party_violations', []) if isinstance(wf, dict) else []
        if isinstance(details, list) and details:
            # Never collapse the concrete offending filename/action into a
            # generic refusal: operators need the precise violation.
            raise GateAuditError('R39_R4_ACTION_RUNTIME_GAP:' + ';'.join(map(str, details[:12])))
        raise GateAuditError('R39_R4_ACTION_RUNTIME_GAP:invalid_workflow_receipt')
    from .actions_runtime_pins import verify as verify_generator_actions
    try:
        verify_generator_actions(receipt.get('generated_action_source_pins'))
    except ValueError as exc:
        raise GateAuditError(str(exc)) from exc
    sg = receipt.get('source_gates')
    if not isinstance(sg, dict) or set(sg) != set(_SOURCE_REQUIREMENTS) or any(
            not isinstance(v, dict) or v.get('status') != 'SOURCE_PRESENT' for v in sg.values()):
        raise GateAuditError('R39_R4_SOURCE_CONTRACT_GAP')
    if receipt.get('admission', {}).get('status') != 'PASS':
        raise GateAuditError('R39_R4_SOURCE_NOT_ACCEPTED')
    gates = receipt.get('independent_gates_this_source')
    if gates != _OPEN:
        raise GateAuditError('R39_R4_UNAUTHORIZED_GATE_CLOSURE')
    qualification = receipt.get('qualification')
    if qualification != {
            'source_inventory_accepted': True,
            'full_current_sha_ci_attested': False,
            'hosted_real_consumer_attested': False,
            'native_parallel_parity_attested': False,
            'device_attested': False,
            'signing_attested': False,
            'release_attested': False,
            'legacy_retirement_authorized': False,
        }:
        raise GateAuditError('R39_R4_UNAUTHORIZED_QUALIFICATION')
    hist = receipt.get('historical_exact_source_evidence')
    if hist != list(HISTORICAL):
        raise GateAuditError('R39_R4_HISTORICAL_PROVENANCE_CHANGED')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only exact-source roadmap admission (not release authority)')
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        receipt = audit(args.repo)
    except (OSError, GateAuditError) as exc:
        print(f'R39_R4_SOURCE_AUDIT_REFUSED: {exc}', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print('R39_R4_SOURCE_INVENTORY_PASS; independent real-host/device/release gates remain OPEN')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
