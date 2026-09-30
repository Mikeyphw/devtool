from __future__ import annotations

import json
from pathlib import Path

import pytest

from devtool.environment_qualification import assert_environment_overlay_safe
from devtool.gradle_ingress import converge_gradle_spec
from devtool.gradle_planning import GradleInvocationRequest
from devtool.gradle_takeover import (
    GradleIntent,
    GradleOrigin,
    GradleTakeoverError,
    broker_gradle_invocation,
    resolve_real_launcher,
    shell_bin_dir,
    takeover_metadata,
)
from devtool.models import CommandSpec, ProjectConfig
from devtool.presentation_surfaces import (
    PresentationConsumer,
    PresentationSurface,
    build_presentation_bundle,
    gradle_takeover_projection,
)


def _wrapper(root: Path) -> Path:
    path = root / "gradlew"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    props.write_text("distributionUrl=https\\://services.gradle.org/distributions/gradle-9.7-bin.zip\n", encoding="utf-8")
    (props.parent / "gradle-wrapper.jar").write_bytes(b"gate-wrapper")
    return path


def _config(root: Path, *, mode: str = "strict", scope: str = "both") -> ProjectConfig:
    _wrapper(root)
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="gradle",
        runner="gradle",
        execution_environment="native-termux",
        runner_options={
            "gradle": {
                "takeover": {
                    "enabled": True,
                    "mode": mode,
                    "scope": scope,
                }
            }
        },
    )


def test_gate_shim_first_path_resolves_away_from_takeover_shim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real = real_dir / "gradle"
    real.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    real.chmod(0o755)
    shim = shell_bin_dir()
    shim.mkdir(parents=True, exist_ok=True)
    (shim / ".devtool-gradle-takeover-shim").write_text("gate\n", encoding="utf-8")
    (shim / "gradle").write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    (shim / "gradle").chmod(0o755)
    env = {"PATH": f"{shim}:{real_dir}"}
    cfg = _config(tmp_path)
    resolved = resolve_real_launcher(cfg, requested="system", env=env)
    assert Path(resolved.executable).resolve() == real.resolve()
    assert str(shim.resolve()) in resolved.stripped_shim_dirs
    assert Path(resolved.executable).parent != shim.resolve()


def test_gate_repository_cannot_forge_internal_bypass() -> None:
    with pytest.raises(ValueError, match="protected Devtool environment variable"):
        assert_environment_overlay_safe({"DEVTOOL_GRADLE_TAKEOVER_CAPABILITY": "forged"}, source="repository")


def test_gate_work_probe_control_remain_distinct_and_nonrecursive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path)
    request = GradleInvocationRequest(
        caller="gradle-takeover-g1",
        action="gate",
        project_root=str(tmp_path),
        requested_roots=("help",),
    )
    work = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.WORK, requested_launcher="wrapper")
    probe = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.PROBE, requested_launcher="wrapper")
    control = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.CONTROL, requested_launcher="wrapper")
    assert work.plan is not None and work.receipt.canonicalized is True
    assert probe.plan is None and probe.receipt.bypass_reason == "typed-probe-bypass"
    assert control.plan is None and control.receipt.bypass_reason == "typed-control-bypass"
    assert len({work.receipt.receipt_id, probe.receipt.receipt_id, control.receipt.receipt_id}) == 3


def test_gate_representative_ingress_converges_on_one_semantic_identity_domain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path, mode="manage", scope="devtool")
    origins = (
        GradleOrigin.WRAPPER,
        GradleOrigin.INVOKE,
        GradleOrigin.WORKFLOW,
        GradleOrigin.VALIDATION,
        GradleOrigin.TESTSPEC,
        GradleOrigin.ARTIFACT,
        GradleOrigin.ANDROID,
    )
    identities = set()
    receipts = set()
    for origin in origins:
        spec = CommandSpec(name=origin.value, argv=["./gradlew", "help", "--offline"], cwd=str(tmp_path))
        rendered = converge_gradle_spec(cfg, spec, default_origin=origin)[0]
        identities.add(str(rendered.metadata["gradle_takeover_semantic_identity"]))
        receipts.add(str(rendered.metadata["gradle_takeover_receipt_id"]))
    assert len(identities) == 1
    assert len(receipts) == len(origins)


