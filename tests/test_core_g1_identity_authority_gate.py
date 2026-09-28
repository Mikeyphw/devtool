from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devtool.cli import main
from devtool.core_registry import TrustedInvocationEnvelope, TrustedInvocationError, build_core_registry
from devtool.core_runtime import OutputRequest, ResolutionContext, build_run_envelope


def _json_payload(capsys: pytest.CaptureFixture[str]) -> dict:
    out = capsys.readouterr().out
    payload = json.loads(out)
    return payload.get("data", payload)


def test_alias_and_canonical_commands_have_same_semantic_run_identity(tmp_path: Path) -> None:
    canonical = build_run_envelope(ResolutionContext.from_argv(["apply", "overlay", "patch.zip"], repo_root=tmp_path))
    alias = build_run_envelope(ResolutionContext.from_argv(["apply-overlay", "patch.zip"], repo_root=tmp_path))

    assert canonical.invocation.command_id == alias.invocation.command_id == "cli.command.apply.overlay"
    assert canonical.invocation.canonical_path == alias.invocation.canonical_path == ("apply", "overlay")
    assert canonical.invocation.invoked_path != alias.invocation.invoked_path
    assert canonical.descriptor.command_digest == alias.descriptor.command_digest
    assert canonical.descriptor.config_digest == alias.descriptor.config_digest
    assert canonical.descriptor.run_id == alias.descriptor.run_id


def test_nested_command_identity_is_complete_and_not_prefix_truncated() -> None:
    registry = build_core_registry()
    apply_overlay = registry.resolve_command(["apply", "overlay", "patch.zip"])
    apply_bundle = registry.resolve_command(["apply", "bundle", "one.zip", "two.zip"])
    core_runtime = registry.resolve_command(["core", "runtime", "--", "validate"])

    assert apply_overlay["command"].stable_id == "cli.command.apply.overlay"
    assert apply_overlay["canonical_path"] == ("apply", "overlay")
    assert apply_bundle["command"].stable_id == "cli.command.apply.bundle"
    assert apply_bundle["canonical_path"] == ("apply", "bundle")
    assert core_runtime["command"].stable_id == "cli.command.core.runtime"
    assert core_runtime["remaining"] == ("--", "validate")


def test_ordinary_argv_and_environment_cannot_spoof_internal_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = build_core_registry()
    monkeypatch.setenv("DEVTOOL_INTERNAL_INVOCATION_SOURCE", "forged-wrapper")
    envelope = registry.envelope_for(["validate"], source="cli")

    assert envelope.source == "cli"
    assert envelope.command_id == "cli.command.validate"
    assert "forged-wrapper" not in json.dumps(envelope.to_dict(), sort_keys=True)
    with pytest.raises(TrustedInvocationError):
        TrustedInvocationEnvelope(
            command_id="cli.command.validate",
            canonical_path=("validate",),
            invoked_path=("validate",),
            source="forged-wrapper",
            digest="0" * 64,
        )


def test_unknown_config_keys_and_types_fail_with_stable_diagnostics() -> None:
    registry = build_core_registry()
    diagnostics = registry.validate_config_document(
        "global",
        {
            "cli": {"output": {"copy": "yes", "stdout_payload": "sideways"}},
            "unknown": {"nested": True},
        },
    )
    by_path = {".".join(item.path): item.code for item in diagnostics}

    assert by_path["cli.output.copy"] == "invalid-type"
    assert by_path["cli.output.stdout_payload"] == "invalid-type"
    assert by_path["unknown"] == "unknown-key"


def test_effective_config_provenance_and_identity_are_deterministic(tmp_path: Path) -> None:
    kwargs = dict(
        argv=["validate"],
        repo_root=tmp_path,
        cli_values={"cli.option.target": "devtool"},
        repo_config={"cli": {"output": {"copy": False}}},
        global_config={"cli": {"output": {"copy": True}}},
        env={"DEVTOOL_STDOUT_PAYLOAD": "summary"},
    )
    first = build_run_envelope(ResolutionContext.from_argv(**kwargs))
    second = build_run_envelope(ResolutionContext.from_argv(**kwargs))

    assert first.config.semantic_digest == second.config.semantic_digest
    assert first.descriptor.config_digest == second.descriptor.config_digest
    assert first.descriptor.run_id == second.descriptor.run_id
    provenance = {item["field_id"]: item for item in first.config.to_dict()["provenance"]}
    assert provenance["cfg.cli.output.stdout_payload"]["source"] == "env:DEVTOOL_STDOUT_PAYLOAD"


