from __future__ import annotations

import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import tomllib
from unittest.mock import patch

from devtool.ai_prompt import AiPromptOptions, render_ai_prompt
from devtool.artifacts import diff_artifact, preview_artifact
from devtool import __version__
from devtool.cli import _attempt_auto_repair, _maybe_commit_after_apply, _maybe_copy, _run_sequential_overlays, _validation_specs_for_args, build_parser, main
from devtool.config import ProjectConfig, load_project_config
from devtool.log_commands import choose_active_log, parse_log_filename
from devtool.logstore import write_log
from devtool.xdg import cache_dir, data_dir, state_dir
from devtool.models import CommandSpec, Issue, OperationResult
from devtool.summary import build_json_summary


def _sample_ai_prompt_options(output_format: str = "markdown") -> AiPromptOptions:
    return AiPromptOptions(
        repo_name="demo",
        project_type="python",
        build_target="pyproject.toml",
        run_target="src/app.py",
        delivery_mode="overlay",
        artifact_type="zip",
        overlay_count=1,
        commit_message="Commit msg",
        notifications_enabled=True,
        notify_brief=False,
        clipboard_behavior="ask whether to copy raw command output, with Enter meaning yes",
        prefer_wine_validation=False,
        repo_path="/tmp/target-repo",
        output_format=output_format,
    )


