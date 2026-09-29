from __future__ import annotations

import pytest

from devtool.environment_lifecycle import (
    DestinationSnapshot,
    GoTarget,
    GradleAdaptiveHistory,
    GradleFeature,
    GradleFeatureRequest,
    ProviderKind,
    QualificationStatus,
    asdf_snapshot,
    decide_gradle_features,
    destination_go_snapshot,
    destination_python_snapshot,
    go_lifecycle,
    gradle_explain_doctor_projection,
    gradle_remote_receipt_converges,
    native_runner_contribution,
    pyenv_snapshot,
    python_lifecycle,
    qualify_go_target,
    qualify_python_snapshot,
    qualify_version_manager_snapshot,
)


def test_python_destination_snapshot_is_returned_from_destination_and_redacts_secrets():
    snapshot = destination_python_snapshot(
        destination="ssh:tablet",
        executable="/data/data/com.termux/files/usr/bin/python",
        version="3.13.7",
        env={"VIRTUAL_ENV": "/tmp/venv", "API_TOKEN": "secret-value"},
        cache_roots=["/cache/pip"],
    )
    assert snapshot.source == "destination-returned"
    assert snapshot.executable.endswith("/python")
    assert snapshot.identity() == snapshot.identity()
    assert snapshot.safe_env()["API_TOKEN"] == "<redacted>"


def test_python_lifecycle_recursively_owns_sources_locks_and_cache_roots():
    plan = python_lifecycle({"pyproject.toml": "", "poetry.lock": "", "requirements.txt": ""}, project_root="/repo/app")
    explained = plan.explain()
    assert explained["recursive"] is True
    assert "/repo/app/pyproject.toml" in explained["lock_files"]
    assert "/repo/app/poetry.lock" in explained["lock_files"]
    assert explained["cache_roots"] == ["/cache/python"]
    assert "verify-destination-snapshot" in explained["qualification_actions"]


def test_python_qualification_requires_destination_executable_and_version():
    plan = python_lifecycle({"pyproject.toml": ""})
    snapshot = DestinationSnapshot(ProviderKind.PYTHON, "local", None, None)
    result = qualify_python_snapshot(snapshot, plan)
    assert result.status is QualificationStatus.UNQUALIFIED
    assert result.snapshot_identity is None
    assert "destination did not return executable and version" in result.reasons


def test_python_qualified_contribution_is_env_owned_and_exec_consumable():
    plan = python_lifecycle({"uv.lock": ""})
    snapshot = destination_python_snapshot(destination="local", executable="/usr/bin/python", version="3.13")
    result = qualify_python_snapshot(snapshot, plan)
    contribution = native_runner_contribution(snapshot, plan, result)
    assert result.qualified
    assert contribution["authority"] == "ENV"
    assert contribution["qualified"] is True
    assert "environment_identity" in contribution["exec_consumes"]


def test_go_lifecycle_tracks_module_locks_and_cache_ownership():
    plan = go_lifecycle({"go.mod": "module x", "go.sum": "h1"}, project_root="/repo/go")
    assert plan.lock_files == ("/repo/go/go.mod", "/repo/go/go.sum")
    assert plan.owned_environment["GOCACHE"] == "/cache/go-build"
    assert plan.owned_environment["GOMODCACHE"] == "/cache/gomod"


def test_go_cross_target_requires_module_checksum():
    target = GoTarget("linux", "arm64")
    snapshot = destination_go_snapshot(destination="oci:arm64", executable="/usr/bin/go", version="go1.23", target=target)
    result = qualify_go_target(snapshot, target, module_checksum=None)
    assert result.status is QualificationStatus.BLOCKED
    assert "module checksum evidence is required" in result.reasons


def test_go_cgo_target_requires_explicit_c_compiler():
    target = GoTarget("linux", "arm64", cgo_enabled=True)
    snapshot = destination_go_snapshot(destination="oci:arm64", executable="/usr/bin/go", version="go1.23", target=target)
    result = qualify_go_target(snapshot, target, module_checksum="h1:abc")
    assert result.status is QualificationStatus.BLOCKED
    assert "CGO target requires an explicit C compiler" in result.reasons


