from __future__ import annotations

import argparse
from pathlib import Path

from ..tasker import DEFAULT_TASKER_DIR
from .gtk_hud import Gtk4CompactHud
from .gtk_runtime import report_cli_exception


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool-gtk-hud")
    parser.add_argument("--state-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    parser.add_argument("--poll-interval", type=float, default=0.1, help="Seconds between HUD refreshes")
    parser.add_argument("--recent-events", type=int, default=8, help="Recent JSONL events to display")
    parser.add_argument("--once", action="store_true", help="Print one JSON snapshot and exit")
    parser.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory")
    parser.add_argument("--startup-test", action="store_true", help="Open one GTK frame, record startup diagnostics, then exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return Gtk4CompactHud(recent_events=max(0, int(args.recent_events))).run(
            directory=Path(args.state_dir).expanduser(),
            interval_s=max(0.05, float(args.poll_interval)),
            once=bool(args.once),
            follow_active=bool(args.follow_active),
            startup_test=bool(args.startup_test),
        )
    except Exception as exc:
        return report_cli_exception("hud", "devtool-gtk-hud", exc)


if __name__ == "__main__":
    raise SystemExit(main())
