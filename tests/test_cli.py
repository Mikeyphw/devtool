from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from zipfile import ZipFile
import tomllib
from unittest.mock import patch

from repo_devtool.ai_prompt import AiPromptOptions, render_ai_prompt
from repo_devtool import __version__
from repo_devtool.cli import build_parser, main
from repo_devtool.logstore import latest_log, write_log
from repo_devtool.summary import build_json_summary


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
    def test_parser_accepts_alias(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["v"])
        self.assertEqual(args.command, "v")

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
        write_log("hello")
        stream = io.StringIO()
        with redirect_stdout(stream):
            rc = main(["logs", "latest", "--print"])
        self.assertEqual(rc, 0)

    def test_batch_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "App.sln").write_text("", encoding="utf-8")
            rc = main(["--repo", str(root), "--dry-run", "batch", "validate"])
            self.assertEqual(rc, 0)

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


    def test_copy_json_uses_json_summary_even_without_json_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            copied: list[str] = []

            with (
                patch("repo_devtool.cli.copy_text", side_effect=lambda text: copied.append(text) or __import__("repo_devtool.clipboard").clipboard.ClipboardResult(True, backend="xclip")),
                patch("repo_devtool.cli.console.print"),
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
                patch("repo_devtool.cli.copy_text", return_value=__import__("repo_devtool.clipboard").clipboard.ClipboardResult(False, backend="xclip", reason="nope")),
                patch("repo_devtool.cli.console.print") as mock_print,
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
                patch("repo_devtool.cli.Confirm.ask") as mock_confirm,
                patch("repo_devtool.cli.console.print"),
            ):
                rc = main(["--repo", str(root), "--dry-run", "commit", "example commit"])

            self.assertEqual(rc, 0)
            mock_confirm.assert_not_called()

    def test_parser_accepts_stdout_payload_runner_json(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--stdout-payload", "runner-json", "validate"])
        self.assertEqual(args.stdout_payload, "runner-json")

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

    def test_stdout_payload_runner_json_prints_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "App.sln").write_text("", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = main(["--repo", str(root), "--stdout-payload", "runner-json", "--dry-run", "validate"])
            self.assertEqual(rc, 0)
            payload = json.loads(stream.getvalue())
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("clipboard", payload)
            self.assertIn("notification", payload)

    def test_json_summary_uses_failed_not_failures(self) -> None:
        payload = build_json_summary([], repo_root="/tmp/repo")
        self.assertIn("failed", payload)
        self.assertNotIn("failures", payload)

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
