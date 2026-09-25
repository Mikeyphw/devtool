from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from devtool import cli
from devtool.config import load_project_config
from devtool.federation_final_seal import (
    FEDERATION_FINAL_SEAL_AREAS,
    FEDERATION_FINAL_SEAL_EXTENSION_CASES,
    FEDERATION_FINAL_SEAL_EXTENSIONS,
    build_federation_final_seal_report,
    federation_final_seal_as_dict,
    validate_federation_extension_evidence,
)
from devtool.federation_gate2 import build_federation_gate2_report
from devtool.federation_plan import build_federated_group_plan, build_federated_plan


def _load(root: Path):
    with mock.patch.dict(
        os.environ,
        {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")},
        clear=False,
    ):
        return load_project_config(
            str(root),
            None,
            allow_ambiguous_target=True,
            allow_ambiguous_project_kind=True,
        )


def _write_snapshot_contract_repo(
    root: Path,
    *,
    default_target: str,
    targets: dict[str, dict[str, int]],
) -> None:
    """Write a compact plan-equivalent contract extracted from the repo audit.

    FRX-X04 does not vendor the product repositories.  This fixture preserves
    the audited repository target/workflow surface and observed workflow node
    cardinalities needed by the cross-repository planner proof.
    """

    root.mkdir(parents=True, exist_ok=True)
    lines = [
        '[project]',
        'runner="command"',
        '[workspace]',
        f'default_target="{default_target}"',
    ]
    jobs: list[tuple[str, str]] = []
    for target, workflows in targets.items():
        lines += [
            f'[targets.{target}]',
            'runner="command"',
            'root="."',
        ]
        workflow_jobs: dict[str, list[str]] = {}
        for workflow, count in workflows.items():
            names: list[str] = []
            for index in range(1, count + 1):
                name = f"{target}-{workflow}-{index:02d}"
                names.append(name)
                jobs.append((name, f"{target}:{workflow}:{index}"))
            workflow_jobs[workflow] = names
        if workflow_jobs:
            lines.append(f'[targets.{target}.workflows]')
            for workflow, names in workflow_jobs.items():
                refs = ", ".join(json.dumps(f"job:{name}") for name in names)
                lines.append(f'{workflow}=[{refs}]')
    for name, token in jobs:
        lines += [
            f'[jobs.{name}]',
            'runner="command"',
            f'command=["printf", {json.dumps(token)}]',
        ]
    (root / '.devtool.toml').write_text("\n".join(lines) + "\n", encoding="utf-8")


def _snapshot_adoption_fixture(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    repos = {
        "mike-svmods": tmp_path / "Mike_SVMods",
        "telegram-speed-hook": tmp_path / "Telegram-Speed-Hook",
        "chroot-ctl": tmp_path / "chroot-ctl",
        "mike-cli-suite": tmp_path / "mike-cli-suite",
        "scriptexts": tmp_path / "scriptexts",
        "xdm": tmp_path / "xdm",
    }

    # The default-target workflow cardinalities preserve the graph sizes proved
    # by the 2026-09-24 cross-repository federation audit:
    # validate-ready = 4 + 6 + 13 + 4 + aggregate = 28
    # build-ready    = 2 + 2 + aggregate = 5
    # test-ready     = 1 + 1 + aggregate = 3
    _write_snapshot_contract_repo(
        repos["mike-svmods"],
        default_target="svmods",
        targets={
            "svmods": {"validate": 4},
            "letmehatch": {"validate": 1},
            "eggsinwild": {"validate": 1},
            "roostercontrol": {"validate": 1},
        },
    )
    _write_snapshot_contract_repo(
        repos["telegram-speed-hook"],
        default_target="telegram_speed_hook",
        targets={
            "telegram_speed_hook": {
                "restore": 1,
                "build": 2,
                "test": 1,
                "lint": 1,
                "package": 1,
                "validate": 6,
            }
        },
    )
    _write_snapshot_contract_repo(
        repos["chroot-ctl"],
        default_target="chrootctl",
        targets={
            "chrootctl": {"validate": 13},
            "chrootctl_root_module": {
                "restore": 1,
                "build": 1,
                "test": 1,
                "lint": 1,
                "package": 1,
                "validate": 1,
                "run": 1,
            },
        },
    )
    _write_snapshot_contract_repo(
        repos["mike-cli-suite"],
        default_target="suite",
        targets={
            "suite": {"restore": 1, "build": 2, "test": 1, "validate": 4},
            "xbs": {"restore": 1, "build": 1, "test": 1, "validate": 1},
            "mike_servers": {"restore": 1, "build": 1, "test": 1, "validate": 1},
        },
    )
    _write_snapshot_contract_repo(
        repos["scriptexts"],
        default_target="root",
        targets={
            "root": {},
            "smoke-python": {"build": 1, "test": 1, "validate": 1},
        },
    )
    _write_snapshot_contract_repo(
        repos["xdm"],
        default_target="xdm_android",
        targets={
            "xdm_android": {},
            "xdm_modern": {"package": 1},
        },
    )

    coordinator = tmp_path / "coordinator"
    coordinator.mkdir()
    repository_lines = []
    for alias, root in repos.items():
        repository_lines += [
            f'[federation.repositories.{alias}]',
            f'path={json.dumps(str(root))}',
        ]
    coordinator_text = "\n".join(
        [
            '[project]',
            'runner="command"',
            '[federation]',
            'schema=1',
            *repository_lines,
            '[federation.workflows.validate-ready]',
            'depends_on=["mike-svmods::validate", "telegram-speed-hook::validate", "chroot-ctl::validate", "mike-cli-suite::validate"]',
            '[federation.workflows.build-ready]',
            'depends_on=["telegram-speed-hook::build", "mike-cli-suite::build"]',
            '[federation.workflows.test-ready]',
            'depends_on=["telegram-speed-hook::test", "mike-cli-suite::test"]',
            '[federation.groups]',
            'validate-ready=["workflow:validate-ready"]',
            'build-ready=["workflow:build-ready"]',
            'test-ready=["workflow:test-ready"]',
        ]
    ) + "\n"
    (coordinator / '.devtool.toml').write_text(coordinator_text, encoding="utf-8")
    return coordinator, repos


def _assert_parallel_cross_repo_graph(graph, expected_repositories: set[str]) -> None:
    executable = [node for node in graph.nodes if not node.synthetic]
    assert set(graph.repositories) == expected_repositories
    assert {node.repository for node in executable} == expected_repositories
    # Child-local DAG edges are allowed; cross-repository dependency edges are not.
    by_key = graph.by_key
    for node in executable:
        for dependency in node.depends_on:
            assert by_key[dependency].repository == node.repository


def test_snapshot_validate_ready_preserves_28_node_parallel_dag(tmp_path: Path) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    config = _load(coordinator)
    workflow = config.federation.workflows["validate-ready"]
    assert all(not step.depends_on for step in workflow.steps)
    graph = build_federated_plan(config, "validate-ready")
    assert len(graph.nodes) == 28
    _assert_parallel_cross_repo_graph(
        graph,
        {"mike-svmods", "telegram-speed-hook", "chroot-ctl", "mike-cli-suite"},
    )
    canonical = {node.federation_ref for node in graph.nodes if not node.synthetic}
    assert "mike-svmods::svmods::validate" in canonical
    assert "telegram-speed-hook::telegram_speed_hook::validate" in canonical
    assert "chroot-ctl::chrootctl::validate" in canonical
    assert "mike-cli-suite::suite::validate" in canonical
    group_graph = build_federated_group_plan(config, "validate-ready")
    assert len(group_graph.nodes) == 28
    assert group_graph.group == "validate-ready"


def test_snapshot_build_ready_preserves_5_node_parallel_dag(tmp_path: Path) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    config = _load(coordinator)
    workflow = config.federation.workflows["build-ready"]
    assert all(not step.depends_on for step in workflow.steps)
    graph = build_federated_plan(config, "build-ready")
    assert len(graph.nodes) == 5
    _assert_parallel_cross_repo_graph(graph, {"telegram-speed-hook", "mike-cli-suite"})
    assert len(build_federated_group_plan(config, "build-ready").nodes) == 5


def test_snapshot_test_ready_preserves_3_node_parallel_dag(tmp_path: Path) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    config = _load(coordinator)
    workflow = config.federation.workflows["test-ready"]
    assert all(not step.depends_on for step in workflow.steps)
    graph = build_federated_plan(config, "test-ready")
    assert len(graph.nodes) == 3
    _assert_parallel_cross_repo_graph(graph, {"telegram-speed-hook", "mike-cli-suite"})
    assert len(build_federated_group_plan(config, "test-ready").nodes) == 3


def test_snapshot_hidden_target_surfaces_are_now_addressable(tmp_path: Path) -> None:
    coordinator, repos = _snapshot_adoption_fixture(tmp_path)
    text = (coordinator / '.devtool.toml').read_text(encoding="utf-8")
    text += '''\n[federation.workflows.hidden-surfaces]\ndepends_on=[\n  "mike-svmods::letmehatch::validate",\n  "mike-svmods::eggsinwild::validate",\n  "mike-svmods::roostercontrol::validate",\n  "chroot-ctl::chrootctl_root_module::package",\n  "mike-cli-suite::xbs::build",\n  "mike-cli-suite::mike_servers::test",\n  "scriptexts::smoke-python::validate",\n  "xdm::xdm_modern::package",\n]\n'''
    (coordinator / '.devtool.toml').write_text(text, encoding="utf-8")
    config = _load(coordinator)
    graph = build_federated_plan(config, "hidden-surfaces")
    refs = {node.federation_ref for node in graph.nodes if not node.synthetic}
    assert refs == {
        "mike-svmods::letmehatch::validate",
        "mike-svmods::eggsinwild::validate",
        "mike-svmods::roostercontrol::validate",
        "chroot-ctl::chrootctl_root_module::package",
        "mike-cli-suite::xbs::build",
        "mike-cli-suite::mike_servers::test",
        "scriptexts::smoke-python::validate",
        "xdm::xdm_modern::package",
    }
    # Explicit targeting must not rewrite child defaults.
    assert _load(repos["mike-svmods"]).selected_target == "svmods"
    assert _load(repos["chroot-ctl"]).selected_target == "chrootctl"
    assert _load(repos["mike-cli-suite"]).selected_target == "suite"
    assert _load(repos["scriptexts"]).selected_target == "root"
    assert _load(repos["xdm"]).selected_target == "xdm_android"


def test_gate2_and_final_seal_qualify_executable_groups_and_detour_evidence(tmp_path: Path) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    config = _load(coordinator)
    gate2 = build_federation_gate2_report(config)
    assert gate2.ok is True
    checks = {item.name: item for item in gate2.checks}
    assert checks["group:validate-ready"].status == "ok"
    assert checks["group:validate-ready"].data["node_count"] == 28
    assert checks["group:build-ready"].data["node_count"] == 5
    assert checks["group:test-ready"].data["node_count"] == 3

    report = build_federation_final_seal_report(config)
    assert report.ok is True
    assert len(report.checks) == len(FEDERATION_FINAL_SEAL_AREAS) == 14
    planning = {item.area: item for item in report.checks}["graph-planning"]
    assert planning.data["workflows"] == 3
    assert planning.data["executable_groups"] == 3
    assert planning.data["workflow_nodes"] == 36
    assert planning.data["group_nodes"] == 36
    assert planning.data["nodes"] == 72

    payload = federation_final_seal_as_dict(report)
    assert payload["extension_gate"] == "FRX-X04"
    assert tuple(payload["sealed_extensions"]) == FEDERATION_FINAL_SEAL_EXTENSIONS
    assert tuple(payload["required_extension_cases"]) == FEDERATION_FINAL_SEAL_EXTENSION_CASES
    assert payload["summary"]["extension_evidence_errors"] == 0
    assert {row["status"] for row in payload["extension_evidence"]} == {"proved"}


def test_final_seal_fails_closed_when_executable_group_plan_is_invalid(tmp_path: Path) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    text = (coordinator / '.devtool.toml').read_text(encoding="utf-8")
    text += '\n[federation.groups.broken]\nmembers=["mike-svmods::missing-target::validate"]\n'
    (coordinator / '.devtool.toml').write_text(text, encoding="utf-8")
    report = build_federation_final_seal_report(_load(coordinator))
    assert report.ok is False
    planning = {item.area: item for item in report.checks}["graph-planning"]
    assert planning.status == "error"
    assert any("group:broken" in error and "missing-target" in error for error in planning.data["errors"])
    gate2 = build_federation_gate2_report(_load(coordinator))
    group_check = next(item for item in gate2.checks if item.name == "group:broken")
    assert group_check.status == "error"


def test_extension_evidence_contract_is_machine_resolvable_and_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import devtool.federation_final_seal as seal

    ok, rows, errors = validate_federation_extension_evidence()
    assert ok is True and not errors
    assert tuple(row["case_id"] for row in rows) == FEDERATION_FINAL_SEAL_EXTENSION_CASES
    assert all(row["status"] == "proved" for row in rows)

    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    original = seal.FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE
    broken = list(original)
    first = broken[0]
    broken[0] = seal.FederationSafetyEvidence(
        first.case_id,
        first.promise,
        ("tests/missing-frx-x04-evidence.py::test_missing",),
    )
    monkeypatch.setattr(seal, "FEDERATION_FINAL_SEAL_EXTENSION_EVIDENCE", tuple(broken))
    report = seal.build_federation_final_seal_report(_load(coordinator))
    assert report.ok is False
    assert len(report.checks) == 14
    assert report.extension_evidence_errors
    rendered = seal.render_federation_final_seal(report)
    assert "Extension evidence failures:" in rendered


def test_cli_group_plans_and_final_seal_expose_snapshot_adoption_contract(tmp_path: Path, capsys) -> None:
    coordinator, _ = _snapshot_adoption_fixture(tmp_path)
    env = {"DEVTOOL_GLOBAL_CONFIG": str(coordinator / "missing-global.toml")}
    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(coordinator), "federation", "plan", "--group", "validate-ready", "--json"]) == 0
    raw = json.loads(capsys.readouterr().out)
    plan = raw.get("data", raw)
    assert plan["subject_kind"] == "group"
    assert plan["group"] == "validate-ready"
    assert len(plan["nodes"]) == 28

    with mock.patch.dict(os.environ, env, clear=False):
        assert cli.main(["--repo", str(coordinator), "federation", "final-seal", "--json"]) == 0
    raw = json.loads(capsys.readouterr().out)
    seal = raw.get("data", raw)
    assert seal["extension_gate"] == "FRX-X04"
    assert seal["summary"]["extension_evidence_errors"] == 0
