# PRES-X02 Presentation Layout and Interaction Semantics

## Scope

PRES-X02 extends the PRES-X01 reference-only projection foundation with renderer-neutral layout, capability, diagnostic-section, and action-intent projections.

The overlay remains presentation-only: it does not own execution status, validation verdicts, reuse decisions, process attempts, heartbeat/liveness truth, stdout/stderr, or argv material. All views are derived from `PresentationProjection` objects backed by RUN/EXO/RER/validation/ENV references.

## Delivered

- Surface capability negotiation for machine, terminal, TUI, and desktop surfaces.
- Responsive layout intent for compact/standard/detailed profiles.
- Renderer-neutral diagnostic sections: What happened, Why, Where, What you can do, Technical details.
- Action-intent projection for opening evidence, watching progress, following cursors, copying summaries, and rerunning failed validation nodes.
- Schema-v2 `PresentationView` machine contract composed over PRES-X01 projection payloads.
- Terminal text rendering that includes sections/actions without copying command output or process truth.
- Reference-only guard for complete presentation views.

## Validation

- Direct targeted PRES-X02 tests: 26 passed.
- Retained PRES-X01 regression: 24 passed.
- Construction wrapper workflow `./devtoolw pres-x02`: 3 stages, 50 tests, 0 warnings, 0 errors.
- Artifact inspection: `Artifact validation passed`.
