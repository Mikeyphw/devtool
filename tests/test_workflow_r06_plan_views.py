from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from devtool.cli import _plan, build_parser
from devtool.config import load_project_config
from devtool.workflow_plan_views import (
    render_plan_ascii,
    render_plan_dot,
    render_plan_mermaid,
    static_dependency_analysis,
)
from devtool.workflow_runtime import plan_selected_workflow


DECLARATION = '''
[project]
runner = "python"
default_target = "api"
[jobs.generate]
runner = "python"
command = ["python3", "-c", "print('generate')"]
[jobs.lint]
runner = "python"
command = ["python3", "-c", "print('lint')"]
[jobs.test]
runner = "python"
command = ["python3", "-c", "print('test')"]
[jobs.package]
runner = "python"
command = ["python3", "-c", "print('package')"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = [
 { id = "generate", ref = "job:generate", depends_on = [] },
 { id = "lint", ref = "job:lint", depends_on = ["generate"] },
 { id = "test", ref = "job:test", depends_on = ["generate"] },
 { id = "package", ref = "job:package", depends_on = ["lint", "test"] },
]
'''


def _config(tmp_path: Path):
    (tmp_path / '.devtool.toml').write_text(DECLARATION, encoding='utf-8')
    return load_project_config(str(tmp_path), None)


def _plan_text(config, *args):
    parsed = build_parser().parse_args(['plan', '--target', 'api', 'build', *args])
    output = io.StringIO()
    with redirect_stdout(output):
        assert _plan(config, parsed) == 0
    return output.getvalue()


def test_mermaid_graph_keeps_declared_dependencies_only(tmp_path):
    config = _config(tmp_path)
    graph = plan_selected_workflow(config, 'build')
    output = _plan_text(config, '--mermaid', '--workflow-parallel')
    assert output.startswith('flowchart TD\n')
    assert output.count('-->') == 4
    assert 'n0 --> n1' in output and 'n0 --> n2' in output
    assert 'n1 --> n3' in output and 'n2 --> n3' in output
    assert 'n1 --> n2' not in output  # serialized writes are not dependency edges
    for node in graph.nodes:
        assert node.key in output


def test_ascii_is_lossless_and_conservative_on_unknown_writes(tmp_path):
    config = _config(tmp_path)
    output = _plan_text(config, '--ascii', '--workflow-parallel')
    assert 'Longest chain: 2 edges (not time)' in output
    assert 'exclusive: scope unknown' in output
    assert 'write set is unknown' in output
    assert 'api.build.package' in output
    graph = plan_selected_workflow(config, 'build')
    for node in graph.nodes:
        assert node.key in output
    # Output is sized for 40 columns, including wrapped reasons and long IDs.
    assert all(len(line) <= 40 for line in output.splitlines())


def test_json_static_analysis_declares_no_runtime_qualification(tmp_path):
    config = _config(tmp_path)
    output = _plan_text(config, '--json')
    payload = json.loads(output)
    analysis = payload['analysis']
    assert payload['graph'] is not None
    assert analysis['runtime_status_available'] is False
    assert analysis['timing_estimates_available'] is False
    assert analysis['critical_dependency_hops'] == 2
    assert analysis['critical_dependency_path'] == [
        'api.build.generate', 'api.build.lint', 'api.build.package'
    ]
    blockers = {n['id']: n for n in analysis['node_blockers']}
    assert blockers['api.build.package']['depends_on'] == ['api.build.lint', 'api.build.test']
    assert blockers['api.build.package']['parallel_eligible'] is False
    assert 'unknown' in blockers['api.build.package']['scope_reason']
    assert analysis['schedule_source'] == 'canonical_workflow_scheduler'


def test_new_output_modes_conflict_fail_closed(tmp_path):
    config = _config(tmp_path)
    for options in [('--ascii', '--mermaid'), ('--ascii', '--json'), ('--mermaid', '--dot')]:
        with pytest.raises(SystemExit, match='Choose only one'):
            _plan_text(config, *options)
    # Preserve historical precedence of --dot with --json.
    assert _plan_text(config, '--dot', '--json').startswith('digraph devtool_workflow')


def test_static_analysis_mirrors_real_graph_and_never_labels_ci_success(tmp_path):
    config = _config(tmp_path)
    graph = plan_selected_workflow(config, 'build')
    analysis = static_dependency_analysis(graph, None)
    assert analysis['schedule_source'] == 'not_available'
    assert analysis['critical_dependency_hops'] == 2
    assert all(n['scheduled_wave'] is None for n in analysis['node_blockers'])
    assert not any('success' in k or 'qualified' in k for k in analysis)
    assert 'Schedule: unavailable' in render_plan_ascii(graph, None)
    assert '-->' in render_plan_mermaid(graph, None)


def test_dot_escapes_untrusted_keys_and_labels():
    ref = SimpleNamespace(resolution_key='', resolution_scope='', name='', kind='job')
    node = SimpleNamespace(key='node"; evil-> "x', target='target"', label='label\nnew',
                           runner='', ref=ref, depends_on=('root"',))
    graph = SimpleNamespace(nodes=[node])
    dot = render_plan_dot(graph, None)
    assert '\\"' in dot
    assert 'label\\nnew' in dot
    assert sum(1 for line in dot.splitlines() if ' -> ' in line) == 1
    assert '"node\\"; evil-> \\"x"' in dot


def test_cli_help_advertises_r06_views():
    parser = build_parser()
    args = parser.parse_args(['plan', '--ascii', 'build'])
    assert args.ascii and not args.mermaid
    args = parser.parse_args(['plan', '--mermaid', 'build'])
    assert args.mermaid and not args.ascii
