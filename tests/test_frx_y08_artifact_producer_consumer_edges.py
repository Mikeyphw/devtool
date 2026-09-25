from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from devtool.artifact_registry import register_paths
from devtool.config import load_project_config
from devtool.federation_artifacts import FederationArtifactError, resolve_artifact_inputs
from devtool.federation_execution import FederatedChildRun, run_federated_workflow
from devtool.federation_plan import build_federated_plan, project_with_federation_group
from devtool.federation_resume import build_resume_plan, load_checkpoint


def _git(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, timeout=10)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True, timeout=10)


def _load(root: Path):
    return load_project_config(root, root / ".devtool.toml", target_name="", allow_ambiguous_target=True, allow_ambiguous_project_kind=True)


def _fixture(tmp_path: Path, *, typed: bool = False):
    parent = tmp_path / "parent"
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    parent.mkdir(); producer.mkdir(); consumer.mkdir()
    child = '''[project]\nrunner="command"\n[workspace]\ndefault_target="app"\n[targets.app]\nrunner="command"\n[targets.app.jobs.check]\nrunner="command"\ncommand=["echo","ok"]\n[targets.app.workflows]\nvalidate=["job:check"]\n'''
    for root in (producer, consumer):
        (root / ".devtool.toml").write_text(child, encoding="utf-8")
        (root / "source.txt").write_text("base\n", encoding="utf-8")
        _git(root)
    value = '{ref="contracts:artifact:schemas.json",type="json",schema="contracts/v1"}' if typed else '"contracts:artifact:schemas.json"'
    (parent / ".devtool.toml").write_text(
        '''[project]\nrunner="command"\n[workspace]\ndefault_target="root"\n[targets.root]\nrunner="command"\n'''
        '''[federation]\nschema=1\n[federation.repositories.platform]\npath="../producer"\n'''
        '''[federation.repositories.app]\npath="../consumer"\n[federation.workflows.integration]\n'''
        f'''steps=[{{id="contracts",ref="platform::app::workflow:validate"}},{{id="app",ref="app::app::workflow:validate",depends_on=["contracts"],inputs={{contracts={value}}}}}]\n''',
        encoding="utf-8",
    )
    return _load(parent), producer, consumer


def _artifact(root: Path, run_id: str, content: bytes = b'{"schema":1}\n') -> Path:
    path = root / ".devtool" / "build-artifacts" / "schemas.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    register_paths(
        root, [path],
        metadata={"run_id": run_id, "type": "json", "artifact_schema": "contracts/v1", "producer": "fixture"},
    )
    return path


def test_config_parses_typed_artifact_input_and_plan_exposes_edge(tmp_path: Path) -> None:
    config, _producer, _consumer = _fixture(tmp_path, typed=True)
    step = config.federation.workflows["integration"].steps[1]
    assert len(step.artifact_inputs) == 1
    spec = step.artifact_inputs[0]
    assert (spec.name, spec.producer_step, spec.selector, spec.artifact_type, spec.schema) == (
        "contracts", "contracts", "schemas.json", "json", "contracts/v1"
    )
    plan = build_federated_plan(config, "integration")
    nodes = [n for n in plan.nodes if n.metadata.get("federation_step") == "app"]
    assert nodes and nodes[0].metadata["artifact_inputs"][0]["producer_step"] == "contracts"


def test_artifact_input_requires_predecessor_and_rejects_path_selector(tmp_path: Path) -> None:
    config, _producer, _consumer = _fixture(tmp_path)
    path = config.repo_root / ".devtool.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('depends_on=["contracts"],', ''), encoding="utf-8")
    with pytest.raises(SystemExit, match="predecessor"):
        _load(config.repo_root)
    path.write_text(text.replace("schemas.json", "build/schemas.json"), encoding="utf-8")
    with pytest.raises(SystemExit, match="registry id/name"):
        _load(config.repo_root)


