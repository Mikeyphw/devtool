from __future__ import annotations

import argparse
from pathlib import Path

from devtool.cli import build_parser
from tools.generate_complete_feature_guides import AI_PATH, HUMAN_PATH, command_specs, generate

ROOT = Path(__file__).resolve().parents[1]


def test_complete_feature_guides_are_generated_from_current_parser() -> None:
    human, ai = generate()
    assert HUMAN_PATH.read_text(encoding="utf-8") == human
    assert AI_PATH.read_text(encoding="utf-8") == ai


def test_complete_feature_guides_cover_every_public_command_parser() -> None:
    parser = build_parser()
    specs = command_specs(parser)
    assert len(specs) >= 100
    human = HUMAN_PATH.read_text(encoding="utf-8")
    ai = AI_PATH.read_text(encoding="utf-8")
    for spec in specs:
        marker = f"### `devtool {' '.join(spec.path)}`"
        assert marker in human, marker
        assert marker in ai, marker


def test_complete_feature_guides_cover_root_public_options() -> None:
    parser = build_parser()
    human = HUMAN_PATH.read_text(encoding="utf-8")
    ai = AI_PATH.read_text(encoding="utf-8")
    expected = set()
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
            continue
        if action.help is argparse.SUPPRESS:
            continue
        expected.update(action.option_strings)
    assert "--target" in expected
    assert "--repo" in expected or "-r" in expected
    assert "--resource-profile" in human
    assert "--resource-profile" in ai
    for option in expected:
        assert option in human, option
        assert option in ai, option


def test_guides_explain_warning_scope_and_major_feature_families() -> None:
    human = HUMAN_PATH.read_text(encoding="utf-8")
    ai = AI_PATH.read_text(encoding="utf-8")
    for text in (human, ai):
        assert "target warning" in text.lower()
        assert "infrastructure warning" in text.lower()
        assert "overlay" in text.lower()
        assert "android signing" in text.lower()
        assert "durable" in text.lower()
        assert "workflow" in text.lower()
        assert "machine" in text.lower()
