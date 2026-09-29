# PRES-X03 cross-surface rendering adoption

## Position

- PRES scope: 3/?
- Full-plan: 25/63
- Boundary: presentation implementation, not a gate/seal

## Scope

PRES-X03 wires PRES-X01 reference-only projections and PRES-X02 layout/action
semantics into richer consumer adapters for machine, terminal, TUI, desktop,
Android Live HUD, and IPC-style consumers.  The adapters preserve canonical
truth ownership: RUN/EXO own execution/progress, RER owns reuse decisions,
validation owns contracts/verdicts, and ENV owns environment explanations.

## Delivered

- Consumer model for machine, terminal, TUI, desktop, HUD, and IPC.
- Rendered-surface envelopes with refs, actions, parity keys, fidelity, and
  generated-truth guardrails.
- Presentation bundle schema v3 aggregating source view plus consumer renders.
- Terminal, TUI, desktop, HUD, and IPC adapter payloads over a single source
  `PresentationView`.
- Cross-surface parity matrix validating shared status/ref/action identity.
- Android-HUD alias normalization while preserving terminal-style compact layout.
- Reference-only assertions for individual renders and whole bundles.

## Validation

- Direct PRES-X03 targeted tests pass.
- Retained PRES-X02 layout/action tests pass.
- Wrapper workflow `./devtoolw pres-x03` passes with zero warnings/errors.
- Artifact validation passes.

## Exported obligations

PRES-G1 must gate cross-surface parity and reference-only guarantees across the
PRES-X01..X03 implementation series.
