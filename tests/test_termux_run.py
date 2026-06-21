
from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from repo_devtool.cli import build_parser
from repo_devtool.termux_run import (
    ParsedBlock,
    RemoteCapabilities,
    block_contains_commit,
    build_ssh_options,
    parse_clipboard_block,
    rewrite_block_for_remote,
    scp_options_from_ssh,
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
            rewritten,
            '/home/mike/.local/bin/devtool -n -r "$REPO_CURRENT" --stdout-payload runner-json apply-overlay-validate "$ARTIFACT_CURRENT"\n',
        )

    def test_rewrite_block_for_remote_falls_back_to_json(self) -> None:
        block = 'devtool -r "$REPO_CURRENT" --copy-json validate\n'
        rewritten = rewrite_block_for_remote(
            block,
            remote_devtool="/home/mike/.local/bin/devtool",
            capabilities=RemoteCapabilities(supports_stdout_payload=False, supports_runner_json=False),
        )
        self.assertEqual(
            rewritten,
            '/home/mike/.local/bin/devtool -r "$REPO_CURRENT" --json validate\n',
        )

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

