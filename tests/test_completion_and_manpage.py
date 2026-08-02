from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from devtool.cli import build_parser
from devtool.completion import ZSH_COMPLETION


ROOT = Path(__file__).resolve().parents[1]
COMPLETION_PATH = ROOT / "completions" / "_devtool"
MAN_PATH = ROOT / "share" / "man" / "man1" / "devtool.1"


def _walk_parsers(parser: argparse.ArgumentParser):
    yield parser
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        seen: set[int] = set()
        for child in action.choices.values():
            if id(child) in seen:
                continue
            seen.add(id(child))
            yield from _walk_parsers(child)


class CompletionAndManpageTests(unittest.TestCase):
    def test_packaged_and_runtime_zsh_completions_are_identical(self) -> None:
        self.assertEqual(ZSH_COMPLETION, COMPLETION_PATH.read_text(encoding="utf-8"))

    def test_completion_mentions_every_long_cli_option(self) -> None:
        missing: list[str] = []
        for parser in _walk_parsers(build_parser()):
            for action in parser._actions:
                for option in action.option_strings:
                    if option.startswith("--") and option != "--help" and option not in ZSH_COMPLETION:
                        missing.append(option)
        self.assertEqual(sorted(set(missing)), [])

    def test_completion_mentions_every_command_and_alias(self) -> None:
        parser = build_parser()
        subparsers = next(
            action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        )
        missing = [
            name
            for name in subparsers.choices
            if not re.search(rf"(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])", ZSH_COMPLETION)
        ]
        self.assertEqual(missing, [])

    def test_completion_documents_exclusive_validation_task_semantics(self) -> None:
        wording = (
            "exclusive Android/Gradle validation task; repeat to run only those tasks"
        )
        self.assertGreaterEqual(ZSH_COMPLETION.count(wording), 3)
        self.assertIn(
            "(--task --test-task)'{--task,--test-task}'",
            ZSH_COMPLETION,
        )

    def test_manpage_documents_exclusive_validation_task_semantics(self) -> None:
        man = MAN_PATH.read_text(encoding="utf-8")
        required = (
            "only the named Gradle task or tasks are placed in\n"
            "the validation pipeline",
            "replace the\n"
            "entire built-in Android/Gradle validation pipeline with this task",
            "suppresses all other\n"
            "validation phases, including embedded artifact validation scripts",
            "Artifact manifest,\n"
            "checksum, inventory, and transactional apply safety checks still run",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, man)

    def test_manpage_tracks_current_release_and_multi_runner_features(self) -> None:
        man = MAN_PATH.read_text(encoding="utf-8")
        required = (
            '"devtool 1.1.0"',
            ".SH PROJECT TYPES",
            ".SH TEST SELECTION",
            ".SH EXECUTION ENVIRONMENTS",
            ".SH ANDROID AND GRADLE",
            ".SH PYTHON",
            ".SH SHELL COMPLETION",
            "--project-type",
            "--variant",
            "--execution-environment",
            "--test-profile",
            "--instrumented",
            "--no-build",
            "--no-restore",
            "--delete-artifact-on-failure",
            "android-hud-bridge",
            ".SH RESUMABLE VALIDATION",
            "validation-reset",
            "validation-accept-plan-change",
            "--max-infrastructure-retries",
            "--max-resource-retries",
            "--no-auto-retry",
        )
        for token in required:
            with self.subTest(token=token):
                self.assertIn(token, man)


if __name__ == "__main__":
    unittest.main()
