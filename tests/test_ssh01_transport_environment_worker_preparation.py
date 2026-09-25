from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.core.events import TransferProgressChanged, event_from_dict
from devtool.core.events.listeners import event_to_dict
from devtool import ssh_environment, ssh_multiplex, ssh_prepare, ssh_returns, ssh_tmux_runner, ssh_transport, ssh_workspace
from devtool.ssh_tmux_runner import SSHRunnerConfig, _translate_env_for_repo
from devtool.ssh_worker import SSHWorkerProbe


def cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a1", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True,
        sync_excludes=(), bootstrap_policy="missing", operation_id="ssh01-op",
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def probe(*, platform: str = "termux", tar: bool = True, zstd: bool = True) -> SSHWorkerProbe:
    prefix = "/data/data/com.termux/files/usr" if platform == "termux" else "/usr"
    tools = {
        "python": f"{prefix}/bin/python3", "tmux": f"{prefix}/bin/tmux",
        "rsync": f"{prefix}/bin/rsync", "git": f"{prefix}/bin/git",
        "tar": f"{prefix}/bin/tar" if tar else "", "zstd": f"{prefix}/bin/zstd" if zstd else "",
        "java": f"{prefix}/bin/java", "gradle": f"{prefix}/bin/gradle",
        "node": f"{prefix}/bin/node", "npm": f"{prefix}/bin/npm", "go": f"{prefix}/bin/go",
        "cmake": f"{prefix}/bin/cmake", "ninja": f"{prefix}/bin/ninja", "aapt2": "", "aidl": "",
    }
    return SSHWorkerProbe(
        profile="tablet", destination="u0_a1@tablet", port=8022, platform=platform,
        os_name="Linux", architecture="aarch64", cpu_count=8, memory_kib=8_000_000,
        free_storage_kib=16_000_000, home="/data/data/com.termux/files/home" if platform == "termux" else "/home/u",
        prefix=prefix, shell=f"{prefix}/bin/bash", package_manager="pkg" if platform == "termux" else "apt-get",
        tools=tools, versions={}, bootstrap_policy="missing", tmux_required=True,
    )


def spec(repo: Path) -> ssh_workspace.SSHWorkspaceSpec:
    return ssh_workspace.SSHWorkspaceSpec(
        "tablet", "u0_a1@tablet", "managed", str(repo), "repo", "repo-id", "repo-id",
        "/home/u/ws", "/home/u/ws/repo-id", True, "incremental", (),
    )


def status(*, exists: bool, marker: str = "repo-id") -> ssh_workspace.SSHWorkspaceStatus:
    return ssh_workspace.SSHWorkspaceStatus(
        exists, exists, False, True, "/home/u/ws", "/home/u/ws/repo-id" if exists else "",
        marker if exists else "", {}, "ready" if exists else "missing",
    )


def test_cold_seed_selects_archive_then_reconciliation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    for i in range(70): (repo / f"f{i}.txt").write_text("x")
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", "0")
    monkeypatch.setattr(ssh_transport, "_local_tool", lambda name: f"/usr/bin/{name}")
    plan = ssh_transport.seed_plan(spec(repo), status(exists=False), probe(), remote_bytes=0)
    assert plan.use_archive is True
    assert plan.mode == "archive-seed+reconcile"
    assert plan.source_files == 70


def test_warm_workspace_prefers_rsync_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "big.bin").write_bytes(b"x" * 4096)
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", "1")
    monkeypatch.setattr(ssh_transport, "_local_tool", lambda name: f"/usr/bin/{name}")
    plan = ssh_transport.seed_plan(spec(repo), status(exists=True), probe(), remote_bytes=4096)
    assert plan.use_archive is False
    assert plan.mode == "rsync-delta"
    assert "already populated" in plan.reason


def test_partial_destination_can_still_use_archive_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "big.bin").write_bytes(b"x" * 8192)
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", "1")
    monkeypatch.setattr(ssh_transport, "_local_tool", lambda name: f"/usr/bin/{name}")
    plan = ssh_transport.seed_plan(spec(repo), status(exists=True), probe(), remote_bytes=1024)
    assert plan.use_archive is True
    assert plan.destination_coverage < 0.5