def test_end_to_end_materializes_registry_digest_without_producer_path_leak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    config, producer, consumer = _fixture(tmp_path, typed=True)
    observed: dict[str, object] = {}

    def reenter(argv: list[str]) -> int:
        root = Path(argv[argv.index("--repo") + 1]).resolve()
        run_id = os.environ["DEVTOOL_RUN_ID"]
        if root == producer.resolve():
            artifact = _artifact(producer, run_id)
            observed["digest"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        elif root == consumer.resolve():
            payload = json.loads(os.environ["DEVTOOL_FEDERATION_ARTIFACT_INPUTS"])
            row = payload["contracts"]
            assert row["registry_id"] and row["sha256"] == observed["digest"]
            assert row["type"] == "json" and row["schema"] == "contracts/v1"
            assert not row["path"].startswith("/")
            assert str(producer) not in json.dumps(payload)
            materialized = consumer / row["path"]
            assert materialized.read_bytes() == b'{"schema":1}\n'
            observed["consumer"] = row
        return 0

    report = run_federated_workflow(config, "integration", reenter=reenter, snapshot_lookup=lambda _run_id: None)
    assert report.ok and observed.get("consumer")
    child = next(row for row in report.children if row.step_id == "app")
    edge = child.artifact_inputs["contracts"]
    assert edge["producer_step"] == "contracts"
    assert edge["sha256"] == observed["digest"]
    assert edge["materialized_path"].startswith(".devtool/federation-inputs/")


def test_producer_digest_mutation_invalidates_consumer_reuse_without_source_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    monkeypatch.setattr("devtool.federation_resume.state_dir", lambda: state)
    monkeypatch.setattr("devtool.federation_git_lineage.state_dir", lambda: state)
    config, producer, consumer = _fixture(tmp_path)

    def reenter(argv: list[str]) -> int:
        root = Path(argv[argv.index("--repo") + 1]).resolve()
        if root == producer.resolve():
            _artifact(producer, os.environ["DEVTOOL_RUN_ID"], b"v1\n")
        elif root == consumer.resolve():
            assert "DEVTOOL_FEDERATION_ARTIFACT_INPUTS" in os.environ
        return 0

    first = run_federated_workflow(config, "integration", reenter=reenter, snapshot_lookup=lambda _run_id: None)
    assert first.ok
    artifact = producer / ".devtool" / "build-artifacts" / "schemas.json"
    artifact.write_bytes(b"v2-mutated\n")
    resume = build_resume_plan(config, load_checkpoint(config, first.parent_run_id), mode="resume")
    by_id = {row.step_id: row for row in resume.decisions}
    assert by_id["contracts"].action == "reused"
    assert by_id["app"].action == "rerun_due_to_drift"
    assert "consumed artifact digest changed: contracts" in by_id["app"].reasons


def test_remote_artifact_requires_complete_ssh02_lifecycle(tmp_path: Path) -> None:
    config, producer, consumer = _fixture(tmp_path)
    artifact = _artifact(producer, "producer-run")
    registry = json.loads((producer / ".devtool" / "artifacts" / "registry.json").read_text())
    record = dict(registry["items"][0])
    record["remote_transfer"] = {
        "produced": True, "staged": True, "transferred": True,
        "verified": False, "restored_locally": False, "state": "partial",
    }
    ownership = {
        "root": str(producer),
        "references": [{"kind": "artifact-registry", "metadata": {"records": [record]}}],
    }
    producer_child = FederatedChildRun(
        "contracts", "platform", "validate", "app", "producer-run", "node", "succeeded", 0,
        operational_ownership=ownership,
    )
    step = config.federation.workflows["integration"].steps[1]
    with pytest.raises(FederationArtifactError, match="SSH-02 state"):
        resolve_artifact_inputs(step, {"contracts": producer_child}, consumer_root=consumer, parent_run_id="parent")
    record["remote_transfer"].update({"verified": True, "restored_locally": True, "state": "complete"})
    edges = resolve_artifact_inputs(step, {"contracts": producer_child}, consumer_root=consumer, parent_run_id="parent")
    assert edges["contracts"]["sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert edges["contracts"]["remote_transfer"]["restored_locally"] is True


def test_group_expansion_remaps_artifact_producer_step_ids(tmp_path: Path) -> None:
    config, _producer, _consumer = _fixture(tmp_path)
    path = config.repo_root / ".devtool.toml"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('\n[federation.groups.release]\nmembers=["integration"]\n')
    grouped = _load(config.repo_root)
    scoped, workflow = project_with_federation_group(grouped, "release")
    consumer_step = next(step for step in workflow.steps if step.repository == "app")
    assert consumer_step.artifact_inputs
    artifact_input = consumer_step.artifact_inputs[0]
    assert artifact_input.producer_step.startswith("g")
    assert artifact_input.ref.startswith(artifact_input.producer_step + ":artifact:")
    plan = build_federated_plan(scoped, workflow.name)
    assert plan.nodes
