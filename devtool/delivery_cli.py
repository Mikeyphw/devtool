"""Read-only/locally journaled delivery CLI, backed by delivery_session."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

from .delivery_session import DeliverySessionError, plan, start, status, resume, report


def run_delivery_command(args: Any) -> int:
    operation = str(getattr(args, "delivery_session_command", "") or "")
    root = Path(str(getattr(args, "repo", None) or Path.cwd()))
    try:
        if operation in {"plan", "start"}:
            options = {"base": str(getattr(args, "base", "main") or "main"),
                       "head": getattr(args, "head", None),
                       "overlay": (Path(args.overlay) if getattr(args, "overlay", None) else None)}
            payload = plan(root, **options) if operation == "plan" else start(root, **options)
        else:
            identity = str(getattr(args, "delivery_id", "") or "")
            action = {"status": status, "resume": resume, "report": report}.get(operation)
            if action is None:
                raise DeliverySessionError("Unsupported delivery command")
            payload = action(identity)
            if Path(payload["repo_root"]).resolve() != root.expanduser().resolve():
                raise DeliverySessionError("Session belongs to another repository/worktree")
    except (DeliverySessionError, OSError, ValueError) as exc:
        print(f"Devtool delivery: {exc}", file=sys.stderr)
        return 2
    if bool(getattr(args, "json", False) or getattr(args, "_devtool_json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(f"Devtool delivery: {operation} — {payload['stage']}")
        print(f"Source: {payload['head_sha']}")
        if payload.get("id"):
            print(f"Session: {payload['id']}")
        if payload.get("disposition"):
            print(f"Next: {payload['disposition']} — {payload['next_action']}")
        print("Remote delivery: not verified | Release: not qualified")
    return 0
