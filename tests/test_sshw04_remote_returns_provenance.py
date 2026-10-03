from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from devtool import ssh_returns, ssh_workspace, test_runstore
from devtool.models import CommandSpec, OperationResult
from devtool.ssh_execution import SSHExecutionPreparation
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool.ssh_worker import SSHWorkerProbe
from devtool.ssh_workspace import SSHWorkspaceSpec


def _cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0_a123", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental", sync_delete=True, sync_default_excludes=True,
        sync_excludes=(), bootstrap_policy="missing",
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _probe() -> SSHWorkerProbe:
    return SSHWorkerProbe(
        profile="tablet", destination="u0_a123@tablet", port=8022,
        platform="termux", os_name="Linux", architecture="aarch64",
        cpu_count=8, memory_kib=8 * 1024 * 1024, free_storage_kib=32 * 1024 * 1024,
        home="/data/data/com.termux/files/home", prefix="/data/data/com.termux/files/usr",
        shell="/data/data/com.termux/files/usr/bin/bash", package_manager="pkg",
        tools={"python":"/usr/bin/python","tmux":"/usr/bin/tmux","rsync":"/usr/bin/rsync","git":"/usr/bin/git","java":"","gradle":""},
        versions={}, bootstrap_policy="missing", tmux_required=True,
    )


def test_default_sync_excludes_returned_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    spec = ssh_workspace.resolve_workspace_spec(_cfg(), repo, remote_home="/home/u")
    assert ".devtool/ssh-return/" in spec.excludes


