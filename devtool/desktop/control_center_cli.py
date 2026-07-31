from __future__ import annotations

import argparse
from pathlib import Path

from ..tasker import DEFAULT_TASKER_DIR
from .gtk_control_center import Gtk4ControlCenter


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devtool-control-center")
    parser.add_argument("-r", "--repo", default=".", help="Repository root")
    parser.add_argument("-c", "--config", default="", help="Config file path")
    parser.add_argument("--target", default="", help="Named target/profile from .devtool.toml")
    parser.add_argument("--variant", default="", help="Build/test variant within the selected target")
    parser.add_argument("--state-dir", default=DEFAULT_TASKER_DIR, help="Directory containing devtool live HUD state")
    parser.add_argument("--follow-active", action="store_true", help="Follow the global active HUD directory")
    parser.add_argument("--history-limit", type=int, default=25, help="Artifact history rows to load")
    parser.add_argument("--recent-events", type=int, default=8, help="Recent HUD events to load")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between GTK refreshes")
    parser.add_argument("--page", choices=["overview", "runs", "history", "diagnostics", "repositories", "targets", "run", "settings"], default="overview")
    parser.add_argument("--backend", choices=["auto", "gtk4"], default="auto", help="Control Center backend; GTK4 is the only desktop backend")
    parser.add_argument("--once", action="store_true", help="Print a read-only GTK4 Control Center JSON snapshot and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    center = Gtk4ControlCenter(history_limit=args.history_limit, recent_events=args.recent_events)
    if args.once or args.backend == "gtk4" or Gtk4ControlCenter.available():
        return center.run(
            repository=Path(args.repo).expanduser(),
            config_path=args.config or None,
            target=args.target,
            variant=args.variant,
            state_dir=Path(args.state_dir).expanduser(),
            follow_active=bool(args.follow_active),
            once=bool(args.once),
            page=args.page,
            interval_s=float(args.poll_interval),
        )
    raise RuntimeError("Devtool GTK4 Control Center requires python3-gi, GTK 4, and libadwaita.")


if __name__ == "__main__":
    raise SystemExit(main())
