from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from devtool.cli import _federation_command, build_parser
from devtool.config import load_project_config
from devtool.federation_gate import build_federation_gate1_report
from devtool.federation_impact import analyze_federation_impact, explain_federation_impact
from devtool.federation_interfaces import build_interface_report, interface_catalog


def _child(root: Path, *, conforming: bool = True) -> None:
    root.mkdir(parents=True, exist_ok=True)
    workflows = '''[targets.app.workflows]\nvalidate=["job:check"]\npackage=["job:check"]\n''' if conforming else '''[targets.app.workflows]\nvalidate=["job:check"]\n'''
    wrapper = '''[wrapper.commands.build]\ndevtool="build"\ntarget="app"\n''' if conforming else ''
    output = 'outputs=["out/app.apk"]\n' if conforming else ''
    (root / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n'''
        + output
        + '''[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n'''
        + workflows
        + wrapper,
        encoding="utf-8",
    )


def _config(tmp_path: Path, *, conforming: bool = True):
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    parent.mkdir()
    _child(child, conforming=conforming)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.interfaces."android-app/v1"]\nrequired_workflows=["validate","package"]\nrequired_wrapper_commands=["build"]\nrequired_artifacts=["apk"]\n[federation.groups.apps]\nrepositories=["child"]\ninterface="android-app/v1"\n''',
        encoding="utf-8",
    )
    return load_project_config(
        parent,
        parent / ".devtool.toml",
        target_name="",
        allow_ambiguous_target=True,
        allow_ambiguous_project_kind=True,
    )


def test_y06_parses_versioned_interface_and_group_binding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    interface = config.federation.interfaces["android-app/v1"]
    assert interface.required_workflows == ("validate", "package")
    assert interface.required_wrapper_commands == ("build",)
    assert interface.required_artifacts == ("apk",)
    assert config.federation.groups["apps"].interface == "android-app/v1"
    payload = interface_catalog(config)
    assert payload["interfaces"]["android-app/v1"]["groups"] == ["apps"]


def test_y06_conformance_reads_child_owned_public_surface_without_mutation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    child_config = tmp_path / "child" / ".devtool.toml"
    before = child_config.read_bytes()
    report = build_interface_report(config, group_name="apps")
    assert report.ok
    assert report.members[0].wrapper_commands == ("build",)
    assert "package" in report.members[0].workflows
    assert "apk" in report.members[0].artifacts
    assert child_config.read_bytes() == before


def test_y06_missing_child_contracts_fail_conformance_and_preflight(tmp_path: Path) -> None:
    config = _config(tmp_path, conforming=False)
    report = build_interface_report(config, interface_name="android-app/v1")
    assert not report.ok
    member = report.members[0]
    assert member.missing_workflows == ("package",)
    assert member.missing_wrapper_commands == ("build",)
    assert member.missing_artifacts == ("apk",)

    preflight = build_federation_gate1_report(config)
    checks = [check for check in preflight.checks if check.section == "interfaces"]
    assert checks and checks[0].status == "error"


def test_y06_unknown_interface_binding_fails_during_config_parse(tmp_path: Path) -> None:
    parent = tmp_path / "parent"; child = tmp_path / "child"
    parent.mkdir(); _child(child)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.child]\npath="../child"\n[federation.groups.apps]\nrepositories=["child"]\ninterface="missing/v1"\n''',
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="unknown interface"):
        load_project_config(parent, parent / ".devtool.toml", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def test_y06_cli_catalog_and_conformance_are_machine_readable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    parser = build_parser()
    for argv, expected in [
        (["-r", str(config.repo_root), "federation", "interfaces", "--json"], "interfaces"),
        (["-r", str(config.repo_root), "federation", "conformance", "--group", "apps", "--json"], "members"),
    ]:
        args = parser.parse_args(argv)
        out = io.StringIO()
        with redirect_stdout(out):
            assert _federation_command(args) == 0
        assert expected in json.loads(out.getvalue())


def test_y06_interfaces_join_y05_impact_only_through_explicit_group_binding(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = analyze_federation_impact(config, repositories=["child"])
    assert report.interfaces == ("android-app/v1",)
    assert report.interface_groups["android-app/v1"] == ("apps",)
    why = explain_federation_impact(report, "android-app/v1")
    assert why["kind"] == "interface" and why["affected"] is True
    assert why["groups"] == ["apps"]
