from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from devtool.cli import _groups, _targets, _workspace_target_names_for_args, build_parser, main
from devtool.config import load_project_config, resolve_config_dict
from devtool.models import ProjectConfig
from devtool.runners import build_specs
from devtool.workflow_runtime import (
    WorkflowRuntimeError,
    build_workspace_specs,
    build_workspace_validation_specs,
    plan_selected_workflow,
    plan_workspace_targets,
)
from devtool.workspace import expand_workspace_group, resolve_workspace_targets


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def config(self, text: str, *, target: str = "", ambiguous: bool = False) -> ProjectConfig:
        (self.root / ".devtool.toml").write_text(text, encoding="utf-8")
        return load_project_config(
            str(self.root),
            None,
            target_name=target,
            allow_ambiguous_target=ambiguous,
        )


class WorkspaceGroupTests(unittest.TestCase):
    def test_workspace_default_target_is_canonical_and_legacy_equal_value_is_allowed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
default_target = "api"
[workspace]
default_target = "api"
[targets.api]
runner = "python"
root = "."
[targets.web]
runner = "node"
root = "."
'''
            )
            self.assertEqual(cfg.selected_target, "api")
            self.assertEqual(cfg.workspace.default_target, "api")

    def test_conflicting_workspace_and_legacy_defaults_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(SystemExit, "conflicts with legacy"):
                Repo(root).config(
                    '''
