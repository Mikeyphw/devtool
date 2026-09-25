from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.models import CommandSpec
from devtool import ssh_exec, ssh_history, ssh_resilience, ssh_returns, ssh_roadmap_fixtures, ssh_tmux_runner
from devtool.ssh_tmux_runner import SSHRunnerConfig


def cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet", port=8022, user="u0", profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="/remote/workspaces",
        run_state_root="/remote/runs", host_key_policy="accept-new", operation_id="run-1",
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def rich_record(**overrides):
    value = {
        "run_id": "run-1", "session": "devtool-tablet-build", "state": "completed",
        "status": 0, "active": False, "started_unix": 100, "finished_unix": 105,
        "workspace_id": "repo-123", "workspace_path": "/remote/workspaces/repo-123",
        "repository_fingerprint": "repo-fp", "repository_root": "/local/repo", "target": "devtool",
        "phase": "test", "source_sync_id": "sync-1", "frozen_source_lock_digest": "lock-1",
        "phase_node_id": "node-1", "output_state": "complete", "stdout_exists": True,
        "stdout_size": 3, "stderr_exists": True, "stderr_size": 4,
        "typed_contract": {"repository": "devtool", "target": "devtool", "type": "wrapper", "name": "test", "ref": "devtool::devtool::test"},
        "lineage": {"parent_run_id": "parent", "parent_node_id": "parent-node", "child_run_id": "run-1"},
        "confirmation_mode": "automatic", "lease_dir": "/remote/workspaces/.leases/repo-123.lock",
    }
    value.update(overrides)
    return value


def test_commands_mode_persists_remote_repository_root_without_local_termux_path(tmp_path: Path) -> None:
    from devtool.ssh_tmux_runner import wrap_specs_for_ssh_commands

    repo = tmp_path / "repo"
    repo.mkdir()
    config = SSHRunnerConfig(
        host="buildbox", profile="", mode="commands", cwd="/srv/repo",
        workspace_mode="existing", run_state_root="/remote/runs",
    )
    spec = CommandSpec("test", ["python3", "-V"], cwd=str(repo), env={"KEEP_REMOTE_SAFE": "yes"})
    wrapped = wrap_specs_for_ssh_commands(
        [spec], config, project_config=SimpleNamespace(runner_options={}), repo_root=repo, dry_run=True,
    )[0]
    shell = wrapped.argv[-1]
    assert f"ssh_repository_root={repo}" not in shell
    assert "ssh_repository_root=/srv/repo" in shell


def test_phase_script_persists_split_output_and_canonical_identity() -> None:
    script = ssh_tmux_runner._build_remote_phase_script(
        cfg(), session="devtool-tablet-build", command="printf ok; printf err >&2", run_id="run-1",
        workspace_id="repo-123", workspace_path="/remote/repo", lease_dir="/remote/workspaces/.leases/repo.lock",
        phase_metadata={
            "ssh_repository_fingerprint": "repo-fp", "ssh_target": "devtool", "ssh_phase": "test",
            "ssh_contract_type": "wrapper", "ssh_contract_name": "test", "ssh_contract_ref": "devtool::devtool::test",
            "ssh_frozen_source_lock_digest": "lock-1", "federation_parent_run_id": "parent",
            "federation_parent_node_id": "node-parent", "ssh_confirmation_mode": "automatic",
        },
    )
    assert "stdout.log" in script and "stderr.log" in script
    assert "mkfifo" in script and "out_pid" in script and "err_pid" in script
    assert "stdout_path=" in script and "stderr_path=" in script
    assert "ssh_repository_fingerprint=repo-fp" in script
    assert "ssh_contract_ref=devtool::devtool::test" in script
    assert "ssh_frozen_source_lock_digest=lock-1" in script
    assert "federation_parent_run_id=parent" in script
    assert "ssh_confirmation_mode=automatic" in script


def test_runs_payload_preserves_legacy_sshw05_history(monkeypatch: pytest.MonkeyPatch) -> None:
    line = "run-old\ts-old\tcompleted\t0\t0\trepo-id\t/remote/repo\t123\t/remote/.leases/repo.lock\n"
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=line, stderr=""))
    payload = ssh_resilience.runs_payload(cfg())
    record = payload["records"][0]
    assert record["workspace_id"] == "repo-id"
    assert record["workspace_path"] == "/remote/repo"
    assert record["started_unix"] == 123
    assert record["lease_dir"] == "/remote/.leases/repo.lock"
    assert record["output_state"] == "unavailable"


