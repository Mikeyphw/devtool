from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path

import pytest

from devtool.cli import build_parser
from devtool.core.events import EventBus
from devtool.core.execution_recovery import recover_event_bus
from devtool.execution_providers import SSH_PROOT_OCI, execution_provider
from devtool import oci_workspace, proot_oci
from devtool.oci_workspace import (
    APPROVED_CACHES,
    CACHE_GUEST,
    WORKSPACE_GUEST,
    OciWorkspaceBind,
    OciWorkspacePlan,
    filter_oci_environment,
    proot_login_options,
    validate_env_allowlist,
)
from devtool.ssh_tmux_runner import SSHRunnerConfig, _ssh_profile_section, resolve_ssh_config
from devtool.ssh_workspace import repository_identity


EMPTY_SOURCE_REVISION_DIGEST = hashlib.sha256(
    json.dumps(
        {"git_head": "", "working_tree_dirty": False, "working_tree_fingerprint": ""},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
GIT_EMPTY_WORKTREE_SOURCE_REVISION_DIGEST = hashlib.sha256(
    json.dumps(
        {
            "git_head": "",
            "working_tree_dirty": False,
            "working_tree_fingerprint": hashlib.sha256(b"").hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


class _QueuePopen:
    outputs: list[tuple[bytes, bytes, int]] = []
    calls: list[list[str]] = []
    kwargs_calls: list[dict[str, object]] = []
    pid_seed = 9100

    def __init__(self, argv, **kwargs):
        type(self).pid_seed += 1
        self.pid = type(self).pid_seed
        self.argv = list(argv)
        type(self).calls.append(self.argv)
        type(self).kwargs_calls.append(dict(kwargs))
        if not type(self).outputs:
            raise AssertionError("unexpected subprocess")
        self._stdout, self._stderr, self.returncode = type(self).outputs.pop(0)

    def communicate(self, input=None, timeout=None):
        self.input = input
        return self._stdout, self._stderr

    def kill(self):
        self.returncode = -9


def _config() -> SSHRunnerConfig:
    return SSHRunnerConfig(
        host="192.168.1.20", port=8022, user="u0_a123", profile="tablet",
        identity="/home/me/.ssh/tablet", execution_provider=SSH_PROOT_OCI,
        workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        oci_cache_root="~/.cache/devtool/oci/workspace-caches",
        oci_env_allowlist=("CI", "LANG"),
    )


def _patch_bus(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> EventBus:
    bus = EventBus()
    monkeypatch.setattr(oci_workspace, "publish_event", bus.publish)
    monkeypatch.setattr(oci_workspace, "get_event_bus", lambda: bus)
    monkeypatch.setattr(proot_oci, "publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    monkeypatch.setattr(proot_oci, "state_dir", lambda: tmp_path / "state")
    return bus


def test_oci04_cli_surface_remains_compatible_after_provider_activation() -> None:
    parser = build_parser()
    parsed = parser.parse_args(["oci", "workspace", "tablet", "--dry-run", "--format", "json"])
    assert parsed.oci_command == "workspace"
    assert parsed.oci_dry_run is True
    assert execution_provider(SSH_PROOT_OCI, remote=True).executable is True


def test_profile_oci_cache_and_environment_allowlist_are_resolved() -> None:
    data = {
        "ssh": {"hosts": {"tablet": {
            "host": "192.168.1.20",
            "workspace": {"mode": "managed"},
            "execution": {"provider": "ssh-proot-oci"},
            "oci": {"cache_root": "~/.cache/custom-oci", "env_allowlist": ["CI", "LANG"]},
        }}}
    }
    section = _ssh_profile_section(data, "tablet")
    assert section["oci_cache_root"] == "~/.cache/custom-oci"
    assert section["oci_env_allowlist"] == ["CI", "LANG"]



def test_full_config_resolution_applies_profile_oci_settings(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        """[ssh]
profile = "tablet"

[ssh.hosts.tablet]
host = "tablet"

[ssh.hosts.tablet.worker]
tmux = true

[ssh.hosts.tablet.workspace]
mode = "managed"
root = "~/.cache/devtool/workspaces"

[ssh.hosts.tablet.execution]
provider = "ssh-proot-oci"

[ssh.hosts.tablet.oci]
cache_root = "~/.cache/custom-oci"
env_allowlist = ["CI", "LANG"]
""",
        encoding="utf-8",
    )
    config = resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    assert config.execution_provider == SSH_PROOT_OCI
    assert config.workspace_mode == "managed"
    assert config.oci_cache_root == "~/.cache/custom-oci"
    assert config.oci_env_allowlist == ("CI", "LANG")

def test_environment_forwarding_is_exact_allowlist_and_rejects_secret_or_managed_names() -> None:
    source = {"CI": "1", "LANG": "en_US.UTF-8", "PATH": "/host/bin", "MY_SECRET": "boom", "PD_DOCKER_AUTH": "u:p"}
    assert filter_oci_environment(source, ("CI", "LANG")) == {"CI": "1", "LANG": "en_US.UTF-8"}
    with pytest.raises(ValueError, match="secret-bearing"):
        validate_env_allowlist(["CI", "MY_SECRET"])
    with pytest.raises(ValueError, match="managed"):
        validate_env_allowlist(["DEVTOOL_RUN_ID"])
    with pytest.raises(ValueError, match="managed"):
        validate_env_allowlist(["PD_DOCKER_AUTH"])


def test_remote_and_guest_path_contract_rejects_traversal() -> None:
    with pytest.raises(ValueError, match="cannot contain '..'"):
        oci_workspace._normalize_remote_root("~/.cache/../escape", label="cache")
    with pytest.raises(ValueError, match="cannot contain '..'"):
        oci_workspace._normalize_guest("/workspace/../escape", label="guest")
    assert oci_workspace._normalize_guest("//workspace//project", label="guest") == "/workspace/project"
    assert oci_workspace._normalize_remote_root("$HOME//.cache/./devtool//oci/", label="cache") == "~/.cache/devtool/oci"
    assert oci_workspace._normalize_remote_root("/data//local/./tmp/", label="workspace") == "/data/local/tmp"


def test_proot_options_bind_workspace_and_cache_set_workdir_and_do_not_forward_host_environment() -> None:
    plan = OciWorkspacePlan(
        profile="tablet", workspace_id="repo-123",
        remote_workspace="/data/data/com.termux/files/home/.cache/devtool/workspaces/repo-123",
        remote_cache_root="/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/repo-123",
        source_sync_id="sync-123",
        working_directory=WORKSPACE_GUEST,
        binds=(
            OciWorkspaceBind("/host/work", WORKSPACE_GUEST, "workspace"),
            OciWorkspaceBind("/host/cache", CACHE_GUEST, "persistent-cache"),
        ),
        managed_environment={"GRADLE_USER_HOME": f"{CACHE_GUEST}/gradle"},
        forwarded_environment={"CI": "1"},
    )
    options = proot_login_options(plan)
    rendered = " ".join(options)
    assert "--work-dir /workspace" in rendered
    assert "--bind /host/work:/workspace" in rendered
    assert "--bind /host/cache:/var/cache/devtool" in rendered
    assert "--env CI=1" in rendered
    assert f"--env GRADLE_USER_HOME={CACHE_GUEST}/gradle" in rendered
    assert "PATH=" not in rendered and "PD_DOCKER_AUTH" not in rendered


def test_workspace_payload_uses_two_owned_remote_processes_and_persists_canonical_exo_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.kwargs_calls.clear()
    _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nhome_real=/data/data/com.termux/files/home\nsync_id=sync-123\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=1\ncache_exists=1\ncache_ready=1\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = oci_workspace.oci_workspace_payload(
        _config(), repo_root=repo, run_id="run-oci04-workspace", environment={"CI": "1", "LANG": "C", "MY_SECRET": "nope"},
    )
    assert payload["ok"] is True
    assert payload["plan"]["guest_workspace"] == "/workspace"
    assert payload["plan"]["working_directory"] == "/workspace"
    assert payload["plan"]["guest_cache_root"] == "/var/cache/devtool"
    assert payload["plan"]["approved_caches"] == list(APPROVED_CACHES)
    assert payload["plan"]["source_sync_id"] == "sync-123"
    assert payload["plan"]["forwarded_environment_names"] == ["CI", "LANG"]
    assert len(_QueuePopen.calls) == 2
    snap = bus.execution_store.snapshot("run-oci04-workspace")
    assert snap is not None and snap.status == "succeeded"
    assert [snap.node(node).status for node in payload["node_ids"]] == ["succeeded", "succeeded", "succeeded", "succeeded"]
    assert len(snap.processes) == 2
    assert len(snap.artifacts) == 1
    assert snap.artifacts[0].producer_node_id == payload["node_ids"][3]
    assert snap.artifacts[0].sha256 and len(snap.artifacts[0].sha256) == 64
    assert snap.artifacts[0].size and snap.artifacts[0].size > 0
    assert snap.artifacts[0].verification_status == "verified"
    assert Path(payload["execution_json"]).is_file()
    evidence = json.loads(Path(payload["evidence_artifact"]).read_text())
    assert "MY_SECRET" not in json.dumps(evidence)
    assert evidence["forwarded_environment_names"] == ["CI", "LANG"]
    assert all(proc.provenance["execution_provider"] == "ssh-proot-oci" for proc in snap.processes)
    assert snap.processes[1].provenance["source_sync_id"] == "sync-123"
    recovered_bus, report = recover_event_bus(Path(payload["execution_dir"]))
    recovered = recovered_bus.execution_store.snapshot("run-oci04-workspace")
    assert report.events_replayed > 0
    assert recovered is not None and recovered.status == "succeeded"
    assert [node.status for node in recovered.nodes] == ["succeeded", "succeeded", "succeeded", "succeeded"]


def test_workspace_payload_dry_run_keeps_cache_process_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-123\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=0\ncache_exists=0\ncache_ready=0\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-dry", dry_run=True, environment={})
    assert payload["dry_run"] is True
    assert payload["cache_prepared"] is False
    assert payload["plan"]["forwarded_environment_names"] == []
    cache_command = " ".join(_QueuePopen.calls[1])
    assert "[ 0 -eq 1 ]" in cache_command


def test_workspace_failure_blocks_dependency_chain_without_provider_local_lifecycle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [(b"", b"managed SSH workspace is missing", 66)]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="Inspect managed SSH workspace"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-fail")
    snap = bus.execution_store.snapshot("run-oci04-fail")
    assert snap is not None and snap.status == "failed"
    statuses = [node.status for node in snap.nodes]
    assert statuses.count("failed") == 1
    assert statuses.count("blocked") == 2
    assert snap.nodes[0].status == "succeeded"
    assert snap.nodes[1].status == "failed"
    assert snap.nodes[2].blocked_by == (snap.nodes[1].node_id,)
    assert snap.nodes[3].blocked_by == (snap.nodes[2].node_id,)



def test_malformed_workspace_path_evidence_fails_inspection_node_before_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real=relative/path\nsync_id=sync-123\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="invalid structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-invalid-workspace")
    snap = bus.execution_store.snapshot("run-oci04-invalid-workspace")
    assert snap is not None and snap.status == "failed"
    assert snap.nodes[0].status == "succeeded"
    assert snap.nodes[1].status == "failed"
    assert snap.nodes[2].status == "blocked"
    assert snap.nodes[3].status == "blocked"


def test_dry_run_rejects_remote_claim_that_cache_was_mutated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-123\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=1\ncache_exists=1\ncache_ready=1\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="invalid structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-dry-mutation", dry_run=True, environment={})
    snap = bus.execution_store.snapshot("run-oci04-dry-mutation")
    assert snap is not None and snap.status == "failed"
    assert snap.nodes[0].status == "succeeded"
    assert snap.nodes[1].status == "succeeded"
    assert snap.nodes[2].status == "failed"
    assert snap.nodes[3].status == "blocked"


def test_git_identity_lookup_is_exo_owned_and_origin_credentials_never_persist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import subprocess
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    secret_origin = "https://user:super-secret-token@example.invalid/demo.git"
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", secret_origin], check=True)
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        (secret_origin.encode() + b"\n", b"", 0),
        (b"", b"", 0),
        (b"", b"", 0),
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-secret\nsource_revision_digest={GIT_EMPTY_WORKTREE_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=1\ncache_exists=1\ncache_ready=1\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(oci_workspace, "_LOCAL_POPEN", _QueuePopen)
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = oci_workspace.oci_workspace_payload(
        _config(), repo_root=repo, run_id="run-oci04-git-owned", environment={"CI": "1"},
    )
    snap = bus.execution_store.snapshot("run-oci04-git-owned")
    assert snap is not None and snap.status == "succeeded"
    assert len(snap.processes) == 5
    assert all(proc.node_id == payload["node_ids"][0] for proc in snap.processes[:3])
    assert all(proc.provenance.get("transport") == "local" for proc in snap.processes[:3])
    durable = Path(payload["execution_json"]).read_text(encoding="utf-8") + Path(payload["journal"]).read_text(encoding="utf-8") + Path(payload["evidence_artifact"]).read_text(encoding="utf-8")
    assert "super-secret-token" not in durable
    assert secret_origin not in durable


def test_real_cache_prepare_requires_every_approved_cache_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-123\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=1\ncache_exists=1\ncache_ready=0\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="invalid structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-cache-not-ready", environment={})
    snap = bus.execution_store.snapshot("run-oci04-cache-not-ready")
    assert snap is not None and snap.status == "failed"
    assert snap.nodes[2].status == "failed"
    assert snap.nodes[3].status == "blocked"


def test_preflight_validation_failure_is_canonical_exo_failure_and_blocks_dependents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bus = _patch_bus(monkeypatch, tmp_path)
    config = _config()
    config.execution_provider = "ssh-native"
    with pytest.raises(ValueError, match="preflight failed"):
        oci_workspace.oci_workspace_payload(config, repo_root=repo, run_id="run-oci04-preflight-fail", environment={})
    snap = bus.execution_store.snapshot("run-oci04-preflight-fail")
    assert snap is not None and snap.status == "failed"
    assert [node.status for node in snap.nodes] == ["failed", "blocked", "blocked", "blocked"]
    assert snap.nodes[1].blocked_by == (snap.nodes[0].node_id,)
    assert snap.nodes[2].blocked_by == (snap.nodes[1].node_id,)
    assert snap.nodes[3].blocked_by == (snap.nodes[2].node_id,)


def test_bind_path_and_environment_transport_reject_ambiguous_control_data() -> None:
    with pytest.raises(ValueError, match="cannot contain ':'"):
        oci_workspace._normalize_remote_root("~/cache:other", label="cache")
    with pytest.raises(ValueError, match="control characters"):
        oci_workspace._normalize_remote_root("~/cache\nother", label="cache")
    with pytest.raises(ValueError, match="contains NUL"):
        filter_oci_environment({"CI": "1\x00bad"}, ("CI",))

def test_home_relative_path_expansion_supports_tilde_and_dollar_home(tmp_path: Path) -> None:
    import subprocess
    for raw in ("~/.cache/devtool", "$HOME/.cache/devtool"):
        script = "set -eu\n" + oci_workspace._expand_home_shell(raw, "x") + "\nprintf '%s\\n' \"$x\"\n"
        completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env={"HOME": str(tmp_path)})
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == str(tmp_path / ".cache/devtool")



def _write_managed_workspace_control_files(workspace: Path, workspace_id: str, *, sync_id: str = "sync-123") -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    revision = {"git_head": "abc123", "working_tree_dirty": False, "working_tree_fingerprint": "0" * 64}
    (workspace / ".devtool-workspace-id").write_text(workspace_id + "\n", encoding="utf-8")
    (workspace / ".devtool-remote-workspace.json").write_text(json.dumps({
        "schema_version": 1, "workspace_id": workspace_id, "instance_id": workspace_id,
        "mode": "managed", "remote_path": str(workspace), "last_sync_id": sync_id,
        "source_revision": revision,
    }), encoding="utf-8")
    (workspace / ".devtool-sync-state.json").write_text(json.dumps({
        "schema_version": 1, "state": "complete", "recoverable": False, "sync_id": sync_id,
        "source_revision": revision,
    }), encoding="utf-8")


def test_workspace_probe_executes_against_coherent_managed_sync(tmp_path: Path) -> None:
    import subprocess
    workspace_id = "repo-123"
    workspace = tmp_path / ".cache/devtool/workspaces" / workspace_id
    _write_managed_workspace_control_files(workspace, workspace_id)
    script = oci_workspace._workspace_probe_script("~/.cache/devtool/workspaces", workspace_id)
    env = dict(os.environ); env["HOME"] = str(tmp_path)
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env=env)
    assert completed.returncode == 0, completed.stderr
    assert f"workspace_id={workspace_id}" in completed.stdout
    assert f"workspace_real={workspace}" in completed.stdout
    assert "sync_id=sync-123" in completed.stdout


def test_workspace_probe_rejects_stale_metadata_relative_to_sync_state(tmp_path: Path) -> None:
    import subprocess
    workspace_id = "repo-123"
    workspace = tmp_path / ".cache/devtool/workspaces" / workspace_id
    _write_managed_workspace_control_files(workspace, workspace_id, sync_id="sync-old")
    state_path = workspace / ".devtool-sync-state.json"
    state = json.loads(state_path.read_text())
    state["sync_id"] = "sync-new"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    script = oci_workspace._workspace_probe_script("~/.cache/devtool/workspaces", workspace_id)
    env = dict(os.environ); env["HOME"] = str(tmp_path)
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env=env)
    assert completed.returncode != 0
    assert "stale relative to sync-state" in completed.stderr


def test_workspace_probe_rejects_symlinked_control_file(tmp_path: Path) -> None:
    import subprocess
    workspace_id = "repo-123"
    workspace = tmp_path / ".cache/devtool/workspaces" / workspace_id
    _write_managed_workspace_control_files(workspace, workspace_id)
    metadata = workspace / ".devtool-remote-workspace.json"
    real_metadata = workspace / "metadata.real.json"
    metadata.rename(real_metadata)
    metadata.symlink_to(real_metadata.name)
    script = oci_workspace._workspace_probe_script("~/.cache/devtool/workspaces", workspace_id)
    env = dict(os.environ); env["HOME"] = str(tmp_path)
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env=env)
    assert completed.returncode == 73
    assert "refusing symlink component" in completed.stderr


def test_workspace_probe_requires_owned_complete_managed_sync_metadata() -> None:
    script = oci_workspace._workspace_probe_script("~/.cache/devtool/workspaces", "repo-123")
    assert ".devtool-workspace-id" in script
    assert ".devtool-remote-workspace.json" in script
    assert ".devtool-sync-state.json" in script
    assert "metadata.get('workspace_id') != expected" in script
    assert "state.get('state') != 'complete'" in script
    assert "synchronization is not complete" in script
    assert "metadata.get('last_sync_id') != sync_id" in script
    assert "metadata.get('remote_path') != workspace_real" in script
    assert "metadata_revision != state_revision" in script


def test_structured_evidence_missing_fails_the_owning_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [(b"workspace_id=only\n", b"", 0)]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="required structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-evidence")
    snap = bus.execution_store.snapshot("run-oci04-evidence")
    assert snap is not None and snap.status == "failed"
    assert snap.nodes[0].status == "succeeded"
    assert snap.nodes[1].status == "failed"
    assert snap.nodes[1].diagnostic_ids
    assert snap.nodes[2].status == "blocked"
    assert snap.nodes[3].status == "blocked"


