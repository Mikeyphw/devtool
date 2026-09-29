from __future__ import annotations
from pathlib import Path
from devtool.gradle_planning import GradleInvocationRequest, GradleLauncherIdentity, plan_gradle_invocation
from devtool.models import ProjectConfig


def _config(root: Path, **gradle) -> ProjectConfig:
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.gradle").write_text("rootProject.name='gate'\n", encoding="utf-8")
    return ProjectConfig(repo_root=root, project_root=str(root), kind="gradle", runner="gradle", execution_environment="native-termux", runner_options={"gradle": gradle})


def _launcher() -> GradleLauncherIdentity:
    return GradleLauncherIdentity.create(executable="gradle", source="system", effective_version="9.7", required_version="9.7")


def test_env_g2_balanced_policy_retains_safe_reuse_layers(tmp_path: Path) -> None:
    config = _config(tmp_path, resource_profile="balanced", configuration_cache="required", telemetry_level="standard", build_cache=True, daemon=True, vfs=True, parallel=True, max_workers=2)
    plan = plan_gradle_invocation(config=config, request=GradleInvocationRequest(caller="env-g2", action="gate", project_root=str(tmp_path), requested_roots=("check",)), launcher=_launcher(), cpu_count=4, memory_mb=4096)
    p = plan.policy
    assert p.resource_profile == "balanced"
    assert p.configuration_cache.requested == "required" and p.configuration_cache.effective == "on"
    assert p.configuration_cache_qualification.compatible is True
    assert p.telemetry_level.effective == "standard"
    assert p.build_cache.effective is True and p.daemon.effective is True and p.vfs_watch.effective is True
    assert p.parallel.effective is True and p.max_workers.effective == 2


def test_env_g2_barriers_preserve_semantic_order(tmp_path: Path) -> None:
    config = _config(tmp_path)
    plan = plan_gradle_invocation(config=config, request=GradleInvocationRequest(caller="env-g2", action="gate", project_root=str(tmp_path), requested_roots=("clean", "assemble", "check", "publishBundle", "test")), launcher=_launcher(), cpu_count=4, memory_mb=4096)
    assert [item.roots for item in plan.invocations] == [("clean",), ("assemble", "check"), ("publishBundle",), ("test",)]
    assert [[b.kind for b in item.barriers] for item in plan.invocations] == [["clean"], [], ["effect"], []]


def test_env_g2_heap_clamp_records_reason_instead_of_faking_request(tmp_path: Path) -> None:
    config = _config(tmp_path, resource_profile="balanced", heap_mb=4096, host_safe_heap_ceiling_mb=1536)
    plan = plan_gradle_invocation(config=config, request=GradleInvocationRequest(caller="env-g2", action="gate", project_root=str(tmp_path), requested_roots=("check",)), launcher=_launcher(), cpu_count=4, memory_mb=4096)
    assert plan.policy.heap.requested_mb == 4096
    assert plan.policy.heap.effective_mb == 1536
    assert "clamped" in plan.policy.heap.reason


def test_authoritative_gate_rejects_construction_gradle_override(tmp_path: Path, monkeypatch) -> None:
    from devtool.env_g2_gradle_gate import EnvG2GateError, _gradle_executable
    fake = tmp_path / "gradle"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("DEVTOOL_ENV_G2_GRADLE", str(fake))
    monkeypatch.delenv("DEVTOOL_ENV_G2_ALLOW_TEST_GRADLE", raising=False)
    try:
        _gradle_executable()
    except EnvG2GateError as exc:
        assert "forbidden for authoritative ENV-G2 validation" in str(exc)
    else:
        raise AssertionError("authoritative ENV-G2 accepted a construction-only Gradle override")
