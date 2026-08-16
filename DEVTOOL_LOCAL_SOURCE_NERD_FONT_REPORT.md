# Devtool local source dependency + Nerd Font TUI overlay

This overlay supersedes `devtool_local_python_source_dependency_fix_overlay.zip` for users who have not applied it yet.

## Local Python source dependency fix

For uv-managed Python targets, Devtool can install conservative sibling source dependencies before running the selected project. This covers local modules such as `~/Code/mikomn` when a project depends on `mikomn[cli]` and the sibling checkout declares `[project].name = "mikomn"`.

Safety rules:

- existing `[tool.uv.sources]` declarations remain authoritative;
- unselected optional extras do not trigger sibling source installation;
- sibling source candidates must have a matching declared project name;
- unsafe guesses and mismatched local projects are ignored.

## Nerd Font TUI marker logic

The live TUI running-state marker now uses a project-language Nerd Font glyph when enabled and safe, while preserving normal borders and the `✓` / `✗` terminal status icons.

Safety rules:

- `DEVTOOL_NERD_FONT=1` forces glyphs;
- `DEVTOOL_NERD_FONT=0` or `DEVTOOL_NO_NERD_FONT=1` keeps the diamond fallback;
- CI, `TERM=dumb`, `DEVTOOL_ASCII=1`, and explicit ASCII locales keep the diamond fallback;
- project language is inferred from target/command labels and local project marker files.

## Validation

Focused validation executed for local-source resolution and TUI/progress rendering.
