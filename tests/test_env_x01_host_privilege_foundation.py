from __future__ import annotations

import os
from pathlib import Path
import tempfile
from unittest.mock import patch

import pytest

from devtool.config import load_project_config, minimal_global_config_template
from devtool.environment_qualification import (
    CapabilityRequirements,
    EnvironmentCandidate,
    EnvironmentQualificationCache,
    assert_environment_overlay_safe,
    qualify_environment,
)
from devtool.execution import build_chroot_exec_argv
from devtool.execution_environments import HOST, NATIVE_TERMUX, normalize_execution_environment, resolve_execution_plan
from devtool.host_context import HostContext, detect_host_context
from devtool.privilege import (
    HOST_CHROOT_ENTER,
    LINUX_VM_DROP_CACHES,
    PrivilegePolicy,
    PrivilegeRequest,
    invalidate_privilege_qualification_cache,
    qualify_privilege_provider,
    resolve_privilege_plan,
)


def termux_host(*, euid: int = 20000) -> HostContext:
    return HostContext(
        os_family="android",
        system="Linux",
        architecture="aarch64",
        machine="aarch64",
        termux=True,
        inside_chroot=False,
        uid=euid,
        euid=euid,
    )


def test_explicit_sparse_linux_environment_does_not_leak_android_parent_platform(monkeypatch) -> None:
    monkeypatch.setattr("devtool.host_context.platform.system", lambda: "Android")
    monkeypatch.setattr("devtool.host_context.sys.platform", "android")
    host = detect_host_context({"HOME": "/home/mike"})
    assert host.os_family == "linux"
    assert host.current_substrate == "desktop-linux"


def test_build_resource_policy_does_not_inherit_android_platform_for_synthetic_desktop() -> None:
    from devtool.build_optimization import resolve_build_policy
    from devtool.models import ProjectConfig

    config = ProjectConfig(repo_root=Path("/repo"), kind="rust")
    with (
        patch("devtool.host_context.platform.system", return_value="Android"),
        patch("devtool.build_optimization.os.cpu_count", return_value=12),
        patch("devtool.build_optimization.available_memory_mb", return_value=16000),
        patch.dict(os.environ, {"HOME": "/home/mike", "PREFIX": ""}, clear=True),
    ):
        policy = resolve_build_policy(config, "rust")

    assert policy.environment == "desktop-linux"
    assert policy.max_workers == 8


def test_build_resource_policy_still_recognizes_real_termux_from_environment_snapshot() -> None:
    from devtool.build_optimization import resolve_build_policy
    from devtool.models import ProjectConfig

    config = ProjectConfig(repo_root=Path("/repo"), kind="rust")
    with (
        patch("devtool.host_context.platform.system", return_value="Android"),
        patch("devtool.build_optimization.os.cpu_count", return_value=12),
        patch("devtool.build_optimization.available_memory_mb", return_value=2500),
        patch.dict(os.environ, {
            "HOME": "/data/data/com.termux/files/home",
            "PREFIX": "/data/data/com.termux/files/usr",
            "TERMUX_VERSION": "0.119",
        }, clear=True),
    ):
        policy = resolve_build_policy(config, "rust")

    assert policy.environment == "native-termux"
    assert policy.max_workers == 2


def test_termux_host_identity_separates_android_host_from_native_substrate() -> None:
    env = {
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TERMUX_VERSION": "0.119",
    }
    host = detect_host_context(env, system="Linux", machine="aarch64", platform_name="linux")
    assert host.os_family == "android"
    assert host.host_kind == "termux-android"
    assert host.current_substrate == NATIVE_TERMUX


def test_chroot_identity_is_a_guest_substrate_not_native_termux() -> None:
    env = {
        "HOME": "/home/mike",
        "PREFIX": "/usr",
        "DEVTOOL_EXECUTION_CONTEXT": "chroot",
        "DEVTOOL_CHROOT_CONTEXT": "androidos",
    }
    host = detect_host_context(env, system="Linux", machine="aarch64", platform_name="linux")
    assert host.os_family == "linux"
    assert host.inside_chroot is True
    assert host.termux is False
    assert host.current_substrate == "chroot"
    assert host.chroot_name == "androidos"




