from __future__ import annotations

"""SSH roadmap fixtures and transaction-root gate install hooks."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def contract_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "ssh01-contract-fixture",
        "roadmap_id": "SSH-01",
        "read_only": True,
        "workspace_transport": ["archive-seed", "rsync-delta", "archive-seed+reconcile"],
        "return_transport": ["rsync-delta", "archive-zstd"],
        "environment_policies": ["remote", "overlay", "hermetic"],
        "toolchain_provenance": ["remote-existing", "termux-existing", "devtool-cache", "missing"],
        "commands": ["ssh env", "ssh prepare"],
        "source_mutation_by_prepare": False,
        "federation_contracts": ["GIT-FRX-07", "GIT-FRX-08/FRX-Y03", "GIT-FRX-09"],
        "typed_event": "transport.progress",
    }


def execution_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "ssh02-execution-history-recovery-fixture",
        "roadmap_id": "SSH-02",
        "read_only": True,
        "canonical_identity": [
            "run_id", "profile", "repository", "target", "typed_contract", "git_lineage",
            "workspace_id", "source_sync_id", "frozen_source_lock_digest", "phase", "tmux_session",
        ],
        "persistent_output": ["stdout", "stderr", "complete", "partial", "still-writing", "unavailable", "interrupted"],
        "historical_commands": ["ssh sessions", "ssh runs", "ssh output"],
        "managed_exec": ["foreground", "detached", "attach", "follow", "argv", "explicit-shell"],
        "recovery": ["resume", "cancel", "recover", "retrieve"],
        "artifact_states": [
            "expected", "produced", "staged", "transferred", "verified",
            "restored-locally", "failed", "recoverable",
        ],
        "tmux_is_authority": False,
        "runstore_authority": True,
        "federation_contracts": ["FRX-Y07", "GIT-FRX-09"],
    }


def final_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "ssh03-lifecycle-hardening-final-fixture",
        "roadmap_id": "SSH-03",
        "read_only": True,
        "gc": {
            "dry_run_default": True,
            "policies": ["age", "count", "lru", "size"],
            "protects": ["active", "stale", "interrupted", "unsafe-id", "symlink-escape"],
        },
        "host_identity": {
            "authority": "openssh-known-hosts",
            "parallel_database": False,
            "evidence": ["algorithm", "sha256-fingerprint", "worker-platform", "worker-architecture"],
        },
        "recovery": ["stale-phase", "owned-lease", "orphan-lease"],
        "capacity": ["repository-size-estimate", "configured-floor", "pre-sync-enospc-block"],
        "doctor": ["target-aware", "provider-evidence", "toolchain-evidence", "remediation-command"],
        "performance": {"records_metrics": True, "fragile_wall_clock_thresholds": False},
        "security": ["repository-path-isolation", "safe-run-id", "root-bounded-delete", "no-key-material-output"],
        "documentation": ["docs/SSH_REMOTE_EXECUTION.md", "docs/DEVTOOL_AI_AGENT_GUIDE.md"],
        "final_gate": True,
    }


def gate_install_payload(roadmap_id: str = "SSH-02") -> dict[str, Any]:
    root = Path(os.environ.get("DEVTOOL_TRANSACTION_REPO_ROOT") or os.getcwd()).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"{roadmap_id} editable install root is not a Devtool checkout: {root}")
    command = [sys.executable, "-m", "pip", "install", "--no-build-isolation", "-e", str(root)]
    completed = subprocess.run(command, cwd=str(root), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"{roadmap_id} transaction-root editable install failed: {detail or completed.returncode}")
    return {
        "schema_version": 1,
        "kind": f"{roadmap_id.lower().replace('-', '')}-gate-editable-install",
        "roadmap_id": roadmap_id,
        "ok": True,
        "transaction_repo_root": str(root),
        "command": command,
        "returncode": int(completed.returncode),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m devtool.ssh_roadmap_fixtures")
    parser.add_argument("command", choices=("contract", "execution", "final", "gate-install", "gate-install-final", "gate-install-git-frx16"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            payload = contract_payload()
        elif args.command == "execution":
            payload = execution_payload()
        elif args.command == "final":
            payload = final_payload()
        elif args.command == "gate-install-final":
            payload = gate_install_payload("SSH-03")
        elif args.command == "gate-install-git-frx16":
            payload = gate_install_payload("GIT-FRX-16")
        else:
            payload = gate_install_payload("SSH-02")
    except ValueError as exc:
        print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
