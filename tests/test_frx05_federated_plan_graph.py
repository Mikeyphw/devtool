from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.federation_plan import FederationPlanError, build_federated_plan


def _child(root: Path, workflow: str, *, target: str = "app", two_targets: bool = False) -> None:
    text = f'''[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "{target}"\n\n[targets.{target}]\nrunner = "command"\nroot = "."\n\n[jobs.one]\nrunner = "command"\ncommand = ["echo", "one"]\n\n[jobs.two]\nrunner = "command"\ncommand = ["echo", "two"]\n\n[targets.{target}.workflows]\n{workflow} = ["job:one", "job:two"]\n'''
    if two_targets:
        text = text.replace(f'[workspace]\ndefault_target = "{target}"\n\n', '')
        text += '\n[targets.other]\nrunner = "command"\nroot = "."\n'
    (root / '.devtool.toml').write_text(text, encoding='utf-8')


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / 'missing-global.toml')}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _parent(tmp_path: Path, federation_body: str) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    parent = tmp_path / 'parent'; backend = tmp_path / 'backend'; android = tmp_path / 'android'
    parent.mkdir(); backend.mkdir(); android.mkdir()
    _child(backend, 'prepare')
    _child(android, 'release')
    (parent / '.devtool.toml').write_text(f'''[project]\nrunner="command"\n\n[federation]\nschema=1\n\n[federation.repositories.backend]\npath="../backend"\n\n[federation.repositories.android]\npath="../android"\n\n{federation_body}\n''', encoding='utf-8')
    return parent, backend, android


def test_shorthand_depends_on_builds_parallel_children_and_aggregate(tmp_path: Path) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\ndepends_on=["backend::prepare", "android::release"]''')
    graph = build_federated_plan(_load(parent), 'release')
    assert graph.repositories == ('backend', 'android')
    assert graph.leaves[0].key == '@federation::release'
    aggregate = graph.by_key['@federation::release']
    assert len(aggregate.depends_on) == 2
    assert any(key.startswith('backend::') for key in aggregate.depends_on)
    assert any(key.startswith('android::') for key in aggregate.depends_on)
    assert all(node.synthetic or 'repository_identity' in node.metadata for node in graph.nodes)


def test_structured_steps_create_cross_repository_dependency(tmp_path: Path) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\nsteps=[\n {id="prepare", ref="backend::prepare"},\n {id="android", ref="android::release", depends_on=["prepare"]},\n]''')
    graph = build_federated_plan(_load(parent), 'release')
    backend_leaves = [n.key for n in graph.nodes if n.repository == 'backend' and n.key not in {d for x in graph.nodes if x.repository == 'backend' for d in x.depends_on}]
    android_roots = [n for n in graph.nodes if n.repository == 'android' and not any(d.startswith('android::') for d in n.depends_on)]
    assert android_roots
    assert any(dep.startswith('backend::') for dep in android_roots[0].depends_on)
    assert graph.topological_order()[-1].key == '@federation::release'


def test_structured_diamond_is_deterministic(tmp_path: Path) -> None:
    parent = tmp_path / 'parent'; a=tmp_path/'a'; b=tmp_path/'b'; c=tmp_path/'c'; d=tmp_path/'d'
    for root, wf in ((a,'one'),(b,'two'),(c,'three'),(d,'four')):
        root.mkdir(parents=True, exist_ok=True); _child(root, wf)
    parent.mkdir()
    (parent/'.devtool.toml').write_text('''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.a]\npath="../a"\n[federation.repositories.b]\npath="../b"\n[federation.repositories.c]\npath="../c"\n[federation.repositories.d]\npath="../d"\n[federation.workflows.all]\nsteps=[\n{id="a",ref="a::one"},\n{id="b",ref="b::two",depends_on=["a"]},\n{id="c",ref="c::three",depends_on=["a"]},\n{id="d",ref="d::four",depends_on=["b","c"]},\n]\n''', encoding='utf-8')
    graph = build_federated_plan(_load(parent), 'all')
    first = graph.as_dict()['order']
    second = build_federated_plan(_load(parent), 'all').as_dict()['order']
    assert first == second
    assert first[-1] == '@federation::all'


def test_cross_repository_cycle_is_rejected_at_plan_time(tmp_path: Path) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\nsteps=[\n {id="prepare", ref="backend::prepare", depends_on=["android"]},\n {id="android", ref="android::release", depends_on=["prepare"]},\n]''')
    with pytest.raises(FederationPlanError, match='cycle'):
        build_federated_plan(_load(parent), 'release')


def test_missing_child_workflow_fails_closed(tmp_path: Path) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\ndepends_on=["backend::missing"]''')
    with pytest.raises(FederationPlanError, match='has no workflow'):
        build_federated_plan(_load(parent), 'release')


def test_ambiguous_child_target_fails_closed(tmp_path: Path) -> None:
    parent, backend, _ = _parent(tmp_path, '''[federation.workflows.release]\ndepends_on=["backend::prepare"]''')
    _child(backend, 'prepare', two_targets=True)
    with pytest.raises(FederationPlanError, match='no unambiguous selected target'):
        build_federated_plan(_load(parent), 'release')


def test_parser_rejects_unknown_repo_and_unknown_step_dependency(tmp_path: Path) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\ndepends_on=["missing::build"]''')
    with pytest.raises(SystemExit, match='unknown federation repository'):
        _load(parent)

    parent2, _, _ = _parent(tmp_path / 'two', '''[federation.workflows.release]\nsteps=[{id="a",ref="backend::prepare",depends_on=["missing"]}]''')
    with pytest.raises(SystemExit, match='unknown step id'):
        _load(parent2)


def test_cli_plan_json_and_planning_never_executes_child_command(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    parent, _, _ = _parent(tmp_path, '''[federation.workflows.release]\ndepends_on=["backend::prepare", "android::release"]''')
    import subprocess
    real_run = subprocess.run
    executed: list[object] = []
    def guarded(*args, **kwargs):
        command = args[0] if args else kwargs.get('args')
        if isinstance(command, (list, tuple)) and command and command[0] == 'echo':
            executed.append(command)
            raise AssertionError('child command executed during federation plan')
        return real_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, 'run', guarded)
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent / 'missing-global.toml')}, clear=False):
        assert cli.main(['--repo', str(parent), 'federation', 'plan', 'release', '--json']) == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get('data', payload)
    assert payload['workflow'] == 'release'
    assert payload['order'][-1] == '@federation::release'
    assert executed == []
