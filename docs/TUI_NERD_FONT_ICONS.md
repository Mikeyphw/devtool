# TUI Nerd Font project-language icons

Devtool keeps the live progress panel borders as normal box drawing characters and only replaces the running-state decorative diamond with a project-language Nerd Font glyph when the terminal is likely to support it.

The behavior is intentionally conservative:

- `DEVTOOL_NERD_FONT=1` forces project icons.
- `DEVTOOL_NERD_FONT=0` disables project icons.
- `DEVTOOL_NO_NERD_FONT=1` disables project icons.
- `DEVTOOL_ASCII=1`, `TERM=dumb`, CI, or explicit ASCII locales keep the `◆` fallback.

Success and failure still use the existing `✓` and `✗` status glyphs. The project icon is only used while work is running.

Project language is inferred from the target/command label first, then conservative repository markers such as `pyproject.toml`, Gradle files, `Cargo.toml`, `go.mod`, and `package.json`.