def test_go_cross_target_qualification_captures_effective_target_identity():
    target = GoTarget("android", "arm64", cgo_enabled=True, cc="aarch64-linux-android-clang")
    snapshot = destination_go_snapshot(destination="termux", executable="/usr/bin/go", version="go1.23", target=target, env={"GOCACHE": "/tmp/go-build", "GOMODCACHE": "/tmp/mod"})
    result = qualify_go_target(snapshot, target, module_checksum="h1:module")
    assert result.status is QualificationStatus.QUALIFIED
    assert result.evidence["target"] == "android/arm64/cgo/aarch64-linux-android-clang"
    assert result.snapshot_identity == snapshot.identity()


def test_asdf_uses_destination_data_dir_and_tool_versions_request():
    snapshot = asdf_snapshot(
        destination="ssh:tablet",
        tool="python",
        current_version="3.13.7",
        executable="/home/u/.asdf/shims/python",
        env={"ASDF_DATA_DIR": "/remote/asdf"},
        project_files={".tool-versions": "python 3.13.7\nnodejs 24.0.0"},
    )
    assert snapshot.data_dir == "/remote/asdf"
    assert snapshot.legacy_version_file == ".tool-versions"
    assert snapshot.requested_version == "3.13.7"
    assert qualify_version_manager_snapshot(snapshot).qualified


def test_asdf_legacy_version_file_mismatch_blocks_qualification():
    snapshot = asdf_snapshot(
        destination="local",
        tool="python",
        current_version="3.12.0",
        executable="/asdf/shims/python",
        project_files={".python-version": "3.13.7"},
    )
    result = qualify_version_manager_snapshot(snapshot)
    assert result.status is QualificationStatus.BLOCKED
    assert "requested version does not match destination-observed version" in result.reasons


def test_asdf_rejects_relative_data_dir():
    with pytest.raises(ValueError):
        asdf_snapshot(
            destination="local",
            tool="go",
            current_version="1.23.0",
            executable="/asdf/shims/go",
            env={"ASDF_DATA_DIR": "relative/asdf"},
        )


def test_pyenv_returns_destination_pyenv_root_and_legacy_file():
    snapshot = pyenv_snapshot(
        destination="ssh:tablet",
        executable="/remote/.pyenv/shims/python",
        current_version="3.13.7",
        env={"PYENV_ROOT": "/remote/.pyenv"},
        project_files={".python-version": "3.13.7"},
    )
    assert snapshot.provider is ProviderKind.PYENV
    assert snapshot.data_dir == "/remote/.pyenv"
    assert snapshot.legacy_version_file == ".python-version"
    assert qualify_version_manager_snapshot(snapshot).qualified


def test_pyenv_env_override_participates_in_requested_version():
    snapshot = pyenv_snapshot(
        destination="local",
        executable="/pyenv/shims/python",
        current_version="3.13.7",
        env={"PYENV_VERSION": "3.13.7"},
        project_files={".python-version": "3.12.1"},
    )
    assert snapshot.requested_version == "3.13.7"
    assert qualify_version_manager_snapshot(snapshot).qualified


def test_pyenv_mismatch_blocks_qualification():
    snapshot = pyenv_snapshot(destination="local", executable="/pyenv/shims/python", current_version="3.12.1", project_files={".python-version": "3.13.7"})
    assert qualify_version_manager_snapshot(snapshot).status is QualificationStatus.BLOCKED


def test_gradle_feature_opt_in_blocks_without_evidence():
    decisions = decide_gradle_features([GradleFeatureRequest(GradleFeature.ISOLATED_PROJECTS, True, evidence={"gradle_version": "9.7"})])
    assert decisions[0].enabled is False
    assert decisions[0].status is QualificationStatus.BLOCKED
    assert "missing opt-in evidence" in decisions[0].reason


def test_gradle_feature_not_requested_remains_disabled_but_qualified():
    decisions = decide_gradle_features([GradleFeatureRequest(GradleFeature.REMOTE_BUILD_CACHE, False)])
    assert decisions[0].enabled is False
    assert decisions[0].status is QualificationStatus.QUALIFIED
    assert decisions[0].reason == "not requested"


def test_gradle_feature_opt_in_requires_cache_identity_fixture_and_version():
    decisions = decide_gradle_features([
        GradleFeatureRequest(
            GradleFeature.PARALLEL_CONFIGURATION_CACHE,
            True,
            evidence={"gradle_version": "9.7", "fixture": "cc-parallel", "cache_identity": "cache-1"},
        )
    ])
    assert decisions[0].enabled is True
    assert decisions[0].status is QualificationStatus.QUALIFIED
    assert decisions[0].evidence_identity


