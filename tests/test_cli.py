from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import tomllib
from unittest.mock import patch

from devtool.ai_prompt import AiPromptOptions, render_ai_prompt
from devtool.apply_notifications import build_apply_notification
from devtool.artifacts import diff_artifact, preview_artifact
from devtool import __version__
from devtool.cli import _apply_artifact_validation_defaults, _apply_runner_cli_options, _artifact_commit_paths, _attempt_auto_repair, _load_or_create_validation_session, _maybe_commit_after_apply, _maybe_copy, _record_apply_history, _run_sequential_overlays, _validate_apply_args, _validation_apply_context, _validation_expectation_result, _validation_specs_for_args, build_parser, main, split_runner_passthrough_args
from devtool.config import ProjectConfig, load_project_config
from devtool.log_commands import choose_active_log, parse_log_filename
from devtool.logstore import write_log
from devtool.xdg import cache_dir, data_dir, state_dir
from devtool.models import CommandSpec, Issue, OperationResult
from devtool.summary import build_json_summary
from devtool.transactions import find_active_transaction


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

    def test_artifact_inventory_is_forwarded_to_validation_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="android", runner_options={})
            args = SimpleNamespace(test_profile="", execution_environment="auto")
            manifest = {
                "inventory": {
                    "entries": [
                        {"path": "app/src/main/kotlin/Main.kt", "action": "modify"},
                        {"path": "docs/PHASE.md", "action": "add"},
                    ]
                }
            }

            _apply_artifact_validation_defaults(config, args, manifest)

        validation = config.runner_options["validation"]
        self.assertEqual(
            validation["changed_paths"],
            ["app/src/main/kotlin/Main.kt", "docs/PHASE.md"],
        )
        self.assertEqual(validation["changed_paths_source"], "artifact-inventory")

    def test_artifact_warning_expectations_do_not_gate_validation(self) -> None:
        manifest = {"validation": {"expected": {}}}
        infra = OperationResult(
            "lint",
            ["gradlew", "lintDebug"],
            0,
            0.0,
            "ok",
            [Issue("warning", "lint", "-", "GRADLE_INFRA", "AAPT2 override is experimental")],
        )
        source = OperationResult(
            "lint",
            ["gradlew", "lintDebug"],
            0,
            0.0,
            "ok",
            [Issue("warning", "lint", "app/src/Main.kt", "KOTLIN", "unused value")],
        )

        self.assertIsNone(_validation_expectation_result(manifest, [infra]))
        self.assertIsNone(_validation_expectation_result(manifest, [infra, source]))

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

        self.assertEqual([spec.metadata["matrix"] for spec in specs], ["debug", "release"])
        self.assertTrue(all(spec.metadata["coalesced_dotnet_validation"] for spec in specs))
        self.assertIn("Debug", specs[0].argv)
        self.assertIn("Release", specs[1].argv)

    def test_execution_environment_is_mutually_exclusive_with_legacy_chroot_flag(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--execution-environment", "chroot", "--chroot", "androidos", "status"])
    def test_parser_accepts_alias(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["v"])
        self.assertEqual(args.command, "v")

    def test_build_parser_accepts_artifact_output_override(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["build", "-o", "dist/apks", "-n", "demo-debug.apk"])
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="android")
        _apply_runner_cli_options(config, args)

        self.assertEqual(config.runner_options["artifacts"]["output_directory"], "dist/apks")
        self.assertEqual(config.runner_options["artifacts"]["output_name"], "demo-debug.apk")

    def test_parser_accepts_apply_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply-overlay", "--validate", "--commit", "msg", "artifact.zip"])
        self.assertEqual(args.command, "apply-overlay")
        self.assertTrue(args.validate)
        self.assertFalse(args.validate_only)
        self.assertEqual(args.commit, "msg")

    def test_unknown_target_returns_friendly_error_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "android").mkdir()
            (root / ".devtool.toml").write_text(
                "[project]\nkind='command'\n"
                "\n[targets.chrootctl_android]\ntype='command'\nroot='android'\n",
                encoding="utf-8",
            )
            stream = io.StringIO()

            with redirect_stderr(stream):
                rc = main(["--repo", str(root), "--target", "chroot_android", "validate-all"])

            stderr = stream.getvalue()
            self.assertEqual(rc, 1)
            self.assertIn("Error: Unknown target 'chroot_android'", stderr)
            self.assertIn("Available targets: chrootctl_android", stderr)
            self.assertIn("Did you mean 'chrootctl_android'?", stderr)
            self.assertIn("Run 'devtool targets' to list configured targets.", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_internal_error_is_concise_without_verbose(self) -> None:
        stream = io.StringIO()

        with patch("devtool.cli._normalize_command_aliases", side_effect=RuntimeError("boom")):
            with redirect_stderr(stream):
                rc = main(["status"])

        stderr = stream.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("Internal devtool error: boom", stderr)
        self.assertIn("Run again with --verbose or DEVTOOL_TRACEBACK=1", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_internal_error_prints_traceback_with_verbose(self) -> None:
        stream = io.StringIO()

        with patch("devtool.cli._normalize_command_aliases", side_effect=RuntimeError("boom")):
            with redirect_stderr(stream):
                rc = main(["--verbose", "status"])

        stderr = stream.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("Traceback", stderr)
        self.assertIn("RuntimeError: boom", stderr)

    def test_parser_accepts_artifact_cleanup_flags_for_apply(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply-overlay", "--keep-artifact", "artifact.zip"])
        self.assertTrue(args.keep_artifact)
        args = parser.parse_args(["apply-overlay", "--delete-artifact-on-failure", "artifact.zip"])
        self.assertTrue(args.delete_artifact_on_failure)

    def test_parser_accepts_apply_notification_opt_out(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["apply", "snapshot", "--no-apply-notification", "artifact.zip"])
        self.assertTrue(args.no_apply_notification)

    def test_apply_notification_summary_is_concise(self) -> None:
        args = SimpleNamespace(command="apply-overlay", artifact="demo_overlay.zip")
        config = ProjectConfig(repo_root=Path("/tmp/demo"), kind="dotnet")
        results = [
            OperationResult("apply-overlay", ["apply-overlay"], 0, 1.2, "ok", []),
            OperationResult("git-commit", ["git", "commit"], 0, 0.3, "ok", [], metadata={"commit_hash": "abcdef1234567890"}),
        ]
        notification = build_apply_notification(args, config, results, {"failed": 0, "warnings": 1, "errors": 0})
        self.assertEqual(notification.title, "Devtool overlay applied")
        self.assertIn("demo", notification.body)
        self.assertIn("demo_overlay.zip", notification.body)
        self.assertIn("commit abcdef123456", notification.body)
        self.assertEqual(notification.toast, "Applied: demo_overlay.zip")

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
        cleanup_mock.assert_called_once()
        cleanup_kwargs = cleanup_mock.call_args.kwargs
        self.assertEqual(cleanup_kwargs["repo_root"], root)
        self.assertEqual(cleanup_kwargs["backend"], "desktop-linux")
        self.assertFalse(cleanup_kwargs["dry_run"])
        self.assertTrue(cleanup_kwargs["extra_cache_paths"])
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

    def test_parser_accepts_validate_all_with_validation_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["validate-all", "--skip-restore", "--skip-build", "--matrix", "debug,release"])
        self.assertEqual(args.command, "validate-all")
        self.assertTrue(args.skip_restore)
        self.assertTrue(args.skip_build)
        self.assertEqual(args.matrix, "debug,release")

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

    def test_active_log_selection_follows_android_bridge_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "DEVTOOL_XDG_DIR": str(Path(tmp) / "state"),
                "DEVTOOL_HUD_BRIDGE_DIR": str(Path(tmp) / "bridge"),
                "XDG_RUNTIME_DIR": "",
            },
            clear=False,
        ):
            bridge = Path(tmp) / "bridge"
            runs = bridge / "runs"
            logs = bridge / "logs"
            runs.mkdir(parents=True)
            logs.mkdir(parents=True)
            log_path = logs / "20260716-162523-546390.log"
            log_path.write_text("bridge-live", encoding="utf-8")
            (runs / "bridge-run.json").write_text(
                json.dumps(
                    {
                        "run_id": "bridge-run",
                        "pid": os.getpid(),
                        "command": "build",
                        "status": "running",
                        "updated_at": "2026-07-16T16:25:23Z",
                        "log_path": str(log_path),
                    }
                ),
                encoding="utf-8",
            )

            selected = choose_active_log(use_fzf=False)

        self.assertEqual(selected, log_path)

    def test_active_log_selection_treats_permission_denied_pid_as_alive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state"), "XDG_RUNTIME_DIR": ""},
            clear=False,
        ):
            state = Path(tmp) / "state"
            runs = state / "runs"
            logs = state / "logs"
            runs.mkdir(parents=True)
            logs.mkdir(parents=True)
            log_path = logs / "20260716-172523-546390.log"
            log_path.write_text("cross-user-live", encoding="utf-8")
            (runs / "cross-user.json").write_text(
                json.dumps(
                    {
                        "run_id": "cross-user",
                        "pid": 424242,
                        "command": "build",
                        "status": "running",
                        "log_path": str(log_path),
                    }
                ),
                encoding="utf-8",
            )
            with patch("devtool.log_commands.os.kill", side_effect=PermissionError):
                selected = choose_active_log(use_fzf=False)

        self.assertEqual(selected, log_path)

    def test_active_log_selection_uses_authoritative_run_lock_without_hud(self) -> None:
        from devtool.single_run import SingleRunLock

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {
                "DEVTOOL_XDG_DIR": str(Path(tmp) / "state"),
                "XDG_RUNTIME_DIR": str(Path(tmp) / "runtime"),
                "DEVTOOL_NO_HUD": "1",
            },
            clear=False,
        ):
            repo = Path(tmp) / "repo"
            repo.mkdir()
            log_path = Path(tmp) / "state" / "logs" / "20260716-182523-546390.log"
            log_path.parent.mkdir(parents=True)
            log_path.write_text("lock-live", encoding="utf-8")
            lock = SingleRunLock(repo, command="build", run_id="lock-run", log_path=str(log_path)).acquire()
            try:
                selected = choose_active_log(use_fzf=False)
            finally:
                lock.release()

        self.assertEqual(selected, log_path)

    def test_active_log_selection_defaults_to_newest_run_without_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"DEVTOOL_XDG_DIR": str(Path(tmp) / "state"), "XDG_RUNTIME_DIR": ""},
            clear=False,
        ):
            state = Path(tmp) / "state"
            runs = state / "runs"
            logs = state / "logs"
            runs.mkdir(parents=True)
            logs.mkdir(parents=True)
            older = logs / "20260716-182523-546390.log"
            newer = logs / "20260716-192523-546390.log"
            older.write_text("older", encoding="utf-8")
            newer.write_text("newer", encoding="utf-8")
            (runs / "older.json").write_text(
                json.dumps(
                    {
                        "run_id": "older",
                        "pid": os.getpid(),
                        "status": "running",
                        "updated_at": "2026-07-16T18:25:23Z",
                        "log_path": str(older),
                    }
                ),
                encoding="utf-8",
            )
            (runs / "newer.json").write_text(
                json.dumps(
                    {
                        "run_id": "newer",
                        "pid": os.getpid(),
                        "status": "running",
                        "updated_at": "2026-07-16T19:25:23Z",
                        "log_path": str(newer),
                    }
                ),
                encoding="utf-8",
            )
            with patch("devtool.log_commands.sys.stdin.isatty", return_value=False):
                selected = choose_active_log(use_fzf=False)

        self.assertEqual(selected, newer)

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

    def test_validate_all_continues_on_error_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            captured: list[bool] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.append(bool(kwargs.get("continue_on_error")))
                return ([
                    OperationResult("restore", ["dotnet", "restore"], 1, 0.0, "restore failed", []),
                    OperationResult("build", ["dotnet", "build"], 1, 0.0, "build failed", []),
                ], "validate output")

            with (
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "validate-all"])

            self.assertEqual(rc, 1)
            self.assertEqual(captured, [True])

    def test_validate_all_fail_fast_overrides_aggregate_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            captured: list[bool] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.append(bool(kwargs.get("continue_on_error")))
                return ([OperationResult("restore", ["dotnet", "restore"], 1, 0.0, "restore failed", [])], "validate output")

            with (
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--fail-fast", "validate-all"])

            self.assertEqual(rc, 1)
            self.assertEqual(captured, [False])

    def test_validate_remains_fail_fast_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            captured: list[bool] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.append(bool(kwargs.get("continue_on_error")))
                return ([OperationResult("restore", ["dotnet", "restore"], 1, 0.0, "restore failed", [])], "validate output")

            with (
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "validate"])

            self.assertEqual(rc, 1)
            self.assertEqual(captured, [False])

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

    def test_init_alias_writes_detected_devtool_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text("[package]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            rc = main(["init", str(root)])
            text = (root / ".devtool.toml").read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        self.assertIn('kind = "rust"', text)

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
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        json_text = json.dumps({
            "warnings": 0,
            "errors": 1,
            "failed": 1,
            "skipped": 0,
            "completion": {"headline": "Build failed; changes rolled back"},
        })
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        text = copied[0]
        self.assertTrue(text.startswith("Build failed; changes rolled back"))
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

    def test_failed_overlay_copy_keeps_artifact_context(self) -> None:
        raw_output = (
            "$ python3 /home/mike/.local/share/devtool/runtime/android/build-apk /repo/app/XDM.Android --task assembleDebug\n"
            "e: file:///repo/app/XDM.Android/app/src/main/kotlin/Screens.kt:2367:1 Modifier 'private' is not applicable to 'local function'.\n"
            "e: file:///repo/app/XDM.Android/app/src/main/kotlin/Screens.kt:2377:12 Syntax error: Expecting '}'.\n"
            "\n> Task :app:compileDebugKotlin FAILED\n"
            "\n$ devtool rollback\n"
            "Rolled back changes.\n"
        )
        summary_text = (
            "apply-overlay: ok exit=0 time=0.05s\n"
            "android-validation-build: failed exit=2 time=60.50s\n"
            "apply-rollback: ok exit=0 time=0.07s\n"
        )
        json_text = json.dumps(
            {
                "warnings": 0,
                "errors": 2,
                "failed": 1,
                "skipped": 0,
                "results": [
                    {
                        "name": "android-validation-build",
                        "issues": [
                            {
                                "severity": "error",
                                "command": "android-validation-build",
                                "file": "-",
                                "line": None,
                                "code": "GRADLE_TASK",
                                "message": "Gradle task failed: :app:testDebugUnitTest",
                            },
                            {
                                "severity": "error",
                                "command": "android-validation-build",
                                "file": "app/build/test-results/testDebugUnitTest/TEST-com.mikeyphw.xdm.android.ArchitectureContractTest.xml",
                                "line": None,
                                "code": "GRADLE_TEST",
                                "message": (
                                    "com.mikeyphw.xdm.android.ArchitectureContractTest.browserAndShareHandoffsOpenDownloadWorkflows: "
                                    "java.lang.AssertionError: ShareSheet intake must inspect shared text and clip data"
                                ),
                            },
                        ],
                    }
                ],
                "overlays": [
                    {
                        "index": 1,
                        "total": 1,
                        "label": "xdm_android_handoff_screenshot_audit_overlay.zip",
                        "target": "xdm_android",
                        "artifact": "/home/mike/Downloads/xdm_android_handoff_screenshot_audit_overlay.zip",
                        "warnings": 0,
                        "errors": 2,
                        "failed": 1,
                        "skipped": 0,
                        "duration_seconds": 60.62,
                    }
                ],
            }
        )
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        text = copied[0]
        self.assertIn("Overlay: xdm_android_handoff_screenshot_audit_overlay.zip [xdm_android]", text)
        self.assertIn("- Artifact: /home/mike/Downloads/xdm_android_handoff_screenshot_audit_overlay.zip", text)
        self.assertIn("- Diagnostics: 0 warnings, 5 errors", text)
        self.assertIn("- Gradle task failed: :app:testDebugUnitTest", text)
        self.assertIn("- com.mikeyphw.xdm.android.ArchitectureContractTest.browserAndShareHandoffsOpenDownloadWorkflows: java.lang.AssertionError: ShareSheet intake must inspect shared text and clip data", text)
        self.assertIn("Failed: android validation build", text)
        self.assertIn("Recovery: rollback succeeded", text)
        self.assertIn("Failure reason:", text)
        self.assertIn("- Gradle task failed: :app:testDebugUnitTest", text)
        self.assertIn("e: file:///repo/app/XDM.Android/app/src/main/kotlin/Screens.kt:2367:1 Modifier", text)
        self.assertIn("e: file:///repo/app/XDM.Android/app/src/main/kotlin/Screens.kt:2377:12 Syntax error", text)
        self.assertIn("compileDebugKotlin FAILED", text)

    def test_failure_copy_includes_all_structured_and_raw_warning_error_details(self) -> None:
        raw_output = (
            "$ ./gradlew lintDebug testDebugUnitTest\n"
            "warning: SDK package fallback used for fixture device.\n"
            "app/src/main/kotlin/Demo.kt:7:5: warning: Deprecated call.\n"
            "e: file:///repo/app/src/main/kotlin/Demo.kt:9:2 Unresolved reference: missingSymbol.\n"
            "> Task :app:testDebugUnitTest FAILED\n"
            "FAILURE: Build failed with an exception.\n"
        )
        summary_text = "android-validation-build: failed exit=1 time=4.00s\n"
        json_text = json.dumps(
            {
                "warnings": 1,
                "errors": 1,
                "failed": 1,
                "skipped": 0,
                "results": [
                    {
                        "name": "android-validation-build",
                        "returncode": 1,
                        "failure_kind": "validation_failure",
                        "output": raw_output,
                        "issues": [
                            {
                                "severity": "warning",
                                "command": "lint",
                                "file": "app/src/main/kotlin/StructuredWarning.kt",
                                "line": 3,
                                "code": "NewApi",
                                "message": "Requires API 33.",
                            },
                            {
                                "severity": "error",
                                "command": "lint",
                                "file": "app/src/main/kotlin/StructuredError.kt",
                                "line": 4,
                                "code": "FatalLint",
                                "message": "Structured fatal lint error.",
                            },
                        ],
                    }
                ],
                "overlays": [
                    {
                        "index": 1,
                        "total": 1,
                        "label": "demo.zip",
                        "target": "devtool",
                        "warnings": 1,
                        "errors": 1,
                        "duration_seconds": 4.0,
                    }
                ],
            }
        )
        args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto")
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        text = copied[0]
        self.assertIn("- Diagnostics: 3 warnings, 4 errors", text)
        self.assertIn("Failure reason:", text)
        self.assertIn("android validation build failed with exit 1: app/src/main/kotlin/StructuredError.kt:4: FatalLint: Structured fatal lint error.", text)
        self.assertIn("Errors:", text)
        self.assertIn("app/src/main/kotlin/StructuredError.kt:4: FatalLint: Structured fatal lint error.", text)
        self.assertIn("e: file:///repo/app/src/main/kotlin/Demo.kt:9:2 Unresolved reference: missingSymbol.", text)
        self.assertIn("> Task :app:testDebugUnitTest FAILED", text)
        self.assertIn("FAILURE: Build failed with an exception.", text)
        self.assertIn("Warnings:", text)
        self.assertIn("app/src/main/kotlin/StructuredWarning.kt:3: NewApi: Requires API 33.", text)
        self.assertIn("warning: SDK package fallback used for fixture device.", text)
        self.assertIn("app/src/main/kotlin/Demo.kt:7:5: warning: Deprecated call.", text)


    def test_failure_reason_uses_raw_argparse_error_when_failure_kind_is_unknown(self) -> None:
        raw_output = (
            "$ python3 /home/mike/.local/share/devtool/runtime/android/build-apk /repo "
            "--java-provider auto\n"
            "usage: build-apk [-h] [-p PROJECT_DIR] [-x [{auto,always,never}]]\n"
            "build-apk: error: unrecognized arguments: --java-provider auto --gradle-provider auto\n"
        )
        summary_text = "android-validation-explicit-tasks: failed exit=2 time=4.67s\n"
        json_text = json.dumps(
            {
                "warnings": 0,
                "errors": 0,
                "failed": 1,
                "results": [
                    {
                        "name": "android-validation-explicit-tasks",
                        "returncode": 2,
                        "failure_kind": "unknown_failure",
                        "output": raw_output,
                        "issues": [],
                    }
                ],
            }
        )
        args = SimpleNamespace(no_clipboard=False, copy=True, copy_via="auto")
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        text = copied[0]
        self.assertIn("Failure reason:", text)
        self.assertIn(
            "android validation explicit tasks failed with exit 2: build-apk: error: unrecognized arguments: --java-provider auto --gradle-provider auto",
            text,
        )
        self.assertIn("Errors:", text)
        self.assertIn("build-apk: error: unrecognized arguments: --java-provider auto --gradle-provider auto", text)

    def test_success_copy_includes_structured_warning_details(self) -> None:
        raw_output = (
            "$ ./gradlew lintDebug\n"
            "Devtool Android Lint: 0 errors, 12 warnings.\n"
        )
        issues = [
            {
                "severity": "warning",
                "command": "lint",
                "file": "app/src/main/AndroidManifest.xml",
                "line": 12,
                "code": "ExportedReceiver",
                "message": "Receiver should explicitly set exported.",
            },
            {
                "severity": "warning",
                "command": "lint",
                "file": "app/src/main/kotlin/MainActivity.kt",
                "line": 44,
                "code": "NewApi",
                "message": "Call requires API level 33.",
            },
        ]
        issues.extend(
            {
                "severity": "warning",
                "command": "lint",
                "file": f"app/src/main/kotlin/Generated{index}.kt",
                "line": index,
                "code": "GeneratedWarning",
                "message": f"Generated warning {index}.",
            }
            for index in range(3, 13)
        )
        summary_text = "build: ok exit=0 time=3.00s\ngit-commit: ok exit=0 time=0.20s\n"
        json_text = json.dumps(
            {
                "warnings": 12,
                "errors": 0,
                "failed": 0,
                "skipped": 0,
                "results": [
                    {
                        "name": "build",
                        "issues": issues,
                    }
                ],
            }
        )
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        self.assertIn("Warnings:", copied[0])
        self.assertIn("app/src/main/AndroidManifest.xml:12: ExportedReceiver: Receiver should explicitly set exported.", copied[0])
        self.assertIn("app/src/main/kotlin/MainActivity.kt:44: NewApi: Call requires API level 33.", copied[0])
        self.assertIn("app/src/main/kotlin/Generated12.kt:12: GeneratedWarning: Generated warning 12.", copied[0])
        self.assertNotIn("more unique warning", copied[0])
        self.assertNotIn("individual warning text was not available", copied[0])

    def test_success_copy_counts_raw_command_warning_details(self) -> None:
        raw_output = (
            "$ uv pip install pytest\n"
            "warning: Failed to hardlink files; falling back to full copy. This may lead to degraded performance.\n"
        )
        summary_text = "apply-overlay: ok exit=0 time=0.50s\ngit-commit: ok exit=0 time=0.10s\n"
        json_text = json.dumps(
            {
                "warnings": 0,
                "errors": 0,
                "failed": 0,
                "skipped": 0,
                "overlays": [
                    {
                        "index": 1,
                        "total": 1,
                        "label": "devtool_overlay.zip",
                        "target": "devtool",
                        "warnings": 0,
                        "errors": 0,
                        "duration_seconds": 1.0,
                    }
                ],
            }
        )
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_via="auto",
        )
        copied: list[str] = []
        result = SimpleNamespace(success=True, backend="test", reason="")
        with patch("devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or result), patch("devtool.cli.console.print"):
            _maybe_copy(raw_output, summary_text, json_text, args)

        self.assertEqual(len(copied), 1)
        self.assertIn("- Diagnostics: 1 warnings, 0 errors", copied[0])
        self.assertIn("Diagnostics: 1 warnings, 0 errors", copied[0])
        self.assertIn("Warnings:", copied[0])
        self.assertIn("warning: Failed to hardlink files; falling back to full copy.", copied[0])
        self.assertNotIn("Diagnostics: 0 warnings, 0 errors", copied[0])

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
                patch("devtool.cli.register_transaction_paths") as journal_repair,
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

    def test_machine_file_writes_machine_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            machine_file = root / "machine.json"

            with patch("devtool.cli.console.print"):
                rc = main(["--repo", str(root), "--machine-file", str(machine_file), "--dry-run", "validate"])

            self.assertEqual(rc, 0)
            self.assertTrue(machine_file.is_file())
            payload = json.loads(machine_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("clipboard", payload)
            self.assertIn("notification", payload)

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

    def test_final_copy_runs_after_live_progress_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            events: list[str] = []

            def finish_progress(*_args, **_kwargs):
                events.append("finish")

            def copy_summary(*_args, **_kwargs):
                events.append("copy")
                return __import__("devtool.clipboard").clipboard.ClipboardResult(True, backend="test")

            with (
                patch("devtool.cli._finish_live_progress", side_effect=finish_progress),
                patch("devtool.cli.copy_text", side_effect=copy_summary),
            ):
                rc = main(["--repo", str(root), "--copy", "--dry-run", "validate"])

        self.assertEqual(rc, 0)
        self.assertEqual(events[-2:], ["finish", "copy"])

    def test_copy_allows_success_after_stale_recovery_cleanup(self) -> None:
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_via="auto",
            command="apply-overlay",
        )
        raw_output = "previous devtool apply appears to have been interrupted\nCompleted successfully"
        json_text = json.dumps({"ok": True, "warnings": 0, "errors": 0, "failed": 0, "skipped": 0})

        with (
            patch("devtool.cli.copy_text", return_value=__import__("devtool.clipboard").clipboard.ClipboardResult(True, backend="xclip")),
            patch("devtool.cli.console.print") as mock_print,
        ):
            _maybe_copy(raw_output, "summary", json_text, args)

        rendered = "\n".join(" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list)
        self.assertIn("Successful run summary copied to clipboard", rendered)
        self.assertNotIn("Clipboard copy skipped", rendered)

    def test_explicit_copy_allows_failure_after_recovery_guard(self) -> None:
        args = SimpleNamespace(
            no_clipboard=False,
            copy=True,
            copy_via="auto",
            command="apply-overlay",
        )
        raw_output = (
            "previous devtool apply appears to have been interrupted\n"
            "Gradle task failed: :app:compileDebugJavaWithJavac"
        )
        json_text = json.dumps({"ok": False, "warnings": 0, "errors": 1, "failed": 1, "skipped": 0})

        with (
            patch("devtool.cli.copy_text", return_value=__import__("devtool.clipboard").clipboard.ClipboardResult(True, backend="xclip")),
            patch("devtool.cli.console.print") as mock_print,
        ):
            _maybe_copy(raw_output, "summary", json_text, args)

        rendered = "\n".join(" ".join(str(arg) for arg in call.args) for call in mock_print.call_args_list)
        self.assertIn("Failure summary copied to clipboard", rendered)
        self.assertNotIn("Clipboard copy skipped", rendered)

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

    def test_parser_accepts_stdout_payload_summary(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--stdout-payload", "summary", "validate"])
        self.assertEqual(args.stdout_payload, "summary")

    def test_parser_rejects_removed_stdout_payload_aliases(self) -> None:
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--stdout-payload", "json", "validate"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--stdout-payload", "machine-json", "validate"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["--stdout-payload", "runner-json", "validate"])

    def test_removed_json_flag_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--repo", str(root), "--json", "validate"])

    def test_copy_flags_must_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                main(["--repo", str(root), "--copy", "--no-clipboard", "validate"])

    def test_stdout_payload_summary_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["--repo", str(root), "--stdout-payload", "summary", "--dry-run", "validate"])
            self.assertEqual(rc, 0)
            output = stream.getvalue()
            self.assertTrue(output.strip())
            self.assertIn("skipped", output)

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

    def test_apply_overlay_no_validate_verifies_and_closes_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / ".git").mkdir()
            artifact = Path(tmp) / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(
                    ".devtool-artifact.json",
                    json.dumps({"type": "overlay", "validation": {"required": True}}),
                )
                zf.writestr("new.txt", "new")

            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False):
                rc = main([
                    "--repo", str(root),
                    "--yes",
                    "--no-clipboard",
                    "apply-overlay",
                    "--no-validate",
                    "--keep-artifact",
                    str(artifact),
                ])
                self.assertEqual(rc, 0)
                self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "new")
                self.assertIsNone(find_active_transaction(root))

                status_rc = main(["--repo", str(root), "--yes", "--no-clipboard", "status"])

            self.assertEqual(status_rc, 0)
            self.assertEqual((root / "new.txt").read_text(encoding="utf-8"), "new")

    def test_apply_overlay_verification_failure_preserves_artifact_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            artifact = Path(tmp) / "artifact.zip"
            artifact.write_bytes(b"placeholder")
            config = ProjectConfig(repo_root=root, kind="unknown", runner_options={})
            rollback = OperationResult("apply-rollback", ["rsync", "rollback"], 0, 0.1, "rolled back", [])

            def fake_apply(_config, _args, _name, _artifact, **_kwargs):
                _args._devtool_artifact_preview = {"changes": [{"kind": "add", "path": "missing.txt"}]}
                _args._devtool_artifact_type = "overlay"
                return [
                    OperationResult(
                        "apply-overlay",
                        ["apply-overlay", str(artifact)],
                        0,
                        0.1,
                        "applied",
                        [],
                        metadata={"transaction_path": str(root / "tx.json"), "artifact_path": str(artifact), "delete_artifact_on_success": True},
                    )
                ], "apply raw"

            with (
                patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False),
                patch("devtool.cli._load_command_config", return_value=config),
                patch("devtool.cli._apply_artifact_manifest_defaults"),
                patch("devtool.cli.find_active_transaction", return_value=None),
                patch("devtool.cli.read_artifact_manifest", return_value={}),
                patch("devtool.cli._run_apply", side_effect=fake_apply),
                patch("devtool.cli._run_transaction_rollback", return_value=(rollback, "rollback raw")) as mock_rollback,
                patch("devtool.cli._delete_original_artifact_after_apply") as mock_cleanup,
                patch("devtool.cli._emit_summary", return_value=(
                    {"warnings": 0, "errors": 1, "failed": 1, "skipped": 0},
                    "",
                    "{}",
                    "{}",
                )),
                patch("devtool.cli._finish_live_progress"),
                patch("devtool.cli.notify_apply_finished"),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["--repo", str(root), "--no-clipboard", "apply-overlay", "--no-validate", str(artifact)])

            self.assertEqual(rc, 1)
            mock_rollback.assert_called_once()
            mock_cleanup.assert_not_called()
            self.assertTrue(artifact.exists())

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

    def test_apply_overlay_continues_after_successful_stale_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            artifact = root / "artifact.zip"
            config = ProjectConfig(repo_root=root, kind="android", runner_options={})
            calls: list[str] = []
            rollback_kwargs: list[dict[str, object]] = []
            summary_calls: list[list[str]] = []

            def fake_stale_rollback(*args, **kwargs):
                calls.append("rollback")
                rollback_kwargs.append(kwargs)
                return (
                    OperationResult(
                        "apply-rollback",
                        ["rsync", "rollback"],
                        0,
                        0.1,
                        "rolled back",
                        [],
                        failure_kind=str(kwargs.get("failure_kind") or ""),
                    ),
                    "$ rsync rollback\nrolled back",
                )

            def fake_emit_summary(results, config, **kwargs):
                summary_calls.append([result.name for result in results])
                summary_text = "\n".join(
                    f"{result.name}: ok exit={result.returncode} time={result.duration_seconds:.2f}s"
                    for result in results
                )
                return (
                    {"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
                    summary_text,
                    json.dumps({"warnings": 0, "errors": 0, "failed": 0, "skipped": 0}),
                    json.dumps({"results": [result.name for result in results]}),
                )

            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False), \
                patch("devtool.cli._load_command_config", return_value=config), \
                patch("devtool.cli._apply_artifact_manifest_defaults"), \
                patch("devtool.cli.find_active_transaction", return_value=str(root / "stale.tx")), \
                patch("devtool.cli.transaction_pending_validation", return_value=False), \
                patch("devtool.cli.read_artifact_manifest", return_value={"validation": {"rollback_on_failure": True}}), \
                patch("devtool.cli._run_transaction_rollback", side_effect=fake_stale_rollback), \
                patch("devtool.cli._run_apply", side_effect=lambda *args, **kwargs: (calls.append("apply") or [OperationResult("apply-overlay", ["apply-overlay", str(artifact)], 0, 0.2, "applied", [], metadata={"transaction_path": str(root / "tx")})], "apply raw")), \
                patch("devtool.cli._reload_config_after_artifact_apply", return_value=config), \
                patch("devtool.cli._maybe_commit_after_apply", side_effect=lambda config, args, results, raw_output, log_path=None: (results, raw_output)), \
                patch("devtool.cli._record_apply_history"), \
                patch("devtool.cli._delete_original_artifact_after_apply", return_value=None), \
                patch("devtool.cli._finish_live_progress"), \
                patch("devtool.cli.notify_apply_finished"), \
                patch("devtool.cli._emit_summary", side_effect=fake_emit_summary), \
                patch("devtool.cli.render_summary"):
                rc = main(["--repo", str(root), "--no-clipboard", "apply-overlay", str(artifact)])

            self.assertEqual(rc, 0)
            self.assertEqual(calls, ["rollback", "apply"])
            self.assertEqual(rollback_kwargs[-1]["failure_kind"], "stale_apply_rolled_back")
            self.assertEqual(summary_calls[-1], ["apply-rollback", "apply-overlay", "apply-verification"])

    def test_apply_history_uses_actual_apply_result_after_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            config = ProjectConfig(repo_root=root, kind="android", runner_options={})
            args = SimpleNamespace(
                command="apply-overlay",
                artifact=str(root / "artifact.zip"),
                validate=False,
                validate_only=False,
                preview=False,
                commit="",
                _devtool_artifact_type="overlay",
                _devtool_artifact_preview=None,
                _devtool_artifact_manifest=None,
                _devtool_artifact_sha="",
            )
            captured: list[dict[str, object]] = []

            def fake_make_history_entry(*args, **kwargs):
                captured.append(kwargs)
                return {"status": kwargs["status"]}

            results = [
                OperationResult("apply-rollback", ["rsync", "rollback"], 0, 0.1, "stale", [], failure_kind="stale_apply_rolled_back"),
                OperationResult("apply-overlay", ["devtool", "apply-overlay"], 1, 0.2, "failed", []),
            ]

            with (
                patch("devtool.cli.make_history_entry", side_effect=fake_make_history_entry),
                patch("devtool.cli.append_history", return_value=root / ".devtool" / "entry.json"),
            ):
                _record_apply_history(config, args, results)

            self.assertEqual(captured[-1]["status"], "failed")

    def test_apply_overlay_no_rollback_on_failure_skips_validation_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            artifact = root / "artifact.zip"
            config = ProjectConfig(repo_root=root, kind="android", runner_options={})
            validation_failure = OperationResult("build", ["./gradlew", "build"], 1, 0.3, "boom", [])

            def fake_emit_summary(results, config, **kwargs):
                summary_text = "\n".join(
                    f"{result.name}: {'ok' if result.returncode == 0 else 'failed'} exit={result.returncode} time={result.duration_seconds:.2f}s"
                    for result in results
                )
                return (
                    {"warnings": 0, "errors": 1, "failed": 1, "skipped": 0},
                    summary_text,
                    json.dumps({"warnings": 0, "errors": 1, "failed": 1, "skipped": 0}),
                    json.dumps({"results": [result.name for result in results]}),
                )

            with patch.dict("os.environ", {"DEVTOOL_XDG_DIR": str(Path(tmp) / "xdg")}, clear=False), \
                patch("devtool.cli._load_command_config", return_value=config), \
                patch("devtool.cli._apply_artifact_manifest_defaults"), \
                patch("devtool.cli.find_active_transaction", return_value=None), \
                patch("devtool.cli.read_artifact_manifest", return_value={"validation": {"rollback_on_failure": True}}), \
                patch("devtool.cli._run_apply", return_value=([OperationResult("apply-overlay", ["apply-overlay", str(artifact)], 0, 0.2, "applied", [], metadata={"transaction_path": str(root / "tx")})], "apply raw")), \
                patch("devtool.cli._reload_config_after_artifact_apply", return_value=config), \
                patch("devtool.cli._validation_specs_for_args", return_value=[CommandSpec("build", ["./gradlew", "build"])]), \
                patch("devtool.cli._run_specs_for_args", return_value=([validation_failure], "validation raw")), \
                patch("devtool.cli._attempt_auto_repair", return_value=([validation_failure], "", None)), \
                patch("devtool.cli._maybe_commit_after_apply", side_effect=lambda config, args, results, raw_output, log_path=None: (results, raw_output)), \
                patch("devtool.cli._record_apply_history"), \
                patch("devtool.cli._delete_original_artifact_after_apply", return_value=None), \
                patch("devtool.cli._finish_live_progress"), \
                patch("devtool.cli.notify_apply_finished"), \
                patch("devtool.cli._emit_summary", side_effect=fake_emit_summary), \
                patch("devtool.cli._run_transaction_rollback", side_effect=AssertionError("rollback should not run when disabled")), \
                patch("devtool.cli.render_summary"):
                rc = main([
                    "--repo", str(root),
                    "--no-clipboard",
                    "apply-overlay",
                    "--validate",
                    "--no-rollback-on-failure",
                    str(artifact),
                ])

            self.assertEqual(rc, 1)

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
                zf.writestr(".pytest_cache/v/cache/nodeids", "[]")
                zf.writestr("tests/__pycache__/test_app.cpython-313.pyc", b"\0pyc")

            preview = preview_artifact(root, artifact)

            self.assertEqual(preview["files"], 1)
            changes = {(item["kind"], item["path"]) for item in preview["changes"]}
            self.assertIn(("add", "keep.txt"), changes)
            self.assertIn(("delete", "remove-me.txt"), changes)
            self.assertNotIn(("add", ".devtool-artifact.json"), changes)
            self.assertNotIn(("add", ".devtool-delete"), changes)
            self.assertNotIn(("add", ".pytest_cache/v/cache/nodeids"), changes)
            self.assertNotIn(("add", "tests/__pycache__/test_app.cpython-313.pyc"), changes)


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
                zf.writestr(".pytest_cache/README.md", "cache")
                zf.writestr("src/__pycache__/App.cpython-313.pyc", b"\0pyc")

            text = diff_artifact(root, artifact)

            self.assertIn("--- a/src/App.cs", text)
            self.assertIn("+++ b/src/App.cs", text)
            self.assertIn("-old", text)
            self.assertIn("+new", text)
            self.assertIn("--- /dev/null", text)
            self.assertIn("+++ b/src/New.cs", text)
            self.assertNotIn(".pytest_cache", text)
            self.assertNotIn("__pycache__", text)

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

    def test_artifact_commit_paths_skip_missing_untracked_history_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "payload.txt").write_text("payload\n", encoding="utf-8")
            args = SimpleNamespace(
                _devtool_artifact_preview={"changes": [{"kind": "add", "path": "payload.txt"}]},
                _devtool_artifact_manifest={},
            )

            paths = _artifact_commit_paths(args, repo)

            self.assertEqual(paths, ["payload.txt"])
            self.assertNotIn(".devtool/artifacts.json", paths)
            self.assertNotIn(".devtool/artifacts.d", paths)

    def test_artifact_commit_paths_exclude_existing_unignored_history_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            registry = repo / ".devtool" / "artifacts.json"
            registry.parent.mkdir(parents=True)
            registry.write_text('{"schema_version": 2, "artifacts": []}\n', encoding="utf-8")
            args = SimpleNamespace(_devtool_artifact_preview={"changes": []}, _devtool_artifact_manifest={})

            paths = _artifact_commit_paths(args, repo)

            self.assertEqual(paths, [])

    def test_artifact_commit_paths_exclude_deleted_tracked_history_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)
            registry = repo / ".devtool" / "artifacts.json"
            registry.parent.mkdir(parents=True)
            registry.write_text('{"schema_version": 2, "artifacts": []}\n', encoding="utf-8")
            subprocess.run(["git", "add", ".devtool/artifacts.json"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "Track history"], cwd=repo, check=True)
            registry.unlink()
            args = SimpleNamespace(_devtool_artifact_preview={"changes": []}, _devtool_artifact_manifest={})

            paths = _artifact_commit_paths(args, repo)

            self.assertEqual(paths, [])

    def test_auto_commit_preserves_preexisting_artifact_history_registry_changes(self) -> None:
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
            self.assertIn(".devtool/artifacts.json", status)
            self.assertIn(".devtool/artifacts.d/", status)
            committed = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertNotIn(".devtool/artifacts.json", committed)
            self.assertNotIn(".devtool/artifacts.d/old-failure.json", committed)
            self.assertIn("new.txt", committed)

    def test_auto_commit_force_adds_declared_ignored_payload_without_runtime_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "devtool@example.invalid"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.name", "Devtool Tests"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(
                ".devtool/\nroot-module/devtool-bridge/bin/\n",
                encoding="utf-8",
            )
            (repo / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "Base"], cwd=repo, check=True)

            payload_path = "root-module/devtool-bridge/bin/devtool-root-bridge"
            payload = repo / payload_path
            payload.parent.mkdir(parents=True)
            payload.write_text("#!/system/bin/sh\n", encoding="utf-8")

            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr(payload_path, payload.read_bytes())

            args = SimpleNamespace(
                command="apply-overlay",
                commit="Apply ignored artifact payload",
                artifact=str(artifact),
                verbose=False,
                dry_run=False,
                validate=False,
                validate_only=False,
                preview=False,
                _devtool_history_recorded=False,
                _devtool_artifact_type="overlay",
                _devtool_artifact_preview={"changes": [{"kind": "add", "path": payload_path}]},
                _devtool_artifact_manifest={},
                _devtool_artifact_sha="",
            )
            results = [OperationResult("apply-overlay", ["apply-overlay"], 0, 0.0, "ok", [])]
            config = ProjectConfig(repo_root=repo, kind="unknown")

            updated, _raw = _maybe_commit_after_apply(config, args, results, "apply ok")

            add_result = next(result for result in updated if result.name == "git-add")
            self.assertEqual(add_result.returncode, 0)
            self.assertIn("-f", add_result.argv)
            self.assertTrue(any(result.name == "git-commit" and result.returncode == 0 for result in updated))
            committed = subprocess.run(
                ["git", "show", "--pretty=", "--name-only", "HEAD"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertIn(payload_path, committed)
            self.assertFalse(any(path.startswith(".devtool/") for path in committed))
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout
            self.assertEqual(status, "")

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

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].argv, ["dotnet", "test", "App.sln"])
        self.assertTrue(specs[0].metadata["coalesced_dotnet_validation"])

    def test_dotnet_validate_skip_restore_disables_implicit_restore(self) -> None:
        from devtool.config import ProjectConfig
        from devtool.runners import build_validation_specs
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = ProjectConfig(repo_root=root, kind="dotnet", build_target="App.sln")
            specs = build_validation_specs(config, skip_restore=True)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].argv, ["dotnet", "test", "--no-restore", "App.sln"])
        self.assertFalse(specs[0].metadata["implicit_restore"])


