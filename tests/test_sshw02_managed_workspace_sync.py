from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from devtool import ssh_workspace
from devtool.ssh_tmux_runner import SSHRunnerConfig, resolve_ssh_config
from devtool.ssh_worker import SSHWorkerProbe


def _worker_probe() -> SSHWorkerProbe:
    return SSHWorkerProbe(
        profile="tablet",
        destination="termux@tablet",
        port=8022,
        platform="termux",
        os_name="Linux",
        architecture="aarch64",
        cpu_count=8,
        memory_kib=8 * 1024 * 1024,
        free_storage_kib=64 * 1024 * 1024,
        home="/data/data/com.termux/files/home",
        prefix="/data/data/com.termux/files/usr",
        shell="/data/data/com.termux/files/usr/bin/bash",
        package_manager="pkg",
        tools={
            "python": "/data/data/com.termux/files/usr/bin/python",
            "tmux": "/data/data/com.termux/files/usr/bin/tmux",
            "rsync": "/data/data/com.termux/files/usr/bin/rsync",
            "git": "/data/data/com.termux/files/usr/bin/git",
            "java": "",
            "gradle": "",
        },
        versions={},
        bootstrap_policy="missing",
        tmux_required=True,
    )


def _config(**kwargs) -> SSHRunnerConfig:
    base = dict(
        host="tablet",
        port=8022,
        user="termux",
        profile="tablet",
        mode="commands",
        worker_tmux=True,
        workspace_mode="managed",
        workspace_root="~/.cache/devtool/workspaces",
        sync_mode="incremental",
        sync_delete=True,
        sync_default_excludes=True,
        sync_excludes=(),
    )
    base.update(kwargs)
    return SSHRunnerConfig(**base)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "Main.kt").write_text("fun main() = Unit\n", encoding="utf-8")
    (repo / ".git").mkdir()
    (repo / ".git" / "index").write_bytes(b"index")
    return repo


