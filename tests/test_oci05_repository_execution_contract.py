from __future__ import annotations

import hashlib
import io
from pathlib import Path
import tarfile

import pytest

from devtool.execution_providers import SSH_NATIVE, SSH_PROOT_OCI, execution_provider
from devtool.oci_execution import _inspect_script, _install_receipt_script
from devtool.oci_image import canonical_android_oci_definition
from devtool.oci_workspace import OciWorkspaceBind, OciWorkspacePlan
from devtool.runners import _ssh_execution_provenance
from devtool.ssh_tmux_runner import (
    SSHRunnerConfig,
    _build_remote_phase_script,
    _cleanup_oci_construction_state,
    _oci_rewrite_runtime_argv,
    _oci_wrap_guest_command,
    _stage_support_files_for_spec,
    resolve_ssh_config,
)


def _plan() -> OciWorkspacePlan:
    return OciWorkspacePlan(
        profile="tablet",
        workspace_id="repo-deadbeef",
        remote_workspace="$HOME/.local/share/devtool/workspaces/repo-deadbeef",
        remote_cache_root="$HOME/.cache/devtool/oci/repo-deadbeef",
        source_sync_id="sync-1",
        working_directory="/workspace",
        binds=(
            OciWorkspaceBind("$HOME/.local/share/devtool/workspaces/repo-deadbeef", "/workspace", "workspace"),
            OciWorkspaceBind("$HOME/.cache/devtool/oci/repo-deadbeef", "/var/cache/devtool", "persistent-cache"),
        ),
        managed_environment={"GRADLE_USER_HOME": "/var/cache/devtool/gradle"},
        forwarded_environment={},
    )


def test_oci05_activates_only_the_proot_oci_remote_provider() -> None:
    assert execution_provider(SSH_PROOT_OCI, remote=True).executable is True
    assert execution_provider(SSH_NATIVE, remote=True).executable is True
    assert execution_provider(SSH_NATIVE, remote=True).capabilities.containerized is False
    assert execution_provider(SSH_PROOT_OCI, remote=True).capabilities.containerized is True


def test_oci05_profile_defaults_force_command_mode_and_v2_image(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        '''[ssh]\nprofile = "tablet"\n\n[ssh.hosts.tablet]\nhost = "tablet"\n\n[ssh.hosts.tablet.workspace]\nmode = "managed"\n\n[ssh.hosts.tablet.execution]\nprovider = "ssh-proot-oci"\n''',
        encoding="utf-8",
    )
    cfg = resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    assert cfg.execution_provider == SSH_PROOT_OCI
    assert cfg.mode == "commands"
    assert cfg.oci_image == "devtool/android-build:arm64-api36-v2"
    assert cfg.oci_container == "devtool-android-api36"
    assert cfg.oci_architecture == "aarch64"
    assert cfg.oci_pull_policy == "if-missing"
    assert cfg.oci_digest_policy == "record"


def test_oci05_profile_rejects_unsupported_pull_and_digest_policies(tmp_path: Path) -> None:
    template = '''[ssh]\nprofile = "tablet"\n\n[ssh.hosts.tablet]\nhost = "tablet"\n\n[ssh.hosts.tablet.workspace]\nmode = "managed"\n\n[ssh.hosts.tablet.execution]\nprovider = "ssh-proot-oci"\n\n[ssh.hosts.tablet.oci]\npull_policy = "{pull}"\ndigest_policy = "{digest}"\n'''
    (tmp_path / ".devtool.toml").write_text(template.format(pull="always", digest="record"), encoding="utf-8")
    with pytest.raises(ValueError, match="pull policy"):
        resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    (tmp_path / ".devtool.toml").write_text(template.format(pull="never", digest="ignore"), encoding="utf-8")
    with pytest.raises(ValueError, match="digest policy"):
        resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})


