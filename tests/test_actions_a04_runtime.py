"""A04: exercise A03-generated commands through the actual Devtool CLI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from devtool.actions_adoption import _yaml, adopt
from devtool.execution_environments import detect_current_environment, normalize_execution_environment, resolve_execution_plan

PIN = '8706a4330ef42b1eadd2b6762d71ef4d71e17cfb'
ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path, *, failed: bool = False) -> Path:
    repo = tmp_path / 'consumer'
    repo.mkdir()
    command = '["python3", "-c", "import sys; sys.exit(7)"]' if failed else '["python3", "-c", "print(271828)"]'
    (repo / '.devtool.toml').write_text('''[project]
runner = "python"
[workspace]
default_target = "app"
[wrapper]
schema = 2
[targets.app]
runner = "python"
root = "."
[targets.app.jobs.unit]
runner = "command"
command = ''' + command + '''
[targets.app.workflows]
ci = [{id="unit", ref="job:unit"}]
''')
    wrapper = repo / 'devtoolw'
    wrapper.write_text('#!/bin/sh\nexec python3 -m devtool --repo "$(dirname "$0")" wrapper exec "$@"\n')
    wrapper.chmod(0o755)
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-qm', 'initial'], check=True)
    return repo


def _fixture_host_selector() -> tuple[str, str | None]:
    """Identify the *actual* Python substrate, not a sanitized validator hint.

    On Termux, artifact validation may temporarily erase PREFIX/HOME markers;
    `host`/`auto` then resolves to `host-android`, which is an observational
    diagnostic, not a valid execution environment. The running interpreter
    path remains an independently observable indicator of native Termux.
    Never fake a Termux identity from Android alone.
    """
    termux_prefix = '/data/data/com.termux/files/usr'
    executable = str(Path(sys.executable).resolve())
    base_prefix = str(Path(sys.base_prefix).resolve())
    if executable.startswith(termux_prefix + '/') or base_prefix == termux_prefix:
        return 'native-termux', termux_prefix
    current = detect_current_environment()
    if current in {'desktop-linux', 'desktop-macos', 'desktop-windows',
                   'native-termux', 'chroot', 'ssh', 'proot-distro'}:
        return current, None
    raise RuntimeError('A04 test fixture cannot qualify unknown substrate: ' + current)


def _env(repo: Path) -> dict[str, str]:
    result = os.environ.copy()
    result['PYTHONPATH'] = str(ROOT) + (os.pathsep + result['PYTHONPATH'] if result.get('PYTHONPATH') else '')
    result['GITHUB_WORKSPACE'] = str(repo)
    selector, restored_prefix = _fixture_host_selector()
    # Restore Termux's *real* executable-derived PREFIX solely for subprocess
    # tests if the parent validator sanitized environment variables. Merely
    # requesting `host` was insufficient: host resolved to `host-android`.
    if restored_prefix:
        result['PREFIX'] = restored_prefix
        result.pop('DEVTOOL_EXECUTION_CONTEXT', None)
        result.pop('DEVTOOL_INSIDE_TERMUX_CHROOT', None)
    result['DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT'] = selector
    result['DEVTOOL_EXECUTION_ENVIRONMENT'] = selector
    # A user/global default must not silently override the fixture substrate.
    # Fixture ownership ends at this child environment; no caller env changes.
    result['XDG_CONFIG_HOME'] = str(repo.parent / 'a04-isolated-config')
    result['DEVTOOL_GLOBAL_CONFIG'] = str(Path(result['XDG_CONFIG_HOME']) / 'devtool' / 'config.toml')
    return result


def _command_from_generated_yaml(yaml: str, needle: str) -> str:
    lines = [line.strip() for line in yaml.splitlines() if needle in line]
    assert len(lines) == 1, lines
    return lines[0]


def _invoke_yaml(repo: Path, runner_tmp: Path, *, failed: bool = False) -> tuple[subprocess.CompletedProcess[str], Path]:
    yml = _yaml('app', 'ci', PIN)
    assert 'invoke --target app ci --json' not in yml
    cmd = _command_from_generated_yaml(yml, ' --machine-file ')
    venv_bin = runner_tmp / 'devtool-venv' / 'bin'
    venv_bin.mkdir(parents=True, exist_ok=True)
    (venv_bin / 'python').write_text('#!/bin/sh\nexec ' + sys.executable + ' \"$@\"\n')
    (venv_bin / 'python').chmod(0o755)
    env = _env(repo)
    env['RUNNER_TEMP'] = str(runner_tmp)
    run = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + cmd], cwd=repo, env=env, capture_output=True, text=True, timeout=35)
    return run, runner_tmp / 'devtool-invoke.json'


def test_a04_generator_uses_global_machine_file_not_invalid_invoke_json():
    yml = _yaml('app', 'ci', PIN)
    line = _command_from_generated_yaml(yml, ' --machine-file ')
    assert re.search(r'--repo "\$GITHUB_WORKSPACE" --machine-file "\$RUNNER_TEMP/devtool-invoke.json" invoke --target app ci$', line)
    assert 'if: always()' in yml and 'devtool-invoke.json' in yml


def test_a04_real_plan_preflight_and_invoke_receipt(tmp_path, monkeypatch):
    # Reproduce the invalid selector exposed by native Termux's validator.
    # `_env` must normalize this inherited fixture-only execution policy.
    monkeypatch.setenv('DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT', 'host-android')
    monkeypatch.setenv('DEVTOOL_EXECUTION_ENVIRONMENT', 'host-android')
    repo = _fixture(tmp_path)
    selector, _ = _fixture_host_selector()
    assert _env(repo)['DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT'] == selector
    assert _env(repo)['DEVTOOL_EXECUTION_ENVIRONMENT'] == selector
    assert selector != 'host-android'
    yml = _yaml('app', 'ci', PIN)
    env = _env(repo)
    runner_tmp = tmp_path / 'runtime'
    runner_tmp.mkdir()
    (runner_tmp / 'devtool-venv' / 'bin').mkdir(parents=True)
    (runner_tmp / 'devtool-venv' / 'bin' / 'python').write_text('#!/bin/sh\nexec ' + sys.executable + ' \"$@\"\n')
    (runner_tmp / 'devtool-venv' / 'bin' / 'python').chmod(0o755)
    env['RUNNER_TEMP'] = str(runner_tmp)
    for needle, output in [(' plan --target ', 'devtool-plan.json'), (' preflight --target ', 'devtool-preflight.json')]:
        cmd = _command_from_generated_yaml(yml, needle)
        run = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + cmd], cwd=repo, env=env, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr + run.stdout
        data = json.loads((runner_tmp / output).read_text())
        assert isinstance(data, dict)
    run, evidence = _invoke_yaml(repo, runner_tmp)
    assert run.returncode == 0, run.stderr + run.stdout
    payload = json.loads(evidence.read_text())
    assert payload['command'] == 'invoke'
    assert payload['ok'] is True
    assert payload['exit_code'] == 0
    assert payload['results'], 'Do not qualify empty execution as a passing DAG'
    assert payload['workflow']


def test_a04_invoke_failure_propagates_but_retains_machine_evidence(tmp_path):
    repo = _fixture(tmp_path, failed=True)
    run, evidence = _invoke_yaml(repo, tmp_path / 'failed-runner')
    assert run.returncode != 0
    assert evidence.is_file(), run.stderr + run.stdout
    payload = json.loads(evidence.read_text())
    assert payload['command'] == 'invoke'
    assert payload['ok'] is False
    assert payload['exit_code'] != 0


def test_a04_nested_fixture_uses_supported_host_selector(tmp_path, monkeypatch):
    monkeypatch.setenv('DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT', 'host-android')
    monkeypatch.setenv('DEVTOOL_EXECUTION_ENVIRONMENT', 'host-android')
    env = _env(tmp_path)
    selector, _ = _fixture_host_selector()
    assert env['DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT'] == selector
    assert env['DEVTOOL_EXECUTION_ENVIRONMENT'] == selector
    assert all(v != 'host-android' for k, v in env.items()
               if k in {'DEVTOOL_EXECUTION_ENVIRONMENT', 'DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT'})


def test_a04_generated_wrapper_alias_actually_runs_target_local_job(tmp_path):
    repo = _fixture(tmp_path)
    generated = adopt(repo, target='app', workflow='ci', devtool_sha=PIN, write=True)
    alias = generated['wrapper_command']
    result = subprocess.run([str(repo / 'devtoolw'), alias], cwd=repo, env=_env(repo), capture_output=True, text=True, timeout=35)
    assert result.returncode == 0, result.stderr + result.stdout
    assert 'unit' in result.stdout


def test_a04_termux_interpreter_recovers_canonical_substrate_after_marker_sanitization(monkeypatch, tmp_path):
    import devtool.host_context as host_context
    monkeypatch.setattr(sys, 'executable', '/data/data/com.termux/files/usr/bin/python3.14')
    monkeypatch.setattr(host_context.platform, 'system', lambda: 'Android')
    monkeypatch.setattr(host_context.sys, 'platform', 'android')
    monkeypatch.delenv('TERMUX_VERSION', raising=False)
    monkeypatch.delenv('PREFIX', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'sanitized-home'))
    assert detect_current_environment() == 'host-android'
    env = _env(tmp_path)
    assert env['PREFIX'] == '/data/data/com.termux/files/usr'
    assert env['DEVTOOL_EXECUTION_ENVIRONMENT'] == 'native-termux'
    assert env['DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT'] == 'native-termux'
    assert detect_current_environment(env) == 'native-termux'
    plan = resolve_execution_plan(requested=env['DEVTOOL_EXECUTION_ENVIRONMENT'], env=env)
    assert plan.effective == 'native-termux'
    assert plan.current == 'native-termux'


def test_a04_unknown_android_host_must_not_fake_termux(monkeypatch, tmp_path):
    import devtool.host_context as host_context
    # Mock the *complete* interpreter identity. Merely replacing executable
    # leaves sys.base_prefix pointing at the real Termux runtime on devices,
    # which rightfully identifies native-termux and invalidates this negative.
    monkeypatch.setattr(sys, 'executable', '/usr/bin/python3')
    monkeypatch.setattr(sys, 'base_prefix', '/data/data/com.termux/files/usr')
    assert _fixture_host_selector() == ('native-termux', '/data/data/com.termux/files/usr')
    monkeypatch.setattr(sys, 'base_prefix', '/usr')
    monkeypatch.setattr(host_context.platform, 'system', lambda: 'Android')
    monkeypatch.setattr(host_context.sys, 'platform', 'android')
    monkeypatch.delenv('TERMUX_VERSION', raising=False)
    monkeypatch.delenv('PREFIX', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'sanitized-home'))
    assert detect_current_environment() == 'host-android'
    import pytest
    with pytest.raises(RuntimeError, match='unknown substrate: host-android'):
        _env(tmp_path)


def test_a04_child_cli_identifies_fixture_substrate(tmp_path):
    repo = tmp_path / 'consumer'
    repo.mkdir()
    env = _env(repo)
    child = subprocess.run([
        sys.executable, '-c',
        'from devtool.execution_environments import detect_current_environment; print(detect_current_environment())',
    ], cwd=repo, env=env, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert child.stdout.strip() == _fixture_host_selector()[0]
