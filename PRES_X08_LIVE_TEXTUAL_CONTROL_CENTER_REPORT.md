# PRES-X08 — Live Textual Control Center Adoption

PRES-X08 replaces the thin Textual placeholder with a bounded renderer/control-client contract over canonical Devtool authorities.

## Delivered

- Dashboard, Runs, Logs, Diagnostics, History, Targets, Workers, Validation, Artifacts and Evidence routes.
- Stable navigation map including `l` Logs, `w` Workers and `s` Artifacts/Snapshots.
- Bounded live events/logs with noisy progress coalescing and terminal/error barriers.
- Run/node selection, hierarchy projection, attention ordering, log follow/pause and deterministic replay.
- Typed action requests with requested/accepted/rejected semantics; domain execution remains EXO/RUN/LIFE/DIST/TEST-owned.
- Confirmation enforcement for destructive control requests.
- Wide/narrow/ultra-narrow/plain/screen-reader capability negotiation with truthful mouse/hyperlink/clipboard/resize/multi-pane/tree flags.
- `devtool.tui_textual_runtime --once` now emits the control-center projection contract.
- Interactive Textual shell exposes the canonical page/key map rather than the prior static placeholder.

## Authority boundary

Textual remains presentation/control only. RUN, EXO, RER, validation, ENV, LIFE, DIST, TEST, RMP and CFG retain canonical domain truth.

## Deferred to PRES-G2

Fresh-process interactive reliability/side-effect-free observation proof, renderer fault isolation, and concrete operator workflow gate remain separate authoritative validation.