def test_oci05_v2_image_embeds_exact_standard_runtime_bytes() -> None:
    definition = canonical_android_oci_definition()
    assert definition.contract["definition_id"] == "android-arm64-api36-v2"
    assert definition.reference == "devtool/android-build:arm64-api36-v2"
    expected = {
        "build-apk": "db73ed326d4d5a774dcb079ea64e0df7325a2d2df43b2f2484216de6418126e8",
        "termux_toolchains.py": "46bff66338eee52d297b90cbfe6773e979d9a27d0b3c93f7816a139cf0e255fe",
        "devtool-events.init.gradle": "9e0a0aa733ffeb41ce76e4b51b5bda4eadfcf52d01d49b3faf1dd4f33b2e72d8",
        "devtool-events-full.init.gradle": "48529c7e3b7a2a4409aacbf102ffb47048165cd4badf3d73ba82e2b309c05788",
    }
    with tarfile.open(fileobj=io.BytesIO(definition.context_bytes), mode="r:") as archive:
        for name, digest in expected.items():
            payload = archive.extractfile(name).read()
            assert hashlib.sha256(payload).hexdigest() == digest


def test_oci05_runtime_rewrite_uses_image_owned_android_runtime(tmp_path: Path) -> None:
    builder = tmp_path / "build-apk"
    builder.write_text("#!/bin/sh\n", encoding="utf-8")
    events = tmp_path / "events.init.gradle"
    canonical_events = Path("devtool/resources/gradle/devtool-events.init.gradle").read_bytes()
    events.write_bytes(canonical_events)
    metadata = {"android_builder": str(builder)}
    argv = [
        "python", str(builder), "--init-script", str(events), "--java-home", "/host/java",
        "--sdk-root=/host/sdk", "--auto-sdk", "--force-setup", "--", "assembleDebug",
    ]
    rewritten = _oci_rewrite_runtime_argv(argv, metadata)
    assert "/usr/local/lib/devtool/build-apk" in rewritten
    assert "/usr/local/lib/devtool/devtool-events.init.gradle" in rewritten
    assert "--auto-sdk" not in rewritten and "--force-setup" not in rewritten
    assert "--skip-sdk-install" in rewritten
    joined = "\n".join(rewritten)
    for value in (
        "/usr/lib/jvm/java-21-openjdk-arm64", "/opt/android-sdk", "36.0.0", "29.0.14206865",
        "never", "deny", "wrapper",
    ):
        assert value in joined
    assert "android_builder" not in metadata
    assert metadata["oci_embedded_android_runtime"] is True
    assert metadata["oci_embedded_gradle_events"] is True


def test_oci05_staging_skips_embedded_runtime_but_keeps_project_helper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    helper = tmp_path / "helper.py"
    helper.write_text("print('ok')\n", encoding="utf-8")
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI)
    monkeypatch.setattr("devtool.ssh_tmux_runner._assert_session_inactive", lambda *_a, **_k: None)
    rewritten, staged = _stage_support_files_for_spec(
        cfg,
        ["tool", "/usr/local/lib/devtool/build-apk", "--helper-file", str(helper)],
        local_root=repo,
        remote_root="$HOME/workspace",
        session="oci05-phase",
        dry_run=True,
        metadata={"ssh_local_helper_files": [str(helper)]},
    )
    assert rewritten[1] == "/usr/local/lib/devtool/build-apk"
    assert len(staged) == 1 and staged[0][0] == helper
    assert rewritten[-1].endswith("helper.py")


