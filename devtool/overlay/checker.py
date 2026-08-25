from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..artifacts import diff_artifact, preview_artifact, read_artifact_manifest, resolve_artifact_path, validate_artifact
from .conflicts import OverlayConflict, detect_inventory_conflicts
from .manifest import manifest_summary


@dataclass(frozen=True, slots=True)
class OverlayCheckResult:
    ok: bool
    artifact: str
    structural_ok: bool
    source_state_ok: bool
    messages: tuple[str, ...]
    conflicts: tuple[OverlayConflict, ...]
    preview: dict[str, Any]
    manifest: dict[str, Any]
    diff: str = ""

    @property
    def rebaseable(self) -> bool:
        return bool(self.conflicts) and all(item.rebaseable for item in self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conflicts"] = [item.to_dict() for item in self.conflicts]
        data["rebaseable"] = self.rebaseable
        return data


def check_overlay(
    repo_root: Path,
    artifact: str | Path,
    *,
    include_diff: bool = False,
    diff_context: int = 3,
    max_diff_lines: int = 4000,
    ignore_checksum: bool = False,
) -> OverlayCheckResult:
    repo_root = repo_root.resolve()
    artifact_path = resolve_artifact_path(artifact)
    structural = validate_artifact(
        repo_root,
        artifact_path,
        artifact_type="overlay",
        check_source_state=False,
        ignore_checksum=ignore_checksum,
    )
    source = validate_artifact(
        repo_root,
        artifact_path,
        artifact_type="overlay",
        check_source_state=True,
        ignore_checksum=ignore_checksum,
    )
    manifest = read_artifact_manifest(artifact_path)
    conflicts = tuple(detect_inventory_conflicts(repo_root, manifest)) if manifest else ()
    try:
        preview = preview_artifact(repo_root, artifact_path, snapshot=False)
    except Exception as exc:
        preview = {"artifact": str(artifact_path), "files": 0, "changes": [], "counts": {}, "error": str(exc)}
    diff_text = ""
    if include_diff and structural.ok:
        try:
            diff_text = diff_artifact(repo_root, artifact_path, context=max(0, diff_context), max_lines=max(100, max_diff_lines))
        except Exception as exc:
            diff_text = f"# diff unavailable: {exc}\n"
    messages = list(dict.fromkeys([*structural.messages, *source.messages]))
    if conflicts:
        messages.append(f"Detected {len(conflicts)} repository-state conflict(s).")
    if conflicts and all(item.rebaseable for item in conflicts):
        messages.append("All detected repository-state conflicts can be rebased with `devtool overlay rebase` or apply `--rebase`.")
    summary = manifest_summary(artifact_path) if manifest else {}
    summary["raw"] = manifest
    return OverlayCheckResult(
        ok=bool(structural.ok and source.ok and not conflicts),
        artifact=str(artifact_path),
        structural_ok=bool(structural.ok),
        source_state_ok=bool(source.ok),
        messages=tuple(messages),
        conflicts=conflicts,
        preview=preview,
        manifest=summary,
        diff=diff_text,
    )
