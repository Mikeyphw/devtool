from __future__ import annotations

from pathlib import Path

import pytest

from devtool.gradle_planning import (
    GradleExecutionReceipt,
    GradleInvocationRequest,
    GradleLauncherIdentity,
    GradlePlanningError,
    aggregate_qualification_proof,
    plan_gradle_invocation,
)
from devtool.models import ProjectConfig


def _config(root: Path, *, gradle: dict[str, object] | None = None, validation: dict[str, object] | None = None) -> ProjectConfig:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle").write_text("rootProject.name='env-x03'\n", encoding="utf-8")
    return ProjectConfig(
        repo_root=root,
        project_root=str(root),
        kind="gradle",
        runner="gradle",
        execution_environment="desktop-linux",
        runner_options={
            "gradle": dict(gradle or {}),
            "validation": dict(validation or {}),
        },
    )


def _launcher() -> GradleLauncherIdentity:
    return GradleLauncherIdentity.create(
        executable="gradle",
        source="system",
        required_version="9.7",
        effective_version="9.7",
    )


def _request(root: Path, *roots: str, phase: str = "") -> GradleInvocationRequest:
    return GradleInvocationRequest(
        caller="env-x03-test",
        action="validation" if phase else "build",
        project_root=str(root),
        requested_roots=tuple(roots),
        validation=bool(phase),
        validation_phase=phase,
    )




def _qualify_alias(
    alias: dict[str, object], *, config: ProjectConfig, initial, roots: tuple[str, ...],
    replacement: str, name: str = "validation",
) -> dict[str, object]:
    replacement_plan = plan_gradle_invocation(
        config=config,
        request=_request(Path(config.project_root), replacement),
        launcher=_launcher(),
        cpu_count=4,
        memory_mb=4096,
    )
    outcomes = {root: "success" for root in roots}
    outcomes[replacement] = "success"
    receipt = GradleExecutionReceipt.from_plan_metadata(
        replacement_plan.as_dict(),
        progress={"observed_tasks": [*roots, replacement], "task_outcomes": outcomes},
        success=True,
        process_executed=True,
        exo_run_ref="process:aggregate-proof",
        run_ref="run:aggregate-proof",
    )
    proof = aggregate_qualification_proof(
        name=name, replacement=replacement, roots=roots,
        context=initial.context, semantic_request_digest=initial.semantic_request_digest,
        evidence_receipt=receipt.as_dict(),
    )
    alias.update(proof)
    return proof

def test_balanced_policy_keeps_reuse_layers_and_records_requested_effective_truth(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"max_workers": 6, "configuration_cache": "on"})
    plan = plan_gradle_invocation(
        config=config,
        request=_request(tmp_path, "assemble"),
        launcher=_launcher(),
        cpu_count=4,
        memory_mb=4096,
    )

    assert plan.policy.resource_profile == "balanced"
    assert plan.policy.max_workers.requested == 6
    assert plan.policy.max_workers.effective == 4
    assert plan.policy.max_workers.reason == "resource-profile clamp"
    assert plan.policy.daemon.effective is True
    assert plan.policy.vfs_watch.effective is True
    assert plan.policy.build_cache.effective is True
    assert plan.policy.configuration_cache.effective == "on"
    assert plan.policy.parallel.effective is True
    assert "--configuration-cache" in plan.invocations[0].args
    assert "--build-cache" in plan.invocations[0].args
    assert "--daemon" in plan.invocations[0].args
    assert "-Dorg.gradle.vfs.watch=true" in plan.invocations[0].args