def test_oci05_guest_command_has_minimal_binds_clean_env_and_no_secret_values() -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI)
    command = _oci_wrap_guest_command(
        cfg,
        plan=_plan(),
        guest_cwd="/workspace/app",
        command="./gradlew test",
        public_env={"DEVTOOL_RUN_ID": "run-1"},
        sensitive_keys=("API_TOKEN",),
        operation_host="$HOME/.local/state/devtool/ops/phase",
        operation_guest="/run/devtool/phase",
        bind_operation=True,
        secret_host_dir="$HOME/.local/state/devtool/run/phase",
        secret_guest_dir="/run/devtool-secret/phase",
    )
    assert 'login devtool-android-api36' in command
    assert "/workspace" in command and "/var/cache/devtool" in command
    assert "/run/devtool/phase" in command and "/run/devtool-secret/phase" in command
    assert "env -i" in command
    assert "API_TOKEN" in command
    assert "super-secret" not in command
    assert "PROOT_TMP_DIR=" not in command and "PD_PROOT_BIN=" not in command




def test_oci05_embedded_runtime_resources_are_fresh_copies_of_canonical_sources() -> None:
    pairs = (
        (Path("devtool/resources/oci/android-arm64/build-apk"), Path("devtool/android/_standalone.py")),
        (Path("devtool/resources/oci/android-arm64/termux_toolchains.py"), Path("devtool/termux_toolchains.py")),
        (Path("devtool/resources/oci/android-arm64/devtool-events.init.gradle"), Path("devtool/resources/gradle/devtool-events.init.gradle")),
    )
    for embedded, canonical in pairs:
        assert embedded.read_bytes() == canonical.read_bytes(), f"stale OCI runtime copy: {embedded}"

def test_oci05_inspection_script_checks_nested_runtime_image_and_embedded_hashes() -> None:
    definition = canonical_android_oci_definition()
    script = _inspect_script(container="devtool-android-api36", context_sha256=definition.context_sha256)
    assert "PROOT_TMP_DIR" in script and "nested=1" in script
    assert "embedded_runtime_ok" in script
    assert "installed_definition_digest" in script and "installed_context_sha256" in script
    assert definition.context_sha256 in script
    assert "blobs/sha256/" in script and "sha256sum" in script


def test_oci05_install_receipt_is_atomic_private_and_part_of_inspection_truth() -> None:
    definition = canonical_android_oci_definition()
    script = _install_receipt_script(
        container="devtool-android-api36",
        image_reference=definition.reference,
        image_digest="sha256:" + "a" * 64,
        archive_sha256="b" * 64,
        archive_size=123,
        definition_digest=definition.definition_digest,
        context_sha256=definition.context_sha256,
    )
    assert "umask 077" in script and "os.fchmod(fd,0o600)" in script
    assert "os.fsync" in script and "os.replace" in script
    inspect = _inspect_script(container="devtool-android-api36", context_sha256=definition.context_sha256)
    for field in ("installed_image_reference", "installed_image_digest", "installed_archive_sha256", "installed_archive_size"):
        assert field in inspect


def test_oci05_pre_tmux_construction_cleanup_removes_secret_and_operation_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import ssh_tmux_runner
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI)
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return Completed()

    monkeypatch.setattr(ssh_tmux_runner.subprocess, "run", fake_run)
    _cleanup_oci_construction_state(
        cfg,
        secret_files=("$HOME/.local/state/devtool/run with space/secret-env.sh", "$HOME/.local/state/devtool/run with space/secret-env.sh.tmp"),
        operation_roots=("$HOME/.local/state/devtool/stage/phase with space",),
    )
    assert len(calls) == 1
    remote = calls[0][-1]
    assert "rm -f --" in remote and "secret-env.sh" in remote and "secret-env.sh.tmp" in remote
    # Current recovery contract retains OCI operation staging while deleting only secrets.
    assert "rm -rf --" not in remote
    assert "retained OCI operation staging for provider recovery" in remote
    assert "phase with space" in remote


def test_oci05_remote_phase_script_preserves_exact_nested_command_exit_code() -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI)
    # Exercise the existing tmux owner directly: OCI is nested inside this command,
    # so its status must be the exact status written/read by the phase owner.
    script = _build_remote_phase_script(
        cfg,
        session="oci05-test",
        command="proot-distro login devtool-android-api36 -- /bin/sh -c 'exit 37'",
        run_id="run-oci05",
        workspace_id="ws-1",
        workspace_path="$HOME/workspace",
    )
    assert "command-status" in script
    assert "rc=$(cat" in script
    assert 'exit "$rc"' in script


