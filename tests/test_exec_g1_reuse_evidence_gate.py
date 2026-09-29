from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from devtool.core.events import EventBus
from devtool.core.reuse import (
    ReuseHistoryIndex,
    annotate_spec_reuse,
    fingerprint_for_spec,
    output_manifest_for_spec,
    qualify_candidate,
)
from devtool.models import CommandSpec, ProjectConfig
from devtool.runners import run_specs


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "exec-g1@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "EXEC-G1"], cwd=path, check=True)
    (path / "input.txt").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "input.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)
    return path


def _candidate(fp: dict, output: dict, *, run: str = "run-source", node: str = "node-source") -> dict:
    return {
        "schema_version": 2,
        "node_definition_id": fp["node_definition_id"],
        "source_run_id": run,
        "source_node_id": node,
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": fp,
        "output_manifest": output,
        "result_identity": "result:" + fp["digest"][:16],
        "evidence_refs": [],
    }


def test_missing_or_mutated_output_cannot_silently_reuse_pass(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    (root / "out.bin").write_bytes(b"good")
    spec = CommandSpec("build", ["tool", "build"], cwd=str(root), metadata={"workflow_inputs": ["input.txt"], "workflow_outputs": ["out.bin"]})
    fp = fingerprint_for_spec(spec, repo_root=root)[0]
    candidate = _candidate(fp.to_dict(), output_manifest_for_spec(spec).to_dict())
    assert qualify_candidate(fp.to_dict(), candidate, cwd=root).disposition == "reuse"
    (root / "out.bin").write_bytes(b"mutated")
    mutated = qualify_candidate(fp.to_dict(), candidate, cwd=root)
    assert mutated.disposition == "invalidated"
    assert "mutated-output" in mutated.reason
    (root / "out.bin").unlink()
    missing = qualify_candidate(fp.to_dict(), candidate, cwd=root)
    assert missing.disposition == "invalidated"
    assert "missing-output" in missing.reason


def test_materialize_requires_verified_digest_addressed_provenance(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    material = root / "artifact.bin"
    material.write_bytes(b"artifact")
    import hashlib
    digest = hashlib.sha256(material.read_bytes()).hexdigest()
    (root / "out.bin").write_bytes(b"good")
    spec = CommandSpec(
        "build", ["tool", "build"], cwd=str(root),
        metadata={
            "workflow_inputs": ["input.txt"],
            "workflow_outputs": ["out.bin"],
            "reuse_materialization_refs": [f"file:{material}#sha256={digest}"],
        },
    )
    fp = fingerprint_for_spec(spec, repo_root=root)[0]
    output = output_manifest_for_spec(spec).to_dict()
    candidate = _candidate(fp.to_dict(), output)
    (root / "out.bin").unlink()
    result = qualify_candidate(fp.to_dict(), candidate, cwd=root)
    assert result.disposition == "materialize"
    assert result.proof_refs == (f"file:{material}#sha256={digest}",)
    material.write_bytes(b"tampered")
    result = qualify_candidate(fp.to_dict(), candidate, cwd=root)
    assert result.disposition == "invalidated"


def test_unknown_old_and_corrupt_reuse_metadata_falls_back_to_run(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    spec = CommandSpec("test", ["tool", "test"], cwd=str(root), metadata={"workflow_inputs": ["input.txt"]})
    fp = fingerprint_for_spec(spec, repo_root=root)[0].to_dict()
    output = output_manifest_for_spec(spec).to_dict()
    cases = [
        {},
        {"schema_version": 1},
        {**_candidate(fp, output), "schema_version": "broken"},
        {**_candidate(fp, output), "fingerprint": {"digest": fp["digest"]}},
    ]
    assert all(qualify_candidate(fp, case, cwd=root).disposition == "run" for case in cases)


def test_reuse_materialize_and_run_are_distinct_gate_outcomes(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    (root / "out.bin").write_bytes(b"ok")
    spec = CommandSpec("build", ["tool", "build"], cwd=str(root), metadata={"workflow_inputs": ["input.txt"], "workflow_outputs": ["out.bin"]})
    fp = fingerprint_for_spec(spec, repo_root=root)[0].to_dict()
    candidate = _candidate(fp, output_manifest_for_spec(spec).to_dict())
    assert qualify_candidate(fp, candidate, cwd=root).disposition == "reuse"
    assert qualify_candidate(fp, {}, cwd=root).disposition == "run"
    # MATERIALIZE is separately covered with verified provenance above; this assertion
    # keeps the canonical planning vocabulary explicit at the milestone boundary.
    from devtool.core.execution_semantics import ReuseDisposition
    assert {ReuseDisposition.REUSE.value, ReuseDisposition.MATERIALIZE.value, ReuseDisposition.RUN.value} == {"reuse", "materialize", "run"}


def test_shadow_qualification_matches_fresh_exo_execution_and_does_not_fabricate_processes(monkeypatch, tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    bus = EventBus()
    monkeypatch.setattr("devtool.runners.publish_event", bus.publish)
    monkeypatch.setattr("devtool.progress_events.publish_event", bus.publish)
    spec = CommandSpec(
        "fresh-build",
        [sys.executable, "-c", "from pathlib import Path; Path('out.bin').write_bytes(b'fresh')"],
        cwd=str(root),
        metadata={
            "workflow_graph": True,
            "workflow_node_key": "gate.build.fresh",
            "workflow_node_spec_index": 1,
            "workflow_node_spec_total": 1,
            "workflow_inputs": ["input.txt"],
            "workflow_outputs": ["out.bin"],
        },
    )
    annotate_spec_reuse(spec, repo_root=root)
    results, _ = run_specs(ProjectConfig(repo_root=root, kind="generic"), [spec], verbose=False, event_run_id="exec-g1-fresh")
    assert results[0].returncode == 0
    snapshot = bus.execution_store.snapshot("exec-g1-fresh")
    node = snapshot.node("gate.build.fresh")
    record = node.metadata["reusable_node_record"]
    assert record["reusable"] is True
    process_count = len(snapshot.processes)
    current = fingerprint_for_spec(spec, repo_root=root)[0].to_dict()
    prediction = qualify_candidate(current, record, cwd=root)
    assert prediction.disposition == "reuse", prediction
    # Qualification derives from EXO-owned terminal history and cannot invent a
    # ProcessStarted event or mutate the source execution record.
    assert len(bus.execution_store.snapshot("exec-g1-fresh").processes) == process_count
    assert bus.execution_store.snapshot("exec-g1-fresh").node("gate.build.fresh").metadata["reusable_node_record"] == record


def test_reuse_index_is_rebuildable_only_from_exo_history(tmp_path: Path) -> None:
    history = tmp_path / "history"
    run = history / "run-1"
    run.mkdir(parents=True)
    record = {
        "schema_version": 2,
        "node_definition_id": "node:def@abc",
        "source_run_id": "run-1",
        "source_node_id": "node-1",
        "execution_status": "succeeded",
        "qualification_complete": True,
        "reusable": True,
        "fingerprint": {"digest": "fp"},
        "output_manifest": {"digest": "out"},
        "result_identity": "result-1",
    }
    (run / "execution.json").write_text(json.dumps({"execution": {"nodes": [{"metadata": {"reusable_node_record": record}}]}}), encoding="utf-8")
    first = ReuseHistoryIndex.from_history_directory(history)
    second = ReuseHistoryIndex.from_history_directory(history)
    assert first.candidates("node:def@abc") == second.candidates("node:def@abc") == (record,)
    assert sorted(p.relative_to(history).as_posix() for p in history.rglob("*") if p.is_file()) == ["run-1/execution.json"]
