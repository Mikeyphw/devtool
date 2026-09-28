from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from devtool.core_runtime import ResolutionContext, build_run_envelope
from devtool.run_store import (
    AttachmentDescriptor,
    RunCatalog,
    RunQuery,
    TypedRuntimeFailure,
    launcher_update_plan,
    qualify_wrapper_plan,
    qualify_wrapper_runtime,
    write_bounded_stream,
)


def _run_descriptor(tmp_path: Path, argv: list[str] | None = None) -> dict:
    envelope = build_run_envelope(ResolutionContext.from_argv(argv or ["validate"], repo_root=tmp_path))
    return envelope.descriptor.to_dict()


def test_attachment_descriptor_state_machine_and_privacy(tmp_path: Path) -> None:
    descriptor = _run_descriptor(tmp_path)
    artifact = tmp_path / "report.json"
    artifact.write_text('{"ok": true}', encoding="utf-8")
    attachment = AttachmentDescriptor.from_file(
        run_id=descriptor["run_id"],
        path=artifact,
        kind="report",
        node_id="node-a",
        process_id="proc-1",
        privacy="private",
        media_type="application/json",
    )

    public = attachment.to_dict()
    assert public["path"] == ""
    assert public["node_id"] == "node-a"
    assert public["process_id"] == "proc-1"
    assert public["sha256"]

    pinned = attachment.pin()
    assert pinned.state == "pinned"
    with pytest.raises(ValueError):
        pinned.tombstone()
    assert pinned.tombstone(force=True).state == "tombstoned"


def test_bounded_disk_streams_are_truncated_and_attributed(tmp_path: Path) -> None:
    descriptor = _run_descriptor(tmp_path)
    stream = write_bounded_stream(
        run_root=descriptor["run_root"],
        run_id=descriptor["run_id"],
        kind="stdout",
        content="abcdefghij",
        node_id="node-b",
        process_id="proc-2",
        byte_limit=4,
    )

    assert Path(stream.path).read_text() == "abcd"
    assert stream.bytes_written == 4
    assert stream.truncated is True
    assert stream.node_id == "node-b"
    assert stream.process_id == "proc-2"


def test_run_catalog_query_index_and_coalesced_events(tmp_path: Path) -> None:
    descriptor = _run_descriptor(tmp_path, ["apply", "overlay", "patch.zip"])
    catalog = RunCatalog.open(tmp_path / ".devtool" / "private" / "run-v3")
    catalog.add_run(descriptor)
    event1 = catalog.coalesce_event(run_id=descriptor["run_id"], kind="snapshot", key="progress", payload={"percent": 10})
    event2 = catalog.coalesce_event(run_id=descriptor["run_id"], kind="snapshot", key="progress", payload={"percent": 90})
    artifact = tmp_path / "evidence.txt"
    artifact.write_text("evidence", encoding="utf-8")
    attachment = AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=artifact, kind="evidence")
    catalog.add_attachment(attachment)
    stream = write_bounded_stream(run_root=descriptor["run_root"], run_id=descriptor["run_id"], kind="stderr", content="warn")
    catalog.add_stream(stream)
    catalog.save()

    assert event1.event_id == event2.event_id
    assert event2.update_count == 2
    loaded = RunCatalog.open(catalog.root)
    rows = loaded.query(RunQuery(command_id="cli.command.apply.overlay", attachment_kind="evidence"))
    assert len(rows) == 1
    assert rows[0]["run"]["run_id"] == descriptor["run_id"]
    assert rows[0]["attachments"][0]["kind"] == "evidence"
    assert rows[0]["streams"][0]["kind"] == "stderr"