def test_cache_overlap_refuses_before_any_source_mutation(tmp_path: Path) -> None:
    import subprocess
    workspace_id = "repo-123"
    workspace = tmp_path / ".cache/devtool/workspaces" / workspace_id
    workspace.mkdir(parents=True)
    script = oci_workspace._cache_prepare_script(
        "~/.cache/devtool/workspaces", workspace_id, str(workspace), dry_run=False,
    )
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env={"HOME": str(tmp_path)})
    assert completed.returncode == 73
    assert "overlaps authoritative workspace" in completed.stderr
    assert not (workspace / "gradle").exists()
    assert not (workspace / "xdg").exists()


def test_cache_symlink_component_is_rejected_before_mutation(tmp_path: Path) -> None:
    import subprocess
    real = tmp_path / "real-cache"
    real.mkdir()
    (tmp_path / ".cache").symlink_to(real, target_is_directory=True)
    script = oci_workspace._cache_prepare_script(
        "~/.cache/devtool/oci/workspace-caches", "repo-123", str(tmp_path / "workspace"), dry_run=False,
    )
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env={"HOME": str(tmp_path)})
    assert completed.returncode == 73
    assert "refusing symlink component" in completed.stderr
    assert not (real / "devtool").exists()


def test_workspace_symlink_component_is_rejected_before_metadata_use(tmp_path: Path) -> None:
    import subprocess
    real_root = tmp_path / "real-workspaces"
    real_root.mkdir()
    cache = tmp_path / ".cache/devtool"
    cache.mkdir(parents=True)
    (cache / "workspaces").symlink_to(real_root, target_is_directory=True)
    script = oci_workspace._workspace_probe_script("~/.cache/devtool/workspaces", "repo-123")
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env={"HOME": str(tmp_path)})
    assert completed.returncode in {66, 73}
    # If the target directory is absent the missing-workspace refusal may happen
    # first; once present, the symlink chain must be rejected.
    target = real_root / "repo-123"
    target.mkdir()
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env={"HOME": str(tmp_path)})
    assert completed.returncode == 73
    assert "refusing symlink component" in completed.stderr