def test_hud_overlay_can_be_requested_by_environment(monkeypatch, tmp_path) -> None:
    from devtool.cli import _normalize_live_progress_args
    import argparse

    monkeypatch.setenv("DEVTOOL_HUD_OVERLAY", "1")
    monkeypatch.delenv("DEVTOOL_NO_HUD", raising=False)
    args = argparse.Namespace(command="validate", auto_hud=False, hud_overlay=False, hud_no_overlay=False)
    _normalize_live_progress_args(args)
    assert args.hud_overlay is True


class OverlayClipboardSummaryTests(unittest.TestCase):
    def test_single_overlay_copy_names_applied_artifact(self) -> None:
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
                    "--repo", str(root), "--yes", "--copy",
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

def test_runner_passthrough_args_are_split_after_build_command() -> None:
    parse_argv, passthrough = split_runner_passthrough_args([
        "--repo",
        "/repo",
        "--target",
        "xdm_android",
        "build",
        "--",
        "-Pxdm.requireAria2Runtime=true",
    ])

    assert parse_argv == ["--repo", "/repo", "--target", "xdm_android", "build"]
    assert passthrough == ["-Pxdm.requireAria2Runtime=true"]


def test_runner_passthrough_split_ignores_option_values_named_like_commands() -> None:
    parse_argv, passthrough = split_runner_passthrough_args([
        "--repo",
        "/repo",
        "--target",
        "build",
        "apply-overlay",
        "--",
        "artifact.zip",
    ])

    assert parse_argv == ["--repo", "/repo", "--target", "build", "apply-overlay", "--", "artifact.zip"]
    assert passthrough == []