[project]
runner = "python"
default_target = "api"
[workspace]
default_target = "web"
[targets.api]
runner = "python"
[targets.web]
runner = "node"
'''
                )

    def test_nested_groups_wildcard_and_dedup_preserve_target_declaration_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
[workspace.groups]
mobile = ["android", "tv"]
frontend = ["web", "@mobile"]
all = ["*"]
release = ["server", "@frontend", "android"]
[targets.server]
runner = "python"
[targets.web]
runner = "node"
[targets.android]
runner = "android"
[targets.tv]
runner = "android"
''',
                ambiguous=True,
            )
            self.assertEqual(expand_workspace_group(cfg.workspace, cfg.targets, "mobile"), ("android", "tv"))
            self.assertEqual(expand_workspace_group(cfg.workspace, cfg.targets, "frontend"), ("web", "android", "tv"))
            self.assertEqual(expand_workspace_group(cfg.workspace, cfg.targets, "release"), ("server", "web", "android", "tv"))
            self.assertEqual(expand_workspace_group(cfg.workspace, cfg.targets, "all"), ("server", "web", "android", "tv"))
            self.assertEqual(
                resolve_workspace_targets(cfg.workspace, cfg.targets, groups=["mobile", "frontend"]),
                ("android", "tv", "web"),
            )

    def test_group_cycle_and_unknown_target_fail_during_config_load(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(SystemExit, "group cycle detected"):
                Repo(root).config(
                    '''
[project]
runner = "python"
[workspace.groups]
a = ["@b"]
b = ["@a"]
[targets.api]
runner = "python"
''', ambiguous=True
                )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaisesRegex(SystemExit, "unknown target 'missing'"):
                Repo(root).config(
                    '''
[project]
runner = "python"
[workspace.groups]
broken = ["missing"]
[targets.api]
runner = "python"
''', ambiguous=True
                )

    def test_group_and_all_target_cli_scope_conflicts_are_explicit(self):
        parser = build_parser()
        args = parser.parse_args(["--group", "release", "validate"])
        self.assertEqual(args.group, ["release"])
        args = parser.parse_args(["--all-targets", "build"])
        self.assertTrue(args.all_targets)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
[workspace.groups]
release = ["api"]
[targets.api]
runner = "python"
''', ambiguous=True
            )
            namespace = argparse.Namespace(group=["release"], all_targets=False, target="api", matrix="", variant="")
            with self.assertRaises(SystemExit):
                _workspace_target_names_for_args(cfg, namespace, parser)

    def test_targets_and_groups_machine_output_expose_membership(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
[workspace]
default_target = "api"
[workspace.groups]
release = ["api", "web"]
[targets.api]
runner = "python"
[targets.web]
runner = "node"
'''
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                _targets(cfg, as_json=True)
            payload = json.loads(stream.getvalue())
            api = next(item for item in payload["targets"] if item["name"] == "api")
            self.assertEqual(api["groups"], ["release"])
            self.assertTrue(api["default"])
            self.assertEqual(payload["workspace"]["groups"]["release"]["targets"], ["api", "web"])

            stream = io.StringIO()
            with redirect_stdout(stream):
                _groups(cfg, as_json=True)
            groups = json.loads(stream.getvalue())
            self.assertEqual(groups["workspace"]["groups"]["release"]["members"], ["api", "web"])


class CrossTargetWorkflowTests(unittest.TestCase):
    def test_cross_target_reference_executes_referenced_target_fallback_runner_at_its_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            web.mkdir()
            (web / "package.json").write_text(json.dumps({"scripts": {"build": "node -e \\\"console.log('web')\\\""}}), encoding="utf-8")
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
[workspace]
default_target = "api"
[jobs.finish]
runner = "python"
command = ["python3", "-c", "print('finish')"]
[targets.web]
runner = "node"
platform = "web"
root = "web"
[targets.api]
runner = "python"
platform = "server"
root = "."
[targets.api.workflows]
build = ["target:web#build", "job:finish"]
'''
            )
            graph = plan_selected_workflow(cfg, "build")
            self.assertEqual([node.target for node in graph.topological_order()], ["web", "api"])
            specs = build_specs(cfg, "build")
            self.assertEqual(specs[0].argv[:3], ["npm", "run", "build"])
            self.assertEqual(Path(specs[0].cwd), web.resolve())
            self.assertEqual(specs[0].metadata["workflow_target"], "web")
            self.assertEqual(specs[1].metadata["workflow_target"], "api")
            self.assertEqual(specs[1].metadata["validation_depends_on"], [specs[0].metadata["validation_phase_key"]])

    def test_cross_target_nested_workflow_expands_and_preserves_dependencies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "api"
[jobs.schema]
runner = "command"
command = ["printf", "schema"]
[jobs.api]
runner = "command"
command = ["printf", "api"]
[targets.protocol]
runner = "command"
[targets.protocol.workflows]
build = ["job:schema"]
[targets.api]
runner = "command"
[targets.api.workflows]
build = ["target:protocol#build", "job:api"]
'''
            )
            specs = build_specs(cfg, "build")
            self.assertEqual([spec.argv[-1] for spec in specs], ["schema", "api"])
            self.assertEqual(specs[1].metadata["validation_depends_on"], [specs[0].metadata["validation_phase_key"]])

    def test_shared_cross_target_prerequisite_is_deduplicated_across_workspace_roots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace.groups]
release = ["api", "web"]
[jobs.protocol]
runner = "command"
command = ["printf", "protocol"]
[jobs.api]
runner = "command"
command = ["printf", "api"]
[jobs.web]
runner = "command"
command = ["printf", "web"]
[targets.protocol]
runner = "command"
[targets.protocol.workflows]
build = ["job:protocol"]
[targets.api]
runner = "command"
[targets.api.workflows]
build = ["target:protocol#build", "job:api"]
[targets.web]
runner = "command"
[targets.web.workflows]
build = ["target:protocol#build", "job:web"]
''', ambiguous=True
            )
            graph = plan_workspace_targets(cfg, ["api", "web"], "build")
            keys = [node.key for node in graph.nodes]
            self.assertEqual(sum(key.startswith("protocol.build.") for key in keys), 1)
            specs = build_workspace_specs(cfg, ["api", "web"], "build")
            self.assertEqual(sum(spec.argv[-1] == "protocol" for spec in specs), 1)
            protocol = next(spec for spec in specs if spec.argv[-1] == "protocol")
            api = next(spec for spec in specs if spec.argv[-1] == "api")
            web = next(spec for spec in specs if spec.argv[-1] == "web")
            self.assertEqual(api.metadata["validation_depends_on"], [protocol.metadata["validation_phase_key"]])
            self.assertEqual(web.metadata["validation_depends_on"], [protocol.metadata["validation_phase_key"]])
            self.assertEqual(api.metadata["workspace_targets"], ["api", "web"])

    def test_cross_target_action_cycle_is_rejected_with_target_action_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".devtool.toml").write_text(
                '''
