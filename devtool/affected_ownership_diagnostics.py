"""Read-only diagnosis of Devtool affected-ownership declarations.

The canonical loader remains authoritative. Historical target metadata is not
mistaken for a supported target-level ownership mode.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .config import load_wrapper_project_config


def diagnose(repo: Path, *, config_path: Path | None = None) -> dict:
    root = Path(repo).expanduser().resolve()
    source = Path(config_path).expanduser().resolve() if config_path else root / ".devtool.toml"
    if not source.is_file() or not source.is_relative_to(root):
        raise ValueError("ownership diagnosis requires a config file inside the repository")
    # The canonical, schema-validated loader already exposes the target
    # declarations and metadata. Do not independently reparse TOML: doing so
    # creates an unreviewed second configuration authority.
    config = load_wrapper_project_config(str(root), str(source))
    findings: list[dict] = []
    modes: dict[str, str] = {}
    for name, target in config.targets.items():
        effective = target.affected_ownership
        modes[name] = effective
        metadata = target.metadata
        if "affected_ownership" in metadata:
            findings.append({
                "target": name,
                "code": "IGNORED_METADATA_AFFECTED_OWNERSHIP",
                "severity": "migration-required",
                "declared_metadata_value": metadata["affected_ownership"],
                "effective_mode": effective,
                "detail": "metadata.affected_ownership is ignored by Devtool's canonical loader",
                "migration": (
                    f'Place affected_ownership = "inputs-only" under [targets.{name}] '
                    'after reviewing target inputs; remove the stale metadata key. '
                    'No automatic behavioral changes are made.'
                ),
            })
        # The canonical loader normalizes declared target roots into absolute
        # paths. Compare resolved roots, not the pre-normalized TOML literal.
        # This warning is descriptive only; it never changes ownership mode.
        if target.root and Path(target.root).resolve(strict=False) == root and effective == "root-and-inputs":
            findings.append({
                "target": name,
                "code": "REPOSITORY_ROOT_WILDCARD",
                "severity": "informational",
                "effective_mode": effective,
                "detail": 'root = "." directly owns every source path under root-and-inputs',
                "migration": 'Review inputs, then opt in to inputs-only if narrower ownership is intended.',
            })
    legacy = sum(row["code"] == "IGNORED_METADATA_AFFECTED_OWNERSHIP" for row in findings)
    return {
        "schema_version": 1,
        "kind": "devtool.affected-ownership-diagnostics",
        "status": "NEEDS_MIGRATION" if legacy else "PASS",
        "target_count": len(modes),
        "legacy_metadata_count": legacy,
        "effective_modes": modes,
        "findings": findings,
        "mutated": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="fail on legacy ownership metadata")
    args = parser.parse_args(argv)
    try:
        result = diagnose(args.repo, config_path=args.config)
    except (OSError, ValueError, SystemExit) as exc:
        print(f"DEVTOOL_OWNERSHIP_DIAGNOSIS_REFUSED: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Devtool ownership: {result['status']} "
              f"({result['target_count']} targets; {result['legacy_metadata_count']} legacy values)")
        for item in result["findings"]:
            print(f"- {item['target']}: {item['code']}: {item['detail']}")
    return 2 if args.strict and result["legacy_metadata_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