def test_nested_git_boundary_is_excluded_from_parent_transport(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "a.txt").write_text("a")
    child = repo / "child"; child.mkdir(); (child / ".git").mkdir(); (child / "secret.bin").write_bytes(b"xx")
    stats = ssh_transport.source_stats(repo, ())
    assert "child/***" in stats.nested_git_excludes
    assert stats.files == 1


def test_escaping_symlink_disables_archive_seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); outside = tmp_path / "outside"; outside.write_text("x")
    (repo / "escape").symlink_to(outside)
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", "0")
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_FILES", "0")
    monkeypatch.setattr(ssh_transport, "_local_tool", lambda name: f"/usr/bin/{name}")
    plan = ssh_transport.seed_plan(spec(repo), status(exists=False), probe())
    assert plan.use_archive is False
    assert "symlinks" in plan.reason


def _patch_sync(monkeypatch: pytest.MonkeyPatch, repo: Path, *, archive_ok: bool, rsync_rc: int = 0) -> None:
    st_before = status(exists=False)
    st_ready = status(exists=True)
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **k: st_before if not getattr(_patch_sync, "seen", False) else st_ready)
    _patch_sync.seen = False
    def prepared(*a, **k):
        _patch_sync.seen = True
        return st_ready
    monkeypatch.setattr(ssh_workspace, "prepare_workspace", prepared)
    monkeypatch.setattr(ssh_workspace, "_remote_workspace_bytes", lambda *a, **k: 0)
    monkeypatch.setattr(ssh_workspace, "_write_sync_state", lambda *a, **k: None)
    monkeypatch.setattr(ssh_workspace, "_write_remote_metadata", lambda *a, **k: None)
    monkeypatch.setattr(ssh_workspace, "_source_revision", lambda *a, **k: {"git_head":"abc"})
    monkeypatch.setattr(ssh_workspace, "_rsync_argv", lambda *a, **k: ["rsync"])
    monkeypatch.setattr(ssh_workspace, "_run_rsync", lambda *a, **k: subprocess.CompletedProcess([],rsync_rc,stdout=">f+++++++++|10|10|a.txt\n",stderr="connection lost" if rsync_rc else ""))
    monkeypatch.setattr(ssh_workspace, "federation_transport_identity", lambda *a: {"federated":True,"verified":True,"start_commit":"abc"})
    monkeypatch.setattr(ssh_workspace, "seed_plan", lambda *a, **k: ssh_transport.SSHSeedPlan(
        "archive-seed+reconcile", True, "cold", 1, 10, 0, 0.0,
        "/bin/tar", "/bin/zstd", "/bin/tar", "/bin/zstd", (),
    ))
    monkeypatch.setattr(ssh_workspace, "run_archive_seed", lambda *a, **k: {"ok":archive_ok,"used":True,"mode":"archive-seed","detail":"ok" if archive_ok else "failed"})
    monkeypatch.setattr("devtool.ssh_resilience.acquire_workspace_lease", lambda *a, **k: SimpleNamespace(as_dict=lambda:{"lease":"x"}))
    monkeypatch.setattr("devtool.ssh_resilience.release_workspace_lease", lambda *a, **k: True)


def test_authoritative_sync_records_archive_plus_reconcile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); (repo/"a.txt").write_text("a")
    _patch_sync(monkeypatch, repo, archive_ok=True)
    result=ssh_workspace.sync_workspace(cfg(), spec(repo), worker=probe())
    assert result["ok"] is True
    assert result["transport_mode"] == "archive-seed+reconcile"
    assert result["transport"]["reconciliation"]["complete"] is True
    assert result["transport"]["telemetry"]["transferred_bytes"] == 10
    assert result["transport"]["telemetry"]["eta_seconds"] == 0.0
    assert result["transport"]["telemetry"]["current_path"] == "a.txt"
    assert result["federation_transport_identity"]["verified"] is True


