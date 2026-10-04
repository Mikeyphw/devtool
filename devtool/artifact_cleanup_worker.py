from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from .artifacts import artifact_cleanup_paths
from .cleanup_lifecycle import (
    CleanupScope,
    GeneratedOutputDescriptor,
    apply_cleanup_plan,
    build_cleanup_ownership_view,
    compile_cleanup_plan,
    persist_cleanup_receipt,
)


def _canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def cleanup_apply_source_artifacts(
    repo_root: str | Path,
    artifact_paths: Sequence[str | Path],
    *,
    reason: str,
    run_id: str = "",
) -> dict[str, Any]:
    root = _canonical(repo_root)
    candidates: list[Path] = []
    for raw_path in artifact_paths:
        artifact = _canonical(raw_path)
        for candidate in artifact_cleanup_paths(artifact):
            resolved = _canonical(candidate)
            if resolved not in candidates:
                candidates.append(resolved)

    existing = [path for path in candidates if path.exists() or path.is_symlink()]
    if not existing:
        return {
            "returncode": 0,
            "output": "Original artifact cleanup skipped; artifact(s) were already absent.",
            "failure_kind": "",
            "metadata": {"cleanup_status": "no-op"},
        }

    descriptors = [
        GeneratedOutputDescriptor.create(
            candidate,
            owner="artifact-apply-source",
            provider="artifact-apply",
            category="consumed-artifact-source",
            producer_id=f"apply-source:{candidate.name}",
            lifecycle="transient",
            rebuild_cost="unknown",
            scope=CleanupScope.PROJECT,
            managed=True,
            published=False,
            provenance={
                "delete_reason": reason,
                "explicit_apply_cleanup": True,
                "source_artifact": str(candidate),
            },
        )
        for candidate in existing
    ]
    ownership = build_cleanup_ownership_view(
        root,
        descriptors,
        policy_generation="life-r04b-fresh-process-artifact-source-cleanup-v1",
        strict_authorities=True,
    )
    plan = compile_cleanup_plan(ownership, scope=CleanupScope.PROJECT, destination="local")
    receipt = apply_cleanup_plan(plan, ownership=ownership, confirm=True)
    receipt_path = persist_cleanup_receipt(root, receipt, run_id=run_id)

    removed = [row.path for row in receipt.results if row.status == "applied"]
    protected = [
        f"{row.path}: {row.reason or row.status}"
        for row in receipt.results
        if row.status not in {"applied", "planned"}
    ]
    failures = [row for row in receipt.results if row.status == "failed"]
    expected_existing = {str(path) for path in existing}
    deleted_existing = set(removed)
    incomplete = sorted(expected_existing - deleted_existing)
    metadata = {
        "cleanup_receipt_id": receipt.receipt_id,
        "cleanup_receipt_path": str(receipt_path),
        "cleanup_status": receipt.status,
        "cleanup_worker": "fresh-process",
    }
    if failures or incomplete:
        details = protected or [str(path) for path in incomplete]
        return {
            "returncode": 1,
            "output": (
                f"Canonical LIFE cleanup refused or failed to delete original artifact(s) {reason}.\n"
                + "\n".join(details)
            ),
            "failure_kind": "artifact_cleanup_failure",
            "metadata": metadata,
        }

    if not removed:
        output = "Original artifact cleanup skipped; artifact(s) were already absent."
    else:
        output = f"Deleted original artifact(s) {reason} through canonical LIFE cleanup.\n" + "\n".join(
            f"removed: {item}" for item in removed
        )
    return {
        "returncode": 0,
        "output": output,
        "failure_kind": "",
        "metadata": metadata,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("artifact", nargs="+")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = cleanup_apply_source_artifacts(
            args.repo_root,
            args.artifact,
            reason=args.reason,
            run_id=args.run_id,
        )
    except BaseException as exc:
        payload = {
            "returncode": 1,
            "output": f"Failed canonical LIFE cleanup of original artifact(s) {args.reason}.\n{type(exc).__name__}: {exc}",
            "failure_kind": "artifact_cleanup_failure",
            "metadata": {"cleanup_worker": "fresh-process"},
        }
    print(json.dumps(payload, sort_keys=True))
    return int(payload.get("returncode") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
