from __future__ import annotations

"""Read-only SSH-01 target-local roadmap fixture."""

import argparse
import json
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m devtool.ssh_roadmap_fixtures")
    parser.add_argument("command", choices=("contract",))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = contract_payload()
    print(json.dumps(payload, indent=2 if args.json else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