def test_archive_failure_falls_back_to_successful_rsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); (repo/"a.txt").write_text("a")
    _patch_sync(monkeypatch, repo, archive_ok=False)
    result=ssh_workspace.sync_workspace(cfg(), spec(repo), worker=probe())
    assert result["ok"] is True
    assert result["transport_mode"] == "rsync-delta"
    assert result["transport"]["archive_fallback"] == "rsync-delta"


def test_partial_destination_still_uses_archive_seed_then_reconcile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); (repo/"payload.bin").write_bytes(b"x" * 4096)
    monkeypatch.setenv("DEVTOOL_SSH_ARCHIVE_SEED_MIN_BYTES", "1")
    monkeypatch.setattr(ssh_transport, "_local_tool", lambda name: f"/usr/bin/{name}")
    plan=ssh_transport.seed_plan(spec(repo), status(exists=True), probe(), remote_bytes=512)
    assert plan.use_archive is True
    assert 0 < plan.destination_coverage < 0.50


def test_interrupted_reconciliation_remains_partial_and_recoverable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); (repo/"a.txt").write_text("a")
    _patch_sync(monkeypatch, repo, archive_ok=True, rsync_rc=23)
    result=ssh_workspace.sync_workspace(cfg(), spec(repo), worker=probe())
    assert result["ok"] is False
    assert result["state"] == "partial"
    assert result["recoverable"] is True
    assert result["transport"]["reconciliation"]["complete"] is False


def test_rsync_delete_and_nested_git_excludes_are_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); child=repo/"child"; child.mkdir(); (child/".git").mkdir()
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda:"/usr/bin/rsync")
    argv=ssh_workspace._rsync_argv(cfg(), spec(repo), dry_run=False, worker=probe())
    assert "--delete-delay" in argv
    assert "child/***" in argv


def test_frozen_federation_source_drift_fails_before_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir()
    monkeypatch.setattr("devtool.invocation_origin.federation_lineage_from_environment", lambda:{"git_lineage":{"start_commit":"expected","repository_id":"rid"}})
    monkeypatch.setattr("devtool.federation_ownership.federation_operational_owner_from_environment", lambda root:{"repository":"child"})
    monkeypatch.setattr("devtool.git_state.collect_git_state", lambda root:SimpleNamespace(head={"sha":"changed"},repository_id="rid"))
    with pytest.raises(ValueError, match="Frozen federation source changed"):
        ssh_transport.federation_transport_identity(repo)


def test_transfer_event_roundtrips_as_node_progress() -> None:
    event=TransferProgressChanged(run_id="r",node_id="n",mode="archive-seed",source_bytes=100,transferred_bytes=25,source_files=4,transferred_files=1,remaining_files=3,workspace_sync_id="s")
    assert event.percent == 25.0
    payload=event_to_dict(event)
    decoded=event_from_dict(payload)
    assert isinstance(decoded, TransferProgressChanged)
    assert decoded.mode == "archive-seed"
    assert decoded.workspace_sync_id == "s"


def test_remote_environment_policy_drops_host_absolute_paths_and_overlay_wins(tmp_path: Path) -> None:
    local=tmp_path/"repo"; local.mkdir()
    env={"HOME":"/data/local/home","PATH":"/local/bin","JAVA_HOME":"/local/jdk","SAFE":"spec","REPO_FILE":str(local/"x")}
    remote=_translate_env_for_repo(env,local,"/remote/repo",config=cfg(environment_policy="remote"))
    assert "HOME" not in remote and "PATH" not in remote and "JAVA_HOME" not in remote
    assert remote["SAFE"] == "spec" and remote["REPO_FILE"] == "/remote/repo/x"
    overlay=_translate_env_for_repo(env,local,"/remote/repo",config=cfg(environment_policy="overlay",environment_overlay={"SAFE":"overlay"}))
    assert overlay["SAFE"] == "overlay"
    hermetic=_translate_env_for_repo({"SAFE":"x","CI":"1","DEVTOOL_X":"y"},local,"/remote/repo",config=cfg(environment_policy="hermetic"))
    assert "SAFE" not in hermetic and hermetic["CI"] == "1" and hermetic["DEVTOOL_X"] == "y"


