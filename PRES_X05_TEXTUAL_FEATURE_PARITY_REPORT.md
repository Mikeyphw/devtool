# PRES-X05 Textual TUI Feature Parity

Position: PRES scope 5/?; full-plan 27/63.

## Merge decision

Standalone implementation overlay. PRES-X04 established the Textual substrate. PRES-X05 makes the Textual path feature-complete enough to replace the current TUI, but it does not flip default entrypoints or remove legacy code. That remains a later PRES replacement/migration overlay and gate.

## Delivered

- Added `TextualFeatureParityPlan` as the reference-only parity contract for current-TUI replacement.
- Added supplemental Textual screen specs for runs, validation, artifacts, commands, and help.
- Added command-palette entries derived from canonical action intents and renderer bindings.
- Added a required feature checklist for run list, active progress, node tree, diagnostics, evidence, validation, artifact lifecycle, command palette, shortcuts, copy/share, and responsive layout.
- Added coverage evaluation that fails closed when required widgets/screens are missing.
- Preserved the truth boundary: Textual feature parity consumes presentation projections, refs, bindings, actions, and capabilities; it does not own RUN, EXO, RER, validation, progress, process, or artifact truth.
- Added wrapper command `pres-x05` and workflow `roadmap-pres-x05`.

## Validation

Direct targeted validation in the construction workspace:

```text
python -m pytest -q --tb=short \
  tests/test_pres_x05_textual_feature_parity.py \
  tests/test_pres_x04_textual_tui.py

28 passed
```

The artifact validation contract points at `./devtoolw pres-x05`, which runs source syntax, PRES-X05 feature parity, and retained PRES-X04 substrate regression on the destination Termux host.

## Notes

Textual remains optional/lazily imported in this overlay. Installing the package and making Textual the default launcher is intentionally left to the later default-replacement overlay so the feature-parity contract can land safely first.