def test_runs_payload_exposes_contract_lineage_output_and_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    # Generate exactly the extended tabular record expected from the worker.
    keys = (
        "repo", "/remote/repo", "100", "105", "/remote/.leases/repo.lock", "commands", "ssh-native", "node-1", "sync-1",
        "", "", "", "", "", "/remote/log", "/remote/stdout", "/remote/stderr", "repo-fp", "/local/repo", "devtool", "test",
        "wrapper", "test", "devtool::devtool::test", "lock-1", "automatic", "0", "0", "parent", "parent-node", "run-1", "devtool", "devtool",
        "test", "wrapper:test", "devtool::devtool::test",
    )
    line = "\t".join(("run-1", "session-1", "completed", "0", "0", *keys, "1", "3", "1", "4")) + "\n"
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=line, stderr=""))
    record = ssh_resilience.runs_payload(cfg())["records"][0]
    assert record["repository_fingerprint"] == "repo-fp"
    assert record["target"] == "devtool"
    assert record["typed_contract"]["ref"] == "devtool::devtool::test"
    assert record["lineage"] == {"parent_run_id": "parent", "parent_node_id": "parent-node", "child_run_id": "run-1"}
    assert record["frozen_source_lock_digest"] == "lock-1"
    assert record["confirmation_mode"] == "automatic"
    assert record["output_state"] == "complete"


def test_historical_output_reads_run_files_after_tmux_is_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    record = rich_record(active=False, state="completed")
    monkeypatch.setattr(ssh_history, "runs_payload", lambda c: {"records": [record]})
    def remote(config, script, **kwargs):
        assert "tmux attach" not in script and "tmux has-session" not in script
        if "stdout.log" in script:
            return subprocess.CompletedProcess([], 0, stdout="out", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="err", stderr="")
    monkeypatch.setattr(ssh_history, "_run_remote", remote)
    payload = ssh_history.output_payload(cfg(), run_id="run-1")
    assert payload["stdout"] == "out" and payload["stderr"] == "err"
    assert payload["output_state"] == "complete"


def test_follow_retries_transport_loss_without_advancing_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    record = rich_record(active=True, state="running", output_state="still-writing")
    monkeypatch.setattr(ssh_history, "_selected_record", lambda *a, **k: dict(record))
    attempts = {"stdout": 0, "stderr": 0}
    offsets: list[tuple[str, int]] = []
    def read(config, rec, stream, *, offset=0):
        offsets.append((stream, offset))
        attempts[stream] += 1
        if stream == "stdout" and attempts[stream] == 1:
            raise ValueError("transport lost")
        if stream == "stdout" and attempts[stream] == 2:
            return "abc"
        return ""
    refreshes = iter([
        rich_record(active=True, state="running", output_state="still-writing"),
        rich_record(active=False, state="completed", output_state="complete"),
    ])
    monkeypatch.setattr(ssh_history, "_read_stream", read)
    monkeypatch.setattr(ssh_history, "_refresh_record", lambda *a, **k: next(refreshes))
    monkeypatch.setattr(ssh_history.time, "sleep", lambda *a: None)
    monkeypatch.setattr(ssh_history, "publish_event", lambda e: None)
    out, err = io.StringIO(), io.StringIO()
    payload = ssh_history.follow_output(cfg(), run_id="run-1", stdout_writer=out, stderr_writer=err, poll_interval=0.01, reconnect_grace_seconds=1)
    assert out.getvalue() == "abc"
    assert payload["reconnects"] == 1
    assert offsets[0] == ("stdout", 0) and offsets[1] == ("stdout", 0)
    assert ("stdout", 3) in offsets


