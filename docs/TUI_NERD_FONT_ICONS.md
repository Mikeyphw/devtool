# TUI Nerd Font project-language icons

Devtool keeps the live progress panel borders as normal box drawing characters and only replaces the running-state decorative diamond with a project-language Nerd Font glyph when the terminal is likely to support it.

UI04 routes semantic icon choice through the shared `icons` preference first. For example, `[ui] icons = "ascii"` forces ASCII-safe semantic status/navigation icons, while `icons = "nerd"` opts into Nerd Font mode. `DEVTOOL_UI_ICONS` provides the per-process equivalent.

The legacy behavior remains intentionally compatible:

- `DEVTOOL_NERD_FONT=1` forces project icons when the shared icon mode is `auto`.
- `DEVTOOL_NERD_FONT=0` disables project icons.
- `DEVTOOL_NO_NERD_FONT=1` disables project icons.
- `DEVTOOL_ASCII=1`, `TERM=dumb`, CI, or explicit ASCII locales keep the safe fallback when icon mode is `auto`.

Status/action/navigation semantics use the central icon registry with Nerd Font, Unicode, ASCII, and native symbolic variants. Project-language glyphs remain a TUI-specific enhancement used while work is running. See `UI_PREFERENCES_AND_TOKENS.md`.

Project language is inferred from the target/command label first, then conservative repository markers such as `pyproject.toml`, Gradle files, `Cargo.toml`, `go.mod`, and `package.json`.
