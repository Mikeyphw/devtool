"""Synthetic, explicitly labeled runner-record fixtures for cleanup unit tests.

These are NOT evidence that a real overlay was applied. Real-run tests exercise
runners.py separately; these only preserve the existing subprocess assertions.
"""
import json
import uuid
from pathlib import Path

from devtool.artifact_lifecycle import ArtifactExecutionRecord
from devtool.artifact_cleanup_worker import cleanup_source_snapshot
from devtool.core_runtime import private_run_root
from devtool.run_store import AttachmentDescriptor, RunCatalog


def attach_durable_apply_evidence(repo: Path, result, *, status: str = "succeeded") -> None:
    run_id = uuid.uuid4().hex
    record = ArtifactExecutionRecord.create(
        artifact_id="artifact:synthetic-fixture", plan_id="plan:synthetic-fixture",
        run_id=run_id, qualification_receipt_id="qualification:synthetic-fixture",
        execution_status=status,
    )
    root = private_run_root(repo)
    target = root / run_id / "artifacts" / (record.record_id.replace(":", "-") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record.as_dict(), sort_keys=True))
    catalog = RunCatalog.open(root)
    catalog.add_run({
        "run_id": run_id, "artifact_id": record.artifact_id,
        "artifact_plan_id": record.plan_id,
        "artifact_qualification_receipt_id": record.qualification_receipt_id,
        "status": status,
    })
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id, path=target, kind="artifact-execution-record", media_type="application/json",
    ))
    source_path = Path(result.metadata["artifact_path"])
    sources_path = target.with_name(target.stem + "-sources.json")
    sources_path.write_text(json.dumps({
        "schema_version": 1, "repository": str(repo.resolve()),
        "run_id": run_id, "artifact_execution_record_id": record.record_id,
        "sources": [cleanup_source_snapshot(source_path)],
    }))
    catalog.add_attachment(AttachmentDescriptor.from_file(
        run_id=run_id, path=sources_path, kind="artifact-apply-source-provenance",
        media_type="application/json",
    ))
    catalog.save()
    result.metadata.update({"run_id": run_id, "artifact_execution_record_id": record.record_id})