class ApplyOverlayValidationSelectionTests(unittest.TestCase):
    def test_apply_overlay_validate_accepts_test_selection_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--repo", "/repo",
            "apply-overlay", "--validate",
            "--task", ":media:test",
            "--task", ":app:testDebugUnitTest",
            "--tests", "BrowserHandoffParserTest",
            "--tests", "DownloadActionPlannerTest",
            "artifact.zip",
        ])
        self.assertEqual(args.test_task, [":media:test", ":app:testDebugUnitTest"])
        self.assertEqual(args.test_classes, ["BrowserHandoffParserTest", "DownloadActionPlannerTest"])

    def test_apply_overlay_validate_forwards_dotnet_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            artifact = root / "artifact.zip"
            with ZipFile(artifact, "w") as zf:
                zf.writestr("README.txt", "ok")
            captured: list[list[str]] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend(spec.argv for spec in specs)
                return ([OperationResult("test", ["dotnet", "test"], 0, 0.0, "ok", [])], "validate output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.apply_overlay", return_value=OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])),
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main([
                    "--repo", str(root), "--yes", "apply-overlay",
                    "--validate", "--skip-restore", "--skip-build",
                    "--class", "ParserTests", str(artifact),
                ])

            self.assertEqual(rc, 0)
            self.assertEqual(len(captured), 1)
            self.assertIn("--filter", captured[0])
            self.assertIn("FullyQualifiedName~ParserTests", captured[0][captured[0].index("--filter") + 1])


