from __future__ import annotations

import pytest

from devtool.cli import build_parser, split_runner_passthrough_args
from devtool.core_parser import canonical_cli_schema, parser_identity_projection
from devtool.core_registry import build_adopted_core_registry


@pytest.mark.parametrize("flag", ["--yes", "-y"])
@pytest.mark.parametrize(
    ("argv", "positions"),
    [
        (["status"], (0, 1)),
        (["st"], (0, 1)),
        (["apply-overlay", "patch.zip"], (0, 1, 2)),
        (["ao", "patch.zip"], (0, 1, 2)),
        (["apply", "overlay", "patch.zip"], (0, 1, 2, 3)),
        (["actions", "provider-export", "--devtool-sha", "a" * 40, "--workflow", "ci"], (0, 1, 2, 6)),
        (["actions", "android", "run", "--ref", "main"], (0, 1, 2, 3, 5)),
        (["transaction", "unretire", "transaction-id"], (0, 1, 2, 3)),
        (["workflow", "retry", "--run-id", "run-id"], (0, 1, 2, 4)),
    ],
)
def test_yes_at_every_command_depth(flag: str, argv: list[str], positions: tuple[int, ...]) -> None:
    parser = build_parser()
    assert parser.parse_args(argv).yes is False
    for position in positions:
        forwarded = [*argv[:position], flag, *argv[position:]]
        result = parser.parse_args(forwarded)
        assert result.yes is True, forwarded
        assert result.force is False, forwarded


@pytest.mark.parametrize(
    "argv",
    [
        ["--yes", "actions", "android", "run", "--ref", "main"],
        ["-y", "actions", "provider-export", "--devtool-sha", "a" * 40, "--workflow", "ci"],
        ["-y", "actions", "-y", "android", "--yes", "run", "--ref", "main", "-y"],
        ["--yes", "workflow", "retry", "--run-id", "run-id"],
        ["actions", "provider-export", "--devtool-sha", "a" * 40, "--workflow", "ci", "--yes"],
    ],
)
def test_explicit_confirmation_survives_child_default_false(argv: list[str]) -> None:
    assert build_parser().parse_args(argv).yes is True


def test_literal_double_dash_preserves_forwarded_yes() -> None:
    parser = build_parser()
    args = parser.parse_args(["core", "runtime", "--", "validate", "--yes", "-y"])
    assert args.yes is False
    assert args.argv[-3:] == ["validate", "--yes", "-y"]
    args = parser.parse_args(["--yes", "core", "runtime", "--", "validate", "--yes"])
    assert args.yes is True
    assert args.argv[-2:] == ["validate", "--yes"]
    args, remaining = parser.parse_known_args(["status", "--", "--yes"])
    assert args.yes is False
    assert "--yes" in remaining
    parsed, passthrough = split_runner_passthrough_args(["build", "--", "--yes", "-y"])
    assert parsed == ["build"]
    assert passthrough == ["--yes", "-y"]


def test_parse_known_args_preserves_unknowns_and_confirmation() -> None:
    args, unknown = build_parser().parse_known_args(["status", "--yes", "--unknown-option"])
    assert args.yes is True
    assert unknown == ["--unknown-option"]


def test_confirmation_does_not_remove_other_safety_flags() -> None:
    argv = ["--yes", "actions", "provider-export", "--devtool-sha", "a" * 40, "--workflow", "ci", "--plan"]
    args = build_parser().parse_args(argv)
    assert args.yes is True
    assert args.provider_review_plan is True  # Existing handler must reject --plan plus --yes.
    assert args.force is False
    assert args.dry_run is False


def test_unknown_commands_still_fail_closed() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["unknown-command", "--yes"])


def test_canonical_option_registry_and_generated_surfaces_are_unchanged() -> None:
    parser = build_parser()
    schema = canonical_cli_schema()
    root_yes = [a for a in schema["parser"]["actions"] if set(a.get("option_strings", [])) >= {"--yes", "-y"}]
    assert len(root_yes) == 1
    assert root_yes[0]["dest"] == "yes"
    assert ((), "--yes") in parser_identity_projection()["options"]
    assert ((), "-y") in parser_identity_projection()["options"]
    parity = build_adopted_core_registry(parser).parser_parity(parser)
    assert parity["missing_count"] == 0
    assert parity["option_missing_count"] == 0
    assert parity["option_extra_count"] == 0


def test_root_help_and_legacy_zsh_completion_keep_global_aliases() -> None:
    from devtool.completion import ZSH_COMPLETION
    help_text = build_parser().format_help()
    assert "--yes" in help_text
    assert "-y" in help_text
    assert "'(-y --yes)'{-y,--yes}'" in ZSH_COMPLETION
