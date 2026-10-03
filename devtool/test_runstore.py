from __future__ import annotations

"""Canonical persisted test results for run history, IPC, and release evidence."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterable
import hashlib

from .logstore import state_dir

SCHEMA_VERSION = 1
_PERSIST_LOCK = threading.RLock()


def _root() -> Path:
    # Compatibility projection store.  Canonical R02B receipts are written under
    # _receipt_root(); this path remains a latest/index projection for older
    # consumers and manual inspection.
    return state_dir() / "test-runs"


def _receipt_root() -> Path:
    return state_dir() / "run-v3" / "test-run-receipts"


def _repo_key(repo_root: Path) -> str:
    return hashlib.sha256(str(repo_root.expanduser().resolve()).encode("utf-8")).hexdigest()[:24]


def _sha256_json(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _safe_run_id(value: str) -> str:
    text = str(value or "").strip()
    text = "".join(ch if (ch.isalnum() or ch in "._-") else "-" for ch in text).strip(".-")
    return text[:160]


def _operation_identity(operation: dict[str, Any]) -> str:
    value = str(operation.get("operation_id") or "").strip()
    if value:
        return value
    result = operation.get("test_result") if isinstance(operation, dict) else None
    rid = str(result.get("id") or "") if isinstance(result, dict) else ""
    name = str(operation.get("name") or "")
    return rid or name or "test"


def _case_map(payload: dict[str, Any] | None) -> dict[str, str]:
    found: dict[str, str] = {}
    if not isinstance(payload, dict):
        return found
    for operation in payload.get("operations", []) or []:
        if not isinstance(operation, dict):
            continue
        result = operation.get("test_result")
        if not isinstance(result, dict):
            continue
        operation_id = _operation_identity(operation)
        result_id = str(result.get("id") or "test")
        for suite in result.get("suites", []) or []:
            if not isinstance(suite, dict):
                continue
            sid = str(suite.get("id") or "")
            for case in suite.get("cases", []) or []:
                if not isinstance(case, dict):
                    continue
                cid = str(case.get("id") or "")
                if cid:
                    # The operation/result prefix prevents two independently
                    # declared tests with identical suite/case IDs colliding.
                    found[f"{operation_id}::{result_id}::{sid}::{cid}"] = str(case.get("status") or "")
    return found


def _unsuccessful(status: str) -> bool:
    return status in {"failed", "error", "cancelled", "xpass"}


def compare_test_runs(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, list[str]]:
    before, after = _case_map(previous), _case_map(current)
    before_bad = {key for key, status in before.items() if _unsuccessful(status)}
    after_bad = {key for key, status in after.items() if _unsuccessful(status)}
    after_seen = set(after)
    return {
        "new_failures": sorted(after_bad - before_bad),
        "existing_failures": sorted(after_bad & before_bad),
        # Missing cases are unknown/not-run, not fixed. Only a case observed in
        # the current run with a non-failing status can close a prior failure.
        "fixed": sorted((before_bad & after_seen) - after_bad),
    }


def _canonical_counts(result: dict[str, Any]) -> dict[str, int]:
    rich = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "cancelled": 0,
        "xfailed": 0,
        "xpassed": 0,
    }
    status_key = {
        "passed": "passed",
        "failed": "failed",
        "skipped": "skipped",
        "error": "errors",
        "cancelled": "cancelled",
        "xfail": "xfailed",
        "xpass": "xpassed",
    }
    for suite in result.get("suites", []) or []:
        if not isinstance(suite, dict):
            continue
        for case in suite.get("cases", []) or []:
            if not isinstance(case, dict):
                continue
            key = status_key.get(str(case.get("status") or ""))
            if key:
                rich[key] += 1
    rich["total"] = sum(rich.values())
    # Compatibility projections match TestResult.tests_* semantics.
    rich["compat_failed"] = rich["failed"] + rich["errors"] + rich["cancelled"] + rich["xpassed"]
    rich["compat_skipped"] = rich["skipped"] + rich["xfailed"]
    return rich


def _summary(operations: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "cancelled": 0,
        "xfailed": 0,
        "xpassed": 0,
        "total": 0,
    }
    for operation in operations:
        result = operation.get("test_result") if isinstance(operation, dict) else None
        counts = _canonical_counts(result) if isinstance(result, dict) else None
        if counts is None:
            legacy = operation.get("counts") if isinstance(operation, dict) else None
            if isinstance(legacy, dict):
                out["passed"] += int(legacy.get("passed", 0) or 0)
                out["failed"] += int(legacy.get("failed", 0) or 0)
                out["skipped"] += int(legacy.get("skipped", 0) or 0)
                out["total"] += int(legacy.get("total", 0) or 0)
            continue
        out["passed"] += counts["passed"]
        # Preserve old summary meaning while retaining the richer buckets too.
        out["failed"] += counts["compat_failed"]
        out["skipped"] += counts["compat_skipped"]
        out["errors"] += counts["errors"]
        out["cancelled"] += counts["cancelled"]
        out["xfailed"] += counts["xfailed"]
        out["xpassed"] += counts["xpassed"]
        out["total"] += counts["total"]
    return out


def _artifacts(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        result = operation.get("test_result") if isinstance(operation, dict) else None
        if not isinstance(result, dict):
            continue
        for artifact in result.get("artifacts", []) or []:
            if not isinstance(artifact, dict) or not artifact.get("path"):
                continue
            value = dict(artifact)
            key = (str(value.get("kind") or ""), str(value.get("path") or ""))
            if key in seen:
                continue
            seen.add(key)
            found.append(value)
    return found




def _execution_provenance(metadata: dict[str, Any]) -> dict[str, Any]:
    from .federation_transport import federation_transport_provenance, require_current_source_trace

    require_current_source_trace(metadata, context="RUN test-operation evidence")
    federation = federation_transport_provenance(metadata).get("federation", {})
    returned = metadata.get("ssh_return") if isinstance(metadata.get("ssh_return"), dict) else {}
    mode = str(metadata.get("execution_mode") or "").strip()
    is_ssh = mode in {"ssh-command", "ssh-proot-oci"} or bool(metadata.get("ssh_destination"))
    if is_ssh:
        provenance: dict[str, Any] = {
            "transport": "ssh",
            "profile": str(metadata.get("ssh_profile") or ""),
            "destination": str(metadata.get("ssh_destination") or ""),
            "worker_platform": str(metadata.get("ssh_worker_platform") or ""),
            "worker_architecture": str(metadata.get("ssh_worker_architecture") or ""),
            "workspace_id": str(metadata.get("ssh_workspace_id") or ""),
            "workspace_instance_id": str(metadata.get("ssh_workspace_instance_id") or ""),
            "workspace_path": str(metadata.get("ssh_workspace_path") or metadata.get("ssh_cwd") or ""),
            "source_sync_id": str(metadata.get("ssh_source_sync_id") or ""),
            "source_revision": dict(metadata.get("ssh_source_revision") or {}) if isinstance(metadata.get("ssh_source_revision"), dict) else {},
            "session": str(metadata.get("ssh_session") or ""),
            "return_state": str(returned.get("state") or ""),
            "return_manifest": str(metadata.get("ssh_return_manifest") or ""),
            "return_recoverable": bool(returned.get("recoverable")),
        }
    else:
        provenance = {}
        if mode:
            provenance["transport"] = mode
        environment = str(metadata.get("execution_environment") or "").strip()
        if environment:
            provenance["execution_environment"] = environment
        if mode == "proot-distro":
            provenance["distribution"] = str(
                metadata.get("proot_distribution")
                or metadata.get("distribution")
                or metadata.get("distro")
                or ""
            )
    if federation:
        provenance["federation"] = federation
    return {key: value for key, value in provenance.items() if value not in ("", None, {}, [])}




def _attach_execution_provenance(payload: dict[str, Any], provenance: dict[str, Any]) -> dict[str, Any]:
    if not provenance:
        return payload
    updated = dict(payload)
    metadata = updated.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["execution_provenance"] = dict(provenance)
    updated["metadata"] = metadata
    return updated

def _build_artifact_records(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    artifacts = metadata.get("artifacts") if isinstance(metadata.get("artifacts"), dict) else {}
    records: list[dict[str, Any]] = []
    for item in artifacts.get("items", []) or []:
        if isinstance(item, dict) and item.get("path"):
            records.append(dict(item))
    return records


def _report_records(metadata: dict[str, Any]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    tests = metadata.get("tests") if isinstance(metadata.get("tests"), dict) else {}
    candidates: list[Any] = [*(tests.get("report_files") or [])]
    for key in ("test_report", "coverage_report", "package_report"):
        if metadata.get(key):
            candidates.append(metadata.get(key))
    for value in candidates:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            found.append(text)
    return found


def _top_level_build_artifacts(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        for item in operation.get("build_artifacts", []) or []:
            if not isinstance(item, dict) or not item.get("path"):
                continue
            key = (str(item.get("type") or item.get("kind") or ""), str(item.get("path") or ""))
            if key in seen:
                continue
            seen.add(key)
            found.append(dict(item))
    return found


def _top_level_logs(operations: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        value = str(operation.get("log_path") or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def normalize_test_run_payload(value: Any) -> dict[str, Any] | None:
    """Upgrade tolerant legacy test-history shapes into the current schema."""
    if not isinstance(value, dict):
        return None
    payload = dict(value)
    # Early experimental snapshots sometimes nested the canonical payload under
    # `tests`; accept that shape so older run history remains consumable.
    nested = payload.get("tests")
    if not payload.get("operations") and isinstance(nested, dict) and nested.get("operations"):
        merged = dict(nested)
        for key in ("run_id", "repository", "updated_at"):
            if key in payload and key not in merged:
                merged[key] = payload[key]
        payload = merged
    if not payload.get("operations") and isinstance(payload.get("test_result"), dict):
        result = dict(payload["test_result"])
        payload["operations"] = [{
            "name": str(payload.get("name") or result.get("id") or "test"),
            "operation_id": str(result.get("id") or payload.get("name") or "test"),
            "test_result": result,
        }]
    operations = [dict(item) for item in (payload.get("operations") or []) if isinstance(item, dict)]
    payload["operations"] = operations
    payload["schema_version"] = SCHEMA_VERSION
    payload["summary"] = _summary(operations)
    payload["artifacts"] = _artifacts(operations)
    payload["build_artifacts"] = _top_level_build_artifacts(operations)
    payload["logs"] = _top_level_logs(operations)
    payload.setdefault("comparison", {"new_failures": [], "existing_failures": [], "fixed": []})
    return payload


def _operation_from_result(result: Any) -> dict[str, Any] | None:
    metadata = getattr(result, "metadata", {}) or {}
    canonical = metadata.get("test_result") if isinstance(metadata, dict) else None
    if not isinstance(canonical, dict):
        return None
    spec_id = str(metadata.get("test_spec_id") or canonical.get("id") or getattr(result, "name", "test"))
    scope = str(
        metadata.get("target")
        or metadata.get("bundle_target")
        or metadata.get("matrix")
        or metadata.get("workspace")
        or ""
    ).strip()
    operation_id = f"{scope}:{spec_id}" if scope else spec_id
    counts = _canonical_counts(canonical)
    provenance = _execution_provenance(metadata)
    return {
        "name": str(getattr(result, "name", "")),
        "operation_id": operation_id,
        "counts": {
            "passed": counts["passed"],
            "failed": counts["compat_failed"],
            "skipped": counts["compat_skipped"],
            "total": counts["total"],
        },
        "test_result": _attach_execution_provenance(canonical, provenance),
        "log_path": str(getattr(result, "log_path", "") or ""),
        "reports": _report_records(metadata),
        "build_artifacts": _build_artifact_records(metadata),
        "provenance": provenance,
    }


def build_run_payload(repo_root: Path, results: Iterable[Any], *, run_id: str = "") -> dict[str, Any] | None:
    operations = [operation for result in results if (operation := _operation_from_result(result)) is not None]
    if not operations:
        return None
    rid = _safe_run_id(str(run_id or os.environ.get("DEVTOOL_RUN_ID") or ""))
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": rid,
        "repository": str(repo_root.expanduser().resolve()),
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "operations": operations,
    }
    return normalize_test_run_payload(payload)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        return normalize_test_run_payload(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


def load_test_run(run_id: str) -> dict[str, Any] | None:
    safe = _safe_run_id(run_id)
    if not safe:
        return None
    return _read(_root() / f"{safe}.json")


def latest_test_run(repo_root: Path, *, exclude_run_id: str = "") -> dict[str, Any] | None:
    root = _root()
    if not root.is_dir():
        return None
    repository = str(repo_root.expanduser().resolve())
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.stem == exclude_run_id:
            continue
        value = _read(path)
        if isinstance(value, dict) and value.get("repository") == repository:
            return value
    return None


def _merge_same_run(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    if (
        not existing
        or existing.get("run_id") != incoming.get("run_id")
        or existing.get("repository") != incoming.get("repository")
    ):
        return incoming
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for operation in [*(existing.get("operations") or []), *(incoming.get("operations") or [])]:
        if not isinstance(operation, dict):
            continue
        key = _operation_identity(operation)
        if key not in by_id:
            order.append(key)
        # A later result for the same stable operation replaces the earlier one
        # instead of double-counting retries within the same logical run.
        by_id[key] = dict(operation)
    merged = dict(existing)
    merged.update({k: v for k, v in incoming.items() if k != "operations"})
    merged["operations"] = [by_id[key] for key in order]
    return normalize_test_run_payload(merged) or incoming



def reconcile_ssh_return_recovery(results: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Reconcile explicit SSH evidence-recovery state into persisted test runs.

    Recovery must not change stable operation/TestResult identity or case counts;
    it only refreshes return provenance for the matching run/session.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        run_id = _safe_run_id(str(item.get("run_id") or ""))
        session = str(item.get("session") or "").strip()
        if run_id and session:
            grouped.setdefault(run_id, []).append(dict(item))

    runs_updated = 0
    operations_updated = 0
    root = _root()
    with _PERSIST_LOCK:
        for run_id, items in grouped.items():
            path = root / f"{run_id}.json"
            payload = _read(path)
            if not isinstance(payload, dict):
                continue
            by_session = {str(item.get("session") or ""): item for item in items}
            changed = False
            operations = payload.get("operations") if isinstance(payload.get("operations"), list) else []
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                provenance = operation.get("provenance") if isinstance(operation.get("provenance"), dict) else {}
                session = str(provenance.get("session") or "")
                recovered = by_session.get(session)
                if recovered is None:
                    continue
                state = str(recovered.get("state") or "partial")
                returncode = int(recovered.get("returncode", -1) or 0)
                updated_provenance = dict(provenance)
                updated_provenance["return_state"] = state
                updated_provenance["return_recoverable"] = state != "complete"
                updated_provenance["returncode"] = returncode
                operation["provenance"] = updated_provenance

                canonical = operation.get("test_result") if isinstance(operation.get("test_result"), dict) else None
                if canonical is not None:
                    canonical_metadata = canonical.get("metadata") if isinstance(canonical.get("metadata"), dict) else {}
                    execution = canonical_metadata.get("execution_provenance") if isinstance(canonical_metadata.get("execution_provenance"), dict) else {}
                    if execution:
                        execution = dict(execution)
                        execution["return_state"] = state
                        canonical_metadata = dict(canonical_metadata)
                        canonical_metadata["execution_provenance"] = execution
                        canonical["metadata"] = canonical_metadata
                operations_updated += 1
                changed = True
            if not changed:
                continue
            payload["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            root.mkdir(parents=True, exist_ok=True)
            tmp = root / f".{run_id}.{threading.get_ident()}.recovery.json.tmp"
            tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, path)
            runs_updated += 1
    return {"runs_updated": runs_updated, "operations_updated": operations_updated}

def write_canonical_test_run_receipt(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Persist the canonical RUN-V3 test receipt and return its path/ref.

    The older state_dir()/test-runs JSON file is retained only as a derived
    projection.  The receipt binds the exact repository, run identity, operation
    payload, testcase results, reports/logs and a digest over the immutable
    content.
    """

    if not isinstance(payload, dict) or not payload.get("operations"):
        raise ValueError("canonical test receipt requires one or more operations")
    repo = Path(repo_root).expanduser().resolve()
    run_id = _safe_run_id(str(payload.get("run_id") or ""))
    if not run_id:
        raise ValueError("canonical test receipt requires a run_id")
    receipt = {
        "schema_version": 1,
        "authority": "RUN-V3 TestRunReceipt",
        "repository": str(repo),
        "repo_key": _repo_key(repo),
        "run_id": run_id,
        "selection_refs": list(dict.fromkeys(
            str((op.get("test_result") or {}).get("metadata", {}).get("canonical_test_selection", {}).get("selection_id") or "")
            for op in payload.get("operations", []) if isinstance(op, dict)
        )),
        "environment_refs": list(dict.fromkeys(
            str((op.get("test_result") or {}).get("metadata", {}).get("test_environment_qualification", {}).get("qualification_id") or "")
            for op in payload.get("operations", []) if isinstance(op, dict)
        )),
        "operations": list(payload.get("operations") or []),
        "comparison": dict(payload.get("comparison") or {}),
        "updated_at": str(payload.get("updated_at") or ""),
    }
    receipt["receipt_id"] = "trr-" + _sha256_json({k: v for k, v in receipt.items() if k != "receipt_id"})[:24]
    receipt["sha256"] = _sha256_json(receipt)
    root = _receipt_root() / _repo_key(repo)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{run_id}.json"
    tmp = root / f".{run_id}.{threading.get_ident()}.receipt.tmp"
    tmp.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path, receipt