class ExclusiveValidationTaskTests(unittest.TestCase):
    def _android_config(self, root: Path) -> ProjectConfig:
        (root / "settings.gradle.kts").write_text('rootProject.name = "Demo"\ninclude(":app")\n', encoding="utf-8")
        (root / "build.gradle.kts").write_text("", encoding="utf-8")
        (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "gradlew").chmod(0o755)
        return ProjectConfig(
            repo_root=root,
            project_root=str(root),
            kind="android",
            runner_options={"validation": {"resumable": True}},
        )

    def test_validate_task_replaces_entire_builtin_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--sequential",
                "--task", ":app:testDebugUnitTest",
                "--task", ":app:lintDebug",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].metadata["gradle_tasks"], [":app:testDebugUnitTest", ":app:lintDebug"])
        self.assertEqual(specs[0].metadata["validation_scope"], "explicit-tasks")
        self.assertEqual(specs[0].metadata["validation_phase_total"], 1)
        self.assertNotIn("help", specs[0].metadata["gradle_tasks"])
        self.assertNotIn("dependencies", specs[0].metadata["gradle_tasks"])
        self.assertIn("gradle-junit", specs[0].metadata["report_parsers"])
        self.assertIn("android-lint", specs[0].metadata["report_parsers"])

    def test_apply_overlay_task_uses_same_exclusive_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "apply-overlay", "--validate",
                "--task", ":app:assembleDebug",
                "artifact.zip",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].metadata["gradle_tasks"], [":app:assembleDebug"])
        self.assertEqual(specs[0].metadata["validation_components"], ["explicit-tasks"])

    def test_apply_task_disables_embedded_script_phase(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--repo", "/repo",
            "apply-overlay", "--defer-validate",
            "--task", ":app:testDebugUnitTest",
            "artifact.zip",
        ])
        context = _validation_apply_context(
            args,
            transaction_path="/repo/.devtool/transactions/1.json",
            manifest={"validation": {"script": "validate.py"}},
        )
        self.assertEqual(context["exclusive_validation_tasks"], [":app:testDebugUnitTest"])
        self.assertEqual(context["minimum_phase"], "finalize")
        self.assertFalse(context["artifact_script_in_plan"])


    def test_immediate_apply_task_does_not_run_embedded_validator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._android_config(root)
            artifact = root / "artifact.zip"
            manifest = {
                "schema_version": 2,
                "type": "overlay",
                "validation": {
                    "mode": "required",
                    "minimum_phase": "package",
                    "script": {"path": ".devtool-artifact/validate.sh", "runner": "bash"},
                },
            }
            with ZipFile(artifact, "w") as archive:
                archive.writestr(".devtool-artifact.json", json.dumps(manifest))
                archive.writestr(".devtool-artifact/validate.sh", "#!/bin/sh\nexit 99\n")
                archive.writestr("README.txt", "patched\n")

            validation_result = OperationResult(
                "android-validation-explicit-tasks",
                ["gradle", ":app:testDebugUnitTest"],
                0,
                0.0,
                "ok",
                [],
            )
            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli._run_apply", return_value=([OperationResult("apply-overlay", ["unzip"], 0, 0.0, "ok", [])], "ok")),
                patch("devtool.cli.read_artifact_manifest", return_value=manifest),
                patch("devtool.cli._run_specs_for_args", return_value=([validation_result], "ok")),
                patch("devtool.cli._artifact_script_result") as validator,
                patch("devtool.cli.render_summary"),
            ):
                rc = main([
                    "--repo", str(root), "--yes", "--no-clipboard",
                    "apply-overlay", "--validate", "--no-apply-notification",
                    "--task", ":app:testDebugUnitTest", str(artifact),
                ])

        self.assertEqual(rc, 0)
        validator.assert_not_called()

    def test_apply_task_requires_a_validation_mode(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--repo", "/repo",
            "apply-overlay", "--task", ":app:testDebugUnitTest",
            "artifact.zip",
        ])
        with self.assertRaises(SystemExit):
            _validate_apply_args(args, parser)

    def test_task_rejects_skip_flags_instead_of_silently_ignoring_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", ":app:testDebugUnitTest", "--skip-build",
            ])
            with self.assertRaises(SystemExit):
                _validation_specs_for_args(config, args, parser)

    def _python_config(self, root: Path) -> ProjectConfig:
        (root / "pyproject.toml").write_text(
            "[project]\nname = \"demo\"\nversion = \"0\"\ndependencies = [\"pytest\"]\n\n[tool.ruff]\nline-length = 100\n",
            encoding="utf-8",
        )
        tests = root / "tests"
        tests.mkdir()
        (tests / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
        return ProjectConfig(
            repo_root=root,
            project_root=str(root),
            kind="python",
            runner_options={
                "python": {
                    "manager": "venv",
                    "test_framework": "pytest",
                    "environment": str(root / ".venv"),
                }
            },
        )

    def test_task_python_path_target_replaces_full_validation_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._python_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "tests",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "test")
        self.assertIn("tests", specs[0].argv)
        self.assertEqual(specs[0].metadata["explicit_validation_tasks"], ["tests"])
        self.assertEqual(specs[0].metadata["validation_scope"], "explicit-tasks")
        self.assertEqual(specs[0].metadata["validation_phase_total"], 1)
        self.assertNotIn("python-source-audit", [spec.name for spec in specs])
        self.assertNotIn("build", [spec.name for spec in specs])

    def test_task_python_named_actions_are_exclusive_validation_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._python_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "lint", "--task", "package-check",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["lint:ruff", "package-check"])
        for spec in specs:
            self.assertEqual(spec.metadata["explicit_validation_tasks"], ["lint", "package-check"])
            self.assertEqual(spec.metadata["validation_scope"], "explicit-tasks")
        self.assertNotIn("test", [spec.name for spec in specs])
        self.assertNotIn("python-source-audit", [spec.name for spec in specs])

    def test_task_rejects_tool_options_for_every_project_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._python_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task=-Pandroid.injected.version.code=1",
            ])
            with self.assertRaises(SystemExit):
                _validation_specs_for_args(config, args, parser)

    def test_task_node_script_selector_replaces_full_validation_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"unit": "node unit.js", "lint": "eslint .", "build": "node build.js"}}),
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="node")
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "unit", "--task", "lint",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["test", "lint"])
        self.assertEqual(specs[0].argv, ["npm", "run", "unit"])
        self.assertEqual(specs[1].argv, ["npm", "run", "lint"])
        for spec in specs:
            self.assertEqual(spec.metadata["explicit_validation_tasks"], ["unit", "lint"])
            self.assertEqual(spec.metadata["validation_scope"], "explicit-tasks")
        self.assertNotIn("restore", [spec.name for spec in specs])
        self.assertNotIn("build", [spec.name for spec in specs])


    def test_task_node_actions_do_not_leak_into_test_script_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                json.dumps({"scripts": {"test": "node test.js", "lint": "eslint ."}}),
                encoding="utf-8",
            )
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="node")
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "test", "--task", "lint",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["test", "lint"])
        self.assertEqual(specs[0].argv, ["npm", "run", "test"])
        self.assertEqual(specs[1].argv, ["npm", "run", "lint"])

    def test_task_python_test_and_lint_do_not_leak_action_names_into_pytest_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._python_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "test", "--task", "lint",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["test", "lint:ruff"])
        self.assertNotIn("lint", specs[0].argv)
        self.assertEqual(specs[0].metadata["explicit_validation_tasks"], ["test", "lint"])
        self.assertEqual(specs[1].metadata["explicit_validation_tasks"], ["test", "lint"])

    def test_task_python_build_action_is_supported_exclusively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._python_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "build",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["prepare-dist", "build"])
        for spec in specs:
            self.assertEqual(spec.metadata["explicit_validation_tasks"], ["build"])
            self.assertEqual(spec.metadata["validation_scope"], "explicit-tasks")
        self.assertNotIn("test", [spec.name for spec in specs])
        self.assertNotIn("lint:ruff", [spec.name for spec in specs])

    def test_task_rust_native_action_is_supported_exclusively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Cargo.toml").write_text(
                "[package]\nname = \"demo\"\nversion = \"0.1.0\"\nedition = \"2021\"\n",
                encoding="utf-8",
            )
            (root / "src").mkdir()
            (root / "src" / "lib.rs").write_text("pub fn demo() -> bool { true }\n", encoding="utf-8")
            config = ProjectConfig(repo_root=root, project_root=str(root), kind="rust")
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task", "test",
            ])

            specs = _validation_specs_for_args(config, args, parser)

        self.assertEqual([spec.name for spec in specs], ["test"])
        self.assertEqual(specs[0].argv[:3], ["cargo", "test", "--all-targets"])
        self.assertEqual(specs[0].metadata["explicit_validation_tasks"], ["test"])
        self.assertEqual(specs[0].metadata["validation_scope"], "explicit-tasks")
        self.assertNotIn("cargo-validation", [spec.name for spec in specs])

    def test_task_rejects_tool_options_before_android_gradle_planning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            args = parser.parse_args([
                "--repo", str(root),
                "validate", "--task=-Pandroid.injected.version.code=1",
            ])
            with self.assertRaises(SystemExit):
                _validation_specs_for_args(config, args, parser)

    def test_sequential_resume_restores_exclusive_task_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            first_args = parser.parse_args([
                "--repo", str(root),
                "validate", "--sequential",
                "--task", ":app:testDebugUnitTest",
                "--task", ":core:test",
            ])
            first, first_specs = _load_or_create_validation_session(config, first_args, parser)

            resume_args = parser.parse_args([
                "--repo", str(root),
                "validate", "--sequential",
            ])
            resumed, resumed_specs = _load_or_create_validation_session(config, resume_args, parser)

        self.assertEqual(resumed.session_id, first.session_id)
        self.assertEqual(first.options["test_task"], [":app:testDebugUnitTest", ":core:test"])
        self.assertEqual(resume_args.test_task, [":app:testDebugUnitTest", ":core:test"])
        self.assertEqual(len(first_specs), 1)
        self.assertEqual(len(resumed_specs), 1)
        self.assertEqual(resumed_specs[0].metadata["gradle_tasks"], [":app:testDebugUnitTest", ":core:test"])

    def test_sequential_resume_rejects_a_different_task_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self._android_config(root)
            parser = build_parser()
            first_args = parser.parse_args([
                "--repo", str(root),
                "validate", "--sequential",
                "--task", ":app:testDebugUnitTest",
            ])
            _load_or_create_validation_session(config, first_args, parser)
            resume_args = parser.parse_args([
                "--repo", str(root),
                "validate", "--sequential",
                "--task", ":app:lintDebug",
            ])
            with self.assertRaisesRegex(Exception, "task list differs"):
                _load_or_create_validation_session(config, resume_args, parser)