def test_explicit_empty_environment_allowlist_stays_empty_end_to_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    config = _config(); config.oci_env_allowlist = ()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-empty-env\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=0\ncache_exists=1\ncache_ready=1\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    payload = oci_workspace.oci_workspace_payload(
        config, repo_root=repo, run_id="run-oci04-empty-allowlist", environment={"CI": "1", "LANG": "C"},
    )
    assert payload["plan"]["forwarded_environment_names"] == []
    evidence = json.loads(Path(payload["evidence_artifact"]).read_text(encoding="utf-8"))
    assert evidence["forwarded_environment_names"] == []
    assert bus.execution_store.snapshot("run-oci04-empty-allowlist").status == "succeeded"


def test_config_explicit_empty_allowlist_does_not_restore_defaults(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        '[ssh]\nprofile = "tablet"\n\n[ssh.hosts.tablet]\nhost = "tablet"\n\n[ssh.hosts.tablet.workspace]\nmode = "managed"\n\n[ssh.hosts.tablet.execution]\nprovider = "ssh-proot-oci"\n\n[ssh.hosts.tablet.oci]\nenv_allowlist = []\n',
        encoding="utf-8",
    )
    config = resolve_ssh_config(["--repo", str(tmp_path)], cwd=str(tmp_path), env={})
    assert config.oci_env_allowlist == ()