def test_termux_ssh_login_remains_native_termux_substrate() -> None:
    env = {
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TERMUX_VERSION": "0.119",
        "SSH_CONNECTION": "192.0.2.10 53000 192.0.2.20 8022",
        "SSH_CLIENT": "192.0.2.10 53000 8022",
        "SSH_TTY": "/dev/pts/3",
    }
    host = detect_host_context(env, system="Linux", machine="aarch64", platform_name="linux")
    assert host.remote is True
    assert host.termux is True
    assert host.remote_provider == ""
    assert host.current_substrate == NATIVE_TERMUX


def test_native_termux_preflight_accepts_ssh_login_session() -> None:
    from devtool.execution_environments import preflight_execution, resolve_execution_plan

    env = {
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TERMUX_VERSION": "0.119",
        "SSH_CONNECTION": "192.0.2.10 53000 192.0.2.20 8022",
    }
    plan = resolve_execution_plan(requested="native-termux", configured="auto", env=env)
    assert plan.current == NATIVE_TERMUX
    assert plan.effective == NATIVE_TERMUX
    preflight = preflight_execution(Path.cwd(), plan)
    assert preflight.ok
    assert [(check.name, check.status, check.detail) for check in preflight.checks if check.name == "termux"] == [("termux", "ready", NATIVE_TERMUX)]

def test_remote_provider_identity_does_not_replace_ssh_substrate() -> None:
    env = {
        "HOME": "/home/mike",
        "SSH_CONNECTION": "1 2 3 4",
        "DEVTOOL_EXECUTION_PROVIDER": "ssh-proot-oci",
    }
    host = detect_host_context(env, system="Linux", machine="aarch64", platform_name="linux")
    assert host.current_substrate == "ssh"
    assert host.remote_provider == "ssh-proot-oci"


def test_host_alias_means_current_substrate_not_desktop_linux() -> None:
    env = {
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TERMUX_VERSION": "1",
    }
    assert normalize_execution_environment("host") == HOST
    with patch.dict(os.environ, env, clear=True):
        plan = resolve_execution_plan(requested="host", configured="auto")
    assert plan.current == NATIVE_TERMUX
    assert plan.effective == NATIVE_TERMUX
    assert "current host substrate" in plan.reason


def test_passive_provider_discovery_never_treats_su_presence_as_readiness() -> None:
    request = PrivilegeRequest(HOST_CHROOT_ENTER, requested_by="test")
    with patch("devtool.privilege.shutil.which", return_value="/system/bin/su"), \
            patch("devtool.privilege._run_probe") as probe:
        result = qualify_privilege_provider("android-su", request, host=termux_host(), probe=False)
    assert result.state == "action-required"
    assert result.passive is True
    probe.assert_not_called()


def test_unattended_android_su_probe_cannot_trigger_root_authorization() -> None:
    request = PrivilegeRequest(HOST_CHROOT_ENTER, requested_by="test", interactive=False)
    with patch("devtool.privilege.shutil.which", return_value="/system/bin/su"), \
            patch("devtool.privilege._run_probe") as probe:
        result = qualify_privilege_provider(
            "android-su", request, host=termux_host(), probe=True,
            allow_interactive_authorization=True,
        )
    assert result.state == "action-required"
    assert "unattended" in result.detail
    probe.assert_not_called()


def test_interactive_su_probe_is_still_blocked_when_global_policy_forbids_prompting() -> None:
    request = PrivilegeRequest(HOST_CHROOT_ENTER, requested_by="test", interactive=True)
    with patch("devtool.privilege.shutil.which", return_value="/system/bin/su"), \
            patch("devtool.privilege._run_probe") as probe:
        result = qualify_privilege_provider(
            "android-su", request, host=termux_host(), probe=True,
            allow_interactive_authorization=False,
        )
    assert result.state == "action-required"
    assert "global policy forbids" in result.detail
    probe.assert_not_called()


