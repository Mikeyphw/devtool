from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.core.events import EventBus
from devtool.federation_execution import run_federated_workflow
from devtool.federation_plan import build_federated_plan
from devtool.federation_provider import FederationProviderCapability, load_federation_provider_capabilities
from devtool.federation_scheduler import build_federated_schedule
from devtool.ssh_provider_evidence import persist_provider_evidence


def _load(root: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(root / "missing-global.toml")}, clear=False):
        return load_project_config(root, root / ".devtool.toml", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _child(root: Path, *, execution_environment: str = "auto", preferred_worker: str = "", resources: dict[str, int] | None = None) -> None:
    root.mkdir(parents=True)
    metadata = []
    if preferred_worker:
        metadata.append(f'federation_preferred_worker = "{preferred_worker}"')
    for name, value in (resources or {}).items():
        metadata.append(f'"{name}" = {value}')
    metadata_block = ""
    if metadata:
        worker = metadata[:1] if preferred_worker else []
        resource_rows = metadata[1:] if preferred_worker else metadata
        metadata_block += "\n[targets.app.metadata]\n" + "\n".join(worker)
        if resource_rows:
            metadata_block += "\n[targets.app.metadata.federation_resources]\n" + "\n".join(resource_rows)
    (root / ".devtool.toml").write_text(
        f'''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[jobs.one]\nrunner="command"\ncommand=["true"]\noutputs=["out/**"]\n[targets.app]\nrunner="command"\nexecution_environment="{execution_environment}"\nroot="."\n{metadata_block}\n[targets.app.workflows]\nwork=["job:one"]\n''',
        encoding="utf-8",
    )


def _parent(tmp_path: Path, *, remote: bool = True, resources: dict[str, int] | None = None) -> Path:
    parent = tmp_path / "parent"; parent.mkdir()
    _child(tmp_path / "alpha", execution_environment="ssh" if remote else "auto", preferred_worker="tablet" if remote else "", resources=resources)
    _child(tmp_path / "beta", execution_environment="ssh" if remote else "auto", preferred_worker="tablet" if remote else "", resources=resources)
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace.scheduler]\nmax_parallel_nodes=4\nmax_local_gradle=1\nmax_ssh_oci_gradle=2\nmax_tablet_gradle=1\nmax_android_device=1\n[workspace.scheduler.provider_budgets]\n"ssh:tablet"=1\n[workspace.scheduler.resource_budgets]\n"port:adb"=1\n[federation]\nschema=1\n[federation.repositories.alpha]\npath="../alpha"\n[federation.repositories.beta]\npath="../beta"\n[federation.workflows.release]\nsteps=[{id="alpha",ref="alpha::work"},{id="beta",ref="beta::work"}]\n''',
        encoding="utf-8",
    )
    return parent


def _tablet() -> FederationProviderCapability:
    return FederationProviderCapability(
        provider_id="ssh:tablet", kind="ssh-native", profile="tablet",
        execution_environments=("ssh",), healthy=True, evidence_source="ssh-doctor",
        evidence_digest="doctor-digest", resources={"ssh-worker": 1},
        metadata={"qualified_target": "app", "worker": {"platform": "termux", "architecture": "aarch64"}},
    )


def test_child_execution_contract_is_authoritative_and_remote_placement_reaches_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _load(_parent(tmp_path))
    monkeypatch.setattr("devtool.federation_execution._provider_capabilities_for_workflow", lambda *_args, **_kwargs: (_tablet(),))
    calls: list[tuple[list[str], dict[str, str]]] = []
    report = run_federated_workflow(
        config, "release", reenter=lambda _argv: 99, snapshot_lookup=lambda _run_id: None,
        parent_run_id="frx-y09-provider", parallel=True, max_parallel=2,
        child_executor=lambda argv, env: calls.append((list(argv), dict(env))) or 0,
    )
    assert report.ok and len(calls) == 2
    assert all(argv[argv.index("--ssh-profile") + 1] == "tablet" for argv, _env in calls)
    assert all(env["DEVTOOL_FEDERATION_EXECUTION_ENVIRONMENT"] == "ssh" for _argv, env in calls)
    assert all(child.placement["provider_id"] == "ssh:tablet" for child in report.children)
    plan = build_federated_plan(config, "release")
    node = next(row for row in plan.nodes if not row.synthetic)
    assert node.metadata["execution_contract"]["allowed_execution_environments"] == ["ssh"]


def test_provider_and_named_resource_budgets_only_split_y01_safe_waves(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path, resources={"port:adb": 1}))
    workflow = config.federation.workflows["release"]
    plan = build_federated_plan(config, "release")
    schedule = build_federated_schedule(
        workflow, plan, parallel_requested=True, max_parallel=4,
        provider_capabilities=(_tablet(),), provider_budgets=config.workspace.scheduler.provider_budgets,
        resource_budgets=config.workspace.scheduler.resource_budgets,
        max_local_gradle=config.workspace.scheduler.max_local_gradle,
        max_ssh_oci_gradle=config.workspace.scheduler.max_ssh_oci_gradle,
        max_tablet_gradle=config.workspace.scheduler.max_tablet_gradle,
        max_android_device=config.workspace.scheduler.max_android_device,
    )
    assert [wave.nodes for wave in schedule.waves] == [("alpha",), ("beta",)]
    assert schedule.waves[0].index == 1 and schedule.waves[1].index == 2
    assert any("FRX-Y09 budget" in reason for reason in schedule.by_step["beta"].queue_reasons)
    assert schedule.by_step["alpha"].resource_requirements
    assert config.workspace.scheduler.provider_budgets["ssh:tablet"] == 1
    assert config.workspace.scheduler.resource_budgets["port:adb"] == 1


def test_forbidden_child_environment_is_not_overridden_by_parent_provider(tmp_path: Path) -> None:
    config = _load(_parent(tmp_path))
    workflow = config.federation.workflows["release"]
    plan = build_federated_plan(config, "release")
    local = FederationProviderCapability(
        provider_id="local", kind="local", execution_environments=("auto", "desktop-linux"),
        metadata={"current_environment": "desktop-linux"},
    )
    with pytest.raises(ValueError, match="no authoritative provider capability matches"):
        build_federated_schedule(workflow, plan, parallel_requested=True, max_parallel=2, provider_capabilities=(local,))


def test_cached_ssh03_evidence_is_consumed_without_remote_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("devtool.ssh_provider_evidence.state_dir", lambda: state)
    monkeypatch.setattr(
        "devtool.federation_provider.list_profiles",
        lambda **_kwargs: {"records": [{"name": "tablet", "execution_provider": "ssh-native", "scope": "repository"}]},
    )
    persist_provider_evidence({
        "profile": "tablet", "target": "app", "ok": True, "execution_provider": "ssh-native",
        "worker": {"platform": "termux", "architecture": "aarch64"},
        "capacity": {"free_mib": 4096}, "tools": {"python": {"path": "/usr/bin/python"}},
    }, source="ssh-doctor")
    config = _load(_parent(tmp_path))
    providers = load_federation_provider_capabilities(config, target="app")
    remote = next(row for row in providers if row.profile == "tablet")
    assert remote.healthy is True
    assert remote.metadata["qualified_target"] == "app"
    assert remote.evidence_source == "ssh-doctor"
    assert remote.evidence_digest
    assert remote.metadata["worker"]["platform"] == "termux"