def test_gradle_remote_request_and_receipt_must_converge():
    result = gradle_remote_receipt_converges("request-a", "receipt-b", "ssh:tablet")
    assert result.status is QualificationStatus.BLOCKED
    assert "differs" in result.reasons[0]


def test_gradle_remote_request_receipt_convergence_qualifies_destination():
    result = gradle_remote_receipt_converges("same", "same", "ssh:tablet")
    assert result.qualified
    assert result.snapshot_identity == "same"


def test_gradle_adaptive_history_is_bounded_to_recent_samples():
    history = GradleAdaptiveHistory(tuple({"duration_seconds": index, "profile": f"p{index}"} for index in range(40)))
    summary = history.summarize()
    assert summary["samples"] == 20
    assert summary["last_profile"] == "p39"
    assert summary["bounded"] is True


def test_gradle_explain_doctor_projection_includes_feature_decisions_and_history():
    decisions = decide_gradle_features([
        GradleFeatureRequest(GradleFeature.REMOTE_BUILD_CACHE, True, {"gradle_version": "9.7", "fixture": "remote", "cache_identity": "r1"})
    ])
    projection = gradle_explain_doctor_projection(
        plan_identity="plan-1",
        decisions=decisions,
        history=GradleAdaptiveHistory(({"duration_seconds": 2.0, "profile": "balanced"},)),
    )
    assert projection["plan_identity"] == "plan-1"
    assert projection["features"][0]["enabled"] is True
    assert projection["adaptive_history"]["median_seconds"] == 2.0


def test_snapshot_identity_is_secret_safe_and_stable():
    a = destination_python_snapshot(destination="local", executable="/usr/bin/python", version="3.13", env={"PASSWORD": "one"})
    b = destination_python_snapshot(destination="local", executable="/usr/bin/python", version="3.13", env={"PASSWORD": "two"})
    assert a.safe_env()["PASSWORD"] == "<redacted>"
    assert a.identity() == b.identity()


def test_provider_contribution_does_not_transfer_authority_to_exec():
    plan = go_lifecycle({"go.mod": "module x", "go.sum": "h1"})
    target = GoTarget("linux", "amd64")
    snapshot = destination_go_snapshot(destination="local", executable="/usr/bin/go", version="go1.23", target=target)
    result = qualify_go_target(snapshot, target, module_checksum="h1:sum")
    contribution = native_runner_contribution(snapshot, plan, result)
    assert contribution["authority"] == "ENV"
    assert contribution["provider"] == "go"
    assert contribution["qualified"] is True
    assert "cache_ownership" in contribution["exec_consumes"]


def test_destination_paths_must_be_absolute_for_provider_snapshots():
    with pytest.raises(ValueError):
        destination_python_snapshot(destination="local", executable="bin/python", version="3.13")


def test_go_snapshot_records_cache_roots_from_destination_env():
    target = GoTarget("linux", "arm64")
    snapshot = destination_go_snapshot(
        destination="remote",
        executable="/usr/bin/go",
        version="go1.23",
        target=target,
        env={"GOCACHE": "/remote/cache/build", "GOMODCACHE": "/remote/cache/mod"},
    )
    assert snapshot.cache_roots == ("/remote/cache/build", "/remote/cache/mod")


def test_asdf_tool_versions_without_matching_tool_does_not_fake_request():
    snapshot = asdf_snapshot(
        destination="local",
        tool="go",
        current_version="1.23.0",
        executable="/asdf/shims/go",
        project_files={".tool-versions": "python 3.13.7"},
    )
    assert snapshot.requested_version is None
    assert snapshot.legacy_version_file is None
    assert qualify_version_manager_snapshot(snapshot).qualified


def test_lifecycle_plan_explain_is_projection_not_mutable_state_store():
    plan = python_lifecycle({"pyproject.toml": ""})
    explanation = plan.explain()
    explanation["lock_files"].append("/evil")
    assert "/evil" not in plan.explain()["lock_files"]


def test_gradle_projection_keeps_benchmark_scope_bounded():
    projection = gradle_explain_doctor_projection(plan_identity="p", decisions=(), history=GradleAdaptiveHistory(()))
    assert projection["benchmark_scope"] == "bounded-history"
    assert projection["adaptive_history"]["samples"] == 0
