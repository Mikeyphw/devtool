from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from devtool.cli import _prepare_specs_for_execution, _workflows
from devtool.config import load_project_config, resolve_config_dict
from devtool.execution import apply_matrix
from devtool.execution_environments import resolve_execution_plan
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import build_specs, build_validation_specs, run_specs, runner_capabilities
from devtool.workflow_graph import WorkflowGraphError, graph_from_workflow
from devtool.validation_sessions import annotate_specs, phase_records
from devtool.workflow_runtime import (
    WorkflowRuntimeError,
    build_workflow_specs,
    plan_selected_workflow,
    workflow_runner_kinds,
)


def _base_args(**overrides):
    values = dict(
        matrix="",
        matrix_mode="rid",
        execution_environment="auto",
        chroot="",
        host=False,
        proot_distro="",
        no_auto_chroot=True,
        instrumented=False,
        dry_run=True,
        json=False,
        machine="",
        allow_shared_storage_build=False,
        shared_storage_policy="allow",
    )
    values.update(overrides)
    return argparse.Namespace(**values)


class WorkflowRepo:
    def __init__(self, root: Path):
        self.root = root

    def write(self, config_text: str) -> ProjectConfig:
        (self.root / ".devtool.toml").write_text(config_text, encoding="utf-8")
        return load_project_config(str(self.root), None)


