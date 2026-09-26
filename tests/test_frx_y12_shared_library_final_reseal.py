from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from devtool.config import load_project_config
from devtool.federation_capabilities import project_capability_dependencies
from devtool.federation_impact import analyze_federation_impact
from devtool.federation_lock import FederationSourceLock, FederationSourceLockError, resolve_execution_source_lock, write_federation_source_lock
from devtool.federation_execution import FederatedChildRun, FederatedRunReport
from devtool.federation_plan import build_federated_group_plan, build_federated_plan
from devtool.federation_resume import build_resume_plan, checkpoint_report
from devtool.federation_scheduler import build_federated_schedule
from devtool.git_audit import record_audit_pass
from devtool.git_campaign import checkpoint_campaign, start_campaign
from devtool.git_execution_notes import append_execution_note
from devtool.git_final_seal import build_campaign_seal, verify_campaign_seal


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(root), *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=15)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return (proc.stdout or "").strip()


def _init_repo(root: Path, *, label: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / ".devtool.toml").write_text(
        '[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n'
        '[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\n'
        'command=["python","-c","print(\'ok\')"]\nmetadata={read_only=true}\n'
        '[targets.app.workflows]\nvalidate=["job:check"]\n', encoding="utf-8"
    )
    (root / "source.txt").write_text(label + "\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Y12 Fixture")
    _git(root, "config", "user.email", "y12@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")