def test_sessions_and_runs_are_historical_projections_not_tmux_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [rich_record(), rich_record(run_id="run-2", session="s2", active=True, state="running", status=None, output_state="still-writing")]
    monkeypatch.setattr(ssh_history, "runs_payload", lambda c: {"records": records, "stale_count": 0})
    sessions = ssh_history.sessions_payload(cfg())
    runs = ssh_history.grouped_runs_payload(cfg())
    assert sessions["historical"] is True and len(sessions["sessions"]) == 2
    assert runs["run_count"] == 2 and runs["runs"][0]["run_id"] == "run-2"
    assert runs["runs"][0]["output_state"] == "still-writing"


def test_exec_preserves_argv_boundaries_and_confirmation_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".devtool.toml").write_text('[project]\nkind="generic"\n')
    project = SimpleNamespace(selected_target="devtool", repository_identity=SimpleNamespace(fingerprint="repo-fp"))
    monkeypatch.setattr(ssh_exec, "load_project_config", lambda *a, **k: project)
    seen = {}
    def wrap(specs, config, **kwargs):
        seen["argv"] = list(specs[0].argv); seen["metadata"] = dict(specs[0].metadata)
        return [CommandSpec("ssh-exec", ["ssh-launcher"], cwd=str(repo), env={}, metadata={"ssh_session":"s1", "oci_phase_node_id":"n1"})]
    monkeypatch.setattr(ssh_exec, "wrap_specs_for_ssh_commands", wrap)
    monkeypatch.setattr(ssh_exec, "publish_event", lambda e: None)
    monkeypatch.setattr(ssh_exec.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""))
    payload = ssh_exec.execute_managed(cfg(), repo, argv=("python", "-c", "print('a b')", "x y"), assume_yes=True)
    assert seen["argv"] == ["python", "-c", "print('a b')", "x y"]
    assert seen["metadata"]["ssh_confirmation_mode"] == "automatic"
    assert payload["confirmation_mode"] == "automatic"
    assert payload["shell_text_mode"] is False and payload["state"] == "succeeded"


def test_exec_shell_text_is_explicit_and_mutually_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".devtool.toml").write_text('[project]\nkind="generic"\n')
    project = SimpleNamespace(selected_target="devtool", repository_identity=SimpleNamespace(fingerprint="repo-fp"))
    monkeypatch.setattr(ssh_exec, "load_project_config", lambda *a, **k: project)
    monkeypatch.setattr(ssh_exec, "wrap_specs_for_ssh_commands", lambda specs, *a, **k: [CommandSpec("x", ["true"], cwd=str(repo), metadata={"ssh_session":"s"})])
    monkeypatch.setattr(ssh_exec, "publish_event", lambda e: None)
    monkeypatch.setattr(ssh_exec.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""))
    payload = ssh_exec.execute_managed(cfg(), repo, shell_text="printf '%s' hi")
    assert payload["shell_text_mode"] is True and payload["argv"] == []
    with pytest.raises(ValueError, match="either argv.*or --shell"):
        ssh_exec.execute_managed(cfg(), repo, argv=("true",), shell_text="true")


def test_detached_exec_returns_recovery_hint_without_false_completion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".devtool.toml").write_text('[project]\nkind="generic"\n')
    project = SimpleNamespace(selected_target="devtool", repository_identity=SimpleNamespace(fingerprint="repo-fp"))
    monkeypatch.setattr(ssh_exec, "load_project_config", lambda *a, **k: project)
    monkeypatch.setattr(ssh_exec, "wrap_specs_for_ssh_commands", lambda specs, *a, **k: [CommandSpec("x", ["launcher"], cwd=str(repo), metadata={"ssh_session":"session-x", "oci_phase_node_id":"node-x"})])
    events=[]; monkeypatch.setattr(ssh_exec, "publish_event", events.append)
    monkeypatch.setattr(ssh_exec.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 125, stdout="", stderr=""))
    payload = ssh_exec.execute_managed(cfg(), repo, argv=("sleep", "1"), detached=True)
    assert payload["ok"] is True and payload["state"] == "running" and payload["returncode"] is None
    assert "ssh output" in payload["recovery_hint"] and "--follow" in payload["recovery_hint"]
    assert not any(type(event).__name__ == "RunFinished" for event in events)


