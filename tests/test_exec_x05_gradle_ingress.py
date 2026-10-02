from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat

import pytest

from devtool.artifact_tests import run_artifact_tests
from devtool.gradle_ingress import converge_gradle_spec, converge_gradle_specs
from devtool.gradle_planning import GradleInvocationRequest, GradleLauncherIdentity
from devtool.gradle_takeover import GradleIntent, GradleOrigin, GradleTakeoverError, broker_gradle_invocation
from devtool.models import CommandSpec, ProjectConfig
from devtool.project_runners import build_gradle_task_specs
from devtool.runners import build_validation_specs, run_specs
from devtool.test_declarations import command_specs
from devtool.test_model import TestSpec


def _exe(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _wrapper(root: Path, *, exit_code: int = 0) -> Path:
    wrapper = _exe(root / "gradlew", f"#!/bin/sh\nexit {exit_code}\n")
    props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    props.write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.7-bin.zip\n",
        encoding="utf-8",
    )
    (props.parent / "gradle-wrapper.jar").write_bytes(b"exec-x05-wrapper")
    return wrapper


def _config(
    root: Path,
    *,
    enabled: bool = True,
    mode: str = "manage",
    scope: str = "devtool",
    execution_environment: str = "host",
    invocation_source: str = "",
    invocation_command: str = "",
) -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind="gradle",
        runner="gradle",
        project_root=str(root),
        execution_environment=execution_environment,
        invocation_source=invocation_source,
        invocation_command=invocation_command,
        runner_options={
            "gradle": {
                "takeover": {
                    "enabled": enabled,
                    "mode": mode,
                    "scope": scope,
                    "launcher": "auto",
                }
            }
        },
    )


def _raw(root: Path, *, env: dict[str, str] | None = None, metadata: dict[str, object] | None = None) -> CommandSpec:
    return CommandSpec(
        name="gradle-help",
        argv=["./gradlew", "help", "--stacktrace"],
        cwd=str(root),
        env=dict(env or {}),
        metadata=dict(metadata or {}),
    )


def test_disabled_takeover_is_behaviorally_inert_for_raw_gradle_ingress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config(tmp_path, enabled=False)
    spec = CommandSpec(name="raw", argv=["gradle", "help"], cwd=str(tmp_path), env={"KEEP": "yes"})

    def should_not_resolve(*args, **kwargs):
        raise AssertionError("disabled takeover must not resolve a real launcher")

    monkeypatch.setattr("devtool.gradle_takeover.resolve_real_launcher", should_not_resolve)
    rendered = converge_gradle_spec(cfg, spec)

    assert len(rendered) == 1
    assert rendered[0].argv == ["gradle", "help"]
    assert rendered[0].cwd == str(tmp_path)
    assert rendered[0].env == {"KEEP": "yes"}
    assert rendered[0].metadata["gradle_takeover"]["receipt"]["bypass_reason"] == "takeover-disabled"


