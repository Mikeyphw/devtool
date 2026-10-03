from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from devtool.core.events import EventBus
from devtool.core.reuse import (
    annotate_spec_reuse,
    output_manifest_for_spec,
    result_identity,
)
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "input.txt").write_text("input\n", encoding="utf-8")
    return root


def _wire_bus(monkeypatch) -> EventBus:
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    return bus


def _spec(root: Path, *, name: str = "build", materialization_ref: str = "") -> CommandSpec:
    metadata = {
        "workflow_graph": True,
        "workflow_node_key": f"remed.r02a.{name}",
        "workflow_node_spec_index": 1,
        "workflow_node_spec_total": 1,
        "workflow_inputs": ["input.txt"],
        "workflow_outputs": ["out.bin"],
        "exec_authoritative_reuse": True,
    }
    if materialization_ref:
        metadata["reuse_materialization_refs"] = [materialization_ref]
    return CommandSpec(
        name,
        [sys.executable, "-c", "from pathlib import Path; Path('spawned.txt').write_text('spawned')"],
        cwd=str(root),
        metadata=metadata,
    )


def _candidate_for_spec(spec: CommandSpec, *, root: Path, source_run_id: str = "source-run") -> dict:
    annotate_spec_reuse(spec, repo_root=root)
    fingerprint = dict(spec.metadata["reuse_fingerprint"])
    output_manifest = output_manifest_for_spec(spec).to_dict()
    evidence_refs = ["history:source-run:node"]
    return {
        "schema_version": 2,
        "node_definition_id": fingerprint["node_definition_id"],
        "source_run_id": source_run_id,
        "source_node_id": str(spec.metadata["workflow_node_key"]),
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": fingerprint,
        "output_manifest": output_manifest,
        "result_identity": result_identity(
            fingerprint_digest=fingerprint["digest"],
            output_manifest_digest=output_manifest["digest"],
            execution_status="succeeded",
            evidence_refs=evidence_refs,
        ),
        "evidence_refs": evidence_refs,
    }


def _write_history(history: Path, candidate: dict) -> None:
    run = history / str(candidate["source_run_id"])
    run.mkdir(parents=True, exist_ok=True)
    (run / "execution.json").write_text(
        json.dumps({"execution": {"nodes": [{"metadata": {"reusable_node_record": candidate}}]}}),
        encoding="utf-8",
    )


def test_r02a_authoritative_reuse_skips_real_process_and_persists_reuse_disposition(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    history = tmp_path / "history"
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    bus = _wire_bus(monkeypatch)

    (root / "out.bin").write_bytes(b"cached-output")
    spec = _spec(root, name="reuse")
    _write_history(history, _candidate_for_spec(spec, root=root))

    # If the scheduler is merely predictive, this file will appear. R02A must
    # prove that authoritative REUSE bypasses the process spawn boundary.
    (root / "spawned.txt").unlink(missing_ok=True)
    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="r02a-reuse")

    assert results[0].returncode == 0
    assert results[0].metadata["execution_disposition"] == "reuse"
    assert results[0].metadata["process_executed"] is False
    assert not (root / "spawned.txt").exists()
    snapshot = bus.execution_store.snapshot("r02a-reuse")
    assert snapshot is not None
    assert len(snapshot.processes) == 0
    node = snapshot.node("remed.r02a.reuse")
    assert node is not None
    record = node.metadata["reusable_node_record"]
    assert record["disposition"] == "reuse"
    assert record["source_run_id"] == "r02a-reuse"
    assert record["disposition"] == "reuse"
    assert node.metadata["exec_scheduler_decision"]["source_run_id"] == "source-run"


def test_r02a_materialize_restores_verified_output_without_process_spawn(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    history = tmp_path / "history"
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"cached-output")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    ref = f"file:{artifact}#sha256={digest}"
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    bus = _wire_bus(monkeypatch)

    (root / "out.bin").write_bytes(b"cached-output")
    spec = _spec(root, name="materialize", materialization_ref=ref)
    _write_history(history, _candidate_for_spec(spec, root=root, source_run_id="source-materialize"))
    (root / "out.bin").unlink()
    (root / "spawned.txt").unlink(missing_ok=True)

    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="r02a-materialize")

    assert results[0].returncode == 0
    assert results[0].metadata["execution_disposition"] == "materialize"
    assert results[0].metadata["materialized"] is True
    assert results[0].metadata["process_executed"] is False
    assert (root / "out.bin").read_bytes() == b"cached-output"
    assert not (root / "spawned.txt").exists()
    snapshot = bus.execution_store.snapshot("r02a-materialize")
    assert snapshot is not None
    assert len(snapshot.processes) == 0
    node = snapshot.node("remed.r02a.materialize")
    assert node is not None
    assert node.metadata["reusable_node_record"]["disposition"] == "materialize"


