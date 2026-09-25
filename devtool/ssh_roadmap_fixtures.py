from __future__ import annotations

"""SSH roadmap fixtures and the SSH-02 transaction-root gate install hook."""

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


def gate_install_payload() -> dict[str, Any]:
    root = Path(os.environ.get("DEVTOOL_TRANSACTION_REPO_ROOT") or os.getcwd()).expanduser().resolve()
    if not (root / "pyproject.toml").is_file():
        raise ValueError(f"SSH-02 editable install root is not a Devtool checkout: {root}")
    command = [sys.executable, "-m", "pip", "install", "--no-build-isolation", "-e", str(root)]
    completed = subprocess.run(command, cwd=str(root), capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ValueError(f"SSH-02 transaction-root editable install failed: {detail or completed.returncode}")
    return {
        "schema_version": 1,
        "kind": "ssh02-gate-editable-install",
        "roadmap_id": "SSH-02",
        "ok": True,
        "transaction_repo_root": str(root),
        "command": command,
        "returncode": int(completed.returncode),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m devtool.ssh_roadmap_fixtures")
    parser.add_argument("command", choices=("contract", "execution", "gate-install"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload = contract_payload() if args.command == "contract" else execution_payload() if args.command == "execution" else gate_install_payload()
    except ValueError as exc:
        print(json.dumps({"schema_version": 1, "ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
