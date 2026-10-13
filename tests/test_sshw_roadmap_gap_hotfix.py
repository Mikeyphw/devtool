from __future__ import annotations

import io
from pathlib import Path
import subprocess
from types import SimpleNamespace

from rich.console import Console

from devtool.cli import build_parser
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool import ssh_qualification
from devtool.ui_core.navigation import navigation_shortcut_map


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".devtool.toml").write_text(
        '[ssh]\nprofile = "tablet"\n\n'
        '[ssh.hosts.tablet]\nhost = "10.0.0.2"\nport = 8022\nuser = "termux"\nmode = "commands"\n\n'
        '[ssh.hosts.tablet.workspace]\nmode = "managed"\nroot = "~/.cache/devtool/workspaces"\n',
        encoding="utf-8",
    )
    return repo


def test_qualify_cli_exposes_real_project_validation_seal() -> None:
    args = build_parser().parse_args([
        "ssh", "qualify", "tablet", "--project-validation", "--target", "xdm_android",
        "--task", "assembleDebug", "--task", "testDebugUnitTest",
    ])
    assert args.ssh_qualify_project_validation is True
    assert args.ssh_qualify_target == "xdm_android"
    assert args.ssh_qualify_tasks == ["assembleDebug", "testDebugUnitTest"]
    assert args.ssh_qualify_validation_twice is True
    single = build_parser().parse_args(["ssh", "qualify", "tablet", "--project-validation", "--single-validation"])
    assert single.ssh_qualify_validation_twice is False


def test_project_validation_argv_uses_normal_managed_validate_path(tmp_path: Path) -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed")
    argv = ssh_qualification._project_validation_argv(
        cfg, tmp_path, target="xdm_android", tasks=("assembleDebug", "testDebugUnitTest")
    )
    assert argv[:4] == [ssh_qualification.sys.executable, "-m", "devtool", "-r"]
    assert "--ssh-profile" in argv and argv[argv.index("--ssh-profile") + 1] == "tablet"
    assert "--target" in argv and argv[argv.index("--target") + 1] == "xdm_android"
    assert argv.count("--task") == 2
    assert argv[-1] == "testDebugUnitTest"


def test_qualification_real_project_path_runs_twice_and_reports_warm_metrics(monkeypatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = SSHRunnerConfig(host="tablet", user="termux", port=8022, profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed")
    probe = SimpleNamespace(missing_required=[], ready=True, home="/home/u", tools={"rsync": "/usr/bin/rsync"})
    spec = SimpleNamespace(remote_path="/home/u/.cache/devtool/workspaces/repo", mode="managed")
    monkeypatch.setattr(ssh_qualification, "worker_doctor_payload", lambda config: {"healthy": True})
    monkeypatch.setattr(ssh_qualification, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_qualification, "resolve_workspace_spec", lambda *a, **k: spec)
    monkeypatch.setattr(ssh_qualification, "sync_workspace", lambda *a, **k: {"ok": True, "change_count": 4, "sync_id": "sync-a"})
    monkeypatch.setattr(ssh_qualification, "plan_workspace_sync", lambda *a, **k: {"ok": True, "change_count": 0, "changes": []})
    project_calls = []
    def project_run(*args, **kwargs):
        project_calls.append(kwargs["ordinal"])
        return {"ok": True, "ordinal": kwargs["ordinal"], "seconds": 8.0 if kwargs["ordinal"] == 1 else 2.0, "return_manifest_count": 1, "return_manifests": [f"m{kwargs['ordinal']}"]}
    monkeypatch.setattr(ssh_qualification, "_run_project_validation", project_run)
    monkeypatch.setattr(ssh_qualification, "_start_tmux_probe", lambda *a, **k: ("qualify", "~/.cache/devtool/q"))
    monkeypatch.setattr(ssh_qualification, "_wait_tmux_probe", lambda *a, **k: {"state": "complete", "status": 0, "active": False})
    monkeypatch.setattr(ssh_qualification, "_retrieve_probe_artifact", lambda *a, **k: {"ok": True, "path": str(repo / "marker"), "bytes": 8})
    monkeypatch.setattr(ssh_qualification, "_cleanup_probe", lambda *a, **k: None)
    payload = ssh_qualification.qualify_worker(
        cfg, repo, project_validation=True, validation_target="xdm_android", validation_tasks=("assembleDebug",)
    )
    assert project_calls == [1, 2]
    assert payload["checks"]["project_validation"] == "pass"
    assert payload["checks"]["warm_validation"] == "pass"
    assert payload["performance"]["project_validation_first_seconds"] == 8.0
    assert payload["performance"]["project_validation_second_seconds"] == 2.0
    assert payload["performance"]["project_validation_warm_ratio"] == 0.25
    assert payload["project_validation"]["post_build_sync_plan"]["change_count"] == 0


def test_project_validation_failure_fails_qualification(monkeypatch, tmp_path: Path) -> None:
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands")
    monkeypatch.setattr(ssh_qualification, "_return_manifest_snapshot", lambda repo: set())
    monkeypatch.setattr(ssh_qualification.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 1))
    try:
        ssh_qualification._run_project_validation(cfg, tmp_path, ordinal=1)
    except ValueError as exc:
        assert "project validation pass 1 failed" in str(exc)
    else:
        raise AssertionError("failed project validation must fail the seal")




def test_tui_cli_accepts_workers_page() -> None:
    args = build_parser().parse_args(["tui", "--page", "workers", "--once"])
    assert args.page == "workers"


def test_remote_phase_persists_log_inside_returnable_worker_workspace() -> None:
    from devtool import ssh_tmux_runner, ssh_returns, ssh_workspace
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed")
    script = ssh_tmux_runner._build_remote_phase_script(
        cfg,
        session="devtool-tablet-build-run-1",
        command="printf 'hello\\n'",
        run_id="run-1",
        workspace_id="workspace-1",
        workspace_path="/home/u/.cache/devtool/workspaces/repo-a",
    )
    assert ".devtool/ssh-runtime-logs/run-1/devtool-tablet-build-run-1.log" in script
    assert "tee -a" in script
    assert "remote_log_path=" in script
    assert ".devtool/ssh-runtime-logs/***" in ssh_returns._DEFAULT_PATTERNS
    assert ".devtool/ssh-runtime-logs/" in ssh_workspace._DEFAULT_EXCLUDES


def test_remote_phase_runtime_log_preserves_real_command_exit_with_fake_tmux(tmp_path: Path) -> None:
    from devtool import ssh_tmux_runner
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_tmux = bindir / "tmux"
    fake_tmux.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  has-session) exit 1 ;;\n"
        "  new-session) shift; [ \"$1\" = -d ] && shift; [ \"$1\" = -s ] && shift 2; sh -c \"$1\"; exit $? ;;\n"
        "  attach-session|kill-session) exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_tmux.chmod(0o755)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed", run_state_root=str(home / "ssh-runs"))
    script = ssh_tmux_runner._build_remote_phase_script(
        cfg,
        session="phase-1",
        command="sh -c 'printf hello; exit 7'",
        run_id="run-1",
        workspace_id="workspace-1",
        workspace_path=str(workspace),
    )
    env = dict(ssh_tmux_runner.os.environ)
    env["HOME"] = str(home)
    env["PATH"] = str(bindir) + ssh_tmux_runner.os.pathsep + env.get("PATH", "")
    completed = subprocess.run(["sh", "-c", script], env=env, check=False, capture_output=True, text=True)
    assert completed.returncode == 7
    log = workspace / ".devtool" / "ssh-runtime-logs" / "run-1" / "phase-1.log"
    assert log.read_text(encoding="utf-8") == "hello"
