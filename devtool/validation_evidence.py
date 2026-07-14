from __future__ import annotations

"""Preserve failure evidence outside repository rollback boundaries."""

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any, Iterable

from .logstore import state_dir


def _paths(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key in {"report_files", "path", "report_path", "log_path"}:
                yield from _paths(item)
            elif isinstance(item, (dict, list, tuple)):
                yield from _paths(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _paths(item)


def preserve_validation_evidence(repo_root: Path, results: list[Any], *, run_id: str = "") -> Path | None:
    failed = any(int(getattr(item, "returncode", 0) or 0) != 0 and getattr(item, "name", "") != "apply-rollback" for item in results)
    if not failed:
        return None
    identifier = run_id.strip() or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = state_dir() / "evidence" / identifier
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, str]] = []
    seen: set[Path] = set()
    for result in results:
        candidates = [str(getattr(result, "log_path", "") or "")]
        candidates.extend(_paths(getattr(result, "metadata", {}) or {}))
        for raw in candidates:
            if not raw:
                continue
            source = Path(raw).expanduser()
            if not source.is_absolute():
                source = (repo_root / source).resolve()
            if not source.is_file() or source in seen:
                continue
            seen.add(source)
            name = source.name
            target = destination / name
            index = 2
            while target.exists():
                target = destination / f"{source.stem}-{index}{source.suffix}"
                index += 1
            shutil.copy2(source, target)
            copied.append({"source": str(source), "preserved": str(target)})
    summary = {
        "run_id": identifier,
        "repository": str(repo_root),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "copied": copied,
        "results": [
            {
                "name": str(getattr(item, "name", "")),
                "returncode": int(getattr(item, "returncode", 0) or 0),
                "failure_kind": str(getattr(item, "failure_kind", "") or ""),
                "log_path": str(getattr(item, "log_path", "") or ""),
                "metadata": getattr(item, "metadata", {}) or {},
            }
            for item in results
        ],
    }
    (destination / "evidence.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    return destination