def test_environment_probe_reports_termux_existing_and_redacts_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout='''platform=termux\nos_name=Linux\narchitecture=aarch64\nhome=/data/data/com.termux/files/home\nprefix=/data/data/com.termux/files/usr\npath=/data/data/com.termux/files/usr/bin\ntmpdir=/data/data/com.termux/files/usr/tmp\nshell=/data/data/com.termux/files/usr/bin/bash\ntool_python=/data/data/com.termux/files/usr/bin/python\nversion_python=Python 3.13\ntool_rsync=/data/data/com.termux/files/usr/bin/rsync\ntool_git=/data/data/com.termux/files/usr/bin/git\ntool_tmux=/data/data/com.termux/files/usr/bin/tmux\ntool_tar=/data/data/com.termux/files/usr/bin/tar\ntool_zstd=/data/data/com.termux/files/usr/bin/zstd\njava_home=/data/data/com.termux/files/usr/lib/jvm/openjdk-21\ngradle_user_home=/data/data/com.termux/files/home/.gradle\nandroid_sdk_root=/data/data/com.termux/files/home/.cache/devtool/android-sdk\ndevtool_cache_root=/data/data/com.termux/files/home/.cache/devtool\ngradle_cache_root=/data/data/com.termux/files/home/.gradle\nnpm_cache_root=/data/data/com.termux/files/home/.npm\ngo_cache_root=/data/data/com.termux/files/home/.cache/go-build\ndevtool_cache_kib=10\ngradle_cache_kib=20\nworkspace_root_accessible=yes\nrun_root_accessible=yes\n'''
    monkeypatch.setattr(ssh_environment,"run_with_multiplex_fallback",lambda *a,**k:subprocess.CompletedProcess([],0,stdout=stdout,stderr=""))
    payload=ssh_environment.environment_payload(cfg(environment_overlay={"API_TOKEN":"secret"}))
    assert payload["tools"]["python"]["provenance"] == "termux-existing"
    assert payload["android"]["provenance"] == "devtool-cache"
    assert payload["overlay"]["API_TOKEN"] == "<redacted>"


def test_environment_probe_reports_normal_linux_tools_as_remote_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    stdout='''platform=linux\nos_name=Linux\narchitecture=x86_64\nhome=/home/dev\nprefix=/usr\npath=/usr/local/bin:/usr/bin\ntmpdir=/tmp\nshell=/bin/bash\ntool_python=/usr/bin/python3\nversion_python=Python 3.12\ntool_java=/usr/bin/java\nversion_java=openjdk 21\ntool_gradle=/usr/bin/gradle\nversion_gradle=Gradle 9.0\ntool_rsync=/usr/bin/rsync\ntool_git=/usr/bin/git\ntool_tmux=/usr/bin/tmux\ntool_tar=/usr/bin/tar\ntool_zstd=/usr/bin/zstd\njava_home=/usr/lib/jvm/java-21\ngradle_user_home=/home/dev/.gradle\nandroid_sdk_root=/opt/android-sdk\ndevtool_cache_root=/home/dev/.cache/devtool\ngradle_cache_root=/home/dev/.gradle\nnpm_cache_root=/home/dev/.npm\ngo_cache_root=/home/dev/.cache/go-build\ndevtool_cache_kib=0\ngradle_cache_kib=0\nworkspace_root_accessible=yes\nrun_root_accessible=yes\n'''
    monkeypatch.setattr(ssh_environment,"run_with_multiplex_fallback",lambda *a,**k:subprocess.CompletedProcess([],0,stdout=stdout,stderr=""))
    payload=ssh_environment.environment_payload(cfg())
    assert payload["worker"]["platform"] == "linux"
    assert payload["tools"]["java"]["provenance"] == "remote-existing"
    assert payload["tools"]["gradle"]["provenance"] == "remote-existing"
    assert payload["android"]["sdk_root"] == "/opt/android-sdk"


