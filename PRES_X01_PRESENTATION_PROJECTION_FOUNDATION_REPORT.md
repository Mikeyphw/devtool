# PRES-X01 — Presentation Projection Foundation

Position: PRES scope 1/?, full-plan 23/63.

## Merge decision

Standalone overlay. The next-three window after ENV-X04 is `PRES-X01 -> PRES-X02 -> PRES-X03/PRES-G1` in the presentation family; PRES-X01 delivers the rendering/projection foundation only. Follow-on presentation overlays own interaction density, formatting, and broader surface adoption.

## Delivered promises

- Added `devtool.presentation_surfaces` as a projection-only rendering layer over canonical RUN/EXO/RER/validation/ENV references.
- Added machine, terminal, TUI, and desktop surface projection models.
- Added bounded EXO/RUN status projection with cursoring.
- Added validation verdict projection derived from contract + evidence refs, including optional-only and not-applicable-required semantics.
- Added RER why-reuse/why-rerun explanation projection without transferring reuse authority.
- Added ENV doctor/explain projection that redacts secret-bearing details.
- Added dashboard composition that keeps canonical refs and does not own progress/status/verdict truth.
- Added reference-only guardrails against copied stdout/stderr/argv/heartbeat/process-attempt truth.
- Added `./devtoolw pres-x01` targeted workflow.

## Validation

- `tests/test_pres_x01_presentation_surfaces.py`: 24 passed.
- retained ENV-X04 explain regression: 28 passed.
- construction wrapper workflow: 3 stages, 52 tests, 0 warnings, 0 errors.

## Exports

- PRES-X02 should consume the projection model for richer terminal/TUI/Desktop presentation and layout behavior.
- PRES-G1 should gate cross-surface parity and ensure no presentation surface becomes a second authority.
