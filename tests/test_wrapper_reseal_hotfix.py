from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.wrapper_execution import (
    WrapperExecutionBridgeProbe,
    execute_wrapper_resolution,
    probe_wrapper_execution_bridge,
)
from devtool.wrapper_final_seal import build_wrapper_final_seal, wrapper_final_seal_as_dict
from devtool.wrapper_launcher import install_launchers
from devtool.wrapper_resolution import resolve_wrapper_argv


def _write(root: Path, text: str) -> None:
    (root / ".devtool.toml").write_text(text.strip() + "\n", encoding="utf-8")
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "WRAPPER.md").write_text("# Wrapper\n\ndevtool wrapper seal\n", encoding="utf-8")


def _config(extra: str = "") -> str:
    return f'''
[project]
runner = "command"

[workspace]
default_target = "android"

[targets.android]
runner = "command"

[targets.android.workflows]
codegen = ["runner:command#build"]

[wrapper]
schema = 1
default_command = "build"

[wrapper.commands.build]
description = "Build"
devtool = "build"
target = "android"

[wrapper.commands.codegen]
description = "Codegen"
workflow = "codegen"
target = "android"

{extra}
'''


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True)


def _sealed_config(extra: str = ""):
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    _write(root, _config(extra))
    install_launchers(root)
    return td, root, _load(root)


def test_shared_bridge_reenters_once_and_propagates_callback_exit_code() -> None:
    td, root, config = _sealed_config()
    try:
        resolution = resolve_wrapper_argv(config, ["build"], launch_cwd=root)
        from devtool.invocation_origin import current_trusted_invocation

        calls: list[list[str]] = []
        origins: list[object] = []
        def reenter(argv):
            calls.append(list(argv))
            origins.append(current_trusted_invocation())
            return 29

        code = execute_wrapper_resolution(resolution, reenter=reenter)
        assert code == 29
        assert len(calls) == 1
        assert not any(item.startswith("--internal-invocation-") or item == "--internal-wrapper-schema" for item in calls[0])
        assert origins[0] is not None
        assert origins[0].source == "wrapper"
        assert origins[0].command == "build"
    finally:
        td.cleanup()


def test_final_seal_v2_contains_bridge_and_exo_origin_evidence() -> None:
    td, root, config = _sealed_config('''
[wrapper.commands.upload]
description = "Upload"
devtool = "run"
target = "android"

[wrapper.commands.upload.options.api_token]
type = "string"
required = true
command_argv = ["--token", "{value}"]
''')
    try:
        payload = wrapper_final_seal_as_dict(build_wrapper_final_seal(config))
    finally:
        td.cleanup()

    assert payload["schema_version"] == 2
    assert payload["status"] == "ok"
    checks = {(item["section"], item["name"]): item for item in payload["checks"]}

    for name in ("generic-contract", "command:build", "command:codegen"):
        assert checks[("execution-bridge", name)]["status"] == "ok"
        assert checks[("execution-origin", name)]["status"] == "ok"
        assert checks[("execution-bridge", name)]["data"]["reentry_count"] == 1
        assert checks[("execution-origin", name)]["data"]["invocation_source"] == "wrapper"

    assert checks[("execution-bridge", "command:upload")]["status"] == "skipped"
    assert checks[("execution-origin", "command:upload")]["status"] == "skipped"


def test_final_seal_fails_closed_if_bridge_no_longer_reenters_once(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.wrapper_final_seal as final_seal

    td, _root, config = _sealed_config()
    try:
        real = probe_wrapper_execution_bridge

        def broken_probe(resolution):
            probe = real(resolution)
            return WrapperExecutionBridgeProbe(
                command_token=probe.command_token,
                reentry_count=2,
                callback_exit_code=probe.callback_exit_code,
                invocation_source=probe.invocation_source,
                invocation_command=probe.invocation_command,
                wrapper_schema=probe.wrapper_schema,
                internal_control_counts=probe.internal_control_counts,
                nested_wrapper_dispatch=probe.nested_wrapper_dispatch,
                canonical_argv=probe.canonical_argv,
            )

        monkeypatch.setattr(final_seal, "probe_wrapper_execution_bridge", broken_probe)
        payload = wrapper_final_seal_as_dict(build_wrapper_final_seal(config))
    finally:
        td.cleanup()

    assert payload["status"] == "error"
    checks = {(item["section"], item["name"]): item for item in payload["checks"]}
    assert checks[("execution-bridge", "generic-contract")]["status"] == "error"
    assert checks[("execution-origin", "generic-contract")]["status"] == "error"


def test_final_seal_fails_closed_if_wrapper_origin_does_not_persist(monkeypatch: pytest.MonkeyPatch) -> None:
    import devtool.wrapper_final_seal as final_seal

    td, _root, config = _sealed_config()
    try:
        def wrong_metadata(*, source, command, wrapper_schema=""):
            return {
                "invocation_source": "cli",
                "invocation_command": str(command),
                "wrapper_schema": str(wrapper_schema),
            }

        monkeypatch.setattr(final_seal, "build_invocation_metadata", wrong_metadata)
        payload = wrapper_final_seal_as_dict(build_wrapper_final_seal(config))
    finally:
        td.cleanup()

    assert payload["status"] == "error"
    checks = {(item["section"], item["name"]): item for item in payload["checks"]}
    assert checks[("execution-origin", "generic-contract")]["status"] == "error"


def test_reseal_evidence_never_exports_sensitive_default() -> None:
    secret = "RESEAL_SECRET_812733"
    td, _root, config = _sealed_config(f'''
[wrapper.commands.publish]
description = "Publish"
devtool = "run"
target = "android"

[wrapper.commands.publish.options.api_token]
type = "string"
default = "{secret}"
command_argv = ["--token={{value}}"]
''')
    try:
        payload = wrapper_final_seal_as_dict(build_wrapper_final_seal(config))
    finally:
        td.cleanup()

    encoded = json.dumps(payload, sort_keys=True)
    assert payload["status"] == "ok"
    assert secret not in encoded
    assert "<redacted>" in encoded


def test_workflow_bridge_evidence_uses_invoke_without_nested_wrapper_dispatch() -> None:
    td, root, config = _sealed_config()
    try:
        resolution = resolve_wrapper_argv(config, ["codegen"], launch_cwd=root)
        probe = probe_wrapper_execution_bridge(resolution)
    finally:
        td.cleanup()

    assert probe.ok is True
    assert probe.command_token == "invoke"
    assert probe.invocation_command == "codegen"
    assert probe.reentry_count == 1
    assert probe.nested_wrapper_dispatch is False
