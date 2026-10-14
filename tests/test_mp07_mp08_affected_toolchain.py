from __future__ import annotations

import argparse
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from devtool.affected import analyze_affected_targets, git_changed_paths
from devtool.cli import _workspace_target_names_for_args, build_parser, main
from devtool.config import generate_template, load_project_config, resolve_config_dict
from devtool.toolchain_requirements import analyze_toolchain_requirements


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def write(self, text: str, *, target: str = "", ambiguous: bool = False):
        (self.root / ".devtool.toml").write_text(textwrap.dedent(text), encoding="utf-8")
        return load_project_config(
            str(self.root),
            None,
            target_name=target,
            allow_ambiguous_target=ambiguous,
        )

    def git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(self.root), *args], text=True, capture_output=True, check=True)
        return proc.stdout

    def init_git(self) -> None:
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Devtool Tests")


class AffectedSelectionTests(unittest.TestCase):
    def test_target_root_change_propagates_to_cross_target_consumers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "packages/protocol").mkdir(parents=True)
            (root / "services/api").mkdir(parents=True)
            (root / "web").mkdir()
            cfg = Repo(root).write(
                '''
                [project]
                runner = "python"
                [targets.protocol]
                runner = "python"
                root = "packages/protocol"
                [targets.api]
                runner = "python"
                root = "services/api"
                [targets.api.workflows]
                build = ["target:protocol#build", "runner:python#build"]
                [targets.web]
                runner = "node"
                root = "web"
                ''',
                ambiguous=True,
            )
            result = analyze_affected_targets(cfg, changed_paths=["packages/protocol/schema.json"])
            self.assertEqual(result.targets, ("protocol", "api"))
            self.assertEqual(result.direct_targets, ("protocol",))
            self.assertTrue(any(reason.kind == "dependency" and reason.source == "protocol" for reason in result.reasons["api"]))

    def test_explicit_target_inputs_and_shared_job_inputs_are_owners(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "apps/api").mkdir(parents=True)
            (root / "apps/web").mkdir(parents=True)
            (root / "tools/codegen").mkdir(parents=True)
            (root / "schema").mkdir()
            (root / "shared").mkdir()
            cfg = Repo(root).write(
                '''
                [project]
                runner = "python"
                [jobs.codegen]
                runner = "command"
                root = "tools/codegen"
                command = ["true"]
                inputs = ["../../schema/**"]
                [targets.api]
                runner = "python"
                root = "apps/api"
                inputs = ["../../shared/**"]
                [targets.web]
                runner = "node"
                root = "apps/web"
                [targets.web.workflows]
                build = ["job:codegen"]
                ''',
                ambiguous=True,
            )
            self.assertEqual(cfg.targets["api"].inputs, ("../../shared/**",))
            result = analyze_affected_targets(cfg, changed_paths=["schema/openapi.json", "shared/types.json"])
            self.assertEqual(result.targets, ("api", "web"))
            self.assertTrue(any(reason.kind == "target-input" for reason in result.reasons["api"]))
            self.assertTrue(any(reason.kind == "job-input" and reason.source == "codegen" for reason in result.reasons["web"]))
            resolved = resolve_config_dict(cfg)
            self.assertEqual(resolved["targets"]["api"]["inputs"], ["../../shared/**"])

    def test_unowned_path_conservatively_selects_only_candidate_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("api", "web", "android"):
                (root / name).mkdir()
            cfg = Repo(root).write(
                '''
                [project]
                runner = "python"
                [targets.api]
                runner = "python"
                root = "api"
                [targets.web]
                runner = "node"
                root = "web"
                [targets.android]
                runner = "android"
                root = "android"
                ''',
                ambiguous=True,
            )
            result = analyze_affected_targets(cfg, changed_paths=["README.md"], candidate_targets=("web", "android"))
            self.assertEqual(result.targets, ("web", "android"))
            self.assertEqual(result.uncertain_paths, ("README.md",))
            self.assertTrue(all(any(r.kind == "uncertain-workspace" for r in result.reasons[t]) for t in result.targets))

    def test_git_working_tree_combines_staged_unstaged_and_untracked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = Repo(root)
            repo.init_git()
            for name in ("staged.txt", "unstaged.txt"):
                (root / name).write_text("one\n", encoding="utf-8")
            repo.git("add", ".")
            repo.git("commit", "-qm", "base")
            (root / "staged.txt").write_text("two\n", encoding="utf-8")
            repo.git("add", "staged.txt")
            (root / "unstaged.txt").write_text("two\n", encoding="utf-8")
            (root / "untracked.txt").write_text("new\n", encoding="utf-8")
            self.assertEqual(set(git_changed_paths(root)), {"staged.txt", "unstaged.txt", "untracked.txt"})

    def test_affected_scope_intersects_group_after_dependency_propagation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "protocol").mkdir()
            (root / "web").mkdir()
            (root / "android").mkdir()
            repo = Repo(root)
            cfg = repo.write(
                '''
                [project]
                runner = "python"
                [workspace.groups]
                frontend = ["web"]
                [targets.protocol]
                runner = "python"
                root = "protocol"
                [targets.web]
                runner = "node"
                root = "web"
                [targets.web.workflows]
                build = ["target:protocol#build", "runner:node#build"]
                [targets.android]
                runner = "android"
                root = "android"
                ''',
                ambiguous=True,
            )
            parser = build_parser()
            args = parser.parse_args(["--group", "frontend", "--affected", "build"])
            with mock.patch("devtool.cli.analyze_affected_targets") as analyze:
                analyze.return_value = analyze_affected_targets(cfg, changed_paths=["protocol/schema.json"], candidate_targets=("web",))
                selected = _workspace_target_names_for_args(cfg, args, parser)
            self.assertEqual(selected, ("web",))

    def test_git_base_head_range_reports_committed_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = Repo(root)
            repo.init_git()
            (root / "api").mkdir()
            (root / "api/base.txt").write_text("base\n", encoding="utf-8")
            repo.git("add", ".")
            repo.git("commit", "-qm", "base")
            base = repo.git("rev-parse", "HEAD").strip()
            (root / "api/base.txt").write_text("changed\n", encoding="utf-8")
            (root / "api/new.txt").write_text("new\n", encoding="utf-8")
            repo.git("add", ".")
            repo.git("commit", "-qm", "head")
            head = repo.git("rev-parse", "HEAD").strip()
            self.assertEqual(set(git_changed_paths(root, base=base, head=head)), {"api/base.txt", "api/new.txt"})

    def test_build_affected_no_changes_is_successful_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = Repo(root)
            repo.init_git()
            (root / "api").mkdir()
            (root / "api/input.txt").write_text("base\n", encoding="utf-8")
            repo.write(
                """
                [project]
                runner = "command"
                [workspace]
                default_target = "api"
                [targets.api]
                runner = "command"
                root = "api"
                build_command = "printf should-not-run"
                """
            )
            repo.git("add", ".")
            repo.git("commit", "-qm", "base")
            # The generic no-change test models a desktop Linux host.
            # Native Termux execution is tested separately.
            with tempfile.TemporaryDirectory() as synthetic_home:
                with mock.patch.dict(os.environ, {
                    "HOME": synthetic_home,
                    "PREFIX": "/usr",
                    "TERMUX_VERSION": "",
                    "DEVTOOL_EXECUTION_CONTEXT": "",
                    "DEVTOOL_INSIDE_TERMUX_CHROOT": "",
                    "DEVTOOL_INSIDE_SSH": "",
                    "SSH_CONNECTION": "",
                }), mock.patch(
                    "devtool.host_context.platform.system",
                    return_value="Linux",
                ):
                    self.assertEqual(git_changed_paths(root), ())
                    stream = io.StringIO()
                    with redirect_stdout(stream):
                        code = main([
                            "--repo", str(root),
                            "--dry-run", "--json",
                            "build", "--affected",
                        ])

            envelope = json.loads(stream.getvalue())
            self.assertEqual(code, 0, envelope)
            self.assertEqual(envelope["data"]["failed"], 0)
            self.assertTrue(all(item.get("skipped") and item.get("returncode") == 0 for item in envelope["data"]["results"]))

    def test_scope_flags_after_subcommand_and_explicit_target_conflict(self):
        parser = build_parser()
        args = parser.parse_args(["build", "--affected", "--group", "frontend"] )
        self.assertTrue(args.affected)
        self.assertEqual(args.group, ["frontend"] )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                """
                [project]
                runner = "python"
                [targets.api]
                runner = "python"
                root = "api"
                """,
                ambiguous=True,
            )
            conflict = parser.parse_args(["--target", "api", "build", "--affected"] )
            with self.assertRaises(SystemExit):
                _workspace_target_names_for_args(cfg, conflict, parser)

    def test_targets_json_and_desktop_snapshot_expose_ownership(self):
        from devtool.desktop.control_center_model import DesktopControlCenterModel
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            Repo(root).write(
                """
                [project]
                runner = "command"
                [workspace]
                default_target = "api"
                [targets.api]
                runner = "command"
                root = "api"
                inputs = ["../shared/**"]
                outputs = ["dist/**"]
                """
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "targets", "--json"] )
            envelope = json.loads(stream.getvalue())
            self.assertEqual(code, 0)
            target = envelope["data"]["targets"][0]
            self.assertEqual(target["inputs"], ["../shared/**"] )
            self.assertEqual(target["outputs"], ["dist/**"] )
            snapshot = DesktopControlCenterModel(repository=root).snapshot().as_dict()
            self.assertEqual(snapshot["targets"][0]["inputs"], ["../shared/**"] )
            self.assertEqual(snapshot["targets"][0]["outputs"], ["dist/**"] )