def test_oci05_combined_provenance_contains_ssh_workspace_and_oci_image_identity() -> None:
    provenance = _ssh_execution_provenance({
        "execution_mode": "ssh-proot-oci",
        "execution_provider": "ssh-proot-oci",
        "ssh_profile": "tablet", "ssh_destination": "tablet", "ssh_workspace_id": "ws-1",
        "ssh_source_sync_id": "sync-1", "ssh_session": "phase-1",
        "oci_runtime": "proot-distro-oci", "oci_container": "devtool-android-api36",
        "oci_image_reference": "devtool/android-build:arm64-api36-v2",
        "oci_image_digest": "sha256:" + "a" * 64, "oci_image_archive_sha256": "b" * 64,
        "oci_architecture": "aarch64", "oci_definition_digest": "c" * 64,
        "oci_build_context_sha256": "d" * 64, "oci_guest_workspace": "/workspace",
        "oci_guest_cache": "/var/cache/devtool",
    })
    assert provenance["transport"] == "ssh"
    assert provenance["workspace_id"] == "ws-1" and provenance["source_sync_id"] == "sync-1"
    assert provenance["execution_provider"] == "ssh-proot-oci"
    assert provenance["container"] == "devtool-android-api36"
    assert provenance["image_reference"].endswith("api36-v2")
    assert provenance["guest_workspace"] == "/workspace"


def test_oci05_native_ssh_provenance_remains_free_of_oci_fields() -> None:
    provenance = _ssh_execution_provenance({
        "execution_mode": "ssh-command", "ssh_profile": "tablet", "ssh_destination": "tablet",
        "ssh_workspace_id": "ws-1", "ssh_source_sync_id": "sync-1", "ssh_session": "phase-1",
    })
    assert provenance["transport"] == "ssh"
    assert "execution_provider" not in provenance
    assert "container" not in provenance and "image_reference" not in provenance


def test_oci05_native_ssh_staging_behavior_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    helper = tmp_path / "helper.py"
    helper.write_text("print('ok')\n", encoding="utf-8")
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_NATIVE)
    monkeypatch.setattr("devtool.ssh_tmux_runner._assert_session_inactive", lambda *_a, **_k: None)
    rewritten, staged = _stage_support_files_for_spec(
        cfg, ["tool", "--helper-file", str(helper)], local_root=repo, remote_root="$HOME/workspace",
        session="native-phase", dry_run=True, metadata={"ssh_local_helper_files": [str(helper)]},
    )
    assert len(staged) == 1 and staged[0][0] == helper
    assert rewritten[-1] != str(helper)


def test_oci05_brand_new_workspace_dry_run_does_not_claim_sync_or_mutate(monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import oci_workspace

    cfg = SSHRunnerConfig(
        host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI,
        workspace_root="~/.cache/devtool/workspaces", oci_cache_root="~/.cache/devtool/oci/workspace-caches",
    )
    calls: list[str] = []
    monkeypatch.setattr(oci_workspace._runtime, "_discover_nodes", lambda *_a, **_k: None)
    monkeypatch.setattr(oci_workspace._runtime, "_finish_node", lambda _run, node, **_k: calls.append("finish:" + node))
    monkeypatch.setattr(oci_workspace._runtime, "_start_node", lambda _run, node, *_a, **_k: calls.append("start:" + node))
    monkeypatch.setattr(
        oci_workspace._runtime,
        "_run_process",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("planned dry-run must not inspect or create remote workspace/cache")),
    )
    monkeypatch.setattr(oci_workspace.RunnerExecutionAdapter, "diagnostic", lambda *_a, **_k: "diag")

    plan, contract_node = oci_workspace.prepare_oci_workspace_for_execution(
        cfg,
        run_id="run-dry",
        workspace_id="repo-deadbeef",
        remote_root="$HOME/.cache/devtool/workspaces/repo-deadbeef",
        source_sync_id="",
        source_revision={},
        dry_run=True,
        environment={},
        workspace_planned=True,
    )
    assert plan.remote_workspace == "~/.cache/devtool/workspaces/repo-deadbeef"
    assert plan.remote_cache_root == "~/.cache/devtool/oci/workspace-caches/repo-deadbeef"
    assert plan.source_sync_id == ""
    assert plan.working_directory == "/workspace"
    assert any(item.startswith("finish:") for item in calls)
    assert contract_node


