"""R39-R5: generated workflows use reviewed Node 24 first-party Action pins."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from devtool import actions_adoption, actions_graph_export, actions_language_providers, toolchain_profiles
from devtool.actions_runtime_pins import EXPECTED, GeneratorPinError, audit, verify


def test_complete_first_party_generator_source_pin_inventory():
    result = audit()
    verify(result)
    assert result['status'] == 'PASS'
    assert len(result['pins']) == 10
    for action, users in result['pins'].items():
        assert users and all(pin == EXPECTED[action] for pin in users.values())
    assert not result['third_party_actions_qualified']
    assert not result['real_consumer_qualified']
    assert not result['release_qualified']


@pytest.mark.parametrize(('module', 'name'), [
    (actions_adoption, '_CHECKOUT_SHA'),
    (actions_adoption, '_SETUP_GO_SHA'),
    (actions_adoption, '_UPLOAD_SHA'),
    (actions_graph_export, '_DOWNLOAD_SHA'),
    (actions_graph_export, '_SETUP_JAVA_SHA'),
    (actions_graph_export, '_SETUP_GRADLE_SHA'),
    (actions_graph_export, '_SETUP_NODE_SHA'),
    (actions_graph_export, '_SETUP_DOTNET_SHA'),
    (actions_language_providers, 'SETUP_PYTHON_SHA'),
    (toolchain_profiles, 'CACHE_ACTION_SHA'),
])
def test_each_generator_pin_drift_is_refused(monkeypatch, module, name):
    monkeypatch.setattr(module, name, '0' * 40)
    result = audit()
    assert result['status'] == 'FAIL'
    assert result['violations']
    with pytest.raises(GeneratorPinError, match='GENERATOR_PIN'):
        verify(result)


def test_adoption_yaml_uses_current_pins_and_remains_nonrelease():
    content = actions_adoption._yaml('app', 'ci', 'a' * 40)
    for action in ('actions/checkout', 'actions/upload-artifact'):
        assert f'uses: {action}@{EXPECTED[action]}' in content
    assert 'pull_request:' not in content
    assert 'workflow_dispatch:' in content


def test_toolchain_capability_doctor_reports_current_pin_set():
    info = toolchain_profiles.capability_report(__import__('pathlib').Path('.'), probe=False)
    pins = info['setup_action_pins']
    assert pins['checkout'] == EXPECTED['actions/checkout']
    assert pins['python'] == EXPECTED['actions/setup-python']
    assert pins['go'] == EXPECTED['actions/setup-go']
    assert pins['cache'] == EXPECTED['actions/cache']
    assert info['release_qualified'] is False


def test_receipt_cannot_promote_hosted_or_release_authority():
    result = audit()
    result['real_consumer_qualified'] = True
    with pytest.raises(GeneratorPinError, match='RECEIPT_CHANGED'):
        verify(result)


def test_cli_is_readonly_json_and_refuses_no_known_pins():
    proc = subprocess.run([sys.executable, '-m', 'devtool.actions_runtime_pins', '--json'],
                          text=True, capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    verify(result)