class CliTests(unittest.TestCase):

    def test_parser_accepts_shared_execution_environment(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--execution-environment", "native-termux", "status"])
        self.assertEqual(args.execution_environment, "native-termux")

    def test_parser_accepts_variant_matrix_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--target", "api", "validate", "--matrix", "debug,release", "--matrix-mode", "variant"])
        self.assertEqual(args.matrix_mode, "variant")

    def test_variant_matrix_expands_validation_per_variant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\n"
                "\n[targets.api]\nsolution='App.sln'\n"
                "\n[targets.api.variants.debug.dotnet]\nconfiguration='Debug'\n"
                "\n[targets.api.variants.release.dotnet]\nconfiguration='Release'\n",
                encoding="utf-8",
            )
            parser = build_parser()
            args = parser.parse_args([
                "-r", str(root), "--target", "api",
                "validate", "--matrix", "debug,release", "--matrix-mode", "variant",
            ])
            config = load_project_config(str(root), None, target_name="api")
            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.metadata["matrix"] for spec in specs], ["debug", "debug", "debug", "release", "release", "release"])
        self.assertIn("Debug", specs[1].argv)
        self.assertIn("Release", specs[4].argv)

    def test_execution_environment_is_mutually_exclusive_with_legacy_chroot_flag(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--execution-environment", "chroot", "--chroot", "androidos", "status"])
    def test_parser_accepts_alias(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["v"])
        self.assertEqual(args.command, "v")

    def test_parser_accepts_apply_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply-overlay", "--validate", "--commit", "msg", "artifact.zip"])
        self.assertEqual(args.command, "apply-overlay")
        self.assertTrue(args.validate)
        self.assertFalse(args.validate_only)
        self.assertEqual(args.commit, "msg")

    def test_parser_accepts_artifact_cleanup_flags_for_apply(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply-overlay", "--keep-artifact", "artifact.zip"])
        self.assertTrue(args.keep_artifact)
        args = parser.parse_args(["apply-overlay", "--delete-artifact-on-failure", "artifact.zip"])
        self.assertTrue(args.delete_artifact_on_failure)

    def test_parser_accepts_clean_cache_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["clean", "--caches"])
        self.assertTrue(args.caches)

    def test_clean_caches_invokes_cache_prune_before_runner_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as home:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            clean_result = __import__("devtool.models").models.OperationResult("clean", ["dotnet", "clean"], 0, 0.0, "ok", [])
            cleanup_patch = patch("devtool.cli.cleanup_devtool_caches", return_value=["/tmp/devtool-cache"])
            run_patch = patch("devtool.cli._run_specs_for_args", return_value=([clean_result], "clean output"))
            with (
                patch.dict("os.environ", {"HOME": home, "XDG_RUNTIME_DIR": ""}),
                cleanup_patch as cleanup_mock,
                run_patch as run_specs,
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "clean", "--caches"])

        self.assertEqual(rc, 0)
        cleanup_mock.assert_called_once_with(dry_run=False)
        run_specs.assert_called_once()

    def test_logstore_honors_devtool_xdg_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
                path = write_log("hello")
        self.assertTrue(str(path).startswith(str(Path(tmp) / "state" / "logs")))

    def test_xdg_helpers_honor_standard_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.dict(
                "os.environ",
                {
                    "DEVTOOL_XDG_DIR": "",
                    "XDG_STATE_HOME": str(root / "state"),
                    "XDG_DATA_HOME": str(root / "data"),
                    "XDG_CACHE_HOME": str(root / "cache"),
                },
                clear=False,
            ):
                self.assertEqual(state_dir(), root / "state" / "devtool")
                self.assertEqual(data_dir(), root / "data" / "devtool")
                self.assertEqual(cache_dir(), root / "cache" / "devtool")

    def test_phone_log_mirror_is_best_effort_copy_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mirror = root / "sdcard-logs"
            with patch.dict(
                "os.environ",
                {
                    "DEVTOOL_XDG_DIR": str(root / "xdg"),
                    "DEVTOOL_PHONE_LOG_MIRROR_DIR": str(mirror),
                    "HOME": "/data/data/com.termux/files/home",
                    "PREFIX": "/data/data/com.termux/files/usr",
                },
                clear=False,
            ):
                path = write_log("phone log")
                mirrored = mirror / path.name
                mirrored_exists = mirrored.is_file()
                mirrored_text = mirrored.read_text(encoding="utf-8") if mirrored_exists else ""

        self.assertTrue(str(path).startswith(str(root / "xdg" / "logs")))
        self.assertTrue(mirrored_exists)
        self.assertEqual(mirrored_text, "phone log")

    def test_parser_rejects_commit_even_on_failure_flag(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply-overlay", "--commit-even-on-failure", "artifact.zip"])

    def test_parser_accepts_validation_skip_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["validate", "--skip-restore", "--skip-build"])
        self.assertEqual(args.command, "validate")
        self.assertTrue(args.skip_restore)
        self.assertTrue(args.skip_build)
        self.assertFalse(args.skip_test)

    def test_parser_rejects_removed_commands(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["apply-overlay-validate", "artifact.zip"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["termux-run"])


    def test_parser_rejects_removed_notification_tasker_and_concurrency_flags(self) -> None:
        parser = build_parser()
        for argv in (
            ["--notify", "build"],
            ["--notify-progress", "build"],
            ["--tasker", "build"],
            ["--tasker-progress", "build"],
            ["--tasker-broadcast", "build"],
            ["--concurrency", "off", "build"],
            ["--lock-timeout", "1", "build"],
            ["--no-wait", "build"],
        ):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                parser.parse_args(argv)

    def test_ai_prompt_prints_to_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["--repo", str(root), "ai-prompt", "--repo-name", "sample"])
            self.assertEqual(rc, 0)
            output = stream.getvalue()
            self.assertIn("AI workflow prompt for sample", output)
            self.assertIn(str(root), output)

    def test_ai_prompt_writes_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.1'\n", encoding="utf-8")
            output_file = root / "prompt.md"
            rc = main([
                "--repo",
                str(root),
                "ai-prompt",
                "--repo-name",
                "demo",
                "--format",
                "markdown",
                "--output",
                str(output_file),
            ])
            self.assertEqual(rc, 0)
            text = output_file.read_text(encoding="utf-8")
            self.assertIn("# AI workflow prompt for demo", text)

    def test_ai_prompt_markdown_uses_target_repo_and_devtool_git_flow(self) -> None:
        text = render_ai_prompt(_sample_ai_prompt_options("markdown"), "markdown")
        self.assertIn("Target repo: `/tmp/target-repo`", text)
        self.assertIn("Do not infer the devtool source tree is the repo under change", text)
        self.assertIn('devtool -r "/tmp/target-repo" commit "Commit msg"', text)
        self.assertIn('devtool -r "/tmp/target-repo" tag <name>', text)
        self.assertIn('devtool -r "/tmp/target-repo" release-notes --from-ref <ref> --to-ref HEAD', text)
        self.assertIn(".devtool-delete", text)
        self.assertNotIn("DEVTOOL_PROJECT_", text)
        self.assertNotIn("DEVTOOL_ENV_", text)
        self.assertNotIn("git add -A", text)

    def test_ai_prompt_text_uses_target_repo_and_devtool_git_flow(self) -> None:
        text = render_ai_prompt(_sample_ai_prompt_options("text"), "text")
        self.assertIn("Target repo: /tmp/target-repo", text)
        self.assertIn("Do not infer the devtool source tree is the repo under change", text)
        self.assertIn('Prefer validation command: devtool -r "/tmp/target-repo" validate --copy', text)
        self.assertIn('The normal Git path is `devtool -r "/tmp/target-repo" commit "Commit msg"`.', text)
        self.assertIn('devtool -r "/tmp/target-repo" ai-prompt --repo-name "demo"', text)
        self.assertIn(".devtool-delete", text)
        self.assertNotIn("DEVTOOL_PROJECT_", text)
        self.assertNotIn("DEVTOOL_ENV_", text)
        self.assertNotIn("git add -A", text)

    def test_logs_latest_prints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            write_log("hello")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["logs", "latest", "--print"])
        self.assertEqual(rc, 0)

    def test_logs_compat_prints_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            write_log("hello")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["logs", "--print"])
        self.assertEqual(rc, 0)
        self.assertIn("hello", stream.getvalue())

    def test_log_filename_timestamp_formats_for_selection(self) -> None:
        date, time = parse_log_filename(Path("20260716-152523-546390.log"))
        self.assertEqual(date, "2026-07-16")
        self.assertEqual(time, "15:25:23")

    def test_logs_list_prints_iso_date_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            log_dir = Path(tmp) / "state" / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "20260716-152523-546390.log").write_text("hello", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["logs", "list"])
        self.assertEqual(rc, 0)
        self.assertIn("2026-07-16 15:25:23", stream.getvalue())

    def test_logs_open_uses_cat_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            path = write_log("hello")
            with patch("devtool.log_commands.subprocess.call", return_value=0) as call:
                rc = main(["logs", "open", "latest", "--pager", "cat"])
        self.assertEqual(rc, 0)
        call.assert_called_once_with(["cat", str(path)])

    def test_logs_tail_reports_no_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            rc = main(["logs", "tail", "--no-follow"])
        self.assertEqual(rc, 1)

    def test_active_log_selection_uses_run_state_log_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state")}, clear=False):
            state = Path(tmp) / "state"
            runs = state / "runs"
            logs = state / "logs"
            runs.mkdir(parents=True)
            logs.mkdir(parents=True)
            log_path = logs / "20260716-152523-546390.log"
            log_path.write_text("live", encoding="utf-8")
            (runs / "run.json").write_text(
                json.dumps({"run_id": "run", "pid": os.getpid(), "command": "build", "status": "running", "log_path": str(log_path)}),
                encoding="utf-8",
            )

            selected = choose_active_log(use_fzf=False)

        self.assertEqual(selected, log_path)

    def test_batch_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            rc = main(["--repo", str(root), "--dry-run", "batch", "validate"])
            self.assertEqual(rc, 0)

    def test_validate_can_skip_restore_and_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            captured: list[str] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend(spec.name for spec in specs)
                return ([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")

            with (
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "validate", "--skip-restore", "--skip-build"])

            self.assertEqual(rc, 0)
            self.assertEqual(captured, ["test"])

    def test_config_defaults_prints_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.1'\n", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["--repo", str(root), "config", "defaults"])
            self.assertEqual(rc, 0)
            output = stream.getvalue()
            self.assertIn("[project]", output)
            self.assertIn("restore_command", output)

    def test_version_matches_project_metadata(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(__version__, metadata["project"]["version"])



    def test_failure_copy_compacts_successful_phases_and_keeps_diagnostics(self) -> None:
        raw_output = """$ devtool extract-overlay overlay.zip -C /repo
Applied overlay from overlay.zip
Artifact validation passed.

$ dotnet restore /repo/App.sln
  Determining projects to restore...
  All projects are up-to-date for restore.

$ dotnet build --no-restore /repo/App.sln
  App.Core -> /repo/bin/App.Core.dll
/repo/App.axaml : Avalonia error AVLN2002: Duplicate x:Class directive [/repo/App.csproj]

Build FAILED.

/repo/App.axaml : Avalonia error AVLN2002: Duplicate x:Class directive [/repo/App.csproj]
    0 Warning(s)
    1 Error(s)

Time Elapsed 00:00:10.00

$ rsync rollback transaction.json
Rolled back devtool apply transaction.
restored_paths:
  hundreds/of/noisy/files.cs

$ delete overlay.zip
Deleted original artifact(s) after failed apply.
removed: overlay.zip
"""
        summary_text = """apply-overlay: ok exit=0 time=0.50s
restore: ok exit=0 time=1.00s
build: failed exit=1 time=10.00s
rollback: ok exit=0 time=0.20s
artifact-cleanup: ok exit=0 time=0.10s
"""
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_summary=False,
            copy_json=False,
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, '{"warnings":0,"errors":1,"failed":1,"skipped":0}', args)

        self.assertEqual(len(copied), 1)
        text = copied[0]
        self.assertIn("Succeeded: overlay apply, restore", text)
        self.assertIn("Failed: build", text)
        self.assertIn("Recovery: rollback succeeded; artifact cleanup succeeded", text)
        self.assertIn("$ dotnet build --no-restore /repo/App.sln", text)
        self.assertIn("Avalonia error AVLN2002", text)
        self.assertNotIn("App.Core ->", text)
        self.assertNotIn("All projects are up-to-date", text)
        self.assertNotIn("hundreds/of/noisy/files.cs", text)

    def test_failure_copy_includes_repair_summary_when_available(self) -> None:
        raw_output = "$ dotnet build /repo/App.sln\n/repo/App.cs(7,3): error CS1061: string does not contain a definition for GetFullPath\n"
        summary_text = "build: failed exit=1 time=1.00s\n"
        json_text = json.dumps(
            {
                "warnings": 0,
                "errors": 1,
                "failed": 1,
                "skipped": 0,
                "repairs": {
                    "counts": {"auto_fix": 1, "confirm_fix": 0, "manual_review": 0},
                    "auto_fix": [
                        {
                            "bucket": "auto_fix",
                            "code": "CS1061",
                            "file": "App.cs",
                            "line": 7,
                            "message": "string does not contain a definition for GetFullPath",
                            "fix": "Use `System.IO.Path.GetFullPath(...)` instead of the string instance call.",
                        }
                    ],
                    "confirm_fix": [],
                    "manual_review": [],
                },
            }
        )
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_summary=False,
            copy_json=False,
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        self.assertIn("Repair summary", copied[0])
        self.assertIn("Auto-fix candidates", copied[0])
        self.assertIn("System.IO.Path.GetFullPath", copied[0])

    def test_auto_repair_rewrites_and_revalidates_when_it_reduces_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            source = repo / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text("class Demo { void Run() { var path = inputPath.GetFullPath(); } }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=repo, kind="dotnet")
            args = SimpleNamespace(command="validate", repo=str(repo), dry_run=False, verbose=False)
            validation_results = [
                OperationResult(
                    name="build",
                    argv=["dotnet", "build"],
                    returncode=1,
                    duration_seconds=0.0,
                    output="",
                    issues=[Issue("error", "build", "src/Program.cs", "CS1061", "string does not contain a definition for GetFullPath")],
                )
            ]
            repair_ok = [OperationResult("build", ["dotnet", "build"], 0, 0.0, "ok", [])]

            with patch("devtool.cli._run_specs_for_args", return_value=(repair_ok, "revalidated")):
                repaired_results, raw_output, repair_result = _attempt_auto_repair(
                    config,
                    args,
                    validation_results=validation_results,
                    validation_specs=[CommandSpec("build", ["dotnet", "build"])],
                    continue_on_error=False,
                    log_path=None,
                    tasker_progress=None,
                    parser=build_parser(),
                )

            self.assertIsNotNone(repair_result)
            self.assertTrue(repaired_results[0].name == "auto-repair")
            self.assertIn("System.IO.Path.GetFullPath", source.read_text(encoding="utf-8"))
            self.assertIn("revalidated", raw_output)

    def test_auto_repair_restores_sources_when_retry_does_not_reduce_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            source = repo / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            original = "class Demo { void Run() { var path = inputPath.GetFullPath(); } }\n"
            source.write_text(original, encoding="utf-8")
            config = ProjectConfig(repo_root=repo, kind="dotnet")
            args = SimpleNamespace(command="validate", repo=str(repo), dry_run=False, verbose=False)
            validation_results = [
                OperationResult(
                    name="build",
                    argv=["dotnet", "build"],
                    returncode=1,
                    duration_seconds=0.0,
                    output="",
                    issues=[Issue("error", "build", "src/Program.cs", "CS1061", "string does not contain a definition for GetFullPath")],
                )
            ]
            still_failed = [OperationResult("build", ["dotnet", "build"], 1, 0.0, "failed", [])]

            with patch("devtool.cli._run_specs_for_args", return_value=(still_failed, "still failed")):
                repaired_results, raw_output, repair_result = _attempt_auto_repair(
                    config,
                    args,
                    validation_results=validation_results,
                    validation_specs=[CommandSpec("build", ["dotnet", "build"])],
                    continue_on_error=False,
                    log_path=None,
                    tasker_progress=None,
                    parser=build_parser(),
                )

            self.assertIsNotNone(repair_result)
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertEqual(repaired_results[0].name, "build")
            self.assertIn("Restored original source files", raw_output)

    def test_sequential_overlay_flow_uses_auto_repair_before_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            source = repo / "src" / "Program.cs"
            source.parent.mkdir(parents=True)
            source.write_text("class Demo { void Run() { var path = inputPath.GetFullPath(); } }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=repo, kind="dotnet")
            args = SimpleNamespace(
                command="apply-overlays",
                repo=str(repo),
                config="",
                artifact="bundle.zip",
                validate=True,
                validate_only=False,
                no_validate=False,
                no_artifact_script=True,
                skip_restore=False,
                skip_build=False,
                skip_test=False,
                keep_artifact=True,
                dry_run=True,
                force=False,
                yes=True,
                project="",
                target="",
                solution="",
                _devtool_cli_target_explicit=False,
                _devtool_cli_solution_explicit=False,
                _devtool_history_recorded=False,
            )
            apply_result = OperationResult(
                name="apply-overlay",
                argv=["apply-overlay", "bundle.zip"],
                returncode=0,
                duration_seconds=0.0,
                output="applied",
                issues=[],
                metadata={"transaction_path": "/tmp/devtool-transaction"},
            )
            validation_failure = [
                OperationResult(
                    name="build",
                    argv=["dotnet", "build"],
                    returncode=1,
                    duration_seconds=0.0,
                    output="",
                    issues=[Issue("error", "build", "src/Program.cs", "CS1061", "string does not contain a definition for GetFullPath")],
                )
            ]
            validation_success = [OperationResult("build", ["dotnet", "build"], 0, 0.0, "ok", [])]
            entries = [
                {
                    "path": repo / "bundle-entry.zip",
                    "source": repo / "bundle.zip",
                    "bundle_member": "bundle-entry.zip",
                    "label": "bundle-entry",
                    "target": "",
                    "index": 1,
                    "total": 1,
                    "materialized": False,
                }
            ]

            with (
                patch("devtool.cli._overlay_sequence_entries", return_value=(None, None, entries)),
                patch("devtool.cli._run_apply", return_value=([apply_result], "apply raw")),
                patch("devtool.cli._validation_specs", return_value=[CommandSpec("build", ["dotnet", "build"])]),
                patch("devtool.cli._run_specs_for_args", side_effect=[(validation_failure, "validation raw"), (validation_success, "repaired raw")]),
                patch("devtool.cli._artifact_script_result", return_value=(None, "")),
                patch("devtool.cli._validation_expectation_result", return_value=None),
                patch("devtool.cli._maybe_commit_after_apply", side_effect=lambda config, args, results, raw_output, log_path=None: (results, raw_output)),
                patch("devtool.cli._record_apply_history"),
                patch("devtool.cli._run_transaction_rollback", side_effect=AssertionError("rollback should not run after repair")),
            ):
                results, raw_output = _run_sequential_overlays(
                    config,
                    args,
                    build_parser(),
                    log_path=None,
                    tasker_progress=None,
                    continue_on_error=False,
                )

            self.assertEqual(results[0].name, "apply-overlay")
            self.assertTrue(any(result.name == "auto-repair" for result in results))
            self.assertTrue(any(result.returncode == 0 and result.name == "build" for result in results))
            self.assertIn("Auto repair pass:", raw_output)
            self.assertIn("repaired raw", raw_output)
            self.assertEqual(source.read_text(encoding="utf-8"), "class Demo { void Run() { var path = System.IO.Path.GetFullPath(inputPath); } }\n")


    def test_bundle_reuses_successful_dotnet_validation_for_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            entries = [
                {"path": repo / "one.zip", "source": repo / "bundle.zip", "bundle_member": "one.zip", "label": "one", "target": "shared", "index": 1, "total": 2, "materialized": True},
                {"path": repo / "two.zip", "source": repo / "bundle.zip", "bundle_member": "two.zip", "label": "two", "target": "shared", "index": 2, "total": 2, "materialized": True},
            ]
            args = SimpleNamespace(
                command="apply-overlays", repo=str(repo), config="", artifact="bundle.zip", validate=True,
                validate_only=False, no_validate=False, no_artifact_script=True, skip_restore=False,
                skip_build=False, skip_test=False, keep_artifact=True, dry_run=True, force=False, yes=True,
                project="", target="", solution="", _devtool_cli_target_explicit=False,
                _devtool_cli_solution_explicit=False, _devtool_history_recorded=False,
            )
            apply_result = OperationResult("apply-overlays", ["apply"], 0, 0.0, "ok", [])
            validation_result = OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])
            shared_config = ProjectConfig(
                repo_root=repo, kind="dotnet", restore_target="/repo/App.sln",
                build_target="/repo/App.sln", test_target="/repo/App.sln", selected_target="shared",
            )
            calls = []

            def fake_run(*_args, **_kwargs):
                calls.append("validation")
                return [validation_result], "validated"

            with (
                patch("devtool.cli._overlay_sequence_entries", return_value=(None, None, entries)),
                patch("devtool.cli.read_artifact_manifest", return_value={"target": "shared", "validation": {"required": True}}),
                patch("devtool.cli._run_apply", return_value=([apply_result], "applied")),
                patch("devtool.cli.load_project_config", return_value=shared_config),
                patch("devtool.cli._validation_specs", return_value=[CommandSpec("test", ["dotnet", "test"])]),
                patch("devtool.cli._run_specs_for_args", side_effect=fake_run),
                patch("devtool.cli._artifact_script_result", return_value=(None, "")),
                patch("devtool.cli._validation_expectation_result", return_value=None),
                patch("devtool.cli.shutdown_dotnet_build_server", return_value=(OperationResult("dotnet-build-server-shutdown", ["dotnet"], 0, 0.0, "ok", []), "shutdown")),
                patch("devtool.cli._maybe_commit_after_apply", side_effect=lambda config, args, results, raw_output, log_path=None: (results, raw_output)),
                patch("devtool.cli._record_apply_history"),
            ):
                results, raw = _run_sequential_overlays(
                    shared_config, args, build_parser(), log_path=None, tasker_progress=None, continue_on_error=False
                )

            self.assertEqual(calls, ["validation"])
            self.assertTrue(any(result.name == "validation-reused" for result in results))
            self.assertIn("same workspace is restored, built, and tested only once", raw)

    def test_copy_json_uses_json_summary_even_without_json_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            copied: list[str] = []

            with (
                patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or __import__("devtool.clipboard").clipboard.ClipboardResult(True, backend="xclip")),
                patch("devtool.cli.console.print"),
            ):
                rc = main(["--repo", str(root), "--copy-json", "--dry-run", "validate"])

            self.assertEqual(rc, 0)
            self.assertEqual(len(copied), 1)
            copied_json = copied[0]
            self.assertIn('"warnings"', copied_json)
            self.assertIn('"errors"', copied_json)
            self.assertIn('"failed"', copied_json)
            self.assertNotIn('"failures"', copied_json)

    def test_copy_uses_result_success_not_truthiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")

            with (
                patch("devtool.cli.copy_text", return_value=__import__("devtool.clipboard").clipboard.ClipboardResult(False, backend="xclip", reason="nope")),
                patch("devtool.cli.console.print") as mock_print,
            ):
                rc = main(["--repo", str(root), "--copy", "--dry-run", "validate"])

            self.assertEqual(rc, 0)
            rendered = "\n".join(" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list)
            self.assertIn("nope", rendered)

    def test_commit_does_not_prompt_for_interactive_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()

            with (
                patch("devtool.cli.Confirm.ask") as mock_confirm,
                patch("devtool.cli.console.print"),
            ):
                rc = main(["--repo", str(root), "--dry-run", "commit", "example commit"])

            self.assertEqual(rc, 0)
            mock_confirm.assert_not_called()

    def test_parser_accepts_stdout_payload_json(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--stdout-payload", "json", "validate"])
        self.assertEqual(args.stdout_payload, "json")

    def test_parser_rejects_removed_stdout_payload_aliases(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--stdout-payload", "machine-json", "validate"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--stdout-payload", "runner-json", "validate"])

    def test_json_cannot_be_combined_with_stdout_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--repo", str(root), "--json", "--stdout-payload", "raw", "validate"])

    def test_copy_flags_must_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--repo", str(root), "--copy", "--copy-json", "validate"])

    def test_stdout_payload_json_prints_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["--repo", str(root), "--stdout-payload", "json", "--dry-run", "validate"])
            self.assertEqual(rc, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("clipboard", payload)
            self.assertIn("notification", payload)

    def test_json_summary_uses_failed_not_failures(self) -> None:
        payload = build_json_summary([], repo_root="/tmp/repo")
        self.assertIn("failed", payload)
        self.assertNotIn("failures", payload)

    def test_git_dirty_ignores_repo_local_devtool_dir(self) -> None:
        from devtool.cli import _git_dirty as cli_git_dirty
        from devtool.detect import _git_dirty as detect_git_dirty

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root / ".devtool").mkdir()
            (root / ".devtool" / "state.json").write_text("{}", encoding="utf-8")

            self.assertFalse(cli_git_dirty(root))
            self.assertFalse(detect_git_dirty(root))

            (root / "real.txt").write_text("dirty", encoding="utf-8")
            self.assertTrue(cli_git_dirty(root))
            self.assertTrue(detect_git_dirty(root))

    def test_apply_overlay_rejects_path_traversal_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "bad.zip"
            escaped = root.parent / "escaped-from-archive.txt"
            if escaped.exists():
                escaped.unlink()
            with ZipFile(artifact, "w") as zf:
                zf.writestr("../escaped-from-archive.txt", "boom")

            rc = main(["--repo", str(root), "--yes", "apply-overlay", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertFalse(escaped.exists())


    def test_apply_overlay_skips_metadata_and_honors_delete_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "old.txt").write_text("old", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"type": "overlay"}))
                zf.writestr(".devtool-delete", "old.txt\n")
                zf.writestr("new.txt", "new")

            rc = main(["--repo", str(root), "--yes", "apply-overlay", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((root / "old.txt").exists())
            self.assertFalse((root / ".devtool-artifact.json").exists())
            self.assertFalse((root / ".devtool-delete").exists())


    def test_apply_overlay_removes_sidecar_manifest_next_to_artifact_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".devtool.toml").write_text('[project]\ntest_command = "echo test"\n', encoding="utf-8")
            artifact = root / "artifact.zip"
            sidecar = root / "artifact.zip.devtool.json"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("new.txt", "new")
            sidecar.write_text(json.dumps({"type": "overlay"}), encoding="utf-8")

            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertFalse(artifact.exists())
            self.assertFalse(sidecar.exists())
            self.assertFalse((root / ".devtool-artifact.json").exists())
            self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "new")

    def test_apply_overlay_preserves_original_artifact_when_validation_rolls_back_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "existing.txt").write_text("before", encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\ntest_command = "echo test"\n', encoding="utf-8")
            artifact = root / "artifact.zip"
            sidecar = root / "artifact.zip.devtool.json"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("existing.txt", "after")
                zf.writestr("new.txt", "new")
            sidecar.write_text(json.dumps({"type": "overlay"}), encoding="utf-8")

            with (
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}),
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 1, 0.0, "failed", [])], "validate output")),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--skip-restore", "--skip-build", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertTrue(artifact.exists())
            self.assertTrue(sidecar.exists())
            self.assertEqual((root / "existing.txt").read_text(encoding="utf-8"), "before")
            self.assertFalse((root / "new.txt").exists())

    def test_apply_overlay_delete_artifact_on_failure_is_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "existing.txt").write_text("before", encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\ntest_command = "echo test"\n', encoding="utf-8")
            artifact = root / "artifact.zip"
            sidecar = root / "artifact.zip.devtool.json"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("existing.txt", "after")
            sidecar.write_text(json.dumps({"type": "overlay"}), encoding="utf-8")

            with (
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}),
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 1, 0.0, "failed", [])], "validate output")),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--skip-restore", "--skip-build", "--delete-artifact-on-failure", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertFalse(artifact.exists())
            self.assertFalse(sidecar.exists())

    def test_apply_overlay_preserves_corrupt_artifact_when_failure_precedes_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "broken.zip"
            artifact.write_bytes(b"not a zip archive")

            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", str(artifact)])

            self.assertNotEqual(rc, 0)
            self.assertTrue(artifact.exists())
            self.assertEqual(artifact.read_bytes(), b"not a zip archive")

    def test_apply_overlay_keep_artifact_preserves_original_after_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "existing.txt").write_text("before", encoding="utf-8")
            (root / ".devtool.toml").write_text('[project]\ntest_command = "echo test"\n', encoding="utf-8")
            artifact = root / "artifact.zip"
            sidecar = root / "artifact.zip.devtool.json"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("existing.txt", "after")
                zf.writestr("new.txt", "new")
            sidecar.write_text(json.dumps({"type": "overlay"}), encoding="utf-8")

            with (
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}),
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 1, 0.0, "failed", [])], "validate output")),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--skip-restore", "--skip-build", "--keep-artifact", str(artifact)])

            self.assertEqual(rc, 1)
            self.assertTrue(artifact.exists())
            self.assertTrue(sidecar.exists())
            self.assertEqual((root / "existing.txt").read_text(encoding="utf-8"), "before")
            self.assertFalse((root / "new.txt").exists())

    def test_apply_snapshot_skips_metadata_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "snapshot.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"type": "snapshot"}))
                zf.writestr("README.txt", "snapshot")

            rc = main(["--repo", str(root), "--yes", "apply-snapshot", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual((root / "README.txt").read_text(encoding="utf-8"), "snapshot")
            self.assertFalse((root / ".devtool-artifact.json").exists())

    def test_preview_excludes_metadata_but_shows_delete_manifest_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"type": "overlay"}))
                zf.writestr(".devtool-delete", "remove-me.txt\n")
                zf.writestr("keep.txt", "ok")

            preview = preview_artifact(root, artifact)

            self.assertEqual(preview["files"], 1)
            changes = {(item["kind"], item["path"]) for item in preview["changes"]}
            self.assertIn(("add", "keep.txt"), changes)
            self.assertIn(("delete", "remove-me.txt"), changes)
            self.assertNotIn(("add", ".devtool-artifact.json"), changes)
            self.assertNotIn(("add", ".devtool-delete"), changes)


    def test_diff_artifact_shows_repo_relative_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "src").mkdir()
            (root / "src" / "App.cs").write_text("old\n", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("src/App.cs", "new\n")
                zf.writestr("src/New.cs", "added\n")

            text = diff_artifact(root, artifact)

            self.assertIn("--- a/src/App.cs", text)
            self.assertIn("+++ b/src/App.cs", text)
            self.assertIn("-old", text)
            self.assertIn("+new", text)
            self.assertIn("--- /dev/null", text)
            self.assertIn("+++ b/src/New.cs", text)

    def test_apply_overlay_diff_is_preview_only_and_does_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "README.txt").write_text("old\n", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "new\n")

            stream = io.StringIO()
            with redirect_stdout(stream), patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "apply-overlay", "--diff", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual((root / "README.txt").read_text(encoding="utf-8"), "old\n")
            self.assertIn("--- a/README.txt", stream.getvalue())
            self.assertIn("+new", stream.getvalue())

    def test_apply_overlay_manifest_prompt_precedes_dirty_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"type": "overlay", "repo_name": root.name}))
                zf.writestr("README.txt", "ok")

            with (
                patch.object(__import__("devtool.cli").cli.console, "is_interactive", True),
                patch("devtool.cli.sys.stdin.isatty", return_value=True),
                patch("devtool.cli._git_dirty", return_value=True),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.Confirm.ask", side_effect=[True, True]) as mock_confirm,
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--no-clipboard", "apply-overlay", str(artifact)])

            self.assertEqual(rc, 0)
            prompts = [call.args[0] for call in mock_confirm.call_args_list]
            self.assertGreaterEqual(len(prompts), 2)
            self.assertEqual(prompts[0], "Continue with this artifact?")
            self.assertEqual(prompts[1], "Git tree is dirty. Continue?")
            self.assertTrue(mock_confirm.call_args_list[0].kwargs.get("default"))

    def test_yes_bypasses_manifest_dirty_and_interactive_apply_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"type": "overlay"}))
                zf.writestr("README.txt", "ok")

            with (
                patch("devtool.cli._git_dirty", return_value=True),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.Confirm.ask") as mock_confirm,
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--interactive", str(artifact)])

            self.assertEqual(rc, 0)
            mock_confirm.assert_not_called()

    def test_apply_overlay_validate_only_skips_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")

            with (
                patch("devtool.cli.apply_overlay") as mock_apply,
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")) as mock_run_specs,
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate-only", str(artifact)])

            self.assertEqual(rc, 0)
            mock_apply.assert_not_called()
            mock_run_specs.assert_called_once()

    def test_apply_overlay_validate_can_skip_restore_and_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")

            captured: list[str] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend(spec.name for spec in specs)
                return ([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--skip-restore", "--skip-build", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual(captured, ["test"])


    def test_ai_prompt_markdown_includes_named_target_details(self) -> None:
        options = _sample_ai_prompt_options("markdown")
        options.target_name = "s3pe"
        options.solution_target = "s3pe_avalonia/S3pe.Modern.sln"
        options.restore_target = "s3pe_avalonia/S3pe.Modern.sln"
        options.test_target = "s3pe_avalonia/S3pe.Modern.sln"
        text = render_ai_prompt(options, "markdown")
        self.assertIn('Target name: `s3pe`', text)
        self.assertIn('Solution target: `s3pe_avalonia/S3pe.Modern.sln`', text)
        self.assertIn('apply-overlay --validate "<downloaded artifact file>"', text)
        self.assertIn('--target "s3pe"', text)

    def test_apply_overlay_validate_uses_artifact_manifest_target_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "apps").mkdir()
            (root / "apps" / "S3pe.Modern.sln").write_text("", encoding="utf-8")
            (root / "tools").mkdir()
            (root / "tools" / "Tools.sln").write_text("", encoding="utf-8")
            (root / ".devtool.toml").write_text(
                "[project]\nkind='dotnet'\n"
                "\n[targets.s3pe]\nsolution='apps/S3pe.Modern.sln'\n"
                "\n[targets.tools]\nsolution='tools/Tools.sln'\n",
                encoding="utf-8",
            )
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(".devtool-artifact.json", json.dumps({"validate_target": "s3pe"}))
                zf.writestr("README.txt", "ok")

            captured: list[list[str]] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend([spec.argv for spec in specs])
                return [], ""

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--skip-restore", "--skip-build", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertTrue(any("apps/S3pe.Modern.sln" in " ".join(argv) for argv in captured))

    def test_apply_overlay_validate_can_commit_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")

            commit_calls: list[str] = []

            def fake_git_commit(repo_root, message, *, verbose=False, dry_run=False):
                commit_calls.append(message)
                return ([__import__("devtool.models").models.OperationResult("git-commit", ["git", "commit", "-m", message], 0, 0.0, "committed", [])], "commit output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")),
                patch("devtool.cli.git_commit", side_effect=fake_git_commit),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--commit", "handoff commit", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual(commit_calls, ["handoff commit"])

    def test_auto_commit_reconciles_complete_artifact_history_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)

            registry = repo / ".devtool" / "artifacts.json"
            registry.parent.mkdir(parents=True)
            registry.write_text('{"schema_version": 2, "artifacts": []}\n', encoding="utf-8")
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "Base"], cwd=repo, check=True)

            # Simulate registry records left by earlier failed/manual artifact runs.
            old_entries = repo / ".devtool" / "artifacts.d"
            old_entries.mkdir(parents=True)
            (old_entries / "old-failure.json").write_text('{"status": "rolled-back"}\n', encoding="utf-8")
            registry.write_text('{"schema_version": 2, "artifacts": [{"status": "rolled-back"}]}\n', encoding="utf-8")
            (repo / "new.txt").write_text("new\n", encoding="utf-8")

            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("new.txt", "new\n")

            args = SimpleNamespace(
                command="apply-overlay",
                commit="Apply artifact",
                artifact=str(artifact),
                verbose=False,
                dry_run=False,
                validate=False,
                validate_only=False,
                preview=False,
                _devtool_history_recorded=False,
                _devtool_artifact_type="overlay",
                _devtool_artifact_preview={"changes": [{"kind": "add", "path": "new.txt"}]},
                _devtool_artifact_manifest={},
                _devtool_artifact_sha="",
            )
            results = [OperationResult("apply-overlay", ["apply-overlay"], 0, 0.0, "ok", [])]
            config = ProjectConfig(repo_root=repo, kind="unknown")

            updated, _raw = _maybe_commit_after_apply(config, args, results, "apply ok")

            self.assertTrue(any(result.name == "git-commit" and result.returncode == 0 for result in updated))
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(status, "")
            committed = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertIn(".devtool/artifacts.json", committed)
            self.assertIn(".devtool/artifacts.d/old-failure.json", committed)
            self.assertIn("new.txt", committed)

    def test_apply_overlay_validate_skips_commit_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_overlay", return_value=__import__("devtool.models").models.OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 1, 0.0, "failed", [])], "validate output")),
                patch("devtool.cli.git_commit") as mock_commit,
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-overlay", "--validate", "--commit", "handoff commit", str(artifact)])

            self.assertEqual(rc, 1)
            mock_commit.assert_not_called()

    def test_apply_snapshot_validate_can_commit_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "snapshot.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")

            commit_calls: list[str] = []

            def fake_git_commit(repo_root, message, *, verbose=False, dry_run=False):
                commit_calls.append(message)
                return ([__import__("devtool.models").models.OperationResult("git-commit", ["git", "commit", "-m", message], 0, 0.0, "committed", [])], "commit output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_snapshot", return_value=__import__("devtool.models").models.OperationResult("apply-snapshot", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", return_value=([__import__("devtool.models").models.OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")),
                patch("devtool.cli.git_commit", side_effect=fake_git_commit),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--yes", "apply-snapshot", "--validate", "--commit", "snapshot handoff", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual(commit_calls, ["snapshot handoff"])

