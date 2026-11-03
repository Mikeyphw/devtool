"""Source-only negative tests; full .NET qualification must run on Actions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest

SOURCE = Path(__file__).parents[1] / 'tools' / 'dn02_hosted_acceptance.py'
spec = importlib.util.spec_from_file_location('dn02_hosted_acceptance', SOURCE)
assert spec and spec.loader
accept = importlib.util.module_from_spec(spec)
spec.loader.exec_module(accept)


def test_dn02_context_requires_real_run_and_reviewed_runner():
    env = {
        'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'Mikeyphw/devtool',
        'RUNNER_OS': 'Linux', 'RUNNER_ARCH': 'X64',
        'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '2',
        'GITHUB_SHA': 'a' * 40,
    }
    ok = accept._check_context(env)
    assert ok['run_id'] == 123 and ok['run_attempt'] == 2
    for key, bad in [('GITHUB_ACTIONS', 'false'), ('RUNNER_OS', 'Windows'),
                     ('RUNNER_ARCH', 'ARM64'), ('GITHUB_RUN_ID', '0'),
                     ('GITHUB_RUN_ATTEMPT', 'not-a-number')]:
        with pytest.raises(accept.AcceptanceError):
            accept._check_context({**env, key: bad})


def test_dn02_receipts_are_create_only(tmp_path):
    destination = tmp_path / 'receipts' / 'preflight.json'
    accept._write_json(destination, {'kind': 'test', 'release_qualified': False})
    assert accept._json(destination)['kind'] == 'test'
    with pytest.raises(accept.AcceptanceError, match='already exists'):
        accept._write_json(destination, {'kind': 'test'})


def test_dn02_trx_identifiers_and_reproducibility_contract():
    assert len(accept.TRX) == 2
    assert len(set(accept.TRX)) == 2
    assert 'Tests/RoosterControl.PureTests/packages.lock.json' in accept.CONSUMER_PATHS
    assert 'Tests/EggsInWild.AnimalNotes.Tests/packages.lock.json' in accept.CONSUMER_PATHS
    assert accept.SDK == '10.0.100'
    assert accept.RUNTIME == '6.0.36'


def test_dn02_source_pin_is_mandatory(tmp_path):
    with pytest.raises(accept.AcceptanceError, match='full 40-character'):
        accept.inspect_inputs(tmp_path, tmp_path, 'd9dbb4c', '549d6cd', {})


def test_dn02_workflow_is_pinned_and_not_release():
    workflow = (Path(__file__).parents[1] / '.github' / 'workflows' /
                'devtool-dn02-hosted-net6-acceptance.yml').read_text()
    assert 'pull_request:' in workflow
    assert 'push:' in workflow
    assert 'permissions:\n  contents: read' in workflow
    assert '549d6cd0d08894dfac4950e362e437eb06010729' in workflow
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in workflow
    assert workflow.count('actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1') == 2
    assert "dotnet-version: '10.0.100'" in workflow
    assert "dotnet-version: '6.0.428'" in workflow
    assert '--locked-mode' in workflow and '--no-restore' in workflow
    assert 'tools/dn02_hosted_acceptance.py verify' in workflow
    assert 'scripts/verify_dotnet_trx.py' in SOURCE.read_text()


def test_dn02_sdk_selection_isolated_and_exact(tmp_path):
    import json
    root = tmp_path / 'runner'
    context = root / 'dn02-sdk-context'
    context.mkdir(parents=True)
    pin = context / 'global.json'
    expected = {'sdk': {'version': '10.0.100', 'rollForward': 'disable',
                        'allowPrerelease': False}}
    pin.write_text(json.dumps(expected))
    env = {'RUNNER_TEMP': str(root), 'DN02_SDK_DIR': str(context)}
    selected, digest = accept.sdk_context(env)
    assert selected == context
    assert len(digest) == 64
    for invalid in [
        {'sdk': {'version': '10.0.100', 'rollForward': 'latestFeature',
                 'allowPrerelease': False}},
        {'sdk': {'version': '10.0.101', 'rollForward': 'disable',
                 'allowPrerelease': False}},
        {'sdk': {'version': '10.0.100', 'rollForward': 'disable'}},
    ]:
        pin.write_text(json.dumps(invalid))
        with pytest.raises(accept.AcceptanceError, match='SDK selection context'):
            accept.sdk_context(env)
    pin.write_text(json.dumps(expected))
    with pytest.raises(accept.AcceptanceError, match='reviewed runner-local'):
        accept.sdk_context({**env, 'DN02_SDK_DIR': str(tmp_path)})


def test_dn02_workflow_runs_restore_and_tests_in_exact_sdk_context():
    workflow = (Path(__file__).parents[1] / '.github' / 'workflows' /
                'devtool-dn02-hosted-net6-acceptance.yml').read_text()
    assert workflow.count('working-directory: ${{ runner.temp }}/dn02-sdk-context') == 3
    assert '"$DN02_CONSUMER/Tests/RoosterControl.PureTests/RoosterControl.PureTests.csproj"' in workflow
    assert '"$DN02_CONSUMER/Tests/EggsInWild.AnimalNotes.Tests/EggsInWild.AnimalNotes.Tests.csproj"' in workflow
    assert "'rollForward':'disable'" in workflow
    assert 'DN02_SDK_DIR=%s' in workflow