def test_sync_result_carries_unique_source_sync_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    spec = SSHWorkspaceSpec("tablet","u0_a123@tablet","managed",str(repo),"repo","repo-id","repo-id","/home/u/ws","/home/u/ws/repo-id",True,"incremental",())
    status = ssh_workspace.SSHWorkspaceStatus(True, True, False, True, "/home/u/ws", "/home/u/ws/repo-id", "repo-id", {}, "ready")
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _probe())
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: status)
    monkeypatch.setattr(ssh_workspace, "prepare_workspace", lambda *a, **kw: status)
    monkeypatch.setattr(ssh_workspace, "_rsync_argv", lambda *a, **kw: ["rsync"])
    monkeypatch.setattr(ssh_workspace, "_write_remote_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(ssh_workspace, "_source_revision", lambda root: {"git_head":"abc","working_tree_dirty":True,"working_tree_fingerprint":"ff"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=">f+++++++++|src/Main.kt\n", stderr=""))
    result = ssh_workspace.sync_workspace(_cfg(), spec)
    assert result["sync_id"].startswith("sync-repo-id-")
    assert result["source_revision"]["git_head"] == "abc"


def test_return_bundle_manifest_and_patterns(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "module").mkdir()
    cfg = _cfg(operation_id="op-1")
    project = SimpleNamespace(runner_options={"artifacts": {"paths": ["build/custom/*.apk"]}})
    bundle = ssh_returns.create_return_bundle(
        cfg, local_root=repo, host_cwd=str(repo / "module"), remote_root="/remote/repo",
        session="devtool-tablet-build", project_config=project,
        spec_metadata={"test_report":"reports/junit.xml"}, source_sync_id="sync-123",
    )
    payload = json.loads(bundle.manifest_path.read_text())
    assert payload["source_sync_id"] == "sync-123"
    assert payload["remote_root"] == "/remote/repo"
    assert "**/build/outputs/***" in bundle.patterns
    assert "module/reports/junit.xml" in bundle.patterns
    assert "module/build/custom/*.apk" in bundle.patterns
    assert bundle.project_root == bundle.repo_root / "module"


def test_return_fragment_is_resumable_and_does_not_replace_build_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bundle = ssh_returns.create_return_bundle(
        _cfg(operation_id="op"), local_root=repo, host_cwd=str(repo), remote_root="/remote/repo",
        session="s", project_config=SimpleNamespace(runner_options={}), spec_metadata={}, source_sync_id="sync-x",
    )
    monkeypatch.setattr(ssh_returns, "_local_rsync", lambda: "/usr/bin/rsync")
    fragment = ssh_returns.shell_return_fragment(_cfg(), bundle, remote_rsync="/data/data/com.termux/files/usr/bin/rsync")
    assert "--partial" in fragment
    assert "--delay-updates" in fragment
    assert "return_rc=$?" in fragment
    assert 'if [ "$rc" -eq 125 ]' in fragment
    assert "rm -rf --" not in fragment
    assert "blocked-existing-local-mirror" in fragment
    assert "Run SSH return recovery so LIFE/RUN/RER can revalidate" in fragment
    assert "transfer.status.tmp" in fragment
    assert "mv -f --" in fragment
    assert "transfer.status" in fragment


def test_load_return_state_marks_partial_as_recoverable(tmp_path: Path) -> None:
    root = tmp_path / "return"; root.mkdir()
    manifest = root / "manifest.json"; status = root / "transfer.status"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle","remote_root":"/remote/repo","source_sync_id":"sync-1"}), encoding="utf-8")
    status.write_text("returncode=23\nstate=partial\n", encoding="utf-8")
    state = ssh_returns.load_return_state({"ssh_return_manifest":str(manifest),"ssh_return_status_file":str(status)})
    assert state["state"] == "partial"
    assert state["recoverable"] is True
    assert state["source_sync_id"] == "sync-1"


def test_runstore_records_remote_provenance_logs_reports_and_build_artifacts(tmp_path: Path) -> None:
    metadata = {
        "test_spec_id":"unit",
        "test_result":{"schema_version":1,"id":"unit","suites":[],"artifacts":[],"metadata":{"evidence":"report"}},
        "execution_mode":"ssh-command","ssh_profile":"tablet","ssh_destination":"u@tablet",
        "ssh_workspace_id":"repo-id","ssh_workspace_path":"/remote/repo","ssh_source_sync_id":"sync-1",
        "ssh_source_revision":{"git_head":"abc"},"ssh_session":"devtool-unit",
        "ssh_return":{"state":"complete","recoverable":False},"ssh_return_manifest":"/phone/manifest.json",
        "tests":{"report_files":[".devtool/ssh-return/run/s/repo/build/test-results/a.xml"]},
        "artifacts":{"items":[{"path":".devtool/build-artifacts/app.apk","type":"apk","sha256":"deadbeef","valid":True}]},
    }
    result = OperationResult("test:unit", ["pytest"], 0, 1.0, "", [], log_path="/phone/run.log", metadata=metadata)
    payload = test_runstore.build_run_payload(tmp_path, [result], run_id="run-1")
    assert payload is not None
    op = payload["operations"][0]
    assert op["operation_id"] == "unit"
    assert op["test_result"]["id"] == "unit"
    assert op["provenance"]["source_sync_id"] == "sync-1"
    assert op["log_path"] == "/phone/run.log"
    assert op["reports"]
    assert op["build_artifacts"][0]["type"] == "apk"
    assert payload["build_artifacts"][0]["path"] == ".devtool/build-artifacts/app.apk"
    assert payload["logs"] == ["/phone/run.log"]


def test_recovery_rejects_manifest_for_other_destination(tmp_path: Path) -> None:
    root = tmp_path / ".devtool" / "ssh-return" / "run-1" / "session"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "kind":"ssh-return-bundle","destination":"other@host","remote_root":"/remote/repo",
        "local_return_repo_root":str(root / "repo"),"patterns":[],"run_id":"run-1","session":"session",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match profile destination"):
        ssh_returns.recover_return_bundles(_cfg(), tmp_path, run_id="run-1")


def test_wrapped_managed_phase_declares_return_bundle_and_sync_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import ssh_execution, ssh_tmux_runner
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".git").mkdir()
    workspace = SSHWorkspaceSpec(
        "tablet", "u0_a123@tablet", "managed", str(repo), "repo", "repo-id", "repo-id",
        "/remote/workspaces", "/remote/workspaces/repo-id", True, "incremental", (),
    )
    prep = SSHExecutionPreparation(
        remote_root="/remote/workspaces/repo-id", worker=_probe(), workspace=workspace,
        workspace_status={"canonical_path":"/remote/workspaces/repo-id"}, bootstrap={},
        sync={"ok":True,"change_count":2,"sync_id":"sync-xyz","source_revision":{"git_head":"abc"}},
        toolchain={"ok":True,"kind":"generic","detail":"ready"},
    )
    monkeypatch.setattr(ssh_execution, "prepare_ssh_command_execution", lambda *a, **kw: prep)
    monkeypatch.setattr(ssh_tmux_runner, "_preflight_local_transport", lambda config: None)
    monkeypatch.setattr(ssh_tmux_runner, "_remote_tmux_preflight", lambda config: None)
    monkeypatch.setattr(ssh_tmux_runner, "_assert_session_inactive", lambda config, session: None)
    monkeypatch.setattr(ssh_returns, "_local_rsync", lambda: "/usr/bin/rsync")
    spec = CommandSpec("test:unit", ["python3", "-c", "print('ok')"], cwd=str(repo), metadata={"runner":"python","test_spec_id":"unit"})
    wrapped = ssh_tmux_runner.wrap_specs_for_ssh_commands(
        [spec], _cfg(operation_id="op"), project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=False,
    )
    meta = wrapped[0].metadata
    assert meta["ssh_source_sync_id"] == "sync-xyz"
    assert meta["ssh_return_manifest"].endswith("manifest.json")
    assert meta["ssh_return_project_root"].endswith("/repo")
    script = wrapped[0].argv[-1]
    assert "--include=**/build/test-results/***" in script
    assert "transfer.status" in script