class TestNoBuildParserTests(unittest.TestCase):
    def test_parser_accepts_test_no_build_after_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["test", "--no-build"])
        self.assertEqual(args.command, "test")
        self.assertTrue(args.no_build)

    def test_parser_accepts_test_no_build_before_subcommand(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--no-build", "test"])
        self.assertEqual(args.command, "test")
        self.assertTrue(args.no_build)

class ValidationDotnetFlagTests(unittest.TestCase):
    def test_dotnet_validate_adds_no_restore_to_build_after_restore(self) -> None:
        from devtool.config import ProjectConfig
        from devtool.runners import build_validation_specs
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="dotnet", build_target="App.sln")
            specs = build_validation_specs(config)

        self.assertEqual(specs[0].argv, ["dotnet", "restore", "App.sln"])
        self.assertEqual(specs[1].argv, ["dotnet", "build", "--no-restore", "App.sln"])
        self.assertEqual(specs[2].argv, ["dotnet", "test", "--no-build", "App.sln"])

    def test_dotnet_validate_skip_restore_does_not_add_no_restore_to_build(self) -> None:
        from devtool.config import ProjectConfig
        from devtool.runners import build_validation_specs
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="dotnet", build_target="App.sln")
            specs = build_validation_specs(config, skip_restore=True)

        self.assertEqual(specs[0].argv, ["dotnet", "build", "App.sln"])
        self.assertEqual(specs[1].argv, ["dotnet", "test", "--no-build", "App.sln"])