def test_privilege_plan_requires_global_capability_authorization() -> None:
    request = PrivilegeRequest(LINUX_VM_DROP_CACHES, mutation=True)
    policy = PrivilegePolicy(allow=frozenset({HOST_CHROOT_ENTER}), source="test-global")
    with pytest.raises(PermissionError, match="not authorized"):
        resolve_privilege_plan(request, policy=policy, host=termux_host(), probe=False)



def test_privilege_qualification_cache_reuses_probe_and_scopes_by_environment() -> None:
    host = HostContext(
        os_family="linux", system="Linux", architecture="x86_64", machine="x86_64",
        termux=False, inside_chroot=False, uid=1000, euid=1000,
    )
    request = PrivilegeRequest(HOST_CHROOT_ENTER, interactive=False)
    policy = PrivilegePolicy(allow=frozenset({HOST_CHROOT_ENTER}), provider_order=("sudo",), source="test")
    invalidate_privilege_qualification_cache()
    with patch("devtool.privilege.shutil.which", return_value="/usr/bin/sudo"), \
            patch("devtool.privilege._run_probe", return_value=("ready", "ok")) as probe:
        resolve_privilege_plan(request, policy=policy, host=host, env={"PATH": "/usr/bin"}, probe=True)
        resolve_privilege_plan(request, policy=policy, host=host, env={"PATH": "/usr/bin"}, probe=True)
        assert probe.call_count == 1
        resolve_privilege_plan(request, policy=policy, host=host, env={"PATH": "/custom/bin"}, probe=True)
        assert probe.call_count == 2


def test_repo_config_cannot_grant_privilege() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".devtool.toml").write_text(
            '[project]\nrunner="python"\n[privilege]\nallow=["*"]\n', encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="global-only trusted policy"):
            load_project_config(str(root), None)


def test_sparse_global_template_documents_privilege_policy() -> None:
    text = minimal_global_config_template()
    assert "[privilege]" in text
    assert 'allow = ["host.chroot.enter"]' in text
    assert 'deny = ["linux.vm.drop_caches"]' in text


def test_protected_runtime_environment_names_cannot_be_forged() -> None:
    with pytest.raises(ValueError, match="DEVTOOL_RUN_ID"):
        assert_environment_overlay_safe({"DEVTOOL_RUN_ID": "spoof", "SAFE": "yes"}, source="repo")
    with pytest.raises(ValueError, match="DEVTOOL_FEDERATION_PARENT_RUN_ID"):
        assert_environment_overlay_safe({"DEVTOOL_FEDERATION_PARENT_RUN_ID": "spoof"}, source="job")
    with pytest.raises(ValueError, match="DEVTOOL_CHROOT_SUDO"):
        assert_environment_overlay_safe({"DEVTOOL_CHROOT_SUDO": "sudo"}, source="repo")
    with pytest.raises(ValueError, match="DEVTOOL_TERMUX_CHROOT_USER"):
        assert_environment_overlay_safe({"DEVTOOL_TERMUX_CHROOT_USER": "root"}, source="target")


def test_repo_env_rejects_protected_runtime_identity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".devtool.toml").write_text(
            '[project]\nrunner="python"\n[env]\nDEVTOOL_RUN_ID="spoof"\n', encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="protected Devtool environment variable DEVTOOL_RUN_ID"):
            load_project_config(str(root), None)


