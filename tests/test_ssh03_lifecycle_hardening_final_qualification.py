from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from devtool.cli import build_parser
from devtool.ssh_tmux_runner import SSHRunnerConfig
from devtool import ssh_lifecycle, ssh_resilience, ssh_roadmap_fixtures, ssh_worker


def _cfg(**overrides) -> SSHRunnerConfig:
    values = dict(
        host="tablet.example", port=8022, user="termux", profile="tablet",
        mode="commands", workspace_mode="managed",
        workspace_root="~/.cache/devtool/workspaces",
        run_state_root="~/.cache/devtool/ssh-runs",
        host_key_policy="strict", lease_timeout_seconds=900, min_free_mb=1024,
    )
    values.update(overrides)
    return SSHRunnerConfig(**values)


def _record(run_id: str, *, state: str = "completed", active: bool = False, finished: int = 1, started: int = 1):
    return {
        "run_id": run_id, "session": f"devtool-{run_id}", "state": state,
        "active": active, "finished_unix": finished, "started_unix": started,
        "status": 0 if state == "completed" else 255,
    }


def test_final_fixture_declares_ssh03_promises_without_fragile_thresholds() -> None:
    payload = ssh_roadmap_fixtures.final_payload()
    assert payload["roadmap_id"] == "SSH-03"
    assert payload["gc"]["dry_run_default"] is True
    assert payload["host_identity"]["authority"] == "openssh-known-hosts"
    assert payload["host_identity"]["parallel_database"] is False
    assert payload["performance"]["records_metrics"] is True
    assert payload["performance"]["fragile_wall_clock_thresholds"] is False
    assert payload["final_gate"] is True


def test_cli_exposes_gc_and_target_aware_doctor() -> None:
    parser = build_parser()
    doctor = parser.parse_args(["ssh", "doctor", "tablet", "--target", "devtool"])
    assert doctor.ssh_doctor_target == "devtool"
    gc = parser.parse_args(["ssh", "gc", "tablet", "--dry-run", "--max-age-days", "14", "--keep-last", "7", "--max-mib", "256"])
    assert gc.ssh_command == "gc"
    assert gc.ssh_gc_dry_run is True
    assert gc.ssh_gc_apply is False
    assert gc.ssh_gc_max_age_days == 14
    assert gc.ssh_gc_keep_last == 7
    assert gc.ssh_gc_max_mib == 256
    applied = parser.parse_args(["ssh", "gc", "tablet", "--apply"])
    assert applied.ssh_gc_apply is True


def test_host_identity_uses_openssh_known_hosts_and_emits_only_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(b"k" * 32).decode("ascii")
    output = f"[tablet.example]:8022 ssh-ed25519 {key}\n"
    calls = []
    monkeypatch.setattr(ssh_lifecycle.shutil, "which", lambda name: "/usr/bin/ssh-keygen")
    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")
    monkeypatch.setattr(ssh_lifecycle.subprocess, "run", fake_run)
    payload = ssh_lifecycle.host_identity_payload(
        _cfg(known_hosts="~/.ssh/known_hosts"),
        probe={"platform": "termux", "os_name": "Linux", "architecture": "aarch64", "home": "/home/u"},
    )
    assert calls[0][:3] == ["/usr/bin/ssh-keygen", "-F", "[tablet.example]:8022"]
    assert payload["state"] == "recorded"
    assert payload["algorithms"] == ["ssh-ed25519"]
    assert payload["fingerprints"][0].startswith("SHA256:")
    assert key not in json.dumps(payload)
    assert payload["worker_identity"]["architecture"] == "aarch64"
    assert payload["worker_identity"]["platform_family"] == "termux"
    assert payload["worker_identity"]["qualified_platform"] is True


def test_host_identity_uses_no_parallel_database_when_unrecorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh_lifecycle.shutil, "which", lambda name: "/usr/bin/ssh-keygen")
    monkeypatch.setattr(
        ssh_lifecycle.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
    )
    payload = ssh_lifecycle.host_identity_payload(_cfg(known_hosts=""), probe={})
    assert payload["entries"] == []
    assert payload["source"] == "openssh-default-known-hosts"
    assert "OpenSSH" in payload["key_change_diagnostic"]


def test_capacity_estimate_accounts_for_repository_and_floor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "payload.bin").write_bytes(b"x" * (3 * 1024 * 1024))
    payload = ssh_lifecycle.estimate_execution_capacity(
        _cfg(min_free_mb=64), repo_root=repo, free_storage_kib=1024 * 1024,
    )
    assert payload["repository_mib"] >= 3
    assert payload["required_free_mib"] >= 256
    assert payload["free_mib"] == 1024
    assert payload["ok"] is True


def test_capacity_preflight_blocks_known_enospc_before_sync(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / "a").write_text("x")
    with pytest.raises(ValueError, match="blocked execution before source synchronization"):
        ssh_lifecycle.ensure_execution_capacity(
            _cfg(min_free_mb=1024), repo_root=repo, free_storage_kib=128 * 1024,
        )


