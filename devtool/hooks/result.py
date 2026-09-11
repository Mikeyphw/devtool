from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import HookExecutionResult

_ALLOWED_STATUSES = {"pass", "warn", "fail"}


def load_structured_hook_result(path: Path) -> HookExecutionResult | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"DEVTOOL_HOOK_RESULT_PATH contains invalid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("DEVTOOL_HOOK_RESULT_PATH must contain a JSON object")
    version = raw.get("schema_version", 1)
    if version != 1:
        raise RuntimeError(f"Unsupported hook result schema_version {version!r}; expected 1")
    status = str(raw.get("status") or "pass").strip().lower()
    if status not in _ALLOWED_STATUSES:
        raise RuntimeError("Hook result status must be one of: pass, warn, fail")
    warnings_raw = raw.get("warnings") or []
    if not isinstance(warnings_raw, list) or any(not isinstance(item, str) for item in warnings_raw):
        raise RuntimeError("Hook result warnings must be a list of strings")
    annotations_raw = raw.get("annotations") or []
    if not isinstance(annotations_raw, list) or any(not isinstance(item, dict) for item in annotations_raw):
        raise RuntimeError("Hook result annotations must be a list of objects")
    evidence_raw = raw.get("evidence") or {}
    if not isinstance(evidence_raw, dict):
        raise RuntimeError("Hook result evidence must be an object")
    return HookExecutionResult(
        status=status,
        summary=str(raw.get("summary") or ""),
        warnings=list(warnings_raw),
        annotations=[dict(item) for item in annotations_raw],
        evidence=dict(evidence_raw),
        raw=dict(raw),
    )