def test_run_specs_parses_returned_remote_report_and_attaches_provenance(tmp_path: Path) -> None:
    from devtool.runners import run_specs
    from devtool.models import ProjectConfig
    returned_repo = tmp_path / ".devtool" / "ssh-return" / "run" / "session" / "repo"
    report_dir = returned_repo / "target" / "surefire-reports"
    report_dir.mkdir(parents=True)
    (report_dir / "TEST-unit.xml").write_text(
        '<testsuite name="remote"><testcase classname="C" name="one"/></testsuite>', encoding="utf-8"
    )
    manifest = returned_repo.parent / "manifest.json"
    status = returned_repo.parent / "transfer.status"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle","remote_root":"/remote/repo","source_sync_id":"sync-77"}), encoding="utf-8")
    status.write_text("returncode=0\nstate=complete\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="maven")
    spec = CommandSpec(
        "test:unit", ["sh", "-c", "true"], cwd=str(tmp_path), metadata={
            "runner":"maven","test_framework":"maven","report_parser":"maven-surefire",
            "report_parsers":["maven-surefire"],"project_root":str(tmp_path),"test_spec_id":"unit",
            "execution_mode":"ssh-command","ssh_profile":"tablet","ssh_destination":"u@tablet",
            "ssh_workspace_id":"repo-id","ssh_workspace_path":"/remote/repo","ssh_source_sync_id":"sync-77",
            "ssh_session":"session","ssh_return_manifest":str(manifest),"ssh_return_status_file":str(status),
            "ssh_return_repo_root":str(returned_repo),"ssh_return_project_root":str(returned_repo),
        },
    )
    result = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)[0][0]
    assert result.metadata["tests"]["total"] == 1
    assert result.metadata["test_result"]["id"] == "unit"
    provenance = result.metadata["test_result"]["metadata"]["execution_provenance"]
    assert provenance["source_sync_id"] == "sync-77"
    assert provenance["return_state"] == "complete"


def test_run_specs_promotes_returned_remote_artifact_into_local_managed_output(tmp_path: Path) -> None:
    from devtool.runners import run_specs
    from devtool.models import ProjectConfig
    returned_repo = tmp_path / ".devtool" / "ssh-return" / "run" / "build" / "repo"
    jar = returned_repo / "build" / "libs" / "app.jar"
    jar.parent.mkdir(parents=True)
    jar.write_bytes(b"jar-bytes")
    manifest = returned_repo.parent / "manifest.json"
    status = returned_repo.parent / "transfer.status"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle","remote_root":"/remote/repo","source_sync_id":"sync-art"}), encoding="utf-8")
    status.write_text("returncode=0\nstate=complete\n", encoding="utf-8")
    config = ProjectConfig(
        repo_root=tmp_path, project_root=str(tmp_path), kind="gradle",
        runner_options={"artifacts":{"paths":["build/libs/*.jar"],"output_directory":".devtool/build-artifacts"}},
    )
    spec = CommandSpec(
        "package", ["sh", "-c", "true"], cwd=str(tmp_path), metadata={
            "runner":"gradle","collect_artifacts":True,"project_root":str(tmp_path),
            "execution_mode":"ssh-command","ssh_profile":"tablet","ssh_destination":"u@tablet",
            "ssh_workspace_id":"repo-id","ssh_workspace_path":"/remote/repo","ssh_source_sync_id":"sync-art",
            "ssh_session":"build","ssh_return_manifest":str(manifest),"ssh_return_status_file":str(status),
            "ssh_return_repo_root":str(returned_repo),"ssh_return_project_root":str(returned_repo),
        },
    )
    result = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)[0][0]
    assert result.returncode == 0
    items = result.metadata["artifacts"]["items"]
    assert len(items) == 1
    local = tmp_path / items[0]["path"]
    assert local.is_file() and local.read_bytes() == b"jar-bytes"
    assert ".devtool/build-artifacts" in str(local)


