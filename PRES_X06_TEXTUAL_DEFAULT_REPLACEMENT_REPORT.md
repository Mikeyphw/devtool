# PRES-X06 Textual TUI Default Replacement

## Scope

PRES-X06 flips the `devtool tui` selection contract so Textual is the default TUI backend while retaining an explicit legacy escape hatch for bootstrap, recovery, and hosts without the optional Textual dependency.

## Delivered

- `devtool tui --backend auto|textual|legacy`
- `devtool tui --legacy-tui`
- `devtool tui --require-textual`
- auto mode now means: prefer Textual, fall back to legacy only when Textual is not importable
- explicit `--backend textual` fails closed when Textual is unavailable
- default backend contract documenting that Textual owns rendering and interaction only
- CLI dispatch routed through the Textual backend selector
- import-safe Textual runtime shell with lazy Textual imports
- regression coverage proving PRES-X05 feature parity remains intact

## Authority boundary

Textual does not own RUN, EXO, RER, validation, ENV, progress, reuse, or verdict truth. It only selects a renderer and consumes the projection contracts from the PRES stack.

## Validation

- `tests/test_pres_x06_textual_default_replacement.py`
- `tests/test_pres_x05_textual_feature_parity.py`
- `./devtoolw pres-x06` during overlay apply
