"""R39-R5: read-only source contracts for first-party generated Actions.

Pin values are reviewed immutable GitHub tag objects. This report verifies
source constants, not the behavior of remote hosted workers or consumers.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

EXPECTED: dict[str, str] = {
    'actions/checkout': 'd23441a48e516b6c34aea4fa41551a30e30af803',  # v6
    'actions/setup-python': 'ece7cb06caefa5fff74198d8649806c4678c61a1',  # v6
    'actions/setup-go': '924ae3a1cded613372ab5595356fb5720e22ba16',  # v6
    'actions/setup-java': 'b6effb05e454b25005698d916606bdc6ffcbf961',  # v5
    'actions/setup-node': '249970729cb0ef3589644e2896645e5dc5ba9c38',  # v6
    'actions/setup-dotnet': '26b0ec14cb23fa6904739307f278c14f94c95bf1',  # v5
    'actions/upload-artifact': 'cf430e030ddbb5b0abf93d22962f4752f3646cd9',  # v7
    'actions/download-artifact': '9000827ccba6bdab643e8b6fd33ac0654aef8333',  # v8
    'actions/cache': 'caa296126883cff596d87d8935842f9db880ef25',  # v5
    'gradle/actions/setup-gradle': '0723195856401067f7a2779048b490ace7a47d7c',  # v5
}
SCHEMA = 'devtool.actions-runtime-pins.v1'


class GeneratorPinError(ValueError):
    pass


def observed() -> dict[str, dict[str, str]]:
    # Import lazily so the verifier never dispatches a provider or touches a host.
    from . import actions_adoption as adoption
    from . import actions_language_providers as language
    from . import actions_graph_export as graph
    from . import toolchain_profiles as profiles
    return {
        'actions/checkout': {'adoption': adoption._CHECKOUT_SHA},
        'actions/setup-python': {'language_providers': language.SETUP_PYTHON_SHA},
        'actions/setup-go': {'adoption': adoption._SETUP_GO_SHA},
        'actions/setup-java': {'graph_export': graph._SETUP_JAVA_SHA},
        'actions/setup-node': {'graph_export': graph._SETUP_NODE_SHA},
        'actions/setup-dotnet': {'graph_export': graph._SETUP_DOTNET_SHA},
        'actions/upload-artifact': {'adoption': adoption._UPLOAD_SHA},
        'actions/download-artifact': {'graph_export': graph._DOWNLOAD_SHA},
        'actions/cache': {'toolchain_profiles': profiles.CACHE_ACTION_SHA},
        'gradle/actions/setup-gradle': {'graph_export': graph._SETUP_GRADLE_SHA},
    }


def audit() -> dict[str, Any]:
    found = observed()
    problems = [f'{action}:{owner}:expected={EXPECTED[action]}:found={pin}'
                for action, owners in sorted(found.items())
                for owner, pin in sorted(owners.items())
                if pin != EXPECTED[action]]
    if set(found) != set(EXPECTED):
        problems.append('R39_R5_GENERATOR_PIN_INVENTORY_MISMATCH')
    return {
        'schema': SCHEMA,
        'status': 'PASS' if not problems else 'FAIL',
        'scope': 'generator source constants only; no hosted or consumer qualification',
        'pins': found,
        'violations': problems,
        'third_party_actions_qualified': False,
        'real_consumer_qualified': False,
        'release_qualified': False,
    }


def verify(receipt: dict[str, Any]) -> None:
    if not isinstance(receipt, dict) or receipt.get('schema') != SCHEMA:
        raise GeneratorPinError('R39_R5_INVALID_GENERATOR_PIN_SCHEMA')
    if receipt != audit():
        raise GeneratorPinError('R39_R5_GENERATOR_PIN_RECEIPT_CHANGED')
    if receipt['status'] != 'PASS':
        raise GeneratorPinError('R39_R5_GENERATOR_PIN_GAP:' + ';'.join(receipt['violations'][:12]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Read-only generated workflow source pin contract')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    receipt = audit()
    if args.json:
        print(json.dumps(receipt, sort_keys=True, indent=2))
    else:
        print('R39_R5_GENERATOR_ACTION_PINS_' + receipt['status'])
    if receipt['status'] != 'PASS':
        print('R39_R5_SOURCE_PIN_GAP: ' + ';'.join(receipt['violations'][:12]), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