def test_chroot_candidate_requires_real_tool_receipt_and_ready_privilege() -> None:
    requirements = CapabilityRequirements(tools=("gradle",))
    policy = PrivilegePolicy(
        allow=frozenset({HOST_CHROOT_ENTER}),
        provider_order=("android-su",),
        allow_interactive_authorization=False,
        source="test-global",
    )
    candidate = EnvironmentCandidate("chroot", name="androidos", provider="chrootctl")
    def which(name: str, *args, **kwargs):
        return f"/system/bin/{name}" if name in {"chrootctl", "su"} else None
    with patch("devtool.environment_qualification.shutil.which", side_effect=which), \
            patch("devtool.privilege.shutil.which", side_effect=which):
        receipt = qualify_environment(
            requirements, candidates=(candidate,), host=termux_host(), policy=policy,
            chroot_name="androidos", probe_privilege=False,
        )
    assert receipt.selected_identity == ""
    row = receipt.candidates[0]
    assert row.accepted is False
    assert any("privilege" in reason for reason in row.rejection_reasons)
    assert any("unqualified" in reason for reason in row.rejection_reasons)


def test_proot_uid_zero_never_satisfies_real_host_privilege() -> None:
    requirements = CapabilityRequirements(privilege_capabilities=(LINUX_VM_DROP_CACHES,))
    candidate = EnvironmentCandidate("proot-distro", name="ubuntu", provider="proot-distro")
    with patch("devtool.environment_qualification.shutil.which", return_value="/usr/bin/proot-distro"):
        receipt = qualify_environment(
            requirements, candidates=(candidate,), host=termux_host(),
            policy=PrivilegePolicy(allow=frozenset({LINUX_VM_DROP_CACHES}), source="test-global"),
        )
    assert receipt.selected_identity == ""
    assert "emulated uid 0" in " ".join(receipt.candidates[0].rejection_reasons)


def test_capability_first_selection_prefers_qualified_current_substrate() -> None:
    requirements = CapabilityRequirements(tools=("python",))
    current = EnvironmentCandidate("native-termux", name="current", provider="native")
    guest = EnvironmentCandidate("proot-distro", name="ubuntu", provider="proot-distro", metadata={"tools": ["python"]})
    def which(name: str, *args, **kwargs):
        return f"/mock/{name}" if name in {"python", "proot-distro"} else None
    with patch("devtool.environment_qualification.shutil.which", side_effect=which):
        receipt = qualify_environment(
            requirements, candidates=(guest, current), host=termux_host(),
            policy=PrivilegePolicy(source="test-global"),
        )
    assert receipt.selected_identity == "native-termux:current"
    assert receipt.selected is not None and receipt.selected.accepted


def test_environment_qualification_cache_expires_and_invalidates() -> None:
    host = termux_host()
    receipt = qualify_environment(
        CapabilityRequirements(), candidates=(EnvironmentCandidate("native-termux", name="current"),),
        host=host, policy=PrivilegePolicy(source="test-global"), ttl_seconds=10,
    )
    cache = EnvironmentQualificationCache(ttl_seconds=10)
    cache.put(receipt)
    with patch("devtool.environment_qualification.time.monotonic", return_value=receipt.created_monotonic + 5):
        assert cache.get(receipt.cache_key) is receipt
    cache.invalidate()
    assert cache.get(receipt.cache_key) is None



def test_action_required_privilege_candidate_is_not_auto_selected() -> None:
    requirements = CapabilityRequirements(privilege_capabilities=(HOST_CHROOT_ENTER,))
    current = EnvironmentCandidate("native-termux", name="current", provider="native")
    policy = PrivilegePolicy(
        allow=frozenset({HOST_CHROOT_ENTER}),
        provider_order=("android-su",),
        allow_interactive_authorization=False,
        source="test-global",
    )
    with patch("devtool.privilege.shutil.which", return_value="/system/bin/su"):
        receipt = qualify_environment(
            requirements, candidates=(current,), host=termux_host(), policy=policy,
            probe_privilege=False,
        )
    assert receipt.selected_identity == ""
    assert receipt.candidates[0].accepted is False
    assert any("requires explicit authorization" in reason for reason in receipt.candidates[0].rejection_reasons)