def test_partial_return_is_warning_not_build_failure(tmp_path: Path) -> None:
    from devtool.runners import run_specs
    from devtool.models import ProjectConfig
    root = tmp_path / ".devtool" / "ssh-return" / "run" / "phase"
    root.mkdir(parents=True)
    manifest = root / "manifest.json"; status = root / "transfer.status"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle","remote_root":"/remote/repo","source_sync_id":"sync-p"}), encoding="utf-8")
    status.write_text("returncode=23\nstate=partial\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="generic")
    spec = CommandSpec("check", ["sh", "-c", "true"], cwd=str(tmp_path), metadata={
        "execution_mode":"ssh-command","ssh_destination":"u@tablet","ssh_return_manifest":str(manifest),
        "ssh_return_status_file":str(status),"ssh_return_root":str(root),
    })
    result = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)[0][0]
    assert result.returncode == 0
    assert result.metadata["ssh_return_recovery_required"] is True
    assert any(issue.code == "SSH_RETURN_PARTIAL" and issue.severity == "warning" for issue in result.issues)


def test_recovery_retries_manifest_with_verified_workspace_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import ssh_worker
    root = tmp_path / ".devtool" / "ssh-return" / "run-2" / "session"
    root.mkdir(parents=True)
    repo_name, workspace_id = ssh_workspace.repository_identity(tmp_path)
    remote_root = f"/home/u/.cache/devtool/workspaces/{workspace_id}"
    (root / "manifest.json").write_text(json.dumps({
        "kind":"ssh-return-bundle","destination":"u0_a123@tablet","remote_root":remote_root,
        "local_return_repo_root":str(root / "repo"),"patterns":["**/build/outputs/***"],"run_id":"run-2","session":"session",
    }), encoding="utf-8")
    probe = _probe()
    probe.home = "/home/u"
    monkeypatch.setattr(ssh_worker, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_returns, "_local_rsync", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout=">f+++++++++|build/outputs/app.apk\n", stderr=""))
    result = ssh_returns.recover_return_bundles(_cfg(), tmp_path, run_id="run-2")
    assert result["ok"] is True
    assert result["results"][0]["state"] == "complete"
    assert (root / "transfer.status").read_text().startswith("returncode=0")


def test_complete_return_does_not_emit_partial_warning(tmp_path: Path) -> None:
    from devtool.runners import run_specs
    from devtool.models import ProjectConfig
    root = tmp_path / ".devtool" / "ssh-return" / "run" / "phase-ok"
    root.mkdir(parents=True)
    manifest = root / "manifest.json"; status = root / "transfer.status"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle","remote_root":"/remote/repo","source_sync_id":"sync-ok"}), encoding="utf-8")
    status.write_text("returncode=0\nstate=complete\n", encoding="utf-8")
    config = ProjectConfig(repo_root=tmp_path, project_root=str(tmp_path), kind="generic")
    spec = CommandSpec("check", ["sh", "-c", "true"], cwd=str(tmp_path), metadata={
        "execution_mode":"ssh-command","ssh_destination":"u@tablet","ssh_return_manifest":str(manifest),
        "ssh_return_status_file":str(status),"ssh_return_root":str(root),
    })
    result = run_specs(config, [spec], verbose=False, shutdown_dotnet=False)[0][0]
    assert result.returncode == 0
    assert result.metadata["ssh_return"]["state"] == "complete"
    assert not any(issue.code == "SSH_RETURN_PARTIAL" for issue in result.issues)