def test_lint_heap_floor_is_not_clamped_by_default_heap_but_respects_host_safe_ceiling(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        gradle={"gradle_heap_mb": 1024},
        validation={},
    )
    plan = plan_gradle_invocation(
        config=config,
        request=_request(tmp_path, "lint", phase="lint"),
        launcher=_launcher(),
        cpu_count=4,
        memory_mb=4096,
    )

    assert plan.policy.heap.default_mb == 1024
    assert plan.policy.heap.requested_mb is None
    assert plan.policy.heap.phase_minimum_mb == 1536
    assert plan.policy.heap.host_safe_ceiling_mb == int(4096 * 0.45)
    assert plan.policy.heap.effective_mb == 1536
    assert plan.policy.heap.effective_mb > plan.policy.heap.default_mb


def test_managed_and_raw_gradle_flags_fail_closed_on_conflict(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"configuration_cache": "on", "args": ["--no-configuration-cache"]})
    with pytest.raises(GradlePlanningError, match="configuration_cache"):
        plan_gradle_invocation(
            config=config,
            request=_request(tmp_path, "check"),
            launcher=_launcher(),
            cpu_count=2,
            memory_mb=4096,
        )


def test_unqualified_aggregate_is_rejected_and_exact_context_qualification_is_accepted(tmp_path: Path) -> None:
    alias = {
        "tasks": ["assemble", "test", "lint"],
        "replacement": "devtoolValidation",
        "qualified": True,  # Legacy boolean must not self-authorize.
    }
    config = _config(tmp_path, gradle={"task_aliases": {"validation": alias}})
    request = _request(tmp_path, "assemble", "test", "lint")
    first = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert first.optimized_roots == ("assemble", "test", "lint")
    assert first.rejected_aggregates == ("validation",)
    assert first.aggregate_qualifications == ()

    proof = _qualify_alias(
        alias, config=config, initial=first,
        roots=("assemble", "test", "lint"), replacement="devtoolValidation",
    )
    qualified = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert qualified.optimized_roots == ("devtoolValidation",)
    assert qualified.rejected_aggregates == ()
    assert qualified.aggregate_qualifications[0].qualification_id == proof["qualification_id"]
    assert qualified.aggregate_qualifications[0].evidence_receipt_id == proof["evidence_receipt_id"]



def test_aggregate_qualification_requires_real_receipt_outcome_proof(tmp_path: Path) -> None:
    alias = {"tasks": ["assemble", "test"], "replacement": "verified"}
    config = _config(tmp_path, gradle={"task_aliases": {"validation": alias}})
    request = _request(tmp_path, "assemble", "test")
    initial = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    replacement_plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "verified"), launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    incomplete = GradleExecutionReceipt.from_plan_metadata(
        replacement_plan.as_dict(),
        progress={"observed_tasks": ["assemble", "verified"], "task_outcomes": {"assemble": "success", "verified": "success"}},
        success=True, process_executed=True, exo_run_ref="process:proof", run_ref="run:proof",
    )
    with pytest.raises(GradlePlanningError, match="observe every replaced root"):
        aggregate_qualification_proof(
            name="validation", replacement="verified", roots=("assemble", "test"),
            context=initial.context, semantic_request_digest=initial.semantic_request_digest,
            evidence_receipt=incomplete.as_dict(),
        )


def test_protected_raw_context_and_heap_arguments_fail_closed(tmp_path: Path) -> None:
    for protected in (
        "-Dorg.gradle.jvmargs=-Xmx8g",
        "--project-dir=/tmp/other",
        "-Dorg.gradle.java.home=/tmp/jdk",
    ):
        config = _config(tmp_path / protected.split("=")[0].replace("/", "_").replace("-", "x"), gradle={"args": [protected]})
        with pytest.raises(GradlePlanningError, match="protected Devtool"):
            plan_gradle_invocation(
                config=config,
                request=_request(Path(config.project_root), "check"),
                launcher=_launcher(),
                cpu_count=2,
                memory_mb=4096,
            )