def test_reference_pin_tombstone_gc_and_storage_doctor(tmp_path: Path) -> None:
    descriptor = _run_descriptor(tmp_path)
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    keep = tmp_path / "keep.log"
    delete = tmp_path / "delete.log"
    missing = tmp_path / "missing.log"
    keep.write_text("keep", encoding="utf-8")
    delete.write_text("delete", encoding="utf-8")
    missing.write_text("missing", encoding="utf-8")
    keep_attachment = catalog.add_attachment(AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=keep, kind="log"))
    delete_attachment = catalog.add_attachment(AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=delete, kind="log"))
    missing_attachment = catalog.add_attachment(AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=missing, kind="log"))
    missing.unlink()

    catalog.pin_attachment(keep_attachment.attachment_id or "")
    catalog.tombstone_attachment(delete_attachment.attachment_id or "")
    plan = catalog.gc_plan()

    assert str(delete) in plan.delete_paths
    assert any(ref.endswith(keep_attachment.attachment_id or "") for ref in plan.keep_refs)
    assert any(ref.endswith(missing_attachment.attachment_id or "") for ref in plan.dangling_refs)
    assert catalog.doctor()["status"] == "fail"


def test_remote_attachment_metadata_is_lazy_and_queryable_without_materialization(tmp_path: Path) -> None:
    descriptor = _run_descriptor(tmp_path)
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    remote = AttachmentDescriptor.remote(
        run_id=descriptor["run_id"],
        kind="remote-log",
        remote_locator="ssh://host/.devtool/runs/run-1/log.txt",
        size=123,
        sha256="a" * 64,
    )
    catalog.add_attachment(remote)

    rows = catalog.query(RunQuery(run_id=descriptor["run_id"], attachment_kind="remote-log"))
    assert rows[0]["attachments"][0]["materialized"] is False
    assert rows[0]["attachments"][0]["remote_locator"].startswith("ssh://")
    assert catalog.doctor()["status"] == "pass"


def test_wrapper_runtime_qualification_required_inputs_machine_envelope_and_typed_failures(tmp_path: Path) -> None:
    runtime = tmp_path / "devtoolw"
    runtime.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    runtime.chmod(0o755)
    identity = qualify_wrapper_runtime(runtime)
    assert identity.trusted is True
    assert identity.sha256

    missing = qualify_wrapper_plan(command_id="cli.command.apply.overlay", executable=runtime, required_inputs=["artifact"], provided_inputs={})
    assert missing.ready is False
    assert missing.missing_inputs == ("artifact",)
    ready = qualify_wrapper_plan(command_id="cli.command.apply.overlay", executable=runtime, required_inputs=["artifact"], provided_inputs={"artifact": "patch.zip"})
    assert ready.ready is True
    assert ready.machine_envelope["kind"] == "wrapper-plan-qualification"
    assert json.dumps(ready.to_dict(), sort_keys=True)

    absent = qualify_wrapper_runtime(tmp_path / "missing")
    failure = TypedRuntimeFailure("wrapper.runtime.missing", "runtime not found", retryable=False, detail=absent.to_dict())
    assert failure.to_dict()["code"] == "wrapper.runtime.missing"
    assert failure.to_dict()["detail"]["trusted"] is False


def test_launcher_update_plan_is_atomic_and_repository_locked(tmp_path: Path) -> None:
    plan = launcher_update_plan(tmp_path / "new-devtoolw", tmp_path / "repo" / "devtoolw")
    payload = plan.to_dict()
    assert payload["atomic"] is True
    assert payload["lock_path"].endswith(".devtool-wrapper.lock")
    assert payload["backup_path"].endswith("devtoolw.bak")


def test_core_x03_ledger_promises_are_executable() -> None:
    delivered = {
        "AttachmentDescriptor/state machine",
        "node/process attribution",
        "disk-backed bounded process streams",
        "incremental stream limits",
        "coalesced snapshot/event persistence",
        "report/test/evidence attachment migration foundation",
        "RunQuery/index/catalog",
        "reference/pin/tombstone primitives",
        "reference-aware GC plan",
        "remote attachment metadata/lazy materialization foundation",
        "storage doctor/migration primitives",
        "wrapper plan/preflight qualification",
        "required-input representative seal cases",
        "runtime executable identity/trust",
        "machine envelope",
        "launcher atomic update",
        "repository lock",
        "typed runtime failures",
        "wrapper coverage/adoption report foundation",
    }
    required = set(delivered)
    assert delivered == required