[project]
runner = "command"
[workspace]
default_target = "a"
[targets.a]
runner = "command"
[targets.a.workflows]
build = ["target:b#build"]
[targets.b]
runner = "command"
[targets.b.workflows]
build = ["target:a#build"]
''',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, r"a#build -> b#build -> a#build"):
                load_project_config(str(root), None, target_name="a")


    def test_referenced_target_preserves_its_environment_execution_backend_and_machine_graph(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "child").mkdir()
            cfg = Repo(root).config(
                '''
[project]
runner = "command"
[workspace]
default_target = "parent"
[targets.child]
runner = "command"
root = "child"
build_command = "python3 -c pass"
execution_environment = "desktop-linux"
[targets.child.env]
CHILD_ONLY = "yes"
[targets.parent]
runner = "command"
root = "."
[targets.parent.workflows]
build = ["target:child#build"]
'''
            )
            specs = build_workspace_specs(cfg, ["parent"], "build")
            self.assertEqual(len(specs), 1)
            spec = specs[0]
            self.assertEqual(Path(spec.cwd), (root / "child").resolve())
            self.assertEqual(spec.env.get("CHILD_ONLY"), "yes")
            self.assertEqual(spec.metadata["workflow_execution_environment"], "desktop-linux")

            resolved = resolve_config_dict(cfg)
            self.assertEqual(resolved["workspace"]["default_target"], "parent")
            expanded = resolved["targets"]["parent"]["expanded_workflow_graphs"]["build"]
            self.assertEqual(expanded["targets"], ["parent"])
            self.assertEqual(expanded["nodes"][0]["target"], "child")

    def test_group_scope_runs_through_real_cli_execution_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state = root / "state"
            (root / ".devtool.toml").write_text(
                '''
[project]
runner = "command"
[workspace.groups]
release = ["a", "b"]
[targets.a]
runner = "command"
build_command = "python3 -c pass"
[targets.b]
runner = "command"
build_command = "python3 -c pass"
''',
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch.dict(
                "os.environ",
                {
                    "TERM": "xterm",
                    "XDG_STATE_HOME": str(state),
                    "DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml"),
                },
                clear=False,
            ), redirect_stdout(output):
                rc = main(["--repo", str(root), "--group", "release", "--no-hud", "build"])
            self.assertEqual(rc, 0)
            self.assertIn("Completed successfully", output.getvalue())

    def test_group_validation_applies_cli_runtime_pytest_selection_to_each_python_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for folder in ("api", "worker"):
                target_root = root / folder
                (target_root / "tests").mkdir(parents=True)
                (target_root / "tests" / "test_selected.py").write_text("def test_selected(): assert True\n", encoding="utf-8")
            cfg = Repo(root).config(
                '''
[project]
runner = "python"
[workspace.groups]
python = ["api", "worker"]
[targets.api]
runner = "python"
root = "api"
[targets.worker]
runner = "python"
root = "worker"
''', ambiguous=True
            )
            cfg.runtime_runner_overrides = {
                "test_selection": {
                    "pytest_tests": ["tests/test_selected.py::test_selected"],
                    "pytest_source": "cli",
                }
            }
            specs = build_workspace_validation_specs(cfg, ["api", "worker"], skip_restore=True, skip_build=True)
            pytest_specs = [spec for spec in specs if len(spec.argv) >= 3 and spec.argv[1:3] == ["-m", "pytest"]]
            self.assertEqual(len(pytest_specs), 2)
            for spec in pytest_specs:
                self.assertIn("tests/test_selected.py::test_selected", spec.argv)
            self.assertEqual({spec.metadata["workflow_target"] for spec in pytest_specs}, {"api", "worker"})


if __name__ == "__main__":
    unittest.main()