def test_real_cache_prepare_reports_no_mutation_when_every_cache_is_already_ready(tmp_path: Path) -> None:
    import subprocess
    home = tmp_path / "home"; home.mkdir(exist_ok=True)
    workspace = home / ".cache/devtool/workspaces/repo-123"; workspace.mkdir(parents=True)
    cache = home / ".cache/devtool/oci/workspace-caches/repo-123"
    for name in APPROVED_CACHES:
        (cache / name).mkdir(parents=True, exist_ok=True)
    script = oci_workspace._cache_prepare_script("~/.cache/devtool/oci/workspace-caches", "repo-123", str(workspace), dry_run=False)
    env = dict(os.environ); env["HOME"] = str(home)
    completed = subprocess.run(["/bin/sh"], input=script, text=True, capture_output=True, env=env)
    assert completed.returncode == 0, completed.stderr
    assert "cache_mutated=0" in completed.stdout
    assert "cache_exists=1" in completed.stdout
    assert "cache_ready=1" in completed.stdout


def test_stale_managed_workspace_source_revision_blocks_cache_and_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-stale\nsource_revision_digest={'f'*64}\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="invalid structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-stale-source", environment={})
    snap = bus.execution_store.snapshot("run-oci04-stale-source")
    assert snap is not None and snap.status == "failed"
    assert [node.status for node in snap.nodes] == ["succeeded", "failed", "blocked", "blocked"]
    assert snap.nodes[2].blocked_by == (snap.nodes[1].node_id,)
    assert snap.nodes[3].blocked_by == (snap.nodes[2].node_id,)