def test_cli_declares_output_and_exec_surfaces_with_global_yes() -> None:
    parser = build_parser()
    output = parser.parse_args(["ssh", "output", "tablet", "--latest", "--follow", "--stream", "stderr", "--format", "json"])
    assert output.ssh_command == "output" and output.ssh_output_latest and output.ssh_output_follow
    assert output.ssh_output_stream == "stderr"
    execute = parser.parse_args(["-y", "ssh", "exec", "tablet", "--target", "devtool", "--detached", "--", "printf", "%s", "a b"])
    assert execute.yes is True and execute.ssh_command == "exec" and execute.ssh_exec_detached is True
    assert execute.ssh_exec_argv == ["printf", "%s", "a b"]


def test_artifact_lifecycle_records_verified_local_bytes(tmp_path: Path) -> None:
    root = tmp_path / "bundle"; repo = root / "repo"; repo.mkdir(parents=True)
    artifact = repo / "build" / "app.apk"; artifact.parent.mkdir(); artifact.write_bytes(b"apk")
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"kind":"ssh-return-bundle", "local_return_repo_root":str(repo), "patterns":["**/*.apk"]}))
    status = root / "transfer.status"; status.write_text("returncode=0\nstate=complete\nmode=rsync-delta\n")
    state = ssh_returns.load_return_state({"ssh_return_manifest":str(manifest), "ssh_return_status_file":str(status), "ssh_return_repo_root":str(repo)})
    item = state["artifacts"][0]
    assert item["artifact"] == "build/app.apk"
    assert all(item[key] for key in ("expected","produced","staged","transferred","verified","restored_locally"))
    assert item["failed"] is False and item["recoverable"] is False and len(item["sha256"]) == 64
    assert state["return_failed"] is False and state["execution_failed"] is False


def test_artifact_lifecycle_keeps_unresolved_expected_selectors_recoverable(tmp_path: Path) -> None:
    root = tmp_path / "bundle"; repo = root / "repo"; repo.mkdir(parents=True)
    manifest = root / "manifest.json"; manifest.write_text(json.dumps({"kind":"ssh-return-bundle", "local_return_repo_root":str(repo), "patterns":["**/*.apk"]}))
    status = root / "transfer.status"; status.write_text("returncode=23\nstate=partial\n")
    state = ssh_returns.load_return_state({"ssh_return_manifest":str(manifest), "ssh_return_status_file":str(status), "ssh_return_repo_root":str(repo)})
    item = state["artifacts"][0]
    assert item["selector"] == "**/*.apk" and item["expected"] is True
    assert item["produced"] is False and item["failed"] is True and item["recoverable"] is True
    assert state["return_failed"] is True and state["recoverable"] is True


def test_recovery_actions_explain_intent_and_do_not_rerun_success(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = rich_record(active=False, state="completed", status=0)
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda c: {"records":[completed]})
    monkeypatch.setattr(ssh_resilience, "_run_remote", lambda *a, **k: subprocess.CompletedProcess([],0,stdout="",stderr=""))
    resumed = ssh_resilience.resume_remote_run(cfg(), run_id="run-1", attach=False)
    assert resumed["recovery"]["intent"] == "reconcile-completed"
    assert resumed["recovery"]["rerun_required"] is False and resumed["recovery"]["resumable"] is False


def test_ssh02_fixture_declares_tmux_non_authority_and_artifact_states() -> None:
    payload = ssh_roadmap_fixtures.execution_payload()
    assert payload["roadmap_id"] == "SSH-02"
    assert payload["tmux_is_authority"] is False and payload["runstore_authority"] is True
    assert {"expected","produced","staged","transferred","verified","restored-locally","failed","recoverable"} <= set(payload["artifact_states"])


def test_gate_install_uses_transaction_repo_root_and_no_build_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=[]\n")
    monkeypatch.setenv("DEVTOOL_TRANSACTION_REPO_ROOT", str(tmp_path))
    calls=[]
    monkeypatch.setattr(ssh_roadmap_fixtures.subprocess, "run", lambda command, **kwargs: calls.append((command,kwargs)) or subprocess.CompletedProcess(command,0,stdout="ok",stderr=""))
    payload=ssh_roadmap_fixtures.gate_install_payload()
    assert payload["transaction_repo_root"] == str(tmp_path)
    assert calls[0][0][-3:] == ["--no-build-isolation", "-e", str(tmp_path)]
    assert calls[0][1]["cwd"] == str(tmp_path)


