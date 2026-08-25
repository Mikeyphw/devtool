from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..artifacts import artifact_inventory_entries, sha256_file


@dataclass(frozen=True, slots=True)
class OverlayConflict:
    path: str
    kind: str
    action: str
    expected: str = ""
    actual: str = ""
    detail: str = ""
    rebaseable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_inventory_conflicts(repo_root: Path, manifest: dict[str, Any]) -> list[OverlayConflict]:
    apply = manifest.get("apply") if isinstance(manifest.get("apply"), dict) else {}
    policy = str(apply.get("conflict_policy") or "fail").strip().lower()
    conflicts: list[OverlayConflict] = []
    for entry in artifact_inventory_entries(manifest):
        path = str(entry.get("path") or "").strip()
        action = str(entry.get("action") or "").strip().lower()
        if not path or action not in {"add", "modify", "delete"}:
            continue
        target = repo_root / Path(path)
        expected = str(entry.get("source_sha256") or "").strip().lower()
        if action == "add":
            if target.exists() and policy == "fail":
                actual = sha256_file(target) if target.is_file() else "<directory>"
                conflicts.append(OverlayConflict(
                    path, "add-existing", action, "<missing>", actual,
                    "Overlay declares an add but the target already exists.",
                    rebaseable=target.is_file(),
                ))
            continue
        if not target.exists() or not target.is_file():
            conflicts.append(OverlayConflict(
                path, "missing-source", action, expected, "<missing>",
                f"Overlay declares {action} but the source file is missing.",
                rebaseable=action == "modify",
            ))
            continue
        actual = sha256_file(target).lower()
        if expected and actual != expected:
            conflicts.append(OverlayConflict(
                path, "source-sha-mismatch", action, expected, actual,
                "Repository file changed since the overlay inventory was generated.",
                rebaseable=True,
            ))
    return conflicts
