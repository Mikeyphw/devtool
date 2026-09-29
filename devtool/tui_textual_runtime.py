"""Import-safe Textual runtime entrypoint for Devtool's replacement TUI."""

from __future__ import annotations

import argparse
from pathlib import Path

from .tui_textual import run_textual_tui_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Devtool Textual TUI runtime")
    parser.add_argument("--page", default="dashboard")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--width", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_textual_tui_runtime(args, repo_root=Path.cwd())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