class BundleEntryValidationTaskApplyTests(unittest.TestCase):
    def _make_android_repo(self, root: Path) -> None:
        (root / ".git").mkdir()
        android = root / "android"
        (android / "app" / "src" / "main").mkdir(parents=True)
        (android / "app" / "src" / "main" / "AndroidManifest.xml").write_text("<manifest package='demo'/>", encoding="utf-8")
        (android / "settings.gradle.kts").write_text('rootProject.name = "Demo"\ninclude(":app")\n', encoding="utf-8")
        (android / "build.gradle.kts").write_text('plugins { id("com.android.application") version "8.8.0" apply false }\n', encoding="utf-8")
        (android / "app" / "build.gradle.kts").write_text('plugins { id("com.android.application") }\nandroid { compileSdk = 35 }\n', encoding="utf-8")
        (android / "gradle" / "wrapper").mkdir(parents=True)
        (android / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
            "distributionUrl=https\\://services.gradle.org/distributions/gradle-8.10.2-bin.zip\n",
            encoding="utf-8",
        )
        (android / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        (android / "gradlew").chmod(0o755)
        (root / ".devtool.toml").write_text(
            '[project]\nkind = "auto"\n\n[targets.chrootctl_android]\ntype = "android"\nroot = "android"\n',
            encoding="utf-8",
        )

    def _make_bundle(self, root: Path) -> Path:
        overlay = root / "android_overlay.zip"
        nested_manifest = {
            "schema_version": 2,
            "type": "overlay",
            "name": "android-overlay",
            "target": "chrootctl_android",
            "validation": {"required": True, "build": True, "test": True},
        }
        with ZipFile(overlay, "w") as archive:
            archive.writestr(".devtool-artifact.json", json.dumps(nested_manifest))
            archive.writestr("android/app/lint.xml", "<lint/>\n")

        bundle = root / "bundle.zip"
        overlay_bytes = overlay.read_bytes()
        bundle_manifest = {
            "schema_version": 2,
            "type": "overlay_bundle",
            "kind": "devtool.overlay_bundle",
            "validation": {"required": True, "strategy": "per-overlay"},
            "apply_order": [
                {
                    "id": "android",
                    "path": "overlays/android.zip",
                    "target": "chrootctl_android",
                    "sha256": hashlib.sha256(overlay_bytes).hexdigest(),
                    "size": len(overlay_bytes),
                    "validation": {
                        "tasks": [":app:compileDebugKotlin", ":app:testDebugUnitTest", ":app:lintDebug"],
                    },
                }
            ],
        }
        with ZipFile(bundle, "w") as archive:
            archive.writestr("devtool-bundle.json", json.dumps(bundle_manifest))
            archive.writestr("overlays/android.zip", overlay_bytes)
        return bundle

    def test_bundle_entry_validation_tasks_drive_apply_overlays_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_android_repo(root)
            bundle = self._make_bundle(root)
            captured: list[list[str]] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend(list(spec.metadata.get("gradle_tasks", [])) for spec in specs)
                return ([OperationResult("validate", ["gradle"], 0, 0.0, "ok", [])], "validate output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main(["-r", str(root), "--yes", "apply-overlays", str(bundle), "--validate"])

            self.assertEqual(rc, 0)
            self.assertIn([":app:compileDebugKotlin", ":app:testDebugUnitTest", ":app:lintDebug"], captured)
            self.assertNotIn(["assembleDebug"], captured)

    def test_cli_task_overrides_bundle_entry_validation_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_android_repo(root)
            bundle = self._make_bundle(root)
            captured: list[list[str]] = []

            def fake_run_specs(config, specs, **kwargs):
                captured.extend(list(spec.metadata.get("gradle_tasks", [])) for spec in specs)
                return ([OperationResult("validate", ["gradle"], 0, 0.0, "ok", [])], "validate output")

            with (
                patch("devtool.cli._git_dirty", return_value=False),
                patch("devtool.cli.run_specs", side_effect=fake_run_specs),
                patch("devtool.cli.render_summary"),
            ):
                rc = main([
                    "-r", str(root), "--yes", "apply-overlays", str(bundle), "--validate",
                    "--task", ":app:assembleDebug",
                ])

            self.assertEqual(rc, 0)
            self.assertIn([":app:assembleDebug"], captured)
            self.assertNotIn([":app:compileDebugKotlin", ":app:testDebugUnitTest", ":app:lintDebug"], captured)
