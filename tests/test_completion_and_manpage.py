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

    def test_core_generated_machine_help_covers_live_cli_options(self) -> None:
        from devtool.core_surfaces import machine_help_payload
        from devtool.core_registry import build_core_registry
        parser = build_parser()
        registry = build_core_registry()
        payload = machine_help_payload(parser, registry)
        assert payload['parser_parity']['missing_count'] == 0
        assert payload['parser_parity']['option_missing_count'] == 0
        # Static legacy Zsh completion deliberately has no exhaustive option
        # inventory; canonical machine-help covers the *live* complete parser.
        option_spellings = {spelling for row in payload['commands']
                            for option in row.get('options', [])
                            for spelling in option['spellings']}
        assert '--rebase' in option_spellings
        assert '--keep-artifact' in option_spellings

    def test_zsh_legacy_completion_retains_supported_core_commands(self) -> None:
        for name in ('apply-overlay', 'apply-snapshot', 'actions', 'validate'):
            self.assertRegex(ZSH_COMPLETION, rf'(?<![A-Za-z0-9_-]){re.escape(name)}(?![A-Za-z0-9_-])')

    def test_completion_documents_exclusive_validation_task_semantics(self) -> None:
        wording = (
            "exclusive validation unit; Gradle task for Android/Gradle or native action/test target elsewhere"
        )
        self.assertGreaterEqual(ZSH_COMPLETION.count(wording), 3)
        self.assertIn(
            "(--task --test-task)'{--task,--test-task}'",
            ZSH_COMPLETION,
        )

    def test_completion_documents_bundle_validation_task_manifest_flags(self) -> None:
        self.assertIn(
            "--validation-task[Gradle task to store in bundle validation.tasks]:task:",
            ZSH_COMPLETION,
        )
        self.assertIn(
            "--validation-tasks[comma-separated Gradle tasks for bundle validation.tasks]:tasks:",
            ZSH_COMPLETION,
        )

    def test_manpage_documents_exclusive_validation_task_semantics(self) -> None:
        man = MAN_PATH.read_text(encoding="utf-8")
        required = (
            "only the named Gradle task or tasks are placed in the validation\n"
            "pipeline",
            "For Python/Node/Rust and other non-Gradle runners, --task selects native\n"
            "validation units",
            "replace the\n"
            "entire built-in validation pipeline with this task",
            "suppress",
            "embedded artifact validation scripts",
            "Artifact manifest,\n"
            "checksum, inventory, and transactional apply safety checks still run",
            "validation.tasks",
            "CLI flags take precedence",
            "--validation-task",
            "--validation-tasks",
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