def test_profile_parses_managed_workspace_and_sync_settings(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        """
[ssh.hosts.tablet]
host = "10.0.0.5"
port = 8022
user = "u0_a123"
mode = "commands"

[ssh.hosts.tablet.worker]
tmux = true

[ssh.hosts.tablet.workspace]
mode = "managed"
root = "~/.cache/devtool/workspaces"

[ssh.hosts.tablet.sync]
mode = "incremental"
delete = true
default_excludes = false
exclude = [".idea/", "local-only/"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = resolve_ssh_config(["--repo", str(tmp_path), "--ssh-profile", "tablet"], cwd=str(tmp_path), env={})
    assert cfg.workspace_mode == "managed"
    assert cfg.workspace_root == "~/.cache/devtool/workspaces"
    assert cfg.sync_mode == "incremental"
    assert cfg.sync_delete is True
    assert cfg.sync_default_excludes is False
    assert cfg.sync_excludes == (".idea/", "local-only/")


def test_existing_workspace_defaults_to_non_destructive_sync(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        """
[ssh.hosts.tablet]
host = "tablet"
mode = "commands"
cwd = "~/Code/repo"

[ssh.hosts.tablet.worker]
tmux = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    cfg = resolve_ssh_config(["--repo", str(tmp_path), "--ssh-profile", "tablet"], cwd=str(tmp_path), env={})
    assert cfg.workspace_mode == "existing"
    assert cfg.sync_delete is False


def test_managed_workspace_requires_named_profile(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        """
[ssh]
host = "tablet"
mode = "commands"

[ssh.workspace]
mode = "managed"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    # Top-level [ssh.workspace] is intentionally not interpreted as a managed
    # worker profile. Raw SSH remains legacy/existing behavior.
    cfg = resolve_ssh_config(["--repo", str(tmp_path), "--ssh", "tablet"], cwd=str(tmp_path), env={})
    assert cfg.workspace_mode == "existing"


def test_workspace_identity_is_stable_and_checkout_specific(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "https://example.invalid/repo.git")
    first = _repo(tmp_path / "one")
    second = _repo(tmp_path / "two")
    assert ssh_workspace.repository_identity(first) == ssh_workspace.repository_identity(first)
    assert ssh_workspace.repository_identity(first)[1] != ssh_workspace.repository_identity(second)[1]


def test_managed_spec_uses_remote_home_and_protects_cache_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    spec = ssh_workspace.resolve_workspace_spec(
        _config(sync_excludes=(".idea/",)),
        repo,
        remote_home="/data/data/com.termux/files/home",
    )
    assert spec.mode == "managed"
    assert spec.remote_path.startswith("/data/data/com.termux/files/home/.cache/devtool/workspaces/repo-")
    assert ".gradle/" in spec.excludes
    assert "build/" in spec.excludes
    assert ".idea/" in spec.excludes
    assert "/.devtool-workspace-id" in spec.excludes
    assert "/.devtool-remote-workspace.json" in spec.excludes
    assert ".git/" not in spec.excludes


def test_ephemeral_workspace_has_deterministic_base_and_run_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config(workspace_mode="ephemeral")
    a = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u", operation_id="run-123")
    b = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u", operation_id="run-123")
    c = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u", operation_id="run-456")
    assert a.workspace_id == b.workspace_id == c.workspace_id
    assert a.instance_id == b.instance_id
    assert a.instance_id != c.instance_id
    assert "/.ephemeral/" in a.remote_path


def test_prepare_script_contains_canonical_root_and_marker_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    spec = ssh_workspace.resolve_workspace_spec(_config(), repo, remote_home="/home/u")
    script = ssh_workspace._prepare_script(spec)
    assert "pwd -P" in script
    assert "workspace escapes managed root" in script
    assert "ownership marker mismatch" in script
    assert "refusing to claim a non-empty unowned managed workspace" in script
    assert ".devtool-workspace-id" in script


def test_workspace_status_rejects_marker_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    monkeypatch.setattr(
        ssh_workspace,
        "_run_remote",
        lambda *a, **kw: subprocess.CompletedProcess(
            [], 0,
            stdout="exists=1\nis_directory=1\nis_symlink=0\nsafe=1\ncanonical_root=/home/u/.cache/devtool/workspaces\ncanonical_path=" + spec.remote_path + "\nmarker=wrong\nmetadata_b64=\n",
            stderr="",
        ),
    )
    status = ssh_workspace.workspace_status(cfg, spec)
    assert status.safe is False
    assert "marker mismatch" in status.detail


def test_rsync_argv_preserves_git_and_excludes_remote_build_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home=_worker_probe().home)
    argv = ssh_workspace._rsync_argv(cfg, spec, dry_run=True)
    rendered = " ".join(argv)
    assert "--delete-delay" in argv
    assert "--dry-run" in argv
    assert "--protect-args" in argv
    assert "--rsync-path=/data/data/com.termux/files/usr/bin/rsync" in argv
    assert ".gradle/" in argv
    assert "build/" in argv
    assert ".git/" not in argv
    assert str(repo) + "/" in argv
    assert f"termux@tablet:{spec.remote_path}/" in argv
    assert "ssh -p 8022" in rendered


def test_absent_workspace_dry_run_is_non_mutating_and_lists_live_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    repo = _repo(tmp_path)
    (repo / "untracked.txt").write_text("live\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "ignore.txt").write_text("cache\n", encoding="utf-8")
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home=_worker_probe().home)
    remote_calls: list[str] = []

    def fake_remote(config, script, **kwargs):
        remote_calls.append(script)
        return subprocess.CompletedProcess([], 0, stdout="exists=0\nis_directory=0\nis_symlink=0\nsafe=1\ncanonical_root=\ncanonical_path=\nmarker=\nmetadata_b64=\n", stderr="")

    monkeypatch.setattr(ssh_workspace, "_run_remote", fake_remote)
    plan = ssh_workspace.plan_workspace_sync(cfg, spec)
    paths = {item["path"] for item in plan["changes"]}
    assert plan["ok"] is True
    assert plan["would_create_workspace"] is True
    assert any("src/Main.kt" in path for path in paths)
    assert any("untracked.txt" in path for path in paths)
    assert any(".git/index" in path for path in paths)
    assert not any("build/ignore.txt" in path for path in paths)
    assert len(remote_calls) == 1  # status only; no mkdir/metadata/write
    assert "mkdir" not in remote_calls[0]


def test_sync_fails_closed_before_rsync_for_unowned_existing_managed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    status = ssh_workspace.SSHWorkspaceStatus(
        exists=True,
        is_directory=True,
        is_symlink=False,
        safe=False,
        canonical_root="/home/u/.cache/devtool/workspaces",
        canonical_path=spec.remote_path,
        marker="",
        metadata={},
        detail="managed workspace exists without a Devtool ownership marker",
    )
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: status)
    called = False

    def fake_run(*a, **kw):
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="unowned|unsafe"):
        ssh_workspace.plan_workspace_sync(cfg, spec)
    assert called is False


def test_managed_sync_writes_registry_metadata_after_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home=_worker_probe().home)
    missing = ssh_workspace.SSHWorkspaceStatus(False, False, False, True, "", "", "", {}, "missing")
    ready = ssh_workspace.SSHWorkspaceStatus(True, True, False, True, spec.workspace_root, spec.remote_path, spec.instance_id, {}, "ready")
    statuses = iter([missing, ready, ready])
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: next(statuses))
    monkeypatch.setattr(ssh_workspace, "prepare_workspace", lambda *a, **kw: ready)
    remote_writes: list[tuple[str, str | None]] = []

    def fake_remote(config, script, *, input_text=None, timeout=None):
        remote_writes.append((script, input_text))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(ssh_workspace, "_run_remote", fake_remote)

    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        if isinstance(argv, list) and argv and argv[0] == "/usr/bin/rsync":
            return subprocess.CompletedProcess(argv, 0, stdout=">f+++++++++++|src/Main.kt\n", stderr="")
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ssh_workspace.sync_workspace(cfg, spec)
    assert result["ok"] is True
    assert result["change_count"] == 1
    assert remote_writes
    registry_writes = [(script, body) for script, body in remote_writes if "/.registry/" in script]
    assert registry_writes
    script, body = registry_writes[-1]
    metadata = json.loads(body or "{}")
    assert metadata["workspace_id"] == spec.workspace_id
    assert metadata["remote_path"] == spec.remote_path
    assert metadata["last_change_count"] == 1
    assert result["lease"] == {}  # manual/prewarm sync releases its lease
    assert any("refusing to release a lease owned by another run" in script for script, _ in remote_writes)


def test_existing_sync_does_not_write_devtool_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config(workspace_mode="existing", cwd="~/Code/repo", sync_delete=False)
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    assert spec.remote_path == "/home/u/Code/repo"
    called = False

    def fake_remote(*args, **kwargs):
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(ssh_workspace, "_run_remote", fake_remote)
    ssh_workspace._write_remote_metadata(cfg, spec, {"x": 1})
    assert called is False


def test_managed_profile_rejects_explicit_ssh_cwd_override(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text(
        """
[ssh.hosts.tablet]
host = "tablet"
mode = "commands"

[ssh.hosts.tablet.worker]
tmux = true

[ssh.hosts.tablet.workspace]
mode = "managed"
""".strip() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="--ssh-cwd.*existing"):
        resolve_ssh_config(
            ["--repo", str(tmp_path), "--ssh-profile", "tablet", "--ssh-cwd", "~/override"],
            cwd=str(tmp_path),
            env={},
        )


def test_managed_workspace_root_cannot_escape_via_dotdot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config(workspace_root="~/../../escape")
    with pytest.raises(ValueError, match="escape.*boundary"):
        ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")


def test_default_excludes_can_be_disabled_but_reserved_metadata_stays_protected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    spec = ssh_workspace.resolve_workspace_spec(
        _config(sync_default_excludes=False, sync_excludes=("custom/",)),
        repo,
        remote_home="/home/u",
    )
    assert ".gradle/" not in spec.excludes
    assert "build/" not in spec.excludes
    assert "custom/" in spec.excludes
    assert "/.devtool-workspace-id" in spec.excludes
    assert "/.devtool-remote-workspace.json" in spec.excludes


def test_sync_preflights_rsync_before_preparing_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    probe = _worker_probe()
    probe.tools["rsync"] = ""
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: probe)
    prepared = False

    def fake_prepare(*args, **kwargs):
        nonlocal prepared
        prepared = True
        raise AssertionError("prepare must not run when rsync is unavailable")

    monkeypatch.setattr(ssh_workspace, "prepare_workspace", fake_prepare)
    with pytest.raises(ValueError, match="requires rsync"):
        ssh_workspace.sync_workspace(cfg, spec)
    assert prepared is False


def test_existing_plan_uses_verified_canonical_path_for_rsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    repo = _repo(tmp_path)
    cfg = _config(workspace_mode="managed")
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    canonical = "/real/devtool/workspaces/" + spec.instance_id
    status = ssh_workspace.SSHWorkspaceStatus(
        True, True, True, True,
        "/real/devtool/workspaces", canonical, spec.instance_id, {}, "ready",
    )
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: status)
    seen: list[list[str]] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    payload = ssh_workspace.plan_workspace_sync(cfg, spec)
    assert payload["ok"] is True
    assert seen
    assert seen[-1][-1] == f"termux@tablet:{canonical}/"
    assert spec.remote_path not in seen[-1][-1]


def test_rsync_archive_mode_does_not_attempt_cross_device_owner_or_special_file_preservation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    monkeypatch.setattr(ssh_workspace, "_rsync_executable", lambda: "/usr/bin/rsync")
    repo = _repo(tmp_path)
    cfg = _config()
    spec = ssh_workspace.resolve_workspace_spec(cfg, repo, remote_home="/home/u")
    argv = ssh_workspace._rsync_argv(cfg, spec, dry_run=True)
    for option in ("--no-owner", "--no-group", "--no-devices", "--no-specials"):
        assert option in argv


def test_ephemeral_workspace_status_uses_stable_preview_instance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_workspace, "_git_origin", lambda root: "")
    monkeypatch.setattr(ssh_workspace, "probe_worker", lambda config: _worker_probe())
    repo = _repo(tmp_path)
    cfg = _config(workspace_mode="ephemeral")
    status = ssh_workspace.SSHWorkspaceStatus(False, False, False, True, "", "", "", {}, "missing")
    monkeypatch.setattr(ssh_workspace, "workspace_status", lambda *a, **kw: status)
    first = ssh_workspace.workspace_payload(cfg, repo)
    second = ssh_workspace.workspace_payload(cfg, repo)
    assert first["workspace"]["instance_id"] == second["workspace"]["instance_id"]
    assert first["workspace"]["instance_id"].endswith("-preview")