def test_argument_classes_preserve_passthrough_and_managed_identity(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"args": ["-Pfeature=true", "--build-cache", "--max-workers=2"], "max_workers": 2})
    plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert plan.arguments.raw == ("-Pfeature=true", "--build-cache", "--max-workers=2")
    assert plan.arguments.managed == ("--build-cache", "--max-workers=2")
    assert plan.arguments.protected == ()
    assert plan.arguments.passthrough == ("-Pfeature=true",)
    assert plan.arguments.semantic_properties == ("-Pfeature=<redacted>",)
    assert plan.arguments.argument_digest
    assert plan.as_dict()["arguments"]["raw"][0] == "-Pfeature=<redacted>"
    assert plan.invocations[0].args[0] == "-Pfeature=true"

def test_destructive_and_effectful_roots_are_typed_invocation_barriers(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = plan_gradle_invocation(
        config=config,
        request=_request(tmp_path, "test", "clean", "publishBundle"),
        launcher=_launcher(),
        cpu_count=2,
        memory_mb=4096,
    )

    assert [item.roots for item in plan.invocations] == [("test",), ("clean",), ("publishBundle",)]
    assert plan.invocations[0].barriers == ()
    assert [b.kind for b in plan.invocations[1].barriers] == ["clean"]
    assert [b.kind for b in plan.invocations[2].barriers] == ["effect"]


def test_property_form_managed_switches_resolve_once_and_device_roots_split(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        gradle={
            "parallel": True,
            "build_cache": True,
            "args": ["-Dorg.gradle.parallel=true", "-Dorg.gradle.caching=true"],
        },
    )
    plan = plan_gradle_invocation(
        config=config,
        request=_request(tmp_path, "test", "connectedDebugAndroidTest"),
        launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )

    assert "-Dorg.gradle.parallel=true" in plan.arguments.managed
    assert "-Dorg.gradle.caching=true" in plan.arguments.managed
    assert "-Dorg.gradle.parallel=true" not in plan.invocations[0].args
    assert "--parallel" in plan.invocations[0].args
    assert [item.roots for item in plan.invocations] == [("test",), ("connectedDebugAndroidTest",)]
    assert [barrier.kind for barrier in plan.invocations[1].barriers] == ["device"]


def test_configuration_cache_required_accepts_modern_standard_telemetry_but_rejects_legacy_full(tmp_path: Path) -> None:
    standard = _config(
        tmp_path / "standard",
        gradle={"configuration_cache": "required", "telemetry_level": "standard"},
    )
    standard_plan = plan_gradle_invocation(
        config=standard, request=_request(Path(standard.project_root), "check"), launcher=_launcher(),
        cpu_count=4, memory_mb=4096,
    )
    assert standard_plan.policy.configuration_cache.effective == "on"
    assert standard_plan.policy.configuration_cache_qualification.telemetry_level == "standard"

    full = _config(
        tmp_path / "full",
        gradle={"configuration_cache": "required", "telemetry_level": "full"},
    )
    with pytest.raises(GradlePlanningError, match="Configuration Cache is required"):
        plan_gradle_invocation(
            config=full, request=_request(Path(full.project_root), "check"), launcher=_launcher(),
            cpu_count=4, memory_mb=4096,
        )


def test_aggregate_qualification_becomes_stale_when_build_logic_changes(tmp_path: Path) -> None:
    alias = {"tasks": ["assemble", "test"], "replacement": "verified"}
    config = _config(tmp_path, gradle={"task_aliases": {"validation": alias}})
    request = _request(tmp_path, "assemble", "test")
    initial = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    _qualify_alias(
        alias, config=config, initial=initial, roots=("assemble", "test"), replacement="verified",
    )
    accepted = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert accepted.optimized_roots == ("verified",)

    (tmp_path / "build.gradle").write_text("tasks.register('changed')\n", encoding="utf-8")
    stale = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert stale.optimized_roots == ("assemble", "test")
    assert stale.rejected_aggregates == ("validation",)


def test_evidence_projection_redacts_raw_property_values_without_changing_execution_argv(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"args": ["-PapiToken=super-secret"]})
    plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        cpu_count=2, memory_mb=4096,
    )
    assert "-PapiToken=super-secret" in plan.invocations[0].args
    assert plan.as_dict()["arguments"]["raw"] == ["-PapiToken=<redacted>"]
    assert plan.as_dict()["request"]["raw_args"] == ["-PapiToken=<redacted>"]


