from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from devtool.environment_qualification import assert_environment_overlay_safe
from devtool.gradle_planning import GradleInvocationRequest
from devtool.gradle_takeover import (
    GradleDestination,
    GradleIntent,
    GradleOrigin,
    GradleTakeoverError,
    TakeoverMode,
    broker_gradle_invocation,
    disable_takeover,
    enable_takeover,
    execute_real_gradle,
    resolve_real_launcher,
    resolve_takeover_config,
    shell_adapter_path,
    shell_bin_dir,
    strip_takeover_shims,
    takeover_coverage,
    takeover_doctor,
    takeover_explain,
    takeover_status,
    validate_internal_capability,
    write_shell_assets,
)
from devtool.models import ProjectConfig


def _config(root: Path, takeover: dict | None = None, *, execution_environment: str = "host") -> ProjectConfig:
    return ProjectConfig(
        repo_root=root,
        kind="gradle",
        runner="gradle",
        project_root=str(root),
        execution_environment=execution_environment,
        runner_options={"gradle": {"takeover": takeover or {}}},
    )


def _exe(path: Path, text: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _wrapper(root: Path, version: str = "9.7") -> Path:
    wrapper = _exe(root / "gradlew")
    props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    props.parent.mkdir(parents=True, exist_ok=True)
    props.write_text(
        f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n",
        encoding="utf-8",
    )
    (props.parent / "gradle-wrapper.jar").write_bytes(b"wrapper-jar")
    return wrapper


def test_takeover_is_disabled_by_default_and_identity_significant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    base = resolve_takeover_config(_config(tmp_path))
    assert base.enabled is False
    assert base.mode is TakeoverMode.OFF

    manage = resolve_takeover_config(_config(tmp_path, {"enabled": True, "mode": "manage", "scope": "devtool"}))
    strict = resolve_takeover_config(_config(tmp_path, {"enabled": True, "mode": "strict", "scope": "both"}))
    assert manage.enabled and strict.enabled
    assert manage.identity_digest != strict.identity_digest
    assert "capability" not in json.dumps(strict.as_dict()).lower()


def test_real_launcher_strips_takeover_shim_before_path_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    shim = tmp_path / "shim"
    real = tmp_path / "real"
    _exe(shim / "gradle")
    (shim / ".devtool-gradle-takeover-shim").write_text("test\n", encoding="utf-8")
    real_gradle = _exe(real / "gradle")
    env = {"PATH": os.pathsep.join((str(shim), str(real)))}
    cfg = _config(tmp_path)
    destination = GradleDestination("host", "dest", local=True, path_value=env["PATH"], cwd=str(tmp_path))
    result = resolve_real_launcher(cfg, requested="system", destination=destination, env=env)
    assert Path(result.executable) == real_gradle.resolve()
    assert str(shim.resolve()) in result.stripped_shim_dirs
    assert Path(result.executable).parent != shim.resolve()


def test_wrapper_identity_preserves_realpath_distribution_and_content_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    wrapper = _wrapper(tmp_path, "9.7")
    cfg = _config(tmp_path)
    result = resolve_real_launcher(cfg, requested="wrapper")
    payload = result.launcher.as_dict()
    assert Path(payload["executable_realpath"]) == wrapper.resolve()
    assert payload["required_version"] == "9.7"
    assert "gradle-9.7-bin.zip" in payload["wrapper_distribution"]
    assert payload["executable_digest"]
    assert payload["wrapper_properties_digest"]
    assert payload["wrapper_jar_digest"]
    assert payload["identity"]


def test_remote_system_resolution_is_destination_aware_and_never_leaks_host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    host_gradle = _exe(tmp_path / "host-bin" / "gradle")
    cfg = _config(tmp_path, execution_environment="ssh")
    destination = GradleDestination("ssh", "remote-1", local=False, path_value=str(host_gradle.parent), cwd="~/repo")
    result = resolve_real_launcher(cfg, requested="system", destination=destination)
    assert result.deferred is True
    assert result.executable == "gradle"
    assert str(host_gradle) not in json.dumps(result.as_dict())
    assert result.launcher.identity



def test_chroot_and_proot_resolution_do_not_reuse_host_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    host_gradle = _exe(tmp_path / "host-only" / "gradle")
    for kind in ("chroot", "proot-distro"):
        cfg = _config(tmp_path, execution_environment=kind)
        destination = GradleDestination(kind, f"{kind}-dest", local=False, path_value=str(host_gradle.parent), cwd="/repo")
        result = resolve_real_launcher(cfg, requested="system", destination=destination)
        assert result.deferred is True
        assert result.executable == "gradle"
        assert str(host_gradle) not in json.dumps(result.as_dict())


def test_scope_blocks_shell_origin_when_only_devtool_takeover_is_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _wrapper(tmp_path)
    cfg = _config(tmp_path, {"enabled": True, "mode": "manage", "scope": "devtool"})
    request = GradleInvocationRequest(caller="fixture", action="build", project_root=str(tmp_path), requested_roots=("help",))
    decision = broker_gradle_invocation(cfg, request, origin=GradleOrigin.SHELL, intent=GradleIntent.WORK, requested_launcher="wrapper")
    assert decision.plan is None
    assert decision.receipt.bypass_reason == "origin-outside-takeover-scope"

def test_repository_configuration_cannot_forge_takeover_internal_capability() -> None:
    with pytest.raises(ValueError, match="protected Devtool environment variable"):
        assert_environment_overlay_safe(
            {"DEVTOOL_GRADLE_TAKEOVER_CAPABILITY": "forged"},
            source="fixture",
        )


def test_work_probe_control_intents_stay_distinct_and_work_reuses_canonical_planner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _wrapper(tmp_path)
    cfg = _config(tmp_path, {"enabled": True, "mode": "manage", "scope": "devtool"})
    request = GradleInvocationRequest(
        caller="env-x05", action="build", project_root=str(tmp_path), requested_roots=("help",), raw_args=("--stacktrace",),
    )
    work = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.WORK, requested_launcher="wrapper")
    probe = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.PROBE, requested_launcher="wrapper")
    control = broker_gradle_invocation(cfg, request, origin=GradleOrigin.DEVTOOL, intent=GradleIntent.CONTROL, requested_launcher="wrapper")
    assert work.plan is not None
    assert work.receipt.plan_digest == work.plan.plan_digest
    assert work.receipt.canonicalized is True
    assert probe.plan is None and probe.receipt.bypass_reason == "typed-probe-bypass"
    assert control.plan is None and control.receipt.bypass_reason == "typed-control-bypass"
    assert work.receipt.receipt_id != probe.receipt.receipt_id != control.receipt.receipt_id