def test_raw_gradlew_is_lowered_once_and_preserves_argv_cwd_and_semantic_env(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    cfg = _config(tmp_path)
    original = _raw(tmp_path, env={"EXEC_X05_SENTINEL": "preserved"})

    rendered = converge_gradle_spec(cfg, original, default_origin=GradleOrigin.INVOKE)

    assert len(rendered) == 1
    spec = rendered[0]
    assert Path(spec.argv[0]).resolve() == wrapper.resolve()
    assert "help" in spec.argv
    assert "--stacktrace" in spec.argv
    assert spec.cwd == str(tmp_path)
    assert spec.env["EXEC_X05_SENTINEL"] == "preserved"
    assert spec.metadata["gradle_takeover_original_argv"] == ["./gradlew", "help", "--stacktrace"]
    assert spec.metadata["gradle_takeover_original_cwd"] == str(tmp_path)
    assert spec.metadata["gradle_takeover_origin"] == "invoke"
    assert spec.metadata["gradle_takeover"]["receipt"]["canonicalized"] is True
    assert spec.metadata["gradle_execution_plan"]["request"]["raw_args"] == ["--stacktrace"]


def test_equivalent_wrapper_and_workflow_ingress_share_semantic_identity_not_receipt(tmp_path: Path) -> None:
    _wrapper(tmp_path)
    base = _config(tmp_path)
    wrapper_cfg = replace(base, invocation_source="wrapper", invocation_command="build")

    from_wrapper = converge_gradle_spec(wrapper_cfg, _raw(tmp_path), default_origin=GradleOrigin.DEVTOOL)[0]
    from_workflow = converge_gradle_spec(base, _raw(tmp_path), default_origin=GradleOrigin.WORKFLOW)[0]

    assert from_wrapper.metadata["gradle_takeover_origin"] == "wrapper"
    assert from_workflow.metadata["gradle_takeover_origin"] == "workflow"
    assert from_wrapper.metadata["gradle_takeover_receipt_id"] != from_workflow.metadata["gradle_takeover_receipt_id"]
    assert from_wrapper.metadata["gradle_takeover_semantic_identity"] == from_workflow.metadata["gradle_takeover_semantic_identity"]
    assert from_wrapper.metadata["environment_domains"]["gradle_takeover"] == from_workflow.metadata["environment_domains"]["gradle_takeover"]


def test_typed_gradle_job_is_brokered_once_then_retagged_without_double_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wrapper(tmp_path)
    cfg = _config(tmp_path)
    import devtool.gradle_takeover as takeover

    original = takeover.plan_gradle_invocation
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(takeover, "plan_gradle_invocation", counted)
    specs = build_gradle_task_specs(cfg, "gradle", ["help"], name="typed-help", action="test")
    assert calls == 1
    semantic = specs[0].metadata["gradle_takeover_semantic_identity"]

    specs = converge_gradle_specs(cfg, specs, default_origin=GradleOrigin.TESTSPEC)
    assert calls == 1
    assert specs[0].metadata["gradle_takeover_origin"] == "testspec"
    assert specs[0].metadata["gradle_takeover_semantic_identity"] == semantic
    assert specs[0].metadata["gradle_takeover_origin_chain"][-2:] == ["devtool", "testspec"]


def test_probe_and_control_remain_non_work_even_when_typed_caller_requires_a_plan(tmp_path: Path) -> None:
    wrapper = _wrapper(tmp_path)
    cfg = _config(tmp_path)
    launcher = GradleLauncherIdentity.create(executable=str(wrapper), source="project-wrapper", project_root=tmp_path)
    request = GradleInvocationRequest(
        caller="exec-x05",
        action="probe",
        project_root=str(tmp_path),
        requested_roots=("help",),
    )
    for intent in (GradleIntent.PROBE, GradleIntent.CONTROL):
        decision = broker_gradle_invocation(
            cfg,
            request,
            origin=GradleOrigin.DEVTOOL,
            intent=intent,
            requested_launcher="wrapper",
            require_plan=True,
            fallback_launcher=launcher,
        )
        assert decision.plan is None
        assert decision.receipt.bypass_reason == f"typed-{intent.value}-bypass"


def test_testspec_and_validation_raw_gradle_paths_cross_ingress_guard(tmp_path: Path) -> None:
    _wrapper(tmp_path)
    cfg = _config(tmp_path)
    cfg.tests = {
        "raw-gradle": TestSpec(
            id="raw-gradle",
            name="raw Gradle",
            runner="command",
            command=("./gradlew", "help"),
            cwd=str(tmp_path),
        )
    }
    declared = command_specs(cfg, ["raw-gradle"])
    assert declared[0].metadata["gradle_takeover_origin"] == "testspec"
    assert declared[0].metadata["gradle_takeover"]["receipt"]["canonicalized"] is True

    validation_cfg = replace(
        cfg,
        kind="command",
        runner="command",
        build_command="./gradlew help",
        tests={},
    )
    validation = build_validation_specs(validation_cfg, skip_restore=True, skip_test=True)
    assert validation[0].metadata["gradle_takeover_origin"] == "validation"
    assert validation[0].metadata["gradle_takeover"]["receipt"]["canonicalized"] is True


def test_artifact_validation_raw_gradle_path_cannot_bypass_ingress_guard(tmp_path: Path) -> None:
    _wrapper(tmp_path)
    (tmp_path / ".devtool.toml").write_text(
        """[project]\nrunner = "command"\nroot = "."\n\n[gradle.takeover]\nenabled = true\nmode = "manage"\nscope = "devtool"\n""",
        encoding="utf-8",
    )
    manifest = {
        "validation": {
            "tests": [
                {"id": "artifact-gradle", "runner": "command", "command": ["./gradlew", "help"]}
            ]
        }
    }
    results, _ = run_artifact_tests(tmp_path, manifest, dry_run=True)
    assert len(results) == 1
    assert results[0].metadata["gradle_takeover_origin"] == "artifact"
    assert results[0].metadata["gradle_takeover"]["receipt"]["canonicalized"] is True


def test_ssh_ingress_preserves_transport_origin_destination_and_outer_wrapper_lineage(tmp_path: Path) -> None:
    cfg = _config(tmp_path, invocation_source="wrapper", invocation_command="build")
    spec = CommandSpec(
        name="remote-gradle",
        argv=["gradle", "help"],
        cwd="/remote/repo",
        metadata={"workflow_execution_environment": "ssh"},
    )
    rendered = converge_gradle_spec(cfg, spec, default_origin=GradleOrigin.WORKFLOW)[0]
    receipt = rendered.metadata["gradle_takeover"]["receipt"]

    assert rendered.metadata["gradle_takeover_origin"] == "ssh"
    assert rendered.metadata["gradle_takeover_origin_chain"] == ["wrapper", "ssh"]
    assert receipt["real_launcher"] == "gradle"
    assert receipt["destination_identity"]
    assert rendered.metadata["gradle_takeover_destination_identity"] == receipt["destination_identity"]


def test_opaque_shell_escape_is_truthful_and_strict_mode_rejects_it(tmp_path: Path) -> None:
    _wrapper(tmp_path)
    manage = _config(tmp_path, mode="manage")
    opaque = CommandSpec(name="nested", argv=["sh", "-c", "echo before; ./gradlew help"], cwd=str(tmp_path))
    managed = converge_gradle_spec(manage, opaque, default_origin=GradleOrigin.WORKFLOW)[0]
    assert managed.argv == ["sh", "-c", "echo before; ./gradlew help"]
    assert managed.metadata["gradle_takeover_opaque_shell"] is True
    assert managed.metadata["gradle_takeover_coverage"] == "unrepresentable-devtool-shell"

    strict = _config(tmp_path, mode="strict")
    with pytest.raises(GradleTakeoverError, match="cannot safely canonicalize opaque"):
        converge_gradle_spec(strict, CommandSpec(name="nested", argv=list(opaque.argv), cwd=str(tmp_path)))


def test_run_evidence_contains_takeover_receipt_and_preserves_underlying_cancellation(tmp_path: Path) -> None:
    _wrapper(tmp_path, exit_code=130)
    cfg = _config(tmp_path)
    spec = CommandSpec(name="cancel-gradle", argv=["./gradlew", "help"], cwd=str(tmp_path))

    results, _ = run_specs(cfg, [spec], verbose=False)

    assert len(results) == 1
    result = results[0]
    assert result.returncode == 130
    assert result.metadata["gradle_takeover_receipt_id"]
    assert result.metadata["gradle_takeover_semantic_identity"]
    takeover = result.metadata["gradle_takeover"]
    assert takeover["receipt"]["origin"] == "devtool"
    assert takeover["receipt"]["requested_launcher"] == "wrapper"
    assert takeover["receipt"]["real_launcher"]
    assert takeover["config"]["mode"] == "manage"


def test_cli_wrapper_provenance_is_carried_into_runtime_project_config(tmp_path: Path) -> None:
    _wrapper(tmp_path)
    (tmp_path / ".devtool.toml").write_text(
        """[project]\nrunner = "gradle"\nroot = "."\n""",
        encoding="utf-8",
    )
    from devtool.cli import _load_command_config, build_parser

    args = build_parser().parse_args(["-r", str(tmp_path), "build"])
    from devtool.invocation_origin import trusted_invocation_context
    with trusted_invocation_context(source="wrapper", command="build", wrapper_schema="1"):
        config = _load_command_config(args)
    assert config.invocation_source == "wrapper"
    assert config.invocation_command == "build"

    spec = converge_gradle_spec(
        replace(config, runner_options={"gradle": {"takeover": {"enabled": True, "mode": "manage", "scope": "devtool"}}}),
        _raw(tmp_path),
    )[0]
    assert spec.metadata["gradle_takeover_origin"] == "wrapper"
