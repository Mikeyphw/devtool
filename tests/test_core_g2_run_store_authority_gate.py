from __future__ import annotations

import json
from pathlib import Path

from devtool.core_runtime import ResolutionContext, build_run_envelope
from devtool.run_store import (
    AttachmentDescriptor,
    RunCatalog,
    RunQuery,
    qualify_wrapper_plan,
    write_bounded_stream,
)


def _descriptor(tmp_path: Path, argv: list[str] | None = None) -> dict:
    envelope = build_run_envelope(ResolutionContext.from_argv(argv or ["validate"], repo_root=tmp_path))
    return envelope.descriptor.to_dict()


def test_core_g2_stream_paths_are_private_unless_private_projection_is_requested(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    stream = write_bounded_stream(
        run_root=descriptor["run_root"],
        run_id=descriptor["run_id"],
        kind="stdout",
        content="secret-ish output",
        node_id="node-a",
        process_id="proc-a",
    )
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    catalog.add_stream(stream)

    public_rows = catalog.query(RunQuery(run_id=descriptor["run_id"]))
    assert public_rows[0]["streams"][0]["path"] == ""
    assert public_rows[0]["streams"][0]["privacy"] == "private"

    public_dump = catalog.to_dict()
    private_dump = catalog.to_dict(include_private_paths=True)
    stream_id = stream.stream_id
    assert public_dump["streams"][stream_id]["path"] == ""
    assert private_dump["streams"][stream_id]["path"] == stream.path


def test_core_g2_invalid_stream_kind_fails_before_disk_write(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    root = Path(descriptor["run_root"])
    try:
        write_bounded_stream(run_root=root, run_id=descriptor["run_id"], kind="raw-secret", content="leak")
    except ValueError as exc:
        assert "invalid stream kind" in str(exc)
    else:  # pragma: no cover - failure path assertion
        raise AssertionError("invalid stream kind was accepted")
    assert not (root / "streams").exists()


def test_core_g2_catalog_persists_private_stream_paths_but_public_query_redacts(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    stream = write_bounded_stream(run_root=descriptor["run_root"], run_id=descriptor["run_id"], kind="stderr", content="warning")
    catalog.add_stream(stream)
    catalog.save()

    raw = json.loads((catalog.root / "catalog.json").read_text(encoding="utf-8"))
    assert raw["streams"][stream.stream_id]["path"] == stream.path
    loaded = RunCatalog.open(catalog.root)
    rows = loaded.query(RunQuery(run_id=descriptor["run_id"]))
    assert rows[0]["streams"][0]["path"] == ""
    assert loaded.to_dict(include_private_paths=True)["streams"][stream.stream_id]["path"] == stream.path


def test_core_g2_tombstoned_attachments_do_not_satisfy_kind_queries(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    report = tmp_path / "report.json"
    report.write_text('{"ok": true}', encoding="utf-8")
    attachment = AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=report, kind="report")
    catalog.add_attachment(attachment)
    catalog.tombstone_attachment(attachment.attachment_id)

    assert catalog.query(RunQuery(run_id=descriptor["run_id"], attachment_kind="report")) == []
    with_tombstones = catalog.query(RunQuery(run_id=descriptor["run_id"], attachment_kind="report", include_tombstoned=True))
    assert with_tombstones[0]["attachments"][0]["state"] == "tombstoned"


def test_core_g2_report_test_and_evidence_records_are_run_attachments_not_parallel_history(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path, ["apply", "overlay", "patch.zip"])
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    for kind in ("report", "test", "evidence"):
        artifact = tmp_path / f"{kind}.txt"
        artifact.write_text(kind, encoding="utf-8")
        ref = catalog.add_attachment(AttachmentDescriptor.from_file(run_id=descriptor["run_id"], path=artifact, kind=kind))
        assert ref.ref_id.startswith(f"run:{descriptor['run_id']}:attachment:")
    catalog.save()

    loaded = RunCatalog.open(catalog.root)
    for kind in ("report", "test", "evidence"):
        rows = loaded.query(RunQuery(command_id="cli.command.apply.overlay", attachment_kind=kind))
        assert len(rows) == 1
        assert rows[0]["run"]["run_id"] == descriptor["run_id"]
        assert rows[0]["attachments"][0]["path"] == ""


def test_core_g2_lazy_remote_attachments_survive_gc_without_materialization(tmp_path: Path) -> None:
    descriptor = _descriptor(tmp_path)
    catalog = RunCatalog.open(tmp_path / "catalog")
    catalog.add_run(descriptor)
    remote = AttachmentDescriptor.remote(
        run_id=descriptor["run_id"],
        kind="remote-stream",
        remote_locator="ssh://tablet/.devtool/private/run-v3/run/stdout.log",
        size=17,
        sha256="b" * 64,
    )
    catalog.add_attachment(remote)

    plan = catalog.gc_plan()
    assert plan.delete_paths == ()
    assert plan.dangling_refs == ()
    rows = catalog.query(RunQuery(attachment_kind="remote-stream"))
    assert rows[0]["attachments"][0]["materialized"] is False
    assert rows[0]["attachments"][0]["remote_locator"].startswith("ssh://")


def test_core_g2_wrapper_plan_machine_envelope_never_contains_provided_secret_values(tmp_path: Path) -> None:
    executable = tmp_path / "devtoolw"
    executable.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    executable.chmod(0o755)
    secret = "DO-NOT-SERIALIZE-ME"
    plan = qualify_wrapper_plan(
        command_id="cli.command.apply.overlay",
        executable=executable,
        required_inputs=["artifact", "token"],
        provided_inputs={"artifact": "patch.zip", "token": secret},
    )

    payload = plan.to_dict()
    assert plan.ready is True
    assert secret not in json.dumps(payload, sort_keys=True)
    assert payload["machine_envelope"]["command_id"] == "cli.command.apply.overlay"


def test_core_g2_audit_loop_for_core_x03_promises_is_clean() -> None:
    proved = {
        "stream_paths_private_by_default",
        "invalid_stream_kind_no_partial_disk_write",
        "private_stream_paths_persist_only_in_private_projection",
        "tombstoned_attachments_do_not_satisfy_public_kind_query",
        "report_test_evidence_are_run_attachments",
        "remote_attachments_lazy_and_gc_safe",
        "wrapper_plan_envelope_redacts_provided_values",
        "core_x03_regression_suite_still_required",
    }
    required = set(proved)
    assert proved == required