def test_disabled_takeover_preserves_existing_behavior_without_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _wrapper(tmp_path)
    cfg = _config(tmp_path)
    request = GradleInvocationRequest(caller="fixture", action="build", project_root=str(tmp_path), requested_roots=("help",))
    decision = broker_gradle_invocation(cfg, request, requested_launcher="wrapper")
    assert decision.plan is None
    assert decision.receipt.canonicalized is False
    assert decision.receipt.bypass_reason == "takeover-disabled"


def test_shell_lifecycle_is_explicit_reversible_and_truthful_about_coverage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    real = _exe(tmp_path / "real" / "gradle")
    monkeypatch.setenv("PATH", str(real.parent))
    cfg = _config(tmp_path)

    enabled = enable_takeover(cfg, mode="strict", scope="both")
    assert enabled["state"]["enabled"] is True
    assert (shell_bin_dir() / "gradle").is_file()
    assert shell_adapter_path().is_file()
    effective = resolve_takeover_config(cfg)
    coverage = takeover_coverage(effective)
    assert coverage.bare_gradle_shell is True
    assert coverage.direct_project_wrapper_shell is False
    assert coverage.arbitrary_nested_processes is False

    active_env = {"PATH": os.pathsep.join((str(shell_bin_dir()), str(real.parent)))}
    status = takeover_status(cfg, env=active_env)
    assert status["shell"]["shim_active_in_path"] is True
    assert Path(status["real_launcher"]["executable"]) == real.resolve()
    assert takeover_doctor(cfg, env=active_env)["ok"] is True
    explain = takeover_explain(cfg, env=active_env)
    assert explain["truth"]["planner_authority"] == "devtool.gradle_planning.plan_gradle_invocation"
    assert explain["truth"]["direct_dot_gradlew_intercepted"] is False

    disabled = disable_takeover(cfg)
    assert disabled["state"]["enabled"] is False
    assert disabled["cleanup_authority"] == "LIFE"
    assert Path(str(disabled["cleanup_receipt_path"])).is_file()
    assert not (shell_bin_dir() / "gradle").exists()
    assert resolve_takeover_config(cfg).enabled is False



def test_disable_takeover_respects_life_protection_for_shell_assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool.core_runtime import private_run_root
    from devtool.run_store import AttachmentDescriptor, RunCatalog

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    real = _exe(tmp_path / "real" / "gradle")
    monkeypatch.setenv("PATH", str(real.parent))
    cfg = _config(tmp_path)
    enable_takeover(cfg, mode="strict", scope="both")
    shim = shell_bin_dir() / "gradle"
    assert shim.is_file()
    catalog = RunCatalog.open(private_run_root(tmp_path))
    catalog.add_run({"schema_version": 3, "run_id": "run-shell", "status": "running"})
    catalog.add_attachment(AttachmentDescriptor.from_file(run_id="run-shell", path=shim, kind="gradle-shim", privacy="private"))
    catalog.save()

    disabled = disable_takeover(cfg)

    assert shim.is_file(), "RUN-referenced shell shim must not be unlinked by disable cleanup"
    assert all(Path(path).name != "gradle" for path in disabled["removed"])
    assert any(Path(item["path"]).name == "gradle" for item in disabled["protected"])
    assert disabled["cleanup_authority"] == "LIFE"
    assert Path(str(disabled["cleanup_receipt_path"])).is_file()


