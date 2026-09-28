
from __future__ import annotations

import json

import pytest

from devtool.cli import build_parser, main
from devtool.core_registry import TrustedInvocationEnvelope, TrustedInvocationError, build_core_registry


def test_core_registry_canonicalizes_nested_commands_and_aliases() -> None:
    registry = build_core_registry()

    assert registry.canonicalize_argv(["apply-overlay", "patch.zip"]) == ("apply", "overlay", "patch.zip")
    assert registry.canonicalize_argv(["wrapper", "plan", "test"]) == ("wrapper", "resolve", "test")
    assert registry.resolve_command(["pytest", "select", "--lane", "fast"])["command"].stable_id == "cli.command.pytest.select"


def test_core_registry_links_cli_options_to_config_fields() -> None:
    registry = build_core_registry()
    links = {item["option_id"]: item["config_field_id"] for item in registry.option_config_links()}

    assert links["cli.option.copy"] == "cfg.cli.output.copy"
    assert links["cli.option.stdout_payload"] == "cfg.cli.output.stdout_payload"
    assert links["cli.option.execution_environment"] == "cfg.cli.environment.execution_environment"
    assert registry.options_by_id["cli.option.internal_invocation_source"].sensitive is True


def test_config_document_validation_rejects_unknown_keys_and_wrong_types() -> None:
    registry = build_core_registry()

    diagnostics = registry.validate_config_document(
        "global",
        {
            "cli": {"output": {"copy": "yes", "stdout_payload": "summary"}},
            "project": {"kind": "python"},
            "totally_unknown": True,
        },
    )
    by_path = {item.path: item.code for item in diagnostics}

    assert by_path[("cli", "output", "copy")] == "invalid-type"
    assert by_path[("project",)] == "unknown-key"
    assert by_path[("totally_unknown",)] == "unknown-key"


def test_trusted_invocation_envelope_cannot_be_spoofed_directly() -> None:
    registry = build_core_registry()
    envelope = registry.envelope_for(["apply-overlay", "patch.zip"], source="wrapper")

    assert envelope.command_id == "cli.command.apply.overlay"
    assert envelope.canonical_path == ("apply", "overlay")
    assert envelope.invoked_path == ("apply-overlay",)
    assert len(envelope.digest) == 64
    with pytest.raises(TrustedInvocationError):
        TrustedInvocationEnvelope(
            command_id="cli.command.apply.overlay",
            canonical_path=("apply", "overlay"),
            invoked_path=("apply-overlay",),
            source="env",
            digest="0" * 64,
        )


def test_registry_parser_parity_inventory_is_explicit_about_missing_commands() -> None:
    registry = build_core_registry()
    parity = registry.parser_parity(build_parser())

    assert "apply overlay" in parity["covered"]
    assert "pytest inventory" in parity["covered"]
    assert parity["missing_count"] > 0
    assert "core registry" in parity["covered"]


def test_core_registry_cli_emits_machine_payload(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["core", "registry", "--json", "--parity"]) == 0
    out = capsys.readouterr().out
    envelope = json.loads(out)
    payload = envelope.get("data", envelope)

    assert payload["schema_version"] == 1
    assert len(payload["digest"]) == 64
    assert any(command["stable_id"] == "cli.command.apply.overlay" for command in payload["commands"])
    assert payload["parser_parity"]["missing_count"] == 0


def test_compat_registry_alias_command_remains_available(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["registry", "--json"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    payload = envelope.get("data", envelope)
    assert payload["schema_version"] == 1