def test_semantic_property_values_change_opaque_plan_identity_without_leaking_values(tmp_path: Path) -> None:
    first_config = _config(tmp_path / "first", gradle={"args": ["-Pfeature=true"]})
    first = plan_gradle_invocation(
        config=first_config,
        request=_request(Path(first_config.project_root), "check"),
        launcher=_launcher(), cpu_count=2, memory_mb=4096,
    )
    second_config = _config(tmp_path / "second", gradle={"args": ["-Pfeature=false"]})
    # Normalize project identity to isolate the property-value contribution.
    second_request = _request(Path(second_config.project_root), "check")
    second = plan_gradle_invocation(
        config=second_config,
        request=second_request,
        launcher=_launcher(), cpu_count=2, memory_mb=4096,
    )
    assert first.arguments.argument_digest != second.arguments.argument_digest
    assert first.policy.configuration_cache_qualification.semantic_argument_digest == first.arguments.argument_digest
    assert second.policy.configuration_cache_qualification.semantic_argument_digest == second.arguments.argument_digest
    assert first.as_dict()["arguments"]["raw"] == ["-Pfeature=<redacted>"]
    assert second.as_dict()["arguments"]["raw"] == ["-Pfeature=<redacted>"]


def test_launcher_identity_binds_executable_and_wrapper_content_not_just_version(tmp_path: Path) -> None:
    root = tmp_path / "project"
    wrapper_dir = root / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True)
    executable = root / "gradlew"
    executable.write_text("#!/bin/sh\necho one\n", encoding="utf-8")
    executable.chmod(0o755)
    props = wrapper_dir / "gradle-wrapper.properties"
    props.write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/gradle-9.7-bin.zip\n"
        "distributionSha256Sum=abc123\n",
        encoding="utf-8",
    )
    (wrapper_dir / "gradle-wrapper.jar").write_bytes(b"jar-one")
    first = GradleLauncherIdentity.create(
        executable=str(executable), source="wrapper", required_version="9.7",
        effective_version="9.7", project_root=root,
    )
    executable.write_text("#!/bin/sh\necho two\n", encoding="utf-8")
    second = GradleLauncherIdentity.create(
        executable=str(executable), source="wrapper", required_version="9.7",
        effective_version="9.7", project_root=root,
    )
    assert first.identity != second.identity
    assert first.executable_digest != second.executable_digest
    assert first.wrapper_properties_digest
    assert first.wrapper_jar_digest
    assert first.wrapper_distribution_sha256 == "abc123"


def test_property_form_cc_daemon_workers_and_failure_policy_converge_once(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        gradle={
            "args": [
                "-Dorg.gradle.configuration-cache=true",
                "-Dorg.gradle.daemon=true",
                "-Dorg.gradle.workers.max=2",
                "--continue",
            ]
        },
    )
    plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        cpu_count=4, memory_mb=4096,
    )
    assert plan.policy.configuration_cache.effective == "on"
    assert plan.policy.daemon.effective is True
    assert plan.policy.max_workers.effective == 2
    assert plan.policy.failure_policy.effective == "collect"
    rendered = plan.invocations[0].args
    assert "-Dorg.gradle.configuration-cache=true" not in rendered
    assert "-Dorg.gradle.daemon=true" not in rendered
    assert "-Dorg.gradle.workers.max=2" not in rendered
    assert rendered.count("--continue") == 1


