from __future__ import annotations

import json
from pathlib import Path

from devtool.cli import build_parser, main
from devtool.core_registry import build_core_registry
from devtool.core_runtime import (
    InteractionPolicy,
    OutputRequest,
    ResolutionContext,
    SecretRef,
    build_run_envelope,
    private_run_root,
    resolve_effective_config,
)


def test_effective_config_uses_deterministic_precedence_and_provenance(tmp_path: Path) -> None:
    context = ResolutionContext.from_argv(
        ["validate"],
        repo_root=tmp_path,
        cli_values={"cli.option.copy": True},
        repo_config={"cli": {"output": {"copy": False, "stdout_payload": "summary"}}},
        global_config={"cli": {"output": {"copy": False, "stdout_payload": "raw"}}},
        env={"DEVTOOL_STDOUT_PAYLOAD": "auto"},
    )

    resolved = resolve_effective_config(context)
    provenance = {item.field_id: item for item in resolved.provenance}

    assert resolved.value("cfg.cli.output.copy") is True
    assert provenance["cfg.cli.output.copy"].source == "cli"
    assert resolved.value("cfg.cli.output.stdout_payload") == "auto"
    assert provenance["cfg.cli.output.stdout_payload"].source == "env:DEVTOOL_STDOUT_PAYLOAD"
    assert len(resolved.semantic_digest) == 64
    assert resolved.semantic_digest == resolve_effective_config(context).semantic_digest


def test_secret_values_are_never_serialized_in_safe_config(tmp_path: Path) -> None:
    context = ResolutionContext.from_argv(
        ["validate"],
        repo_root=tmp_path,
        global_config={"cli": {"secrets": {"token": "super-secret-token"}}},
    )

    resolved = resolve_effective_config(context)
    payload = json.dumps(resolved.to_dict(), sort_keys=True)

    assert "super-secret-token" not in payload
    assert resolved.to_dict()["values"]["cfg.cli.secrets.token"] == {"redacted": True}
    token = resolved.value("cfg.cli.secrets.token")
    assert isinstance(token, SecretRef)
    assert token.to_dict()["kind"] == "secret-ref"


def test_output_mode_does_not_change_command_or_config_semantics(tmp_path: Path) -> None:
    base = ResolutionContext.from_argv(["apply-overlay", "patch.zip"], repo_root=tmp_path, output=OutputRequest(format="human"))
    machine = ResolutionContext.from_argv(["apply-overlay", "patch.zip"], repo_root=tmp_path, output=OutputRequest(format="json", copy=True))

    human_envelope = build_run_envelope(base)
    json_envelope = build_run_envelope(machine)

    assert human_envelope.invocation.command_id == json_envelope.invocation.command_id
    assert human_envelope.invocation.digest == json_envelope.invocation.digest
    assert human_envelope.config.semantic_digest == json_envelope.config.semantic_digest
    assert human_envelope.output.to_dict()["digest"] != json_envelope.output.to_dict()["digest"]


def test_run_descriptor_is_private_repo_scoped_and_stable(tmp_path: Path) -> None:
    context = ResolutionContext.from_argv(["validate"], repo_root=tmp_path)
    first = build_run_envelope(context)
    second = build_run_envelope(context)

    assert first.descriptor.schema_version == 3
    assert first.descriptor.private is True
    assert first.descriptor.run_id == second.descriptor.run_id
    assert first.descriptor.run_root.startswith(str(private_run_root(tmp_path)))
    assert "/.devtool/private/run-v3/" in first.descriptor.run_root
    assert first.scope.repository_id == second.scope.repository_id


def test_interaction_policy_and_project_scope_are_exported(tmp_path: Path) -> None:
    context = ResolutionContext.from_argv(
        ["validate"],
        repo_root=tmp_path,
        interaction=InteractionPolicy.from_flags(yes=True, interactive=False),
    )
    envelope = build_run_envelope(context).to_dict()

    assert envelope["interaction"] == {"assume_yes": True, "interactive": False, "allow_prompts": False}
    assert envelope["scope"]["canonical_repo_root"] == str(tmp_path.resolve(strict=False))
    assert len(envelope["scope"]["scope_digest"]) == 64


def test_wrapper_and_direct_alias_lower_to_same_runtime_identity(tmp_path: Path) -> None:
    direct = build_run_envelope(ResolutionContext.from_argv(["wrapper", "resolve", "test"], repo_root=tmp_path))
    alias = build_run_envelope(ResolutionContext.from_argv(["wrapper", "plan", "test"], repo_root=tmp_path))

    assert direct.invocation.command_id == "cli.command.wrapper.resolve"
    assert direct.invocation.command_id == alias.invocation.command_id
    assert direct.invocation.canonical_path == alias.invocation.canonical_path
    assert direct.invocation.invoked_path != alias.invocation.invoked_path


def test_core_runtime_registry_and_cli_surface(tmp_path: Path, capsys) -> None:
    registry = build_core_registry()
    parity = registry.parser_parity(build_parser())

    assert "core runtime" in parity["covered"]
    assert main(["--repo", str(tmp_path), "core", "runtime", "--json", "--", "validate"]) == 0
    payload = json.loads(capsys.readouterr().out)
    envelope = payload.get("data", payload)

    assert envelope["descriptor"]["schema_version"] == 3
    assert envelope["invocation"]["command_id"] == "cli.command.validate"
    assert envelope["descriptor"]["private"] is True