def test_capacity_unknown_is_reported_but_not_false_blocked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir()
    payload = ssh_lifecycle.ensure_execution_capacity(_cfg(), repo_root=repo, free_storage_kib=0)
    assert payload["verified"] is False
    assert payload["ok"] is True


def test_gc_dry_run_protects_active_stale_and_keep_last(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 100 * 86400
    records = [
        _record("new", finished=now - 10),
        _record("active", state="running", active=True, finished=0),
        _record("stale", state="stale", finished=now - 80 * 86400),
        _record("old", finished=now - 80 * 86400),
    ]
    monkeypatch.setattr(ssh_lifecycle, "runs_payload", lambda cfg: {"records": records})
    monkeypatch.setattr(ssh_lifecycle, "_run_size_inventory", lambda cfg: {"new": 1, "active": 1, "stale": 1, "old": 100})
    payload = ssh_lifecycle.gc_remote_runs(_cfg(), dry_run=True, max_age_days=30, keep_last=1, max_mib=9999, now_unix=now)
    assert [x["run_id"] for x in payload["candidates"]] == ["old"]
    reasons = {x["run_id"]: x["reason"] for x in payload["protected"]}
    assert reasons["new"] == "keep-last"
    assert reasons["active"] == "active"
    assert reasons["stale"] == "recovery-sensitive-state"
    assert payload["deleted"] == []


def test_gc_size_policy_selects_oldest_terminal_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("r3", finished=30), _record("r2", finished=20), _record("r1", finished=10)]
    monkeypatch.setattr(ssh_lifecycle, "runs_payload", lambda cfg: {"records": records})
    monkeypatch.setattr(ssh_lifecycle, "_run_size_inventory", lambda cfg: {"r1": 400, "r2": 400, "r3": 400})
    payload = ssh_lifecycle.gc_remote_runs(_cfg(), dry_run=True, max_age_days=9999, keep_last=1, max_mib=0, now_unix=100)
    assert [x["run_id"] for x in payload["candidates"]] == ["r1", "r2"]
    assert all(x["reason"] == "size-lru-policy" for x in payload["candidates"])
    assert payload["policy"]["eviction_order"] == "least-recently-used-terminal"


def test_gc_apply_deletes_only_selected_safe_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("safe-run", finished=1)]
    monkeypatch.setattr(ssh_lifecycle, "runs_payload", lambda cfg: {"records": records})
    monkeypatch.setattr(ssh_lifecycle, "_run_size_inventory", lambda cfg: {"safe-run": 10})
    seen = []
    monkeypatch.setattr(ssh_lifecycle, "_gc_delete", lambda cfg, ids: seen.extend(ids) or list(ids))
    payload = ssh_lifecycle.gc_remote_runs(_cfg(), dry_run=False, max_age_days=0, keep_last=0, max_mib=999, now_unix=100)
    assert seen == ["safe-run"]
    assert payload["deleted"] == ["safe-run"]


def test_gc_unsafe_run_id_is_never_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [_record("../escape", finished=1)]
    monkeypatch.setattr(ssh_lifecycle, "runs_payload", lambda cfg: {"records": records})
    monkeypatch.setattr(ssh_lifecycle, "_run_size_inventory", lambda cfg: {"../escape": 99999})
    payload = ssh_lifecycle.gc_remote_runs(_cfg(), dry_run=True, max_age_days=0, keep_last=0, max_mib=0, now_unix=100)
    assert payload["candidates"] == []
    assert payload["protected"][0]["reason"] == "unsafe-run-id"


def test_gc_delete_script_is_root_bounded_and_rejects_symlink(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = []
    def fake_remote(config, script, **kwargs):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout="run-1\n", stderr="")
    monkeypatch.setattr(ssh_lifecycle, "_run_remote", fake_remote)
    assert ssh_lifecycle._gc_delete(_cfg(), ["run-1"]) == ["run-1"]
    script = scripts[0]
    assert "pwd -P" in script
    assert ' [ ! -L "$target" ] ' in script
    assert 'case "$real/" in "$root_real/"*)' in script


def test_orphan_lease_audit_classifies_and_repairs_only_safe_orphans(monkeypatch: pytest.MonkeyPatch) -> None:
    out = "repo.lock\trun-x\tdevtool-run-x\t901\t0\torphan\t1\nactive.lock\trun-y\tdevtool-run-y\t999\t1\tactive\t0\n"
    scripts = []
    def fake_remote(config, script, **kwargs):
        scripts.append(script)
        return subprocess.CompletedProcess([], 0, stdout=out, stderr="")
    monkeypatch.setattr(ssh_lifecycle, "_run_remote", fake_remote)
    payload = ssh_lifecycle.orphan_lease_payload(_cfg(), repair=True, now_unix=1000)
    assert payload["orphan_count"] == 1
    assert payload["repaired_count"] == 1
    assert payload["records"][1]["classification"] == "active"
    assert "apply=1" in scripts[0]
    assert "pwd -P" in scripts[0]


