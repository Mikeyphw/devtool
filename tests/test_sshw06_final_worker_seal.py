from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.config import global_config_doctor_entries, load_global_config
from devtool.ssh_profiles import add_profile, list_profiles, remove_profile, set_default_profile
from devtool.ssh_tmux_runner import SSHRunnerConfig, resolve_ssh_config
from devtool import ssh_qualification, ssh_workspace


def _global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "global" / "config.toml"
    monkeypatch.setenv("DEVTOOL_GLOBAL_CONFIG", str(path))
    return path


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(exist_ok=True)
    (root / "README.md").write_text("demo\n", encoding="utf-8")
    return root


def test_global_profile_add_resolve_list_default_remove(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    added = add_profile(
        "tablet", host="192.168.1.55", port=8022, user="u0_a123",
        repo=repo, global_scope=True,
    )
    assert added["ok"] is True
    assert added["path"] == str(path)
    assert path.exists()

    cfg = resolve_ssh_config(["--repo", str(repo), "--ssh-profile", "tablet"], cwd=str(repo), env={})
    assert cfg.profile == "tablet"
    assert cfg.host == "192.168.1.55"
    assert cfg.port == 8022
    assert cfg.user == "u0_a123"
    assert cfg.mode == "commands"
    assert cfg.workspace_mode == "managed"
    assert cfg.worker_tmux is True
    assert cfg.host_key_policy == "accept-new"

    listing = list_profiles(repo=repo)
    assert listing["default_profile"] == "tablet"
    assert listing["records"][0]["name"] == "tablet"
    assert listing["records"][0]["global"] is True
    assert listing["records"][0]["default"] is True

    # Global config accepts [ssh] as a user/machine-scoped section.
    global_data, _ = load_global_config(path)
    issues = global_config_doctor_entries(global_data)
    assert not any("ssh" in str(item).lower() and "not allowed" in str(item).lower() for item in issues)

    removed = remove_profile("tablet", repo=repo, global_scope=True)
    assert removed["cleared_default"] is True
    assert list_profiles(repo=repo)["records"] == []


def test_repo_profile_overrides_global_profile_field_by_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("tablet", host="global.example", port=8022, user="termux", repo=repo, global_scope=True)
    (repo / ".devtool.toml").write_text(
        '[ssh.hosts.tablet]\nport = 2222\n\n[ssh]\nprofile = "tablet"\n', encoding="utf-8"
    )
    cfg = resolve_ssh_config(["--repo", str(repo), "--ssh-profile", "tablet"], cwd=str(repo), env={})
    assert cfg.host == "global.example"
    assert cfg.user == "termux"
    assert cfg.port == 2222
    # Nested managed-worker policy still comes from the global profile.
    assert cfg.workspace_mode == "managed"


def test_global_default_does_not_change_raw_ssh_host_semantics(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("tablet", host="tablet.example", port=8022, user="termux", repo=repo)
    raw = resolve_ssh_config(["--repo", str(repo), "--ssh", "legacy.example"], cwd=str(repo), env={})
    assert raw.profile == ""
    assert raw.host == "legacy.example"
    assert raw.port == 22
    assert raw.user == ""
    assert raw.workspace_mode == "existing"
    named_override = resolve_ssh_config(
        ["--repo", str(repo), "--ssh-profile", "tablet", "--ssh", "override.example"], cwd=str(repo), env={}
    )
    assert named_override.profile == "tablet"
    assert named_override.host == "override.example"
    assert named_override.port == 8022

def test_profile_add_is_fail_closed_without_force(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("tablet", host="first", repo=repo)
    with pytest.raises(ValueError, match="already exists"):
        add_profile("tablet", host="second", repo=repo)
    add_profile("tablet", host="second", repo=repo, force=True)
    cfg = resolve_ssh_config(["--repo", str(repo), "--ssh-profile", "tablet"], cwd=str(repo), env={})
    assert cfg.host == "second"


def test_repo_local_profile_management_does_not_touch_global(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    global_path = _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("tablet", host="repo-only", repo=repo, global_scope=False)
    assert not global_path.exists()
    assert (repo / ".devtool.toml").exists()
    listing = list_profiles(repo=repo)
    assert listing["records"][0]["scope"] == "repo"


def test_set_default_requires_profile_in_selected_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("one", host="one", repo=repo, set_default=False)
    add_profile("two", host="two", repo=repo, set_default=False)
    set_default_profile("two", repo=repo)
    assert list_profiles(repo=repo)["default_profile"] == "two"
    with pytest.raises(ValueError, match="not defined"):
        set_default_profile("missing", repo=repo)


def test_cli_exposes_final_worker_management_surface() -> None:
    parser = build_parser()
    args = parser.parse_args(["ssh", "add", "tablet", "--host", "10.0.0.2", "--port", "8022"])
    assert args.ssh_command == "add"
    assert args.ssh_profile_global is True
    assert args.workspace_mode == "managed"
    args = parser.parse_args(["ssh", "qualify", "tablet", "--dry-run", "--no-bootstrap", "--no-sync"])
    assert args.ssh_command == "qualify"
    assert args.ssh_qualify_dry_run is True
    assert args.ssh_qualify_bootstrap is False
    assert args.ssh_qualify_sync is False
    assert parser.parse_args(["ssh", "profiles"]).ssh_command == "profiles"
    assert parser.parse_args(["ssh", "remove", "tablet", "--repo-local"]).ssh_profile_global is False


def test_qualification_runs_sync_detached_tmux_return_and_clean_second_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = SSHRunnerConfig(
        host="tablet", user="termux", port=8022, profile="tablet", mode="commands",
        worker_tmux=True, workspace_mode="managed", workspace_root="~/.cache/devtool/workspaces",
    )
    probe = SimpleNamespace(
        missing_required=[], ready=True, home="/data/data/com.termux/files/home",
        tools={"rsync": "/data/data/com.termux/files/usr/bin/rsync"},
    )
    monkeypatch.setattr(ssh_qualification, "worker_doctor_payload", lambda config: {"healthy": True, "profile": "tablet"})
    monkeypatch.setattr(ssh_qualification, "probe_worker", lambda config: probe)
    spec = SimpleNamespace(remote_path="/home/u/.cache/devtool/workspaces/repo-x", mode="managed")
    monkeypatch.setattr(ssh_qualification, "resolve_workspace_spec", lambda *a, **k: spec)
    monkeypatch.setattr(ssh_qualification, "sync_workspace", lambda *a, **k: {"ok": True, "change_count": 7, "sync_id": "sync-1"})
    monkeypatch.setattr(ssh_qualification, "plan_workspace_sync", lambda *a, **k: {"ok": True, "change_count": 0, "changes": []})
    monkeypatch.setattr(ssh_qualification, "_start_tmux_probe", lambda *a, **k: ("devtool-tablet-qualify", "~/.cache/devtool/ssh-runs/qualification/q"))
    monkeypatch.setattr(ssh_qualification, "_wait_tmux_probe", lambda *a, **k: {"state": "complete", "status": 0, "active": False})
    monkeypatch.setattr(ssh_qualification, "_retrieve_probe_artifact", lambda *a, **k: {"ok": True, "path": str(repo / "artifact.txt"), "bytes": 16})
    monkeypatch.setattr(ssh_qualification, "_cleanup_probe", lambda *a, **k: None)

    payload = ssh_qualification.qualify_worker(cfg, repo)
    assert payload["ok"] is True
    assert payload["checks"] == {
        "transport": "pass", "bootstrap": "pass", "source_sync": "pass",
        "detached_tmux": "pass", "return_transport": "pass", "second_sync_clean": "pass",
    }
    assert payload["performance"]["first_sync_changes"] == 7
    assert payload["performance"]["second_sync_changes"] == 0


def test_qualification_fails_if_second_sync_is_not_clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed")
    probe = SimpleNamespace(missing_required=[], ready=True, home="/home/u", tools={"rsync": "/usr/bin/rsync"})
    monkeypatch.setattr(ssh_qualification, "worker_doctor_payload", lambda config: {"healthy": True})
    monkeypatch.setattr(ssh_qualification, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_qualification, "resolve_workspace_spec", lambda *a, **k: SimpleNamespace(remote_path="/w", mode="managed"))
    monkeypatch.setattr(ssh_qualification, "sync_workspace", lambda *a, **k: {"ok": True, "change_count": 1})
    monkeypatch.setattr(ssh_qualification, "plan_workspace_sync", lambda *a, **k: {"ok": True, "change_count": 1})
    monkeypatch.setattr(ssh_qualification, "_start_tmux_probe", lambda *a, **k: ("s", "/r"))
    monkeypatch.setattr(ssh_qualification, "_wait_tmux_probe", lambda *a, **k: {"state": "complete", "status": 0})
    monkeypatch.setattr(ssh_qualification, "_retrieve_probe_artifact", lambda *a, **k: {"ok": True, "path": "/tmp/a", "bytes": 1})
    monkeypatch.setattr(ssh_qualification, "_cleanup_probe", lambda *a, **k: None)
    with pytest.raises(ValueError, match="second incremental sync is not clean"):
        ssh_qualification.qualify_worker(cfg, repo)


def test_qualification_dry_run_is_non_mutating(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    cfg = SSHRunnerConfig(host="tablet", profile="tablet", mode="commands", worker_tmux=True, workspace_mode="managed")
    probe = SimpleNamespace(missing_required=["tmux", "rsync"], ready=False, home="/home/u", tools={"rsync": ""})
    monkeypatch.setattr(ssh_qualification, "worker_doctor_payload", lambda config: {"healthy": False})
    monkeypatch.setattr(ssh_qualification, "probe_worker", lambda config: probe)
    monkeypatch.setattr(ssh_qualification, "bootstrap_worker", lambda *a, **k: {"ok": True, "dry_run": True, "missing_before": ["tmux", "rsync"]})
    monkeypatch.setattr(ssh_qualification, "resolve_workspace_spec", lambda *a, **k: SimpleNamespace())
    payload = ssh_qualification.qualify_worker(cfg, repo, dry_run=True)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["sync"]["deferred"] is True
    assert payload["checks"]["detached_tmux"] == "planned"


def test_qualification_output_is_excluded_from_phone_to_tablet_sync() -> None:
    assert ".devtool/ssh-qualification/" in ssh_workspace._DEFAULT_EXCLUDES


def test_existing_profile_requires_explicit_remote_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="requires --cwd"):
        add_profile("legacy", host="host", repo=repo, workspace_mode="existing")
    add_profile("legacy", host="host", repo=repo, workspace_mode="existing", cwd="~/Code/repo")
    cfg = resolve_ssh_config(["--repo", str(repo), "--ssh-profile", "legacy"], cwd=str(repo), env={})
    assert cfg.workspace_mode == "existing"
    assert cfg.cwd == "~/Code/repo"


def test_tmux_qualification_uses_remote_home_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = SSHRunnerConfig(
        host="tablet", profile="tablet", mode="commands", worker_tmux=True,
        run_state_root="~/.cache/devtool/ssh-runs",
    )
    seen = []
    monkeypatch.setattr(
        ssh_qualification,
        "_run_remote",
        lambda config, script, **kwargs: seen.append(script) or subprocess.CompletedProcess([], 0, stdout="session\n", stderr=""),
    )
    _, remote_root = ssh_qualification._start_tmux_probe(cfg, nonce="q-abc")
    assert remote_root.startswith("~/")
    assert '"$HOME"/.cache/devtool/ssh-runs/qualification/q-abc/artifact.txt' in seen[0]
    assert "'~/.cache/devtool" not in seen[0]


def test_profile_force_replace_removes_legacy_alias_sections(tmp_path, monkeypatch):
    from devtool import ssh_profiles

    global_path = tmp_path / "global.toml"
    global_path.write_text(
        '[ssh.profiles.tablet]\nhost = "legacy-a"\n\n'
        '[ssh.profiles.tablet.workspace]\nmode = "existing"\n\n'
        '[ssh.workers.tablet]\nhost = "legacy-b"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_profiles, "global_config_path", lambda: global_path)
    result = ssh_profiles.add_profile("tablet", host="new-host", force=True)
    assert result["replaced"] is True
    text = global_path.read_text(encoding="utf-8")
    assert "[ssh.profiles.tablet]" not in text
    assert "[ssh.workers.tablet]" not in text
    assert "[ssh.hosts.tablet]" in text
    assert 'host = "new-host"' in text


def test_profile_remove_cleans_legacy_alias_sections(tmp_path, monkeypatch):
    from devtool import ssh_profiles

    global_path = tmp_path / "global.toml"
    global_path.write_text(
        '[ssh]\nprofile = "tablet"\n\n'
        '[ssh.workers.tablet]\nhost = "legacy"\n\n'
        '[ssh.workers.tablet.resilience]\nhost_key_policy = "strict"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(ssh_profiles, "global_config_path", lambda: global_path)
    result = ssh_profiles.remove_profile("tablet")
    assert result["cleared_default"] is True
    text = global_path.read_text(encoding="utf-8")
    assert "tablet" not in text


def test_profile_atomic_write_preserves_existing_permissions_and_scope_defaults(tmp_path, monkeypatch):
    from devtool import ssh_profiles
    import stat

    global_path = tmp_path / "global.toml"
    global_path.write_text("# existing\n", encoding="utf-8")
    global_path.chmod(0o640)
    monkeypatch.setattr(ssh_profiles, "global_config_path", lambda: global_path)
    ssh_profiles.add_profile("tablet", host="host")
    assert stat.S_IMODE(global_path.stat().st_mode) == 0o640

    repo = tmp_path / "repo"
    repo.mkdir()
    ssh_profiles.add_profile("local", host="host2", repo=repo, global_scope=False)
    assert stat.S_IMODE((repo / ".devtool.toml").stat().st_mode) == 0o644


def test_set_default_payload_reports_truth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _global(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    add_profile("tablet", host="host", repo=repo, set_default=False)
    payload = set_default_profile("tablet", repo=repo)
    assert payload["ok"] is True
    assert payload["default"] is True
    assert list_profiles(repo=repo)["default_profile"] == "tablet"