def test_oci05_missing_image_build_is_reinspected_before_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from devtool import oci_execution

    cfg = SSHRunnerConfig(
        host="tablet", profile="tablet", workspace_mode="managed", execution_provider=SSH_PROOT_OCI,
        oci_digest_policy="record",
    )
    plan = _plan()
    definition = canonical_android_oci_definition()
    monkeypatch.setattr(oci_execution, "prepare_oci_workspace_for_execution", lambda *_a, **_k: (plan, "workspace-node"))
    actions: list[str] = []

    def fake_inspect(_config, *, run_id, container, context_sha256, action, depends_on):
        actions.append(action)
        if action == "oci05-runtime-image-inspect":
            return ({
                "nested_proot": "0", "runtime_exists": "1", "archive_exists": "0", "archive_sha256": "",
                "archive_size": "0", "image_digest": "", "container_exists": "0",
            }, "inspect-node")
        if action == "oci05-image-after-build":
            return ({
                "nested_proot": "0", "runtime_exists": "1", "archive_exists": "1", "archive_sha256": "b" * 64,
                "archive_size": "123", "image_digest": "sha256:" + "a" * 64, "container_exists": "0",
            }, "after-build-node")
        assert action == "oci05-final-evidence-inspect"
        return ({
            "container_exists": "1", "embedded_runtime_ok": "1",
            "installed_definition_digest": definition.definition_digest,
            "installed_context_sha256": definition.context_sha256,
            "installed_image_reference": "devtool/android-build:arm64-api36-v2",
            "installed_image_digest": "sha256:" + "a" * 64,
            "installed_archive_sha256": "b" * 64, "installed_archive_size": "123",
            "archive_sha256": "b" * 64, "archive_size": "123", "image_digest": "sha256:" + "a" * 64,
        }, "final-node")

    monkeypatch.setattr(oci_execution, "_inspect_state", fake_inspect)
    monkeypatch.setattr(
        oci_execution._runtime,
        "oci_build_payload",
        lambda *_a, **_k: {
            "image_digest": "sha256:" + "a" * 64, "image_sha256": "b" * 64, "image_size": 123,
            "node_ids": ["stage-node", "build-node"],
        },
    )
    monkeypatch.setattr(oci_execution, "_install_built_image", lambda *_a, **_k: "install-node")
    monkeypatch.setattr(oci_execution, "_write_install_receipt", lambda *_a, **_k: "receipt-node")
    monkeypatch.setattr(oci_execution._runtime, "oci_validate_image_payload", lambda *_a, **_k: {"node_id": "validate-node"})

    state = oci_execution.prepare_oci_execution(
        cfg, repo_root=str(tmp_path), run_id="run-oci05", workspace_id="repo-deadbeef",
        remote_root="$HOME/workspace", source_sync_id="sync-1", source_revision={}, environment={},
    )
    assert actions == ["oci05-runtime-image-inspect", "oci05-image-after-build", "oci05-final-evidence-inspect"]
    assert state.image_digest == "sha256:" + "a" * 64
    assert state.archive_sha256 == "b" * 64
    assert state.validation_node_id == "final-node"
