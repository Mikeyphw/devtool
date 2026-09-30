from __future__ import annotations

import textwrap
from pathlib import Path

from devtool.android_integration import android_builder_arguments_for_tasks, android_gradle_invocation_plan
from devtool.config import load_project_config
from devtool.project_runners import JvmGradleRunner
from devtool.test_declarations import command_specs
from devtool.workflow_runtime import build_workflow_specs


def _repo(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".devtool.toml").write_text(textwrap.dedent(body), encoding="utf-8")
    (tmp_path / "settings.gradle").write_text("rootProject.name='env-x03'\n", encoding="utf-8")
    return tmp_path


def test_jvm_cli_workflow_and_test_declaration_share_canonical_plan_shape(tmp_path: Path) -> None:
    gradle_exe = tmp_path / "fake-gradle"
    gradle_exe.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf \"Gradle 9.7\\n\"; exit 0; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    gradle_exe.chmod(0o755)
    root = _repo(
        tmp_path,
        f"""
        [project]
        runner = "gradle"
        default_target = "app"

        [targets.app]
        runner = "gradle"
        root = "."

        [gradle]
        command = "{gradle_exe}"
        version = "9.7"
        structured_events = false
        max_workers = 2

        [jobs.workflow_gradle]
        runner = "gradle"
        gradle_tasks = ["check"]

        [targets.app.workflows]
        verify = ["job:workflow_gradle"]

        [[test]]
        id = "declared-gradle"
        runner = "gradle"
        task = "test"
        """,
    )
    config = load_project_config(str(root), None)

    direct = JvmGradleRunner().build_task_specs(config, ["assemble"], name="direct", action="build")[0]
    workflow = build_workflow_specs(config, "verify")[0]
    declared = command_specs(config, ["declared-gradle"])[0]

    assert direct.metadata["gradle_execution_plan"]["requested_roots"] == ["assemble"]
    assert workflow.metadata["gradle_execution_plan"]["requested_roots"] == ["check"]
    assert declared.metadata["gradle_execution_plan"]["requested_roots"] == ["test"]
    assert workflow.metadata["gradle_semantic_request"] is True
    assert declared.metadata["gradle_semantic_request"] is True
    assert config.jobs["workflow_gradle"].command == ()
    assert config.jobs["workflow_gradle"].gradle_tasks == ("check",)
    assert config.tests["declared-gradle"].command == ()
    assert config.tests["declared-gradle"].gradle_tasks == ("test",)
    for spec in (direct, workflow, declared):
        policy = spec.metadata["gradle_execution_plan"]["policy"]
        assert ("--build-cache" in spec.argv) is bool(policy["build_cache"]["effective"])
        assert ("--daemon" in spec.argv) is bool(policy["daemon"]["effective"])
        expected_vfs = f"-Dorg.gradle.vfs.watch={'true' if policy['vfs_watch']['effective'] else 'false'}"
        assert expected_vfs in spec.argv



def test_test_selection_changes_semantic_gradle_plan_identity(tmp_path: Path) -> None:
    gradle_exe = tmp_path / "fake-gradle-selection"
    gradle_exe.write_text(
        "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then printf \"Gradle 9.7\\n\"; fi\nexit 0\n",
        encoding="utf-8",
    )
    gradle_exe.chmod(0o755)
    root = _repo(
        tmp_path,
        f"""
        [project]
        runner = "gradle"

        [gradle]
        command = "{gradle_exe}"
        version = "9.7"

        [test_selection]
        classes = ["ExampleOne"]
        """,
    )
    first_config = load_project_config(str(root), None)
    first = JvmGradleRunner().build_task_specs(first_config, ["test"], name="test", action="test")[0]

    text = (root / ".devtool.toml").read_text(encoding="utf-8").replace("ExampleOne", "ExampleTwo")
    (root / ".devtool.toml").write_text(text, encoding="utf-8")
    second_config = load_project_config(str(root), None)
    second = JvmGradleRunner().build_task_specs(second_config, ["test"], name="test", action="test")[0]

    first_id = first.metadata["gradle_execution_plan"]["request"]["selection_identity"]
    second_id = second.metadata["gradle_execution_plan"]["request"]["selection_identity"]
    assert first_id
    assert second_id
    assert first_id != second_id
    assert first.metadata["gradle_execution_plan"]["plan_digest"] != second.metadata["gradle_execution_plan"]["plan_digest"]