class ToolchainAggregationTests(unittest.TestCase):
    def test_expanded_graph_aggregates_runners_tools_and_environment(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                f'''
                [project]
                runner = "python"
                [workspace]
                default_target = "api"
                [jobs.nodegen]
                runner = "node"
                root = "api"
                command = ["npm", "--version"]
                [jobs.pycheck]
                runner = "python"
                root = "api"
                command = [{json.dumps(sys.executable)}, "--version"]
                [targets.api]
                runner = "python"
                root = "api"
                [targets.api.workflows]
                validate = ["job:nodegen", "job:pycheck"]
                '''
            )
            real_which = __import__("shutil").which
            def fake_which(name, path=None):
                if name == "npm":
                    return "/usr/bin/npm"
                return real_which(name, path=path)
            with mock.patch("devtool.toolchain_requirements.shutil.which", side_effect=fake_which):
                report = analyze_toolchain_requirements(cfg, ["api"], workflow="validate")
            self.assertIn("node", report.runners)
            self.assertIn("python", report.runners)
            tools = {tool.executable: tool for tool in report.tools}
            self.assertEqual(tools["npm"].status, "ready")
            self.assertEqual(tools[sys.executable].status, "ready")
            self.assertTrue(report.healthy)

    def test_preflight_reports_missing_root_and_unsafe_output_scope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                '''
                [project]
                runner = "command"
                [workspace]
                default_target = "api"
                [jobs.bad]
                runner = "command"
                root = "missing"
                command = ["true"]
                outputs = ["../../outside/**"]
                [targets.api]
                runner = "command"
                root = "api"
                [targets.api.workflows]
                validate = ["job:bad"]
                '''
            )
            report = analyze_toolchain_requirements(cfg, ["api"], workflow="validate")
            self.assertFalse(report.healthy)
            self.assertTrue(any(item.section == "root" and item.status == "missing" for item in report.checks))
            self.assertTrue(any(item.section == "output" and item.status == "unsafe" for item in report.checks))

    def test_destination_tool_is_deferred_not_false_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                '''
                [project]
                runner = "command"
                [workspace]
                default_target = "api"
                [jobs.remote]
                runner = "command"
                root = "api"
                execution_environment = "ssh"
                command = ["definitely-not-on-host", "--version"]
                [targets.api]
                runner = "command"
                root = "api"
                [targets.api.workflows]
                validate = ["job:remote"]
                '''
            )
            report = analyze_toolchain_requirements(cfg, ["api"], workflow="validate")
            tool = next(item for item in report.tools if item.executable == "definitely-not-on-host")
            self.assertEqual(tool.status, "deferred")
            self.assertIn("ssh", tool.environments)

    def test_preflight_json_command_returns_nonzero_for_missing_tool(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            Repo(root).write(
                '''
                [project]
                runner = "command"
                [workspace]
                default_target = "api"
                [jobs.bad]
                runner = "command"
                root = "api"
                command = ["devtool-tool-that-does-not-exist"]
                [targets.api]
                runner = "command"
                root = "api"
                [targets.api.workflows]
                validate = ["job:bad"]
                '''
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "preflight", "--workflow", "validate", "--json"])
            envelope = json.loads(stream.getvalue())
            payload = envelope["data"]
            self.assertEqual(code, 1)
            self.assertFalse(payload["healthy"])
            self.assertTrue(any(tool["executable"] == "devtool-tool-that-does-not-exist" and tool["status"] == "missing" for tool in payload["tools"]))

    def test_doctor_json_contains_expanded_workflow_preflight(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            Repo(root).write(
                f'''
                [project]
                runner = "python"
                [workspace]
                default_target = "api"
                [jobs.check]
                runner = "python"
                root = "api"
                command = [{json.dumps(sys.executable)}, "--version"]
                [targets.api]
                runner = "python"
                root = "api"
                [targets.api.workflows]
                validate = ["job:check"]
                '''
            )
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = main(["--repo", str(root), "doctor", "--workflow", "validate", "--json"])
            envelope = json.loads(stream.getvalue())
            payload = envelope["data"]
            self.assertEqual(code, 0)
            self.assertEqual(payload["workflow_preflight"]["targets"], ["api"])
            self.assertIn("python", payload["workflow_preflight"]["runners"])

    def test_group_preflight_aggregates_mixed_runners_and_shared_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("protocol", "api", "web"):
                (root / name).mkdir()
            Repo(root).write(
                f"""
                [project]
                runner = "command"
                [workspace.groups]
                release = ["api", "web"]
                [jobs.protocol-check]
                runner = "python"
                root = "protocol"
                command = [{json.dumps(sys.executable)}, "--version"]
                [jobs.api-check]
                runner = "python"
                root = "api"
                command = [{json.dumps(sys.executable)}, "--version"]
                [jobs.web-check]
                runner = "node"
                root = "web"
                command = ["node", "--version"]
                [targets.protocol]
                runner = "python"
                root = "protocol"
                [targets.protocol.workflows]
                validate = ["job:protocol-check"]
                [targets.api]
                runner = "python"
                root = "api"
                [targets.api.workflows]
                validate = ["target:protocol#validate", "job:api-check"]
                [targets.web]
                runner = "node"
                root = "web"
                [targets.web.workflows]
                validate = ["target:protocol#validate", "job:web-check"]
                """,
                ambiguous=True,
            )
            real_which = __import__("shutil").which
            def fake_which(name, path=None):
                if name == "node":
                    return "/usr/bin/node"
                return real_which(name, path=path)
            stream = io.StringIO()
            with mock.patch("devtool.toolchain_requirements.shutil.which", side_effect=fake_which), redirect_stdout(stream):
                code = main(["--repo", str(root), "preflight", "--group", "release", "--workflow", "validate", "--json"] )
            envelope = json.loads(stream.getvalue())
            payload = envelope["data"]
            self.assertEqual(code, 0)
            self.assertEqual(payload["targets"], ["api", "web"] )
            self.assertIn("python", payload["runners"] )
            self.assertIn("node", payload["runners"] )
            protocol_nodes = [n for n in payload["graph_nodes"] if n.get("target") == "protocol"]
            self.assertEqual(len({n.get("key") for n in protocol_nodes}), len(protocol_nodes))

    def test_target_execution_environment_is_inherited_by_auto_jobs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                '\n                [project]\n                runner = "command"\n                [workspace]\n                default_target = "api"\n                [jobs.check]\n                runner = "command"\n                root = "api"\n                command = ["remote-tool"]\n                execution_environment = "auto"\n                [targets.api]\n                runner = "command"\n                root = "api"\n                execution_environment = "ssh"\n                [targets.api.workflows]\n                validate = ["job:check"]\n                '
            )
            report = analyze_toolchain_requirements(cfg, ["api"], workflow="validate")
            tool = next(item for item in report.tools if item.executable == "remote-tool")
            self.assertEqual(tool.status, "deferred")
            self.assertIn("ssh", tool.environments)

    def test_managed_python_venv_executable_is_planned_not_false_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api").mkdir()
            cfg = Repo(root).write(
                """
                [project]
                runner = "python"
                [workspace]
                default_target = "api"
                [targets.api]
                runner = "python"
                root = "api"
                [targets.api.workflows]
                validate = ["runner:python#validate"]
                """
            )
            report = analyze_toolchain_requirements(cfg, ["api"], workflow="validate")
            planned = [tool for tool in report.tools if tool.status == "planned"]
            self.assertTrue(planned)
            self.assertTrue(any("/bin/python" in tool.executable for tool in planned))
            self.assertFalse(any(tool.status == "missing" and "/bin/python" in tool.executable for tool in report.tools))


class SurfaceContractTests(unittest.TestCase):
    def test_template_completion_manpage_and_docs_expose_mp07_mp08(self):
        with tempfile.TemporaryDirectory() as td:
            template = generate_template(Path(td))
        self.assertIn('inputs = ["../../packages/protocol/**", "../../schemas/openapi.json"]', template)
        self.assertIn('outputs = ["build/**", "dist/**"]', template)
        repo = Path(__file__).resolve().parents[1]
        completion = (repo / "devtool/completion.py").read_text(encoding="utf-8")
        manpage = (repo / "share/man/man1/devtool.1").read_text(encoding="utf-8")
        self.assertIn("affected:show dependency-aware targets affected by Git changes", completion)
        self.assertIn("preflight:check expanded workflow roots", completion)
        self.assertIn("--affected-base", completion)
        self.assertIn(".B affected", manpage)
        self.assertIn(".B preflight", manpage)
        self.assertTrue((repo / "docs/AFFECTED_TARGETS.md").is_file())
        self.assertTrue((repo / "docs/WORKFLOW_PREFLIGHT.md").is_file())


if __name__ == "__main__":
    unittest.main()