def test_hud_overlay_can_be_requested_by_environment(monkeypatch, tmp_path) -> None:
    from devtool.cli import _normalize_live_progress_args
    import argparse

    monkeypatch.setenv("DEVTOOL_HUD_OVERLAY", "1")
    args = argparse.Namespace(command="validate", auto_hud=False, hud_overlay=False, hud_no_overlay=False)
    _normalize_live_progress_args(args)
    assert args.hud_overlay is True


class OverlayClipboardSummaryTests(unittest.TestCase):
    def test_single_overlay_copy_summary_names_applied_artifact(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            artifact = root / "named_overlay.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("new.txt", "new")
            copied: list[str] = []

            def capture(text: str, **_kwargs):
                copied.append(text)
                return SimpleNamespace(success=True, backend="test", reason="")

            with (
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(root / ".xdg-devtool")}),
                patch("devtool.cli.copy_text", side_effect=capture),
            ):
                rc = main([
                    "--repo", str(root), "--yes", "--copy-summary",
                    "apply-overlay", "--keep-artifact", str(artifact),
                ])

            self.assertEqual(rc, 0)
            self.assertTrue(copied)
            self.assertIn("Applied overlay: named_overlay.zip", copied[-1])
            self.assertIn(str(artifact), copied[-1])


def test_devtool_no_hud_overrides_all_hud_flags(monkeypatch) -> None:
    import argparse
    from devtool.cli import _normalize_live_progress_args

    monkeypatch.setenv("DEVTOOL_NO_HUD", "1")
    monkeypatch.setenv("DEVTOOL_HUD_OVERLAY", "1")
    args = argparse.Namespace(
        command="validate",
        auto_hud=True,
        hud_overlay=True,
        hud_no_overlay=False,
    )

    _normalize_live_progress_args(args)

    assert args.auto_hud is False
    assert args.tasker_progress is False
    assert args.hud_overlay is False
    assert args.hud_no_overlay is True
