from __future__ import annotations

"""Canonical persisted test results for run history, IPC, and release evidence."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any, Iterable

from .logstore import state_dir

SCHEMA_VERSION = 1
_PERSIST_LOCK = threading.RLock()


def _root() -> Path:
    return state_dir() / "test-runs"


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
    return {
        "name": str(getattr(result, "name", "")),
        "operation_id": operation_id,
        "counts": {
            "passed": counts["passed"],
            "failed": counts["compat_failed"],
            "skipped": counts["compat_skipped"],
            "total": counts["total"],
        },
        "test_result": canonical,
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
        tmp = root / f".{current['run_id']}.{threading.get_ident()}.json.tmp"
        tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return path
