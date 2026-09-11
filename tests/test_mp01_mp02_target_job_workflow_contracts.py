from __future__ import annotations

import io
import json
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtool.cli import main
from devtool.config import generate_template, load_project_config, resolve_config_dict
from devtool.doctor import collect_doctor_checks, doctor_payload
from devtool.models import DotnetTarget, ProjectConfig, TargetConfig
from devtool.project_runners import NodeRunner, PythonRunner, get_project_runner
from devtool.workflow_config import normalize_platform_name, normalize_runner_name
from devtool.desktop.control_center_model import DesktopControlCenterModel


class Mp01Mp02Contracts(unittest.TestCase):
    def _repo(self, config_text: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / ".devtool.toml").write_text(textwrap.dedent(config_text), encoding="utf-8")
        return temp, root

    def test_dotnet_target_is_compatibility_alias_for_target_config(self) -> None:
        self.assertIs(DotnetTarget, TargetConfig)

    def test_project_config_runner_is_canonical_and_kind_stays_synced(self) -> None:
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="python", runner="node", platform="web")
        self.assertEqual(config.runner, "node")
        self.assertEqual(config.kind, "node")
        self.assertEqual(config.platform, "web")

    def test_target_config_legacy_kind_populates_runner(self) -> None:
        target = TargetConfig(name="api", kind="python", platform="server")
        self.assertEqual(target.runner, "python")
        self.assertEqual(target.effective_runner, "python")

    def test_normalize_generic_runner_keeps_command_compatibility(self) -> None:
        self.assertEqual(normalize_runner_name("generic"), "command")

    def test_platform_slug_validation(self) -> None:
        self.assertEqual(normalize_platform_name("TV-Browser"), "tv-browser")
        with self.assertRaisesRegex(SystemExit, "Invalid project.platform"):
            normalize_platform_name("TV Browser", context="project.platform")

    def test_project_runner_and_platform_are_loaded_canonically(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            platform = "server"
        """)
        with temp:
            config = load_project_config(str(root), None)
        self.assertEqual(config.runner, "python")
        self.assertEqual(config.kind, "python")
        self.assertEqual(config.platform, "server")
        self.assertEqual(config.config_provenance["runner"], "project")

    def test_project_kind_remains_supported_as_legacy_alias(self) -> None:
        temp, root = self._repo("""
            [project]
            kind = "python"
        """)
        with temp:
            config = load_project_config(str(root), None)
        self.assertEqual(config.runner, "python")
        self.assertEqual(config.platform, "python")
        self.assertEqual(config.config_provenance["runner"], "project-legacy-kind")

    def test_conflicting_project_runner_and_kind_fail_early(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            kind = "node"
        """)
        with temp, self.assertRaisesRegex(SystemExit, "conflicting runner"):
            load_project_config(str(root), None)

    def test_target_runner_overrides_project_runner_without_changing_platform_semantics(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            platform = "server"
            default_target = "web"

            [targets.web]
            runner = "node"
            platform = "web"
            root = "apps/web"
        """)
        with temp:
            (root / "apps" / "web").mkdir(parents=True)
            config = load_project_config(str(root), None)
        self.assertEqual(config.runner, "node")
        self.assertEqual(config.kind, "node")
        self.assertEqual(config.platform, "web")
        self.assertIsInstance(get_project_runner(config), NodeRunner)

    def test_target_legacy_type_remains_supported(self) -> None:
        temp, root = self._repo("""
            [project]
            kind = "auto"
            default_target = "api"

            [targets.api]
            type = "python"
            root = "api"
        """)
        with temp:
            (root / "api").mkdir()
            config = load_project_config(str(root), None)
        target = config.targets["api"]
        self.assertEqual(target.runner, "python")
        self.assertEqual(target.kind, "python")
        self.assertEqual(target.platform, "python")
        self.assertIsInstance(get_project_runner(config), PythonRunner)

    def test_conflicting_target_runner_and_type_fail_early(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.api]
            runner = "python"
            type = "node"
        """)
        with temp, self.assertRaisesRegex(SystemExit, "conflicting runner"):
            load_project_config(str(root), None)

    def test_target_platform_is_independent_from_runner(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "android"
            default_target = "tv"

            [targets.tv]
            runner = "android"
            platform = "tv"
            root = "apps/tv"
        """)
        with temp:
            (root / "apps" / "tv").mkdir(parents=True)
            config = load_project_config(str(root), None)
        self.assertEqual(config.runner, "android")
        self.assertEqual(config.platform, "tv")
        self.assertEqual(config.targets["tv"].platform, "tv")

    def test_structured_job_loads_all_declared_execution_metadata(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [env]
            BASE = "1"

            [jobs.generate]
            runner = "node"
            root = "tools/openapi"
            command = ["npm", "run", "generate"]
            inputs = ["schema/**", "package.json"]
            outputs = ["generated/**"]
            execution_environment = "native-termux"

            [jobs.generate.env]
            LOCAL = "2"

            [jobs.generate.metadata]
            purpose = "client-generation"
        """)
        with temp:
            (root / "tools" / "openapi").mkdir(parents=True)
            config = load_project_config(str(root), None)
        job = config.jobs["generate"]
        self.assertEqual(job.runner, "node")
        self.assertEqual(job.command, ("npm", "run", "generate"))
        self.assertEqual(job.command_mode, "argv")
        self.assertEqual(job.inputs, ("schema/**", "package.json"))
        self.assertEqual(job.outputs, ("generated/**",))
        self.assertEqual(job.execution_environment, "native-termux")
        self.assertEqual(job.env["BASE"], "1")
        self.assertEqual(job.env["LOCAL"], "2")
        self.assertEqual(job.metadata["purpose"], "client-generation")
        self.assertEqual(job.root, str((root / "tools" / "openapi").resolve()))

    def test_job_shell_execution_is_explicit(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [jobs.legacy]
            runner = "command"
            shell_command = "npm run generate && python finish.py"
        """)
        with temp:
            config = load_project_config(str(root), None)
        self.assertEqual(config.jobs["legacy"].command_mode, "shell")

    def test_job_can_reference_native_runner_action(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [jobs.unit]
            runner = "python"
            action = "test"
        """)
        with temp:
            config = load_project_config(str(root), None)
        self.assertEqual(config.jobs["unit"].command_mode, "runner-action")
        self.assertEqual(config.jobs["unit"].action, "test")

    def test_string_command_is_rejected_with_actionable_shell_guidance(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [jobs.bad]
            runner = "node"
            command = "npm run generate"
        """)
        with temp, self.assertRaisesRegex(SystemExit, "shell_command"):
            load_project_config(str(root), None)

    def test_job_execution_modes_are_mutually_exclusive(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [jobs.bad]
            runner = "node"
            command = ["npm", "test"]
            action = "test"
        """)
        with temp, self.assertRaisesRegex(SystemExit, "choose exactly one execution mode"):
            load_project_config(str(root), None)

    def test_job_requires_supported_runner(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"

            [jobs.bad]
            runner = "brainfuck"
            command = ["bf", "run"]
        """)
        with temp, self.assertRaisesRegex(SystemExit, "unsupported runner"):
            load_project_config(str(root), None)

    def test_workflow_normalizes_job_and_runner_references(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [jobs.generate]
            runner = "node"
            command = ["npm", "run", "generate"]

            [targets.api]
            runner = "python"
            platform = "server"

            [targets.api.workflows]
            validate = ["job:generate", "runner:python#validate"]
        """)
        with temp:
            config = load_project_config(str(root), None)
        workflow = config.targets["api"].workflows["validate"]
        self.assertEqual([item.kind for item in workflow.steps], ["job", "runner"])
        self.assertEqual([item.name for item in workflow.steps], ["generate", "python"])
        self.assertEqual(workflow.steps[1].action, "validate")

    def test_cross_target_reference_is_representable_before_mp05_execution(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.protocol]
            runner = "python"
            platform = "shared"

            [targets.api]
            runner = "python"
            platform = "server"

            [targets.api.workflows]
            build = ["target:protocol#build", "runner:python#build"]
        """)
        with temp:
            config = load_project_config(str(root), None)
        first = config.targets["api"].workflows["build"].steps[0]
        self.assertEqual((first.kind, first.name, first.action), ("target", "protocol", "build"))

    def test_unknown_job_reference_fails_during_config_load(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.api]
            runner = "python"

            [targets.api.workflows]
            validate = ["job:missing"]
        """)
        with temp, self.assertRaisesRegex(SystemExit, "unknown job 'missing'"):
            load_project_config(str(root), None)

    def test_unknown_target_reference_fails_during_config_load(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.api]
            runner = "python"

            [targets.api.workflows]
            build = ["target:protocol#build"]
        """)
        with temp, self.assertRaisesRegex(SystemExit, "unknown target 'protocol'"):
            load_project_config(str(root), None)

    def test_unknown_runner_reference_fails_during_config_load(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.api]
            runner = "python"

            [targets.api.workflows]
            validate = ["runner:nope#validate"]
        """)
        with temp, self.assertRaisesRegex(SystemExit, "unsupported runner 'nope'"):
            load_project_config(str(root), None)

    def test_workflow_steps_require_explicit_reference_syntax(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"

            [targets.api]
            runner = "python"

            [targets.api.workflows]
            validate = ["npm run generate"]
        """)
        with temp, self.assertRaisesRegex(SystemExit, "job:<name>"):
            load_project_config(str(root), None)

    def test_resolved_config_machine_model_contains_jobs_and_workflows(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            platform = "server"
            default_target = "api"

            [jobs.generate]
            runner = "node"
            command = ["npm", "run", "generate"]

            [targets.api]
            runner = "python"
            platform = "server"

            [targets.api.workflows]
            validate = ["job:generate", "runner:python#validate"]
        """)
        with temp:
            payload = resolve_config_dict(load_project_config(str(root), None))
        self.assertEqual(payload["project"]["runner"], "python")
        self.assertEqual(payload["project"]["platform"], "server")
        self.assertEqual(payload["jobs"]["generate"]["command"], ["npm", "run", "generate"])
        self.assertEqual(payload["targets"]["api"]["workflows"]["validate"], ["job:generate", "runner:python#validate"])

    def test_targets_json_exposes_platform_runner_and_workflows(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            default_target = "api"
            [targets.api]
            runner = "python"
            platform = "server"
            [targets.api.workflows]
            validate = ["runner:python#validate"]
        """)
        with temp, patch.dict("os.environ", {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--repo", str(root), "targets", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["data"]["targets"][0]["platform"], "server")
        self.assertEqual(payload["data"]["targets"][0]["runner"], "python")
        self.assertIn("validate", payload["data"]["targets"][0]["workflows"])

    def test_jobs_json_is_a_machine_readable_inspection_surface(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            [jobs.generate]
            runner = "node"
            command = ["npm", "run", "generate"]
        """)
        with temp, patch.dict("os.environ", {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--repo", str(root), "jobs", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["data"]["jobs"]["generate"]["runner"], "node")
        self.assertEqual(payload["data"]["jobs"]["generate"]["command_mode"], "argv")

    def test_workflows_json_can_inspect_all_targets_without_selecting_one(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            [targets.a]
            runner = "python"
            [targets.a.workflows]
            test = ["runner:python#test"]
            [targets.b]
            runner = "node"
            [targets.b.workflows]
            test = ["runner:node#test"]
        """)
        with temp, patch.dict("os.environ", {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--repo", str(root), "workflows", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(set(payload["data"]["workflows"]), {"a", "b"})

    def test_doctor_payload_reports_new_identity_and_declarative_workflow_surfaces(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            platform = "server"
            default_target = "api"
            [jobs.gen]
            runner = "node"
            command = ["npm", "run", "gen"]
            [targets.api]
            runner = "python"
            platform = "server"
            [targets.api.workflows]
            build = ["job:gen", "runner:python#build"]
        """)
        with temp:
            config = load_project_config(str(root), None)
            payload = doctor_payload(config)
        self.assertEqual(payload["runner"], "python")
        self.assertEqual(payload["platform"], "server")
        self.assertEqual(payload["jobs"], ["gen"])
        self.assertEqual(payload["workflows"], {"api": ["build"]})

    def test_doctor_surfaces_legacy_identity_migration_without_breaking_it(self) -> None:
        temp, root = self._repo("""
            [project]
            kind = "python"
            default_target = "api"
            [targets.api]
            type = "python"
        """)
        with temp:
            config = load_project_config(str(root), None)
            checks = collect_doctor_checks(config)
        names = {(item.section, item.name): item for item in checks}
        self.assertIn(("project", "identity-migration"), names)
        self.assertIn(("project", "target-identity-migration"), names)
        self.assertEqual(names[("project", "identity-migration")].status, "optional")

    def test_desktop_snapshot_uses_same_platform_runner_workflow_truth(self) -> None:
        temp, root = self._repo("""
            [project]
            runner = "python"
            platform = "server"
            default_target = "api"
            [jobs.gen]
            runner = "node"
            command = ["npm", "run", "gen"]
            [targets.api]
            runner = "python"
            platform = "server"
            [targets.api.workflows]
            validate = ["job:gen", "runner:python#validate"]
        """)
        with temp, patch.dict("os.environ", {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
            snapshot = DesktopControlCenterModel(repository=root).snapshot().as_dict()
        self.assertEqual(snapshot["project"]["runner"], "python")
        self.assertEqual(snapshot["project"]["platform"], "server")
        self.assertEqual(snapshot["project"]["jobs"], ["gen"])
        self.assertEqual(snapshot["targets"][0]["runner"], "python")
        self.assertEqual(snapshot["targets"][0]["platform"], "server")
        self.assertEqual(snapshot["targets"][0]["workflows"], ["validate"])

    def test_generated_template_prefers_runner_platform_and_structured_job_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1'\n", encoding="utf-8")
            template = generate_template(root)
        self.assertIn('runner = "python"', template)
        self.assertIn('platform = "server"', template)
        self.assertIn('# kind = "python"', template)
        self.assertIn('[jobs.generate-client]', template)
        self.assertIn('command = ["npm", "run", "generate"]', template)
        self.assertIn('[targets.api.workflows]', template)


if __name__ == "__main__":
    unittest.main()