def test_multiplex_is_operation_scoped_and_gracefully_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    a=cfg(operation_id="a"); b=cfg(operation_id="b")
    assert ssh_multiplex.control_path(a) != ssh_multiplex.control_path(b)
    calls=[]
    def run(argv,**kwargs):
        calls.append(list(argv))
        if len(calls)==1:
            return subprocess.CompletedProcess(argv,255,stdout="",stderr="mux_client: master is dead")
        return subprocess.CompletedProcess(argv,0,stdout="ok",stderr="")
    monkeypatch.setattr(subprocess,"run",run)
    argv=["ssh",*ssh_multiplex.multiplex_options(a),a.destination,"true"]
    done=ssh_multiplex.run_with_multiplex_fallback(a,argv,capture_output=True,text=True,check=False)
    assert done.returncode == 0 and len(calls)==2
    assert not any("ControlMaster=" in x for x in calls[1])
    assert ssh_multiplex.evidence(a).fallback_reason


def test_multiplex_registers_best_effort_process_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    config = cfg(operation_id="cleanup")
    ssh_multiplex._REGISTERED_CONFIGS.clear()
    closed=[]
    monkeypatch.setattr(ssh_multiplex, "close_master", lambda value: closed.append(value.operation_id) or True)
    options=ssh_multiplex.multiplex_options(config)
    assert any("ControlPath=" in item for item in options)
    assert ssh_multiplex._REGISTERED_CONFIGS
    ssh_multiplex._cleanup_registered_masters()
    assert closed == ["cleanup"]
    assert not ssh_multiplex._REGISTERED_CONFIGS


def test_multiplex_identity_does_not_cross_destination_or_port() -> None:
    base=cfg(operation_id="same")
    other_host=cfg(operation_id="same", host="other")
    other_port=cfg(operation_id="same", port=2222)
    assert ssh_multiplex.control_path(base) != ssh_multiplex.control_path(other_host)
    assert ssh_multiplex.control_path(base) != ssh_multiplex.control_path(other_port)



def test_rsync_machine_metrics_report_actual_delta_bytes() -> None:
    metrics = ssh_workspace._parse_rsync_metrics(
        ">f+++++++++|10|10|a.txt\n.d..t......|0|0|dir/\n>f.st......|3|100|b.bin\n"
    )
    assert metrics == {"transferred_bytes": 13, "changed_source_bytes": 110, "metric_records": 3}
    assert ssh_workspace._parse_rsync_metrics("")["transferred_bytes"] == 0
    assert ssh_workspace._parse_rsync_changes(">f+++++++++|10|10|a.txt\n") == [
        {"code": ">f+++++++++", "path": "a.txt"}
    ]


def test_whole_process_tmux_closes_operation_master(monkeypatch: pytest.MonkeyPatch) -> None:
    config = cfg(mode="devtool", detached=True, operation_id="whole-process", session="ssh01-session")
    monkeypatch.setattr(ssh_tmux_runner, "resolve_ssh_config", lambda *a, **k: config)
    monkeypatch.setattr(ssh_tmux_runner, "_forwarded_argv", lambda a: ["test"])
    monkeypatch.setattr(ssh_tmux_runner, "_ensure_session", lambda *a, **k: config.session)
    monkeypatch.setattr(ssh_tmux_runner, "_preflight_local_transport", lambda *a, **k: None)
    monkeypatch.setattr(ssh_tmux_runner, "_remote_tmux_preflight", lambda *a, **k: None)
    monkeypatch.setattr(ssh_tmux_runner, "stage_forwarded_artifacts", lambda *a, **k: (["test"], []))
    monkeypatch.setattr(ssh_tmux_runner, "build_remote_tmux_script", lambda *a, **k: "true")
    monkeypatch.setattr(ssh_tmux_runner, "_build_ssh_remote_sh_argv", lambda *a, **k: ["ssh", "worker", "true"])
    monkeypatch.setattr(ssh_tmux_runner, "reattach_command", lambda *a, **k: ["ssh", "worker"])
    ran=[]; closed=[]
    monkeypatch.setattr(ssh_multiplex, "run_with_multiplex_fallback", lambda c, argv, **k: ran.append(argv) or subprocess.CompletedProcess(argv, 0))
    monkeypatch.setattr(ssh_multiplex, "close_master", lambda c: closed.append(c.operation_id) or True)
    assert ssh_tmux_runner.run_ssh_tmux(["devtool", "test"]) == 0
    assert ran == [["ssh", "worker", "true"]]
    assert closed == ["whole-process"]