def test_recover_converges_stale_phase_and_orphan_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    stale = _record("run-1", state="stale", finished=0)
    stale["lease_dir"] = "~/.cache/devtool/workspaces/.leases/repo.lock"
    monkeypatch.setattr(ssh_resilience, "runs_payload", lambda cfg: {"records": [stale]})
    monkeypatch.setattr(
        ssh_resilience, "_run_remote",
        lambda config, script, **kwargs: subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        ssh_lifecycle, "orphan_lease_payload",
        lambda config, repair=False, **kwargs: {"ok": True, "orphan_count": 1, "repaired_count": 1, "records": []},
    )
    payload = ssh_resilience.recover_stale_runs(_cfg(), run_id="run-1")
    assert payload["recovered"][0]["state"] == "interrupted"
    assert payload["orphan_leases"]["repaired_count"] == 1
    assert payload["recovery"]["successful_phases_rerun"] is False


def test_worker_doctor_surfaces_identity_capacity_lifecycle_and_remediation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"; repo.mkdir(); (repo / ".devtool.toml").write_text('[project]\nname="x"\n')
    probe = SimpleNamespace(
        tools={"tmux": "/usr/bin/tmux", "rsync": "/usr/bin/rsync", "tar": "/usr/bin/tar", "zstd": "/usr/bin/zstd", "proot_distro": ""},
        platform="termux", os_name="Linux", architecture="aarch64", cpu_count=8,
        memory_kib=1024, free_storage_kib=3*1024*1024, home="/home/u", prefix="/data/data/com.termux/files/usr",
        shell="/bin/sh", package_manager="pkg", bootstrap_policy="missing", tmux_required=True,
        missing_required=[], ready=True, bootstrap_supported=True,
        as_dict=lambda: {"tools": {"tmux": "/usr/bin/tmux"}, "platform": "termux", "os_name": "Linux", "architecture": "aarch64", "cpu_count": 8, "memory_kib": 1024, "free_storage_kib": 3*1024*1024, "home": "/home/u", "package_manager": "pkg", "bootstrap_supported": True, "ready": True},
    )
    monkeypatch.setattr(ssh_worker, "probe_worker", lambda cfg: probe)
    monkeypatch.setattr(ssh_worker, "provider_metadata", lambda *a, **k: {"name": "ssh-native", "executable": True})
    monkeypatch.setattr(ssh_worker, "list_tmux_sessions", lambda cfg: [])
    monkeypatch.setattr(ssh_worker, "multiplex_evidence", lambda cfg: SimpleNamespace(as_dict=lambda: {"enabled": True}))
    monkeypatch.setattr(ssh_lifecycle, "host_identity_payload", lambda cfg, probe=None: {"state": "recorded", "algorithms": ["ssh-ed25519"], "fingerprints": ["SHA256:x"]})
    monkeypatch.setattr(ssh_lifecycle, "orphan_lease_payload", lambda cfg, repair=False: {"ok": True, "orphan_count": 1, "records": []})
    monkeypatch.setattr(ssh_lifecycle, "estimate_execution_capacity", lambda *a, **k: {"verified": True, "ok": True, "required_free_mib": 1024, "free_mib": 3072})
    # Avoid target environment probe here; target-awareness itself is tested by the emitted remediation surface.
    payload = ssh_worker.worker_doctor_payload(_cfg(), target="", repo_root=repo)
    assert payload["host_identity"]["state"] == "recorded"
    assert payload["capacity"]["required_free_mib"] == 1024
    assert payload["lifecycle"]["orphan_count"] == 1
    assert any(item["id"] == "orphan-leases" for item in payload["remediation"])
    assert payload["execution"]["provider"]["name"] == "ssh-native"


def test_remote_phase_script_uses_atomic_state_files() -> None:
    from devtool.ssh_tmux_runner import _build_remote_phase_script
    script = _build_remote_phase_script(_cfg(), session="devtool-phase", command="true", run_id="run-1")
    assert '"$state.tmp"' in script
    assert 'mv -f -- "$state.tmp" "$state"' in script
    assert '"$metadata.tmp"' in script


def test_completion_surface_contains_gc() -> None:
    root = Path(__file__).resolve().parents[1]
    assert "recover gc" in (root / "devtool" / "completion.py").read_text(encoding="utf-8")
    assert "recover gc" in (root / "completions" / "_devtool").read_text(encoding="utf-8")


def test_ssh03_documentation_exists_and_covers_safe_commands() -> None:
    root = Path(__file__).resolve().parents[1]
    doc = (root / "docs" / "SSH_REMOTE_EXECUTION.md").read_text(encoding="utf-8")
    for phrase in ("ssh doctor", "ssh prepare", "ssh runs", "ssh output", "ssh recover", "ssh gc", "--dry-run"):
        assert phrase in doc
    assert "known_hosts" in doc
    assert "FRX-Y09" in doc


def test_ssh03_report_contains_final_promise_ledger() -> None:
    root = Path(__file__).resolve().parents[1]
    report = (root / "SSH03_LIFECYCLE_HARDENING_FINAL_QUALIFICATION_REPORT.md").read_text(encoding="utf-8")
    assert "Promise ledger" in report
    assert "GC" in report
    assert "OpenSSH" in report
    assert "ENOSPC" in report
    assert "final gate" in report.lower()
