from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from devtool import cli
from devtool.config import load_project_config
from devtool.doctor import doctor_payload
from devtool.federation_gate import build_federation_gate1_report, federation_gate1_as_dict


def _child_config(*, target: str = "child") -> str:
    return f'''\n[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "{target}"\n\n[targets.{target}]\nrunner = "command"\n\n[targets.{target}.jobs.echo]\nrunner = "command"\ncommand = ["echo", "child"]\n\n[wrapper]\nschema = 1\ndefault_command = "build"\n\n[wrapper.commands.build]\ndescription = "Child build"\ndevtool = "build"\n'''.lstrip()


def _parent_config(path: str, *, required: bool = True) -> str:
    required_line = "" if required else "required = false\n"
    return f'''\n[project]\nrunner = "command"\n\n[workspace]\ndefault_target = "parent"\n\n[targets.parent]\nrunner = "command"\n\n[federation]\nschema = 1\n\n[federation.repositories.child]\npath = "{path}"\n{required_line}\n'''.lstrip()


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(str(root), None, allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _check(report, section: str, name: str):
    return next(item for item in report.checks if item.section == section and item.name == name)


def test_gate1_passes_isolated_parent_and_child_and_probes_public_surfaces(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")

    report = build_federation_gate1_report(_load(parent))
    payload = federation_gate1_as_dict(report)

    assert report.ok is True
    assert report.status == "ok"
    assert report.mode == "federated"
    assert payload["summary"]["errors"] == 0
    assert _check(report, "boundary", "repository:child").status == "ok"
    assert _check(report, "boundary", "repository:child:reload").status == "ok"
    assert _check(report, "public-contract", "child:child:wrapper").status == "ok"
    assert _check(report, "public-contract", "child:child:target-local-jobs").status == "ok"
    assert _check(report, "exo", "child:child:repository-identity").status == "ok"


def test_gate1_optional_missing_child_is_nonfatal_but_required_missing_fails_closed(tmp_path: Path) -> None:
    optional = tmp_path / "optional"; optional.mkdir()
    (optional / ".devtool.toml").write_text(_parent_config("../missing", required=False), encoding="utf-8")
    optional_report = build_federation_gate1_report(_load(optional))
    assert optional_report.ok is True
    assert _check(optional_report, "boundary", "repository:child").status == "optional"

    required = tmp_path / "required"; required.mkdir()
    (required / ".devtool.toml").write_text(_parent_config("../missing", required=True), encoding="utf-8")
    # FRX-02 config loading itself is intentionally fail-closed for a required missing child.
    with pytest.raises(SystemExit, match="required federation repository"):
        _load(required)


def test_gate1_standalone_repo_is_explicitly_compatible(tmp_path: Path) -> None:
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    report = build_federation_gate1_report(_load(tmp_path))
    assert report.ok is True
    assert report.mode == "standalone"
    assert _check(report, "compatibility", "standalone").status == "ok"
    assert _check(report, "exo", "owner:repository-identity").status == "ok"


def test_gate1_nested_child_without_config_never_inherits_parent_scope(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = parent / "tools" / "child"
    child.mkdir(parents=True)
    (parent / ".devtool.toml").write_text(_parent_config("tools/child"), encoding="utf-8")
    report = build_federation_gate1_report(_load(parent))
    boundary = _check(report, "boundary", "repository:child")
    reload_check = _check(report, "boundary", "repository:child:reload")
    assert report.ok is True
    assert boundary.data.get("config_path") is None or boundary.data.get("config_path") == ""
    assert reload_check.data.get("selected_target") is None or reload_check.data.get("selected_target") == ""


def test_gate1_fails_if_isolated_reload_changes_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")

    import devtool.federation_gate as gate
    real = gate._load_child_config
    def poisoned(resolution):
        loaded = real(resolution)
        object.__setattr__(loaded, "repository_identity", _load(parent).repository_identity)
        return loaded
    monkeypatch.setattr(gate, "_load_child_config", poisoned)

    report = build_federation_gate1_report(_load(parent))
    assert report.ok is False
    assert _check(report, "boundary", "repository:child:reload").status == "error"


def test_gate1_fails_if_exo_drops_repository_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    import devtool.federation_gate as gate
    monkeypatch.setattr(gate, "_exo_identity_probe", lambda config, run_id: (False, {}))
    report = build_federation_gate1_report(_load(tmp_path))
    assert report.ok is False
    assert _check(report, "exo", "owner:repository-identity").status == "error"


def test_cli_preflight_json_and_failure_exit_code(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    env = {"DEVTOOL_GLOBAL_CONFIG": str(tmp_path / "missing.toml")}
    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(tmp_path), "federation", "preflight", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    payload = payload.get("data", payload)
    assert payload["gate"] == "FRX-04"
    assert payload["mode"] == "standalone"
    assert payload["ok"] is True

    import devtool.cli as cli_module
    from devtool.federation_gate import FederationGateReport, FederationGateCheck
    bad = FederationGateReport(1, "FRX-04", str(tmp_path), "standalone", "error", (
        FederationGateCheck("identity", "owner", "error", "forced"),
    ))
    monkeypatch.setattr(cli_module, "build_federation_gate1_report", lambda config: bad)
    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(tmp_path), "federation", "gate1", "--json"]) == 1


def test_doctor_contains_gate1_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".devtool.toml").write_text('[project]\nrunner="command"\n', encoding="utf-8")
    config = _load(tmp_path)
    # Keep host-specific probes out; exercise real federation gate insertion directly.
    import devtool.doctor as doctor_module
    checks = doctor_module.collect_doctor_checks(config)
    gate = next(item for item in checks if item.section == "federation" and item.name == "gate1")
    assert gate.status == "ok"
    assert "FRX-04 status=ok" in gate.detail


def test_gate_report_never_executes_child_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); child.mkdir()
    (parent / ".devtool.toml").write_text(_parent_config("../child"), encoding="utf-8")
    (child / ".devtool.toml").write_text(_child_config(), encoding="utf-8")
    import subprocess
    calls = []
    real_run = subprocess.run
    def guarded(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and command[0] == "echo":
            calls.append(command)
            raise AssertionError("child job executed during gate")
        return real_run(*args, **kwargs)
    monkeypatch.setattr(subprocess, "run", guarded)
    assert build_federation_gate1_report(_load(parent)).ok is True
    assert calls == []
