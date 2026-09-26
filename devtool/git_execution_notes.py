from __future__ import annotations

"""GIT-FRX-11 append-only Git notes execution provenance.

The dedicated ``refs/notes/devtool`` ref is Devtool-owned execution evidence.
Updates are built on a private temporary notes ref and published with a
compare-and-swap ``git update-ref`` so concurrent writers cannot silently
clobber one another.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
from typing import Any, Mapping

from .federation_discovery import resolve_federated_repository

DEVTOOL_NOTES_REF = "refs/notes/devtool"
EXECUTION_NOTE_SCHEMA_VERSION = 1


class GitExecutionNoteError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(root: Path, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        env = os.environ.copy()
        env.setdefault("GIT_AUTHOR_NAME", "Devtool Evidence")
        env.setdefault("GIT_AUTHOR_EMAIL", "devtool@localhost.invalid")
        env.setdefault("GIT_COMMITTER_NAME", "Devtool Evidence")
        env.setdefault("GIT_COMMITTER_EMAIL", "devtool@localhost.invalid")
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=input_text,
            env=env,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=12,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitExecutionNoteError(f"Unable to execute git {' '.join(args)}: {exc}") from exc


def _require(proc: subprocess.CompletedProcess[str], context: str) -> str:
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    detail = (proc.stderr or proc.stdout or "git command failed").strip()
    raise GitExecutionNoteError(f"{context}: {detail}")


def _resolve_commit(root: Path, revision: str) -> str:
    return _require(_git(root, "rev-parse", "--verify", f"{revision}^{{commit}}"), f"Unable to resolve commit {revision!r}")


def _ref_oid(root: Path, ref: str) -> str:
    proc = _git(root, "rev-parse", "--verify", "--quiet", ref)
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    if proc.returncode == 1:
        return ""
    return _require(proc, f"Unable to inspect {ref}")


def read_execution_note(repo_root: str | Path, revision: str = "HEAD") -> dict[str, Any]:
    root = Path(repo_root).resolve()
    commit = _resolve_commit(root, revision)
    proc = _git(root, "notes", f"--ref={DEVTOOL_NOTES_REF}", "show", commit)
    if proc.returncode == 1:
        return {
            "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
            "kind": "devtool.execution-note",
            "commit": commit,
            "records": [],
        }
    text = _require(proc, f"Unable to read Devtool execution note for {commit}")
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if not isinstance(decoded, dict) or decoded.get("kind") != "devtool.execution-note":
        return {
            "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
            "kind": "devtool.execution-note",
            "commit": commit,
            "legacy_notes": [text],
            "records": [],
        }
    payload = decoded
    records = payload.get("records")
    if not isinstance(records, list):
        raise GitExecutionNoteError(f"Existing execution note for {commit} has invalid records")
    payload.setdefault("schema_version", EXECUTION_NOTE_SCHEMA_VERSION)
    payload["commit"] = commit
    return payload


def append_execution_note(repo_root: str | Path, revision: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    commit = _resolve_commit(root, revision)
    before_ref = _ref_oid(root, DEVTOOL_NOTES_REF)
    payload = read_execution_note(root, commit)
    record = dict(evidence)
    record.setdefault("schema_version", EXECUTION_NOTE_SCHEMA_VERSION)
    record.setdefault("kind", "devtool.execution-evidence")
    record["commit"] = commit
    identity_material = {
        "parent_run_id": record.get("parent_run_id"),
        "child_run_id": record.get("child_run_id"),
        "repository": record.get("repository"),
        "target": record.get("target"),
        "contract_ref": record.get("contract_ref"),
        "lifecycle": record.get("lifecycle"),
        "frozen_lock_identity": record.get("frozen_lock_identity"),
        "artifact_inputs": record.get("artifact_inputs"),
        "artifact_outputs": record.get("artifact_outputs"),
    }
    record_id = str(record.get("record_id") or _digest(identity_material))
    record["record_id"] = record_id
    record["evidence_digest"] = _digest({key: value for key, value in record.items() if key != "evidence_digest"})

    existing_records = [item for item in payload.get("records", []) if isinstance(item, dict)]
    existing = next((item for item in existing_records if str(item.get("record_id") or "") == record_id), None)
    if existing is not None:
        if str(existing.get("evidence_digest") or "") != record["evidence_digest"]:
            raise GitExecutionNoteError(
                f"Append-only Devtool execution note conflict for record {record_id}: existing evidence differs"
            )
        return {
            "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
            "kind": "devtool.execution-note-write",
            "notes_ref": DEVTOOL_NOTES_REF,
            "commit": commit,
            "record_id": record_id,
            "evidence_digest": record["evidence_digest"],
            "records": len(existing_records),
            "notes_ref_before": before_ref,
            "notes_ref_after": before_ref,
            "idempotent": True,
        }
    records = list(existing_records)
    records.append(record)
    records.sort(key=lambda item: (str(item.get("parent_run_id") or ""), str(item.get("child_run_id") or ""), str(item.get("record_id") or "")))
    payload.update({
        "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
        "kind": "devtool.execution-note",
        "commit": commit,
        "records": records,
    })
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    temporary_ref = f"refs/notes/devtool-tmp/{os.getpid()}-{uuid.uuid4().hex}"
    try:
        if before_ref:
            _require(_git(root, "update-ref", temporary_ref, before_ref), "Unable to seed temporary Devtool notes ref")
        add = _git(root, "notes", f"--ref={temporary_ref}", "add", "-f", "-F", "-", commit, input_text=encoded)
        _require(add, f"Unable to stage Devtool execution note for {commit}")
        next_ref = _ref_oid(root, temporary_ref)
        if not next_ref:
            raise GitExecutionNoteError("Temporary Devtool notes ref did not resolve after note update")
        publish = _git(root, "update-ref", DEVTOOL_NOTES_REF, next_ref, before_ref or "")
        if publish.returncode != 0:
            detail = (publish.stderr or publish.stdout or "compare-and-swap failed").strip()
            raise GitExecutionNoteError(
                f"Concurrent Devtool notes update detected for {DEVTOOL_NOTES_REF}; refusing lost update: {detail}"
            )
    finally:
        _git(root, "update-ref", "-d", temporary_ref)

    return {
        "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
        "kind": "devtool.execution-note-write",
        "notes_ref": DEVTOOL_NOTES_REF,
        "commit": commit,
        "record_id": record_id,
        "evidence_digest": record["evidence_digest"],
        "records": len(records),
        "notes_ref_before": before_ref,
        "notes_ref_after": _ref_oid(root, DEVTOOL_NOTES_REF),
    }


def _artifact_output_digest_rows(operational: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ref in operational.get("references", []) if isinstance(operational, Mapping) else []:
        if not isinstance(ref, Mapping) or str(ref.get("kind") or "") not in {"artifact-registry", "execution-artifact"}:
            continue
        metadata = ref.get("metadata") if isinstance(ref.get("metadata"), Mapping) else {}
        if str(ref.get("kind") or "") == "artifact-registry":
            records = metadata.get("records") if isinstance(metadata.get("records"), list) else []
            for item in records:
                if not isinstance(item, Mapping):
                    continue
                digest = str(item.get("sha256") or "").strip()
                if digest:
                    rows.append({
                        "id": str(item.get("id") or ""),
                        "type": str(item.get("type") or ""),
                        "schema": str(item.get("schema") or ""),
                        "sha256": digest,
                        "state": str(item.get("state") or ""),
                        "remote_transfer": dict(item.get("remote_transfer") or {}) if isinstance(item.get("remote_transfer"), Mapping) else {},
                    })
        else:
            digest = str(metadata.get("sha256") or "").strip()
            if digest:
                rows.append({
                    "id": str(ref.get("identifier") or ""),
                    "type": str(metadata.get("artifact_type") or ""),
                    "schema": "",
                    "sha256": digest,
                    "state": str(ref.get("status") or ""),
                    "remote_transfer": dict(metadata.get("remote_transfer") or {}) if isinstance(metadata.get("remote_transfer"), Mapping) else {},
                })
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        deduped[(row["sha256"], row["id"])] = row
    return [deduped[key] for key in sorted(deduped)]


def _artifact_input_digest_rows(artifact_inputs: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, raw in artifact_inputs.items():
        if name == "__manifest__" or not isinstance(raw, Mapping):
            continue
        rows.append({
            "name": str(name),
            "producer_step": str(raw.get("producer_step") or ""),
            "artifact_id": str(raw.get("artifact_id") or raw.get("id") or ""),
            "type": str(raw.get("artifact_type") or raw.get("type") or ""),
            "schema": str(raw.get("schema") or ""),
            "sha256": str(raw.get("sha256") or ""),
        })
    return sorted(rows, key=lambda row: row["name"])


def persist_report_execution_notes(parent: Any, report: Any) -> tuple[dict[str, Any], ...]:
    """Persist execution notes for every Git-backed child result in a report.

    This runs after the coordinator has produced canonical child/run evidence so
    notes consume existing authorities instead of becoming a second run store.
    """

    source_lock = dict(getattr(report.plan, "source_lock", {}) or {})
    schedule = dict(getattr(report, "schedule", {}) or {})
    schedule_nodes = {
        str(item.get("step_id") or ""): dict(item)
        for item in schedule.get("nodes", []) if isinstance(item, Mapping)
    }
    wave_by_step: dict[str, int] = {}
    for index, wave in enumerate(schedule.get("waves", []) if isinstance(schedule.get("waves"), list) else []):
        if not isinstance(wave, Mapping):
            continue
        for step_id in wave.get("nodes", []) if isinstance(wave.get("nodes"), list) else []:
            wave_by_step[str(step_id)] = index
    workflow_name = str(getattr(report, "workflow", "") or "")
    group_name = str(getattr(report, "group", "") or "")
    group = parent.federation.groups.get(group_name) if group_name else None
    interface_name = str(getattr(group, "interface", "") or "") if group is not None else ""
    interface = parent.federation.interfaces.get(interface_name) if interface_name else None

    writes: list[dict[str, Any]] = []
    for child in getattr(report, "children", ()):
        lineage = dict(getattr(child, "git_lineage", {}) or {})
        result_commit = str(lineage.get("result_commit") or "").strip()
        if not result_commit:
            continue
        resolution = resolve_federated_repository(parent, child.repository)
        if resolution.repository_identity is None:
            continue
        repository_cfg = parent.federation.repositories.get(child.repository)
        capability_identity = {
            "exports": list(getattr(repository_cfg, "exports", ()) or ()),
            "requires": [
                {
                    "name": req.name,
                    "optional": req.optional,
                    "compatible": list(req.compatible),
                    "policy": req.policy,
                }
                for req in (getattr(repository_cfg, "requires", ()) or ())
            ],
        }
        capability_identity["digest"] = _digest(capability_identity)
        interface_identity = {
            "name": interface_name,
            "required_workflows": list(getattr(interface, "required_workflows", ()) or ()),
            "required_wrapper_commands": list(getattr(interface, "required_wrapper_commands", ()) or ()),
            "required_artifacts": list(getattr(interface, "required_artifacts", ()) or ()),
        }
        interface_identity["digest"] = _digest(interface_identity)
        evidence = {
            "parent_run_id": str(getattr(report, "parent_run_id", "") or ""),
            "child_run_id": str(child.run_id),
            "repository": str(child.repository),
            "repository_id": str(lineage.get("repository_id") or ""),
            "target": str(child.target),
            "workflow": workflow_name,
            "group": group_name,
            "step_id": str(child.step_id),
            "contract_ref": str(child.contract_ref or child.federation_ref),
            "requested_ref": str(child.requested_ref),
            "status": str(child.status),
            "exit_code": int(child.exit_code),
            "lifecycle": str(child.lifecycle or "executed"),
            "frozen_lock_identity": str(source_lock.get("lock_digest") or lineage.get("frozen_plan_identity") or ""),
            "frozen_lock_equivalence_identity": str(source_lock.get("equivalence_digest") or lineage.get("frozen_plan_equivalence_identity") or ""),
            "frozen_repository_source_identity": str(lineage.get("frozen_repository_source_identity") or ""),
            "capabilities": capability_identity,
            "interface": interface_identity,
            "artifact_inputs": _artifact_input_digest_rows(dict(child.artifact_inputs or {})),
            "artifact_outputs": _artifact_output_digest_rows(dict(child.operational_ownership or {})),
            "resume_reuse": {
                "mode": str((getattr(report, "lifecycle", {}) or {}).get("mode") or "run"),
                "resumed_from": str((getattr(report, "lifecycle", {}) or {}).get("resumed_from") or ""),
                "child_lifecycle": str(child.lifecycle or "executed"),
            },
            "scheduler": {
                "wave": wave_by_step.get(str(child.step_id), 0),
                "placement": schedule_nodes.get(str(child.step_id), {}) or {
                    "step_id": str(child.step_id),
                    "repository": str(child.repository),
                    "target": str(child.target),
                    "workflow": workflow_name,
                    "scheduling_mode": "sequential",
                    "placement": dict(getattr(child, "placement", {}) or {}),
                },
                "parallel_effective": bool(schedule.get("parallel_effective", False)),
                "max_parallel": int(schedule.get("max_parallel", 0) or 0),
            },
            "git_lineage": {
                "start_commit": str(lineage.get("start_commit") or ""),
                "result_commit": result_commit,
                "result_ref": str(lineage.get("result_ref") or ""),
                "rollback_ref": str(lineage.get("rollback_ref") or ""),
                "commit_mode": str(lineage.get("commit_mode") or ""),
                "overlay_digests": list(lineage.get("overlay_digests") or []),
                "supersession_pairs": list(lineage.get("supersession_pairs") or []),
            },
        }
        writes.append(append_execution_note(resolution.root, result_commit, evidence))
    return tuple(writes)


def _fixture() -> dict[str, Any]:
    """Exercise append-only note writes without depending on user Git identity."""
    with tempfile.TemporaryDirectory(prefix="devtool-git-frx11-") as td:
        root = Path(td) / "repo"
        root.mkdir()
        for args in (("init", "-q"), ("config", "user.name", "Devtool Fixture"), ("config", "user.email", "fixture@example.invalid")):
            proc = _git(root, *args)
            if proc.returncode != 0:
                raise GitExecutionNoteError((proc.stderr or proc.stdout or "fixture git setup failed").strip())
        (root / "README.md").write_text("fixture\n", encoding="utf-8")
        for args in (("add", "README.md"), ("commit", "-qm", "fixture")):
            proc = _git(root, *args)
            if proc.returncode != 0:
                raise GitExecutionNoteError((proc.stderr or proc.stdout or "fixture commit failed").strip())
        commit = (_git(root, "rev-parse", "HEAD").stdout or "").strip()
        first = append_execution_note(root, commit, {
            "parent_run_id": "fixture-parent",
            "child_run_id": "fixture-child-1",
            "repository": "fixture",
            "frozen_lock_identity": "fixture-lock",
            "artifact_inputs": [{"sha256": "a" * 64}],
            "artifact_outputs": [{"sha256": "b" * 64}],
            "scheduler": {"placement": {"step_id": "fixture"}},
        })
        second = append_execution_note(root, commit, {
            "parent_run_id": "fixture-parent",
            "child_run_id": "fixture-child-2",
            "repository": "fixture",
            "resume_reuse": {"mode": "resume", "child_lifecycle": "reused"},
        })
        payload = read_execution_note(root, commit)
        if len(payload.get("records", [])) != 2:
            raise GitExecutionNoteError("GIT-FRX-11 fixture did not preserve append-only note history")
        return {
            "schema_version": EXECUTION_NOTE_SCHEMA_VERSION,
            "roadmap_id": "GIT-FRX-11",
            "notes_ref": DEVTOOL_NOTES_REF,
            "commit": commit,
            "record_count": len(payload.get("records", [])),
            "first_record_digest": first.get("evidence_digest", ""),
            "latest_record_digest": second.get("evidence_digest", ""),
            "automatic_push": False,
        }


def _main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m devtool.git_execution_notes")
    parser.add_argument("command", choices=["fixture"])
    args = parser.parse_args(argv)
    if args.command != "fixture":
        raise SystemExit(2)
    print(json.dumps(_fixture(), indent=2, sort_keys=True))
    return 0


__all__ = [
    "DEVTOOL_NOTES_REF",
    "EXECUTION_NOTE_SCHEMA_VERSION",
    "GitExecutionNoteError",
    "append_execution_note",
    "persist_report_execution_notes",
    "read_execution_note",
]


if __name__ == "__main__":
    raise SystemExit(_main())