def test_non_dry_cache_evidence_requires_cache_to_exist(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    _name, workspace_id = repository_identity(repo)
    workspace = f"/data/data/com.termux/files/home/.cache/devtool/workspaces/{workspace_id}"
    cache = f"/data/data/com.termux/files/home/.cache/devtool/oci/workspace-caches/{workspace_id}"
    bus = _patch_bus(monkeypatch, tmp_path)
    _QueuePopen.calls.clear(); _QueuePopen.outputs = [
        ((f"workspace_id={workspace_id}\nworkspace_real={workspace}\nsync_id=sync-cache-missing\nsource_revision_digest={EMPTY_SOURCE_REVISION_DIGEST}\n").encode(), b"", 0),
        ((f"cache_real={cache}\ncache_mutated=0\ncache_exists=0\ncache_ready=1\n").encode(), b"", 0),
    ]
    monkeypatch.setattr(proot_oci.subprocess, "Popen", _QueuePopen)
    with pytest.raises(ValueError, match="invalid structured evidence"):
        oci_workspace.oci_workspace_payload(_config(), repo_root=repo, run_id="run-oci04-cache-exists-contract", environment={})
    snap = bus.execution_store.snapshot("run-oci04-cache-exists-contract")
    assert snap is not None and snap.nodes[2].status == "failed"