def test_environment_qualification_cache_key_includes_requested_ttl() -> None:
    from devtool.environment_qualification import invalidate_environment_qualification_cache

    invalidate_environment_qualification_cache()
    candidate = EnvironmentCandidate("native-termux", name="current", provider="native")
    first = qualify_environment(
        CapabilityRequirements(), candidates=(candidate,), host=termux_host(),
        policy=PrivilegePolicy(source="test-global"), ttl_seconds=30,
    )
    second = qualify_environment(
        CapabilityRequirements(), candidates=(candidate,), host=termux_host(),
        policy=PrivilegePolicy(source="test-global"), ttl_seconds=1,
    )
    assert first.cache_key != second.cache_key
    assert first.ttl_seconds == 30
    assert second.ttl_seconds == 1

def test_chroot_entry_no_longer_has_implicit_sudo_default() -> None:
    env = {
        "HOME": "/data/data/com.termux/files/home",
        "PREFIX": "/data/data/com.termux/files/usr",
        "TERMUX_VERSION": "1",
        "DEVTOOL_CHROOTCTL": "chrootctl",
        "DEVTOOL_CHROOT_PATH_PREFIX": "",
    }
    class Plan:
        def wrap_argv(self, argv):
            return ["trusted-provider", *argv]
    with patch("devtool.execution.load_global_privilege_policy", return_value=PrivilegePolicy(source="test")), \
            patch("devtool.execution.resolve_privilege_plan", return_value=Plan()) as resolve:
        argv = build_chroot_exec_argv("echo ok", "androidos", env=env)
    assert argv[:2] == ["trusted-provider", "chrootctl"]
    assert "sudo" not in argv
    request = resolve.call_args.args[0]
    assert request.capability_id == HOST_CHROOT_ENTER


def test_explicit_legacy_chroot_privilege_override_remains_compatible() -> None:
    argv = build_chroot_exec_argv(
        "echo ok", "androidos",
        env={"DEVTOOL_CHROOTCTL": "chrootctl", "DEVTOOL_CHROOT_SUDO": "sudo", "DEVTOOL_CHROOT_PATH_PREFIX": ""},
    )
    assert argv[:2] == ["sudo", "-n"]
    assert argv[2:5] == ["chrootctl", "--name", "androidos"]


def test_resource_cache_reclaim_executes_only_after_global_policy_authorization() -> None:
    from devtool import runners
    import subprocess

    host = HostContext(
        os_family="linux", system="Linux", architecture="x86_64", machine="x86_64",
        termux=False, inside_chroot=False, uid=1000, euid=1000,
    )
    policy = PrivilegePolicy(
        allow=frozenset({LINUX_VM_DROP_CACHES}),
        provider_order=("sudo",),
        source="test-global",
    )
    completed = subprocess.CompletedProcess(["sudo"], 0)
    with (
        patch("devtool.host_context.detect_host_context", return_value=host),
        patch("devtool.privilege.load_global_privilege_policy", return_value=policy),
        patch("devtool.privilege.shutil.which", side_effect=lambda name, path=None: "/usr/bin/sudo" if name == "sudo" else ("/bin/sh" if name == "sh" else None)),
        patch("devtool.privilege._run_probe", return_value=("ready", "ok")),
        patch("devtool.runners.subprocess.run", return_value=completed) as run_mock,
    ):
        assert runners._try_root_cache_reclaim() is True

    argv = run_mock.call_args.args[0]
    assert argv[:2] == ["/usr/bin/sudo", "-n"]
    assert "drop_caches" in argv[-1]


def test_resource_cache_reclaim_is_denied_without_global_policy_authorization() -> None:
    from devtool import runners

    policy = PrivilegePolicy(
        allow=frozenset({HOST_CHROOT_ENTER}),
        deny=frozenset({LINUX_VM_DROP_CACHES}),
        source="test-global",
    )
    with (
        patch("devtool.privilege.load_global_privilege_policy", return_value=policy),
        patch("devtool.runners.subprocess.run") as run_mock,
    ):
        assert runners._try_root_cache_reclaim() is False
    run_mock.assert_not_called()