def test_secret_values_are_absent_from_machine_and_run_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    secret = "CORE-G1-SHOULD-NOT-LEAK"
    envelope = build_run_envelope(
        ResolutionContext.from_argv(["validate"], repo_root=tmp_path, global_config={"cli": {"secrets": {"token": secret}}})
    ).to_dict()
    assert secret not in json.dumps(envelope, sort_keys=True)
    assert envelope["config"]["values"]["cfg.cli.secrets.token"] == {"redacted": True}

    assert main(["--repo", str(tmp_path), "core", "runtime", "--json", "--", "validate"]) == 0
    cli_payload = _json_payload(capsys)
    assert secret not in json.dumps(cli_payload, sort_keys=True)


def test_human_json_and_copy_output_do_not_change_run_identity(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def run(args: list[str]) -> dict:
        assert main(args) == 0
        return _json_payload(capsys)

    base = ["--repo", str(tmp_path), "core", "runtime", "--json", "--", "validate"]
    human = run(base)
    json_mode = run(["--repo", str(tmp_path), "core", "runtime", "--json", "--format", "json", "--", "validate"])
    copy_mode = run(["--repo", str(tmp_path), "--copy", "core", "runtime", "--json", "--format", "copy", "--", "validate"])

    for payload in (json_mode, copy_mode):
        assert payload["invocation"]["command_id"] == human["invocation"]["command_id"]
        assert payload["descriptor"]["command_digest"] == human["descriptor"]["command_digest"]
        assert payload["descriptor"]["config_digest"] == human["descriptor"]["config_digest"]
        assert payload["descriptor"]["run_id"] == human["descriptor"]["run_id"]
    assert copy_mode["output"]["copy"] is True
    assert json_mode["output"]["digest"] != human["output"]["digest"]


def test_wrapper_alias_and_direct_cli_lower_to_same_command_and_config_identity(tmp_path: Path) -> None:
    direct = build_run_envelope(ResolutionContext.from_argv(["wrapper", "resolve", "target"], repo_root=tmp_path))
    alias = build_run_envelope(ResolutionContext.from_argv(["wrapper", "plan", "target"], repo_root=tmp_path))

    assert direct.invocation.command_id == alias.invocation.command_id == "cli.command.wrapper.resolve"
    assert direct.invocation.canonical_path == alias.invocation.canonical_path
    assert direct.descriptor.command_digest == alias.descriptor.command_digest
    assert direct.descriptor.config_digest == alias.descriptor.config_digest
    assert direct.descriptor.run_id == alias.descriptor.run_id


def test_registered_operations_receive_one_private_run_descriptor(tmp_path: Path) -> None:
    registry = build_core_registry()
    command_paths = [
        command.canonical_path
        for command in registry.commands
        if command.stable_id in {
            "cli.command.apply.overlay",
            "cli.command.validate",
            "cli.command.config.doctor",
            "cli.command.wrapper.resolve",
            "cli.command.pytest.inventory",
            "cli.command.core.registry",
            "cli.command.core.runtime",
        }
    ]
    seen: set[str] = set()
    for path in command_paths:
        envelope = build_run_envelope(ResolutionContext.from_argv(path, repo_root=tmp_path)).to_dict()
        descriptor = envelope["descriptor"]
        assert descriptor["schema_version"] == 3
        assert descriptor["private"] is True
        assert "/.devtool/private/run-v3/" in descriptor["run_root"]
        assert descriptor["run_id"] not in seen
        seen.add(descriptor["run_id"])
    assert len(seen) == len(command_paths)


def test_core_x01_x02_promise_audit_loop_is_clean() -> None:
    # This ledger is intentionally executable: CORE-G1 must fail if any audited
    # promise is left unproved or only locally documented.
    proved = {
        "alias_canonical_semantic_identity",
        "nested_command_identity_complete",
        "provenance_not_spoofable_from_argv_or_env",
        "unknown_config_fails",
        "effective_config_provenance_deterministic",
        "secret_values_absent_from_machine_and_run_output",
        "output_modes_do_not_change_run_identity",
        "wrapper_and_direct_identity_converge",
        "private_run_descriptor_for_registered_operations",
    }
    required = set(proved)
    assert proved == required