class MP03GraphConfigTests(unittest.TestCase):
    def test_plain_string_workflow_preserves_sequential_semantics(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = WorkflowRepo(root)
            cfg = repo.write(
                '''
[project]
runner = "python"
default_target = "api"

[jobs.first]
runner = "python"
command = ["python3", "-c", "print('first')"]

[jobs.second]
runner = "python"
command = ["python3", "-c", "print('second')"]

[targets.api]
runner = "python"
platform = "server"
root = "."

[targets.api.workflows]
build = ["job:first", "job:second", "runner:python#build"]
'''
            )
            graph = plan_selected_workflow(cfg, "build")
            nodes = graph.nodes
            self.assertEqual(nodes[0].depends_on, ())
            self.assertEqual(nodes[1].depends_on, (nodes[0].key,))
            self.assertEqual(nodes[2].depends_on, (nodes[1].key,))
            self.assertEqual([node.key for node in graph.topological_order()], [node.key for node in nodes])

    def test_inline_steps_express_branch_and_join(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = WorkflowRepo(root)
            cfg = repo.write(
                '''
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
            )
            graph = plan_selected_workflow(cfg, "build")
            by_id = {node.ref.step_id: node for node in graph.nodes}
            self.assertEqual(by_id["generate"].depends_on, ())
            self.assertEqual(by_id["lint"].depends_on, (by_id["generate"].key,))
            self.assertEqual(by_id["test"].depends_on, (by_id["generate"].key,))
            self.assertEqual(set(by_id["package"].depends_on), {by_id["lint"].key, by_id["test"].key})
            self.assertEqual(
                [node.ref.step_id for node in graph.topological_order()],
                ["generate", "lint", "test", "package"],
            )
            self.assertEqual(
                graph.dependency_closure([by_id["package"].key]),
                frozenset(node.key for node in graph.nodes),
            )
            self.assertEqual(
                graph.dependent_closure([by_id["generate"].key]),
                frozenset(node.key for node in graph.nodes),
            )

    def test_unknown_dependency_fails_during_config_load(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = WorkflowRepo(root)
            with self.assertRaisesRegex(SystemExit, "unknown step id 'missing'"):
                repo.write(
                    '''
[project]
runner = "python"
default_target = "api"
[jobs.a]
runner = "python"
command = ["python3", "-c", "pass"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = [{ id = "a", ref = "job:a", depends_on = ["missing"] }]
'''
                )

    def test_dependency_cycle_fails_during_config_load(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = WorkflowRepo(root)
            with self.assertRaisesRegex(SystemExit, "dependency cycle"):
                repo.write(
                    '''
[project]
runner = "python"
default_target = "api"
[jobs.a]
runner = "python"
command = ["python3", "-c", "pass"]
[jobs.b]
runner = "python"
command = ["python3", "-c", "pass"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = [
 { id = "a", ref = "job:a", depends_on = ["b"] },
 { id = "b", ref = "job:b", depends_on = ["a"] },
]
'''
                )

    def test_depends_on_requires_stable_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = WorkflowRepo(root)
            with self.assertRaisesRegex(SystemExit, "has no id"):
                repo.write(
                    '''
[project]
runner = "python"
default_target = "api"
[jobs.a]
runner = "python"
command = ["python3", "-c", "pass"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = [{ ref = "job:a", depends_on = [] }]
'''
                )


class MP04RunnerCompositionTests(unittest.TestCase):
    def test_node_action_job_and_python_runner_compose_without_runner_cross_talk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            tests = root / "tests"
            web.mkdir()
            tests.mkdir()
            (web / "package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"console.log('built')\""}}),
                encoding="utf-8",
            )
            (tests / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"

[jobs.web-build]
runner = "node"
root = "web"
action = "build"

[targets.api]
runner = "python"
platform = "server"
root = "."

[targets.api.workflows]
test = ["job:web-build", "runner:python#test"]
'''
            )
            specs = build_specs(cfg, "test")
            self.assertGreaterEqual(len(specs), 2)
            self.assertEqual(specs[0].argv[:3], ["npm", "run", "build"])
            self.assertEqual(specs[0].metadata["runner"], "node")
            python_specs = [spec for spec in specs if spec.metadata.get("workflow_runner") == "python"]
            self.assertTrue(python_specs)
            self.assertTrue(any("pytest" in " ".join(spec.argv) for spec in python_specs))
            self.assertEqual(workflow_runner_kinds(cfg, "test"), ("node", "python"))
            self.assertEqual(specs[1].metadata["workflow_depends_on"], [specs[0].metadata["workflow_node_key"]])
            self.assertTrue(specs[0].metadata["validation_phase_key"].startswith("workflow:"))
            self.assertEqual(
                specs[1].metadata["validation_depends_on"],
                [specs[0].metadata["validation_phase_key"]],
            )
            annotated = annotate_specs(specs)
            records = phase_records(annotated)
            self.assertEqual(records[1].depends_on, [records[0].key])

    def test_structured_and_shell_jobs_preserve_explicit_execution_modes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[jobs.argv]
runner = "node"
root = "."
command = ["npm", "--version"]
execution_environment = "desktop-linux"
inputs = ["package.json"]
outputs = ["generated/**"]
[jobs.shell]
runner = "python"
root = "."
shell_command = "printf shell-ok"
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = ["job:argv", "job:shell"]
'''
            )
            specs = build_specs(cfg, "build")
            self.assertEqual(specs[0].argv, ["npm", "--version"])
            self.assertEqual(specs[0].metadata["progress_parser"], "npm")
            self.assertEqual(specs[0].metadata["workflow_inputs"], ["package.json"])
            self.assertEqual(specs[0].metadata["workflow_outputs"], ["generated/**"])
            self.assertEqual(specs[0].metadata["workflow_execution_environment"], "desktop-linux")
            self.assertTrue(specs[1].metadata["explicit_shell"])
            self.assertIn("printf shell-ok", specs[1].argv[-1])

    def test_target_reference_is_expanded_by_mp05_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "command"
default_target = "api"
[targets.protocol]
runner = "command"
build_command = "printf protocol"
root = "."
[targets.api]
runner = "command"
root = "."
[targets.api.workflows]
build = ["target:protocol#build"]
'''
            )
            graph = plan_selected_workflow(cfg, "build")
            self.assertEqual(graph.nodes[0].target, "protocol")
            self.assertEqual(graph.nodes[0].ref.kind, "runner")
            specs = build_workflow_specs(cfg, "build")
            self.assertEqual(specs[0].metadata["workflow_target"], "protocol")

    def test_unconfigured_actions_keep_legacy_runner_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "tests").mkdir()
            (root / "tests" / "test_x.py").write_text("def test_x(): assert True\n")
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[targets.api]
runner = "python"
root = "."
'''
            )
            specs = build_specs(cfg, "test")
            self.assertTrue(specs)
            self.assertFalse(any(spec.metadata.get("workflow_graph") for spec in specs))

    def test_explicit_validate_workflow_is_authoritative_and_skip_flags_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[jobs.check]
runner = "python"
command = ["python3", "-c", "print('check')"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
validate = ["job:check"]
'''
            )
            self.assertEqual(build_validation_specs(cfg)[0].argv[-1], "print('check')")
            with self.assertRaisesRegex(SystemExit, "cannot rewrite an explicit target workflow"):
                build_validation_specs(cfg, skip_test=True)
            self.assertIn("validate", runner_capabilities(cfg).supported())

    def test_ts00_pytest_selection_flows_into_python_workflow_node(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_a.py").write_text("def test_a(): assert True\ndef test_b(): assert True\n")
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
test = ["runner:python#test"]
'''
            )
            selection = dict(cfg.runner_options.get("test_selection", {}))
            selection["pytest_tests"] = ["tests/test_a.py::test_a"]
            selection["pytest_include_defaults"] = False
            selection["pytest_source"] = "explicit"
            cfg.runner_options["test_selection"] = selection
            specs = build_specs(cfg, "test")
            pytest_specs = [spec for spec in specs if "pytest" in " ".join(spec.argv)]
            self.assertTrue(pytest_specs)
            joined = " ".join(pytest_specs[-1].argv)
            self.assertIn("tests/test_a.py::test_a", joined)
            self.assertNotIn("tests/test_a.py::test_b", joined)


class MP03ExecutionStateTests(unittest.TestCase):
    def _config(self, root: Path) -> ProjectConfig:
        return ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root))

    @staticmethod
    def _spec(name: str, code: str, key: str, deps=()):
        return CommandSpec(
            name,
            [sys.executable, "-c", code],
            metadata={
                "workflow_graph": True,
                "workflow_target": "x",
                "workflow_name": "build",
                "workflow_node_key": key,
                "workflow_depends_on": list(deps),
                "workflow_node_spec_index": 1,
                "workflow_node_spec_total": 1,
                "workflow_runner": "python",
            },
        )

    def test_failed_node_blocks_dependents_but_continue_mode_runs_independent_branch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            specs = [
                self._spec("fail", "raise SystemExit(7)", "a"),
                self._spec("blocked", "print('SHOULD_NOT_RUN')", "b", ("a",)),
                self._spec("independent", "print('independent-ok')", "c"),
            ]
            results, _ = run_specs(self._config(root), specs, verbose=False, continue_on_error=True)
            by_name = {result.name: result for result in results}
            self.assertEqual(by_name["fail"].returncode, 7)
            self.assertTrue(by_name["blocked"].skipped)
            self.assertEqual(by_name["blocked"].metadata["workflow_node_state"], "blocked")
            self.assertNotIn("SHOULD_NOT_RUN", by_name["blocked"].output)
            self.assertEqual(by_name["independent"].returncode, 0)
            self.assertIn("independent-ok", by_name["independent"].output)

    def test_fail_fast_marks_independent_nodes_skipped_and_dependents_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            specs = [
                self._spec("fail", "raise SystemExit(2)", "a"),
                self._spec("blocked", "print('no')", "b", ("a",)),
                self._spec("independent", "print('no')", "c"),
            ]
            results, _ = run_specs(self._config(root), specs, verbose=False, continue_on_error=False)
            by_name = {result.name: result for result in results}
            self.assertEqual(by_name["blocked"].metadata["workflow_node_state"], "blocked")
            self.assertEqual(by_name["independent"].metadata["workflow_node_state"], "skipped")
            self.assertTrue(by_name["independent"].skipped)

    def test_multi_spec_node_only_passes_after_last_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            specs = []
            for index in (1, 2):
                spec = self._spec(f"part{index}", "print('ok')", "a")
                spec.metadata["workflow_node_spec_index"] = index
                spec.metadata["workflow_node_spec_total"] = 2
                specs.append(spec)
            specs.append(self._spec("dependent", "print('dep')", "b", ("a",)))
            results, _ = run_specs(self._config(root), specs, verbose=False, continue_on_error=True)
            self.assertEqual(results[0].metadata["workflow_node_state"], "running")
            self.assertEqual(results[1].metadata["workflow_node_state"], "passed")
            self.assertEqual(results[2].returncode, 0)


class MP03MachineAndExecutionPreparationTests(unittest.TestCase):
    def test_resolved_config_keeps_legacy_workflow_list_and_adds_graph_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[jobs.a]
runner = "python"
command = ["python3", "-c", "pass"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = [{ id = "a", ref = "job:a", depends_on = [] }]
'''
            )
            payload = resolve_config_dict(cfg)
            target = payload["targets"]["api"]
            self.assertEqual(target["workflows"]["build"], ["job:a"])
            self.assertEqual(target["workflow_graphs"]["build"]["graph"]["nodes"][0]["id"], "a")

    def test_workflows_json_is_schema_v2_and_contains_dag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = WorkflowRepo(root).write(
                '''
[project]
runner = "python"
default_target = "api"
[jobs.a]
runner = "python"
command = ["python3", "-c", "pass"]
[targets.api]
runner = "python"
root = "."
[targets.api.workflows]
build = ["job:a"]
'''
            )
            out = io.StringIO()
            with redirect_stdout(out):
                _workflows(cfg, as_json=True)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["schema_version"], 3)
            workflow = payload["workflows"]["api"]["build"]
            self.assertIn("graph", workflow)
            self.assertIn("expanded_graph", workflow)
            self.assertEqual(workflow["expanded_graph"]["targets"], ["api"])


    def test_matrix_expansion_isolates_workflow_node_and_validation_identity_per_lane(self):
        spec = CommandSpec(
            "test", [sys.executable, "-c", "pass"],
            metadata={
                "workflow_graph": True,
                "workflow_node_key": "api.test.unit",
                "workflow_depends_on": ["api.test.generate"],
                "validation_phase_key": "workflow:api.test.unit:part-1",
                "validation_depends_on": ["workflow:api.test.generate:part-1"],
            },
        )
        expanded = apply_matrix([spec], ["linux-x64", "linux-arm64"], mode="rid")
        self.assertEqual(len(expanded), 2)
        keys = [item.metadata["workflow_node_key"] for item in expanded]
        self.assertEqual(len(set(keys)), 2)
        self.assertTrue(keys[0].endswith("[matrix:rid:linux-x64]"))
        self.assertTrue(keys[1].endswith("[matrix:rid:linux-arm64]"))
        self.assertEqual(
            expanded[0].metadata["workflow_depends_on"],
            ["api.test.generate[matrix:rid:linux-x64]"],
        )
        self.assertEqual(
            expanded[1].metadata["validation_depends_on"],
            ["workflow:api.test.generate:part-1[matrix:rid:linux-arm64]"],
        )

    def test_synthetic_blocked_node_publishes_terminal_progress_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            specs = [
                MP03ExecutionStateTests._spec("fail", "raise SystemExit(2)", "a"),
                MP03ExecutionStateTests._spec("blocked", "print('no')", "b", ("a",)),
            ]
            snapshots = []
            results, _ = run_specs(
                ProjectConfig(repo_root=root, kind="command", runner="command", project_root=str(root)),
                specs,
                verbose=False,
                continue_on_error=True,
                progress_callback=lambda payload: snapshots.append(dict(payload)),
            )
            blocked = results[-1]
            self.assertEqual(blocked.metadata["workflow_node_state"], "blocked")
            self.assertIn("workflow_node_statuses", blocked.metadata)
            self.assertTrue(any(
                item.get("workflow_node_key") == "b" and item.get("workflow_node_state") == "blocked"
                for item in snapshots
            ))

    def test_execution_preparation_scopes_config_per_workflow_spec(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = ProjectConfig(
                repo_root=root,
                kind="python",
                runner="python",
                project_root=str(root),
                execution_environment="desktop-linux",
            )
            specs = [
                CommandSpec(
                    "one", [sys.executable, "-c", "pass"], cwd=str(root),
                    metadata={"workflow_graph": True, "workflow_execution_environment": "native-termux", "workflow_runner_options": {}},
                ),
                CommandSpec(
                    "two", [sys.executable, "-c", "pass"], cwd=str(root),
                    metadata={"workflow_graph": True, "workflow_execution_environment": "desktop-linux", "workflow_runner_options": {}},
                ),
            ]
            seen = []

            def fake_plan(scoped_config, _args):
                seen.append(scoped_config.execution_environment)
                return resolve_execution_plan(requested="desktop-linux", configured="desktop-linux")

            with mock.patch("devtool.cli._execution_plan_for_args", side_effect=fake_plan), \
                 mock.patch("devtool.cli.apply_toolchain_wrapper", side_effect=lambda _c, values: values), \
                 mock.patch("devtool.cli.preflight_execution") as preflight:
                preflight.return_value.failures.return_value = ()
                prepared = _prepare_specs_for_execution(cfg, specs, _base_args())
            self.assertEqual(len(prepared), 2)
            self.assertEqual(seen, ["native-termux", "desktop-linux"])


if __name__ == "__main__":
    unittest.main()