def test_gate_semantic_identity_ignores_volatile_host_evidence_when_effective_policy_is_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path, mode="manage", scope="devtool")
    samples = iter(((3363, 8192), (3200, 8192)))
    monkeypatch.setattr("devtool.build_optimization.memory_info_mb", lambda: next(samples))

    first = converge_gradle_spec(
        cfg, CommandSpec(name="wrapper", argv=["./gradlew", "help", "--offline"], cwd=str(tmp_path)),
        default_origin=GradleOrigin.WRAPPER,
    )[0]
    second = converge_gradle_spec(
        cfg, CommandSpec(name="workflow", argv=["./gradlew", "help", "--offline"], cwd=str(tmp_path)),
        default_origin=GradleOrigin.WORKFLOW,
    )[0]

    first_plan = first.metadata["gradle_execution_plan"]
    second_plan = second.metadata["gradle_execution_plan"]
    assert first_plan["policy"]["resource_profile_evidence"]["memory_mb"] != second_plan["policy"]["resource_profile_evidence"]["memory_mb"]
    assert first_plan["invocations"] != second_plan["invocations"]  # adaptive Xmx may legitimately differ
    assert first.metadata["gradle_takeover_semantic_identity"] == second.metadata["gradle_takeover_semantic_identity"]


def test_gate_strict_opaque_shell_escape_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path, mode="strict", scope="devtool")
    with pytest.raises(GradleTakeoverError, match="cannot safely canonicalize opaque"):
        converge_gradle_spec(
            cfg,
            CommandSpec(name="escape", argv=["sh", "-c", "echo x; ./gradlew help"], cwd=str(tmp_path)),
            default_origin=GradleOrigin.WORKFLOW,
        )


def test_gate_takeover_projection_reports_canonical_state_to_machine_plain_and_textual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path, mode="manage", scope="devtool")
    spec = converge_gradle_spec(
        cfg,
        CommandSpec(name="projection", argv=["./gradlew", "help"], cwd=str(tmp_path)),
        default_origin=GradleOrigin.WORKFLOW,
    )[0]
    projection = gradle_takeover_projection([
        {
            "authority": "RUN",
            "ref": "run:gate:1",
            "status": "passed",
            "metadata": spec.metadata,
        }
    ], surface=PresentationSurface.MACHINE)
    bundle = build_presentation_bundle(
        projection,
        consumers=(PresentationConsumer.MACHINE, PresentationConsumer.TERMINAL, PresentationConsumer.TUI),
    )
    machine = bundle.as_machine()
    encoded = json.dumps(machine, sort_keys=True)
    assert "mode=manage" in projection.summary
    assert "origin=workflow" in projection.summary
    assert "requested_launcher" in encoded and "real_launcher" in encoded
    assert "gradle-takeover-receipt" in encoded
    assert machine["generated_truth"] is False
    renders = {item.consumer.value: item for item in bundle.renders}
    assert "Gradle takeover" in renders["terminal"].payload["text"]
    assert any("mode=manage" in section["body"] for section in renders["tui"].payload["sections"])


def test_gate_projection_uses_existing_receipt_without_replanning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = _config(tmp_path, mode="manage", scope="devtool")
    request = GradleInvocationRequest(caller="gate", action="gate", project_root=str(tmp_path), requested_roots=("help",))
    decision = broker_gradle_invocation(cfg, request, origin=GradleOrigin.WORKFLOW, requested_launcher="wrapper")
    metadata = takeover_metadata(decision)
    projection = gradle_takeover_projection([{"ref": "run:1", "metadata": metadata}])
    assert projection.events[0].detail["receipt_id"] == decision.receipt.receipt_id
    assert projection.events[0].detail["semantic_identity"] == metadata["gradle_takeover_semantic_identity"]


def test_authoritative_gate_rejects_construction_gradle_override_without_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.gradle_takeover_g1_gate import GradleTakeoverG1Error, _gradle_executable
    fake = tmp_path / "gradle"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_GRADLE_TAKEOVER_G1_GRADLE", str(fake))
    monkeypatch.delenv("DEVTOOL_GRADLE_TAKEOVER_G1_ALLOW_TEST_GRADLE", raising=False)
    with pytest.raises(GradleTakeoverG1Error):
        _gradle_executable()
