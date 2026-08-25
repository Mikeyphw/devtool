# Responsive TUI and repository cleanup

Architecture overlay 08 adds the terminal control surface and closes the migration with safe repository housekeeping.

## Responsive terminal UI

`devtool tui` is deliberately Rich/prompt based rather than curses/full-screen. This keeps it reliable in Termux, tmux, SSH sessions, Android soft keyboards, and terminals which resize after phone rotation.

Pages:

- `dashboard` — repository, Git and high-level artifact/apply state.
- `build` — active HUD build/test phase, progress/detail, and registered build artifacts.
- `overlays` — recent overlay/apply history plus optional live artifact preflight (source SHA/conflicts/rebase status).

The layout is recalculated on every frame:

- **compact: 40–60 columns** — stacked panels, no multi-column tables, short navigation labels.
- **narrow: 61–96 columns** — reduced tables with secondary columns removed.
- **wide: 97+ columns** — full artifact/history detail.

`devtool tui --once --width 48` is useful for testing a Termux-sized frame without entering the interactive loop. Use `devtool tui --page overlays --artifact overlay.zip` to include the overlay preflight card.

## Repository cleanup

`devtool cleanup` archives historical source/back-up files into `.devtool/history/backups/<timestamp>/` while preserving their repository-relative paths. It does not delete current source files and does not traverse `.git` or existing `.devtool` history.

Recognized historical forms include `*.before-*`, `*.pre-*`, `.bak`, `.backup`, `.orig`, and known Devtool migration-backup directories.

Use `--dry-run` before a large cleanup. Old archived batches are only deleted when `--prune-history-days DAYS` is explicitly supplied.
