# PRES-X07 Textual Replacement Hardening

## Scope

PRES-X07 hardens the Textual-default replacement introduced by PRES-X06. It fences the legacy TUI behind explicit selection or missing-Textual fallback, exposes backend resolution for SSH/Termux troubleshooting, and records remaining cleanup assumptions without transferring presentation or execution truth.

## Delivered

- `devtool tui --no-tui-fallback` as an alias for `--require-textual`
- `devtool tui --explain-backend` for backend selection diagnostics without launching the UI
- backend-selection notices for Textual selection, explicit legacy selection, auto fallback, and unavailable Textual
- legacy-fence assertion proving legacy can only run when explicit or as a permitted auto fallback
- cleanup summary for remaining legacy paths
- hardening contract documenting Textual as primary and legacy as fallback-only
- runtime warning when auto mode falls back to legacy
- regression coverage proving PRES-X06 default replacement remains intact

## Authority boundary

Textual remains renderer/interaction-only. The hardening notice, backend selection, and cleanup summary are derived from selection metadata and do not own RUN, EXO, RER, validation, ENV, progress, reuse, verdict, stdout, stderr, or process-attempt truth.

## Validation

- `tests/test_pres_x07_textual_replacement_hardening.py`
- `tests/test_pres_x06_textual_default_replacement.py`
- `./devtoolw pres-x07` during overlay apply
