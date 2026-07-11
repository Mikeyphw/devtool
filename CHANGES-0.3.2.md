# Devtool 0.3.2

- Keep **Copy summary**, **Open log**, and **Close** visible in the compact GTK desktop HUD.
- Increase the compact HUD height slightly so the persistent action row fits without crowding live status information.
- Replace full-transcript failure clipboard output with a focused report.
- Summarize successful phases on one short line and preserve the failed command plus useful diagnostics.
- Collapse verbose rollback restored/removed path inventories into a brief recovery status.
- Keep `--copy-json` unchanged and use the focused report for both `--copy` and `--copy-summary` failures.