def _load(parent: Path):
    with mock.patch.dict(os.environ, {"DEVTOOL_GLOBAL_CONFIG": str(parent / "missing-global.toml")}, clear=False):
        return load_project_config(parent, parent / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fixture(tmp_path: Path):
    parent = tmp_path / "control"
    platform = tmp_path / "shared-platform"
    app_a = tmp_path / "app-a"
    app_b = tmp_path / "app-b"
    transitive = tmp_path / "feature-transitive"
    for root, label in ((platform, "platform"), (app_a, "a"), (app_b, "b"), (transitive, "transitive")):
        _init_repo(root, label=label)
    parent.mkdir()
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[git]\nsigning="off"\n[workspace]\ndefault_target="root"\n[workspace.scheduler]\nmax_parallel_nodes=4\n[targets.root]\nrunner="command"\n[federation]\nschema=1\n[federation.repositories.platform]\npath="../shared-platform"\n[federation.repositories.platform.exports]\ncapabilities=["shared-platform/v1"]\n[federation.repositories.app-a]\npath="../app-a"\n[federation.repositories.app-a.exports]\ncapabilities=["app-a-api/v1"]\n[federation.repositories.app-a.requires]\ncapabilities=["shared-platform/v1"]\n[federation.repositories.app-b]\npath="../app-b"\n[federation.repositories.app-b.requires]\ncapabilities=["shared-platform/v1"]\n[federation.repositories.transitive]\npath="../feature-transitive"\n[federation.repositories.transitive.requires]\ncapabilities=["app-a-api/v1"]\n[federation.workflows.adoption]\nsteps=[\n{id="platform",ref="platform::app::validate"},\n{id="app-a",ref="app-a::app::validate"},\n{id="app-b",ref="app-b::app::validate"},\n{id="transitive",ref="transitive::app::validate"},\n]\n''', encoding="utf-8"
    )
    _git(parent, "init", "-q")
    _git(parent, "config", "user.name", "Y12 Fixture")
    _git(parent, "config", "user.email", "y12@example.invalid")
    _git(parent, "add", ".")
    _git(parent, "commit", "-qm", "control")
    return _load(parent), parent, platform, app_a, app_b, transitive


def test_shared_platform_change_marks_direct_and_transitive_consumers_and_parallelizes_safe_siblings(tmp_path: Path) -> None:
    config, _parent, platform, _app_a, _app_b, _transitive = _fixture(tmp_path)
    impact = analyze_federation_impact(config, repositories=["platform"])
    assert set(impact.affected_repositories) == {"platform", "app-a", "app-b", "transitive"}
    assert [row["path"] for row in impact.reasons["transitive"] if row["kind"] == "capability"] == [["platform", "app-a", "transitive"]]

    plan = build_federated_plan(config, "adoption")
    workflow = project_capability_dependencies(config, config.federation.workflows["adoption"])
    schedule = build_federated_schedule(workflow, plan, parallel_requested=True, max_parallel=4)
    wave_by = {step: wave.index for wave in schedule.waves for step in wave.nodes}
    assert wave_by["platform"] < wave_by["app-a"] == wave_by["app-b"] < wave_by["transitive"]

    frozen = FederationSourceLock.from_dict(plan.source_lock)
    write_federation_source_lock(config, frozen)
    (platform / "source.txt").write_text("platform-v2\n", encoding="utf-8")
    _git(platform, "add", "source.txt")
    _git(platform, "commit", "-qm", "platform v2")
    changed = _load(config.repo_root)
    changed_plan = build_federated_plan(changed, "adoption")
    with pytest.raises(FederationSourceLockError):
        resolve_execution_source_lock(changed, changed_plan, drift_policy="fail")



def test_retry_reuses_unchanged_shared_library_sibling_and_reruns_failed_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "resume-state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    config, _parent, _platform, _app_a, _app_b, _transitive = _fixture(tmp_path)
    plan = build_federated_plan(config, "adoption")
    statuses = {"platform": "succeeded", "app-a": "failed", "app-b": "succeeded", "transitive": "failed"}
    children = []
    for step in config.federation.workflows["adoption"].steps:
        node = next(n for n in plan.nodes if not n.synthetic and n.metadata.get("federation_step") == step.step_id)
        children.append(FederatedChildRun(
            step_id=step.step_id, repository=step.repository, workflow="validate", target="app",
            run_id=f"child-{step.step_id}", parent_node_id=f"federation:adoption:{step.step_id}",
            status=statuses[step.step_id], exit_code=0 if statuses[step.step_id] == "succeeded" else 1,
            repository_identity=dict(node.metadata.get("repository_identity") or {}),
            run_lineage={"parent_run_id": "shared-parent"}, operational_ownership={"references": []},
            federation_ref=f"{step.repository}::app::validate", requested_ref=step.ref,
            child_contract="workflow:validate", contract_ref=f"{step.repository}::app::workflow:validate",
            git_lineage={}, lifecycle="executed",
        ))
    checkpoint = checkpoint_report(config, FederatedRunReport("adoption", "shared-parent", "failed", 1, tuple(children), plan))
    retry = build_resume_plan(config, checkpoint, mode="retry")
    decisions = {row.step_id: row.action for row in retry.decisions}
    assert decisions["platform"] == "reused"
    assert decisions["app-b"] == "reused"
    assert decisions["app-a"] == "retried"
    assert decisions["transitive"] in {"retried", "rerun_due_to_drift"}

def test_final_y12_manifest_verifies_every_shared_platform_participant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(tmp_path / "xdg"))
    config, _parent, platform, app_a, app_b, transitive = _fixture(tmp_path)
    start_campaign(config, "shared-platform-release", workflow="adoption")
    capability_rows = {
        "platform": {"exports": ["shared-platform/v1"], "requires": []},
        "app-a": {"exports": ["app-a-api/v1"], "requires": [{"name": "shared-platform/v1"}]},
        "app-b": {"exports": [], "requires": [{"name": "shared-platform/v1"}]},
        "transitive": {"exports": [], "requires": [{"name": "app-a-api/v1"}]},
    }
    roots = {"platform": platform, "app-a": app_a, "app-b": app_b, "transitive": transitive}
    for index, (name, root) in enumerate(roots.items(), start=1):
        commit = _git(root, "rev-parse", "HEAD")
        append_execution_note(root, commit, {
            "parent_run_id": "shared-platform-parent",
            "child_run_id": f"shared-{name}",
            "repository": name,
            "target": "app",
            "contract_ref": f"{name}::app::workflow:validate",
            "lifecycle": "executed",
            "frozen_lock_identity": "shared-lock-v1",
            "frozen_lock_equivalence_identity": "shared-lock-equiv-v1",
            "frozen_repository_source_identity": f"source-{name}",
            "capabilities": capability_rows[name],
            "interface": {"name": "shared-library", "result": "conformant"},
            "scheduler": {"wave": index, "placement": {"provider_id": "local"}, "parallel_effective": True, "max_parallel": 4},
            "resume_reuse": {"mode": "run", "resumed_from": "", "child_lifecycle": "executed"},
            "artifact_inputs": [] if name == "platform" else [{"name": "platform-contract", "producer_step": "platform", "sha256": "contract-v1"}],
            "artifact_outputs": [{"id": "platform-contract", "sha256": "contract-v1"}] if name == "platform" else [],
        })
    checkpoint_campaign(config, "shared-platform-release")
    record_audit_pass(config, campaign_id="shared-platform-release", roadmap_range="GIT-FRX-01..18", gap_count=0, pass_id="y12-zero-gap")
    seal = build_campaign_seal(config, "shared-platform-release")
    rows = {row["repository"]: row for row in seal["manifest"]["repositories"]}
    assert set(rows) == set(roots)
    for name, root in roots.items():
        assert rows[name]["result_commit"] == _git(root, "rev-parse", "HEAD")
        assert rows[name]["final_tree"] == _git(root, "rev-parse", "HEAD^{tree}")
        assert rows[name]["frozen_lock_identity"] == "shared-lock-v1"
        assert rows[name]["canonical_contract_identity"].startswith(name + "::app::")
        assert rows[name]["interface_conformance"]["result"] == "conformant"
    assert rows["platform"]["capabilities"]["exports"] == ["shared-platform/v1"]
    assert rows["app-a"]["artifact_producer_consumer_digests"]["inputs"][0]["sha256"] == "contract-v1"
    assert verify_campaign_seal(config, "shared-platform-release")["ok"] is True



def _snapshot_contract_repo(root: Path, *, default_target: str, workflows: dict[str, int]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    lines = ['[project]', 'runner="command"', '[workspace]', f'default_target="{default_target}"', f'[targets.{default_target}]', 'runner="command"', 'root="."']
    jobs: list[str] = []
    if workflows:
        lines.append(f'[targets.{default_target}.workflows]')
        for workflow, count in workflows.items():
            names = [f'{default_target}-{workflow}-{i:02d}' for i in range(1, count + 1)]
            jobs.extend(names)
            refs = ', '.join(json.dumps(f'job:{name}') for name in names)
            lines.append(f'{workflow}=[{refs}]')
    for name in jobs:
        lines += [f'[jobs.{name}]', 'runner="command"', 'command=["true"]', 'metadata={read_only=true}']
    (root / '.devtool.toml').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def test_y12_preserves_mandatory_x04_28_5_3_cross_repository_graphs(tmp_path: Path) -> None:
    repos = {
        'svmods': tmp_path / 'Mike_SVMods',
        'telegram': tmp_path / 'Telegram-Speed-Hook',
        'chroot': tmp_path / 'chroot-ctl',
        'suite': tmp_path / 'mike-cli-suite',
    }
    _snapshot_contract_repo(repos['svmods'], default_target='svmods', workflows={'validate': 4})
    _snapshot_contract_repo(repos['telegram'], default_target='telegram_speed_hook', workflows={'build': 2, 'test': 1, 'validate': 6})
    _snapshot_contract_repo(repos['chroot'], default_target='chrootctl', workflows={'validate': 13})
    _snapshot_contract_repo(repos['suite'], default_target='suite', workflows={'build': 2, 'test': 1, 'validate': 4})
    control = tmp_path / 'snapshot-control'; control.mkdir()
    (control / '.devtool.toml').write_text(
        '[project]\nrunner="command"\n[federation]\nschema=1\n'
        + ''.join(f'[federation.repositories.{name}]\npath={json.dumps(str(root))}\n' for name, root in repos.items())
        + '[federation.workflows.validate-ready]\ndepends_on=["svmods::validate","telegram::validate","chroot::validate","suite::validate"]\n'
        + '[federation.workflows.build-ready]\ndepends_on=["telegram::build","suite::build"]\n'
        + '[federation.workflows.test-ready]\ndepends_on=["telegram::test","suite::test"]\n'
        + '[federation.groups]\nvalidate-ready=["workflow:validate-ready"]\nbuild-ready=["workflow:build-ready"]\ntest-ready=["workflow:test-ready"]\n',
        encoding='utf-8',
    )
    config = _load(control)
    assert len(build_federated_group_plan(config, 'validate-ready').nodes) == 28
    assert len(build_federated_group_plan(config, 'build-ready').nodes) == 5
    assert len(build_federated_group_plan(config, 'test-ready').nodes) == 3