def test_vfs_qualification_falls_back_on_shared_android_storage(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"vfs": True})
    plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        gradle_user_home="/storage/emulated/0/gradle-home", cpu_count=4, memory_mb=4096,
    )
    assert plan.context.cache_domain == "shared-android-storage"
    assert plan.policy.vfs_watch.requested is True
    assert plan.policy.vfs_watch.effective is False
    assert plan.policy.vfs_qualification.compatible is False
    assert plan.policy.vfs_qualification.fallback_reason == "gradle-user-home-on-shared-android-storage"
    assert plan.policy.build_cache.effective is False
    assert plan.policy.build_cache.reason == "shared-android-storage-not-canonical-cache-domain"
    assert plan.policy.configuration_cache.effective == "off"
    assert plan.policy.configuration_cache.reason == "shared-android-storage-not-configuration-cache-qualified"
    assert "--no-build-cache" in plan.invocations[0].args
    assert "--no-configuration-cache" in plan.invocations[0].args
    assert "-Dorg.gradle.vfs.watch=false" in plan.invocations[0].args


def test_failure_policy_is_intent_derived_and_rendered_for_direct_gradle(tmp_path: Path) -> None:
    config = _config(tmp_path)
    build = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "assemble"), launcher=_launcher(),
        cpu_count=2, memory_mb=4096,
    )
    validation = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "test", phase="unit-test"), launcher=_launcher(),
        cpu_count=2, memory_mb=4096,
    )
    assert build.policy.failure_policy.effective == "fail-fast"
    assert "--continue" not in build.invocations[0].args
    assert validation.policy.failure_policy.effective == "collect"
    assert "--continue" in validation.invocations[0].args


def test_semantic_environment_values_change_context_identity_without_value_disclosure(tmp_path: Path, monkeypatch) -> None:
    config = _config(tmp_path, gradle={"context_env_keys": ["CUSTOM_BUILD_MODE"]})
    monkeypatch.setenv("CUSTOM_BUILD_MODE", "one-secret-ish-value")
    first = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        cpu_count=2, memory_mb=4096,
    )
    monkeypatch.setenv("CUSTOM_BUILD_MODE", "different-value")
    second = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        cpu_count=2, memory_mb=4096,
    )
    assert first.context.context_digest != second.context.context_digest
    assert first.context.semantic_environment_digest != second.context.semantic_environment_digest
    evidence = first.context.as_dict()
    assert "CUSTOM_BUILD_MODE" in evidence["semantic_environment_keys"]
    assert "one-secret-ish-value" not in str(evidence)


def test_devtool_owned_context_inputs_participate_in_context_and_cache_qualification(tmp_path: Path) -> None:
    config = _config(tmp_path)
    first_request = GradleInvocationRequest(
        caller="env-x03-test", action="build", project_root=str(tmp_path),
        requested_roots=("assemble",), trusted_context_inputs=("trusted-init:one",),
    )
    second_request = GradleInvocationRequest(
        caller="env-x03-test", action="build", project_root=str(tmp_path),
        requested_roots=("assemble",), trusted_context_inputs=("trusted-init:two",),
    )
    first = plan_gradle_invocation(
        config=config, request=first_request, launcher=_launcher(), cpu_count=2, memory_mb=4096,
    )
    second = plan_gradle_invocation(
        config=config, request=second_request, launcher=_launcher(), cpu_count=2, memory_mb=4096,
    )
    assert first.context.context_digest != second.context.context_digest
    assert first.policy.configuration_cache_qualification.qualification_id != second.policy.configuration_cache_qualification.qualification_id
    assert first.context.trusted_context_inputs == ("trusted-init:one",)