def test_adaptive_return_uses_archive_only_when_remote_tools_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir()
    bundle=ssh_returns.create_return_bundle(cfg(),local_root=repo,host_cwd=str(repo),remote_root="/remote/repo",session="s",project_config=SimpleNamespace(runner_options={}),spec_metadata={})
    monkeypatch.setattr(ssh_returns,"_local_rsync",lambda:"/usr/bin/rsync")
    archive=ssh_returns.shell_return_fragment(cfg(),bundle,remote_rsync="/usr/bin/rsync",remote_zstd="/usr/bin/zstd",remote_python="/usr/bin/python3")
    assert "archive-zstd" in archive and "source_bytes=" in archive
    assert 'tar -C ' in archive and 'return_rc=$?; fi;' in archive
    assert "transferred_bytes=%s" in archive
    rsync=ssh_returns.shell_return_fragment(cfg(),bundle,remote_rsync="/usr/bin/rsync")
    assert "archive-zstd" not in rsync and "rsync-delta" in rsync


def test_prepare_reuses_existing_runtime_and_never_syncs_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo=tmp_path/"repo"; repo.mkdir(); (repo/".devtool.toml").write_text('[project]\nkind="generic"\n')
    pr=probe()
    monkeypatch.setattr(ssh_prepare,"probe_worker",lambda c:pr)
    monkeypatch.setattr(ssh_prepare,"environment_payload",lambda *a,**k:{
        "tools":{
            "java":{"path":"/usr/bin/java","version":"21","provenance":"remote-existing"},
            "gradle":{"path":"/usr/bin/gradle","version":"9.0","provenance":"remote-existing"},
        },
        "android":{"sdk_root":"/opt/android-sdk","provenance":"remote-existing"},"ok":True,
    })
    monkeypatch.setattr(ssh_prepare,"load_project_config",lambda *a,**k:SimpleNamespace())
    monkeypatch.setattr(ssh_prepare,"build_specs",lambda *a,**k:[])
    monkeypatch.setattr(ssh_prepare,"ensure_remote_toolchain",lambda *a,**k:{"ok":True,"kind":"generic","detail":"reuse"})
    monkeypatch.setattr(ssh_prepare,"resolve_workspace_spec",lambda *a,**k:spec(repo))
    monkeypatch.setattr(ssh_prepare,"workspace_status",lambda *a,**k:status(exists=False))
    payload=ssh_prepare.prepare_worker_target(cfg(),repo,dry_run=False)
    assert payload["ok"] is True
    assert payload["bootstrap"]["changed"] is False
    assert all(v is False for v in payload["source_operations"].values())
    assert payload["workspace"]["mutated"] is False
    inventory={item["component"]:item for item in payload["toolchain_inventory"]}
    assert inventory["java"]["classification"] == "reusable"
    assert inventory["gradle"]["classification"] == "reusable"
    assert inventory["android-sdk"]["classification"] == "reusable"
    assert "needs-install" in payload["classification_vocabulary"]


def test_cli_declares_env_and_prepare_contracts() -> None:
    parser=build_parser()
    env=parser.parse_args(["ssh","env","tablet","--target","devtool","--format","json"])
    assert env.ssh_command == "env" and env.ssh_env_target == "devtool"
    prep=parser.parse_args(["ssh","prepare","tablet","--target","devtool","--dry-run"])
    assert prep.ssh_command == "prepare" and prep.ssh_prepare_dry_run is True