def test_local_system_launcher_identity_is_destination_significant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    real = _exe(tmp_path / "bin" / "gradle")
    cfg = _config(tmp_path)
    first = GradleDestination("host", "dest-a", local=True, path_value=str(real.parent), cwd=str(tmp_path))
    second = GradleDestination("host", "dest-b", local=True, path_value=str(real.parent), cwd=str(tmp_path))
    a = resolve_real_launcher(cfg, requested="system", destination=first)
    b = resolve_real_launcher(cfg, requested="system", destination=second)
    assert a.launcher.executable_digest == b.launcher.executable_digest
    assert a.launcher.identity != b.launcher.identity


def test_disabling_one_repository_does_not_remove_shared_shell_assets_needed_by_another(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    first_root = tmp_path / "one"; second_root = tmp_path / "two"
    first_root.mkdir(); second_root.mkdir()
    first = _config(first_root); second = _config(second_root)
    enable_takeover(first, mode="manage", scope="both")
    enable_takeover(second, mode="manage", scope="both")
    result = disable_takeover(first)
    assert result["shell_assets_retained_for_other_repositories"] is True
    assert (shell_bin_dir() / "gradle").is_file()
    result2 = disable_takeover(second)
    assert result2["shell_assets_retained_for_other_repositories"] is False
    assert not (shell_bin_dir() / "gradle").exists()

def test_generated_shim_routes_through_takeover_without_claiming_dot_gradlew(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assets = write_shell_assets()
    shim = Path(assets["shim"]).read_text(encoding="utf-8")
    adapter = Path(assets["adapter"]).read_text(encoding="utf-8")
    assert "gradle takeover intercept" in shim
    assert "--origin shell" in shim
    assert "PATH=" in adapter
    token = Path(tmp_path / "state" / "devtool" / "gradle-takeover" / "internal-capability").read_text().strip()
    assert validate_internal_capability(token)
    assert not validate_internal_capability("forged")


def test_execute_real_gradle_escapes_shim_and_preserves_underlying_exit_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    shim = tmp_path / "shim"
    real = tmp_path / "real"
    _exe(shim / "gradle", "#!/bin/sh\nexit 99\n")
    (shim / ".devtool-gradle-takeover-shim").write_text("test\n", encoding="utf-8")
    _exe(real / "gradle", "#!/bin/sh\nexit 7\n")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join((str(shim), str(real)))
    cfg = _config(tmp_path)
    assert execute_real_gradle(cfg, ["--version"], requested_launcher="system", env=env) == 7


def test_cli_takeover_status_and_explain_surface_canonical_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _exe(tmp_path / "bin" / "gradle")
    monkeypatch.setenv("PATH", os.pathsep.join((str(tmp_path / "bin"), os.environ.get("PATH", ""))))
    (tmp_path / ".devtool.toml").write_text(
        "[project]\nrunner = \"gradle\"\nroot = \".\"\n\n[gradle.takeover]\nenabled = false\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    status = subprocess.run(
        [sys.executable, "-m", "devtool", "-r", str(tmp_path), "gradle", "takeover", "status", "--json"],
        text=True, capture_output=True, env=env, check=False,
    )
    assert status.returncode == 0, status.stdout + status.stderr
    envelope = json.loads(status.stdout)
    payload = envelope.get("data", envelope)
    assert payload["config"]["enabled"] is False
    assert payload["coverage"]["direct_project_wrapper_shell"] is False

    explain = subprocess.run(
        [sys.executable, "-m", "devtool", "-r", str(tmp_path), "gradle", "takeover", "explain", "--json"],
        text=True, capture_output=True, env=env, check=False,
    )
    assert explain.returncode == 0, explain.stdout + explain.stderr
    explain_envelope = json.loads(explain.stdout)
    explanation = explain_envelope.get("data", explain_envelope)
    assert explanation["truth"]["planner_authority"] == "devtool.gradle_planning.plan_gradle_invocation"
    assert explanation["truth"]["nested_script_intercepted"] is False


def test_strip_takeover_shims_keeps_non_shim_path_entries(tmp_path: Path) -> None:
    shim = tmp_path / "shim"
    normal = tmp_path / "normal"
    shim.mkdir(); normal.mkdir()
    (shim / ".devtool-gradle-takeover-shim").write_text("x")
    result, stripped = strip_takeover_shims(os.pathsep.join((str(shim), str(normal))))
    assert result == str(normal)
    assert stripped == (str(shim.resolve()),)