def _receipt_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "sha256"}


def valid_canonical_test_run_receipt(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("authority") != "RUN-V3 TestRunReceipt":
        return False
    digest = str(payload.get("sha256") or "").strip()
    if not digest:
        return False
    return digest == _sha256_json(_receipt_digest_payload(payload))


def latest_canonical_test_run_receipt(repo_root: Path, *, exclude_run_id: str = "") -> dict[str, Any] | None:
    root = _receipt_root() / _repo_key(repo_root)
    if not root.is_dir():
        return None
    excluded = _safe_run_id(exclude_run_id)
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if excluded and path.stem == excluded:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if valid_canonical_test_run_receipt(payload):
            return payload
    return None


def failed_test_ids_from_receipt(payload: dict[str, Any] | None) -> list[str]:
    failed_statuses = {"failed", "error", "cancelled", "xpass"}
    values: list[str] = []
    if not isinstance(payload, dict):
        return values
    for operation in payload.get("operations", []) or []:
        if not isinstance(operation, dict):
            continue
        result = operation.get("test_result")
        if not isinstance(result, dict):
            continue
        for suite in result.get("suites", []) or []:
            if not isinstance(suite, dict):
                continue
            for case in suite.get("cases", []) or []:
                if not isinstance(case, dict):
                    continue
                if str(case.get("status") or "").strip().lower() in failed_statuses:
                    cid = str(case.get("id") or "").strip()
                    if cid:
                        values.append(cid)
    return list(dict.fromkeys(values))


def latest_failed_test_ids(repo_root: Path) -> list[str]:
    return failed_test_ids_from_receipt(latest_canonical_test_run_receipt(repo_root))


def persist_test_run(repo_root: Path, results: Iterable[Any], *, run_id: str = "") -> Path | None:
    incoming = build_run_payload(repo_root, results, run_id=run_id)
    if incoming is None:
        return None
    root = _root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{incoming['run_id']}.json"
    # Parallel workflow nodes execute run_specs concurrently. Serialize the
    # read/merge/write cycle so every node contributes to one canonical run.
    with _PERSIST_LOCK:
        current = _merge_same_run(_read(path), incoming)
        previous = latest_test_run(repo_root, exclude_run_id=str(current["run_id"]))
        current["comparison"] = compare_test_runs(previous, current)
        current["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        receipt_path, receipt = write_canonical_test_run_receipt(repo_root, current)
        current["authority"] = "projection"
        current["canonical_authority"] = "RUN-V3 TestRunReceipt"
        current["canonical_receipt_path"] = str(receipt_path)
        current["canonical_receipt_id"] = str(receipt.get("receipt_id") or "")
        current["canonical_receipt_sha256"] = str(receipt.get("sha256") or "")
        tmp = root / f".{current['run_id']}.{threading.get_ident()}.json.tmp"
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return path
