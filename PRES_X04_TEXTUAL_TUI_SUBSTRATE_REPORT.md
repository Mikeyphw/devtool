# PRES-X04 — Textual TUI Substrate Foundation

## Scope

PRES-X04 pivots the remaining presentation roadmap toward Textual as the primary
TUI implementation.  It does not make Textual the default launcher yet; that
belongs to later parity/default-replacement overlays.  This overlay creates the
reference-only app contract, lazy dependency probing, launch-plan semantics,
terminal profile selection, and keyboard/action mapping needed for replacement.

## Delivered

- `devtool.tui_textual` optional Textual substrate contract.
- Lazy Textual availability probe that does not import the framework during validation.
- `TextualAppSpec`, `TextualScreenSpec`, `TextualWidgetSpec`, and `TextualLaunchPlan`.
- Dashboard/details/evidence screen model over `PresentationBundle` only.
- SSH/mobile/tablet-aware compact/standard/detailed profile selection.
- Canonical `ActionIntent` to Textual key binding map.
- Fail-closed reference-only guard for Textual specs.
- Optional dependency extras for `textual` / `tui` without making validation require the package.

## Authority boundary

Textual owns rendering and interaction.  It may render `PresentationBundle`,
`PresentationView`, sections, refs, and actions.  It must not own progress,
process attempts, validation truth, reuse truth, or execution events.

## Validation

Targeted validation is `./devtoolw pres-x04`, which runs:

- source syntax;
- `tests/test_pres_x04_textual_tui.py`;
- retained PRES-X03 rendering regression.