def test_android_balanced_validation_uses_same_policy_without_legacy_constrained_mode(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        """
        [project]
        runner = "android"

        [android]
        platform = "none"

        [gradle]
        command = "gradle"
        version = "9.7"
        structured_events = false
        configuration_cache = "on"
        build_cache = true

        [validation]
        android_safe = true
        resource_profile = "balanced"
        max_workers = 2
        """,
    )
    config = load_project_config(str(root), None)
    component, plan = android_gradle_invocation_plan(
        config,
        [":app:assembleDebug", ":app:testDebugUnitTest"],
        caller="env-x03-test",
        action="validation",
        validation=True,
        validation_phase="test",
    )
    argv = android_builder_arguments_for_tasks(
        config,
        [":app:assembleDebug", ":app:testDebugUnitTest"],
        scan_artifacts=False,
        validation=True,
        validation_phase="test",
        component_plan=component,
        canonical_plan=plan,
        physical_roots=plan.invocations[0].roots,
    )

    assert "--constrained" not in argv
    assert "--build-cache" in argv
    assert "--no-daemon" not in argv
    marker = argv.index("--")
    gradle_argv = argv[marker + 1 :]
    assert "--configuration-cache" in gradle_argv
    assert "-Dorg.gradle.vfs.watch=true" in gradle_argv
    assert plan.policy.resource_profile == "balanced"
    assert plan.policy.daemon.effective is True
    assert plan.policy.vfs_watch.effective is True


def test_inline_artifact_gradle_task_lowers_through_canonical_planner(tmp_path: Path, monkeypatch) -> None:
    from devtool import artifact_tests as artifact_tests_module

    gradle_exe = tmp_path / "fake-gradle-artifact"
    gradle_exe.write_text(
        "#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then printf \"Gradle 9.7\\n\"; fi\nexit 0\n",
        encoding="utf-8",
    )
    gradle_exe.chmod(0o755)
    root = _repo(
        tmp_path,
        f"""
        [project]
        runner = "gradle"

        [gradle]
        command = "{gradle_exe}"
        version = "9.7"
        """,
    )
    captured = []

    def fake_run_specs(config, specs, **kwargs):
        captured.extend(specs)
        return [], ""

    monkeypatch.setattr(artifact_tests_module, "_run_specs", fake_run_specs)
    artifact_tests_module.run_artifact_tests(
        root,
        {"validation": {"tests": [{"id": "artifact-gradle", "runner": "gradle", "task": "check"}]}},
    )

    assert len(captured) == 1
    spec = captured[0]
    assert spec.metadata["gradle_semantic_request"] is True
    assert spec.metadata["artifact_gradle_task"] == "check"
    assert spec.metadata["gradle_execution_plan"]["requested_roots"] == ["check"]
    assert spec.argv[0] == str(gradle_exe)
    assert "--build-cache" in spec.argv


def test_jvm_artifact_producer_scope_is_forwarded_to_telemetry(tmp_path: Path) -> None:
    gradle_exe = tmp_path / "fake-gradle-producer"
    gradle_exe.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then printf "Gradle 9.7\\n"; fi\nexit 0\n',
        encoding="utf-8",
    )
    gradle_exe.chmod(0o755)
    root = _repo(
        tmp_path,
        f'''\n        [project]\n        runner = "gradle"\n\n        [gradle]\n        command = "{gradle_exe}"\n        version = "9.7"\n        telemetry_level = "standard"\n        configuration_cache = "off"\n        ''',
    )
    config = load_project_config(str(root), None)
    spec = JvmGradleRunner().build_task_specs(config, ["assemble"], name="build", action="build")[0]
    assert spec.env["DEVTOOL_GRADLE_ARTIFACT_PRODUCERS"] == "assemble"


def test_android_instrumentation_cli_build_is_canonical_and_device_orchestrator_skips_build(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace
    from devtool.android import cli as android_cli
    from devtool.android import instrumentation as instrumentation_module
    from devtool.models import CommandSpec, OperationResult, ProjectConfig
    from devtool.project_runners import AndroidGradleRunner
    from devtool import runners as runners_module

    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="android", runner="android")
    planned = CommandSpec(
        "instrumentation-build",
        ["gradle", "assembleDebug"],
        metadata={"gradle_execution_plan": {"plan_digest": "canonical"}},
    )
    monkeypatch.setattr(AndroidGradleRunner, "build_specs", lambda self, cfg, action: [planned, CommandSpec("device", ["device"])])
    observed: dict[str, object] = {}

    def fake_run_specs(cfg, specs, *, verbose, **kwargs):
        observed["specs"] = specs
        return [OperationResult("instrumentation-build", ["gradle"], 0, 0.1, "", [])], ""

    monkeypatch.setattr(runners_module, "run_specs", fake_run_specs)
    monkeypatch.setattr(
        instrumentation_module,
        "request_from_config",
        lambda cfg, **kwargs: SimpleNamespace(skip_build=kwargs["skip_build"]),
    )
    fake_orchestrator = SimpleNamespace(
        run=lambda request: SimpleNamespace(as_dict=lambda: {"skip_build": request.skip_build})
    )
    monkeypatch.setattr(
        instrumentation_module.AndroidInstrumentationOrchestrator,
        "from_config",
        lambda cfg, **kwargs: fake_orchestrator,
    )

    payload = android_cli.android_instrumentation_payload(config, skip_build=False)
    assert observed["specs"] == [planned]
    assert payload["result"]["skip_build"] is True
