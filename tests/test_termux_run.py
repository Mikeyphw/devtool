
from __future__ import annotations

import io
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_devtool.cli import build_parser
from repo_devtool.termux_run import (
    ParsedBlock,
    RemoteCapabilities,
    RemoteDevtoolCapabilities,
    block_contains_commit,
    build_ssh_options,
    handle_termux_run,
    parse_clipboard_block,
    rewrite_block_for_remote,
    scp_options_from_ssh,
    show_remote_stdout,
)


class TermuxRunTests(unittest.TestCase):
    def test_parser_accepts_termux_run(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["termux-run", "--ssh-user", "mike", "--ssh-host", "box"])
        self.assertEqual(args.command, "termux-run")
        self.assertEqual(args.ssh_user, "mike")
        self.assertEqual(args.ssh_host, "box")

    def test_parse_clipboard_block_resolves_artifact_assignment(self) -> None:
        block = '\n'.join(
            [
                'REPO_CURRENT="/home/mike/Code/s3oc_modern"',
                'ARTIFACT_CURRENT="$HOME/Downloads/example_overlay.zip"',
                'devtool -r "$REPO_CURRENT" apply-overlay-validate "$ARTIFACT_CURRENT"',
            ]
        )
        parsed = parse_clipboard_block(block)
        self.assertEqual(parsed.archive_basename, "example_overlay.zip")
        self.assertEqual(parsed.remote_artifact_path, "/home/mike/Downloads/example_overlay.zip")

    def test_rewrite_block_for_remote_injects_runner_json_before_subcommand(self) -> None:
        block = 'devtool -n -r "$REPO_CURRENT" --copy-json apply-overlay-validate "$ARTIFACT_CURRENT"\n'
        rewritten = rewrite_block_for_remote(
            block,
            remote_devtool="/home/mike/.local/bin/devtool",
            capabilities=RemoteCapabilities(supports_stdout_payload=True, supports_runner_json=True),
        )
        self.assertEqual(
            shlex.split(rewritten),
            [
                "/home/mike/.local/bin/devtool",
                "-n",
                "-r",
                "$REPO_CURRENT",
                "--stdout-payload",
                "runner-json",
                "apply-overlay-validate",
                "$ARTIFACT_CURRENT",
            ],
        )

    def test_rewrite_block_for_remote_falls_back_to_json(self) -> None:
        block = 'devtool -r "$REPO_CURRENT" --copy-json validate\n'
        rewritten = rewrite_block_for_remote(
            block,
            remote_devtool="/home/mike/.local/bin/devtool",
            capabilities=RemoteCapabilities(supports_stdout_payload=False, supports_runner_json=False),
        )
        self.assertEqual(
            shlex.split(rewritten),
            [
                "/home/mike/.local/bin/devtool",
                "-r",
                "$REPO_CURRENT",
                "--json",
                "validate",
            ],
        )

    def test_remote_capabilities_alias_is_stable(self) -> None:
        self.assertIs(RemoteCapabilities, RemoteDevtoolCapabilities)

    def test_build_ssh_options_and_scp_translation(self) -> None:
        options = build_ssh_options(port="2222", key_path="~/.ssh/id_ed25519", connect_timeout=5)
        self.assertIn("-p", options)
        self.assertIn("2222", options)
        self.assertIn("-i", options)
        scp_options = scp_options_from_ssh(options)
        self.assertIn("-P", scp_options)
        self.assertIn("2222", scp_options)

    def test_block_contains_commit_detects_git_and_devtool_commit(self) -> None:
        self.assertTrue(block_contains_commit('git -C "$REPO_CURRENT" commit -m "msg"\n'))
        self.assertTrue(block_contains_commit('/home/mike/.local/bin/devtool -r "$REPO_CURRENT" commit "msg"\n'))
        self.assertFalse(block_contains_commit('devtool -r "$REPO_CURRENT" validate\n'))

    def test_termux_syntax_hides_line_numbers_on_narrow_terminal(self) -> None:
        block = 'devtool -r "$REPO_CURRENT" validate\n'
        out = io.StringIO()
        with patch.dict("os.environ", {"TERMUX_VERSION": "1", "COLUMNS": "72"}, clear=False), patch("sys.stdout", out):
            from repo_devtool.termux_run import confirm_clipboard_block
            confirm_clipboard_block(block, assume_yes=True)
        rendered = out.getvalue()
        self.assertNotIn("1 ", rendered)

    def test_show_remote_stdout_uses_compact_title_on_narrow_termux(self) -> None:
        out = io.StringIO()
        with patch.dict("os.environ", {"TERMUX_VERSION": "1", "COLUMNS": "72"}, clear=False), patch("sys.stdout", out):
            show_remote_stdout("hello\n")
        rendered = out.getvalue()
        self.assertIn("Remote output", rendered)

    def test_termux_run_does_not_overwrite_clipboard_with_runner_json(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["termux-run", "--ssh-user", "mike", "--ssh-host", "box", "--yes"])
        payload = {
            "schema_version": 1,
            "ok": True,
            "command": "validate",
            "repo": "/home/mike/Code/example",
            "totals": {"warnings": 0, "errors": 0, "failed": 0, "skipped": 0},
            "clipboard": {"preferred": "json", "json": {"answer": 42}},
            "notification": {"should_notify": False},
        }

        with (
            patch("repo_devtool.termux_run.shutil.which", return_value="/bin/true"),
            patch("repo_devtool.termux_run.build_target", return_value="mike@box"),
            patch("repo_devtool.termux_run.check_ssh_connection"),
            patch("repo_devtool.termux_run.get_termux_clipboard", return_value='devtool -r "$REPO_CURRENT" validate\n'),
            patch("repo_devtool.termux_run.parse_clipboard_block", return_value=ParsedBlock(
                original_text='devtool -r "$REPO_CURRENT" validate\n',
                remote_artifact_expr=None,
                remote_artifact_path=None,
                archive_basename=None,
                requires_archive_upload=False,
            )),
            patch("repo_devtool.termux_run.detect_remote_capabilities", return_value=RemoteCapabilities(supports_stdout_payload=True, supports_runner_json=True)),
            patch("repo_devtool.termux_run.rewrite_block_for_remote", return_value="rewritten\n"),
            patch("repo_devtool.termux_run.run_remote_block", return_value=(__import__("json").dumps(payload), 0)),
            patch("repo_devtool.termux_run.set_termux_clipboard") as mock_clipboard,
            patch("repo_devtool.termux_run.show_remote_stdout") as mock_stdout,
            patch("repo_devtool.termux_run.console.print"),
            patch("repo_devtool.termux_run.stderr_console.print"),
        ):
            rc = handle_termux_run(args)

        self.assertEqual(rc, 0)
        mock_clipboard.assert_not_called()
        mock_stdout.assert_not_called()


    def test_rewrite_block_for_remote_preserves_target_flag(self) -> None:
        block = 'devtool -n -r "$REPO_CURRENT" --target s3pe apply-overlay-validate "$ARTIFACT_CURRENT"\n'
        rewritten = rewrite_block_for_remote(
            block,
            remote_devtool="/home/mike/.local/bin/devtool",
            capabilities=RemoteCapabilities(supports_stdout_payload=True, supports_runner_json=True),
        )
        self.assertEqual(
            shlex.split(rewritten),
            [
                "/home/mike/.local/bin/devtool",
                "-n",
                "-r",
                "$REPO_CURRENT",
                "--target",
                "s3pe",
                "--stdout-payload",
                "runner-json",
                "apply-overlay-validate",
                "$ARTIFACT_CURRENT",
            ],
        )