def test_exec_follow_json_mode_captures_persisted_streams_without_live_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".devtool.toml").write_text('[project]\nkind="generic"\n')
    project = SimpleNamespace(selected_target="devtool", repository_identity=SimpleNamespace(fingerprint="repo-fp"))
    monkeypatch.setattr(ssh_exec, "load_project_config", lambda *a, **k: project)
    monkeypatch.setattr(
        ssh_exec,
        "wrap_specs_for_ssh_commands",
        lambda specs, *a, **k: [CommandSpec(
            "ssh-exec", ["launcher"], cwd=str(repo),
            metadata={"ssh_session":"session-follow", "oci_phase_node_id":"node-follow"},
        )],
    )
    monkeypatch.setattr(ssh_exec, "publish_event", lambda e: None)
    monkeypatch.setattr(ssh_exec.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 125, stdout="", stderr=""))

    def follow(config, **kwargs):
        kwargs["stdout_writer"].write("persisted-out\n")
        kwargs["stderr_writer"].write("persisted-err\n")
        return {"status": 0, "state": "completed", "active": False, "output_state": "complete"}

    monkeypatch.setattr(ssh_exec, "follow_output", follow)
    monkeypatch.setattr(ssh_exec, "resume_remote_run", lambda *a, **k: {"ok": True})
    payload = ssh_exec.execute_managed(
        cfg(), repo, argv=("printf", "ok"), follow=True,
        capture_launcher=True, follow_live=False,
    )
    assert payload["state"] == "succeeded" and payload["returncode"] == 0
    assert payload["follow_result"]["stdout"] == "persisted-out\n"
    assert payload["follow_result"]["stderr"] == "persisted-err\n"
    assert payload["execution_failed"] is False


def test_grouped_runs_project_local_return_and_runstore_truth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    bundle = repo / ".devtool" / "ssh-return" / "run-1" / "session-1"
    returned_repo = bundle / "repo"
    artifact = returned_repo / "build" / "app.apk"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"apk")
    (bundle / "manifest.json").write_text(json.dumps({
        "kind": "ssh-return-bundle",
        "run_id": "run-1",
        "session": "session-1",
        "local_return_repo_root": str(returned_repo),
        "patterns": ["**/*.apk"],
    }))
    (bundle / "transfer.status").write_text("returncode=0\nstate=complete\nmode=rsync-delta\n")
    monkeypatch.setattr(ssh_history, "runs_payload", lambda c: {
        "records": [rich_record(session="session-1")], "stale_count": 0,
    })
    import devtool.test_runstore as test_runstore
    monkeypatch.setattr(test_runstore, "load_test_run", lambda run_id: {
        "repository": str(repo.resolve()),
        "summary": {"passed": 7, "failed": 0, "total": 7},
        "artifacts": [{"path": "reports/tests.xml"}],
        "build_artifacts": [{"path": "build/app.apk"}],
        "logs": [{"path": "logs/test.log"}],
        "operations": [{"operation_id": "test"}],
        "comparison": {"new_failures": []},
    })
    payload = ssh_history.grouped_runs_payload(cfg(), repo_root=repo)
    run = payload["runs"][0]
    assert run["return_state"] == "complete" and run["return_recoverable"] is False
    assert run["return_bundles"][0]["artifacts"][0]["artifact"] == "build/app.apk"
    assert run["test_summary"] == {"passed": 7, "failed": 0, "total": 7}
    assert run["build_artifacts"] == [{"path": "build/app.apk"}]
    assert run["logs"] == [{"path": "logs/test.log"}]


def test_ssh_completion_exposes_ssh01_and_ssh02_surfaces() -> None:
    from devtool.completion import ZSH_COMPLETION
    action_line = "profiles list add remove rm default qualify seal doctor env prepare bootstrap sessions ls workspace ws sync retrieve return runs output exec resume cancel recover"
    assert action_line in ZSH_COMPLETION
    assert "output)" in ZSH_COMPLETION and "--follow[follow persisted output without attaching to tmux]" in ZSH_COMPLETION
    assert "exec)" in ZSH_COMPLETION and "--shell[explicit shell-text command; argv mode otherwise]" in ZSH_COMPLETION