def test_aggregate_qualification_becomes_stale_when_semantic_request_changes(tmp_path: Path) -> None:
    alias = {"tasks": ["assemble", "test"], "replacement": "verified"}
    config = _config(tmp_path, gradle={"args": ["-Pmode=one"], "task_aliases": {"validation": alias}})
    request = _request(tmp_path, "assemble", "test")
    initial = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    _qualify_alias(
        alias, config=config, initial=initial, roots=("assemble", "test"), replacement="verified",
    )
    assert plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    ).optimized_roots == ("verified",)

    config.runner_options["gradle"]["args"] = ["-Pmode=two"]
    stale = plan_gradle_invocation(
        config=config, request=request, launcher=_launcher(), cpu_count=4, memory_mb=4096,
    )
    assert stale.optimized_roots == ("assemble", "test")
    assert stale.rejected_aggregates == ("validation",)


def test_auto_resource_profile_is_evidence_driven_and_explicit_profile_is_stable(tmp_path: Path) -> None:
    auto_config = _config(tmp_path / "auto")
    constrained = plan_gradle_invocation(
        config=auto_config,
        request=_request(Path(auto_config.project_root), "check"),
        launcher=_launcher(), cpu_count=4, memory_mb=2048,
    )
    emergency = plan_gradle_invocation(
        config=auto_config,
        request=_request(Path(auto_config.project_root), "check"),
        launcher=_launcher(), cpu_count=4, memory_mb=1024,
    )
    explicit_config = _config(tmp_path / "explicit", gradle={"resource_profile": "balanced"})
    explicit = plan_gradle_invocation(
        config=explicit_config,
        request=_request(Path(explicit_config.project_root), "check"),
        launcher=_launcher(), cpu_count=1, memory_mb=1024,
    )
    assert constrained.policy.resource_profile == "constrained"
    assert emergency.policy.resource_profile == "emergency"
    assert constrained.policy.resource_profile_provenance == "adaptive-host-evidence"
    assert "memory" in constrained.policy.resource_profile_reason
    assert constrained.policy.resource_profile_evidence["memory_mb"] == 2048
    assert explicit.policy.resource_profile == "balanced"
    assert explicit.policy.resource_profile_provenance == "typed-config"


def test_separate_max_workers_value_is_canonicalized_as_managed_policy(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"args": ["--max-workers", "2"]})
    plan = plan_gradle_invocation(
        config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
        cpu_count=8, memory_mb=4096,
    )
    assert plan.policy.max_workers.effective == 2
    assert "--max-workers" in plan.arguments.managed
    assert "2" in plan.arguments.managed
    assert "2" not in plan.arguments.passthrough
    assert "--max-workers=2" in plan.invocations[0].args


def test_configuration_cache_required_fails_closed_on_shared_android_storage(tmp_path: Path) -> None:
    config = _config(tmp_path, gradle={"configuration_cache": "required"})
    with pytest.raises(GradlePlanningError, match="shared Android storage"):
        plan_gradle_invocation(
            config=config, request=_request(tmp_path, "check"), launcher=_launcher(),
            gradle_user_home="/storage/emulated/0/gradle-home", cpu_count=4, memory_mb=4096,
        )


def test_build_logic_identity_overflow_fails_closed_for_cc_and_aggregate_qualification(tmp_path: Path) -> None:
    build_logic = tmp_path / "build-logic" / "src"
    build_logic.mkdir(parents=True)
    for index in range(270):
        (build_logic / f"Plugin{index:03d}.kt").write_text(f"// {index}\n", encoding="utf-8")
    alias = {"tasks": ["assemble", "test"], "replacement": "verified"}
    config = _config(
        tmp_path,
        gradle={"configuration_cache": "auto", "task_aliases": {"validation": alias}},
    )
    plan = plan_gradle_invocation(
        config=config,
        request=_request(tmp_path, "assemble", "test"),
        launcher=_launcher(),
        cpu_count=4,
        memory_mb=4096,
    )

    assert plan.context.build_logic_complete is False
    assert plan.context.build_logic_overflow_count > 0
    assert plan.policy.configuration_cache.effective == "off"
    assert plan.policy.configuration_cache.reason == "build-logic-identity-incomplete"
    assert plan.rejected_aggregates == ("validation",)
