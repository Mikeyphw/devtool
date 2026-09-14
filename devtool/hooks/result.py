from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import HookExecutionResult
from .payload import json_safe

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
    if not isinstance(version, int) or isinstance(version, bool) or version != 1:
        raise RuntimeError(f"Unsupported hook result schema_version {version!r}; expected integer 1")
    status_raw = raw.get("status", "pass")
    if not isinstance(status_raw, str):
        raise RuntimeError("Hook result status must be a string")
    status = status_raw.strip().lower() or "pass"
    if status not in _ALLOWED_STATUSES:
        raise RuntimeError("Hook result status must be one of: pass, warn, fail")
    if "summary" in raw and not isinstance(raw.get("summary"), str):
        raise RuntimeError("Hook result summary must be a string")
    warnings_raw = raw.get("warnings") or []
    if not isinstance(warnings_raw, list) or any(not isinstance(item, str) for item in warnings_raw):
        raise RuntimeError("Hook result warnings must be a list of strings")
    annotations_raw = raw.get("annotations") or []
    if not isinstance(annotations_raw, list) or any(not isinstance(item, dict) for item in annotations_raw):
        raise RuntimeError("Hook result annotations must be a list of objects")
    annotations: list[dict[str, Any]] = []
    for item in annotations_raw:
        annotation = dict(item)
        for key in ("severity", "code", "message", "summary"):
            if key in annotation and not isinstance(annotation[key], str):
                raise RuntimeError(f"Hook result annotation {key} must be a string")
        annotations.append(json_safe(annotation))
    evidence_raw = raw.get("evidence") or {}
    if not isinstance(evidence_raw, dict):
        raise RuntimeError("Hook result evidence must be an object")
    safe_evidence = json_safe(dict(evidence_raw))
    if not isinstance(safe_evidence, dict):
        raise RuntimeError("Hook result evidence must be an object")
    safe_raw = json_safe(dict(raw))
    return HookExecutionResult(
        status=status,
        summary=raw.get("summary") or "",
        warnings=list(warnings_raw),
        annotations=annotations,
        evidence=safe_evidence,
        raw=safe_raw if isinstance(safe_raw, dict) else {},
    )