def test_r02a_missing_or_mismatched_history_fails_closed_to_real_execution(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    history = tmp_path / "history"
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    _write_history(history, {
        "schema_version": 2,
        "node_definition_id": "wrong-node",
        "source_run_id": "wrong-run",
        "source_node_id": "wrong-node",
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": {"digest": "wrong"},
        "output_manifest": {"digest": "wrong"},
        "result_identity": "wrong",
    })
    bus = _wire_bus(monkeypatch)

    spec = _spec(root, name="run")
    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="r02a-run")

    assert results[0].returncode == 0
    assert results[0].metadata.get("execution_disposition") != "reuse"
    assert (root / "spawned.txt").read_text(encoding="utf-8") == "spawned"
    snapshot = bus.execution_store.snapshot("r02a-run")
    assert snapshot is not None
    assert len(snapshot.processes) == 1
    node = snapshot.node("remed.r02a.run")
    assert node is not None
    assert node.metadata["reusable_node_record"]["disposition"] == "run"


def test_r02a_reuse_index_ignores_legacy_projection_files(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    history = tmp_path / "history"
    legacy = tmp_path / "execution"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    monkeypatch.setenv("DEVTOOL_EXECUTION_PROJECTION_DIR", str(legacy))
    (legacy / "reuse-index.json").write_text(json.dumps({"disposition": "reuse"}), encoding="utf-8")
    bus = _wire_bus(monkeypatch)

    spec = _spec(root, name="legacy-projection")
    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="r02a-legacy")

    assert results[0].returncode == 0
    assert (root / "spawned.txt").exists(), "legacy execution projection must not authorize reuse"
    snapshot = bus.execution_store.snapshot("r02a-legacy")
    assert snapshot is not None
    assert len(snapshot.processes) == 1



def test_r02a_public_metadata_cannot_disable_scheduler_boundary(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    history = tmp_path / "history"
    monkeypatch.setenv("DEVTOOL_EXECUTION_HISTORY_DIR", str(history))
    bus = _wire_bus(monkeypatch)

    (root / "out.bin").write_bytes(b"cached-output")
    spec = _spec(root, name="forged-disable")
    spec.metadata["exec_scheduler_enabled"] = False
    _write_history(history, _candidate_for_spec(spec, root=root))
    (root / "spawned.txt").unlink(missing_ok=True)

    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="r02a-forged-disable")

    assert results[0].returncode == 0
    assert results[0].metadata["execution_disposition"] == "reuse"
    assert results[0].metadata["process_executed"] is False
    assert not (root / "spawned.txt").exists(), "metadata must not bypass the canonical scheduler"
    snapshot = bus.execution_store.snapshot("r02a-forged-disable")
    assert snapshot is not None
    node = snapshot.node("remed.r02a.forged-disable")
    assert node is not None
    assert node.metadata["exec_scheduler_decision"]["authoritative"] is True

def test_r02a_normal_cli_persists_execution_history_and_run_attachments(monkeypatch, tmp_path: Path) -> None:
    from devtool import cli
    from devtool.core_runtime import private_run_root
    from devtool.run_store import RunCatalog

    repo = tmp_path / "cli-repo"
    state = tmp_path / "xdg-state"
    repo.mkdir()
    script = repo / "build.py"
    script.write_text("from pathlib import Path\nPath('cli-out.txt').write_text('ok')\nprint('ok')\n", encoding="utf-8")
    (repo / ".devtool.toml").write_text(
        """
[project]
runner = "command"
default_target = "demo"

[jobs.build]
runner = "command"
command = ["python3", "build.py"]
outputs = ["cli-out.txt"]

[targets.demo]
runner = "command"
root = "."

[targets.demo.workflows]
build = [
  { id = "build", ref = "job:build", depends_on = [] }
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEVTOOL_XDG_DIR", str(state))
    monkeypatch.setenv("DEVTOOL_NO_AUTO_HUD", "1")
    monkeypatch.delenv("DEVTOOL_RUN_ID", raising=False)
    monkeypatch.delenv("DEVTOOL_PROJECT_EXECUTION_ENVIRONMENT", raising=False)
    monkeypatch.delenv("DEVTOOL_EXECUTION_ENVIRONMENT", raising=False)

    rc = cli.main([
        "-r", str(repo),
        "--target", "demo",
        "--execution-environment", "host",
        "build",
    ])

    assert rc == 0
    history_root = state / "history"
    histories = sorted(history_root.glob("*/execution.json"))
    assert histories, "normal CLI path must persist execution history"
    document = json.loads(histories[-1].read_text(encoding="utf-8"))
    run_id = document["run_id"]
    assert document["execution"]["status"] == "succeeded"
    assert (state / "execution" / run_id / "execution.json").is_file()
    catalog = RunCatalog.open(private_run_root(repo), create=False)
    row = catalog.runs.get(run_id)
    assert row is not None
    refs = row.get("execution_attachment_refs") or {}
    assert {"execution-snapshot", "execution-checkpoint", "execution-history"}.issubset(set(refs))
    # The catalog stores private attachments by opaque run refs; the actual
    # production files are verified above at their canonical history/projection
    # locations, and the attachment descriptors retain their byte hashes.
    for kind, ref_id in refs.items():
        attachment_id = str(ref_id).split(":attachment:", 1)[-1]
        attachment = catalog.attachments.get(attachment_id)
        assert attachment is not None, (kind, ref_id)
        assert attachment.kind == kind
        assert attachment.sha256