def test_recovery_rejects_manifest_local_mirror_outside_owned_bundle(tmp_path: Path) -> None:
    root = tmp_path / ".devtool" / "ssh-return" / "run-tamper" / "session"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps({
        "kind":"ssh-return-bundle", "destination":"u0_a123@tablet", "remote_root":"/remote/repo",
        "local_return_repo_root":str(tmp_path / ".devtool" / "ssh-return"),
        "patterns":["**/build/outputs/***"], "run_id":"run-tamper", "session":"session",
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="owned bundle directory"):
        ssh_returns.recover_return_bundles(_cfg(), tmp_path, run_id="run-tamper")


def test_recovery_clears_stale_bundle_files_before_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import ssh_worker
    root = tmp_path / ".devtool" / "ssh-return" / "run-clean" / "session"
    repo = root / "repo"
    repo.mkdir(parents=True)
    stale = repo / "build" / "outputs" / "stale.apk"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    _repo_name, workspace_id = ssh_workspace.repository_identity(tmp_path)
    remote_root = f"/home/u/.cache/devtool/workspaces/{workspace_id}"
    (root / "manifest.json").write_text(json.dumps({
        "kind":"ssh-return-bundle", "destination":"u0_a123@tablet", "remote_root":remote_root,
        "local_return_repo_root":str(repo), "patterns":["**/build/outputs/***"],
        "run_id":"run-clean", "session":"session",
    }), encoding="utf-8")
    probe = _probe(); probe.home = "/home/u"
    monkeypatch.setattr(ssh_worker, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_returns, "_local_rsync", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 0, stdout="", stderr=""))
    result = ssh_returns.recover_return_bundles(_cfg(), tmp_path, run_id="run-clean")
    assert result["ok"] is True
    assert not stale.exists()
    assert repo.is_dir()
    row = result["results"][0]
    assert row["local_mirror_cleanup_authority"] == "LIFE"
    assert Path(row["local_mirror_cleanup_receipt"]).is_file()


def test_recovery_retains_run_referenced_local_mirror_instead_of_direct_rmtree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import ssh_worker
    from devtool.core_runtime import private_run_root
    from devtool.run_store import AttachmentDescriptor, RunCatalog

    root = tmp_path / ".devtool" / "ssh-return" / "run-protected" / "session"
    repo = root / "repo"
    protected = repo / "build" / "outputs" / "app.apk"
    protected.parent.mkdir(parents=True)
    protected.write_bytes(b"referenced")
    _repo_name, workspace_id = ssh_workspace.repository_identity(tmp_path)
    remote_root = f"/home/u/.cache/devtool/workspaces/{workspace_id}"
    (root / "manifest.json").write_text(json.dumps({
        "kind":"ssh-return-bundle", "destination":"u0_a123@tablet", "remote_root":remote_root,
        "local_return_repo_root":str(repo), "patterns":["**/build/outputs/***"],
        "run_id":"run-protected", "session":"session",
    }), encoding="utf-8")
    catalog = RunCatalog.open(private_run_root(tmp_path))
    catalog.add_run({"schema_version": 3, "run_id": "durable-run", "status": "succeeded"})
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id="durable-run", path=protected, kind="artifact", privacy="private"
    ))
    catalog.save()
    probe = _probe(); probe.home = "/home/u"
    monkeypatch.setattr(ssh_worker, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_returns, "_local_rsync", lambda: "/usr/bin/rsync")

    with pytest.raises(ValueError, match="SSH return local mirror cleanup was refused by LIFE authority"):
        ssh_returns.recover_return_bundles(_cfg(), tmp_path, run_id="run-protected")

    assert protected.is_file(), "RUN-referenced returned evidence must not be erased by recovery retry cleanup"
    receipts = list((tmp_path / ".devtool" / "private" / "cleanup" / "receipts").glob("*.json"))
    assert receipts


def test_recovery_reconciles_runstore_provenance_without_changing_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from devtool import test_runstore as runstore
    from devtool.models import OperationResult
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    metadata = {
        "test_spec_id":"unit",
        "test_result":{"schema_version":1,"id":"unit","suites":[],"artifacts":[],"metadata":{"execution_provenance":{"transport":"ssh","session":"devtool-unit","return_state":"partial"}}},
        "execution_mode":"ssh-command","ssh_destination":"u@tablet","ssh_session":"devtool-unit",
        "ssh_return":{"state":"partial","recoverable":True},
    }
    result = OperationResult("test:unit", ["pytest"], 0, 1.0, "", [], metadata=metadata)
    path = runstore.persist_test_run(tmp_path, [result], run_id="run-recover")
    assert path is not None
    updated = runstore.reconcile_ssh_return_recovery([{
        "run_id":"run-recover","session":"devtool-unit","state":"complete","returncode":0,
    }])
    assert updated == {"runs_updated":1,"operations_updated":1}
    payload = runstore.load_test_run("run-recover")
    assert payload is not None
    op = payload["operations"][0]
    assert op["operation_id"] == "unit"
    assert op["test_result"]["id"] == "unit"
    assert op["provenance"]["return_state"] == "complete"
    assert op["provenance"]["return_recoverable"] is False
    assert op["test_result"]["metadata"]["execution_provenance"]["return_state"] == "complete"
